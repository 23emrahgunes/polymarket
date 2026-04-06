import asyncio
import logging
import requests
import os
import sys

# Handle path
ABS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(ABS_ROOT)

logging.basicConfig(level=logging.INFO, format='[VERIFY-STREAM] %(message)s')
logger = logging.getLogger(__name__)

async def verify_activity_stream():
    """
    Connects to the Polymarket Gamma API /markets endpoint to prove visibility
    of active trading data.
    """
    url = "https://gamma-api.polymarket.com/markets?active=true&limit=10"
    logger.info(f"Connecting to Data Stream (Markets): {url}")

    try:
        response = await asyncio.to_thread(requests.get, url, timeout=10)
        if response.status_code == 200:
            markets = response.json()
            if not isinstance(markets, list):
                logger.error("Invalid response format.")
                return

            logger.info("====================================")
            logger.info(f"LATEST TRADING OPPORTUNITIES DETECTED:")
            logger.info("====================================")

            for i, m in enumerate(markets):
                vol = m.get("volume24hr") or m.get("volume")
                question = m.get("question")
                market_id = m.get("id")

                logger.info(f"#{i+1} | 24h Vol: ${float(vol):,.2f} | Market: {question[:50]}... | ID: {market_id}")

            logger.info("====================================")
            logger.info("PROVED: Bot can see global market data and volume.")
            return True
        else:
            logger.error(f"Failed to connect. Status: {response.status_code}")
            return False
    except Exception as e:
        logger.error(f"Error verifying stream: {e}")
        return False

if __name__ == "__main__":
    asyncio.run(verify_activity_stream())
