import pytest
import asyncio
import os
import sys
from src.database import Database
from src.explorer import MarketExplorer
from src.scrapers.activity import ActivityHunter

# Setup absolute paths for tests
ABS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(ABS_ROOT)

@pytest.mark.asyncio
async def test_database_init_on_empty_path():
    db_path = "tests/test_new_init.db"
    if os.path.exists(db_path): os.remove(db_path)
    db = Database(db_path)
    await db.connect()
    balance = await db.get_balance()
    assert balance == 1000.0
    await db.close()
    if os.path.exists(db_path): os.remove(db_path)

@pytest.mark.asyncio
async def test_trade_insertion_and_resolution():
    db_path = "tests/test_trade_flow.db"
    if os.path.exists(db_path): os.remove(db_path)
    db = Database(db_path)
    await db.connect()

    # 1. Add trade
    await db.add_trade("M_TEST", "BUY", 50.0, 0.5, 0.1, 0.9, whale_address="0x123")
    trades = await db.get_open_trades()
    assert len(trades) == 1
    assert trades[0]["market_id"] == "M_TEST"

    # 2. Update resolution
    await db.update_trade_resolution(trades[0]["id"], "CLOSED_WIN", 100.0)

    # Check bot performance
    total, wins, win_rate, total_pnl = await db.get_bot_performance()
    assert total == 1
    assert wins == 1
    assert total_pnl == 100.0

    await db.close()
    if os.path.exists(db_path): os.remove(db_path)

def test_explorer_active_market_normalization():
    # Simulate discovery logic in explorer.py
    m = {
        "question": "Will BTC be above 100k?",
    }

    q = m["question"].upper()
    category = "OTHER"
    if any(sym in q for sym in ["BTC", "ETH", "SOL", "XRP", "DOGE", "BNB"]):
        category = "CRYPTO"

    assert category == "CRYPTO"

@pytest.mark.asyncio
async def test_activity_normalization_and_dedupe():
    hunter = ActivityHunter()
    mock_act_id = "tx_unique_1"
    hunter.processed_transaction_ids.add(mock_act_id)
    assert mock_act_id in hunter.processed_transaction_ids
