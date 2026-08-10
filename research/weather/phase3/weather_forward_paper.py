#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sqlite3
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
HKO_FND = "https://data.weather.gov.hk/weatherAPI/opendata/weather.php"
GEOBLOCK = "https://polymarket.com/api/geoblock"
HKT = ZoneInfo("Asia/Hong_Kong")
PAPER_ONLY = True
TITLE_PREFIX = "highest temperature in hong kong on "
BUCKET_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*°?C(?:\s+or\s+(higher|below))?\s*$", re.I)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None = None) -> str:
    return (dt or utc_now()).isoformat()


def parse_iso(s: str) -> datetime:
    d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def jsonish(x: Any, default):
    if isinstance(x, (list, dict)):
        return x
    if isinstance(x, str):
        try:
            return json.loads(x)
        except Exception:
            return default
    return default


def fnum(x: Any) -> float | None:
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except Exception:
        return None


def parse_bucket(label: str):
    m = BUCKET_RE.match(str(label).strip())
    if not m:
        return None, None
    return float(m.group(1)), (m.group(2) or "exact").lower()


def bucket_contains(temp_c: float, label: str) -> bool:
    val, mode = parse_bucket(label)
    if val is None:
        return False
    if mode == "higher":
        return temp_c >= val
    if mode == "below":
        return temp_c < val + 1.0
    return val <= temp_c < val + 1.0


def choose_bucket(temp_c: float, markets: list[dict]) -> dict | None:
    hits = [m for m in markets if bucket_contains(temp_c, m["bucket"])]
    if not hits:
        return None
    # Exact bucket is preferable when an event representation ever contains overlap.
    hits.sort(key=lambda m: 0 if parse_bucket(m["bucket"])[1] == "exact" else 1)
    return hits[0]


class Http:
    def __init__(self):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": "polymarket-weather-forward-paper/1.0", "Accept": "application/json"})

    def get_json(self, url: str, params=None, retries: int = 5):
        last = None
        for i in range(retries):
            try:
                r = self.s.get(url, params=params, timeout=30)
                if r.status_code == 429:
                    time.sleep(min(2 + i, 10)); continue
                if r.status_code >= 500:
                    time.sleep(min(2 ** i, 10)); continue
                r.raise_for_status()
                return r.json()
            except Exception as e:
                last = e
                time.sleep(min(2 ** i, 10))
        raise RuntimeError(f"GET failed {url}: {last}")

    def post_json(self, url: str, payload, retries: int = 5):
        last = None
        for i in range(retries):
            try:
                r = self.s.post(url, json=payload, timeout=30)
                if r.status_code == 429:
                    time.sleep(min(2 + i, 10)); continue
                if r.status_code >= 500:
                    time.sleep(min(2 ** i, 10)); continue
                r.raise_for_status()
                return r.json()
            except Exception as e:
                last = e
                time.sleep(min(2 ** i, 10))
        raise RuntimeError(f"POST failed {url}: {last}")


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
CREATE TABLE IF NOT EXISTS runs(
  id INTEGER PRIMARY KEY, ts TEXT NOT NULL, status TEXT NOT NULL, note TEXT
);
CREATE TABLE IF NOT EXISTS hko_forecasts(
  id INTEGER PRIMARY KEY, detected_at TEXT NOT NULL, update_time TEXT, target_date TEXT NOT NULL,
  max_c REAL NOT NULL, min_c REAL, forecast_text TEXT, payload_hash TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_hko_date ON hko_forecasts(target_date, id);
CREATE TABLE IF NOT EXISTS revisions(
  id INTEGER PRIMARY KEY, detected_at TEXT NOT NULL, update_time TEXT, target_date TEXT NOT NULL,
  old_max_c REAL NOT NULL, new_max_c REAL NOT NULL, delta_c REAL NOT NULL,
  event_slug TEXT, target_bucket TEXT, yes_token TEXT, decision TEXT, reason TEXT
);
CREATE TABLE IF NOT EXISTS market_snapshots(
  id INTEGER PRIMARY KEY, ts TEXT NOT NULL, target_date TEXT NOT NULL, event_slug TEXT NOT NULL,
  market_id TEXT, bucket TEXT NOT NULL, yes_token TEXT NOT NULL,
  best_bid REAL, best_ask REAL, bid_size REAL, ask_size REAL, mid REAL, spread REAL,
  hko_max_c REAL, hko_update_time TEXT
);
CREATE INDEX IF NOT EXISTS idx_snap_token_ts ON market_snapshots(yes_token, ts);
CREATE TABLE IF NOT EXISTS paper_trades(
  id INTEGER PRIMARY KEY, opened_at TEXT NOT NULL, revision_id INTEGER NOT NULL,
  target_date TEXT NOT NULL, event_slug TEXT NOT NULL, market_id TEXT, bucket TEXT NOT NULL,
  yes_token TEXT NOT NULL, hko_old_max_c REAL NOT NULL, hko_new_max_c REAL NOT NULL,
  entry_bid REAL, entry_ask REAL NOT NULL, entry_spread REAL, stake REAL NOT NULL, shares REAL NOT NULL,
  status TEXT NOT NULL DEFAULT 'OPEN', resolved_at TEXT, result INTEGER, payout REAL, pnl REAL,
  UNIQUE(revision_id, yes_token)
);
"""


def db_open(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def discover_hk_events(http: Http) -> list[dict]:
    data = http.get_json(f"{GAMMA}/public-search", params={
        "q": "highest temperature in Hong Kong",
        "limit_per_type": 30,
        "search_profiles": "false",
        "keep_closed_markets": 0,
    })
    events = data.get("events") or [] if isinstance(data, dict) else []
    out = []
    for e in events:
        title = str(e.get("title") or "").strip()
        if not title.lower().startswith(TITLE_PREFIX):
            continue
        if e.get("closed") is True or e.get("active") is False:
            continue
        if not e.get("endDate"):
            continue
        end_utc = parse_iso(e["endDate"])
        target_date = end_utc.astimezone(HKT).date().isoformat()
        markets = []
        for mk in e.get("markets") or []:
            if mk.get("closed") is True or mk.get("active") is False:
                continue
            outcomes = jsonish(mk.get("outcomes"), [])
            tokens = jsonish(mk.get("clobTokenIds"), [])
            if not tokens:
                continue
            yi = 0
            for j, o in enumerate(outcomes):
                if str(o).strip().lower() == "yes":
                    yi = j; break
            if yi >= len(tokens):
                continue
            bucket = str(mk.get("groupItemTitle") or mk.get("question") or "").strip()
            if parse_bucket(bucket)[0] is None:
                continue
            markets.append({
                "market_id": str(mk.get("id") or ""),
                "market_slug": str(mk.get("slug") or ""),
                "bucket": bucket,
                "yes_token": str(tokens[yi]),
            })
        if markets:
            out.append({
                "event_id": str(e.get("id") or ""),
                "event_slug": str(e.get("slug") or ""),
                "title": title,
                "end_utc": end_utc,
                "target_date": target_date,
                "markets": markets,
            })
    return sorted(out, key=lambda x: x["target_date"])


def fetch_hko(http: Http) -> tuple[str | None, dict[str, dict]]:
    d = http.get_json(HKO_FND, params={"dataType": "fnd", "lang": "en"})
    update = d.get("updateTime") if isinstance(d, dict) else None
    out = {}
    for r in (d.get("weatherForecast") or []):
        ds = str(r.get("forecastDate") or "")
        if len(ds) != 8:
            continue
        target = f"{ds[:4]}-{ds[4:6]}-{ds[6:8]}"
        mx = fnum((r.get("forecastMaxtemp") or {}).get("value"))
        mn = fnum((r.get("forecastMintemp") or {}).get("value"))
        if mx is None:
            continue
        out[target] = {"max_c": mx, "min_c": mn, "text": r.get("forecastWeather")}
    return update, out


def fetch_books(http: Http, markets: list[dict]) -> dict[str, dict]:
    if not markets:
        return {}
    payload = [{"token_id": m["yes_token"]} for m in markets]
    rows = http.post_json(f"{CLOB}/books", payload)
    result = {}
    if not isinstance(rows, list):
        return result
    for b in rows:
        token = str(b.get("asset_id") or "")
        bids = b.get("bids") or []
        asks = b.get("asks") or []
        bpairs = [(fnum(x.get("price")), fnum(x.get("size"))) for x in bids]
        apairs = [(fnum(x.get("price")), fnum(x.get("size"))) for x in asks]
        bpairs = [(p,s) for p,s in bpairs if p is not None]
        apairs = [(p,s) for p,s in apairs if p is not None]
        best_bid, bid_size = (max(bpairs, key=lambda x:x[0]) if bpairs else (None,None))
        best_ask, ask_size = (min(apairs, key=lambda x:x[0]) if apairs else (None,None))
        mid = ((best_bid + best_ask) / 2.0) if best_bid is not None and best_ask is not None else None
        spread = (best_ask - best_bid) if best_bid is not None and best_ask is not None else None
        result[token] = {"bid": best_bid, "ask": best_ask, "bid_size": bid_size, "ask_size": ask_size, "mid": mid, "spread": spread}
    return result


def latest_forecast(con: sqlite3.Connection, target_date: str) -> sqlite3.Row | None:
    return con.execute("SELECT * FROM hko_forecasts WHERE target_date=? ORDER BY id DESC LIMIT 1", (target_date,)).fetchone()


def record_forecast(con: sqlite3.Connection, target_date: str, update: str | None, f: dict) -> tuple[sqlite3.Row | None, bool]:
    prev = latest_forecast(con, target_date)
    core = {"target_date": target_date, "max_c": f["max_c"], "min_c": f.get("min_c"), "text": f.get("text"), "update": update}
    h = hashlib.sha256(json.dumps(core, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    if prev and prev["payload_hash"] == h:
        return prev, False
    con.execute("INSERT INTO hko_forecasts(detected_at,update_time,target_date,max_c,min_c,forecast_text,payload_hash) VALUES(?,?,?,?,?,?,?)",
                (iso(), update, target_date, f["max_c"], f.get("min_c"), f.get("text"), h))
    con.commit()
    return prev, True


def snapshot_event(con: sqlite3.Connection, event: dict, books: dict[str, dict], hko: dict, update: str | None):
    now = iso()
    for m in event["markets"]:
        b = books.get(m["yes_token"], {})
        con.execute("""INSERT INTO market_snapshots(ts,target_date,event_slug,market_id,bucket,yes_token,best_bid,best_ask,bid_size,ask_size,mid,spread,hko_max_c,hko_update_time)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (now,event["target_date"],event["event_slug"],m["market_id"],m["bucket"],m["yes_token"],b.get("bid"),b.get("ask"),b.get("bid_size"),b.get("ask_size"),b.get("mid"),b.get("spread"),hko["max_c"],update))
    con.commit()


def days_ahead(target_date: str) -> int:
    return (date.fromisoformat(target_date) - datetime.now(HKT).date()).days


def maybe_open_paper(con: sqlite3.Connection, event: dict, prev: sqlite3.Row | None, cur: dict, update: str | None,
                     books: dict[str, dict], min_revision: float, max_ask: float, max_spread: float, stake: float, max_days_ahead: int):
    if not prev:
        return
    old = float(prev["max_c"])
    new = float(cur["max_c"])
    delta = new - old
    if abs(delta) < min_revision:
        return

    decision, reason = "SKIP", ""
    bucket = choose_bucket(new, event["markets"])
    token = bucket["yes_token"] if bucket else None
    cutoff = event["end_utc"]  # historical research established event.endDate as the 08:00 HKT decision cutoff for this series.
    da = days_ahead(event["target_date"])

    if utc_now() >= cutoff:
        reason = "after_08HKT_cutoff"
    elif da < 0 or da > max_days_ahead:
        reason = f"days_ahead={da}_outside_0..{max_days_ahead}"
    elif not bucket:
        reason = "no_bucket_for_new_forecast"
    else:
        b = books.get(token, {})
        ask, bid, spread, ask_size = b.get("ask"), b.get("bid"), b.get("spread"), b.get("ask_size")
        if ask is None or ask <= 0:
            reason = "no_best_ask"
        elif ask > max_ask:
            reason = f"ask>{max_ask:.2f}"
        elif spread is None or spread > max_spread:
            reason = f"spread>{max_spread:.2f}_or_missing"
        elif ask_size is not None and ask_size < stake / ask:
            reason = "best_ask_depth_too_small"
        else:
            decision, reason = "PAPER_BUY", "revision_rule_pass"

    curx = con.execute("""INSERT INTO revisions(detected_at,update_time,target_date,old_max_c,new_max_c,delta_c,event_slug,target_bucket,yes_token,decision,reason)
                          VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                       (iso(),update,event["target_date"],old,new,delta,event["event_slug"],bucket["bucket"] if bucket else None,token,decision,reason))
    rid = curx.lastrowid
    if decision == "PAPER_BUY":
        b = books[token]
        ask = float(b["ask"])
        shares = stake / ask
        con.execute("""INSERT OR IGNORE INTO paper_trades(opened_at,revision_id,target_date,event_slug,market_id,bucket,yes_token,hko_old_max_c,hko_new_max_c,entry_bid,entry_ask,entry_spread,stake,shares,status)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'OPEN')""",
                    (iso(),rid,event["target_date"],event["event_slug"],bucket["market_id"],bucket["bucket"],token,old,new,b.get("bid"),ask,b.get("spread"),stake,shares))
    con.commit()
    print(f"REVISION {event['target_date']} HKO {old:.1f}->{new:.1f}C delta={delta:+.1f} bucket={bucket['bucket'] if bucket else '-'} decision={decision} reason={reason}")


def settle_open_trades(con: sqlite3.Connection, http: Http):
    rows = con.execute("SELECT * FROM paper_trades WHERE status='OPEN' ORDER BY id").fetchall()
    for t in rows:
        try:
            e = http.get_json(f"{GAMMA}/events/slug/{t['event_slug']}")
            if not isinstance(e, dict):
                continue
            if e.get("closed") is not True:
                continue
            final = None
            for mk in e.get("markets") or []:
                if str(mk.get("id") or "") != str(t["market_id"]):
                    continue
                outcomes = jsonish(mk.get("outcomes"), [])
                prices = jsonish(mk.get("outcomePrices"), [])
                yi = 0
                for j,o in enumerate(outcomes):
                    if str(o).lower().strip() == "yes": yi=j; break
                if yi < len(prices):
                    final = fnum(prices[yi])
                break
            if final is None or not (final >= .99 or final <= .01):
                continue
            result = 1 if final >= .99 else 0
            payout = float(t["shares"]) * result
            pnl = payout - float(t["stake"])
            con.execute("UPDATE paper_trades SET status='SETTLED',resolved_at=?,result=?,payout=?,pnl=? WHERE id=?",
                        (iso(),result,payout,pnl,t["id"]))
            con.commit()
            print(f"SETTLED id={t['id']} {t['target_date']} {t['bucket']} result={result} pnl={pnl:+.3f}")
        except Exception as e:
            print(f"settle warning trade={t['id']}: {e}", file=sys.stderr)


def geoblock_probe(http: Http) -> dict:
    try:
        d = http.get_json(GEOBLOCK)
        return d if isinstance(d, dict) else {"error": "unexpected_response"}
    except Exception as e:
        return {"error": str(e)}


def cycle(con: sqlite3.Connection, http: Http, args):
    if not PAPER_ONLY:
        raise RuntimeError("PAPER_ONLY invariant violated")
    update, forecasts = fetch_hko(http)
    events = discover_hk_events(http)
    print(f"[{iso()}] active_hk_events={len(events)} hko_dates={len(forecasts)} hko_update={update}")

    for event in events:
        f = forecasts.get(event["target_date"])
        if not f:
            print(f"  no HKO forecast for {event['target_date']} {event['event_slug']}")
            continue
        books = fetch_books(http, event["markets"])
        snapshot_event(con, event, books, f, update)
        prev, changed = record_forecast(con, event["target_date"], update, f)
        if changed:
            if prev:
                print(f"  HKO changed {event['target_date']}: {float(prev['max_c']):.1f} -> {f['max_c']:.1f}C")
            else:
                print(f"  HKO baseline {event['target_date']}: {f['max_c']:.1f}C")
            maybe_open_paper(con,event,prev,f,update,books,args.min_revision,args.max_ask,args.max_spread,args.stake,args.max_days_ahead)
    settle_open_trades(con, http)


def report(con: sqlite3.Connection):
    print("===== PHASE 3 FORWARD PAPER REPORT =====")
    totals = con.execute("SELECT COUNT(*) n, SUM(CASE WHEN status='OPEN' THEN 1 ELSE 0 END) open_n, SUM(CASE WHEN status='SETTLED' THEN 1 ELSE 0 END) settled_n FROM paper_trades").fetchone()
    print(f"trades={totals['n'] or 0} open={totals['open_n'] or 0} settled={totals['settled_n'] or 0}")
    r = con.execute("SELECT COUNT(*) n, COALESCE(SUM(stake),0) stake, COALESCE(SUM(payout),0) payout, COALESCE(SUM(pnl),0) pnl, COALESCE(SUM(result),0) wins FROM paper_trades WHERE status='SETTLED'").fetchone()
    roi = (r['pnl']/r['stake']*100) if r['stake'] else float('nan')
    print(f"settled={r['n']} wins={r['wins']} stake={r['stake']:.3f} payout={r['payout']:.3f} pnl={r['pnl']:+.3f} ROI={roi:.2f}%")
    rev = con.execute("SELECT COUNT(*) n, SUM(CASE WHEN decision='PAPER_BUY' THEN 1 ELSE 0 END) buys FROM revisions").fetchone()
    print(f"forecast_revisions={rev['n'] or 0} paper_buy_revisions={rev['buys'] or 0}")
    print("\nLatest trades:")
    rows = con.execute("SELECT id,opened_at,target_date,bucket,hko_old_max_c,hko_new_max_c,entry_ask,status,result,pnl FROM paper_trades ORDER BY id DESC LIMIT 20").fetchall()
    for x in rows:
        print(dict(x))


def main() -> int:
    ap = argparse.ArgumentParser(description="PAPER-ONLY forward HK weather revision/stale-quote experiment")
    ap.add_argument("--db", default="phase3_out/weather_forward_paper.sqlite")
    ap.add_argument("--interval", type=int, default=60)
    ap.add_argument("--stake", type=float, default=1.0)
    ap.add_argument("--min-revision", type=float, default=1.0)
    ap.add_argument("--max-ask", type=float, default=0.50)
    ap.add_argument("--max-spread", type=float, default=0.12)
    ap.add_argument("--max-days-ahead", type=int, default=2)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()
    if args.stake <= 0:
        raise SystemExit("stake must be >0")
    con = db_open(Path(args.db))
    if args.report:
        report(con); return 0
    http = Http()
    geo = geoblock_probe(http)
    print("===== PHASE 3 WEATHER FORWARD PAPER =====")
    print("PAPER_ONLY=true; no private key, no API credentials, no order placement endpoints.")
    print(f"geoblock_probe={json.dumps(geo, ensure_ascii=False)} (logged for compliance context only; no orders are sent)")
    print(f"db={args.db} interval={args.interval}s stake=${args.stake:.2f} min_revision={args.min_revision:.1f}C max_ask={args.max_ask:.2f} max_spread={args.max_spread:.2f} max_days_ahead={args.max_days_ahead}")
    while True:
        try:
            cycle(con,http,args)
            con.execute("INSERT INTO runs(ts,status,note) VALUES(?,?,?)", (iso(),"OK",None)); con.commit()
        except KeyboardInterrupt:
            print("stopped"); return 0
        except Exception as e:
            print(f"CYCLE ERROR: {e}", file=sys.stderr)
            con.execute("INSERT INTO runs(ts,status,note) VALUES(?,?,?)", (iso(),"ERROR",str(e))); con.commit()
        if args.once:
            return 0
        time.sleep(max(20,args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
