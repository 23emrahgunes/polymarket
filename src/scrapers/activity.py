import asyncio
import logging
import time
from collections import defaultdict
from typing import Dict, List

import requests


logger = logging.getLogger(__name__)


class ActivityHunter:
    def __init__(self, gamma_api_base="https://gamma-api.polymarket.com", debug_signal_mode: bool = False):
        self.gamma_api_base = gamma_api_base
        self.debug_signal_mode = debug_signal_mode
        self.activity_clusters = defaultdict(list)
        self.processed_transaction_ids = set()
        self.whale_event_threshold = 1_000.0
        self.cluster_time_window = 120
        self.debug_event_emitted = False

    async def fetch_latest_activity(self, limit=50):
        try:
            url = f"{self.gamma_api_base}/activity?limit={limit}"
            response = await asyncio.to_thread(requests.get, url, timeout=10)
            if response.status_code == 200:
                data = response.json()
                if isinstance(data, list):
                    return data
            return []
        except Exception as exc:
            logger.info("ActivityHunter: API communication error - %s", exc)
            return []

    async def monitor_stream(self):
        while True:
            try:
                if self.debug_signal_mode and not self.debug_event_emitted:
                    self.debug_event_emitted = True
                    await asyncio.sleep(1)
                    yield {
                        "type": "WHALE_EVENT",
                        "token_id": "debug_sports_token_yes",
                        "side": "BUY",
                        "amount": 2_500.0,
                        "wallet": "0xDEBUGSPORTS",
                        "price": 0.57,
                    }

                activities = await self.fetch_latest_activity()
                for event in self._normalize_activities(activities):
                    yield event
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.info("ActivityHunter: stream monitoring error - %s", exc)
                await asyncio.sleep(5)

    def _normalize_activities(self, activities: List[Dict]) -> List[Dict]:
        normalized_events: List[Dict] = []
        now = time.time()

        for activity in activities:
            if not isinstance(activity, dict):
                continue

            tx_id = activity.get("id") or activity.get("transaction_hash")
            if tx_id in self.processed_transaction_ids:
                continue
            self.processed_transaction_ids.add(tx_id)
            if len(self.processed_transaction_ids) > 2_000:
                self.processed_transaction_ids.clear()

            token_id = activity.get("market_id")
            if not token_id:
                continue

            side = str(activity.get("side", "BUY")).upper()
            wallet = activity.get("proxy_wallet") or activity.get("address")
            price = float(activity.get("price", 0.0) or 0.0)
            size = float(activity.get("size", 0.0) or 0.0)
            amount = size * price

            if wallet and amount >= self.whale_event_threshold:
                normalized_events.append(
                    {
                        "type": "WHALE_EVENT",
                        "token_id": token_id,
                        "side": side,
                        "amount": amount,
                        "wallet": wallet,
                        "price": price,
                    }
                )

            if wallet:
                cluster_key = (token_id, side)
                self.activity_clusters[cluster_key].append({"wallet": wallet, "timestamp": now})
                self.activity_clusters[cluster_key] = [
                    entry
                    for entry in self.activity_clusters[cluster_key]
                    if now - entry["timestamp"] < self.cluster_time_window
                ]
                unique_wallets = {entry["wallet"] for entry in self.activity_clusters[cluster_key]}
                if len(unique_wallets) >= 2:
                    normalized_events.append(
                        {
                            "type": "CLUSTER_DETECTED",
                            "token_id": token_id,
                            "side": side,
                            "wallets_count": len(unique_wallets),
                            "avg_price": price,
                            "amount": amount,
                        }
                    )

        return normalized_events
