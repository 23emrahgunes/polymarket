#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from phase2_gefs_t2m_smoke import (
    HKO_LAT,
    HKO_LON,
    decode_nearest_c,
    fetch_tmp_message,
    member_names,
)

BUCKET_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*°?C(?:\s+or\s+(higher|below))?\s*$", re.I)


def parse_bucket(s: str):
    m = BUCKET_RE.match(str(s))
    if not m:
        return None, None
    return float(m.group(1)), (m.group(2) or "exact").lower()


def bucket_hit(max_c: float, bucket: str) -> bool | None:
    val, mode = parse_bucket(bucket)
    if val is None:
        return None
    if mode == "higher":
        return max_c >= val
    if mode == "below":
        return max_c <= val
    # Phase-2 exploratory convention only. Exact settlement mapping will later be
    # aligned to the market's resolution rule and station-local verification.
    return int(round(max_c)) == int(round(val))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target-date", required=True)
    ap.add_argument("--cycle", default="18", choices=["00", "06", "12", "18"])
    ap.add_argument("--manifest", default="phase1_full_out/hk_phase2_manifest.csv")
    ap.add_argument("--perturbed", type=int, default=30)
    ap.add_argument("--lat", type=float, default=HKO_LAT)
    ap.add_argument("--lon", type=float, default=HKO_LON)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    target = date.fromisoformat(args.target_date)
    run_date = target - timedelta(days=1) if args.cycle == "18" else target
    ymd = run_date.strftime("%Y%m%d")
    fhrs = [6, 9, 12, 15, 18, 21]
    members = member_names(args.perturbed)

    s = requests.Session()
    s.headers.update({"User-Agent": "polymarket-weather-phase2-gefs/0.2"})

    print("===== GEFS 31-MEMBER DAY ENGINE =====")
    print(f"target_hkt_date={target} run={ymd} {args.cycle}Z members={len(members)}")
    print(f"target_point={args.lat:.6f},{args.lon:.6f}")
    print("NOTE: probabilities below are RAW GEFS-grid probabilities; station/grid bias is not calibrated yet.\n")

    rows = []
    first_grid = None
    for i, member in enumerate(members, 1):
        vals = []
        ok = True
        err = None
        for fhr in fhrs:
            try:
                blob, _inv, _rng = fetch_tmp_message(s, ymd, args.cycle, member, fhr)
                dec = decode_nearest_c(blob, args.lat, args.lon)
                vals.append(float(dec["temp_c"]))
                if first_grid is None:
                    first_grid = (float(dec["grid_lat"]), float(dec["grid_lon"]))
            except Exception as e:
                ok = False
                err = f"f{fhr:03d}: {e}"
                break
        if ok and vals:
            mx = max(vals)
            rows.append({"member": member, "remaining_day_max_c": mx, "status": "ok", "error": ""})
            print(f"[{i:02d}/{len(members)}] {member} max={mx:.2f}C")
        else:
            rows.append({"member": member, "remaining_day_max_c": np.nan, "status": "error", "error": err or "unknown"})
            print(f"[{i:02d}/{len(members)}] {member} ERROR {err}")

    df = pd.DataFrame(rows)
    good = df[df["status"] == "ok"].copy()
    if good.empty:
        print("No members decoded successfully.")
        return 2

    arr = good["remaining_day_max_c"].to_numpy(float)
    print("\n===== RAW ENSEMBLE SUMMARY =====")
    print(f"members_ok={len(arr)}/{len(members)}")
    print(f"mean={arr.mean():.2f}C median={np.median(arr):.2f}C std={arr.std(ddof=1) if len(arr)>1 else 0:.2f}C")
    print(f"min={arr.min():.2f}C p10={np.quantile(arr,.10):.2f}C p25={np.quantile(arr,.25):.2f}C p75={np.quantile(arr,.75):.2f}C p90={np.quantile(arr,.90):.2f}C max={arr.max():.2f}C")
    if first_grid:
        print(f"nearest_GEFS_grid={first_grid[0]:.3f},{first_grid[1]:.3f}")

    rounded = pd.Series(np.rint(arr).astype(int)).value_counts().sort_index()
    print("\n===== RAW ROUNDED MAX DISTRIBUTION =====")
    for k, n in rounded.items():
        print(f"{int(k)}C: {int(n):2d}/{len(arr)} = {100*n/len(arr):6.2f}%")

    mp = Path(args.manifest)
    if mp.exists():
        manifest = pd.read_csv(mp)
        z = manifest[manifest["date_hkt"].astype(str) == args.target_date].copy()
        print("\n===== POLYMARKET CANDIDATES ON TARGET DATE =====")
        if z.empty:
            print("No manifest candidates for this date.")
        else:
            outrows = []
            for _, r in z.iterrows():
                hits = [bucket_hit(v, r["bucket"]) for v in arr]
                hits = [x for x in hits if x is not None]
                p = float(np.mean(hits)) if hits else np.nan
                market_p = float(r["price"])
                edge = p - market_p if np.isfinite(p) else np.nan
                rec = {
                    "date_hkt": args.target_date,
                    "event_slug": r["event_slug"],
                    "bucket": r["bucket"],
                    "market_price": market_p,
                    "result": int(r["result"]),
                    "raw_gefs_p": p,
                    "raw_edge": edge,
                    "members_ok": len(arr),
                    "gefs_mean_max_c": float(arr.mean()),
                    "gefs_std_max_c": float(arr.std(ddof=1) if len(arr)>1 else 0),
                }
                outrows.append(rec)
                print(
                    f"bucket={r['bucket']} market={market_p*100:.2f}% "
                    f"raw_GEFS={p*100:.2f}% raw_edge={edge*100:+.2f}pp result={int(r['result'])}"
                )
            if args.out:
                outdf = pd.DataFrame(outrows)
                Path(args.out).parent.mkdir(parents=True, exist_ok=True)
                outdf.to_csv(args.out, index=False)
                print(f"\nSAVED: {args.out}")

    print("\nNEXT: repeat across historical events, then fit station/grid bias and probability calibration out-of-sample. Do not trade raw GEFS probabilities directly.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
