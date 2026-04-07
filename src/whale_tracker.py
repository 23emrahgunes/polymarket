import asyncio
import logging
import os
import time
from typing import List, Optional

import requests
from py_clob_client.client import ClobClient


logger = logging.getLogger(__name__)


class WhaleTracker:
    def __init__(
        self,
        polymarket_client: ClobClient,
        db=None,
        debug_signal_mode: bool = False,
        poll_interval_seconds: float = 15.0,
    ):
        self.polymarket = polymarket_client
        self.db = db
        self.debug_signal_mode = debug_signal_mode
        self.poll_interval_seconds = poll_interval_seconds
        self.top_whales: List[str] = []
        self.gamma_api_base = "https://gamma-api.polymarket.com"

    def _deterministic_fallback_wallets(self) -> List[str]:
        return [
            "0x2B86E8987483756209b5380591244E390A74f9d6",
            "0x0287a149E699B52637D43a8566a707641eB40A5D",
            "0x1fA2A350fD088E0799797072E639343B23847990",
        ]

    async def fetch_top_whales(self, limit: int = 20):
        if self.debug_signal_mode:
            self.top_whales = self._deterministic_fallback_wallets()[:limit]
            logger.info("WhaleTracker: DEBUG_SIGNAL_MODE active. Using deterministic fallback whale list.")
            return self.top_whales

        try:
            logger.info("WhaleTracker: Fetching Polymarket leaderboard via Gamma API...")
            response = await asyncio.to_thread(
                requests.get,
                f"{self.gamma_api_base}/leaderboard?limit={limit}",
                timeout=10,
            )
            if response.status_code == 200:
                data = response.json()
                if isinstance(data, list):
                    self.top_whales = [entry.get("address") for entry in data if entry.get("address")]
                    if self.top_whales:
                        logger.info("WhaleTracker: loaded %s whale wallets from API.", len(self.top_whales))
                        return self.top_whales
            logger.info("WhaleTracker: API unavailable. Falling back to deterministic whale list.")
        except Exception as exc:
            logger.info("WhaleTracker: API fetch failed. Falling back to deterministic whale list. error=%s", exc)

        env_whales = os.getenv("WHALE_LIST")
        if env_whales:
            self.top_whales = [address.strip() for address in env_whales.split(",") if address.strip()]
            logger.info("WhaleTracker: monitoring %s wallets from WHALE_LIST.", len(self.top_whales))
        else:
            self.top_whales = self._deterministic_fallback_wallets()[:limit]
            logger.info("WhaleTracker: monitoring %s wallets from deterministic fallback list.", len(self.top_whales))
        return self.top_whales

    async def re_rank_whales(self, limit: int = 20):
        if not self.db:
            return

        logger.info("WhaleTracker: starting deterministic re-ranking.")
        retained_whales: List[str] = []
        for whale in self.top_whales:
            stats = await self.db.get_whale_stats(whale)
            if not stats:
                retained_whales.append(whale)
                continue

            total = stats["total_trades"]
            wins = stats["wins"]
            win_rate = (wins / total) if total else 1.0
            if win_rate >= 0.5:
                retained_whales.append(whale)
            else:
                logger.info("WhaleTracker: removing %s due to trust_score %.2f", whale[:10], win_rate)

        if len(retained_whales) < limit:
            leaderboard = await self.fetch_top_whales(limit=limit * 2)
            for whale in leaderboard:
                if whale not in retained_whales:
                    retained_whales.append(whale)
                if len(retained_whales) >= limit:
                    break

        self.top_whales = retained_whales[:limit]
        logger.info("WhaleTracker: re-ranking complete. monitoring %s whales.", len(self.top_whales))

    async def check_whale_positions(self, whale_address: str) -> Optional[dict]:
        try:
            response = await asyncio.to_thread(
                requests.get,
                f"{self.gamma_api_base}/activity?address={whale_address}&limit=5",
                timeout=10,
            )
            if response.status_code != 200:
                return None

            activities = response.json()
            if not isinstance(activities, list) or not activities:
                return None

            latest_activity = activities[0]
            if not isinstance(latest_activity, dict):
                return None

            side_raw = str(latest_activity.get("side", "")).lower()
            side = "BUY" if "buy" in side_raw else "SELL"
            price = float(latest_activity.get("price", 0.0) or 0.0)
            size = float(latest_activity.get("size", 0.0) or 0.0)
            event_amount = size * price
            market_id = latest_activity.get("conditionId") or latest_activity.get("condition_id")
            token_id = latest_activity.get("market_id")

            if (market_id or token_id) and price > 0:
                return {
                    "whale": whale_address,
                    "action": side,
                    "market_id": market_id,
                    "token_id": token_id,
                    "price": price,
                    "amount": event_amount,
                    "timestamp": time.time(),
                }
            return None
        except Exception as exc:
            logger.info("WhaleTracker: could not inspect whale %s - %s", whale_address[:10], exc)
            return None

    async def monitor_whale_activity(self):
        if not self.top_whales:
            await self.fetch_top_whales()

        while True:
            try:
                if not self.top_whales:
                    logger.info("[REJECT] source=whale_tracker category=UNKNOWN market=leaderboard reasons=whale_source_unavailable inputs=%s", {"detail": "no_top_whales"})
                    await asyncio.sleep(self.poll_interval_seconds)
                    continue

                tasks = [self.check_whale_positions(whale) for whale in self.top_whales]
                results = await asyncio.gather(*tasks)
                for action in results:
                    if action:
                        yield action
                await asyncio.sleep(self.poll_interval_seconds)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("WhaleTracker: monitoring cycle failed - %s", exc)
                await asyncio.sleep(self.poll_interval_seconds)
