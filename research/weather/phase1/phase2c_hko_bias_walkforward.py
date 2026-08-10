#!/usr/bin/env python3
from __future__ import annotations

import argparse
import io
from pathlib import Path

import numpy as np
import pandas as pd
import requests

HKO_MAX_URL = "https://data.weather.gov.hk/weatherAPI/opendata/opendata.php?dataType=CLMMAXT&year={year}&rformat=csv&station=HKO"


def fetch_hko_daily_max(year: int) -> pd.DataFrame:
    url = HKO_MAX_URL.format(year=year)
    r = requests.get(url, timeout=45, headers={"User-Agent": "polymarket-weather-phase2c/0.1"})
    r.raise_for_status()
    text = r.content.decode("utf-8-sig", errors="replace")
    lines = text.splitlines()

    # HKO CSVs can include descriptive lines before the tabular header. Find the
    # first line that looks like a Year/Month/Day header rather than assuming row 0.
    header_i = None
    for i, line in enumerate(lines):
        low = line.lower()
        if "year" in low and "month" in low and "day" in low and "," in line:
            header_i = i
            break
    if header_i is None:
        raise RuntimeError("Could not locate Year/Month/Day header in HKO CLMMAXT CSV")

    df = pd.read_csv(io.StringIO("\n".join(lines[header_i:])))
    df.columns = [str(c).strip() for c in df.columns]
    lower = {c.lower(): c for c in df.columns}

    def col_like(*terms: str):
        for c in df.columns:
            lc = c.lower()
            if all(t in lc for t in terms):
                return c
        return None

    year_c = lower.get("year") or col_like("year")
    month_c = lower.get("month") or col_like("month")
    day_c = lower.get("day") or col_like("day")
    if not all([year_c, month_c, day_c]):
        raise RuntimeError(f"Missing date columns in HKO CSV: {list(df.columns)}")

    # Prefer an explicit data/value/max-temperature column; otherwise choose the
    # first substantially numeric non-date/non-quality column.
    value_candidates = []
    for c in df.columns:
        lc = c.lower()
        if c in {year_c, month_c, day_c}:
            continue
        if any(k in lc for k in ["completeness", "quality", "station", "remark"]):
            continue
        score = 0
        if "max" in lc and "temp" in lc:
            score += 5
        if "temperature" in lc:
            score += 3
        if "data" in lc or "value" in lc:
            score += 2
        num = pd.to_numeric(df[c], errors="coerce")
        numeric_fraction = float(num.notna().mean()) if len(num) else 0.0
        if numeric_fraction > 0.5:
            value_candidates.append((score, numeric_fraction, c))
    if not value_candidates:
        raise RuntimeError(f"Could not identify temperature value column: {list(df.columns)}")
    value_c = sorted(value_candidates, reverse=True)[0][2]

    y = pd.to_numeric(df[year_c], errors="coerce")
    m = pd.to_numeric(df[month_c], errors="coerce")
    d = pd.to_numeric(df[day_c], errors="coerce")
    v = pd.to_numeric(df[value_c], errors="coerce")
    out = pd.DataFrame({"year": y, "month": m, "day": d, "hko_actual_max_c": v}).dropna()
    out[["year", "month", "day"]] = out[["year", "month", "day"]].astype(int)
    out["date_hkt"] = pd.to_datetime(out[["year", "month", "day"]]).dt.strftime("%Y-%m-%d")
    return out[["date_hkt", "hko_actual_max_c"]].drop_duplicates("date_hkt")


def metrics(name: str, actual: pd.Series, pred: pd.Series):
    z = pd.DataFrame({"a": pd.to_numeric(actual, errors="coerce"), "p": pd.to_numeric(pred, errors="coerce")}).dropna()
    if z.empty:
        print(f"{name}: no rows")
        return
    e = z["a"] - z["p"]
    mae = float(np.abs(e).mean())
    rmse = float(np.sqrt(np.mean(np.square(e))))
    print(f"{name}: n={len(z)} bias(actual-pred)={e.mean():+.3f}C median_bias={e.median():+.3f}C MAE={mae:.3f}C RMSE={rmse:.3f}C")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--members", default="phase1_full_out/gefs_full/gefs_member_max.csv")
    ap.add_argument("--out", default="phase1_full_out/gefs_full/hko_actual_vs_gefs_walkforward.csv")
    ap.add_argument("--year", type=int, default=2026)
    ap.add_argument("--min-train", type=int, default=15)
    ap.add_argument("--window", type=int, default=30)
    args = ap.parse_args()

    members = pd.read_csv(args.members)
    members = members[members["status"].astype(str).str.lower() == "ok"].copy()
    members["date_hkt"] = members["date_hkt"].astype(str)
    members["remaining_day_max_c"] = pd.to_numeric(members["remaining_day_max_c"], errors="coerce")
    members = members.dropna(subset=["remaining_day_max_c"])

    daily = members.groupby("date_hkt").agg(
        members_ok=("member", "nunique"),
        gefs_mean_max_c=("remaining_day_max_c", "mean"),
        gefs_median_max_c=("remaining_day_max_c", "median"),
        gefs_std_max_c=("remaining_day_max_c", "std"),
        gefs_min_max_c=("remaining_day_max_c", "min"),
        gefs_max_max_c=("remaining_day_max_c", "max"),
    ).reset_index().sort_values("date_hkt")

    hko = fetch_hko_daily_max(args.year)
    z = daily.merge(hko, on="date_hkt", how="left")
    z["raw_resid_mean_c"] = z["hko_actual_max_c"] - z["gefs_mean_max_c"]
    z["raw_resid_median_c"] = z["hko_actual_max_c"] - z["gefs_median_max_c"]

    # Strict walk-forward bias: date t may only use residuals from dates < t.
    wf_bias = []
    wf_train_n = []
    vals = z["raw_resid_median_c"].to_numpy(float)
    for i in range(len(z)):
        prior = vals[max(0, i - args.window):i]
        prior = prior[np.isfinite(prior)]
        wf_train_n.append(len(prior))
        wf_bias.append(float(np.median(prior)) if len(prior) >= args.min_train else np.nan)
    z["wf_train_n"] = wf_train_n
    z["wf_bias_c"] = wf_bias
    z["wf_corrected_median_max_c"] = z["gefs_median_max_c"] + z["wf_bias_c"]
    z["wf_error_c"] = z["hko_actual_max_c"] - z["wf_corrected_median_max_c"]

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    z.to_csv(args.out, index=False)

    covered = z[z["hko_actual_max_c"].notna()].copy()
    print("===== PHASE 2C HKO x GEFS STATION/GRID BIAS =====")
    print(f"GEFS dates: {len(z)}")
    print(f"HKO actual joined: {len(covered)}/{len(z)}")
    print(f"date range: {z.date_hkt.min()} -> {z.date_hkt.max()}")
    print()
    metrics("RAW GEFS mean", covered["hko_actual_max_c"], covered["gefs_mean_max_c"])
    metrics("RAW GEFS median", covered["hko_actual_max_c"], covered["gefs_median_max_c"])
    wf = covered[covered["wf_bias_c"].notna()].copy()
    metrics("WALK-FORWARD bias-corrected median", wf["hko_actual_max_c"], wf["wf_corrected_median_max_c"])

    covered["month"] = covered["date_hkt"].str.slice(0, 7)
    print("\n===== MONTHLY RAW MEDIAN BIAS =====")
    m = covered.groupby("month").agg(
        dates=("date_hkt", "size"),
        mean_bias_c=("raw_resid_median_c", "mean"),
        median_bias_c=("raw_resid_median_c", "median"),
        mae_c=("raw_resid_median_c", lambda s: float(np.abs(s).mean())),
    ).reset_index()
    print(m.round(3).to_string(index=False))

    print("\n===== WALK-FORWARD SAMPLE =====")
    cols = ["date_hkt", "hko_actual_max_c", "gefs_median_max_c", "raw_resid_median_c", "wf_train_n", "wf_bias_c", "wf_corrected_median_max_c", "wf_error_c"]
    print(z[cols].tail(25).round(3).to_string(index=False))
    print(f"\nSAVED: {args.out}")
    print("NEXT: use only prior-date bias estimates to shift member distributions, then probability-calibrate candidate buckets and evaluate thresholds on held-out chronology.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
