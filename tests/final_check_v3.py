import os
import sys
import asyncio
import logging

# Set up imports: absolute path handling
ABS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(ABS_ROOT)

from src.scanner import MarketScanner
from src.explorer import MarketExplorer
from src.whale_tracker import WhaleTracker
from src.copy_trader import CopyTrader
from src.database import Database
from py_clob_client.client import ClobClient
from py_clob_client.constants import POLYGON

# Configure logging
logging.basicConfig(level=logging.INFO, format='[FINAL CHECK V3] %(message)s')
logger = logging.getLogger(__name__)

def test_module_imports():
    logger.info("Test Case 1: Verifying module imports...")
    try:
        from src.main import main
        from src.whale_tracker import WhaleTracker
        from src.copy_trader import CopyTrader
        logger.info("SUCCESS: src module is importable without ModuleNotFoundError.")
        return True
    except ModuleNotFoundError as e:
        logger.error(f"FAILURE: ModuleNotFoundError - {e}")
        return False
    except Exception as e:
        logger.error(f"FAILURE: Unexpected error - {e}")
        return False

async def test_leaderboard_logic():
    logger.info("Test Case 2: Verifying Leaderboard fetching logic...")
    private_key = os.getenv("POLYGON_PRIVATE_KEY", "0x0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef")
    client = ClobClient(
        host="https://clob.polymarket.com",
        chain_id=POLYGON,
        key=private_key
    )
    whale_tracker = WhaleTracker(client)
    whales = await whale_tracker.fetch_top_whales(limit=20)

    if isinstance(whales, list) and len(whales) == 20:
        logger.info(f"SUCCESS: WhaleTracker returned {len(whales)} elite wallets.")
        return True
    else:
        logger.error("FAILURE: WhaleTracker failed to return valid data.")
        return False

async def test_price_guard_logic():
    logger.info("Test Case 3: Verifying Price Guard logic with a mock trade...")
    # Mock components for fast check
    class MockScanner:
        def __init__(self, price):
            self.price = price
        async def get_token_price(self, token_id):
            return self.price

    class MockTrader:
        async def execute_trade(self, *args, **kwargs):
            return True, "Trade Success"

    # 1. Price within guard range (0%)
    copy_trader_match = CopyTrader(MockTrader(), MockScanner(0.50))
    whale_action_match = {
        "whale": "0xWhaleMatch",
        "action": "BUY",
        "market_id": "TEST_MARKET",
        "price": 0.50 # Current price is 0.50, diff = 0%
    }
    match_result = await copy_trader_match.evaluate_signal(whale_action_match)

    # 2. Price outside guard range (25%)
    copy_trader_fail = CopyTrader(MockTrader(), MockScanner(0.50))
    whale_action_fail = {
        "whale": "0xWhaleFail",
        "action": "BUY",
        "market_id": "TEST_MARKET",
        "price": 0.40 # Current price is 0.50, diff = 25%
    }
    fail_result = await copy_trader_fail.evaluate_signal(whale_action_fail)

    if match_result is True and fail_result is False:
        logger.info("SUCCESS: Price Guard logic verified.")
        return True
    else:
        logger.error(f"FAILURE: Price Guard logic error. Match: {match_result}, Fail: {fail_result}")
        return False

async def main():
    results = []

    results.append(test_module_imports())
    results.append(await test_leaderboard_logic())
    results.append(await test_price_guard_logic())

    if all(results):
        logger.info("====================================")
        logger.info("ALL TESTS PASSED - Ghost Intelligence v3.0 is READY.")
        logger.info("====================================")
        sys.exit(0)
    else:
        logger.error("====================================")
        logger.error("SOME TESTS FAILED - Check logs above.")
        logger.error("====================================")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
