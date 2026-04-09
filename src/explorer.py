import asyncio
import json
import logging
import os
from typing import Dict, List

import requests

from src.decision_engine import classify_market_category
from src.market_mapping import build_market_aliases


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
            discovered_by_id: Dict[str, Dict] = {}
            page_size = max(min(limit, 500), 1)
            offset = 0

            while len(discovered_by_id) < limit:
                request_limit = min(page_size, max(limit - len(discovered_by_id), 1))
                url = f"{self.gamma_api_base}/markets?active=true&closed=false&limit={request_limit}&offset={offset}"
                response = await asyncio.to_thread(requests.get, url, timeout=10)
                if response.status_code != 200:
                    logger.error("Explorer: Gamma API returned HTTP %s", response.status_code)
                    break

                markets_data = response.json()
                if not isinstance(markets_data, list):
                    logger.error("Explorer: Gamma API returned non-list data.")
                    break
                if not markets_data:
                    break

                for raw_market in markets_data:
                    market = self._normalize_market(raw_market)
                    if market is None:
                        continue
                    discovered_by_id[market["market_id"]] = market

                if len(markets_data) < request_limit:
                    break
                offset += request_limit

            discovered_markets = sorted(
                discovered_by_id.values(),
                key=lambda item: item.get("volume_24h", 0.0),
                reverse=True,
            )
            return discovered_markets[:limit]
        except Exception as exc:
            logger.error("Explorer: failed to fetch active markets - %s", exc)
            return []

    async def find_market_by_alias(self, aliases: List[str], limit: int = 1000) -> Dict | None:
        if not aliases:
            return None

        active_markets = await self.fetch_active_markets(limit=limit)
        return self._match_market_by_alias(active_markets, aliases)

    @staticmethod
    def _match_market_by_alias(markets: List[Dict], aliases: List[str]) -> Dict | None:
        alias_set = {alias for alias in aliases if alias}
        if not alias_set:
            return None
        for market in markets:
            market_aliases = set(market.get("alias_candidates", []))
            if market_aliases.intersection(alias_set):
                return market
        return None

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

        token_ids = [str(token_id).strip() for token_id in token_ids if str(token_id).strip()]
        if not token_ids:
            return None

        question = raw_market.get("question", "Unknown market")
        market_id = raw_market.get("conditionId") or str(raw_market.get("id") or "")
        market_id = str(market_id).strip()
        alias_candidates = build_market_aliases(
            market_id=market_id,
            token_id=token_ids[0],
            token_ids=token_ids,
            extra_aliases=[raw_market.get("conditionId"), raw_market.get("id"), raw_market.get("slug")],
        )
        return {
            "market_id": market_id,
            "question": question,
            "token_ids": token_ids,
            "token_id": token_ids[0],
            "volume_24h": float(raw_market.get("volume24hr") or raw_market.get("volume24h") or raw_market.get("volume") or 0.0),
            "active": bool(raw_market.get("active", True)),
            "category": classify_market_category(question),
            "alias_candidates": alias_candidates,
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
            "alias_candidates": build_market_aliases("debug-sports-finals-2026", "debug_sports_token_yes", ["debug_sports_token_yes", "debug_sports_token_no"]),
        }
        crypto_market = {
            "market_id": "debug-crypto-btc-100k-2026",
            "question": "Will BTC be above $100,000 on December 31, 2026?",
            "token_id": "debug_crypto_token_yes",
            "token_ids": ["debug_crypto_token_yes", "debug_crypto_token_no"],
            "category": "CRYPTO",
            "volume_24h": 250_000.0,
            "active": True,
            "alias_candidates": build_market_aliases("debug-crypto-btc-100k-2026", "debug_crypto_token_yes", ["debug_crypto_token_yes", "debug_crypto_token_no"]),
        }
        crypto_long_market = {
            "market_id": "debug-crypto-btc-95k-2026",
            "question": "Will BTC be above $95,000 on December 31, 2026?",
            "token_id": "debug_crypto_long_token_yes",
            "token_ids": ["debug_crypto_long_token_yes", "debug_crypto_long_token_no"],
            "category": "CRYPTO",
            "volume_24h": 250_000.0,
            "active": True,
            "alias_candidates": build_market_aliases("debug-crypto-btc-95k-2026", "debug_crypto_long_token_yes", ["debug_crypto_long_token_yes", "debug_crypto_long_token_no"]),
        }

        if self.debug_signal_profile == "crypto_dual":
            return [crypto_market]
        if self.debug_signal_profile == "crypto_triple_long":
            return [crypto_long_market]
        if self.debug_signal_profile == "all":
            return [sports_market, crypto_market]
        return [sports_market]
