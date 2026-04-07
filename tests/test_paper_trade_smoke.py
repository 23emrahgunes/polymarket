import pytest
import asyncio
import os
from src.database import Database
from src.trading import TradeExecutor

@pytest.mark.asyncio
async def test_paper_trade_smoke_flow():
    """
    Smoke test for PAPER-TRADE:
    - Inject mock signal
    - Confirm trade insertion
    - Confirm balance decrease
    """
    db_path = "tests/test_smoke.db"
    if os.path.exists(db_path): os.remove(db_path)

    db = Database(db_path)
    await db.connect()

    # Initialize with 1000
    initial_balance = await db.get_balance()
    assert initial_balance == 1000.0

    executor = TradeExecutor(db, live_mode=False)

    # Inject Signal
    # market_id, side, size, price, edge, confidence
    success, msg = await executor.execute_trade("SMOKE_MARKET", "YES", 50.0, 0.5, 0.1, 0.95)

    assert success is True
    assert "[PAPER]" in msg

    # Verify DB insertion
    trades = await db.get_open_trades()
    assert len(trades) == 1
    assert trades[0]["market_id"] == "SMOKE_MARKET"
    assert trades[0]["size"] == 50.0

    # Verify balance decrease
    new_balance = await db.get_balance()
    assert new_balance == 950.0

    await db.close()
    if os.path.exists(db_path): os.remove(db_path)
    print("\n[PAPER-TRADE] Smoke test verified successful.")
