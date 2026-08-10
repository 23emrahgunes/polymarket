#!/usr/bin/env python3
from __future__ import annotations

import argparse
import math
import re
from pathlib import Path

import numpy as np
import pandas as pd

BUCKET_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*°?C(?:\s+or\s+(higher|below))?\s*$", re.I)


def parse_bucket(s: str):
    m = BUCKET_RE.match(str(s))
    if not m:
        return None, None
    return float(m.group(1)), (m.group(2) or "exact").lower()


def bucket_hit_range(temp_c: float, bucket: str) -> bool | None:
    """Polymarket HK whole-degree range mapping under 0.1C source precision.

    Example validated from resolved market: HKO 33.7C -> 33C bucket.
    Therefore exact N means [N.0, N+1.0), N-or-below means < N+1,
    and N-or-higher means >= N.
    """
    val, mode = parse_bucket(bucket)
    if val is None or not np.isfinite(temp_c):
        return None
    eps = 1e-9
    if mode == "higher":
        return temp_c >= val - eps
    if mode == "below":
        return temp_c < (val + 1.0) - eps
    return (temp_c >= val - eps) and (temp_c < (val + 1.0) - eps)


def econ(z: pd.DataFrame) -> dict:
    if z.empty:
        return {"rows": 0, "events": 0, "wins": 0, "cost": 0.0, "payout": 0.0, "net": 0.0, "roi_pct": np.nan}
    cost = float(z["market_price"].sum())
    payout = float(z["result"].sum())
    net = payout - cost
    return {
        "rows": int(len(z)),
        "events": int(z["event_slug"].nunique()),
        "wins": int(z["result"].sum()),
        "cost": cost,
        "payout": payout,
        "net": net,
        "roi_pct": (net / cost * 100.0) if cost else np.nan,
    }


def show_econ(label: str, z: pd.DataFrame):
    r = econ(z)
    print(
        f"{label}: rows={r['rows']} events={r['events']} wins={r['wins']} "
        f"cost={r['cost']:.3f} payout={r['payout']:.0f} net={r['net']:+.3f} ROI={r['roi_pct']:.2f}%"
    )
    return r


def brier(y, p):
    a = pd.DataFrame({"y": pd.to_numeric(y, errors="coerce"), "p": pd.to_numeric(p, errors="coerce")}).dropna()
    if a.empty:
        return np.nan
    return float(np.mean((a["p"] - a["y"]) ** 2))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", default="phase1_full_out/hk_phase2_manifest.csv")
    ap.add_argument("--members", default="phase1_full_out/gefs_full/gefs_member_max.csv")
    ap.add_argument("--bias", default="phase1_full_out/gefs_full/hko_actual_vs_gefs_walkforward.csv")
    ap.add_argument("--out", default="phase1_full_out/gefs_full/phase2d_candidates_recalc.csv")
    ap.add_argument("--train-frac", type=float, default=0.60)
    ap.add_argument("--min-trades", type=int, default=8)
    args = ap.parse_args()

    manifest = pd.read_csv(args.manifest)
    members = pd.read_csv(args.members)
    bias = pd.read_csv(args.bias)

    manifest["date_hkt"] = manifest["date_hkt"].astype(str)
    members["date_hkt"] = members["date_hkt"].astype(str)
    bias["date_hkt"] = bias["date_hkt"].astype(str)

    members = members[members["status"].astype(str).str.lower() == "ok"].copy()
    members["remaining_day_max_c"] = pd.to_numeric(members["remaining_day_max_c"], errors="coerce")
    members = members.dropna(subset=["remaining_day_max_c"])

    bcols = ["date_hkt", "hko_actual_max_c", "wf_bias_c", "wf_train_n"]
    bias = bias[bcols].copy()
    for c in ["hko_actual_max_c", "wf_bias_c", "wf_train_n"]:
        bias[c] = pd.to_numeric(bias[c], errors="coerce")

    # First validate the bucket semantics against official HKO actuals and the
    # already-resolved Polymarket result column. This must be exact before any PnL interpretation.
    v = manifest.merge(bias[["date_hkt", "hko_actual_max_c"]], on="date_hkt", how="left")
    v["semantic_result"] = [bucket_hit_range(t, b) for t, b in zip(v["hko_actual_max_c"], v["bucket"])]
    v["semantic_result"] = v["semantic_result"].astype("boolean")
    valid = v["semantic_result"].notna().copy()
    valid["semantic_result_int"] = valid["semantic_result"].astype(int)
    valid["result_int"] = pd.to_numeric(valid["result"], errors="coerce").astype(int)
    mism = valid[valid["semantic_result_int"] != valid["result_int"]].copy()

    print("===== PHASE 2D BUCKET SEMANTICS VALIDATION =====")
    print(f"rows_checked={len(valid)}/{len(v)} mismatches={len(mism)}")
    if not mism.empty:
        print(mism[["date_hkt", "event_slug", "bucket", "hko_actual_max_c", "result", "semantic_result_int"]].to_string(index=False))
        raise SystemExit("STOP: bucket-range semantics do not match resolved outcomes; do not backtest.")
    print("VALIDATION PASS: HKO actual -> bucket mapping matches all candidate resolution labels.\n")

    # Recompute member probabilities from stored continuous member maxima. No network/download needed.
    out = []
    bias_map = bias.set_index("date_hkt").to_dict("index")
    member_groups = {d: g.copy() for d, g in members.groupby("date_hkt")}

    for _, r in manifest.iterrows():
        ds = str(r["date_hkt"])
        g = member_groups.get(ds)
        if g is None or g.empty:
            continue
        arr = g["remaining_day_max_c"].to_numpy(float)
        raw_hits = [bucket_hit_range(x, r["bucket"]) for x in arr]
        raw_hits = [x for x in raw_hits if x is not None]
        raw_p = float(np.mean(raw_hits)) if raw_hits else np.nan

        info = bias_map.get(ds, {})
        wf_bias = info.get("wf_bias_c", np.nan)
        actual = info.get("hko_actual_max_c", np.nan)
        wf_train_n = info.get("wf_train_n", np.nan)

        corr_p = np.nan
        corr_p_jeffreys = np.nan
        corr_hits_n = np.nan
        if np.isfinite(wf_bias):
            carr = arr + float(wf_bias)
            chits = [bucket_hit_range(x, r["bucket"]) for x in carr]
            chits = [x for x in chits if x is not None]
            if chits:
                k = int(np.sum(chits))
                n = len(chits)
                corr_hits_n = k
                corr_p = k / n
                corr_p_jeffreys = (k + 0.5) / (n + 1.0)

        market = float(r["price"])
        out.append({
            "date_hkt": ds,
            "event_slug": r["event_slug"],
            "bucket": r["bucket"],
            "market_price": market,
            "result": int(r["result"]),
            "hko_actual_max_c": actual,
            "members_ok": len(arr),
            "wf_train_n": wf_train_n,
            "wf_bias_c": wf_bias,
            "raw_gefs_p_range": raw_p,
            "raw_edge_range": raw_p - market if np.isfinite(raw_p) else np.nan,
            "corr_gefs_p": corr_p,
            "corr_gefs_p_jeffreys": corr_p_jeffreys,
            "corr_hits": corr_hits_n,
            "corr_edge": corr_p - market if np.isfinite(corr_p) else np.nan,
            "corr_edge_jeffreys": corr_p_jeffreys - market if np.isfinite(corr_p_jeffreys) else np.nan,
        })

    d = pd.DataFrame(out).sort_values(["date_hkt", "event_slug", "market_price"]).reset_index(drop=True)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    d.to_csv(args.out, index=False)
    print(f"SAVED: {args.out}")

    print("\n===== RECOMPUTED FULL-SAMPLE DIAGNOSTIC (NOT OOS) =====")
    show_econ("ALL", d)
    for th in [0.00, 0.02, 0.05, 0.10, 0.15, 0.20]:
        show_econ(f"RAW corrected-mapping edge>{th*100:.0f}pp", d[d["raw_edge_range"] > th])
    eligible = d[d["corr_edge"].notna()].copy()
    print(f"\nBias-corrected eligible rows={len(eligible)} dates={eligible.date_hkt.nunique()}")
    for th in [0.00, 0.02, 0.05, 0.10, 0.15, 0.20]:
        show_econ(f"BIAS-CORR edge>{th*100:.0f}pp", eligible[eligible["corr_edge"] > th])

    # Retrospective chronological diagnostic. Split by unique date to avoid same-day leakage.
    dates = sorted(eligible["date_hkt"].unique())
    if len(dates) < 20:
        print("Not enough bias-corrected dates for chronological split.")
        return 0
    cut = max(1, min(len(dates)-1, int(math.floor(len(dates) * args.train_frac))))
    train_dates = set(dates[:cut])
    test_dates = set(dates[cut:])
    tr = eligible[eligible["date_hkt"].isin(train_dates)].copy()
    te = eligible[eligible["date_hkt"].isin(test_dates)].copy()

    print("\n===== CHRONOLOGICAL RETROSPECTIVE HOLDOUT =====")
    print(f"train_dates={len(train_dates)} {min(train_dates)} -> {max(train_dates)}")
    print(f"test_dates={len(test_dates)} {min(test_dates)} -> {max(test_dates)}")
    print("NOTE: retrospective diagnostic only; this 73-day universe has already been inspected during research. True OOS requires forward paper dates after the research cutoff.\n")

    grid = [0.00, 0.02, 0.05, 0.10, 0.15, 0.20, 0.25]
    train_rows = []
    for th in grid:
        s = tr[tr["corr_edge"] > th]
        r = econ(s)
        r["threshold"] = th
        train_rows.append(r)
        show_econ(f"TRAIN corr_edge>{th*100:.0f}pp", s)

    choices = [r for r in train_rows if r["rows"] >= args.min_trades]
    if not choices:
        choices = train_rows
    # Lock from TRAIN only. Prefer highest net; tie-break toward lower threshold / more trades.
    best = sorted(choices, key=lambda r: (r["net"], r["rows"], -r["threshold"]), reverse=True)[0]
    locked = float(best["threshold"])

    print(f"\nLOCKED_FROM_TRAIN threshold={locked*100:.0f}pp (criterion=max train net, min_trades={args.min_trades})")
    show_econ("TEST ALL eligible", te)
    test_sel = te[te["corr_edge"] > locked]
    show_econ(f"TEST LOCKED corr_edge>{locked*100:.0f}pp", test_sel)

    # Previously observed 5pp is reported only as contaminated reference, never as unbiased selection.
    show_econ("TEST reference corr_edge>5pp", te[te["corr_edge"] > 0.05])

    print("\n===== TEST PROBABILITY QUALITY =====")
    print(f"Brier market_price={brier(te['result'], te['market_price']):.6f}")
    print(f"Brier raw_GEFS_range={brier(te['result'], te['raw_gefs_p_range']):.6f}")
    print(f"Brier bias_corrected_GEFS={brier(te['result'], te['corr_gefs_p']):.6f}")
    print(f"Brier bias_corrected_Jeffreys={brier(te['result'], te['corr_gefs_p_jeffreys']):.6f}")

    print("\n===== TEST SELECTED TRADES =====")
    cols = ["date_hkt", "bucket", "market_price", "corr_gefs_p", "corr_edge", "result", "wf_bias_c"]
    z = test_sel[cols].copy()
    for c in ["market_price", "corr_gefs_p", "corr_edge"]:
        z[c] = (z[c] * 100).round(3)
    print(z.to_string(index=False) if not z.empty else "No selected test trades.")

    print("\nNEXT: if the locked TEST result survives, freeze the rule and start a genuine forward paper test on post-research dates. Do not call retrospective results live alpha.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
