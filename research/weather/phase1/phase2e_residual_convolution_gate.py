#!/usr/bin/env python3
from __future__ import annotations

import argparse
import math
from pathlib import Path

import numpy as np
import pandas as pd

from phase2d_bias_corrected_holdout import bucket_hit_range


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


def show_econ(label: str, z: pd.DataFrame) -> dict:
    r = econ(z)
    print(
        f"{label}: rows={r['rows']} events={r['events']} wins={r['wins']} "
        f"cost={r['cost']:.3f} payout={r['payout']:.0f} net={r['net']:+.3f} ROI={r['roi_pct']:.2f}%"
    )
    return r


def brier(y, p) -> float:
    z = pd.DataFrame({"y": pd.to_numeric(y, errors="coerce"), "p": pd.to_numeric(p, errors="coerce")}).dropna()
    if z.empty:
        return float("nan")
    return float(np.mean((z["p"] - z["y"]) ** 2))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", default="phase1_full_out/hk_phase2_manifest.csv")
    ap.add_argument("--members", default="phase1_full_out/gefs_full/gefs_member_max.csv")
    ap.add_argument("--bias", default="phase1_full_out/gefs_full/hko_actual_vs_gefs_walkforward.csv")
    ap.add_argument("--out", default="phase1_full_out/gefs_full/phase2e_residual_convolution.csv")
    ap.add_argument("--window", type=int, default=30)
    ap.add_argument("--min-train-residuals", type=int, default=15)
    ap.add_argument("--train-frac", type=float, default=0.60)
    ap.add_argument("--min-threshold-trades", type=int, default=8)
    ap.add_argument("--min-test-trades", type=int, default=5)
    args = ap.parse_args()

    manifest = pd.read_csv(args.manifest)
    members = pd.read_csv(args.members)
    bias = pd.read_csv(args.bias)

    for d in [manifest, members, bias]:
        d["date_hkt"] = d["date_hkt"].astype(str)

    members = members[members["status"].astype(str).str.lower() == "ok"].copy()
    members["remaining_day_max_c"] = pd.to_numeric(members["remaining_day_max_c"], errors="coerce")
    members = members.dropna(subset=["remaining_day_max_c"])

    need_bias = ["date_hkt", "hko_actual_max_c", "gefs_median_max_c", "raw_resid_median_c"]
    missing = [c for c in need_bias if c not in bias.columns]
    if missing:
        raise SystemExit(f"bias file missing columns: {missing}")
    for c in need_bias[1:]:
        bias[c] = pd.to_numeric(bias[c], errors="coerce")
    bias = bias.sort_values("date_hkt").drop_duplicates("date_hkt")

    # Hard safety check: official HKO actuals must reproduce all candidate results.
    actual_map = bias.set_index("date_hkt")["hko_actual_max_c"].to_dict()
    semantic = []
    for _, r in manifest.iterrows():
        a = actual_map.get(str(r["date_hkt"]), np.nan)
        semantic.append(bucket_hit_range(a, r["bucket"]) if np.isfinite(a) else None)
    chk = manifest.copy()
    chk["semantic_result"] = semantic
    chk = chk[chk["semantic_result"].notna()].copy()
    chk["semantic_result"] = chk["semantic_result"].astype(int)
    chk["result_int"] = pd.to_numeric(chk["result"], errors="coerce").astype(int)
    mism = chk[chk["semantic_result"] != chk["result_int"]]
    print("===== PHASE 2E SAFETY CHECK =====")
    print(f"bucket_semantics_checked={len(chk)}/{len(manifest)} mismatches={len(mism)}")
    if len(chk) != len(manifest) or not mism.empty:
        if not mism.empty:
            print(mism[["date_hkt", "bucket", "result", "semantic_result"]].to_string(index=False))
        raise SystemExit("STOP: HKO actual/bucket semantics validation failed")
    print("VALIDATION PASS\n")

    member_groups = {
        ds: g["remaining_day_max_c"].to_numpy(float)
        for ds, g in members.groupby("date_hkt")
    }
    residual_by_date = {
        str(r.date_hkt): float(r.raw_resid_median_c)
        for r in bias.itertuples(index=False)
        if np.isfinite(r.raw_resid_median_c)
    }
    all_dates = sorted(manifest["date_hkt"].unique())

    rows = []
    for ds in all_dates:
        arr = member_groups.get(ds)
        if arr is None or len(arr) < 20:
            continue
        prior_dates = [d for d in sorted(residual_by_date) if d < ds]
        prior_dates = prior_dates[-args.window:]
        residuals = np.array([residual_by_date[d] for d in prior_dates], dtype=float)
        if len(residuals) < args.min_train_residuals:
            continue

        # Empirical predictive distribution: current GEFS ensemble convolved with
        # strictly prior-date station/model residuals. No current/future actual is used.
        pred_samples = (arr[:, None] + residuals[None, :]).reshape(-1)

        day = manifest[manifest["date_hkt"] == ds]
        for _, r in day.iterrows():
            hits = [bucket_hit_range(float(x), r["bucket"]) for x in pred_samples]
            hits = [x for x in hits if x is not None]
            p = float(np.mean(hits)) if hits else np.nan
            # Small finite-sample smoothing over empirical convolution samples.
            k = int(np.sum(hits)) if hits else 0
            n = len(hits)
            p_jeff = (k + 0.5) / (n + 1.0) if n else np.nan
            market = float(r["price"])
            rows.append({
                "date_hkt": ds,
                "event_slug": r["event_slug"],
                "bucket": r["bucket"],
                "market_price": market,
                "result": int(r["result"]),
                "members_ok": len(arr),
                "residual_train_n": len(residuals),
                "residual_median_c": float(np.median(residuals)),
                "residual_std_c": float(np.std(residuals, ddof=1)) if len(residuals) > 1 else 0.0,
                "predictive_samples_n": n,
                "conv_p": p,
                "conv_p_jeffreys": p_jeff,
                "conv_edge": p - market if np.isfinite(p) else np.nan,
                "conv_edge_jeffreys": p_jeff - market if np.isfinite(p_jeff) else np.nan,
            })

    d = pd.DataFrame(rows).sort_values(["date_hkt", "event_slug", "market_price"]).reset_index(drop=True)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    d.to_csv(args.out, index=False)
    print(f"SAVED: {args.out}")
    print(f"eligible_rows={len(d)} eligible_dates={d.date_hkt.nunique() if not d.empty else 0}")
    if d.empty:
        raise SystemExit("STOP: no Phase 2E rows")

    print("\n===== FULL-SAMPLE DIAGNOSTIC (CONTAMINATED / NOT OOS) =====")
    show_econ("ALL eligible", d)
    for th in [0.00, 0.02, 0.05, 0.10, 0.15, 0.20]:
        show_econ(f"CONV edge>{th*100:.0f}pp", d[d["conv_edge"] > th])

    dates = sorted(d["date_hkt"].unique())
    cut = max(1, min(len(dates) - 1, int(math.floor(len(dates) * args.train_frac))))
    train_dates = set(dates[:cut])
    test_dates = set(dates[cut:])
    tr = d[d["date_hkt"].isin(train_dates)].copy()
    te = d[d["date_hkt"].isin(test_dates)].copy()

    print("\n===== PHASE 2E CHRONOLOGICAL HOLDOUT =====")
    print(f"train_dates={len(train_dates)} {min(train_dates)} -> {max(train_dates)}")
    print(f"test_dates={len(test_dates)} {min(test_dates)} -> {max(test_dates)}")
    print("NOTE: retrospective holdout only. True OOS begins with forward paper after this research cutoff.\n")

    grid = [0.00, 0.02, 0.05, 0.10, 0.15, 0.20]
    train_stats = []
    for th in grid:
        s = tr[tr["conv_edge"] > th]
        r = show_econ(f"TRAIN conv_edge>{th*100:.0f}pp", s)
        r["threshold"] = th
        train_stats.append(r)

    choices = [r for r in train_stats if r["rows"] >= args.min_threshold_trades]
    if not choices:
        choices = train_stats
    # Freeze threshold using TRAIN only: maximize train net, tie-break toward more trades then lower threshold.
    best = sorted(choices, key=lambda r: (r["net"], r["rows"], -r["threshold"]), reverse=True)[0]
    locked = float(best["threshold"])

    print(f"\nLOCKED_FROM_TRAIN threshold={locked*100:.0f}pp")
    test_all = show_econ("TEST ALL eligible", te)
    test_sel_df = te[te["conv_edge"] > locked].copy()
    test_sel = show_econ(f"TEST LOCKED conv_edge>{locked*100:.0f}pp", test_sel_df)

    bm = brier(te["result"], te["market_price"])
    bc = brier(te["result"], te["conv_p"])
    bj = brier(te["result"], te["conv_p_jeffreys"])
    print("\n===== TEST PROBABILITY QUALITY =====")
    print(f"Brier market_price={bm:.6f}")
    print(f"Brier residual_convolution={bc:.6f}")
    print(f"Brier residual_convolution_Jeffreys={bj:.6f}")
    print(f"Brier improvement vs market={bm-bc:+.6f}")

    print("\n===== TEST SELECTED TRADES =====")
    cols = ["date_hkt", "bucket", "market_price", "conv_p", "conv_edge", "result", "residual_train_n", "residual_median_c", "residual_std_c"]
    z = test_sel_df[cols].copy()
    for c in ["market_price", "conv_p", "conv_edge"]:
        z[c] = (z[c] * 100).round(3)
    print(z.round(3).to_string(index=False) if not z.empty else "No selected test trades.")

    # Final research gate. We require both probability quality and economic survival,
    # plus enough test trades that one isolated jackpot cannot pass the gate.
    probability_pass = bool(np.isfinite(bm) and np.isfinite(bc) and bc < bm)
    economic_pass = bool(test_sel["rows"] >= args.min_test_trades and test_sel["net"] > 0)
    gate_pass = probability_pass and economic_pass

    print("\n===== PHASE 2E FINAL GATE =====")
    print(f"probability_pass={probability_pass} (convolution Brier < market Brier)")
    print(f"economic_pass={economic_pass} (test trades>={args.min_test_trades} and net>0)")
    print(f"FINAL={'PASS -> FREEZE RULE + FORWARD PAPER' if gate_pass else 'FAIL -> DO NOT DEPLOY; CLOSE THIS GEFS/HK THESIS'}")
    print("No live trading conclusion is permitted from this retrospective dataset.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
