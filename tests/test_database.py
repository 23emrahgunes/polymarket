import pytest
import aiofiles
import os
from src.database import Database

@pytest.mark.asyncio
async def test_database_initialization():
    db_path = "tests/test_ghost_trader.db"
    if os.path.exists(db_path):
        os.remove(db_path)

    db = Database(db_path)
    await db.connect()

    balance = await db.get_balance()
    assert balance == 1000.0

    await db.close()
    if os.path.exists(db_path):
        os.remove(db_path)

@pytest.mark.asyncio
async def test_update_balance():
    db_path = "tests/test_ghost_trader_balance.db"
    if os.path.exists(db_path):
        os.remove(db_path)

    db = Database(db_path)
    await db.connect()

    await db.update_balance(950.0)
    balance = await db.get_balance()
    assert balance == 950.0

    await db.close()
    if os.path.exists(db_path):
        os.remove(db_path)

@pytest.mark.asyncio
async def test_trades():
    db_path = "tests/test_ghost_trader_trades.db"
    if os.path.exists(db_path):
        os.remove(db_path)

    db = Database(db_path)
    await db.connect()

    await db.add_trade("BTC-YES", "BUY", 50, 0.6, 0.1, 0.8)
    open_trades = await db.get_open_trades()
    assert len(open_trades) == 1
    assert open_trades[0]["market_id"] == "BTC-YES"

    trade_id = open_trades[0]["id"]
    await db.update_trade_status(trade_id, "CLOSED")

    open_trades = await db.get_open_trades()
    assert len(open_trades) == 0

    await db.close()
    if os.path.exists(db_path):
        os.remove(db_path)
