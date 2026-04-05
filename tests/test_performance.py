import pytest
import asyncio
import os
from src.database import Database

@pytest.mark.asyncio
async def test_bot_performance_metrics():
    db_path = "tests/test_performance.db"
    if os.path.exists(db_path):
        os.remove(db_path)

    db = Database(db_path)
    await db.connect()

    # Simulate a winning trade
    await db.add_trade("M1", "BUY", 100, 0.5, 0.1, 0.8, status="CLOSED_WIN")
    await db.update_trade_resolution(1, "CLOSED_WIN", 100.0) # $100 profit

    # Simulate a losing trade
    await db.add_trade("M2", "BUY", 100, 0.5, 0.1, 0.8, status="CLOSED_LOSS")
    await db.update_trade_resolution(2, "CLOSED_LOSS", -100.0) # $100 loss

    total, wins, win_rate, total_pnl = await db.get_bot_performance()
    assert total == 2
    assert wins == 1
    assert win_rate == 50.0
    assert total_pnl == 0.0

    await db.close()
    if os.path.exists(db_path):
        os.remove(db_path)

@pytest.mark.asyncio
async def test_whale_accuracy_tracking():
    db_path = "tests/test_whale_stats.db"
    if os.path.exists(db_path):
        os.remove(db_path)

    db = Database(db_path)
    await db.connect()

    whale = "0xTestWhale"
    await db.update_whale_stats(whale, 50.0) # Win
    await db.update_whale_stats(whale, -20.0) # Loss

    stats = await db.get_whale_stats(whale)
    assert stats['total_trades'] == 2
    assert stats['wins'] == 1
    assert stats['trust_score'] == 0.5

    await db.close()
    if os.path.exists(db_path):
        os.remove(db_path)
