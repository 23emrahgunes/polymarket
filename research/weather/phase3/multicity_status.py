#!/usr/bin/env python3
from __future__ import annotations
import sqlite3
from pathlib import Path

DB=Path("phase3_out/weather_multicity_paper.sqlite")
con=sqlite3.connect(DB)
con.row_factory=sqlite3.Row
print("===== MULTI-CITY PAPER STATUS =====")
r=con.execute("SELECT COUNT(*) n,SUM(status='OK') ok,SUM(status='ERROR') errors,MAX(ts) last FROM runs").fetchone()
print(f"runs={r['n'] or 0} ok={r['ok'] or 0} errors={r['errors'] or 0} last_heartbeat={r['last']}")
s=con.execute("SELECT COUNT(*) n,COUNT(DISTINCT city) cities,COUNT(DISTINCT target_date) dates,COUNT(DISTINCT yes_token) tokens,MIN(ts) first,MAX(ts) last FROM market_snapshots").fetchone()
print(f"snapshots={s['n'] or 0} cities={s['cities'] or 0} dates={s['dates'] or 0} tokens={s['tokens'] or 0} first={s['first']} last={s['last']}")
f=con.execute("SELECT COUNT(*) n,COUNT(DISTINCT city) cities,COUNT(DISTINCT target_date) dates,MIN(detected_at) first,MAX(detected_at) last FROM forecasts").fetchone()
print(f"forecasts={f['n'] or 0} cities={f['cities'] or 0} dates={f['dates'] or 0} first={f['first']} last={f['last']}")
rv=con.execute("SELECT COUNT(*) n,SUM(decision='PAPER_BUY') buys FROM revisions").fetchone()
print(f"revisions={rv['n'] or 0} paper_buy_revisions={rv['buys'] or 0}")
t=con.execute("SELECT COUNT(*) n,SUM(status='OPEN') open_n,SUM(status='SETTLED') settled_n FROM paper_trades").fetchone()
print(f"trades={t['n'] or 0} open={t['open_n'] or 0} settled={t['settled_n'] or 0}")
print("\n===== BY CITY =====")
for x in con.execute("""SELECT city,COUNT(*) snapshots,COUNT(DISTINCT target_date) dates,COUNT(DISTINCT yes_token) tokens,MAX(ts) last
                        FROM market_snapshots GROUP BY city ORDER BY city""").fetchall(): print(dict(x))
print("\n===== LATEST REVISIONS =====")
rows=con.execute("SELECT detected_at,city,target_date,old_max_f,new_max_f,old_bucket,new_bucket,decision,reason FROM revisions ORDER BY id DESC LIMIT 30").fetchall()
print("none" if not rows else "")
for x in rows: print(dict(x))
print("\n===== LATEST TRADES =====")
rows=con.execute("SELECT id,opened_at,city,target_date,bucket,old_max_f,new_max_f,entry_ask,status,result,pnl FROM paper_trades ORDER BY id DESC LIMIT 30").fetchall()
print("none" if not rows else "")
for x in rows: print(dict(x))
print("\n===== LAST ERRORS =====")
rows=con.execute("SELECT ts,note FROM runs WHERE status='ERROR' ORDER BY id DESC LIMIT 10").fetchall()
print("none" if not rows else "")
for x in rows: print(dict(x))
