#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import requests

HKT = "Asia/Hong_Kong"
RESOURCE_URL = "https://data.weather.gov.hk/weatherAPI/opendata/weather.php?dataType=fnd&lang=en"
ARCHIVE_BASES = [
    "https://app.data.gov.hk/v1/historical-archive",
    "https://api.data.gov.hk/v1/historical-archive",
]
# DATA.GOV.HK documents YYYYMMDD-HHMM. Be tolerant of timestamps embedded in
# URLs/objects and of optional seconds in case the response representation changes.
VERSION_TOKEN_RE = re.compile(r"(?<!\d)(\d{8}-\d{4}(?:\d{2})?)(?!\d)")
BUCKET_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*°?C(?:\s+or\s+(higher|below))?\s*$", re.I)


def walk_values(obj: Any):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from walk_values(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from walk_values(v)
    else:
        yield obj


def parse_versions(obj: Any) -> list[str]:
    """Extract archive timestamps wherever they appear in the JSON response."""
    found: set[str] = set()
    for value in walk_values(obj):
        if value is None:
            continue
        s = str(value)
        for m in VERSION_TOKEN_RE.finditer(s):
            found.add(m.group(1))
    return sorted(found)


def version_dt(s: str) -> pd.Timestamp:
    fmt = "%Y%m%d-%H%M%S" if len(s) == 15 else "%Y%m%d-%H%M"
    return pd.Timestamp(datetime.strptime(s, fmt), tz=HKT)


class ArchiveClient:
    def __init__(self, sleep_s: float = 0.15):
        self.s = requests.Session()
        self.s.headers.update({
            "User-Agent": "polymarket-weather-phase2/1.1",
            "Accept": "application/json,text/plain,*/*",
        })
        self.sleep_s = sleep_s

    def get_json(self, path: str, params: dict[str, Any], retries: int = 5):
        last = None
        for base in ARCHIVE_BASES:
            for i in range(retries):
                try:
                    r = self.s.get(f"{base}/{path}", params=params, timeout=35)
                    if r.status_code == 429:
                        time.sleep(float(r.headers.get("Retry-After", 1.5)))
                        continue
                    if r.status_code >= 500:
                        time.sleep(min(2 ** i, 8))
                        continue
                    r.raise_for_status()
                    time.sleep(self.sleep_s)
                    return r.json(), base
                except Exception as e:
                    last = e
                    time.sleep(min(2 ** i, 6))
        raise RuntimeError(f"Archive GET failed for {path}: {last}")

    def list_versions(self, start: str, end: str) -> tuple[list[str], str, Any]:
        data, base = self.get_json("list-file-versions", {
            "url": RESOURCE_URL,
            "start": start,
            "end": end,
        })
        return parse_versions(data), base, data

    def get_file(self, version: str, cache_dir: Path) -> tuple[dict[str, Any], str]:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cp = cache_dir / f"fnd_{version}.json"
        if cp.exists():
            return json.loads(cp.read_text(encoding="utf-8")), "cache"

        last = None
        for base in ARCHIVE_BASES:
            for i in range(5):
                try:
                    r = self.s.get(
                        f"{base}/get-file",
                        params={"url": RESOURCE_URL, "time": version},
                        timeout=35,
                        allow_redirects=True,
                    )
                    if r.status_code == 429:
                        time.sleep(float(r.headers.get("Retry-After", 1.5)))
                        continue
                    if r.status_code >= 500:
                        time.sleep(min(2 ** i, 8))
                        continue
                    r.raise_for_status()
                    data = r.json()
                    cp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                    time.sleep(self.sleep_s)
                    return data, base
                except Exception as e:
                    last = e
                    time.sleep(min(2 ** i, 6))
        raise RuntimeError(f"Archive file download failed {version}: {last}")


def scalar_value(x: Any):
    if isinstance(x, dict):
        return x.get("value")
    return x


def extract_forecast(snapshot: dict[str, Any], target_date: str) -> dict[str, Any] | None:
    target_compact = target_date.replace("-", "")
    forecasts = snapshot.get("weatherForecast") or snapshot.get("weatherForecasts") or []
    if not isinstance(forecasts, list):
        return None
    for f in forecasts:
        if str(f.get("forecastDate", "")) == target_compact:
            return {
                "hko_update_time": snapshot.get("updateTime"),
                "hko_forecast_max_c": scalar_value(f.get("forecastMaxtemp")),
                "hko_forecast_min_c": scalar_value(f.get("forecastMintemp")),
                "hko_forecast_max_rh": scalar_value(f.get("forecastMaxrh")),
                "hko_forecast_min_rh": scalar_value(f.get("forecastMinrh")),
                "hko_forecast_weather": f.get("forecastWeather"),
                "hko_forecast_wind": f.get("forecastWind"),
                "hko_psr": f.get("PSR"),
            }
    return None


def parse_bucket(bucket: str):
    m = BUCKET_RE.match(str(bucket))
    if not m:
        return None, None
    return float(m.group(1)), (m.group(2) or "exact").lower()


def bucket_features(bucket: str, forecast_max: Any):
    val, mode = parse_bucket(bucket)
    try:
        fm = float(forecast_max)
    except Exception:
        return None, None, None
    if val is None:
        return None, None, None
    if mode == "higher":
        match = fm >= val
        signed = fm - val
        dist = 0.0 if match else val - fm
    elif mode == "below":
        match = fm <= val
        signed = val - fm
        dist = 0.0 if match else fm - val
    else:
        match = int(round(fm)) == int(round(val))
        signed = fm - val
        dist = abs(fm - val)
    return bool(match), float(dist), float(signed)


def choose_version(versions: list[str], decision_hkt: pd.Timestamp):
    eligible = []
    for v in versions:
        try:
            dt = version_dt(v)
        except Exception:
            continue
        if dt <= decision_hkt:
            eligible.append((dt, v))
    return max(eligible)[1] if eligible else None


def response_shape(obj: Any) -> str:
    if isinstance(obj, dict):
        return "dict keys=" + ",".join(map(str, list(obj.keys())[:20]))
    if isinstance(obj, list):
        return f"list len={len(obj)}"
    return type(obj).__name__


def economic_summary(df: pd.DataFrame, mask: pd.Series, label: str):
    z = df[mask].dropna(subset=["price", "result"]).copy()
    if z.empty:
        print(f"{label}: no rows")
        return
    cost = float(z["price"].sum())
    payout = float(z["result"].sum())
    net = payout - cost
    roi = (net / cost * 100) if cost else float("nan")
    print(f"{label}: rows={len(z)} events={z.event_slug.nunique()} cost={cost:.3f} payout={payout:.0f} net={net:.3f} ROI={roi:.2f}%")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", default="phase1_full_out/hk_phase2_manifest.csv")
    ap.add_argument("--out", default="phase1_full_out/hk_phase2_hko_v2.csv")
    ap.add_argument("--cache", default="phase1_full_out/hko_archive_cache_v2")
    ap.add_argument("--decision-hour", type=int, default=8)
    ap.add_argument("--max-dates", type=int, default=0)
    args = ap.parse_args()

    manifest = pd.read_csv(args.manifest)
    required = {"date_hkt", "event_slug", "bucket", "price", "result"}
    missing = required - set(manifest.columns)
    if missing:
        raise SystemExit(f"Manifest missing columns: {sorted(missing)}")

    dates = sorted(manifest["date_hkt"].astype(str).unique())
    if args.max_dates:
        dates = dates[: args.max_dates]

    client = ArchiveClient()
    cache_dir = Path(args.cache)
    by_date: dict[str, dict[str, Any]] = {}

    print(f"[1/3] HKO archive lookup: {len(dates)} dates, decision={args.decision_hour:02d}:00 HKT", file=sys.stderr)
    for i, date_s in enumerate(dates, 1):
        day = pd.Timestamp(date_s, tz=HKT)
        decision = day + pd.Timedelta(hours=args.decision_hour)
        # Include two preceding days so a pre-08:00 decision always has a prior
        # published 9-day forecast candidate even if archive capture was sparse.
        start = (day - pd.Timedelta(days=2)).strftime("%Y%m%d")
        end = day.strftime("%Y%m%d")
        rec: dict[str, Any] = {
            "date_hkt": date_s,
            "decision_hkt": decision.isoformat(),
            "hko_archive_version": None,
            "hko_archive_error": None,
        }
        try:
            versions, base, raw = client.list_versions(start, end)
            chosen = choose_version(versions, decision)
            rec["hko_archive_versions_found"] = len(versions)
            rec["hko_archive_api"] = base
            rec["hko_archive_response_shape"] = response_shape(raw)
            rec["hko_archive_versions_sample"] = "|".join(versions[-8:])
            if not chosen:
                rec["hko_archive_error"] = "no_version_at_or_before_decision"
            else:
                snap, dlbase = client.get_file(chosen, cache_dir)
                fx = extract_forecast(snap, date_s)
                rec["hko_archive_version"] = chosen
                rec["hko_archive_download_api"] = dlbase
                if fx:
                    rec.update(fx)
                else:
                    rec["hko_archive_error"] = "target_date_missing_in_snapshot"
        except Exception as e:
            rec["hko_archive_error"] = str(e)[:500]

        by_date[date_s] = rec
        print(
            f"  [{i}/{len(dates)}] {date_s} versions={rec.get('hko_archive_versions_found',0)} "
            f"version={rec.get('hko_archive_version')} max={rec.get('hko_forecast_max_c')} "
            f"err={rec.get('hko_archive_error')} sample={rec.get('hko_archive_versions_sample','')}",
            file=sys.stderr,
        )

    print("[2/3] Joining HKO features...", file=sys.stderr)
    meta = pd.DataFrame(by_date.values())
    out = manifest[manifest["date_hkt"].astype(str).isin(dates)].merge(meta, on="date_hkt", how="left")

    # Always create feature columns, even when archive coverage is zero.
    for c in [
        "hko_forecast_max_c", "hko_forecast_min_c", "hko_update_time",
        "hko_forecast_max_rh", "hko_forecast_min_rh", "hko_forecast_weather",
        "hko_forecast_wind", "hko_psr",
    ]:
        if c not in out.columns:
            out[c] = pd.NA

    feats = out.apply(
        lambda r: bucket_features(r.get("bucket"), r.get("hko_forecast_max_c")),
        axis=1,
        result_type="expand",
    )
    feats.columns = ["hko_bucket_match", "hko_bucket_distance_c", "hko_bucket_signed_delta_c"]
    out = pd.concat([out, feats], axis=1)
    out.to_csv(args.out, index=False)

    print("[3/3] Summary", file=sys.stderr)
    covered = out["hko_forecast_max_c"].notna()
    unique_dates_covered = out.loc[covered, "date_hkt"].nunique()
    print("===== PHASE 2A HKO ARCHIVE V2 =====")
    print(f"Rows: {len(out)}")
    print(f"Events: {out.event_slug.nunique()}")
    print(f"Dates with HKO forecast: {unique_dates_covered}/{len(dates)}")

    errs = out.loc[out["hko_archive_error"].notna(), [
        "date_hkt", "hko_archive_error", "hko_archive_response_shape",
        "hko_archive_versions_found", "hko_archive_versions_sample",
    ]].drop_duplicates() if "hko_archive_error" in out.columns else pd.DataFrame()
    if not errs.empty:
        print("\nArchive errors/diagnostics:")
        print(errs.to_string(index=False))

    print("\n===== ECONOMIC FILTERS (1 share per selected bucket, no fees/slippage) =====")
    economic_summary(out, covered, "ALL manifest rows with HKO data")
    economic_summary(out, covered & (out["hko_bucket_match"] == True), "HKO exact/threshold match")
    dist = pd.to_numeric(out["hko_bucket_distance_c"], errors="coerce")
    economic_summary(out, covered & (dist <= 1.0), "HKO within 1C")

    print("\n===== RESULT BY HKO MATCH =====")
    tmp = out[covered].copy()
    if tmp.empty:
        print("No HKO forecast rows yet; inspect diagnostics above.")
    else:
        g = tmp.groupby("hko_bucket_match", dropna=False).agg(
            rows=("result", "size"),
            events=("event_slug", "nunique"),
            wins=("result", "sum"),
            mean_price=("price", "mean"),
            win_rate=("result", "mean"),
        ).reset_index()
        g["mean_price_pct"] = g["mean_price"] * 100
        g["win_rate_pct"] = g["win_rate"] * 100
        g["gap_pp"] = (g["win_rate"] - g["mean_price"]) * 100
        print(g[["hko_bucket_match", "rows", "events", "wins", "mean_price_pct", "win_rate_pct", "gap_pp"]].round(3).to_string(index=False))

    print(f"\nSAVED: {args.out}")


if __name__ == "__main__":
    main()
