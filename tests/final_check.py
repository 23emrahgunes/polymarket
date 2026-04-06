import os
import sys
import asyncio
import logging

# Set up imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.scanner import MarketScanner
from src.explorer import MarketExplorer
from py_clob_client.client import ClobClient
from py_clob_client.constants import POLYGON

# Configure logging
logging.basicConfig(level=logging.INFO, format='[FINAL CHECK] %(message)s')
logger = logging.getLogger(__name__)

async def test_polymarket_connection():
    logger.info("Test Case 1: Verifying Polymarket API connection...")
    # Use a dummy but valid format private key if not set
    private_key = os.getenv("POLYGON_PRIVATE_KEY", "0x0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef")
    client = ClobClient(
        host="https://clob.polymarket.com",
        chain_id=POLYGON,
        key=private_key
    )
    explorer = MarketExplorer(client)
    markets = await explorer.fetch_active_markets()

    if isinstance(markets, list):
        logger.info(f"SUCCESS: MarketExplorer returned {len(markets)} markets.")
        return True
    else:
        logger.error("FAILURE: MarketExplorer failed to return valid data.")
        return False

def test_module_imports():
    logger.info("Test Case 2: Verifying module imports...")
    try:
        from src.main import main
        from src.logic import calculate_black_scholes_prob
        from src.database import Database
        logger.info("SUCCESS: src module is importable without ModuleNotFoundError.")
        return True
    except ModuleNotFoundError as e:
        logger.error(f"FAILURE: ModuleNotFoundError - {e}")
        return False
    except Exception as e:
        logger.error(f"FAILURE: Unexpected error - {e}")
        return False

async def test_exchange_websocket():
    logger.info("Test Case 3: Verifying Exchange WebSocket connection...")
    scanner = MarketScanner(exchange_id='coinbase')
    try:
        # Test with BTC/USDT ticker
        symbols = ["BTC/USDT"]
        task = asyncio.create_task(scanner.watch_tickers(symbols))

        # Wait up to 15 seconds for price update
        for i in range(15):
            if scanner.current_prices.get("BTC/USDT"):
                logger.info(f"SUCCESS: Exchange WebSocket connected. BTC Price: {scanner.current_prices.get('BTC/USDT')}")
                task.cancel()
                await scanner.close()
                return True
            await asyncio.sleep(1)

        logger.error("FAILURE: Exchange WebSocket timed out waiting for ticker updates.")
        task.cancel()
        await scanner.close()
        return False
    except Exception as e:
        logger.error(f"FAILURE: Exchange WebSocket error - {e}")
        await scanner.close()
        return False

async def main():
    results = []

    results.append(test_module_imports())
    results.append(await test_polymarket_connection())
    results.append(await test_exchange_websocket())

    if all(results):
        logger.info("====================================")
        logger.info("ALL TESTS PASSED - Ghost Intelligence v2.0 is READY.")
        logger.info("====================================")
        sys.exit(0)
    else:
        logger.error("====================================")
        logger.error("SOME TESTS FAILED - Check logs above.")
        logger.error("====================================")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
