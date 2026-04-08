import asyncio
import json
import logging
import os
from typing import Dict, List

import requests

from src.decision_engine import classify_market_category


logger = logging.getLogger(__name__)


class MarketExplorer:
    def __init__(self, polymarket_client, debug_signal_mode: bool = False, debug_signal_profile: str = "sports"):
        self.polymarket = polymarket_client
        self.gamma_api_base = "https://gamma-api.polymarket.com"
        self.debug_signal_mode = debug_signal_mode
        self.debug_signal_profile = (debug_signal_profile or os.getenv("DEBUG_SIGNAL_PROFILE", "sports")).strip().lower()

    async def fetch_active_markets(self, limit: int = 200) -> List[Dict]:
        if self.debug_signal_mode:
            logger.info("Explorer: DEBUG_SIGNAL_MODE active. Using deterministic verification markets.")
            return self._get_debug_markets()

        try:
            url = f"{self.gamma_api_base}/markets?active=true&closed=false&limit={limit}"
            response = await asyncio.to_thread(requests.get, url, timeout=10)
            if response.status_code != 200:
                logger.error("Explorer: Gamma API returned HTTP %s", response.status_code)
                return []

            markets_data = response.json()
            if not isinstance(markets_data, list):
                logger.error("Explorer: Gamma API returned non-list data.")
                return []

            discovered_markets: List[Dict] = []
            for raw_market in markets_data:
                market = self._normalize_market(raw_market)
                if market is not None:
                    discovered_markets.append(market)

            discovered_markets.sort(key=lambda item: item.get("volume_24h", 0.0), reverse=True)
            return discovered_markets[:limit]
        except Exception as exc:
            logger.error("Explorer: failed to fetch active markets - %s", exc)
            return []

    def _normalize_market(self, raw_market: Dict) -> Dict | None:
        if not isinstance(raw_market, dict):
            return None

        raw_token_ids = raw_market.get("clobTokenIds") or raw_market.get("tokenIds")
        if not raw_token_ids:
            return None

        if isinstance(raw_token_ids, str):
            try:
                token_ids = json.loads(raw_token_ids)
            except json.JSONDecodeError:
                return None
        else:
            token_ids = raw_token_ids

        if not isinstance(token_ids, list) or not token_ids:
            return None

        question = raw_market.get("question", "Unknown market")
        return {
            "market_id": raw_market.get("conditionId") or str(raw_market.get("id") or ""),
            "question": question,
            "token_ids": token_ids,
            "token_id": token_ids[0],
            "volume_24h": float(raw_market.get("volume24hr") or raw_market.get("volume24h") or raw_market.get("volume") or 0.0),
            "active": bool(raw_market.get("active", True)),
            "category": classify_market_category(question),
        }

    def _get_debug_markets(self) -> List[Dict]:
        sports_market = {
            "market_id": "debug-sports-finals-2026",
            "question": "Will the Istanbul Lions win the 2026 championship match?",
            "token_id": "debug_sports_token_yes",
            "token_ids": ["debug_sports_token_yes", "debug_sports_token_no"],
            "category": "SPORTS",
            "volume_24h": 75_000.0,
            "active": True,
        }
        crypto_market = {
            "market_id": "debug-crypto-btc-100k-2026",
            "question": "Will BTC be above $100,000 on December 31, 2026?",
            "token_id": "debug_crypto_token_yes",
            "token_ids": ["debug_crypto_token_yes", "debug_crypto_token_no"],
            "category": "CRYPTO",
            "volume_24h": 250_000.0,
            "active": True,
        }
        crypto_long_market = {
            "market_id": "debug-crypto-btc-95k-2026",
            "question": "Will BTC be above $95,000 on December 31, 2026?",
            "token_id": "debug_crypto_long_token_yes",
            "token_ids": ["debug_crypto_long_token_yes", "debug_crypto_long_token_no"],
            "category": "CRYPTO",
            "volume_24h": 250_000.0,
            "active": True,
        }

        if self.debug_signal_profile == "crypto_dual":
            return [crypto_market]
        if self.debug_signal_profile == "crypto_triple_long":
            return [crypto_long_market]
        if self.debug_signal_profile == "all":
            return [sports_market, crypto_market]
        return [sports_market]
