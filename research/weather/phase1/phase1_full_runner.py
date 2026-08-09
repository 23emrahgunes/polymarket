#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import requests

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
TEMP_RE = re.compile(r"^(Highest|Lowest) temperature in (.+?) on (.+?)\??$", re.I)
DEFAULT_HORIZONS = [24, 12, 6, 3, 1]
PRICE_BINS = [0, .001, .005, .01, .02, .03, .05, .10, .20, .40, .60, .80, .95, .99, 1.000001]
PRICE_LABELS = [
    "0–0.1¢", "0.1–0.5¢", "0.5–1¢", "1–2¢", "2–3¢", "3–5¢",
    "5–10¢", "10–20¢", "20–40¢", "40–60¢", "60–80¢", "80–95¢",
    "95–99¢", "99–100¢"
]


def parse_jsonish(x: Any, default):
    if isinstance(x, (list, dict)):
        return x
    if isinstance(x, str):
        try:
            return json.loads(x)
        except Exception:
            return default
    return default


def ts(s: str) -> int:
    d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return int(d.timestamp())


def iso(t: int) -> str:
    return datetime.fromtimestamp(int(t), timezone.utc).isoformat()


class Http:
    def __init__(self):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": "weather-edge-lab-phase1/1.1", "Accept": "application/json"})

    def get(self, url: str, params=None, retries=6):
        last = None
        for i in range(retries):
            try:
                r = self.s.get(url, params=params, timeout=35)
                if r.status_code == 429:
                    time.sleep(float(r.headers.get("Retry-After", 1.5)))
                    continue
                if r.status_code >= 500:
                    time.sleep(min(2 ** i, 8))
                    continue
                r.raise_for_status()
                time.sleep(.10)
                return r.json()
            except Exception as e:
                last = e
                time.sleep(min(2 ** i, 8))
        raise RuntimeError(f"GET failed {url}: {last}")


def discover_events(http: Http, start: str, end: str, max_events: int = 0):
    """Discover resolved temperature events using Gamma keyset pagination.

    Gamma's cursor-based endpoint is required for large historical scans.  The
    legacy /events offset route can fail with HTTP 422 once the offset grows
    large enough.  Keyset pagination returns {events, next_cursor} and accepts
    tag/date filters while including nested markets.
    """
    out = []
    after_cursor = None
    page = 0

    while True:
        params = {
            "closed": "true",
            "limit": 500,
            "ascending": "true",
            "order": "endDate",
            "end_date_min": f"{start}T00:00:00Z",
            "end_date_max": f"{end}T23:59:59Z",
            "tag_slug": "weather",
            "related_tags": "true",
        }
        if after_cursor:
            params["after_cursor"] = after_cursor

        data = http.get(f"{GAMMA}/events/keyset", params=params)
        if not isinstance(data, dict):
            raise RuntimeError(f"Unexpected Gamma keyset response: {type(data)}")

        batch = data.get("events", [])
        if not isinstance(batch, list):
            raise RuntimeError(f"Unexpected Gamma events payload: {type(batch)}")

        page += 1
        for e in batch:
            title = (e.get("title") or "").strip()
            if TEMP_RE.match(title):
                out.append(e)
                if max_events and len(out) >= max_events:
                    return out

        next_cursor = data.get("next_cursor")
        print(
            f"Gamma keyset page={page}: {len(batch)} rows, temp matches={len(out)}, "
            f"next_cursor={'yes' if next_cursor else 'no'}",
            file=sys.stderr,
        )

        if not batch or not next_cursor or next_cursor == after_cursor:
            break
        after_cursor = next_cursor

    return out


def normalize_event(event: dict):
    title = (event.get("title") or "").strip()
    m = TEMP_RE.match(title)
    if not m or not event.get("endDate"):
        return None
    kind = m.group(1).lower()
    city = m.group(2).strip()
    slug = str(event.get("slug") or "")
    anchor = ts(event["endDate"])

    markets = event.get("markets") or []
    if not markets:
        return None

    rows = []
    for mk in markets:
        outcomes = parse_jsonish(mk.get("outcomes"), [])
        prices = parse_jsonish(mk.get("outcomePrices"), [])
        tokens = parse_jsonish(mk.get("clobTokenIds"), [])
        if not outcomes or not tokens:
            continue
        try:
            yi = next(i for i, o in enumerate(outcomes) if str(o).strip().lower() == "yes")
        except StopIteration:
            yi = 0
        if yi >= len(tokens):
            continue
        try:
            final_yes = float(prices[yi]) if yi < len(prices) else np.nan
        except Exception:
            final_yes = np.nan
        bucket = mk.get("groupItemTitle") or mk.get("question") or mk.get("slug")
        rows.append({
            "event_slug": slug,
            "event_id": event.get("id"),
            "market_id": mk.get("id"),
            "market_slug": mk.get("slug"),
            "bucket": str(bucket),
            "yes_token": str(tokens[yi]),
            "yes_final_price": final_yes,
        })

    winners = [r for r in rows if not pd.isna(r["yes_final_price"]) and r["yes_final_price"] >= .99]
    valid = len(winners) == 1
    winner_token = winners[0]["yes_token"] if valid else None
    for r in rows:
        r["result"] = 1 if valid and r["yes_token"] == winner_token else (0 if valid else np.nan)

    er = {
        "event_slug": slug,
        "event_id": event.get("id"),
        "title": title,
        "city": city,
        "kind": kind,
        "anchor_ts": anchor,
        "anchor_iso": iso(anchor),
        "anchor_source": "event.endDate",
        "winner_bucket": winners[0]["bucket"] if valid else None,
        "resolution_valid": valid,
        "n_bucket_markets": len(rows),
    }
    return er, rows


def load_history(http: Http, token: str, start_ts: int, end_ts: int, fidelity: int, cache_dir: Path):
    cache_dir.mkdir(parents=True, exist_ok=True)
    cp = cache_dir / f"{token}_{start_ts}_{end_ts}_{fidelity}.json"
    if cp.exists():
        try:
            return json.loads(cp.read_text(encoding="utf-8"))
        except Exception:
            pass
    data = http.get(f"{CLOB}/prices-history", params={
        "market": token,
        "startTs": start_ts,
        "endTs": end_ts,
        "fidelity": fidelity,
    })
    cp.write_text(json.dumps(data), encoding="utf-8")
    return data


def points_from_history(data):
    if isinstance(data, dict):
        pts = data.get("history", [])
    elif isinstance(data, list):
        pts = data
    else:
        pts = []
    out = []
    for p in pts:
        try:
            out.append((int(float(p.get("t", p.get("timestamp")))), float(p.get("p", p.get("price")))))
        except Exception:
            continue
    out.sort()
    return out


def price_at_or_before(points, target: int, max_stale_s: int):
    best = None
    for t, p in points:
        if t <= target:
            best = (t, p)
        else:
            break
    if best is None:
        return None
    stale = target - best[0]
    if stale > max_stale_s:
        return None
    return best[1], stale, best[0]


def bootstrap_gap(g: pd.DataFrame, n_boot: int, seed: int):
    clusters = g["cluster_id"].astype(str).unique().tolist()
    if len(clusters) < 2:
        return np.nan, np.nan
    by = {c: g[g.cluster_id.astype(str) == c] for c in clusters}
    rng = random.Random(seed)
    vals = []
    for _ in range(n_boot):
        sampled = [rng.choice(clusters) for _ in clusters]
        b = pd.concat([by[c] for c in sampled], ignore_index=True)
        vals.append(float(b.result.mean() - b.price.mean()))
    return float(np.quantile(vals, .025)), float(np.quantile(vals, .975))


def calibration(df: pd.DataFrame, dims: list[str], n_boot: int, seed: int):
    rows = []
    cols = dims + ["horizon_h", "price_bin"]
    for keys, g in df.groupby(cols, observed=True):
        if not isinstance(keys, tuple):
            keys = (keys,)
        row = dict(zip(cols, keys))
        mp = float(g.price.mean())
        wr = float(g.result.mean())
        lo, hi = bootstrap_gap(g, n_boot, seed)
        row.update({
            "n_buckets": len(g),
            "n_events": int(g.event_slug.nunique()),
            "n_independent_city_days": int(g.cluster_id.nunique()),
            "wins": int(g.result.sum()),
            "mean_price": mp,
            "win_rate": wr,
            "calibration_gap": wr - mp,
            "gap_ci_low_cluster_boot": lo,
            "gap_ci_high_cluster_boot": hi,
            "mean_staleness_min": float(g.staleness_s.mean()/60),
        })
        rows.append(row)
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026-03-01")
    ap.add_argument("--end", default=datetime.now(timezone.utc).date().isoformat())
    ap.add_argument("--out", default="phase1_full_out")
    ap.add_argument("--horizons", default="24,12,6,3,1")
    ap.add_argument("--fidelity", type=int, default=5)
    ap.add_argument("--max-staleness-min", type=int, default=90)
    ap.add_argument("--bootstrap", type=int, default=600)
    ap.add_argument("--max-events", type=int, default=0)
    args = ap.parse_args()

    horizons = [int(x) for x in args.horizons.split(",") if x.strip()]
    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    cache = outdir / "cache"
    http = Http()

    print("[1/5] Discovering events...", file=sys.stderr)
    raw = discover_events(http, args.start, args.end, args.max_events)
    event_rows, market_rows = [], []
    for e in raw:
        n = normalize_event(e)
        if n:
            er, mr = n
            event_rows.append(er)
            market_rows.extend(mr)
    edf = pd.DataFrame(event_rows)
    mdf = pd.DataFrame(market_rows)
    edf.to_csv(outdir / "events.csv", index=False)
    mdf.to_csv(outdir / "markets.csv", index=False)
    if edf.empty or mdf.empty:
        raise SystemExit("No normalized events/markets found")

    print(f"[2/5] events={len(edf)} markets={len(mdf)}", file=sys.stderr)
    obs = []
    for i, ev in edf.reset_index(drop=True).iterrows():
        if not bool(ev.resolution_valid):
            continue
        em = mdf[mdf.event_slug == ev.event_slug]
        start_ts = int(ev.anchor_ts - (max(horizons)+3)*3600)
        end_ts = int(ev.anchor_ts)
        print(f"  [{i+1}/{len(edf)}] {ev.event_slug}", file=sys.stderr)
        for _, mk in em.iterrows():
            try:
                hist = load_history(http, str(mk.yes_token), start_ts, end_ts, args.fidelity, cache)
            except Exception as e:
                print(f"    history error {mk.market_slug}: {e}", file=sys.stderr)
                continue
            pts = points_from_history(hist)
            for h in horizons:
                target = int(ev.anchor_ts - h*3600)
                z = price_at_or_before(pts, target, args.max_staleness_min*60)
                if not z:
                    continue
                px, stale, point_ts = z
                obs.append({
                    "event_slug": ev.event_slug,
                    "event_id": ev.event_id,
                    "city": ev.city,
                    "kind": ev.kind,
                    "anchor_ts": int(ev.anchor_ts),
                    "bucket": mk.bucket,
                    "market_slug": mk.market_slug,
                    "yes_token": mk.yes_token,
                    "horizon_h": h,
                    "target_ts": target,
                    "target_iso": iso(target),
                    "price_point_ts": point_ts,
                    "price_point_iso": iso(point_ts),
                    "price": px,
                    "staleness_s": stale,
                    "result": int(mk.result),
                    "winner_bucket": ev.winner_bucket,
                    "anchor_source": ev.anchor_source,
                })

    odf = pd.DataFrame(obs)
    if odf.empty:
        raise SystemExit("No historical observations returned")
    odf["price_bin"] = pd.cut(odf.price, PRICE_BINS, labels=PRICE_LABELS, include_lowest=True, right=False)
    odf["event_day"] = pd.to_datetime(odf.anchor_ts, unit="s", utc=True).dt.date.astype(str)
    odf["cluster_id"] = odf.city.astype(str) + "|" + odf.event_day
    odf["brier"] = (odf.price - odf.result) ** 2
    odf.to_csv(outdir / "observations.csv", index=False)

    print(f"[3/5] observations={len(odf)} events={odf.event_slug.nunique()} city-days={odf.cluster_id.nunique()}", file=sys.stderr)
    cal = calibration(odf, [], args.bootstrap, 42)
    cal_city = calibration(odf, ["city"], max(250, args.bootstrap//2), 42)
    cal_kind = calibration(odf, ["kind"], max(250, args.bootstrap//2), 42)
    cal.to_csv(outdir / "calibration.csv", index=False)
    cal_city.to_csv(outdir / "calibration_by_city.csv", index=False)
    cal_kind.to_csv(outdir / "calibration_by_kind.csv", index=False)

    q = pd.DataFrame([
        ["events_total", len(edf)],
        ["events_valid_resolution", int(edf.resolution_valid.fillna(False).sum())],
        ["bucket_markets_total", len(mdf)],
        ["events_with_prices", int(odf.event_slug.nunique())],
        ["observations", len(odf)],
        ["cities", int(odf.city.nunique())],
        ["independent_city_days", int(odf.cluster_id.nunique())],
    ], columns=["metric", "value"])
    q.to_csv(outdir / "quality_summary.csv", index=False)

    print("[4/5] Building report...", file=sys.stderr)
    positive = cal[(cal.n_independent_city_days >= 20) & (cal.gap_ci_low_cluster_boot > 0)].copy()
    lines = [
        "# Full Phase 1 Calibration Report", "",
        f"- Events: **{len(edf):,}**",
        f"- Valid resolved events: **{int(edf.resolution_valid.fillna(False).sum()):,}**",
        f"- Bucket markets: **{len(mdf):,}**",
        f"- Historical observations: **{len(odf):,}**",
        f"- Independent city-days: **{odf.cluster_id.nunique():,}**", "",
        "## Warning", "",
        "This broad pass anchors horizons to Gamma `event.endDate`. It is a discovery pass, not final trading evidence. Candidate cities must later be rerun with exact station-local ex-ante anchors to remove outcome-knowability leakage.", "",
        "## Candidate positive cells (minimum 20 independent city-days)", "",
    ]
    if positive.empty:
        lines.append("None passed the confidence-interval gate.")
    else:
        lines.append(positive[["horizon_h","price_bin","n_independent_city_days","mean_price","win_rate","calibration_gap","gap_ci_low_cluster_boot","gap_ci_high_cluster_boot"]].to_markdown(index=False))
    (outdir / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")

    summary = {
        "events": int(len(edf)),
        "valid_events": int(edf.resolution_valid.fillna(False).sum()),
        "markets": int(len(mdf)),
        "observations": int(len(odf)),
        "independent_city_days": int(odf.cluster_id.nunique()),
        "candidate_positive_cells": positive.replace({np.nan: None}).to_dict(orient="records"),
        "warning": "Broad pass uses event.endDate anchor; exact station-local ex-ante anchors are required before trading conclusions.",
    }
    (outdir / "summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print("[5/5] DONE", file=sys.stderr)
    print(outdir.resolve())


if __name__ == "__main__":
    main()
