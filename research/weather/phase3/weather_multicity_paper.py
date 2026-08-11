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
NWS = "https://api.weather.gov"
PAPER_ONLY = True

MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}
DATE_SLUG_RE = re.compile(r"-on-(january|february|march|april|may|june|july|august|september|october|november|december)-(\d{1,2})-(\d{4})(?:$|[/?])", re.I)
RANGE_RE = re.compile(r"^\s*(-?\d+)\s*[-–]\s*(-?\d+)\s*°?F\s*$", re.I)
TAIL_RE = re.compile(r"^\s*(-?\d+)\s*°?F\s+or\s+(higher|below)\s*$", re.I)

CITY_CONFIGS = {
    "NYC": {"aliases": ["NYC", "New York City"], "station": "KLGA", "tz": "America/New_York"},
    "Chicago": {"aliases": ["Chicago"], "station": "KORD", "tz": "America/Chicago"},
    "Dallas": {"aliases": ["Dallas"], "station": "KDAL", "tz": "America/Chicago"},
    "Miami": {"aliases": ["Miami"], "station": "KMIA", "tz": "America/New_York"},
    "Houston": {"aliases": ["Houston"], "station": "KHOU", "tz": "America/Chicago"},
    "Seattle": {"aliases": ["Seattle"], "station": "KSEA", "tz": "America/Los_Angeles"},
    "San Francisco": {"aliases": ["San Francisco"], "station": "KSFO", "tz": "America/Los_Angeles"},
    "Los Angeles": {"aliases": ["Los Angeles"], "station": "KLAX", "tz": "America/Los_Angeles"},
    "Atlanta": {"aliases": ["Atlanta"], "station": "KATL", "tz": "America/New_York"},
    "Denver": {"aliases": ["Denver"], "station": "KDEN", "tz": "America/Denver"},
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None = None) -> str:
    return (dt or utc_now()).isoformat()


def parse_iso(s: str) -> datetime:
    d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def fnum(x: Any) -> float | None:
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except Exception:
        return None


def jsonish(x: Any, default):
    if isinstance(x, (list, dict)):
        return x
    if isinstance(x, str):
        try:
            return json.loads(x)
        except Exception:
            return default
    return default


def target_date_from_slug(slug: str) -> str | None:
    m = DATE_SLUG_RE.search(str(slug))
    if not m:
        return None
    return date(int(m.group(3)), MONTHS[m.group(1).lower()], int(m.group(2))).isoformat()


def parse_bucket_f(label: str):
    s = str(label or "").strip()
    m = RANGE_RE.match(s)
    if m:
        lo, hi = float(m.group(1)), float(m.group(2))
        return ("range", min(lo, hi), max(lo, hi))
    m = TAIL_RE.match(s)
    if m:
        v = float(m.group(1))
        return (m.group(2).lower(), v, v)
    return None


def bucket_contains_f(temp_f: float, label: str) -> bool:
    p = parse_bucket_f(label)
    if not p:
        return False
    mode, lo, hi = p
    if mode == "range":
        return lo <= temp_f <= hi
    if mode == "higher":
        return temp_f >= lo
    if mode == "below":
        return temp_f <= lo
    return False


def choose_bucket(temp_f: float, markets: list[dict]) -> dict | None:
    hits = [m for m in markets if bucket_contains_f(temp_f, m["bucket"])]
    return hits[0] if hits else None


class Http:
    def __init__(self):
        self.s = requests.Session()
        self.s.headers.update({
            "User-Agent": "polymarket-weather-multicity-paper/1.0 (github.com/23emrahgunes/polymarket)",
            "Accept": "application/json",
        })

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
CREATE TABLE IF NOT EXISTS source_cache(
 city TEXT PRIMARY KEY, station TEXT NOT NULL, lat REAL, lon REAL, forecast_hourly_url TEXT, checked_at TEXT
);
CREATE TABLE IF NOT EXISTS forecasts(
 id INTEGER PRIMARY KEY, detected_at TEXT NOT NULL, city TEXT NOT NULL, station TEXT NOT NULL,
 target_date TEXT NOT NULL, source_updated TEXT, max_f REAL NOT NULL, payload_hash TEXT NOT NULL,
 UNIQUE(city,target_date,payload_hash)
);
CREATE INDEX IF NOT EXISTS idx_fc_city_date ON forecasts(city,target_date,id);
CREATE TABLE IF NOT EXISTS revisions(
 id INTEGER PRIMARY KEY, detected_at TEXT NOT NULL, city TEXT NOT NULL, station TEXT NOT NULL,
 target_date TEXT NOT NULL, event_slug TEXT, source_updated TEXT,
 old_max_f REAL NOT NULL, new_max_f REAL NOT NULL, delta_f REAL NOT NULL,
 old_bucket TEXT, new_bucket TEXT, yes_token TEXT, decision TEXT NOT NULL, reason TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS market_snapshots(
 id INTEGER PRIMARY KEY, ts TEXT NOT NULL, city TEXT NOT NULL, station TEXT NOT NULL,
 target_date TEXT NOT NULL, event_slug TEXT NOT NULL, market_id TEXT, bucket TEXT NOT NULL, yes_token TEXT NOT NULL,
 best_bid REAL, best_ask REAL, bid_size REAL, ask_size REAL, mid REAL, spread REAL,
 forecast_max_f REAL, source_updated TEXT
);
CREATE INDEX IF NOT EXISTS idx_mc_snap ON market_snapshots(city,target_date,yes_token,ts);
CREATE TABLE IF NOT EXISTS paper_trades(
 id INTEGER PRIMARY KEY, opened_at TEXT NOT NULL, revision_id INTEGER NOT NULL,
 city TEXT NOT NULL, station TEXT NOT NULL, target_date TEXT NOT NULL, event_slug TEXT NOT NULL,
 market_id TEXT, bucket TEXT NOT NULL, yes_token TEXT NOT NULL,
 old_max_f REAL NOT NULL, new_max_f REAL NOT NULL, source_updated TEXT,
 entry_bid REAL, entry_ask REAL NOT NULL, entry_spread REAL,
 stake REAL NOT NULL, shares REAL NOT NULL,
 status TEXT NOT NULL DEFAULT 'OPEN', resolved_at TEXT, result INTEGER, payout REAL, pnl REAL,
 UNIQUE(revision_id,yes_token)
);
"""


def db_open(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def match_city_title(title: str, aliases: list[str]) -> bool:
    t = title.lower().strip()
    if not t.startswith("highest temperature in "):
        return False
    return any(f"highest temperature in {a.lower()} on " in t for a in aliases)


def discover_city_events(http: Http, city: str, cfg: dict) -> list[dict]:
    q = f"highest temperature in {cfg['aliases'][0]}"
    d = http.get_json(f"{GAMMA}/public-search", params={
        "q": q, "limit_per_type": 30, "search_profiles": "false", "keep_closed_markets": 0,
    })
    events = d.get("events") or [] if isinstance(d, dict) else []
    out = []
    for e in events:
        title = str(e.get("title") or "").strip()
        if not match_city_title(title, cfg["aliases"]):
            continue
        if e.get("closed") is True or e.get("active") is False:
            continue
        slug = str(e.get("slug") or "")
        target_date = target_date_from_slug(slug)
        if not target_date:
            continue
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
            if parse_bucket_f(bucket) is None:
                continue
            markets.append({
                "market_id": str(mk.get("id") or ""),
                "bucket": bucket,
                "yes_token": str(tokens[yi]),
            })
        if markets:
            out.append({
                "city": city, "station": cfg["station"], "tz": cfg["tz"],
                "event_slug": slug, "target_date": target_date, "markets": markets,
            })
    return sorted(out, key=lambda x: x["target_date"])


def station_forecast_url(con: sqlite3.Connection, http: Http, city: str, station: str) -> tuple[float,float,str]:
    row = con.execute("SELECT * FROM source_cache WHERE city=?", (city,)).fetchone()
    if row and row["forecast_hourly_url"]:
        return float(row["lat"]), float(row["lon"]), str(row["forecast_hourly_url"])
    sd = http.get_json(f"{NWS}/stations/{station}")
    coords = ((sd.get("geometry") or {}).get("coordinates") or []) if isinstance(sd, dict) else []
    if len(coords) < 2:
        raise RuntimeError(f"NWS station geometry missing for {station}")
    lon, lat = float(coords[0]), float(coords[1])
    pd = http.get_json(f"{NWS}/points/{lat:.4f},{lon:.4f}")
    props = pd.get("properties") or {}
    url = props.get("forecastHourly")
    if not url:
        raise RuntimeError(f"NWS forecastHourly missing for {station}")
    con.execute("INSERT OR REPLACE INTO source_cache(city,station,lat,lon,forecast_hourly_url,checked_at) VALUES(?,?,?,?,?,?)",
                (city,station,lat,lon,url,iso()))
    con.commit()
    return lat, lon, str(url)


def to_f(temp: float, unit: str) -> float:
    return temp * 9.0 / 5.0 + 32.0 if str(unit).upper().startswith("C") else temp


def fetch_nws_target_max(con: sqlite3.Connection, http: Http, city: str, cfg: dict, target_date: str) -> tuple[float,str|None,str]:
    _, _, url = station_forecast_url(con,http,city,cfg["station"])
    d = http.get_json(url)
    props = d.get("properties") or {}
    tz = ZoneInfo(cfg["tz"])
    vals = []
    payload = []
    for p in props.get("periods") or []:
        st = p.get("startTime")
        tv = fnum(p.get("temperature"))
        unit = p.get("temperatureUnit") or "F"
        if not st or tv is None:
            continue
        dt = parse_iso(st).astimezone(tz)
        if dt.date().isoformat() != target_date:
            continue
        tf = to_f(tv,unit)
        vals.append(tf)
        payload.append((dt.isoformat(), round(tf,3)))
    if not vals:
        raise RuntimeError(f"no NWS hourly periods for {city} {target_date}")
    updated = props.get("updated") or props.get("generatedAt") or props.get("updateTime")
    h = hashlib.sha256(json.dumps({"updated":updated,"hours":payload}, sort_keys=True).encode()).hexdigest()
    return max(vals), updated, h


def fetch_books(http: Http, markets: list[dict]) -> dict[str,dict]:
    if not markets:
        return {}
    rows = http.post_json(f"{CLOB}/books", [{"token_id":m["yes_token"]} for m in markets])
    result = {}
    if not isinstance(rows,list):
        return result
    for b in rows:
        token = str(b.get("asset_id") or "")
        bids = [(fnum(x.get("price")),fnum(x.get("size"))) for x in (b.get("bids") or [])]
        asks = [(fnum(x.get("price")),fnum(x.get("size"))) for x in (b.get("asks") or [])]
        bids = [(p,s) for p,s in bids if p is not None]
        asks = [(p,s) for p,s in asks if p is not None]
        bid,bid_size = max(bids,key=lambda x:x[0]) if bids else (None,None)
        ask,ask_size = min(asks,key=lambda x:x[0]) if asks else (None,None)
        mid = (bid+ask)/2 if bid is not None and ask is not None else None
        spread = ask-bid if bid is not None and ask is not None else None
        result[token] = {"bid":bid,"ask":ask,"bid_size":bid_size,"ask_size":ask_size,"mid":mid,"spread":spread}
    return result


def latest_forecast(con: sqlite3.Connection, city: str, target_date: str):
    return con.execute("SELECT * FROM forecasts WHERE city=? AND target_date=? ORDER BY id DESC LIMIT 1", (city,target_date)).fetchone()


def record_forecast(con: sqlite3.Connection, city: str, station: str, target_date: str, updated: str|None, max_f: float, payload_hash: str):
    prev = latest_forecast(con,city,target_date)
    if prev and prev["payload_hash"] == payload_hash:
        return prev, False
    con.execute("INSERT OR IGNORE INTO forecasts(detected_at,city,station,target_date,source_updated,max_f,payload_hash) VALUES(?,?,?,?,?,?,?)",
                (iso(),city,station,target_date,updated,max_f,payload_hash))
    con.commit()
    return prev, True


def snapshot_event(con: sqlite3.Connection, event: dict, books: dict, max_f: float, updated: str|None):
    now = iso()
    for m in event["markets"]:
        b = books.get(m["yes_token"],{})
        con.execute("""INSERT INTO market_snapshots(ts,city,station,target_date,event_slug,market_id,bucket,yes_token,best_bid,best_ask,bid_size,ask_size,mid,spread,forecast_max_f,source_updated)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (now,event["city"],event["station"],event["target_date"],event["event_slug"],m["market_id"],m["bucket"],m["yes_token"],
                     b.get("bid"),b.get("ask"),b.get("bid_size"),b.get("ask_size"),b.get("mid"),b.get("spread"),max_f,updated))
    con.commit()


def entry_time_ok(event: dict, max_days_ahead: int, max_entry_local_hour: int) -> tuple[bool,str]:
    tz = ZoneInfo(event["tz"])
    now = datetime.now(tz)
    td = date.fromisoformat(event["target_date"])
    da = (td-now.date()).days
    if da < 0 or da > max_days_ahead:
        return False,f"days_ahead={da}_outside_0..{max_days_ahead}"
    if da == 0 and now.hour >= max_entry_local_hour:
        return False,f"after_local_{max_entry_local_hour:02d}00_cutoff"
    return True,"ok"


def maybe_open(con: sqlite3.Connection, event: dict, prev, max_f: float, updated: str|None, books: dict,
               min_revision_f: float, max_ask: float, max_spread: float, stake: float, max_days_ahead: int, max_entry_local_hour: int):
    if not prev:
        return
    old = float(prev["max_f"]); new = float(max_f); delta = new-old
    old_bucket = choose_bucket(old,event["markets"])
    new_bucket = choose_bucket(new,event["markets"])
    decision,reason = "SKIP",""
    token = new_bucket["yes_token"] if new_bucket else None
    time_ok,time_reason = entry_time_ok(event,max_days_ahead,max_entry_local_hour)
    if abs(delta) < min_revision_f:
        reason = f"abs_delta<{min_revision_f:.1f}F"
    elif not new_bucket:
        reason = "no_bucket_for_new_forecast"
    elif old_bucket and old_bucket["bucket"] == new_bucket["bucket"]:
        reason = "forecast_changed_but_bucket_unchanged"
    elif not time_ok:
        reason = time_reason
    else:
        b = books.get(token,{})
        ask,bid,spread,ask_size = b.get("ask"),b.get("bid"),b.get("spread"),b.get("ask_size")
        if ask is None or ask <= 0:
            reason = "no_best_ask"
        elif ask > max_ask:
            reason = f"ask>{max_ask:.2f}"
        elif spread is None or spread > max_spread:
            reason = f"spread>{max_spread:.2f}_or_missing"
        elif ask_size is not None and ask_size < stake/ask:
            reason = "best_ask_depth_too_small"
        else:
            decision,reason = "PAPER_BUY","revision_moved_target_bucket"
    cur = con.execute("""INSERT INTO revisions(detected_at,city,station,target_date,event_slug,source_updated,old_max_f,new_max_f,delta_f,old_bucket,new_bucket,yes_token,decision,reason)
                         VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                      (iso(),event["city"],event["station"],event["target_date"],event["event_slug"],updated,old,new,delta,
                       old_bucket["bucket"] if old_bucket else None,new_bucket["bucket"] if new_bucket else None,token,decision,reason))
    rid = cur.lastrowid
    if decision == "PAPER_BUY":
        b=books[token]; ask=float(b["ask"]); shares=stake/ask
        con.execute("""INSERT OR IGNORE INTO paper_trades(opened_at,revision_id,city,station,target_date,event_slug,market_id,bucket,yes_token,old_max_f,new_max_f,source_updated,entry_bid,entry_ask,entry_spread,stake,shares,status)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'OPEN')""",
                    (iso(),rid,event["city"],event["station"],event["target_date"],event["event_slug"],new_bucket["market_id"],new_bucket["bucket"],token,
                     old,new,updated,b.get("bid"),ask,b.get("spread"),stake,shares))
    con.commit()
    print(f"REVISION {event['city']} {event['target_date']} {old:.0f}->{new:.0f}F delta={delta:+.0f} old_bucket={old_bucket['bucket'] if old_bucket else '-'} new_bucket={new_bucket['bucket'] if new_bucket else '-'} decision={decision} reason={reason}")


def settle_open(con: sqlite3.Connection, http: Http):
    for t in con.execute("SELECT * FROM paper_trades WHERE status='OPEN' ORDER BY id").fetchall():
        try:
            e = http.get_json(f"{GAMMA}/events/slug/{t['event_slug']}")
            if not isinstance(e,dict) or e.get("closed") is not True:
                continue
            final=None
            for mk in e.get("markets") or []:
                if str(mk.get("id") or "") != str(t["market_id"]):
                    continue
                outcomes=jsonish(mk.get("outcomes"),[]); prices=jsonish(mk.get("outcomePrices"),[])
                yi=0
                for j,o in enumerate(outcomes):
                    if str(o).strip().lower()=="yes": yi=j; break
                if yi < len(prices): final=fnum(prices[yi])
                break
            if final is None or not (final>=.99 or final<=.01):
                continue
            result=1 if final>=.99 else 0
            payout=float(t["shares"])*result; pnl=payout-float(t["stake"])
            con.execute("UPDATE paper_trades SET status='SETTLED',resolved_at=?,result=?,payout=?,pnl=? WHERE id=?", (iso(),result,payout,pnl,t["id"]))
            con.commit()
            print(f"SETTLED {t['city']} id={t['id']} {t['target_date']} {t['bucket']} result={result} pnl={pnl:+.3f}")
        except Exception as e:
            print(f"settle warning id={t['id']}: {e}", file=sys.stderr)


def cycle(con: sqlite3.Connection, http: Http, args):
    if not PAPER_ONLY:
        raise RuntimeError("PAPER_ONLY invariant violated")
    active_total=0
    for city,cfg in CITY_CONFIGS.items():
        try:
            events=discover_city_events(http,city,cfg)
            active_total += len(events)
            if not events:
                print(f"CITY {city}: no active markets")
                continue
            for event in events:
                try:
                    max_f,updated,h = fetch_nws_target_max(con,http,city,cfg,event["target_date"])
                except Exception as e:
                    print(f"  {city} {event['target_date']} forecast skip: {e}")
                    continue
                books=fetch_books(http,event["markets"])
                snapshot_event(con,event,books,max_f,updated)
                prev,changed=record_forecast(con,city,cfg["station"],event["target_date"],updated,max_f,h)
                if changed:
                    if prev:
                        print(f"  NWS changed {city} {event['target_date']}: {float(prev['max_f']):.0f}->{max_f:.0f}F updated={updated}")
                    else:
                        print(f"  NWS baseline {city} {event['target_date']}: {max_f:.0f}F updated={updated}")
                    maybe_open(con,event,prev,max_f,updated,books,args.min_revision_f,args.max_ask,args.max_spread,args.stake,args.max_days_ahead,args.max_entry_local_hour)
        except Exception as e:
            print(f"CITY ERROR {city}: {e}", file=sys.stderr)
    settle_open(con,http)
    print(f"[{iso()}] cycle_complete active_events={active_total} cities={len(CITY_CONFIGS)}")


def report(con: sqlite3.Connection):
    print("===== MULTI-CITY WEATHER PAPER REPORT =====")
    r=con.execute("SELECT COUNT(*) n,SUM(CASE WHEN status='OPEN' THEN 1 ELSE 0 END) open_n,SUM(CASE WHEN status='SETTLED' THEN 1 ELSE 0 END) settled_n FROM paper_trades").fetchone()
    print(f"trades={r['n'] or 0} open={r['open_n'] or 0} settled={r['settled_n'] or 0}")
    s=con.execute("SELECT COUNT(*) n,COALESCE(SUM(stake),0) stake,COALESCE(SUM(payout),0) payout,COALESCE(SUM(pnl),0) pnl,COALESCE(SUM(result),0) wins FROM paper_trades WHERE status='SETTLED'").fetchone()
    roi=s['pnl']/s['stake']*100 if s['stake'] else float('nan')
    print(f"settled={s['n']} wins={s['wins']} stake={s['stake']:.3f} payout={s['payout']:.3f} pnl={s['pnl']:+.3f} ROI={roi:.2f}%")
    print("\nBy city:")
    rows=con.execute("""SELECT city,COUNT(*) trades,SUM(CASE WHEN status='SETTLED' THEN 1 ELSE 0 END) settled,
                        COALESCE(SUM(CASE WHEN status='SETTLED' THEN pnl ELSE 0 END),0) pnl
                        FROM paper_trades GROUP BY city ORDER BY city""").fetchall()
    for x in rows: print(dict(x))
    rev=con.execute("SELECT COUNT(*) n,SUM(CASE WHEN decision='PAPER_BUY' THEN 1 ELSE 0 END) buys FROM revisions").fetchone()
    print(f"\nrevisions={rev['n'] or 0} paper_buy_revisions={rev['buys'] or 0}")
    print("Latest trades:")
    for x in con.execute("SELECT id,opened_at,city,target_date,bucket,old_max_f,new_max_f,entry_ask,status,result,pnl FROM paper_trades ORDER BY id DESC LIMIT 30").fetchall():
        print(dict(x))


def main() -> int:
    ap=argparse.ArgumentParser(description="PAPER-ONLY multi-city NWS forecast-revision stale-quote experiment")
    ap.add_argument("--db",default="phase3_out/weather_multicity_paper.sqlite")
    ap.add_argument("--interval",type=int,default=90)
    ap.add_argument("--stake",type=float,default=1.0)
    ap.add_argument("--min-revision-f",type=float,default=1.0)
    ap.add_argument("--max-ask",type=float,default=.50)
    ap.add_argument("--max-spread",type=float,default=.12)
    ap.add_argument("--max-days-ahead",type=int,default=2)
    ap.add_argument("--max-entry-local-hour",type=int,default=12)
    ap.add_argument("--once",action="store_true")
    ap.add_argument("--report",action="store_true")
    args=ap.parse_args()
    con=db_open(Path(args.db))
    if args.report:
        report(con); return 0
    print("===== MULTI-CITY WEATHER FORWARD PAPER =====")
    print("PAPER_ONLY=true; no private key, no trading credentials, no order-placement code.")
    print(f"cities={','.join(CITY_CONFIGS)} interval={args.interval}s stake=${args.stake:.2f} min_revision={args.min_revision_f:.1f}F require_bucket_change=true max_ask={args.max_ask:.2f} max_spread={args.max_spread:.2f} max_entry_local_hour={args.max_entry_local_hour}")
    http=Http()
    while True:
        try:
            cycle(con,http,args)
            con.execute("INSERT INTO runs(ts,status,note) VALUES(?,?,?)",(iso(),"OK",None)); con.commit()
        except KeyboardInterrupt:
            return 0
        except Exception as e:
            print(f"CYCLE ERROR: {e}",file=sys.stderr)
            con.execute("INSERT INTO runs(ts,status,note) VALUES(?,?,?)",(iso(),"ERROR",str(e))); con.commit()
        if args.once: return 0
        time.sleep(max(30,args.interval))

if __name__=="__main__":
    raise SystemExit(main())
