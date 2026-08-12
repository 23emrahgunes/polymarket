#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

from weather_multicity_paper import (
    CITY_CONFIGS,
    GAMMA,
    Http,
    choose_bucket,
    discover_city_events,
    entry_time_ok,
    fetch_books,
    fetch_nws_target_max,
    fnum,
    iso,
    jsonish,
)

PAPER_ONLY = True

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
CREATE TABLE IF NOT EXISTS runs(
  id INTEGER PRIMARY KEY, ts TEXT NOT NULL, status TEXT NOT NULL, note TEXT
);
CREATE TABLE IF NOT EXISTS predictions(
  id INTEGER PRIMARY KEY,
  detected_at TEXT NOT NULL,
  prediction_key TEXT NOT NULL UNIQUE,
  city TEXT NOT NULL,
  station TEXT NOT NULL,
  target_date TEXT NOT NULL,
  event_slug TEXT NOT NULL,
  source_updated TEXT,
  forecast_max_f REAL NOT NULL,
  bucket TEXT,
  market_id TEXT,
  yes_token TEXT,
  best_bid REAL,
  best_ask REAL,
  spread REAL,
  ask_size REAL,
  decision TEXT NOT NULL,
  reason TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pred_city_date ON predictions(city,target_date,id);
CREATE TABLE IF NOT EXISTS paper_trades(
  id INTEGER PRIMARY KEY,
  opened_at TEXT NOT NULL,
  prediction_id INTEGER NOT NULL,
  city TEXT NOT NULL,
  station TEXT NOT NULL,
  target_date TEXT NOT NULL,
  event_slug TEXT NOT NULL,
  market_id TEXT NOT NULL,
  bucket TEXT NOT NULL,
  yes_token TEXT NOT NULL,
  forecast_max_f REAL NOT NULL,
  source_updated TEXT,
  entry_bid REAL,
  entry_ask REAL NOT NULL,
  entry_spread REAL,
  stake REAL NOT NULL,
  shares REAL NOT NULL,
  status TEXT NOT NULL DEFAULT 'OPEN',
  resolved_at TEXT,
  result INTEGER,
  payout REAL,
  pnl REAL,
  UNIQUE(city,target_date,bucket)
);
"""


def db_open(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def prediction_key(city: str, target_date: str, source_updated: str | None, max_f: float, bucket: str | None) -> str:
    raw = json.dumps({
        "city": city,
        "target_date": target_date,
        "source_updated": source_updated,
        "max_f": round(float(max_f), 3),
        "bucket": bucket,
    }, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()


def count_event_trades(con: sqlite3.Connection, city: str, target_date: str) -> int:
    return int(con.execute(
        "SELECT COUNT(*) FROM paper_trades WHERE city=? AND target_date=?",
        (city, target_date),
    ).fetchone()[0])


def make_prediction(con: sqlite3.Connection, event: dict, max_f: float, source_updated: str | None,
                    books: dict, args) -> None:
    bucket = choose_bucket(max_f, event["markets"])
    bucket_name = bucket["bucket"] if bucket else None
    token = bucket["yes_token"] if bucket else None
    b = books.get(token, {}) if token else {}
    bid = b.get("bid")
    ask = b.get("ask")
    spread = b.get("spread")
    ask_size = b.get("ask_size")

    key = prediction_key(event["city"], event["target_date"], source_updated, max_f, bucket_name)
    if con.execute("SELECT 1 FROM predictions WHERE prediction_key=?", (key,)).fetchone():
        return

    decision = "SKIP"
    reason = ""
    time_ok, time_reason = entry_time_ok(event, args.max_days_ahead, args.max_entry_local_hour)

    if not bucket:
        reason = "no_bucket_for_forecast"
    elif not time_ok:
        reason = time_reason
    elif ask is None or ask <= 0:
        reason = "no_best_ask"
    elif ask < args.min_ask:
        reason = f"ask<{args.min_ask:.2f}"
    elif ask > args.max_ask:
        reason = f"ask>{args.max_ask:.2f}"
    elif spread is None or spread > args.max_spread:
        reason = f"spread>{args.max_spread:.2f}_or_missing"
    elif ask_size is not None and ask_size < args.stake / ask:
        reason = "best_ask_depth_too_small"
    elif count_event_trades(con, event["city"], event["target_date"]) >= args.max_trades_per_event:
        reason = "event_trade_cap"
    else:
        decision = "PAPER_BUY"
        reason = "active_nws_forecast_bucket"

    cur = con.execute("""
        INSERT INTO predictions(
          detected_at,prediction_key,city,station,target_date,event_slug,source_updated,
          forecast_max_f,bucket,market_id,yes_token,best_bid,best_ask,spread,ask_size,decision,reason
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        iso(), key, event["city"], event["station"], event["target_date"], event["event_slug"], source_updated,
        max_f, bucket_name, bucket["market_id"] if bucket else None, token,
        bid, ask, spread, ask_size, decision, reason,
    ))
    pid = cur.lastrowid

    if decision == "PAPER_BUY":
        shares = args.stake / float(ask)
        con.execute("""
            INSERT OR IGNORE INTO paper_trades(
              opened_at,prediction_id,city,station,target_date,event_slug,market_id,bucket,yes_token,
              forecast_max_f,source_updated,entry_bid,entry_ask,entry_spread,stake,shares,status
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'OPEN')
        """, (
            iso(), pid, event["city"], event["station"], event["target_date"], event["event_slug"],
            bucket["market_id"], bucket_name, token, max_f, source_updated,
            bid, float(ask), spread, args.stake, shares,
        ))

    con.commit()
    print(
        f"PREDICT {event['city']} {event['target_date']} max={max_f:.0f}F bucket={bucket_name or '-'} "
        f"bid={bid if bid is not None else '-'} ask={ask if ask is not None else '-'} "
        f"spread={spread if spread is not None else '-'} decision={decision} reason={reason}"
    )


def settle_open(con: sqlite3.Connection, http: Http) -> None:
    rows = con.execute("SELECT * FROM paper_trades WHERE status='OPEN' ORDER BY id").fetchall()
    for t in rows:
        try:
            e = http.get_json(f"{GAMMA}/events/slug/{t['event_slug']}")
            if not isinstance(e, dict) or e.get("closed") is not True:
                continue
            final = None
            for mk in e.get("markets") or []:
                if str(mk.get("id") or "") != str(t["market_id"]):
                    continue
                outcomes = jsonish(mk.get("outcomes"), [])
                prices = jsonish(mk.get("outcomePrices"), [])
                yi = 0
                for j, o in enumerate(outcomes):
                    if str(o).strip().lower() == "yes":
                        yi = j
                        break
                if yi < len(prices):
                    final = fnum(prices[yi])
                break
            if final is None or not (final >= .99 or final <= .01):
                continue
            result = 1 if final >= .99 else 0
            payout = float(t["shares"]) * result
            pnl = payout - float(t["stake"])
            con.execute(
                "UPDATE paper_trades SET status='SETTLED',resolved_at=?,result=?,payout=?,pnl=? WHERE id=?",
                (iso(), result, payout, pnl, t["id"]),
            )
            con.commit()
            print(f"SETTLED {t['city']} {t['target_date']} {t['bucket']} result={result} pnl={pnl:+.3f}")
        except Exception as exc:
            print(f"settle warning id={t['id']}: {exc}")


def cycle(con: sqlite3.Connection, http: Http, args) -> None:
    active = 0
    for city, cfg in CITY_CONFIGS.items():
        try:
            events = discover_city_events(http, city, cfg)
            active += len(events)
            for event in events:
                try:
                    max_f, updated, _ = fetch_nws_target_max(con, http, city, cfg, event["target_date"])
                    books = fetch_books(http, event["markets"])
                    make_prediction(con, event, max_f, updated, books, args)
                except Exception as exc:
                    print(f"CITY EVENT ERROR {city} {event['target_date']}: {exc}")
        except Exception as exc:
            print(f"CITY ERROR {city}: {exc}")
    settle_open(con, http)
    print(f"[{iso()}] active_prediction_cycle events={active} cities={len(CITY_CONFIGS)}")


def report(con: sqlite3.Connection) -> None:
    print("===== ACTIVE WEATHER FORECAST PAPER REPORT =====")
    p = con.execute("SELECT COUNT(*) n, COUNT(DISTINCT city) cities, COUNT(DISTINCT target_date) dates FROM predictions").fetchone()
    print(f"predictions={p['n']} cities={p['cities']} dates={p['dates']}")
    t = con.execute("""
        SELECT COUNT(*) n,
               SUM(CASE WHEN status='OPEN' THEN 1 ELSE 0 END) open_n,
               SUM(CASE WHEN status='SETTLED' THEN 1 ELSE 0 END) settled_n,
               COALESCE(SUM(CASE WHEN status='SETTLED' THEN result ELSE 0 END),0) wins,
               COALESCE(SUM(CASE WHEN status='SETTLED' THEN stake ELSE 0 END),0) stake,
               COALESCE(SUM(CASE WHEN status='SETTLED' THEN payout ELSE 0 END),0) payout,
               COALESCE(SUM(CASE WHEN status='SETTLED' THEN pnl ELSE 0 END),0) pnl
        FROM paper_trades
    """).fetchone()
    roi = (t['pnl'] / t['stake'] * 100.0) if t['stake'] else float('nan')
    print(f"trades={t['n'] or 0} open={t['open_n'] or 0} settled={t['settled_n'] or 0} wins={t['wins'] or 0} pnl={t['pnl']:+.3f} ROI={roi:.2f}%")
    print("\nLATEST PREDICTIONS")
    for r in con.execute("""
        SELECT detected_at,city,target_date,forecast_max_f,bucket,best_bid,best_ask,spread,decision,reason
        FROM predictions ORDER BY id DESC LIMIT 30
    """).fetchall():
        print(dict(r))
    print("\nLATEST TRADES")
    for r in con.execute("""
        SELECT id,opened_at,city,target_date,bucket,forecast_max_f,entry_ask,status,result,pnl
        FROM paper_trades ORDER BY id DESC LIMIT 30
    """).fetchall():
        print(dict(r))


def main() -> int:
    ap = argparse.ArgumentParser(description="PAPER-ONLY active NWS forecast -> Polymarket bucket experiment")
    ap.add_argument("--db", default="phase3_out/weather_active_forecast.sqlite")
    ap.add_argument("--interval", type=int, default=120)
    ap.add_argument("--stake", type=float, default=1.0)
    ap.add_argument("--min-ask", type=float, default=.02)
    ap.add_argument("--max-ask", type=float, default=.35)
    ap.add_argument("--max-spread", type=float, default=.08)
    ap.add_argument("--max-days-ahead", type=int, default=2)
    ap.add_argument("--max-entry-local-hour", type=int, default=12)
    ap.add_argument("--max-trades-per-event", type=int, default=1)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()

    if not PAPER_ONLY:
        raise RuntimeError("PAPER_ONLY invariant violated")
    con = db_open(Path(args.db))
    if args.report:
        report(con)
        return 0

    print("===== ACTIVE MULTI-CITY WEATHER FORECAST PAPER =====")
    print("PAPER_ONLY=true; no private key, no trading credentials, no order-placement code.")
    print(
        f"cities={len(CITY_CONFIGS)} stake=${args.stake:.2f} ask={args.min_ask:.2f}..{args.max_ask:.2f} "
        f"max_spread={args.max_spread:.2f} max_days_ahead={args.max_days_ahead} "
        f"max_entry_local_hour={args.max_entry_local_hour}"
    )

    http = Http()
    while True:
        try:
            cycle(con, http, args)
            con.execute("INSERT INTO runs(ts,status,note) VALUES(?,?,?)", (iso(), "OK", None))
            con.commit()
        except KeyboardInterrupt:
            return 0
        except Exception as exc:
            print(f"CYCLE ERROR: {exc}")
            con.execute("INSERT INTO runs(ts,status,note) VALUES(?,?,?)", (iso(), "ERROR", str(exc)))
            con.commit()
        if args.once:
            return 0
        time.sleep(max(60, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
