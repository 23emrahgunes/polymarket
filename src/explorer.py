import asyncio
import logging
from py_clob_client.client import ClobClient
from py_clob_client.constants import POLYGON
import os

logger = logging.getLogger(__name__)

class MarketExplorer:
    def __init__(self, polymarket_client: ClobClient):
        self.polymarket = polymarket_client

    async def fetch_active_markets(self):
        """
        Fetches all active markets from Polymarket CLOB and filters them.
        Refined: Only process markets that are explicitly marked as active.
        """
        try:
            # Wrap blocking SDK call in to_thread
            raw_response = await asyncio.to_thread(self.polymarket.get_markets)

            markets = []
            if isinstance(raw_response, list):
                markets = raw_response
            elif isinstance(raw_response, dict):
                markets = raw_response.get("data", raw_response.get("markets", []))
            else:
                logger.warning(f"Unexpected response type: {type(raw_response)}")
                return []

            filtered_markets = []
            for market in markets:
                if not isinstance(market, dict): continue

                # Refined: Check for active status
                # Polymarket API usually provides 'active' (bool) or 'closed' (bool)
                if market.get("closed") is True or market.get("active") is False:
                    continue

                # Liquid Filter: Volume > $10,000
                volume_24h = float(market.get("volume_24h", 0))
                if volume_24h < 10000:
                    continue

                # Fetch tokens for spread calculation
                tokens = market.get("tokens", [])
                if tokens:
                    token_id = tokens[0].get("token_id")
                    spread = await self.get_spread(token_id)
                    if spread > 0.02:
                        continue # Spread > 2% filter

                # Category Detection
                question = market.get("question", "").upper()
                if any(sym in question for sym in ["BTC", "ETH", "SOL", "XRP", "DOGE", "BNB"]):
                    market["category"] = "CRYPTO"
                elif any(kw in question for kw in ["TRUMP", "BIDEN", "ELECTION", "PRESIDENT"]):
                    market["category"] = "POLITICS"
                else:
                    market["category"] = "OTHER"

                filtered_markets.append(market)

            return filtered_markets
        except Exception as e:
            logger.error(f"Error exploring markets: {e}")
            return []

    async def get_spread(self, token_id):
        """
        Calculates the spread for a given token with robust method call.
        """
        try:
            orderbook = await asyncio.to_thread(self.polymarket.get_order_book, token_id)
            if hasattr(orderbook, 'bids') and hasattr(orderbook, 'asks'):
                if orderbook.bids and orderbook.asks:
                    best_bid = float(getattr(orderbook.bids[0], 'price', orderbook.bids[0].get('price', 0)))
                    best_ask = float(getattr(orderbook.asks[0], 'price', orderbook.asks[0].get('price', 0)))
                    if best_ask > 0:
                        return (best_ask - best_bid) / best_ask
            elif isinstance(orderbook, dict):
                bids = orderbook.get("bids", [])
                asks = orderbook.get("asks", [])
                if bids and asks:
                    best_bid = float(bids[0].get("price", 0))
                    best_ask = float(asks[0].get("price", 0))
                    if best_ask > 0:
                        return (best_ask - best_bid) / best_ask
            return 1.0 # High spread if no data
        except Exception as e:
            # Mute 404/Missing orderbook errors here as well
            error_msg = str(e)
            if "404" in error_msg or "not found" in error_msg.lower():
                logger.debug(f"No orderbook found for spread calculation of {token_id}")
            else:
                logger.warning(f"Error calculating spread for {token_id}: {e}")
            return 1.0
