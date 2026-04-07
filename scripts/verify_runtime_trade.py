import asyncio
import logging
import os
import sys

# absolute path handling
ABS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(ABS_ROOT)

from src.database import Database
from src.scanner import MarketScanner
from src.trading import TradeExecutor
from src.copy_trader import CopyTrader
from unittest.mock import MagicMock, AsyncMock

# Force DEBUG_SIGNAL_MODE
os.environ["DEBUG_SIGNAL_MODE"] = "true"
os.environ["ENV"] = "test" # Bypasses real polymarket SDK initialization

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def verify_runtime_trade():
    db_path = "data/ghost_trader_runtime_test.db"
    if os.path.exists(db_path): os.remove(db_path)

    db = Database(db_path)
    await db.connect()

    scanner = MarketScanner(exchange_id='coinbase')
    trader = TradeExecutor(db)
    copy_trader = CopyTrader(trader, scanner)

    # 1. Simulate a Whale Action Signal
    market_id = "test_market_runtime"
    token_id = "test_token_runtime"

    # Mock scanner.get_token_price to return 0.5
    scanner.get_token_price = AsyncMock(return_value=0.5)

    # Mock category lookup
    copy_trader._get_market_category = AsyncMock(return_value="CRYPTO")

    whale_action = {
        "whale": "0x1234567890abcdef",
        "action": "BUY",
        "market_id": market_id,
        "token_id": token_id,
        "price": 0.48
    }

    logger.info("Injecting simulated whale action into CopyTrader...")
    success = await copy_trader.evaluate_signal(whale_action)

    if success:
        logger.info("CopyTrader evaluation SUCCESS.")
    else:
        logger.error("CopyTrader evaluation FAILED.")
        sys.exit(1)

    # 2. Check Database for trade
    trades = await db.get_open_trades()
    if len(trades) > 0:
        trade = dict(trades[0])
        logger.info(f"[PAPER-TRADE-RUNTIME] inserted trade id={trade['id']} for market={trade['market_id']}")

        balance = await db.get_balance()
        logger.info(f"Updated Balance: {balance}")

        if balance < 1000.0:
            logger.info("RUNTIME INTEGRATION VERIFIED - TRADE CREATED AND BALANCE UPDATED")
        else:
            logger.error("Balance not updated correctly.")
            sys.exit(1)
    else:
        logger.error("No trade found in SQLite.")
        sys.exit(1)

    await db.close()
    await scanner.close()
    if os.path.exists(db_path): os.remove(db_path)

if __name__ == "__main__":
    asyncio.run(verify_runtime_trade())
