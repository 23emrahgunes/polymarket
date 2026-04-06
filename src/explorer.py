import asyncio
import logging
import requests
import os
import time

logger = logging.getLogger(__name__)

class MarketExplorer:
    def __init__(self, polymarket_client):
        self.polymarket = polymarket_client
        self.gamma_api_base = "https://gamma-api.polymarket.com"

        # High-volume Fallback Token IDs (US Election, BTC, etc.)
        self.fallback_token_ids = [
            "21742416952778735398292850937877549041280327668630713028308365920042456453676", # Example
            "10000000000000000000000000000000000000000000000000000000000000000000000000001"  # Mock
        ]

    async def fetch_active_markets(self, limit=50):
        """
        Fetches active markets from Gamma API and extracts correct clobTokenIds.
        """
        try:
            # Use Gamma API for reliable active markets discovery
            url = f"{self.gamma_api_base}/markets?active=true&closed=false&limit={limit}"
            response = await asyncio.to_thread(requests.get, url, timeout=10)

            if response.status_code != 200:
                logger.error(f"Explorer: Gamma API error HTTP {response.status_code}")
                return self._get_fallback_markets()

            markets_data = response.json()
            if not isinstance(markets_data, list):
                return self._get_fallback_markets()

            discovered_markets = []
            for m in markets_data:
                # Extract clobTokenIds
                clob_token_ids = m.get("clobTokenIds")
                if not clob_token_ids: continue

                # Use the first token ID (typically YES)
                try:
                    import json
                    # clobTokenIds is usually a JSON string in some API responses or a list
                    if isinstance(clob_token_ids, str):
                        token_ids = json.loads(clob_token_ids)
                    else:
                        token_ids = clob_token_ids

                    if not token_ids: continue

                    market = {
                        "market_id": m.get("id"),
                        "question": m.get("question"),
                        "token_id": token_ids[0], # Primary token (YES)
                        "volume_24h": float(m.get("volume24h", 0)),
                        "active": True
                    }

                    # Categorization
                    q = market["question"].upper()
                    if any(sym in q for sym in ["BTC", "ETH", "SOL", "XRP", "DOGE", "BNB"]):
                        market["category"] = "CRYPTO"
                    elif any(kw in q for kw in ["TRUMP", "BIDEN", "ELECTION", "PRESIDENT"]):
                        market["category"] = "POLITICS"
                    elif any(kw in q for kw in ["NBA", "NFL", "SOCCER", "MATCH"]):
                        market["category"] = "SPORTS"
                    else:
                        market["category"] = "OTHER"

                    discovered_markets.append(market)
                except Exception as e:
                    logger.debug(f"Error parsing token IDs for market {m.get('id')}: {e}")
                    continue

            if not discovered_markets:
                return self._get_fallback_markets()

            # Sort by volume
            discovered_markets.sort(key=lambda x: x.get("volume_24h", 0), reverse=True)
            return discovered_markets[:limit]

        except Exception as e:
            logger.error(f"Explorer: Critical error in discovery - {e}")
            return self._get_fallback_markets()

    def _get_fallback_markets(self):
        """
        Safety net: returns hardcoded high-volume markets if API fails.
        """
        logger.warning("Explorer: Using hardcoded fallback markets.")
        return [
            {
                "market_id": "fallback_btc",
                "question": "Will BTC be above $70k?",
                "token_id": "21742416952778735398292850937877549041280327668630713028308365920042456453676",
                "category": "CRYPTO",
                "volume_24h": 100000,
                "active": True
            }
        ]

    async def get_spread(self, token_id):
        try:
            orderbook = await asyncio.to_thread(self.polymarket.get_order_book, token_id)
            if hasattr(orderbook, 'bids') and hasattr(orderbook, 'asks'):
                if orderbook.bids and orderbook.asks:
                    best_bid = float(getattr(orderbook.bids[0], 'price', orderbook.bids[0].get('price', 0)))
                    best_ask = float(getattr(orderbook.asks[0], 'price', orderbook.asks[0].get('price', 0)))
                    if best_ask > 0:
                        return (best_ask - best_bid) / best_ask
            return 1.0
        except Exception as e:
            logger.debug(f"No orderbook found for spread calculation of {token_id}")
            return 1.0
