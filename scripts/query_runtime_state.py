#!/usr/bin/env python3
from __future__ import annotations

import os
import sqlite3
from pathlib import Path


def main() -> int:
    db_path = Path(os.getenv("GHOST_TRADER_DB_PATH", "data/ghost_trader.db"))
    if not db_path.exists():
        print(f"SQLite database not found: {db_path}")
        return 1

    connection = sqlite3.connect(db_path)
    cursor = connection.cursor()

    wallet = cursor.execute("SELECT balance FROM wallet WHERE id = 1").fetchone()
    venue_accounts = cursor.execute(
        """
        SELECT venue, execution_mode, cash_balance, equity, available_balance
        FROM venue_accounts
        ORDER BY venue, execution_mode
        """
    ).fetchall()
    trades = cursor.execute(
        """
        SELECT id, venue, instrument_type, market_id, side, size, price, confidence, whale_address, timestamp
        FROM trades
        ORDER BY id DESC
        LIMIT 10
        """
    ).fetchall()
    positions = cursor.execute(
        """
        SELECT id, venue, instrument_type, symbol_or_market_id, side, entry_price, mark_price, notional_usd, unrealized_pnl, realized_pnl, status
        FROM venue_positions
        ORDER BY id DESC
        LIMIT 10
        """
    ).fetchall()
    orders = cursor.execute(
        """
        SELECT id, venue, symbol_or_market_id, order_type, side, qty, price, stop_price, reduce_only, status
        FROM venue_orders
        WHERE status = 'OPEN'
        ORDER BY id DESC
        LIMIT 10
        """
    ).fetchall()
    try:
        whale_counts = cursor.execute(
            """
            SELECT source_type, COUNT(*) AS count
            FROM whale_wallets
            WHERE enabled = 1
            GROUP BY source_type
            ORDER BY source_type
            """
        ).fetchall()
        whale_wallets = cursor.execute(
            """
            SELECT address, source_type, discovery_score, last_event_amount, event_count_24h, failure_streak
            FROM whale_wallets
            WHERE enabled = 1
            ORDER BY discovery_score DESC, last_event_amount DESC
            LIMIT 10
            """
        ).fetchall()
    except sqlite3.OperationalError:
        whale_counts = []
        whale_wallets = []
    connection.close()

    print(f"DB_PATH={db_path}")
    print(f"WALLET_BALANCE={wallet[0] if wallet else 'missing'}")
    print("VENUE_ACCOUNTS")
    for account in venue_accounts:
        print(account)
    print("RECENT_TRADES")
    for trade in trades:
        print(trade)
    print("OPEN_POSITIONS")
    for position in positions:
        print(position)
    print("OPEN_ORDERS")
    for order in orders:
        print(order)
    print("WHALE_WALLET_COUNTS")
    for count in whale_counts:
        print(count)
    print("TOP_WHALE_WALLETS")
    for whale_wallet in whale_wallets:
        print(whale_wallet)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
