#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="phase3_out/weather_forward_paper.sqlite")
    args = ap.parse_args()

    p = Path(args.db)
    if not p.exists():
        raise SystemExit(f"DB not found: {p}")

    con = sqlite3.connect(p)
    con.row_factory = sqlite3.Row

    def one(sql: str):
        return con.execute(sql).fetchone()

    runs = one("SELECT COUNT(*) n, SUM(CASE WHEN status='OK' THEN 1 ELSE 0 END) ok_n, SUM(CASE WHEN status='ERROR' THEN 1 ELSE 0 END) err_n, MAX(ts) last_ts FROM runs")
    snaps = one("SELECT COUNT(*) n, COUNT(DISTINCT target_date) dates, COUNT(DISTINCT yes_token) tokens, MIN(ts) first_ts, MAX(ts) last_ts FROM market_snapshots")
    fc = one("SELECT COUNT(*) n, COUNT(DISTINCT target_date) dates, MIN(detected_at) first_ts, MAX(detected_at) last_ts FROM hko_forecasts")
    rev = one("SELECT COUNT(*) n, SUM(CASE WHEN decision='PAPER_BUY' THEN 1 ELSE 0 END) buys FROM revisions")
    tr = one("SELECT COUNT(*) n, SUM(CASE WHEN status='OPEN' THEN 1 ELSE 0 END) open_n, SUM(CASE WHEN status='SETTLED' THEN 1 ELSE 0 END) settled_n FROM paper_trades")

    print("===== PHASE 3 DAEMON STATUS =====")
    print(f"runs={runs['n'] or 0} ok={runs['ok_n'] or 0} errors={runs['err_n'] or 0} last_heartbeat={runs['last_ts']}")
    print(f"snapshots={snaps['n'] or 0} dates={snaps['dates'] or 0} tokens={snaps['tokens'] or 0} first={snaps['first_ts']} last={snaps['last_ts']}")
    print(f"forecast_rows={fc['n'] or 0} forecast_dates={fc['dates'] or 0} first={fc['first_ts']} last={fc['last_ts']}")
    print(f"revisions={rev['n'] or 0} paper_buy_revisions={rev['buys'] or 0}")
    print(f"trades={tr['n'] or 0} open={tr['open_n'] or 0} settled={tr['settled_n'] or 0}")

    print("\n===== LAST 10 RUNS =====")
    for r in con.execute("SELECT id,ts,status,note FROM runs ORDER BY id DESC LIMIT 10"):
        print(dict(r))

    print("\n===== LATEST SNAPSHOTS =====")
    for r in con.execute("SELECT ts,target_date,bucket,best_bid,best_ask,spread,hko_max_c FROM market_snapshots ORDER BY id DESC LIMIT 12"):
        print(dict(r))

    print("\n===== LAST ERRORS =====")
    errs = list(con.execute("SELECT id,ts,note FROM runs WHERE status='ERROR' ORDER BY id DESC LIMIT 10"))
    if not errs:
        print("none")
    else:
        for r in errs:
            print(dict(r))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
