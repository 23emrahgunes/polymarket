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
    trades = cursor.execute(
        """
        SELECT id, market_id, side, size, price, confidence, whale_address, timestamp
        FROM trades
        ORDER BY id DESC
        LIMIT 10
        """
    ).fetchall()
    connection.close()

    print(f"DB_PATH={db_path}")
    print(f"WALLET_BALANCE={wallet[0] if wallet else 'missing'}")
    print("RECENT_TRADES")
    for trade in trades:
        print(trade)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
