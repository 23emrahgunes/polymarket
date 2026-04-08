import asyncio
import logging
import os
import sqlite3
import sys
import time


ABS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(ABS_ROOT)

from src.database import Database
from src.runtime import GhostBotRuntime, RuntimeSettings


DEFAULT_DB_PATH = os.path.join("data", "runtime_verification.db")


logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


def resolve_db_path() -> str:
    requested_path = os.getenv("RUNTIME_VERIFY_DB_PATH", DEFAULT_DB_PATH)
    if not os.path.exists(requested_path):
        return requested_path

    try:
        os.remove(requested_path)
        return requested_path
    except PermissionError:
        timestamp = int(time.time())
        return os.path.join("data", f"runtime_verification_{timestamp}.db")


async def verify_runtime_trade():
    db_path = resolve_db_path()
    os.environ["DEBUG_SIGNAL_MODE"] = "true"
    os.environ["DEBUG_SIGNAL_PROFILE"] = "sports"
    os.environ["RUNTIME_VERIFY_ONCE"] = "true"
    os.environ["VERIFY_REQUIRED_VENUES"] = "polymarket"
    os.environ["VERIFY_REQUIRED_CATEGORY"] = "SPORTS"
    os.environ["GHOST_TRADER_DB_PATH"] = db_path

    database = Database(db_path)
    await database.connect()
    wallet_before = await database.get_balance()
    await database.close()

    runtime = GhostBotRuntime(RuntimeSettings.from_env())
    await runtime.run()

    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    cursor = connection.cursor()

    wallet_after = cursor.execute("SELECT balance FROM wallet WHERE id = 1").fetchone()["balance"]
    trades = cursor.execute(
        """
        SELECT id, market_id, side, size, price, confidence, whale_address, timestamp
        FROM trades
        ORDER BY id ASC
        """
    ).fetchall()
    connection.close()

    print("DB_PATH")
    print(db_path)
    print("WALLET_BEFORE")
    print(wallet_before)
    print("WALLET_AFTER")
    print(wallet_after)
    print("TRADES")
    for trade in trades:
        print(dict(trade))

    if not trades:
        raise SystemExit("No runtime trade was inserted into SQLite.")
    if wallet_after >= wallet_before:
        raise SystemExit("Wallet balance did not decrease after runtime verification.")


if __name__ == "__main__":
    asyncio.run(verify_runtime_trade())
