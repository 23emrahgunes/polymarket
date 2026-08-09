#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import re
import time
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
FHR_DEFAULT = [6, 9, 12, 15, 18, 21]


def parse_bucket(s: str):
    m = BUCKET_RE.match(str(s))
    if not m:
        return None, None
    return float(m.group(1)), (m.group(2) or "exact").lower()


def bucket_hit(max_c: float, bucket: str) -> bool | None:
    """Exploratory raw-GEFS mapping only; station/bucket calibration comes later."""
    val, mode = parse_bucket(bucket)
    if val is None:
        return None
    if mode == "higher":
        return max_c >= val
    if mode == "below":
        return max_c <= val
    return int(round(max_c)) == int(round(val))


def q(arr: np.ndarray, p: float) -> float:
    return float(np.quantile(arr, p))


def atomic_json(path: Path, obj: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def append_csv(path: Path, df: pd.DataFrame):
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists() and path.stat().st_size > 0
    df.to_csv(path, mode="a", header=not exists, index=False)


def load_completed(state_path: Path) -> set[str]:
    if not state_path.exists():
        return set()
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
        return set(map(str, data.get("completed_dates", [])))
    except Exception:
        return set()


def session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": "polymarket-weather-phase2-gefs-full/0.1"})
    return s


def decode_day(
    target_s: str,
    cycle: str,
    members: list[str],
    fhrs: list[int],
    lat: float,
    lon: float,
    retries: int,
    sleep_s: float,
):
    target = date.fromisoformat(target_s)
    run_date = target - timedelta(days=1) if cycle == "18" else target
    ymd = run_date.strftime("%Y%m%d")
    s = session()

    rows = []
    first_grid = None
    for mi, member in enumerate(members, 1):
        vals = []
        member_error = None
        for fhr in fhrs:
            last = None
            dec = None
            for attempt in range(retries):
                try:
                    blob, _inv, _rng = fetch_tmp_message(s, ymd, cycle, member, fhr)
                    dec = decode_nearest_c(blob, lat, lon)
                    break
                except Exception as e:
                    last = e
                    time.sleep(min(1.5 * (attempt + 1), 5.0))
            if dec is None:
                member_error = f"f{fhr:03d}: {last}"
                break
            vals.append(float(dec["temp_c"]))
            if first_grid is None:
                first_grid = (float(dec["grid_lat"]), float(dec["grid_lon"]))
            if sleep_s:
                time.sleep(sleep_s)

        if member_error is None and vals:
            mx = max(vals)
            rows.append({
                "date_hkt": target_s,
                "run_ymd": ymd,
                "cycle": cycle,
                "member": member,
                "remaining_day_max_c": float(mx),
                "members_requested": len(members),
                "grid_lat": first_grid[0] if first_grid else np.nan,
                "grid_lon": first_grid[1] if first_grid else np.nan,
                "status": "ok",
                "error": "",
            })
            print(f"      [{mi:02d}/{len(members)}] {member} max={mx:.2f}C", flush=True)
        else:
            rows.append({
                "date_hkt": target_s,
                "run_ymd": ymd,
                "cycle": cycle,
                "member": member,
                "remaining_day_max_c": np.nan,
                "members_requested": len(members),
                "grid_lat": first_grid[0] if first_grid else np.nan,
                "grid_lon": first_grid[1] if first_grid else np.nan,
                "status": "error",
                "error": member_error or "unknown",
            })
            print(f"      [{mi:02d}/{len(members)}] {member} ERROR {member_error}", flush=True)

    return pd.DataFrame(rows), first_grid


def candidate_rows(manifest_day: pd.DataFrame, members_df: pd.DataFrame):
    good = members_df[members_df["status"] == "ok"].copy()
    arr = good["remaining_day_max_c"].to_numpy(float)
    if len(arr) == 0:
        return pd.DataFrame()

    std = float(arr.std(ddof=1)) if len(arr) > 1 else 0.0
    common = {
        "members_ok": len(arr),
        "gefs_mean_max_c": float(arr.mean()),
        "gefs_median_max_c": float(np.median(arr)),
        "gefs_std_max_c": std,
        "gefs_min_max_c": float(arr.min()),
        "gefs_p10_max_c": q(arr, .10),
        "gefs_p25_max_c": q(arr, .25),
        "gefs_p75_max_c": q(arr, .75),
        "gefs_p90_max_c": q(arr, .90),
        "gefs_max_max_c": float(arr.max()),
    }

    out = []
    for _, r in manifest_day.iterrows():
        hits = [bucket_hit(v, r["bucket"]) for v in arr]
        hits = [x for x in hits if x is not None]
        p = float(np.mean(hits)) if hits else np.nan
        market_p = float(r["price"])
        edge = p - market_p if np.isfinite(p) else np.nan
        rec = {
            "date_hkt": str(r["date_hkt"]),
            "event_slug": r["event_slug"],
            "bucket": r["bucket"],
            "market_price": market_p,
            "result": int(r["result"]),
            "raw_gefs_p": p,
            "raw_edge": edge,
            **common,
        }
        out.append(rec)
    return pd.DataFrame(out)


def print_running_summary(path: Path):
    if not path.exists() or path.stat().st_size == 0:
        return
    try:
        d = pd.read_csv(path)
    except Exception:
        return
    z = d.dropna(subset=["market_price", "result", "raw_gefs_p"]).copy()
    if z.empty:
        return
    print("\n===== RUNNING RAW FILTER SUMMARY =====")
    base_cost = z["market_price"].sum()
    base_pay = z["result"].sum()
    print(f"ALL rows={len(z)} events={z.event_slug.nunique()} cost={base_cost:.3f} payout={base_pay:.0f} net={base_pay-base_cost:.3f} ROI={((base_pay-base_cost)/base_cost*100 if base_cost else float('nan')):.2f}%")
    for threshold in [0.00, 0.02, 0.05, 0.10]:
        s = z[z["raw_edge"] > threshold]
        if s.empty:
            print(f"raw_edge>{threshold*100:.0f}pp: no rows")
            continue
        cost = s["market_price"].sum()
        pay = s["result"].sum()
        roi = (pay-cost)/cost*100 if cost else float("nan")
        print(f"raw_edge>{threshold*100:.0f}pp: rows={len(s)} events={s.event_slug.nunique()} cost={cost:.3f} payout={pay:.0f} net={pay-cost:.3f} ROI={roi:.2f}%")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", default="phase1_full_out/hk_phase2_manifest.csv")
    ap.add_argument("--out-dir", default="phase1_full_out/gefs_full")
    ap.add_argument("--cycle", default="18", choices=["00", "06", "12", "18"])
    ap.add_argument("--perturbed", type=int, default=30)
    ap.add_argument("--lat", type=float, default=HKO_LAT)
    ap.add_argument("--lon", type=float, default=HKO_LON)
    ap.add_argument("--max-dates", type=int, default=0)
    ap.add_argument("--retries", type=int, default=3)
    ap.add_argument("--sleep", type=float, default=0.0)
    args = ap.parse_args()

    manifest = pd.read_csv(args.manifest)
    required = {"date_hkt", "event_slug", "bucket", "price", "result"}
    missing = required - set(manifest.columns)
    if missing:
        raise SystemExit(f"manifest missing columns: {sorted(missing)}")
    manifest["date_hkt"] = manifest["date_hkt"].astype(str)

    dates = sorted(manifest["date_hkt"].unique())
    if args.max_dates:
        dates = dates[:args.max_dates]

    out_dir = Path(args.out_dir)
    members_path = out_dir / "gefs_member_max.csv"
    candidates_path = out_dir / "gefs_candidates_raw.csv"
    state_path = out_dir / "state.json"
    out_dir.mkdir(parents=True, exist_ok=True)

    completed = load_completed(state_path)
    members = member_names(args.perturbed)
    fhrs = list(FHR_DEFAULT)

    print("===== PHASE 2B GEFS FULL RUNNER =====", flush=True)
    print(f"dates={len(dates)} completed={len(completed.intersection(dates))} cycle={args.cycle}Z members={len(members)} fhrs={fhrs}", flush=True)
    print(f"target_point={args.lat:.6f},{args.lon:.6f}", flush=True)
    print("NOTE: raw GEFS probabilities only. No station/grid bias or probability calibration is applied here.\n", flush=True)

    for i, ds in enumerate(dates, 1):
        if ds in completed:
            print(f"[{i}/{len(dates)}] {ds} SKIP completed", flush=True)
            continue

        day_manifest = manifest[manifest["date_hkt"] == ds].copy()
        print(f"[{i}/{len(dates)}] {ds} candidates={len(day_manifest)}", flush=True)
        try:
            mdf, grid = decode_day(
                ds, args.cycle, members, fhrs, args.lat, args.lon,
                retries=max(1, args.retries), sleep_s=max(0.0, args.sleep),
            )
            good_n = int((mdf["status"] == "ok").sum())
            if good_n < max(20, int(math.ceil(len(members) * 0.8))):
                print(f"    NOT checkpointing: only {good_n}/{len(members)} members decoded", flush=True)
                continue

            cdf = candidate_rows(day_manifest, mdf)
            if cdf.empty:
                print("    NOT checkpointing: no candidate probabilities generated", flush=True)
                continue

            append_csv(members_path, mdf)
            append_csv(candidates_path, cdf)
            completed.add(ds)
            atomic_json(state_path, {
                "completed_dates": sorted(completed),
                "last_completed": ds,
                "cycle": args.cycle,
                "members_requested": len(members),
                "forecast_hours": fhrs,
                "target_lat": args.lat,
                "target_lon": args.lon,
                "nearest_grid": list(grid) if grid else None,
            })

            arr = mdf.loc[mdf["status"] == "ok", "remaining_day_max_c"].to_numpy(float)
            print(f"    DONE members={good_n}/{len(members)} mean={arr.mean():.2f}C std={arr.std(ddof=1):.2f}C min={arr.min():.2f}C max={arr.max():.2f}C", flush=True)
            for _, r in cdf.iterrows():
                print(f"    bucket={r['bucket']} market={r['market_price']*100:.2f}% raw_GEFS={r['raw_gefs_p']*100:.2f}% edge={r['raw_edge']*100:+.2f}pp result={int(r['result'])}", flush=True)
        except KeyboardInterrupt:
            raise
        except Exception as e:
            print(f"    DATE ERROR: {type(e).__name__}: {e}", flush=True)

    print("\n===== COMPLETE/PAUSED SUMMARY =====", flush=True)
    print(f"completed_dates={len(completed.intersection(dates))}/{len(dates)}", flush=True)
    print(f"members_csv={members_path}", flush=True)
    print(f"candidates_csv={candidates_path}", flush=True)
    print(f"state={state_path}", flush=True)
    print_running_summary(candidates_path)
    print("\nNEXT: join official HKO observed daily maxima, then fit station/grid bias and probability calibration strictly walk-forward before any trading interpretation.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
