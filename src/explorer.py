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

        # High-volume Fallback Token IDs
        self.fallback_token_ids = [
            "21742416952778735398292850937877549041280327668630713028308365920042456453676"
        ]

    async def fetch_active_markets(self, limit=200):
        """
        Fetches active markets from Gamma API with robust field mapping.
        """
        try:
            # Fetch all active, open markets
            url = f"{self.gamma_api_base}/markets?active=true&closed=false&limit={limit}"
            response = await asyncio.to_thread(requests.get, url, timeout=10)

            if response.status_code != 200:
                logger.error(f"Explorer: Gamma API error HTTP {response.status_code}")
                return self._get_fallback_markets()

            markets_data = response.json()
            if not isinstance(markets_data, list):
                logger.error("Explorer: Gamma API returned non-list data.")
                return self._get_fallback_markets()

            discovered_markets = []
            for m in markets_data:
                if not isinstance(m, dict): continue

                # Extract correct clobTokenIds (list of strings)
                raw_token_ids = m.get("clobTokenIds")
                if not raw_token_ids: continue

                try:
                    import json
                    if isinstance(raw_token_ids, str):
                        token_ids = json.loads(raw_token_ids)
                    else:
                        token_ids = raw_token_ids

                    if not token_ids or not isinstance(token_ids, list): continue

                    # Map fields robustly
                    # market_id -> conditionId is the most stable unique identifier
                    market_id = m.get("conditionId") or str(m.get("id"))

                    market = {
                        "market_id": market_id,
                        "question": m.get("question", "Unknown"),
                        "token_ids": token_ids, # All associated clobTokenIds
                        "token_id": token_ids[0], # YES clobTokenId
                        "volume_24h": float(m.get("volume24hr") or m.get("volume24h") or m.get("volume") or 0),
                        "active": True
                    }

                    # Categorization
                    q = market["question"].upper()
                    if any(sym in q for sym in ["BTC", "ETH", "SOL", "XRP", "DOGE", "BNB"]):
                        market["category"] = "CRYPTO"
                    elif any(kw in q for kw in ["TRUMP", "BIDEN", "ELECTION", "PRESIDENT"]):
                        market["category"] = "POLITICS"
                    elif any(kw in q for kw in ["NBA", "NFL", "SOCCER", "MATCH", "SCORE", "GOAL"]):
                        market["category"] = "SPORTS"
                    else:
                        market["category"] = "OTHER"

                    discovered_markets.append(market)
                except Exception as e:
                    logger.debug(f"Error parsing market data: {e}")
                    continue

            if not discovered_markets:
                return self._get_fallback_markets()

            # Sort by volume and return top candidates
            discovered_markets.sort(key=lambda x: x.get("volume_24h", 0), reverse=True)
            return discovered_markets[:limit]

        except Exception as e:
            logger.error(f"Explorer: Critical error in discovery - {e}")
            return self._get_fallback_markets()

    def _get_fallback_markets(self):
        """
        Safety net with high-volume tokens.
        """
        logger.warning("Explorer: Using hardcoded fallback markets.")
        return [
            {
                "market_id": "fallback_btc",
                "question": "Will BTC be above $70k?",
                "token_id": "21742416952778735398292850937877549041280327668630713028308365920042456453676",
                "token_ids": ["21742416952778735398292850937877549041280327668630713028308365920042456453676"],
                "category": "CRYPTO",
                "volume_24h": 1000000,
                "active": True
            }
        ]
