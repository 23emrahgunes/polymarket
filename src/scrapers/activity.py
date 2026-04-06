import asyncio
import logging
import time
import requests
from collections import defaultdict

logger = logging.getLogger(__name__)

class ActivityHunter:
    def __init__(self, gamma_api_base="https://gamma-api.polymarket.com"):
        self.gamma_api_base = gamma_api_base
        self.activity_clusters = defaultdict(list)
        self.processed_transaction_ids = set()
        self.whale_event_threshold = 1000.0 # Force signal verification as requested
        self.cluster_time_window = 120
        self.cluster_min_wallets = 3

    async def fetch_latest_activity(self, limit=50):
        """
        Fetches the latest global activity from Gamma API.
        Handles errors gracefully without crashing the loop.
        """
        try:
            url = f"{self.gamma_api_base}/activity?limit={limit}"
            response = await asyncio.to_thread(requests.get, url, timeout=10)
            if response.status_code == 200:
                data = response.json()
                if isinstance(data, list):
                    return data
            elif response.status_code == 404:
                # Silently handle 404 on activity endpoint if it's intermittent
                return []
            return []
        except Exception as e:
            logger.debug(f"ActivityHunter: API communication error - {e}")
            return []

    async def monitor_stream(self):
        """
        Continuously listens to the activity stream for Cluster and Whale events.
        """
        while True:
            try:
                activities = await self.fetch_latest_activity()
                if not isinstance(activities, list):
                    await asyncio.sleep(5)
                    continue

                for act in activities:
                    if not isinstance(act, dict): continue

                    # Forced Debug: Log every detected transaction
                    # Gamma activity 'market_id' field is actually the outcome token_id
                    size = float(act.get("size", 0))
                    price = float(act.get("price", 0))
                    raw_amount = size * price
                    token_id = act.get("market_id")

                    if not token_id: continue
                    logger.debug(f"Raw Trade Detected | Size: ${raw_amount:,.2f} | TokenID: {token_id}")

                    # Prevent duplicate processing
                    tx_id = act.get("id") or act.get("transaction_hash")
                    if tx_id in self.processed_transaction_ids: continue
                    self.processed_transaction_ids.add(tx_id)
                    if len(self.processed_transaction_ids) > 2000:
                        self.processed_transaction_ids.clear()

                    side = str(act.get("side", "")).upper()
                    wallet = act.get("proxy_wallet") or act.get("address")

                    if not wallet: continue

                    # 1. Whale Event Detection (>$1,000)
                    if raw_amount >= self.whale_event_threshold:
                        yield {
                            "type": "WHALE_EVENT",
                            "market_id": token_id, # Return token_id to be resolved by main.py
                            "side": side,
                            "amount": raw_amount,
                            "wallet": wallet,
                            "price": price
                        }

                    # 2. Cluster Detection (3+ wallets in 120s)
                    key = (token_id, side)
                    now = time.time()
                    self.activity_clusters[key].append({"wallet": wallet, "timestamp": now})
                    self.activity_clusters[key] = [
                        entry for entry in self.activity_clusters[key]
                        if now - entry["timestamp"] < self.cluster_time_window
                    ]

                    unique_wallets = {entry["wallet"] for entry in self.activity_clusters[key]}
                    if len(unique_wallets) >= self.cluster_min_wallets:
                        yield {
                            "type": "CLUSTER_DETECTED",
                            "market_id": token_id,
                            "side": side,
                            "wallets_count": len(unique_wallets),
                            "avg_price": price
                        }
                        self.activity_clusters[key] = []

                await asyncio.sleep(5) # Real-time priority
            except Exception as e:
                logger.debug(f"ActivityHunter: Stream monitoring error - {e}")
                await asyncio.sleep(10)
