from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Optional

import pandas as pd

from src.brain import Brain
from src.copy_trader import CopyTrader
from src.database import Database
from src.decision_engine import DecisionEngine, DecisionInputs, classify_market_category
from src.explorer import MarketExplorer
from src.logic import calculate_annualized_volatility, calculate_black_scholes_prob, calculate_edge, calculate_rsi
from src.market_config import EXCHANGE_MAPPINGS, resolve_crypto_symbol
from src.parser import parse_polymarket_question
from src.scanner import MarketScanner
from src.scrapers.activity import ActivityHunter
from src.trading import TradeExecutor
from src.whale_tracker import WhaleTracker


logger = logging.getLogger(__name__)


def _env_flag(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class RuntimeSettings:
    exchange_id: str = "coinbase"
    db_path: str = "data/ghost_trader.db"
    debug_signal_mode: bool = False
    runtime_verify_once: bool = False
    market_limit: int = 200
    discovery_interval_seconds: float = 5.0
    whale_interval_seconds: float = 15.0
    status_interval_seconds: float = 300.0
    market_scan_interval_seconds: float = 60.0

    @classmethod
    def from_env(cls) -> "RuntimeSettings":
        return cls(
            exchange_id=os.getenv("EXCHANGE_ID", "coinbase"),
            db_path=os.getenv("GHOST_TRADER_DB_PATH", "data/ghost_trader.db"),
            debug_signal_mode=_env_flag("DEBUG_SIGNAL_MODE", False),
            runtime_verify_once=_env_flag("RUNTIME_VERIFY_ONCE", False),
        )


class GhostBotRuntime:
    def __init__(self, settings: RuntimeSettings):
        self.settings = settings
        self.decision_engine = DecisionEngine()
        self.stop_event = asyncio.Event()
        self.trade_inserted_event = asyncio.Event()
        self.active_market_context: Dict[str, Dict] = {}
        self.token_to_market_id: Dict[str, str] = {}

        self.scanner: Optional[MarketScanner] = None
        self.db: Optional[Database] = None
        self.explorer: Optional[MarketExplorer] = None
        self.brain: Optional[Brain] = None
        self.trader: Optional[TradeExecutor] = None
        self.whale_tracker: Optional[WhaleTracker] = None
        self.activity_hunter: Optional[ActivityHunter] = None
        self.copy_trader: Optional[CopyTrader] = None

    async def initialize(self) -> None:
        self.scanner = MarketScanner(
            exchange_id=self.settings.exchange_id,
            debug_signal_mode=self.settings.debug_signal_mode,
        )
        self.db = Database(self.settings.db_path)
        await self.db.connect()
        self.explorer = MarketExplorer(
            self.scanner.polymarket,
            debug_signal_mode=self.settings.debug_signal_mode,
        )
        self.brain = Brain(decision_engine=self.decision_engine)
        self.trader = TradeExecutor(
            self.db,
            live_mode=False,
            trade_insert_callback=self.on_trade_inserted,
        )
        self.whale_tracker = WhaleTracker(
            self.scanner.polymarket,
            db=self.db,
            debug_signal_mode=self.settings.debug_signal_mode,
            poll_interval_seconds=self.settings.whale_interval_seconds,
        )
        self.activity_hunter = ActivityHunter(debug_signal_mode=self.settings.debug_signal_mode)
        self.copy_trader = CopyTrader(self.trader, self.scanner, self.db, self.decision_engine)

    async def close(self) -> None:
        self.stop_event.set()
        if self.scanner is not None:
            await self.scanner.close()
        if self.db is not None:
            await self.db.close()

    async def on_trade_inserted(self, trade_record: Dict) -> None:
        if self.settings.runtime_verify_once:
            self.trade_inserted_event.set()

    async def bootstrap_market_context(self) -> None:
        if self.explorer is None or self.scanner is None or self.copy_trader is None:
            return

        active_markets = await self.explorer.fetch_active_markets(limit=self.settings.market_limit)
        self._refresh_market_context(active_markets)
        symbols = {
            resolve_crypto_symbol(market.get("question", ""), self.settings.exchange_id)
            for market in active_markets
            if classify_market_category(market.get("question", "")) == "CRYPTO"
        }
        await self.scanner.update_monitored_symbols([symbol for symbol in symbols if symbol])

    async def run(self) -> None:
        await self.initialize()
        await self.bootstrap_market_context()

        tasks = [
            asyncio.create_task(self.run_discovery_loop(), name="discovery"),
            asyncio.create_task(self.run_whale_tracker_loop(), name="whale_tracker"),
            asyncio.create_task(self.run_activity_hunter_loop(), name="activity_hunter"),
        ]

        try:
            if self.settings.runtime_verify_once:
                verification_waiter = asyncio.create_task(self.trade_inserted_event.wait(), name="verify_waiter")
                done, pending = await asyncio.wait(
                    [*tasks, verification_waiter],
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in done:
                    if task is verification_waiter:
                        self.stop_event.set()
                        continue
                    exc = task.exception()
                    if exc is not None:
                        raise exc
                for pending_task in pending:
                    pending_task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
            else:
                await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await self.close()

    async def run_discovery_loop(self) -> None:
        if self.explorer is None or self.scanner is None or self.trader is None or self.db is None:
            return

        last_status_log = 0.0
        last_market_scan_log = 0.0

        while not self.stop_event.is_set():
            cycle_started = time.time()
            active_markets = await self.explorer.fetch_active_markets(limit=self.settings.market_limit)
            self._refresh_market_context(active_markets)

            if not active_markets:
                logger.info("[STATUS] No active Polymarket markets discovered in this cycle.")
            else:
                symbols = {
                    resolve_crypto_symbol(market.get("question", ""), self.settings.exchange_id)
                    for market in active_markets
                    if market.get("category") == "CRYPTO"
                }
                await self.scanner.update_monitored_symbols([symbol for symbol in symbols if symbol])
                for market in active_markets:
                    await self.process_market(market)
                    if self.stop_event.is_set():
                        break

            await self.trader.check_resolutions(self.scanner)

            now = time.time()
            if now - last_status_log >= self.settings.status_interval_seconds:
                total, wins, win_rate, total_pnl = await self.db.get_bot_performance()
                logger.info(
                    "[STATUS] active_markets=%s tracked_whales=%s total_trades=%s win_rate=%.1f total_pnl=%.2f scan_time=%.2fs",
                    len(active_markets),
                    len(self.whale_tracker.top_whales if self.whale_tracker else []),
                    total,
                    win_rate,
                    total_pnl,
                    now - cycle_started,
                )
                last_status_log = now

            if now - last_market_scan_log >= self.settings.market_scan_interval_seconds and active_markets:
                top_markets = sorted(active_markets, key=lambda item: item.get("volume_24h", 0.0), reverse=True)[:3]
                summary = ", ".join(
                    f"{market.get('category')}:{market.get('question', '')[:25]}(${market.get('volume_24h', 0.0):,.0f})"
                    for market in top_markets
                )
                logger.info("[MARKET-SCAN] active=%s top=%s", len(active_markets), summary)
                last_market_scan_log = now

            await self._sleep_or_stop(self.settings.discovery_interval_seconds)

    async def run_activity_hunter_loop(self) -> None:
        if self.activity_hunter is None:
            return

        logger.info("Ghost Intelligence v4.0: Activity Hunter active.")
        async for event in self.activity_hunter.monitor_stream():
            if self.stop_event.is_set():
                break
            await self.handle_activity_event(event)

    async def run_whale_tracker_loop(self) -> None:
        if self.whale_tracker is None:
            return

        logger.info("Ghost Intelligence v4.0: Whale Tracker active.")
        async for action in self.whale_tracker.monitor_whale_activity():
            if self.stop_event.is_set():
                break
            await self.handle_whale_action(action)

    async def process_market(self, market: Dict) -> None:
        if self.scanner is None or self.trader is None:
            return

        category = market.get("category", classify_market_category(market.get("question", "")))
        market_id = market.get("market_id", "")
        token_id = market.get("token_id")
        question = market.get("question", "")
        volume_24h = float(market.get("volume_24h", 0.0))

        inputs = DecisionInputs(
            source="discovery",
            category=category,
            market_id=market_id,
            token_id=token_id,
            question=question,
            volume_24h=volume_24h,
        )

        if not token_id:
            self.decision_engine.log_result(self.decision_engine.reject(inputs, "market_not_mapped"), logger)
            return

        if category != "CRYPTO":
            self.decision_engine.log_result(self.decision_engine.reject(inputs, "route_whale_orderflow_only"), logger)
            return

        snapshot = await self.scanner.get_orderbook_snapshot(token_id)
        if not snapshot["is_valid"]:
            self.decision_engine.log_result(
                self.decision_engine.reject(
                    DecisionInputs(**{**inputs.__dict__, "mid_price": snapshot.get("mid_price"), "spread_pct": snapshot.get("spread_pct")}),
                    snapshot.get("reason", "invalid_orderbook_data"),
                ),
                logger,
            )
            return

        symbol = resolve_crypto_symbol(question, self.settings.exchange_id)
        if not symbol:
            self.decision_engine.log_result(self.decision_engine.reject(inputs, "market_not_mapped"), logger)
            return

        current_exchange_price = self.scanner.current_prices.get(symbol)
        if current_exchange_price is None:
            current_exchange_price = await self.scanner.refresh_symbol_price(symbol)
        if current_exchange_price is None:
            self.decision_engine.log_result(
                self.decision_engine.reject(
                    DecisionInputs(**{**inputs.__dict__, "mid_price": snapshot["mid_price"], "spread_pct": snapshot["spread_pct"]}),
                    "missing_exchange_price",
                ),
                logger,
            )
            return

        historical_data = await self.scanner.get_historical_data(symbol)
        if historical_data.empty or "close" not in historical_data:
            self.decision_engine.log_result(
                self.decision_engine.reject(
                    DecisionInputs(**{**inputs.__dict__, "mid_price": snapshot["mid_price"], "spread_pct": snapshot["spread_pct"]}),
                    "missing_exchange_price",
                ),
                logger,
            )
            return

        strike_price, expiry_dt = parse_polymarket_question(question)
        if not strike_price:
            self.decision_engine.log_result(
                self.decision_engine.reject(
                    DecisionInputs(**{**inputs.__dict__, "mid_price": snapshot["mid_price"], "spread_pct": snapshot["spread_pct"]}),
                    "market_not_mapped",
                ),
                logger,
            )
            return

        now = datetime.now(timezone.utc)
        if expiry_dt is None or expiry_dt <= now:
            self.decision_engine.log_result(
                self.decision_engine.reject(
                    DecisionInputs(**{**inputs.__dict__, "mid_price": snapshot["mid_price"], "spread_pct": snapshot["spread_pct"]}),
                    "market_expired",
                ),
                logger,
            )
            return

        volatility = calculate_annualized_volatility(historical_data["close"])
        time_to_expiry_years = (expiry_dt - now).total_seconds() / (24 * 365 * 3600)
        implied_probability = calculate_black_scholes_prob(
            current_exchange_price,
            strike_price,
            time_to_expiry_years,
            volatility,
        )
        edge = calculate_edge(snapshot["mid_price"], implied_probability)
        rsi_series = calculate_rsi(historical_data["close"])
        rsi_value = rsi_series.iloc[-1] if not rsi_series.empty else None
        if pd.isna(rsi_value):
            rsi_value = None

        decision = self.decision_engine.score_discovery(
            DecisionInputs(
                source="discovery",
                category=category,
                market_id=market_id,
                token_id=token_id,
                question=question,
                volume_24h=volume_24h,
                mid_price=snapshot["mid_price"],
                spread_pct=snapshot["spread_pct"],
                edge=edge,
                rsi=float(rsi_value) if rsi_value is not None else None,
            )
        )
        self.decision_engine.log_result(decision, logger)

        if decision.should_trade:
            await self.trader.execute_trade(
                market_id,
                "YES",
                decision.trade_size,
                snapshot["mid_price"],
                edge=edge,
                confidence=decision.score,
                source="discovery",
                category=category,
            )

    async def handle_whale_action(self, action: Dict) -> None:
        if self.copy_trader is None:
            return

        await self.copy_trader.evaluate_signal(action)

    async def handle_activity_event(self, event: Dict) -> None:
        if self.copy_trader is None:
            return

        await self.copy_trader.evaluate_activity_event(event)

    def _refresh_market_context(self, active_markets: list[Dict]) -> None:
        self.active_market_context = {}
        self.token_to_market_id = {}

        for market in active_markets:
            market_id = market.get("market_id")
            if not market_id:
                continue
            normalized_market = dict(market)
            normalized_market["category"] = market.get("category", classify_market_category(market.get("question", "")))
            self.active_market_context[market_id] = normalized_market

            token_id = market.get("token_id")
            if token_id:
                self.token_to_market_id[token_id] = market_id
            for market_token_id in market.get("token_ids", []):
                self.token_to_market_id[market_token_id] = market_id

        if self.copy_trader is not None:
            self.copy_trader.update_market_contexts(self.active_market_context, self.token_to_market_id)

    async def _sleep_or_stop(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self.stop_event.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            return
