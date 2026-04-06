import os
import sys
import asyncio
import logging
import requests

# Handle path setup
ABS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(ABS_ROOT)

from py_clob_client.client import ClobClient
from py_clob_client.constants import POLYGON
from src.explorer import MarketExplorer

logging.basicConfig(level=logging.INFO, format='[LIVE-CHECK] %(message)s')
logger = logging.getLogger(__name__)

async def main():
    logger.info("Starting Polymarket API Live-Check...")

    private_key = os.getenv("POLYGON_PRIVATE_KEY", "0x0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef")
    client = ClobClient(
        host="https://clob.polymarket.com",
        chain_id=POLYGON,
        key=private_key
    )

    explorer = MarketExplorer(client)

    logger.info("Step 1: Fetching active markets from Gamma API...")
    markets = await explorer.fetch_active_markets(limit=5)

    if not markets:
        logger.error("FAILURE: No active markets discovered.")
        sys.exit(1)

    logger.info(f"SUCCESS: Discovered {len(markets)} active markets.")

    success_count = 0
    for m in markets[:3]:
        token_id = m.get("token_id")
        question = m.get("question")

        logger.info(f"Checking Orderbook for: {question[:50]}... (ID: {token_id})")

        try:
            # We verify the URL structure requirement via the SDK call
            # URL: https://clob.polymarket.com/book?token_id={id}
            orderbook = await asyncio.to_thread(client.get_order_book, token_id)

            # Check if orderbook data is valid (SDK throws exception or returns null-like if 404)
            if orderbook and (hasattr(orderbook, 'bids') or isinstance(orderbook, dict)):
                logger.info(f"SUCCESS: Orderbook received for {token_id}. Status: 200 OK")
                success_count += 1
            else:
                logger.error(f"FAILURE: Invalid orderbook data for {token_id}.")
        except Exception as e:
            logger.error(f"FAILURE: Fetch failed for {token_id}. Error: {e}")

    if success_count >= 1:
        logger.info("====================================")
        logger.info(f"FINAL RESULT: PASS ({success_count}/3 successful fetches)")
        logger.info("====================================")
        sys.exit(0)
    else:
        logger.error("====================================")
        logger.error("FINAL RESULT: FAIL (Zero orderbooks fetched)")
        logger.error("====================================")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
