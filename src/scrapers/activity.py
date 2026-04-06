import asyncio
import logging
import time
import requests
from collections import defaultdict

logger = logging.getLogger(__name__)

class ActivityHunter:
    def __init__(self, gamma_api_base="https://gamma-api.polymarket.com"):
        self.gamma_api_base = gamma_api_base
        # Cluster tracking: {(market_id, side): [timestamp1, timestamp2, ...]}
        self.activity_clusters = defaultdict(list)
        self.processed_transaction_ids = set()
        self.whale_event_threshold = 1000.0 # TEMPORARY: $1000 (Force signal verification)
        self.cluster_time_window = 120 # 120 seconds
        self.cluster_min_wallets = 3

    async def fetch_latest_activity(self, limit=50):
        """
        Fetches the latest global activity from Polymarket Gamma API.
        """
        try:
            # Note: Gamma API endpoint for global activity
            url = f"{self.gamma_api_base}/activity?limit={limit}"
            response = await asyncio.to_thread(requests.get, url, timeout=10)
            if response.status_code == 200:
                return response.json()
            return []
        except Exception as e:
            logger.debug(f"ActivityHunter: Error fetching stream - {e}")
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
                    raw_amount = float(act.get("size", 0)) * float(act.get("price", 0))
                    raw_token_id = act.get("market_id")
                    logger.debug(f"Raw Trade Detected | Size: ${raw_amount:,.2f} | TokenID: {raw_token_id}")

                    # Prevent duplicate processing
                    tx_id = act.get("id") or act.get("transaction_hash")
                    if tx_id in self.processed_transaction_ids: continue
                    self.processed_transaction_ids.add(tx_id)
                    # Cleanup old IDs
                    if len(self.processed_transaction_ids) > 1000:
                        self.processed_transaction_ids.clear()

                    market_id = act.get("market_id")
                    side = str(act.get("side", "")).upper()
                    amount = float(act.get("size", 0)) * float(act.get("price", 0)) # Total USD
                    wallet = act.get("proxy_wallet") or act.get("address")

                    if not market_id or not wallet: continue

                    # 1. Whale Event Detection (>$2,500)
                    if amount >= self.whale_event_threshold:
                        yield {
                            "type": "WHALE_EVENT",
                            "market_id": market_id,
                            "side": side,
                            "amount": amount,
                            "wallet": wallet,
                            "price": float(act.get("price", 0))
                        }

                    # 2. Cluster Detection (3+ wallets in 120s)
                    key = (market_id, side)
                    now = time.time()
                    self.activity_clusters[key].append({"wallet": wallet, "timestamp": now})

                    # Cleanup old entries in cluster
                    self.activity_clusters[key] = [
                        entry for entry in self.activity_clusters[key]
                        if now - entry["timestamp"] < self.cluster_time_window
                    ]

                    # Check unique wallets in window
                    unique_wallets = {entry["wallet"] for entry in self.activity_clusters[key]}
                    if len(unique_wallets) >= self.cluster_min_wallets:
                        yield {
                            "type": "CLUSTER_DETECTED",
                            "market_id": market_id,
                            "side": side,
                            "wallets_count": len(unique_wallets),
                            "avg_price": float(act.get("price", 0))
                        }
                        # Clear cluster after trigger to prevent spamming
                        self.activity_clusters[key] = []

                await asyncio.sleep(5) # Real-time priority: 5s polling
            except Exception as e:
                logger.error(f"ActivityHunter: Stream monitoring error - {e}")
                await asyncio.sleep(10)
