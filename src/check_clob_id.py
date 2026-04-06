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

logging.basicConfig(level=logging.INFO, format='[CLOB-CHECK] %(message)s')
logger = logging.getLogger(__name__)

async def main():
    logger.info("Starting Polymarket CLOB Token-ID Proof Check...")

    private_key = os.getenv("POLYGON_PRIVATE_KEY", "0x0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef")
    client = ClobClient(
        host="https://clob.polymarket.com",
        chain_id=POLYGON,
        key=private_key
    )

    explorer = MarketExplorer(client)

    logger.info("Step 1: Discovering top active market clobTokenIds...")
    markets = await explorer.fetch_active_markets(limit=5)

    if not markets:
        logger.error("FAILURE: No active markets found.")
        sys.exit(1)

    success_count = 0
    for m in markets[:3]:
        token_id = m.get("token_id") # This is the clobTokenId
        question = m.get("question")

        # PROOF: Exact URL required by Polymarket CLOB
        clob_url = f"https://clob.polymarket.com/book?token_id={token_id}"
        logger.info(f"Targeting Market: {question[:40]}...")
        logger.info(f"Generated URL: {clob_url}")

        try:
            # Using the SDK method that hits the above URL
            orderbook = await asyncio.to_thread(client.get_order_book, token_id)

            # Check for bids/asks to prove it's a valid book
            if hasattr(orderbook, 'bids') or (isinstance(orderbook, dict) and "bids" in orderbook):
                logger.info(f"PROOF: Valid Orderbook Received (200 OK). No 404.")
                success_count += 1
            else:
                logger.error(f"FAILURE: Data received but invalid format for {token_id}")
        except Exception as e:
            logger.error(f"FAILURE: 404 or Error fetching {token_id}: {e}")

    if success_count >= 1:
        logger.info("====================================")
        logger.info(f"FINAL RESULT: PASS ({success_count}/3 successful Proofs)")
        logger.info("====================================")
        sys.exit(0)
    else:
        logger.error("====================================")
        logger.error("FINAL RESULT: FAIL (ID Mapping Issue Persists)")
        logger.error("====================================")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
