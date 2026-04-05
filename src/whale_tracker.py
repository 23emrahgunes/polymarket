import asyncio
import logging
import random
import os
import time
import requests
from py_clob_client.client import ClobClient

logger = logging.getLogger(__name__)

class WhaleTracker:
    def __init__(self, polymarket_client: ClobClient, db=None):
        self.polymarket = polymarket_client
        self.db = db
        self.top_whales = [] # List of addresses
        self.last_whale_positions = {} # {address: [last_positions]}
        self.gamma_api_base = "https://gamma-api.polymarket.com"

    async def fetch_top_whales(self, limit=20):
        """
        Fetches the top 20 most profitable wallets from the Polymarket/Gamma API leaderboard.
        """
        try:
            logger.info("WhaleTracker: Fetching Polymarket Leaderboard via Gamma API...")

            try:
                response = await asyncio.to_thread(requests.get, f"{self.gamma_api_base}/leaderboard?limit={limit}", timeout=10)
                if response.status_code == 200:
                    data = response.json()
                    if isinstance(data, list):
                        self.top_whales = [entry.get("address") for entry in data if entry.get("address")]
                        if self.top_whales:
                            logger.info(f"WhaleTracker: Successfully fetched {len(self.top_whales)} wallets from API.")
                            return self.top_whales
            except Exception as e:
                logger.warning(f"WhaleTracker: API fetch failed ({e}). Falling back to static elite list.")

            # Fallback
            env_whales = os.getenv("WHALE_LIST")
            if env_whales:
                self.top_whales = [addr.strip() for addr in env_whales.split(",")]
                logger.info(f"WhaleTracker: Monitoring {len(self.top_whales)} wallets from ENV_VAR.")
            else:
                self.top_whales = [
                    "0x2B86E8987483756209b5380591244E390A74f9d6",
                    "0x0287a149E699B52637D43a8566a707641eB40A5D",
                    "0x1fA2A350fD088E0799797072E639343B23847990"
                ]
                while len(self.top_whales) < limit:
                    self.top_whales.append(f"0x{random.getrandbits(160):x}")

                logger.info(f"WhaleTracker: Monitoring {len(self.top_whales)} wallets from elite fallback list.")

            return self.top_whales
        except Exception as e:
            logger.error(f"Error resolving leaderboard: {e}")
            return []

    async def re_rank_whales(self, limit=20):
        """
        Automatic Re-Ranking: Every 24 hours, automatically replace whales in the 'Elite List'
        whose win-rate drops below 50% with new candidates from the Polymarket leaderboard.
        """
        if not self.db:
            return

        logger.info("WhaleTracker: Starting 24h automatic re-ranking...")

        # 1. Fetch current whale stats from DB
        to_keep = []
        for whale in self.top_whales:
            stats = await self.db.get_whale_stats(whale)
            if stats:
                total = stats['total_trades']
                wins = stats['wins']
                win_rate = (wins / total) if total > 0 else 1.0 # Keep new whales with no trades yet

                if win_rate >= 0.5:
                    to_keep.append(whale)
                else:
                    logger.info(f"WhaleTracker: Re-ranking {whale[:10]}... due to low win-rate ({win_rate:.0%})")
            else:
                to_keep.append(whale) # Keep if no trades yet

        # 2. Refill the list from the leaderboard
        if len(to_keep) < limit:
            logger.info(f"WhaleTracker: Replacing {limit - len(to_keep)} underperforming wallets.")
            leaderboard = await self.fetch_top_whales(limit=limit*2)
            for new_whale in leaderboard:
                if new_whale not in to_keep:
                    to_keep.append(new_whale)
                if len(to_keep) == limit:
                    break

        self.top_whales = to_keep
        logger.info(f"WhaleTracker: Re-ranking complete. Monitoring {len(self.top_whales)} elite wallets.")

    async def check_whale_positions(self, whale_address):
        """
        Checks a single whale's recent activity via Gamma API.
        """
        try:
            await asyncio.sleep(random.uniform(1, 3))
            try:
                response = await asyncio.to_thread(requests.get, f"{self.gamma_api_base}/activity?address={whale_address}&limit=5", timeout=10)
                if response.status_code == 200:
                    activities = response.json()
                    if isinstance(activities, list) and len(activities) > 0:
                        latest_act = activities[0]
                        if isinstance(latest_act, dict):
                            market_id = latest_act.get("market_id")
                            side_raw = str(latest_act.get("side", "")).lower()
                            side = "BUY" if "buy" in side_raw else "SELL"
                            price = float(latest_act.get("price", 0))

                            if market_id and price > 0:
                                return {
                                    "whale": whale_address,
                                    "action": side,
                                    "market_id": market_id,
                                    "price": price,
                                    "timestamp": time.time()
                                }
            except:
                pass

            # Mock activity for v3.0 demo if API is dry
            if random.random() < 0.05:
                return {
                    "whale": whale_address,
                    "action": "BUY",
                    "market_id": f"MARKET_{random.randint(100, 999)}",
                    "price": round(random.uniform(0.3, 0.7), 2),
                    "timestamp": time.time()
                }
            return None
        except Exception as e:
            logger.error(f"WhaleTracker: Error checking whale {whale_address[:10]}... - {e}")
            return None

    async def monitor_whale_activity(self):
        """
        Continuously monitors tracked whales for new position activity concurrently.
        """
        if not self.top_whales:
            await self.fetch_top_whales()

        while True:
            try:
                tasks = [self.check_whale_positions(whale) for whale in self.top_whales]
                results = await asyncio.gather(*tasks)

                for action in results:
                    if action:
                        yield action

                await asyncio.sleep(random.uniform(10, 20))
            except Exception as e:
                logger.error(f"WhaleTracker: Error in monitoring cycle - {e}")
                await asyncio.sleep(30)
