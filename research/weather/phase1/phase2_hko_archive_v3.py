#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
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
JSON_URL = "https://data.weather.gov.hk/weatherAPI/opendata/weather.php?dataType=fnd&lang=en"
RSS_URL = "https://rss.weather.gov.hk/rss/SeveralDaysWeatherForecast_v2.xml"
SOURCES = [("json", JSON_URL), ("rss", RSS_URL)]
ARCHIVE_BASES = [
    "https://app.data.gov.hk/v1/historical-archive",
    "https://api.data.gov.hk/v1/historical-archive",
]
VERSION_RE = re.compile(r"(?<!\d)(\d{8}-\d{4}(?:\d{2})?)(?!\d)")
COMPACT_RE = re.compile(r"(?<!\d)(\d{12}|\d{14})(?!\d)")
BUCKET_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*°?C(?:\s+or\s+(higher|below))?\s*$", re.I)


def iter_values(obj: Any):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from iter_values(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from iter_values(v)
    else:
        yield obj


def to_version_token(value: Any) -> list[str]:
    out: list[str] = []
    if value is None or isinstance(value, bool):
        return out
    s = str(value).strip()
    for m in VERSION_RE.finditer(s):
        out.append(m.group(1))
    for m in COMPACT_RE.finditer(s):
        z = m.group(1)
        try:
            fmt = "%Y%m%d%H%M%S" if len(z) == 14 else "%Y%m%d%H%M"
            dt = datetime.strptime(z, fmt)
            out.append(dt.strftime("%Y%m%d-%H%M%S" if len(z) == 14 else "%Y%m%d-%H%M"))
        except Exception:
            pass
    # Some archive responses have used numeric epoch representations. Convert
    # them to the documented GMT+8 YYYYMMDD-HHMM token expected by get-file.
    try:
        if isinstance(value, (int, float)) or re.fullmatch(r"\d{10,13}", s):
            n = float(value)
            if n >= 1e12:
                dt = pd.to_datetime(n, unit="ms", utc=True).tz_convert(HKT)
            elif n >= 1e9:
                dt = pd.to_datetime(n, unit="s", utc=True).tz_convert(HKT)
            else:
                dt = None
            if dt is not None:
                out.append(dt.strftime("%Y%m%d-%H%M"))
    except Exception:
        pass
    # ISO timestamp fallback.
    if any(ch in s for ch in ("T", ":", "+")):
        try:
            dt = pd.Timestamp(s)
            if dt.tzinfo is None:
                dt = dt.tz_localize(HKT)
            else:
                dt = dt.tz_convert(HKT)
            if 2010 <= dt.year <= 2100:
                out.append(dt.strftime("%Y%m%d-%H%M"))
        except Exception:
            pass
    return out


def parse_versions(obj: Any) -> list[str]:
    found: set[str] = set()
    # Prefer the documented timestamps field, then scan whole response.
    targets: list[Any] = []
    if isinstance(obj, dict) and "timestamps" in obj:
        targets.append(obj.get("timestamps"))
    targets.append(obj)
    for target in targets:
        for v in iter_values(target):
            found.update(to_version_token(v))
    return sorted(found)


def version_dt(token: str) -> pd.Timestamp:
    fmt = "%Y%m%d-%H%M%S" if len(token) == 15 else "%Y%m%d-%H%M"
    return pd.Timestamp(datetime.strptime(token, fmt), tz=HKT)


def raw_diag(obj: Any) -> tuple[Any, str]:
    if not isinstance(obj, dict):
        return None, repr(obj)[:240]
    vc = obj.get("version-count")
    ts = obj.get("timestamps")
    return vc, repr(ts)[:500]


class ArchiveClient:
    def __init__(self, sleep_s: float = 0.12):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": "polymarket-weather-phase2/1.2", "Accept": "*/*"})
        self.sleep_s = sleep_s

    def request_json(self, path: str, params: dict[str, Any], retries: int = 5):
        last = None
        for base in ARCHIVE_BASES:
            for i in range(retries):
                try:
                    r = self.s.get(f"{base}/{path}", params=params, timeout=35)
                    if r.status_code == 429:
                        time.sleep(float(r.headers.get("Retry-After", 1.5)))
                        continue
                    if r.status_code >= 500:
                        time.sleep(min(2 ** i, 8)); continue
                    r.raise_for_status()
                    time.sleep(self.sleep_s)
                    return r.json(), base
                except Exception as e:
                    last = e; time.sleep(min(2 ** i, 6))
        raise RuntimeError(f"archive request failed {path}: {last}")

    def list_versions(self, resource_url: str, start: str, end: str):
        data, base = self.request_json("list-file-versions", {"url": resource_url, "start": start, "end": end})
        return parse_versions(data), data, base

    def get_file(self, kind: str, resource_url: str, version: str, cache_dir: Path):
        cache_dir.mkdir(parents=True, exist_ok=True)
        ext = "json" if kind == "json" else "xml"
        cp = cache_dir / f"{kind}_{version}.{ext}"
        if cp.exists():
            text = cp.read_text(encoding="utf-8", errors="replace")
            return (json.loads(text) if kind == "json" else text), "cache"
        last = None
        for base in ARCHIVE_BASES:
            for i in range(5):
                try:
                    r = self.s.get(f"{base}/get-file", params={"url": resource_url, "time": version}, timeout=35, allow_redirects=True)
                    if r.status_code == 429:
                        time.sleep(float(r.headers.get("Retry-After", 1.5))); continue
                    if r.status_code >= 500:
                        time.sleep(min(2 ** i, 8)); continue
                    r.raise_for_status()
                    text = r.text
                    cp.write_text(text, encoding="utf-8")
                    time.sleep(self.sleep_s)
                    return (r.json() if kind == "json" else text), base
                except Exception as e:
                    last = e; time.sleep(min(2 ** i, 6))
        raise RuntimeError(f"archive get-file failed {kind} {version}: {last}")


def scalar(x: Any):
    return x.get("value") if isinstance(x, dict) else x


def extract_json_forecast(snapshot: dict[str, Any], target_date: str):
    target = target_date.replace("-", "")
    rows = snapshot.get("weatherForecast") or snapshot.get("weatherForecasts") or []
    if not isinstance(rows, list):
        return None
    for f in rows:
        if str(f.get("forecastDate", "")) == target:
            return {
                "hko_update_time": snapshot.get("updateTime"),
                "hko_forecast_max_c": scalar(f.get("forecastMaxtemp")),
                "hko_forecast_min_c": scalar(f.get("forecastMintemp")),
                "hko_forecast_weather": f.get("forecastWeather"),
                "hko_forecast_wind": f.get("forecastWind"),
                "hko_psr": f.get("PSR"),
            }
    return None


def clean_rss_text(xml_text: str) -> str:
    s = html.unescape(xml_text)
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"[ \t]+", " ", s)
    return s


def extract_rss_forecast(xml_text: str, target_date: str):
    text = clean_rss_text(xml_text)
    dt = pd.Timestamp(target_date)
    # Accept 21/3, 21/03, 21-3 around the forecast row.
    pats = [rf"(?:Date/Month\s*)?{dt.day}\s*[/\-]\s*0?{dt.month}\b"]
    pos = -1
    for p in pats:
        m = re.search(p, text, flags=re.I)
        if m:
            pos = m.start(); break
    if pos < 0:
        return None
    chunk = text[pos:pos+1400]
    tm = re.search(r"Temp(?:erature)?\s*Range\s*:\s*(-?\d+(?:\.\d+)?)\s*[-–]\s*(-?\d+(?:\.\d+)?)\s*°?\s*C", chunk, flags=re.I)
    if not tm:
        # Some RSS versions omit the word Range.
        tm = re.search(r"(-?\d+(?:\.\d+)?)\s*[-–]\s*(-?\d+(?:\.\d+)?)\s*°?\s*C", chunk, flags=re.I)
    if not tm:
        return None
    return {
        "hko_update_time": None,
        "hko_forecast_min_c": float(tm.group(1)),
        "hko_forecast_max_c": float(tm.group(2)),
        "hko_forecast_weather": None,
        "hko_forecast_wind": None,
        "hko_psr": None,
    }


def parse_bucket(bucket: str):
    m = BUCKET_RE.match(str(bucket))
    if not m: return None, None
    return float(m.group(1)), (m.group(2) or "exact").lower()


def bucket_features(bucket: str, forecast_max: Any):
    val, mode = parse_bucket(bucket)
    try: fm = float(forecast_max)
    except Exception: return None, None, None
    if val is None: return None, None, None
    if mode == "higher":
        match = fm >= val; signed = fm-val; dist = 0.0 if match else val-fm
    elif mode == "below":
        match = fm <= val; signed = val-fm; dist = 0.0 if match else fm-val
    else:
        match = int(round(fm)) == int(round(val)); signed = fm-val; dist = abs(fm-val)
    return bool(match), float(dist), float(signed)


def choose_version(versions: list[str], decision: pd.Timestamp):
    eligible = []
    for v in versions:
        try: dt = version_dt(v)
        except Exception: continue
        if dt <= decision: eligible.append((dt, v))
    return max(eligible)[1] if eligible else None


def economic(df: pd.DataFrame, mask: pd.Series, label: str):
    z = df[mask].dropna(subset=["price", "result"])
    if z.empty:
        print(f"{label}: no rows"); return
    cost=float(z.price.sum()); payout=float(z.result.sum()); net=payout-cost
    print(f"{label}: rows={len(z)} events={z.event_slug.nunique()} cost={cost:.3f} payout={payout:.0f} net={net:.3f} ROI={(net/cost*100 if cost else float('nan')):.2f}%")


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--manifest",default="phase1_full_out/hk_phase2_manifest.csv")
    ap.add_argument("--out",default="phase1_full_out/hk_phase2_hko_v3.csv")
    ap.add_argument("--cache",default="phase1_full_out/hko_archive_cache_v3")
    ap.add_argument("--decision-hour",type=int,default=8)
    ap.add_argument("--max-dates",type=int,default=0)
    args=ap.parse_args()

    manifest=pd.read_csv(args.manifest)
    dates=sorted(manifest.date_hkt.astype(str).unique())
    if args.max_dates: dates=dates[:args.max_dates]
    client=ArchiveClient(); cache=Path(args.cache); recs=[]
    print(f"[1/3] HKO archive v3: {len(dates)} dates decision={args.decision_hour:02d}:00 HKT",file=sys.stderr)

    for i,date_s in enumerate(dates,1):
        day=pd.Timestamp(date_s,tz=HKT); decision=day+pd.Timedelta(hours=args.decision_hour)
        start=(day-pd.Timedelta(days=2)).strftime("%Y%m%d"); end=day.strftime("%Y%m%d")
        rec={"date_hkt":date_s,"decision_hkt":decision.isoformat(),"hko_archive_error":None,"hko_archive_source":None,"hko_archive_version":None}
        diagnostics=[]
        for kind,url in SOURCES:
            try:
                versions,raw,base=client.list_versions(url,start,end)
                vc,raw_ts=raw_diag(raw)
                diagnostics.append(f"{kind}:version_count={vc};parsed={len(versions)};timestamps={raw_ts}")
                chosen=choose_version(versions,decision)
                if not chosen: continue
                snap,dlbase=client.get_file(kind,url,chosen,cache)
                fx=extract_json_forecast(snap,date_s) if kind=="json" else extract_rss_forecast(snap,date_s)
                if not fx:
                    diagnostics.append(f"{kind}:{chosen}:target_missing")
                    continue
                rec.update(fx); rec["hko_archive_source"]=kind; rec["hko_archive_version"]=chosen; rec["hko_archive_api"]=base; rec["hko_archive_download_api"]=dlbase
                break
            except Exception as e:
                diagnostics.append(f"{kind}:ERR:{str(e)[:180]}")
        if rec.get("hko_forecast_max_c") is None:
            rec["hko_archive_error"]="no_usable_forecast_before_decision"
        rec["hko_archive_diagnostic"]=" || ".join(diagnostics)[:1800]
        recs.append(rec)
        print(f" [{i}/{len(dates)}] {date_s} source={rec.get('hko_archive_source')} version={rec.get('hko_archive_version')} max={rec.get('hko_forecast_max_c')} err={rec.get('hko_archive_error')}",file=sys.stderr)
        if rec.get("hko_archive_error"):
            print("    "+rec["hko_archive_diagnostic"],file=sys.stderr)

    print("[2/3] joining features",file=sys.stderr)
    meta=pd.DataFrame(recs)
    out=manifest[manifest.date_hkt.astype(str).isin(dates)].merge(meta,on="date_hkt",how="left")
    for c in ["hko_forecast_max_c","hko_forecast_min_c","hko_update_time","hko_forecast_weather","hko_forecast_wind","hko_psr"]:
        if c not in out: out[c]=pd.NA
    feats=out.apply(lambda r: bucket_features(r.bucket,r.hko_forecast_max_c),axis=1,result_type="expand")
    feats.columns=["hko_bucket_match","hko_bucket_distance_c","hko_bucket_signed_delta_c"]
    out=pd.concat([out,feats],axis=1); out.to_csv(args.out,index=False)

    print("[3/3] summary",file=sys.stderr)
    covered=out.hko_forecast_max_c.notna()
    print("===== PHASE 2A HKO ARCHIVE V3 =====")
    print(f"Rows: {len(out)}")
    print(f"Events: {out.event_slug.nunique()}")
    print(f"Dates with HKO forecast: {out.loc[covered,'date_hkt'].nunique()}/{len(dates)}")
    if not covered.any():
        print("\nDIAGNOSTICS:")
        print(out[["date_hkt","hko_archive_error","hko_archive_diagnostic"]].drop_duplicates().to_string(index=False))
    print("\n===== ECONOMIC FILTERS =====")
    economic(out,covered,"ALL with HKO data")
    economic(out,covered & (out.hko_bucket_match==True),"HKO exact/threshold match")
    dist=pd.to_numeric(out.hko_bucket_distance_c,errors="coerce")
    economic(out,covered & (dist<=1.0),"HKO within 1C")
    if covered.any():
        print("\n===== RESULT BY MATCH =====")
        g=out[covered].groupby("hko_bucket_match",dropna=False).agg(rows=("result","size"),events=("event_slug","nunique"),wins=("result","sum"),mean_price=("price","mean"),win_rate=("result","mean")).reset_index()
        g["mean_price_pct"]=g.mean_price*100; g["win_rate_pct"]=g.win_rate*100; g["gap_pp"]=(g.win_rate-g.mean_price)*100
        print(g[["hko_bucket_match","rows","events","wins","mean_price_pct","win_rate_pct","gap_pp"]].round(3).to_string(index=False))
    print(f"\nSAVED: {args.out}")

if __name__=="__main__": main()
