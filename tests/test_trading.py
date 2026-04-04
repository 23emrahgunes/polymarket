import pytest
import asyncio
from src.database import Database
from src.trading import PaperTrader
import os

@pytest.mark.asyncio
async def test_paper_trader_double_spending():
    db_path = "tests/test_paper_trader_ds.db"
    if os.path.exists(db_path):
        os.remove(db_path)

    db = Database(db_path)
    await db.connect()
    trader = PaperTrader(db)

    # Try to execute two trades concurrently with total size > balance
    # Balance is $1000 initially. Let's try 3 trades of $400 each.
    tasks = [
        trader.execute_trade("BTC-1", "BUY", 400, 0.5, 0.1, 0.8),
        trader.execute_trade("BTC-2", "BUY", 400, 0.5, 0.1, 0.8),
        trader.execute_trade("BTC-3", "BUY", 400, 0.5, 0.1, 0.8)
    ]

    results = await asyncio.gather(*tasks)

    # Only 2 trades should succeed ($400 + $400 = $800 < $1000; $800 + $400 = $1200 > $1000)
    success_count = sum(1 for res in results if res[0])
    assert success_count == 2

    final_balance = await db.get_balance()
    assert final_balance == 200.0 # 1000 - 800

    await db.close()
    if os.path.exists(db_path):
        os.remove(db_path)
