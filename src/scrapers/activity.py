import asyncio
import logging
import os
import time
from collections import defaultdict
from typing import Dict, List, Optional

from src.decision_engine import classify_market_category
from src.gamma_client import GammaApiClient
from src.market_mapping import collect_alias_candidates


logger = logging.getLogger(__name__)


GRAPH_DISCOVERY_MIN_EVENT_USD = 1_500.0
GRAPH_DISCOVERY_MIN_TOTAL_NOTIONAL = 3_000.0


class ActivityHunter:
    def __init__(
        self,
        gamma_api_base="https://gamma-api.polymarket.com",
        debug_signal_mode: bool = False,
        debug_signal_profile: str = "sports",
        db=None,
        gamma_client: GammaApiClient | None = None,
        discovery_min_event_usd: float = 2_500.0,
        discovery_min_events: int = 2,
        discovery_single_event_usd: float = 10_000.0,
    ):
        self.gamma_api_base = gamma_api_base
        self.debug_signal_mode = debug_signal_mode
        self.debug_signal_profile = (debug_signal_profile or os.getenv("DEBUG_SIGNAL_PROFILE", "sports")).strip().lower()
        self.db = db
        self.gamma_client = gamma_client or GammaApiClient(gamma_api_base=gamma_api_base)
        self.activity_clusters = defaultdict(list)
        self.processed_transaction_ids = set()
        self.whale_event_threshold = 1_000.0
        self.cluster_time_window = 120
        self.debug_event_emitted = False
        self.discovery_min_event_usd = discovery_min_event_usd
        self.discovery_min_events = discovery_min_events
        self.discovery_single_event_usd = discovery_single_event_usd
        self.graph_discovery_min_event_usd = GRAPH_DISCOVERY_MIN_EVENT_USD
        self.graph_discovery_min_total_notional = GRAPH_DISCOVERY_MIN_TOTAL_NOTIONAL
        self._last_api_error_reason: Optional[str] = None
        self._last_api_error_ts = 0.0

    async def fetch_latest_activity(self, limit=50):
        result = await self.gamma_client.fetch_global_activity(limit=limit)
        if result.ok:
            return result.data
        self._log_fetch_error(result.reason)
        return []

    async def monitor_stream(self):
        while True:
            try:
                if self.debug_signal_mode and not self.debug_event_emitted:
                    self.debug_event_emitted = True
                    await asyncio.sleep(1)
                    yield self._get_debug_event()

                activities = await self.fetch_latest_activity()
                await self.record_discovery_candidates(activities)
                for event in self._normalize_activities(activities):
                    yield event
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.info("ActivityHunter: stream monitoring error - %s", exc)
                await asyncio.sleep(5)

    def _get_debug_event(self) -> Dict:
        if self.debug_signal_profile == "crypto_dual":
            return {
                "type": "WHALE_EVENT",
                "token_id": "debug_crypto_token_yes",
                "side": "BUY",
                "amount": 5_000.0,
                "price": 0.45,
                "source": "activity",
            }

        return {
            "type": "WHALE_EVENT",
            "token_id": "debug_sports_token_yes",
            "side": "BUY",
            "amount": 2_500.0,
            "wallet": "0xDEBUGSPORTS",
            "price": 0.57,
            "source": "activity",
        }

    def _normalize_activities(self, activities: List[Dict]) -> List[Dict]:
        normalized_events: List[Dict] = []
        now = time.time()

        for activity in activities:
            if not isinstance(activity, dict):
                continue

            tx_id = activity.get("id") or activity.get("transaction_hash")
            tx_id = tx_id or activity.get("transactionHash")
            if tx_id in self.processed_transaction_ids:
                continue
            self.processed_transaction_ids.add(tx_id)
            if len(self.processed_transaction_ids) > 2_000:
                self.processed_transaction_ids.clear()

            market_id = activity.get("conditionId") or activity.get("condition_id") or activity.get("market_id")
            token_id = activity.get("asset") or activity.get("token_id") or activity.get("market_id")
            if not market_id and not token_id:
                continue
            alias_candidates = collect_alias_candidates(
                market_id,
                token_id,
                extra=[
                    activity.get("asset"),
                    activity.get("conditionId"),
                    activity.get("condition_id"),
                    activity.get("market_id"),
                ],
            )

            side = str(activity.get("side", "BUY")).upper()
            wallet = activity.get("proxyWallet") or activity.get("proxy_wallet") or activity.get("address")
            price = float(activity.get("price", 0.0) or 0.0)
            size = float(activity.get("size", 0.0) or 0.0)
            amount = float(activity.get("usdcSize", 0.0) or 0.0)
            if amount <= 0:
                amount = size * price

            if wallet and amount >= self.whale_event_threshold:
                normalized_events.append(
                    {
                        "type": "WHALE_EVENT",
                        "market_id": market_id,
                        "token_id": token_id or market_id,
                        "side": side,
                        "amount": amount,
                        "wallet": wallet,
                        "price": price,
                        "alias_candidates": alias_candidates,
                    }
                )

            if wallet:
                cluster_key = (market_id or token_id, side)
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
                            "market_id": market_id,
                            "token_id": token_id or market_id,
                            "side": side,
                            "wallets_count": len(unique_wallets),
                            "avg_price": price,
                            "amount": amount,
                            "alias_candidates": alias_candidates,
                        }
                    )

        return normalized_events

    async def record_discovery_candidates(self, activities: List[Dict]) -> None:
        if self.db is None:
            return

        grouped_wallets: dict[tuple[str, str], dict] = {}
        for activity in activities:
            if not isinstance(activity, dict):
                continue
            wallet = activity.get("proxyWallet") or activity.get("proxy_wallet") or activity.get("address")
            if not wallet:
                continue

            price = float(activity.get("price", 0.0) or 0.0)
            size = float(activity.get("size", 0.0) or 0.0)
            amount = float(activity.get("usdcSize", 0.0) or 0.0)
            if amount <= 0:
                amount = size * price
            question = str(activity.get("question") or activity.get("title") or "")
            category = classify_market_category(question) if question else "UNKNOWN"
            if amount >= self.discovery_min_event_usd:
                await self.db.upsert_whale_wallet(
                    wallet,
                    "activity_discovery",
                    event_amount=amount,
                    event_category=category,
                )

            market_ref = activity.get("conditionId") or activity.get("condition_id") or activity.get("market_id") or activity.get("asset")
            side = str(activity.get("side", "BUY")).upper()
            if amount < self.graph_discovery_min_event_usd or not market_ref:
                continue
            group_key = (str(market_ref), side)
            group = grouped_wallets.setdefault(
                group_key,
                {"wallets": {}, "total_amount": 0.0, "category": category},
            )
            group["wallets"][str(wallet).lower()] = max(
                float(group["wallets"].get(str(wallet).lower(), 0.0) or 0.0),
                amount,
            )
            group["total_amount"] += amount
            if group["category"] == "UNKNOWN" and category != "UNKNOWN":
                group["category"] = category

        for (market_ref, side), group in grouped_wallets.items():
            wallets = list(group["wallets"].keys())
            if len(wallets) < 2:
                continue
            if float(group["total_amount"] or 0.0) < self.graph_discovery_min_total_notional:
                continue
            await self.db.upsert_whale_wallet_graph_cluster(
                wallets,
                market_ref=market_ref,
                side=side,
                total_notional=float(group["total_amount"] or 0.0),
                event_category=group["category"],
            )

    def _log_fetch_error(self, reason: Optional[str]) -> None:
        reason = reason or "activity_feed_unavailable"
        now = time.time()
        if reason == self._last_api_error_reason and (now - self._last_api_error_ts) < 60:
            return
        self._last_api_error_reason = reason
        self._last_api_error_ts = now
        logger.info("ActivityHunter: API communication error - %s", reason)
