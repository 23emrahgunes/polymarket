from __future__ import annotations

import asyncio
import math
import os
import time
from typing import Dict, List, Optional

from py_clob_client.client import ClobClient

from src.gamma_client import GammaApiClient
from src.market_mapping import collect_alias_candidates


import logging


logger = logging.getLogger(__name__)


class WhaleTracker:
    def __init__(
        self,
        polymarket_client: ClobClient,
        db=None,
        gamma_client: GammaApiClient | None = None,
        debug_signal_mode: bool = False,
        poll_interval_seconds: float = 15.0,
        target_wallet_count: int = 50,
        discovery_min_events: int = 2,
        discovery_single_event_usd: float = 10_000.0,
    ):
        self.polymarket = polymarket_client
        self.db = db
        self.gamma_client = gamma_client or GammaApiClient()
        self.debug_signal_mode = debug_signal_mode
        self.poll_interval_seconds = poll_interval_seconds
        self.target_wallet_count = max(target_wallet_count, 1)
        self.discovery_min_events = discovery_min_events
        self.discovery_single_event_usd = discovery_single_event_usd
        self.top_whales: List[str] = []
        self.source_mode = "hybrid_cache"
        self.leaderboard_wallets_count = 0
        self.activity_discovered_wallets_count = 0
        self.graph_discovered_wallets_count = 0
        self.persisted_wallets_count = 0
        self.wallet_timeouts_last_cycle = 0
        self.last_leaderboard_refresh = 0.0
        self.selection_refresh_seconds = 300.0

    def _deterministic_fallback_wallets(self) -> List[str]:
        return [
            "0x2B86E8987483756209b5380591244E390A74f9d6",
            "0x0287a149E699B52637D43a8566a707641eB40A5D",
            "0x1fA2A350fD088E0799797072E639343B23847990",
            "0x8C5D8B9bA8f51365b6E2B0c66FfC6A4D0a5A6B10",
            "0x7E4A8a3B1D6cA59fF88F2d63D96bA7B47e6cA11f",
            "0x4cC4B4d3fC4Ff45138b72B6b7dA6fAe1932d19E1",
            "0x2c72A8fA4eB0976A7c7D6F1A57E1d563348a6Bf2",
            "0x95A4C8b3988eB3832Ebe4b0A2d6d1d0b7c11f8D3",
            "0xC1E6dB03bB15cA0b52d7a9D7f2A6D2A8b451Ec44",
            "0xD40f14b5d36D1b6aB1aA8A9a3d3e1e0c21A1B555",
            "0xEe3a1bA2B9D4dA4d83C2F1eC1f8B6c7D2e4a1666",
            "0x0A9d1D3f2b4C6e7A8d9F0c1E2a3B4d5E6f7A8777",
            "0x1B2c3D4e5F60718293a4B5c6D7e8F90123456888",
            "0x2233445566778899AaBbCcDdEeFf001122334499",
            "0x33445566778899AaBbCcDdEeFf001122334455AA",
            "0x445566778899AaBbCcDdEeFf00112233445566BB",
            "0x5566778899AaBbCcDdEeFf0011223344556677CC",
            "0x66778899AaBbCcDdEeFf001122334455667788DD",
            "0x778899AaBbCcDdEeFf00112233445566778899EE",
            "0x8899AaBbCcDdEeFf00112233445566778899AaFF",
        ]

    def _manual_seed_wallets(self) -> List[str]:
        env_whales = os.getenv("WHALE_LIST", "")
        return [address.strip() for address in env_whales.split(",") if address.strip()]

    async def fetch_top_whales(self, limit: int | None = None):
        limit = limit or self.target_wallet_count

        if self.debug_signal_mode:
            self.top_whales = self._deterministic_fallback_wallets()[: min(limit, 3)]
            self.source_mode = "debug_static_seed"
            self.leaderboard_wallets_count = 0
            self.activity_discovered_wallets_count = 0
            self.persisted_wallets_count = len(self.top_whales)
            logger.info("WhaleTracker: DEBUG_SIGNAL_MODE active. Using deterministic fallback whale list.")
            return self.top_whales

        if self.db is None:
            self.top_whales = self._manual_seed_wallets() or self._deterministic_fallback_wallets()[:limit]
            self.source_mode = "seed_only_mode"
            return self.top_whales

        await self.db.disable_stale_whale_wallets(stale_days=7)
        manual_wallets = self._manual_seed_wallets()
        static_wallets = self._deterministic_fallback_wallets()
        leaderboard_wallets = await self._refresh_leaderboard(limit=max(limit, self.target_wallet_count))

        for wallet in manual_wallets:
            await self.db.upsert_whale_wallet(wallet, "manual_seed")
        for wallet in static_wallets:
            await self.db.upsert_whale_wallet(wallet, "static_seed")

        activity_rows = await self.db.get_ranked_whale_wallets(
            limit=limit * 3,
            source_type="activity_discovery",
            min_event_count_24h=self.discovery_min_events,
            single_event_min_usd=self.discovery_single_event_usd,
        )
        graph_rows = await self.db.get_ranked_whale_wallets(
            limit=limit * 3,
            source_type="graph_discovery",
            min_event_count_24h=self.discovery_min_events,
            single_event_min_usd=self.discovery_single_event_usd,
        )
        persisted_rows = await self.db.get_ranked_whale_wallets(
            limit=limit * 4,
            min_event_count_24h=self.discovery_min_events,
            single_event_min_usd=self.discovery_single_event_usd,
        )

        await self._refresh_wallet_scores(activity_rows)
        await self._refresh_wallet_scores(graph_rows)
        await self._refresh_wallet_scores(persisted_rows)

        activity_wallets = [row["address"] for row in activity_rows]
        graph_wallets = [row["address"] for row in graph_rows]
        persisted_wallets = [row["address"] for row in persisted_rows]
        persisted_cache_wallets = [
            row["address"]
            for row in persisted_rows
            if str(row["source_type"]) in {"activity_discovery", "leaderboard", "graph_discovery"}
        ]
        counts = await self.db.get_whale_wallet_counts()

        selected: List[str] = []
        seen = set()

        def _extend(wallets: List[str], *, cap: int | None = None) -> None:
            nonlocal selected
            for wallet in wallets:
                if wallet in seen:
                    continue
                seen.add(wallet)
                selected.append(wallet)
                if len(selected) >= limit:
                    return
                if cap is not None and len(selected) >= cap:
                    return

        graph_quota = min(10, math.ceil(limit * 0.30))
        primary_activity_cap = max(limit - graph_quota, 0)

        _extend(activity_wallets, cap=primary_activity_cap if graph_wallets else None)
        _extend(graph_wallets, cap=min(limit, len(selected) + graph_quota))
        for group in [activity_wallets, leaderboard_wallets, persisted_cache_wallets, manual_wallets, static_wallets, persisted_wallets]:
            if len(selected) >= limit:
                break
            _extend(group)

        self.top_whales = selected[:limit]
        self.leaderboard_wallets_count = len(leaderboard_wallets)
        self.activity_discovered_wallets_count = counts["activity_discovered_wallets"]
        self.graph_discovered_wallets_count = counts["graph_discovered_wallets"]
        self.persisted_wallets_count = counts["persisted_wallets"]
        self.source_mode = self._resolve_source_mode(
            leaderboard_wallets,
            activity_wallets,
            graph_wallets,
            persisted_cache_wallets,
            manual_wallets,
            static_wallets,
            self.top_whales,
        )

        if not self.top_whales:
            logger.info(
                "[REJECT] source=whale_tracker category=UNKNOWN market=leaderboard reasons=whale_source_unavailable inputs=%s",
                {"detail": "cache_empty", "source_mode": self.source_mode},
            )
        elif self.source_mode == "seed_only_mode":
            logger.info("WhaleTracker: source_mode=seed_only_mode using manual/static seeds until stronger wallet sources recover.")
        else:
            logger.info(
                "WhaleTracker: source_mode=%s selected=%s leaderboard_wallets=%s activity_discovered_wallets=%s graph_discovered_wallets=%s persisted_wallets=%s",
                self.source_mode,
                len(self.top_whales),
                self.leaderboard_wallets_count,
                self.activity_discovered_wallets_count,
                self.graph_discovered_wallets_count,
                self.persisted_wallets_count,
            )
        return self.top_whales

    async def re_rank_whales(self, limit: int = 20):
        await self.fetch_top_whales(limit=limit)

    async def check_whale_positions(self, whale_address: str) -> Optional[dict]:
        result = await self.gamma_client.fetch_wallet_activity(whale_address, limit=5)
        if not result.ok:
            if self.db is not None:
                await self.db.record_whale_wallet_failure(whale_address)
            if result.timed_out:
                self.wallet_timeouts_last_cycle += 1
            return None

        if self.db is not None:
            await self.db.record_whale_wallet_success(whale_address)

        activities = result.data
        if not isinstance(activities, list) or not activities:
            return None

        latest_activity = next(
            (
                activity
                for activity in activities
                if isinstance(activity, dict) and str(activity.get("type", "TRADE")).upper() == "TRADE"
            ),
            activities[0],
        )
        if not isinstance(latest_activity, dict):
            return None

        side_raw = str(latest_activity.get("side", "")).lower()
        side = "BUY" if "buy" in side_raw else "SELL"
        price = float(latest_activity.get("price", 0.0) or 0.0)
        size = float(latest_activity.get("size", 0.0) or 0.0)
        event_amount = float(latest_activity.get("usdcSize", 0.0) or 0.0)
        if event_amount <= 0:
            event_amount = size * price
        market_id = latest_activity.get("conditionId") or latest_activity.get("condition_id")
        token_id = latest_activity.get("asset") or latest_activity.get("market_id")
        alias_candidates = collect_alias_candidates(
            market_id,
            token_id,
            extra=[
                latest_activity.get("asset"),
                latest_activity.get("conditionId"),
                latest_activity.get("condition_id"),
                latest_activity.get("market_id"),
            ],
        )

        if (market_id or token_id) and price > 0:
            return {
                "whale": whale_address,
                "action": side,
                "market_id": market_id,
                "token_id": token_id,
                "price": price,
                "amount": event_amount,
                "alias_candidates": alias_candidates,
                "timestamp": time.time(),
            }
        return None

    async def monitor_whale_activity(self):
        await self.fetch_top_whales(limit=self.target_wallet_count)

        while True:
            try:
                results = await self.poll_whales_once()
                for action in results:
                    if action:
                        yield action
                await asyncio.sleep(self.poll_interval_seconds)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("WhaleTracker: monitoring cycle failed - %s", exc)
                await asyncio.sleep(self.poll_interval_seconds)

    async def poll_whales_once(self) -> List[dict]:
        if not self.top_whales or (time.time() - self.last_leaderboard_refresh) >= self.selection_refresh_seconds:
            await self.fetch_top_whales(limit=self.target_wallet_count)

        if not self.top_whales:
            logger.info(
                "[REJECT] source=whale_tracker category=UNKNOWN market=leaderboard reasons=whale_source_unavailable inputs=%s",
                {"detail": "cache_empty", "source_mode": self.source_mode},
            )
            return []

        self.wallet_timeouts_last_cycle = 0
        tasks = [self.check_whale_positions(whale) for whale in self.top_whales]
        results = await asyncio.gather(*tasks)
        if self.wallet_timeouts_last_cycle:
            logger.info(
                "WhaleTracker: wallet_timeouts_last_cycle=%s tracked_whales=%s source_mode=%s",
                self.wallet_timeouts_last_cycle,
                len(self.top_whales),
                self.source_mode,
            )
        return [action for action in results if action]

    async def _refresh_leaderboard(self, limit: int) -> List[str]:
        result = await self.gamma_client.fetch_leaderboard(limit=limit)
        if not result.ok:
            logger.info("WhaleTracker: leaderboard unavailable. Continuing with hybrid cache.")
            self.last_leaderboard_refresh = time.time()
            self.leaderboard_wallets_count = 0
            return []

        wallets = [
            entry.get("address") or entry.get("proxyWallet") or entry.get("proxy_wallet")
            for entry in result.data
            if isinstance(entry, dict) and (entry.get("address") or entry.get("proxyWallet") or entry.get("proxy_wallet"))
        ]
        for wallet in wallets:
            if self.db is not None:
                await self.db.upsert_whale_wallet(wallet, "leaderboard")
        self.last_leaderboard_refresh = time.time()
        return wallets[:limit]

    @staticmethod
    def _resolve_source_mode(
        leaderboard_wallets: List[str],
        activity_wallets: List[str],
        graph_wallets: List[str],
        persisted_cache_wallets: List[str],
        manual_wallets: List[str],
        static_wallets: List[str],
        selected_wallets: List[str],
    ) -> str:
        if activity_wallets and (leaderboard_wallets or graph_wallets):
            return "hybrid_cache"
        if activity_wallets:
            return "cache_only"
        if graph_wallets and leaderboard_wallets:
            return "graph_plus_leaderboard"
        if graph_wallets:
            return "graph_discovery"
        if leaderboard_wallets:
            return "leaderboard_live"
        if persisted_cache_wallets:
            return "persisted_cache"
        if selected_wallets and (manual_wallets or static_wallets):
            return "seed_only_mode"
        return "cache_empty"

    async def _refresh_wallet_scores(self, rows: List[Dict]) -> None:
        if self.db is None:
            return
        for row in rows:
            score = self.db._rank_whale_wallet_row(row)
            await self.db.update_whale_wallet_score(str(row["address"]), score)
