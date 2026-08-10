#!/usr/bin/env python3
from __future__ import annotations

import argparse
import io
import json
from pathlib import Path

import numpy as np
import pandas as pd
import requests

HKO_MAX_URL = "https://data.weather.gov.hk/weatherAPI/opendata/opendata.php?dataType=CLMMAXT&year={year}&rformat=csv&station=HKO"
DAILY_EXTRACT_URLS = [
    "https://www.weather.gov.hk/cis/dailyExtract/dailyExtract_{year}{month:02d}.xml",
    "https://www.weather.gov.hk/cis/dailyExtract/dailyExtract_{year}{month}.xml",
]


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": "polymarket-weather-phase2c/0.3"})
    return s


def fetch_hko_daily_max_clmmax(year: int, s: requests.Session | None = None) -> pd.DataFrame:
    """Monthly-updated HKO daily maximum series used as an independent cross-check/fallback."""
    s = s or _session()
    url = HKO_MAX_URL.format(year=year)
    r = s.get(url, timeout=45)
    r.raise_for_status()
    text = r.content.decode("utf-8-sig", errors="replace")
    lines = text.splitlines()

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
        raise RuntimeError(f"Missing date columns in HKO CLMMAXT CSV: {list(df.columns)}")

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
        frac = float(num.notna().mean()) if len(num) else 0.0
        if frac > 0.5:
            value_candidates.append((score, frac, c))
    if not value_candidates:
        raise RuntimeError(f"Could not identify temperature value column: {list(df.columns)}")
    value_c = sorted(value_candidates, reverse=True)[0][2]

    y = pd.to_numeric(df[year_c], errors="coerce")
    m = pd.to_numeric(df[month_c], errors="coerce")
    d = pd.to_numeric(df[day_c], errors="coerce")
    v = pd.to_numeric(df[value_c], errors="coerce")
    out = pd.DataFrame({"year": y, "month": m, "day": d, "clmmax_c": v}).dropna()
    out[["year", "month", "day"]] = out[["year", "month", "day"]].astype(int)
    out["date_hkt"] = pd.to_datetime(out[["year", "month", "day"]]).dt.strftime("%Y-%m-%d")
    return out[["date_hkt", "clmmax_c"]].drop_duplicates("date_hkt")


def _find_month_blocks(obj):
    """Yield dicts containing HKO's `month` + `dayData` arrays from mislabeled .xml JSON."""
    if isinstance(obj, dict):
        if "month" in obj and "dayData" in obj and isinstance(obj.get("dayData"), list):
            yield obj
        for v in obj.values():
            yield from _find_month_blocks(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _find_month_blocks(v)


def _load_daily_extract_payload(text: str) -> object:
    # Despite the .xml suffix / text/xml Content-Type, the current HKO endpoint body is JSON.
    clean = text.lstrip("\ufeff\n\r\t ")
    try:
        return json.loads(clean)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"HKO Daily Extract body is not parseable JSON: {e}; head={clean[:180]!r}") from e


def fetch_daily_extract_month(year: int, month: int, s: requests.Session | None = None) -> pd.DataFrame:
    """Fetch HKO Daily Extract. row[2] is Absolute Daily Max; cross-validated in main()."""
    s = s or _session()
    errors = []
    payload = None
    chosen = None
    for template in DAILY_EXTRACT_URLS:
        url = template.format(year=year, month=month)
        try:
            r = s.get(url, timeout=45)
            r.raise_for_status()
            obj = _load_daily_extract_payload(r.content.decode("utf-8-sig", errors="replace"))
            blocks = list(_find_month_blocks(obj))
            if not blocks:
                raise RuntimeError("no month/dayData block found")
            # Prefer the requested month if the endpoint returns multiple months.
            matched = [b for b in blocks if int(str(b.get("month")).strip()) == int(month)]
            if not matched:
                raise RuntimeError(f"requested month {month} absent; blocks={[b.get('month') for b in blocks[:15]]}")
            payload = matched[0]
            chosen = url
            break
        except Exception as e:
            errors.append(f"{url}: {type(e).__name__}: {e}")
    if payload is None:
        raise RuntimeError("; ".join(errors))

    rows = []
    for row in payload.get("dayData", []):
        if not isinstance(row, list) or len(row) < 3:
            continue
        day = pd.to_numeric(pd.Series([row[0]]), errors="coerce").iloc[0]
        abs_daily_max = pd.to_numeric(pd.Series([row[2]]), errors="coerce").iloc[0]
        if pd.isna(day) or pd.isna(abs_daily_max):
            continue
        try:
            ds = pd.Timestamp(year=year, month=month, day=int(day)).strftime("%Y-%m-%d")
        except Exception:
            continue
        rows.append({
            "date_hkt": ds,
            "daily_extract_max_c": float(abs_daily_max),
            "daily_extract_url": chosen,
        })
    if not rows:
        raise RuntimeError(f"No daily max rows parsed from {chosen}")
    return pd.DataFrame(rows).drop_duplicates("date_hkt")


def fetch_daily_extract_for_dates(dates: list[str], s: requests.Session | None = None) -> pd.DataFrame:
    s = s or _session()
    ym = sorted({(pd.Timestamp(ds).year, pd.Timestamp(ds).month) for ds in dates})
    frames = []
    for year, month in ym:
        try:
            f = fetch_daily_extract_month(year, month, s=s)
            frames.append(f)
            print(f"DailyExtract {year}-{month:02d}: rows={len(f)}", flush=True)
        except Exception as e:
            print(f"DailyExtract {year}-{month:02d}: ERROR {e}", flush=True)
    if not frames:
        return pd.DataFrame(columns=["date_hkt", "daily_extract_max_c", "daily_extract_url"])
    out = pd.concat(frames, ignore_index=True).drop_duplicates("date_hkt")
    return out[out["date_hkt"].isin(set(dates))].copy()


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
    ap.add_argument("--window", type=int, default=30, help="Last N VALID prior residuals, not last N calendar rows")
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

    dates = daily["date_hkt"].tolist()
    years = sorted({pd.Timestamp(ds).year for ds in dates})
    s = _session()

    # Daily Extract is the preferred source because it is the market-resolution data family and is fresher.
    de = fetch_daily_extract_for_dates(dates, s=s)
    clm_frames = []
    for y in years:
        try:
            clm_frames.append(fetch_hko_daily_max_clmmax(y, s=s))
        except Exception as e:
            print(f"CLMMAXT {y}: ERROR {e}", flush=True)
    clm = pd.concat(clm_frames, ignore_index=True).drop_duplicates("date_hkt") if clm_frames else pd.DataFrame(columns=["date_hkt", "clmmax_c"])

    # Cross-check the inferred Daily Extract column (row[2]) against the independent CLMMAXT series.
    cv = de.merge(clm, on="date_hkt", how="inner")
    print("\n===== DAILY EXTRACT x CLMMAXT CROSS-CHECK =====")
    if cv.empty:
        print("No overlap available; refusing to trust inferred row[2] without validation.")
        return 3
    cv["abs_diff_c"] = (cv["daily_extract_max_c"] - cv["clmmax_c"]).abs()
    exactish = float((cv["abs_diff_c"] <= 0.051).mean())
    print(f"overlap={len(cv)} exact_within_0.05C={exactish*100:.1f}% median_abs_diff={cv.abs_diff_c.median():.3f}C max_abs_diff={cv.abs_diff_c.max():.3f}C")
    if len(cv) >= 10 and (exactish < 0.90 or float(cv["abs_diff_c"].median()) > 0.10):
        print("VALIDATION FAILED: Daily Extract row[2] does not sufficiently match CLMMAXT. Aborting before bias fit.")
        return 4
    print("VALIDATION PASS: Daily Extract row[2] is consistent with HKO daily maximum temperature.")

    actual = pd.DataFrame({"date_hkt": dates})
    actual = actual.merge(de[["date_hkt", "daily_extract_max_c"]], on="date_hkt", how="left")
    actual = actual.merge(clm, on="date_hkt", how="left")
    actual["hko_actual_max_c"] = actual["daily_extract_max_c"].combine_first(actual["clmmax_c"])
    actual["hko_actual_source"] = np.where(
        actual["daily_extract_max_c"].notna(), "daily_extract_json",
        np.where(actual["clmmax_c"].notna(), "clmmax_fallback", "missing")
    )

    z = daily.merge(actual, on="date_hkt", how="left")
    z["raw_resid_mean_c"] = z["hko_actual_max_c"] - z["gefs_mean_max_c"]
    z["raw_resid_median_c"] = z["hko_actual_max_c"] - z["gefs_median_max_c"]

    # Strict walk-forward: today's actual never contributes to today's bias.
    # Use the last N VALID prior residuals so missing calendar rows do not shrink training history.
    history: list[float] = []
    wf_bias = []
    wf_train_n = []
    for resid in z["raw_resid_median_c"].to_numpy(float):
        prior = history[-args.window:] if args.window > 0 else history
        wf_train_n.append(len(prior))
        wf_bias.append(float(np.median(prior)) if len(prior) >= args.min_train else np.nan)
        if np.isfinite(resid):
            history.append(float(resid))

    z["wf_train_n"] = wf_train_n
    z["wf_bias_c"] = wf_bias
    z["wf_corrected_median_max_c"] = z["gefs_median_max_c"] + z["wf_bias_c"]
    z["wf_error_c"] = z["hko_actual_max_c"] - z["wf_corrected_median_max_c"]

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    z.to_csv(args.out, index=False)

    covered = z[z["hko_actual_max_c"].notna()].copy()
    print("\n===== PHASE 2C HKO x GEFS STATION/GRID BIAS =====")
    print(f"GEFS dates: {len(z)}")
    print(f"HKO actual joined: {len(covered)}/{len(z)}")
    print(f"date range: {z.date_hkt.min()} -> {z.date_hkt.max()}")
    print("actual sources:")
    print(z["hko_actual_source"].value_counts(dropna=False).to_string())
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
    cols = [
        "date_hkt", "hko_actual_source", "hko_actual_max_c", "gefs_median_max_c",
        "raw_resid_median_c", "wf_train_n", "wf_bias_c",
        "wf_corrected_median_max_c", "wf_error_c",
    ]
    print(z[cols].tail(30).round(3).to_string(index=False))
    print(f"\nSAVED: {args.out}")
    print("NEXT: shift each 31-member distribution only by its prior-date wf_bias_c, compute bias-corrected bucket probabilities, then evaluate a predeclared threshold on held-out chronology.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
