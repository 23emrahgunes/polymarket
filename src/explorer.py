import asyncio
import logging
from py_clob_client.client import ClobClient
from py_clob_client.constants import POLYGON
import os

logger = logging.getLogger(__name__)

class MarketExplorer:
    def __init__(self, polymarket_client: ClobClient):
        self.polymarket = polymarket_client

    async def fetch_active_markets(self, limit=50):
        """
        Fetches the top X active, liquid markets from Polymarket CLOB.
        """
        try:
            # Fetch all markets
            raw_response = await asyncio.to_thread(self.polymarket.get_markets)

            markets = []
            if isinstance(raw_response, list):
                markets = raw_response
            elif isinstance(raw_response, dict):
                markets = raw_response.get("data", raw_response.get("markets", []))
            else:
                logger.debug(f"Unexpected response type: {type(raw_response)}")
                return []

            active_liquid_markets = []
            for market in markets:
                if not isinstance(market, dict): continue

                # Refined: Check for active status
                if market.get("closed") is True or market.get("active") is False:
                    continue

                # Ensure it has volume and tokens
                volume_24h = float(market.get("volume_24h", 0))
                tokens = market.get("tokens", [])
                if not tokens: continue

                # Add to candidates
                market["volume_24h_float"] = volume_24h
                active_liquid_markets.append(market)

            # Sort by 24h Volume and take top limit (e.g., 50)
            active_liquid_markets.sort(key=lambda x: x.get("volume_24h_float", 0), reverse=True)
            top_markets = active_liquid_markets[:limit]

            # Categorize only the top markets
            for market in top_markets:
                question = market.get("question", "").upper()
                if any(sym in question for sym in ["BTC", "ETH", "SOL", "XRP", "DOGE", "BNB"]):
                    market["category"] = "CRYPTO"
                elif any(kw in question for kw in ["TRUMP", "BIDEN", "ELECTION", "PRESIDENT"]):
                    market["category"] = "POLITICS"
                elif any(kw in question for kw in ["NBA", "NFL", "SOCCER", "TEAM", "MATCH", "SCORE", "GOAL"]):
                    market["category"] = "SPORTS"
                else:
                    market["category"] = "OTHER"

            logger.debug(f"Explorer: Found {len(top_markets)} active high-volume markets.")
            return top_markets
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
            return 1.0
        except Exception as e:
            error_msg = str(e)
            if "404" in error_msg or "not found" in error_msg.lower():
                logger.debug(f"No orderbook found for spread calculation of {token_id}")
            else:
                logger.warning(f"Error calculating spread for {token_id}: {e}")
            return 1.0
