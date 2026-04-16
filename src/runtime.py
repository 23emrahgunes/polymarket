from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Tuple

import pandas as pd

from src.brain import Brain
from src.copy_trader import CopyTrader
from src.crypto_signal_engine import CryptoSignalEngine, CryptoSignalInputs
from src.database import Database
from src.decision_engine import DecisionEngine, DecisionInputs, DecisionResult, classify_market_category
from src.explorer import MarketExplorer
from src.evaluation_utils import infer_sample_kind, normalize_signal_family, slippage_proxy_bps_from_spread
from src.evaluation_utils import STRATEGY_PROFILE_BINANCE_TECHNICAL, STRATEGY_PROFILE_SAMPLING_RELAXED
from src.gamma_client import GammaApiClient
from src.logic import calculate_annualized_volatility, calculate_black_scholes_prob, calculate_edge, calculate_rsi
from src.market_config import BINANCE_FUTURES_MAPPINGS, EXCHANGE_MAPPINGS, resolve_binance_futures_symbol, resolve_crypto_symbol
from src.market_mapping import build_market_aliases, collect_alias_candidates, event_is_meaningful_for_lazy_lookup, normalize_market_alias
from src.parser import parse_polymarket_question
from src.scanner import MarketScanner
from src.scrapers.activity import ActivityHunter
from src.technical_signal_engine import TechnicalSignalEngine
from src.trading import TradeExecutor
from src.venue_config import VenueConfig, build_default_venue_configs
from src.venues import BinanceFuturesPaperVenue, BinanceSpotVenue, PolymarketVenue
from src.whale_tracker import WhaleTracker


logger = logging.getLogger(__name__)

DEFAULT_BINANCE_TECHNICAL_SYMBOLS: Tuple[str, ...] = ("BTC", "ETH", "SOL")


def _env_flag(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _env_csv(name: str) -> Tuple[str, ...]:
    raw = os.getenv(name, "")
    if not raw.strip():
        return tuple()
    return tuple(part.strip() for part in raw.split(",") if part.strip())


@dataclass(frozen=True)
class RuntimeSettings:
    exchange_id: str = "coinbase"
    db_path: str = "data/ghost_trader.db"
    debug_signal_mode: bool = False
    debug_signal_profile: str = "sports"
    runtime_verify_once: bool = False
    verify_required_venues: Tuple[str, ...] = tuple()
    verify_required_category: Optional[str] = None
    market_limit: int = 200
    market_lookup_limit: int = 15000
    paper_sampling_mode: bool = False
    paper_sampling_target_closed_trades: int = 20
    binance_technical_paper_enabled: bool = False
    binance_technical_force_sample: bool = False
    binance_technical_symbols: Tuple[str, ...] = tuple()
    binance_technical_timeframe: str = "5m"
    binance_technical_min_score: float = 0.62
    binance_technical_force_min_score: float = 0.5
    binance_technical_force_cooldown_minutes: float = 30.0
    binance_technical_interval_seconds: float = 60.0
    orderflow_hot_window_enabled: bool = True
    orderflow_hot_window_limit: int = 150
    orderflow_hot_window_ttl_seconds: float = 900.0
    discovery_interval_seconds: float = 5.0
    whale_interval_seconds: float = 15.0
    status_interval_seconds: float = 60.0
    market_scan_interval_seconds: float = 60.0
    whale_target_count: int = 50
    whale_discovery_min_event_usd: float = 2_500.0
    whale_discovery_min_events: int = 2
    whale_discovery_single_event_usd: float = 10_000.0
    whale_connect_timeout_sec: float = 3.0
    whale_read_timeout_sec: float = 6.0
    whale_inspection_concurrency: int = 8
    venue_configs: Dict[str, VenueConfig] = field(default_factory=build_default_venue_configs)

    @classmethod
    def from_env(cls) -> "RuntimeSettings":
        return cls(
            exchange_id=os.getenv("EXCHANGE_ID", "coinbase"),
            db_path=os.getenv("GHOST_TRADER_DB_PATH", "data/ghost_trader.db"),
            debug_signal_mode=_env_flag("DEBUG_SIGNAL_MODE", False),
            debug_signal_profile=os.getenv("DEBUG_SIGNAL_PROFILE", "sports").strip().lower() or "sports",
            runtime_verify_once=_env_flag("RUNTIME_VERIFY_ONCE", False),
            verify_required_venues=_env_csv("VERIFY_REQUIRED_VENUES"),
            verify_required_category=(os.getenv("VERIFY_REQUIRED_CATEGORY", "").strip().upper() or None),
            market_lookup_limit=max(int(os.getenv("MARKET_LOOKUP_LIMIT", "15000") or 15000), 200),
            paper_sampling_mode=_env_flag("PAPER_SAMPLING_MODE", False),
            paper_sampling_target_closed_trades=max(int(os.getenv("PAPER_SAMPLING_TARGET_CLOSED_TRADES", "20") or 20), 1),
            binance_technical_paper_enabled=_env_flag("BINANCE_TECHNICAL_PAPER_ENABLED", False),
            binance_technical_force_sample=_env_flag("BINANCE_TECHNICAL_FORCE_SAMPLE", False),
            binance_technical_symbols=_env_csv("BINANCE_TECHNICAL_SYMBOLS"),
            binance_technical_timeframe=os.getenv("BINANCE_TECHNICAL_TIMEFRAME", "5m") or "5m",
            binance_technical_min_score=float(os.getenv("BINANCE_TECHNICAL_MIN_SCORE", "0.62") or 0.62),
            binance_technical_force_min_score=float(os.getenv("BINANCE_TECHNICAL_FORCE_MIN_SCORE", "0.5") or 0.5),
            binance_technical_force_cooldown_minutes=float(os.getenv("BINANCE_TECHNICAL_FORCE_COOLDOWN_MINUTES", "30") or 30),
            binance_technical_interval_seconds=max(float(os.getenv("BINANCE_TECHNICAL_INTERVAL_SECONDS", "60") or 60), 10.0),
            orderflow_hot_window_enabled=_env_flag("ORDERFLOW_HOT_WINDOW_ENABLED", True),
            orderflow_hot_window_limit=max(int(os.getenv("ORDERFLOW_HOT_WINDOW_LIMIT", "150") or 150), 1),
            orderflow_hot_window_ttl_seconds=max(float(os.getenv("ORDERFLOW_HOT_WINDOW_TTL_SECONDS", "900") or 900), 60.0),
            status_interval_seconds=max(float(os.getenv("STATUS_INTERVAL_SECONDS", "60") or 60), 10.0),
            whale_target_count=max(int(os.getenv("WHALE_TARGET_COUNT", "50") or 50), 1),
            whale_discovery_min_event_usd=float(os.getenv("WHALE_DISCOVERY_MIN_EVENT_USD", "2500") or 2500),
            whale_discovery_min_events=max(int(os.getenv("WHALE_DISCOVERY_MIN_EVENTS", "2") or 2), 1),
            whale_discovery_single_event_usd=float(os.getenv("WHALE_DISCOVERY_SINGLE_EVENT_USD", "10000") or 10000),
            whale_connect_timeout_sec=float(os.getenv("WHALE_CONNECT_TIMEOUT_SEC", "3") or 3),
            whale_read_timeout_sec=float(os.getenv("WHALE_READ_TIMEOUT_SEC", "6") or 6),
            whale_inspection_concurrency=max(int(os.getenv("WHALE_INSPECTION_CONCURRENCY", "8") or 8), 1),
            venue_configs=build_default_venue_configs(),
        )


class GhostBotRuntime:
    def __init__(self, settings: RuntimeSettings):
        self.settings = settings
        self.decision_engine = DecisionEngine()
        self.crypto_signal_engine = CryptoSignalEngine()
        self.technical_signal_engine = TechnicalSignalEngine()
        self.sample_kind = infer_sample_kind(
            debug_signal_mode=settings.debug_signal_mode,
            debug_profile=settings.debug_signal_profile if settings.debug_signal_mode else None,
        )
        self.debug_profile = settings.debug_signal_profile if settings.debug_signal_mode else None
        self.stop_event = asyncio.Event()
        self.trade_inserted_event = asyncio.Event()
        self.verify_required_venues = set(settings.verify_required_venues or (("polymarket",) if settings.runtime_verify_once else tuple()))
        self.verify_required_category = settings.verify_required_category
        self.verify_completed_venues: set[str] = set()
        self.active_market_context: Dict[str, Dict] = {}
        self.token_to_market_id: Dict[str, str] = {}
        self.lookup_market_context: Dict[str, Dict] = {}
        self.lookup_token_to_market_id: Dict[str, str] = {}
        self.hot_window_market_context: Dict[str, Dict] = {}
        self.hot_window_token_to_market_id: Dict[str, str] = {}
        self.hot_window_expiries: Dict[str, float] = {}
        self.last_lookup_refresh_ts = 0.0
        self.crypto_orderflow_hints: Dict[str, Dict] = {}
        self.mapped_orderflow_events = 0
        self.unmapped_orderflow_events = 0
        self.lazy_lookup_hits = 0
        self.alias_cache_hits = 0
        self.hot_window_hits = 0
        self.hot_window_promotions = 0
        self.hot_window_expiry_events = 0
        self.active_window_misses = 0
        self.hydrated_lookup_markets = 0
        self.hydrated_lookup_aliases = 0
        self.lookup_hydration_warning: Optional[str] = None
        self.sampling_enabled = bool(settings.paper_sampling_mode and self.sample_kind == "live_paper")
        self.sampling_closed_trades = 0
        self.sampling_stop_reason: Optional[str] = None
        self.technical_enabled = bool(settings.binance_technical_paper_enabled and self.sample_kind == "live_paper")
        self.technical_force_last_ts = 0.0
        self.binance_technical_symbol_scope: Tuple[str, ...] = DEFAULT_BINANCE_TECHNICAL_SYMBOLS
        self.technical_stale_review_candidates_90m = 0
        self.technical_stale_exit_candidates_120m = 0
        self.technical_stale_hard_timeout_candidates_240m = 0
        self.technical_stale_exit_executed = 0
        self.technical_stale_hard_timeout_executed = 0
        self.technical_stale_exit_skipped_alignment_support = 0
        self.technical_stale_exit_skipped_recent_support = 0
        self.technical_stale_exit_skipped_profit_protection = 0
        self.technical_open_positions_total = 0
        self.technical_open_positions_strict = 0
        self.technical_open_positions_legacy = 0
        self.technical_open_positions_backfilled = 0
        self.technical_open_positions_ineligible = 0
        self.technical_open_positions_price_structure = 0
        self.technical_open_positions_momentum_source = 0
        self.technical_open_positions_protection_linked = 0
        self.technical_stale_review_runs = 0
        self.technical_open_positions_seen_by_stale_review = 0
        self.technical_stale_review_skipped_reason = "not_run"
        self.technical_legacy_position_shape_summary: Dict[str, int] = {}
        self.technical_legacy_open_positions: list[Dict[str, object]] = []
        self.technical_stale_outcomes: Dict[str, str] = {}

        self.scanner: Optional[MarketScanner] = None
        self.db: Optional[Database] = None
        self.gamma_client: Optional[GammaApiClient] = None
        self.explorer: Optional[MarketExplorer] = None
        self.brain: Optional[Brain] = None
        self.trader: Optional[TradeExecutor] = None
        self.whale_tracker: Optional[WhaleTracker] = None
        self.activity_hunter: Optional[ActivityHunter] = None
        self.copy_trader: Optional[CopyTrader] = None
        self.polymarket_venue: Optional[PolymarketVenue] = None
        self.binance_futures_venue: Optional[BinanceFuturesPaperVenue] = None
        self.binance_spot_venue: Optional[BinanceSpotVenue] = None

    async def initialize(self) -> None:
        self.gamma_client = GammaApiClient(
            connect_timeout_sec=self.settings.whale_connect_timeout_sec,
            read_timeout_sec=self.settings.whale_read_timeout_sec,
            inspection_concurrency=self.settings.whale_inspection_concurrency,
        )
        self.scanner = MarketScanner(
            exchange_id=self.settings.exchange_id,
            debug_signal_mode=self.settings.debug_signal_mode,
        )
        self.db = Database(self.settings.db_path)
        await self.db.connect()
        self.decision_engine.audit_sink = self.audit_decision
        self.explorer = MarketExplorer(
            self.scanner.polymarket,
            debug_signal_mode=self.settings.debug_signal_mode,
            debug_signal_profile=self.settings.debug_signal_profile,
        )
        self.brain = Brain(decision_engine=self.decision_engine)
        self.trader = TradeExecutor(
            self.db,
            live_mode=False,
            trade_insert_callback=self.on_trade_inserted,
            sample_kind=self.sample_kind,
            debug_profile=self.debug_profile,
        )
        self.whale_tracker = WhaleTracker(
            self.scanner.polymarket,
            db=self.db,
            gamma_client=self.gamma_client,
            debug_signal_mode=self.settings.debug_signal_mode,
            poll_interval_seconds=self.settings.whale_interval_seconds,
            target_wallet_count=self.settings.whale_target_count,
            discovery_min_events=self.settings.whale_discovery_min_events,
            discovery_single_event_usd=self.settings.whale_discovery_single_event_usd,
        )
        self.activity_hunter = ActivityHunter(
            debug_signal_mode=self.settings.debug_signal_mode,
            debug_signal_profile=self.settings.debug_signal_profile,
            db=self.db,
            gamma_client=self.gamma_client,
            discovery_min_event_usd=self.settings.whale_discovery_min_event_usd,
            discovery_min_events=self.settings.whale_discovery_min_events,
            discovery_single_event_usd=self.settings.whale_discovery_single_event_usd,
        )
        self.copy_trader = CopyTrader(
            self.trader,
            self.scanner,
            self.db,
            self.decision_engine,
            market_resolver=self.lazy_resolve_market_context,
            lookup_context_resolver=self.resolve_lookup_market_context,
            mapping_event_callback=self.record_mapping_event,
            market_promotion_callback=self.promote_hot_window_market,
        )
        await self.refresh_sampling_state()
        self._publish_sampling_state()

        self.polymarket_venue = PolymarketVenue(
            self.settings.venue_configs["polymarket"],
            self.db,
            self.trader,
            sample_kind=self.sample_kind,
            debug_profile=self.debug_profile,
        )
        self.binance_futures_venue = BinanceFuturesPaperVenue(
            self.settings.venue_configs["binance_futures"],
            self.db,
            self.scanner,
            trade_insert_callback=self.on_trade_inserted,
            sample_kind=self.sample_kind,
            debug_profile=self.debug_profile,
        )
        self.binance_spot_venue = BinanceSpotVenue(
            self.settings.venue_configs["binance_spot"],
            self.db,
            self.scanner,
            trade_insert_callback=self.on_trade_inserted,
            sample_kind=self.sample_kind,
            debug_profile=self.debug_profile,
        )

    async def close(self) -> None:
        self.stop_event.set()
        if self.scanner is not None:
            await self.scanner.close()
        if self.db is not None:
            await self.db.close()

    async def on_trade_inserted(self, trade_record: Dict) -> None:
        if not self.settings.runtime_verify_once:
            return

        category = str(trade_record.get("category", "") or "").upper()
        venue = str(trade_record.get("venue", "") or "").strip()
        if self.verify_required_category and category != self.verify_required_category:
            return
        if self.verify_required_venues and venue not in self.verify_required_venues:
            return

        self.verify_completed_venues.add(venue)
        if not self.verify_required_venues or self.verify_completed_venues.issuperset(self.verify_required_venues):
            self.trade_inserted_event.set()

    async def audit_decision(self, decision) -> None:
        if self.db is None:
            return
        await self.db.add_decision_audit(
            venue=decision.venue,
            market_id=decision.market_id,
            category=decision.category,
            signal_family=normalize_signal_family(decision.source),
            strategy_profile=decision.strategy_profile,
            raw_source_signal=decision.source,
            sample_kind=self.sample_kind,
            is_synthetic=self.sample_kind != "live_paper",
            decision_score=decision.score,
            threshold=decision.threshold,
            trade_size=decision.trade_size,
            action="decision" if decision.should_trade else "reject",
            reason=",".join(decision.reasons) if decision.reasons else None,
            confidence=decision.score,
            whale_trust=float(decision.inputs.get("whale_trust", 0.5)) if decision.inputs.get("whale_trust") is not None else None,
            spread_pct=float(decision.inputs.get("spread_pct")) if decision.inputs.get("spread_pct") is not None else None,
            slippage_proxy_bps=slippage_proxy_bps_from_spread(decision.inputs.get("spread_pct")),
            inputs_json=json.dumps(decision.inputs, sort_keys=True, default=str),
            mapping_stage=decision.inputs.get("mapping_stage"),
            alias_candidates_json=json.dumps(decision.inputs.get("alias_candidates", []), sort_keys=True, default=str),
            lazy_lookup_attempted=bool(decision.inputs.get("lazy_lookup_attempted", False)),
            lazy_lookup_hit=bool(decision.inputs.get("lazy_lookup_hit", False)),
            hot_window_promoted=bool(decision.inputs.get("hot_window_promoted", False)),
        )

    async def refresh_sampling_state(self) -> None:
        if self.db is None:
            return

        if self.sample_kind != "live_paper" or not self.settings.paper_sampling_mode:
            self.sampling_enabled = False
            self.sampling_closed_trades = 0
            self.sampling_stop_reason = None
            self._publish_sampling_state()
            return

        closed_trades = await self.db.count_closed_trades(
            sample_kind="live_paper",
            strategy_profile=STRATEGY_PROFILE_SAMPLING_RELAXED,
            is_synthetic=False,
            venue="polymarket",
        )
        self.sampling_closed_trades = closed_trades
        if closed_trades >= self.settings.paper_sampling_target_closed_trades:
            self.sampling_enabled = False
            self.sampling_stop_reason = "target_reached"
        else:
            self.sampling_enabled = True
            self.sampling_stop_reason = None
        self._publish_sampling_state()

    def _publish_sampling_state(self) -> None:
        if self.copy_trader is None:
            return
        self.copy_trader.update_sampling_state(
            enabled=self.sampling_enabled,
            target_reached=bool(
                self.settings.paper_sampling_mode
                and self.sample_kind == "live_paper"
                and self.sampling_closed_trades >= self.settings.paper_sampling_target_closed_trades
            ),
            closed_trades=self.sampling_closed_trades,
            target_closed_trades=self.settings.paper_sampling_target_closed_trades,
            stop_reason=self.sampling_stop_reason,
        )

    async def bootstrap_market_context(self) -> None:
        if self.explorer is None or self.scanner is None or self.copy_trader is None:
            return

        await self._hydrate_lookup_context_from_db(reset=True)
        lookup_universe = await self.explorer.fetch_market_lookup_universe(limit=self.settings.market_lookup_limit)
        await self._refresh_lookup_context(lookup_universe, source="explorer", reset=False)
        self.last_lookup_refresh_ts = time.time()
        active_markets = await self.explorer.fetch_active_markets(limit=self.settings.market_limit)
        await self._refresh_active_market_context(active_markets)
        await self._prune_hot_window()
        symbols = {
            resolve_crypto_symbol(market.get("question", ""), self.settings.exchange_id)
            for market in lookup_universe
            if classify_market_category(market.get("question", "")) == "CRYPTO"
        }
        await self.scanner.update_monitored_symbols([symbol for symbol in symbols if symbol])
        await self.log_runtime_status(len(active_markets), 0.0)

    async def run(self) -> None:
        await self.initialize()
        await self.bootstrap_market_context()

        tasks = [
            asyncio.create_task(self.run_discovery_loop(), name="discovery"),
            asyncio.create_task(self.run_whale_tracker_loop(), name="whale_tracker"),
            asyncio.create_task(self.run_activity_hunter_loop(), name="activity_hunter"),
        ]
        if self.technical_enabled:
            tasks.append(asyncio.create_task(self.run_binance_technical_loop(), name="binance_technical"))

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
        if self.explorer is None or self.scanner is None or self.db is None:
            return

        last_status_log = 0.0
        last_market_scan_log = 0.0

        while not self.stop_event.is_set():
            cycle_started = time.time()
            if (time.time() - self.last_lookup_refresh_ts) >= self.settings.market_scan_interval_seconds or not self.lookup_market_context:
                await self._hydrate_lookup_context_from_db(reset=True)
                lookup_universe = await self.explorer.fetch_market_lookup_universe(limit=self.settings.market_lookup_limit)
                await self._refresh_lookup_context(lookup_universe, source="explorer", reset=False)
                self.last_lookup_refresh_ts = time.time()
            else:
                lookup_universe = list(self.lookup_market_context.values())

            active_markets = await self.explorer.fetch_active_markets(limit=self.settings.market_limit)
            await self._refresh_active_market_context(active_markets)
            await self._prune_hot_window()
            trade_markets = self._build_trade_market_universe(active_markets)

            if not trade_markets:
                logger.info("[STATUS] No active Polymarket markets discovered in this cycle.")
            else:
                symbols = {
                    resolve_crypto_symbol(market.get("question", ""), self.settings.exchange_id)
                    for market in lookup_universe
                    if market.get("category") == "CRYPTO"
                }
                await self.scanner.update_monitored_symbols([symbol for symbol in symbols if symbol])
                for market in trade_markets:
                    await self.process_market(market)
                    if self.stop_event.is_set():
                        break

            await self.trader.check_resolutions(self.scanner)
            await self.sync_venue_states()

            now = time.time()
            if now - last_status_log >= self.settings.status_interval_seconds:
                await self.log_runtime_status(len(active_markets), now - cycle_started)
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

    async def sync_venue_states(self) -> None:
        if self.binance_futures_venue is not None and self.settings.venue_configs["binance_futures"].enabled:
            await self.binance_futures_venue.sync_account_state(self.scanner)
        if self.binance_spot_venue is not None and self.settings.venue_configs["binance_spot"].enabled:
            await self.binance_spot_venue.sync_account_state(self.scanner)
        await self._review_stale_binance_technical_positions()

    @staticmethod
    def _parse_sqlite_utc(raw_value: object) -> Optional[datetime]:
        if raw_value in (None, ""):
            return None
        text = str(raw_value).strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def _position_age_minutes(self, position: Dict) -> Optional[int]:
        opened_at = self._parse_sqlite_utc(position.get("opened_at"))
        if opened_at is None:
            return None
        now_utc = datetime.now(timezone.utc)
        return max(0, int((now_utc - opened_at).total_seconds() // 60))

    @staticmethod
    def _position_unrealized_pnl_pct(position: Dict) -> float:
        try:
            notional = float(position.get("notional_usd") or 0.0)
            if notional <= 0:
                return 0.0
            unrealized = float(position.get("unrealized_pnl") or 0.0)
            return (unrealized / notional) * 100.0
        except (TypeError, ValueError):
            return 0.0

    def _expected_position_direction(self, position: Dict) -> str:
        side = str(position.get("side") or "").upper()
        if side in {"LONG", "SHORT"}:
            return side
        if side in {"BUY", "YES"}:
            return "LONG"
        if side in {"SELL", "NO"}:
            return "SHORT"
        return side

    def _resolve_futures_symbol_for_technical_position(self, position: Dict) -> str:
        symbol = str(position.get("symbol_or_market_id") or "").strip()
        if not symbol:
            return ""
        venue = str(position.get("venue") or "").strip().lower()
        if venue == "binance_futures":
            return symbol
        base_symbol = symbol.split("/")[0].upper()
        return BINANCE_FUTURES_MAPPINGS.get(base_symbol, base_symbol)

    async def _describe_binance_technical_open_position(self, position: Dict) -> Dict[str, object]:
        venue = str(position.get("venue") or "").strip().lower()
        execution_mode = str(position.get("execution_mode") or "").strip().lower()
        status = str(position.get("status") or "").strip().upper()
        symbol_or_market_id = str(position.get("symbol_or_market_id") or "").strip()
        source_signal = str(position.get("source_signal") or "").strip()
        signal_family = str(position.get("signal_family") or "").strip()
        strategy_profile = str(position.get("strategy_profile") or "").strip()
        sample_kind = str(position.get("sample_kind") or "").strip()
        instrument_type = str(position.get("instrument_type") or "").strip().lower()
        position_id = position.get("id")
        age_minutes = self._position_age_minutes(position)

        open_orders = []
        if self.db is not None and symbol_or_market_id:
            open_orders = await self.db.get_open_venue_orders(venue, symbol_or_market_id=symbol_or_market_id)
        related_orders = []
        for raw_order in open_orders:
            order = dict(raw_order)
            order_position_id = order.get("position_id")
            if order_position_id in (None, "", position_id):
                related_orders.append(order)
        order_types = {str(order.get("order_type") or "").strip().upper() for order in related_orders}
        has_stop_loss_order = "STOP_LOSS" in order_types
        has_take_profit_order = "TAKE_PROFIT" in order_types
        protection_order_match = has_stop_loss_order or has_take_profit_order
        normalized_signal_family = normalize_signal_family(signal_family or source_signal)
        source_signal_match = source_signal in {
            "binance_technical_momentum",
            "binance_futures_price_structure",
            "binance_spot_price_structure",
        }
        price_structure_source = source_signal in {"binance_futures_price_structure", "binance_spot_price_structure"}
        momentum_source = source_signal == "binance_technical_momentum"
        signal_family_match = signal_family in {"binance_technical_momentum", "technical"} or normalized_signal_family == "technical"
        strategy_profile_match = strategy_profile == STRATEGY_PROFILE_BINANCE_TECHNICAL
        sample_kind_match = sample_kind == "live_paper"
        is_strict = strategy_profile_match and sample_kind_match
        marker_labels = []
        if price_structure_source:
            marker_labels.append("price_structure_source")
        if momentum_source:
            marker_labels.append("technical_momentum_source")
        if signal_family_match:
            marker_labels.append("technical_signal_family")
        if strategy_profile_match:
            marker_labels.append("technical_strategy_profile")
        if sample_kind_match:
            marker_labels.append("live_paper_sample")
        if has_stop_loss_order:
            marker_labels.append("stop_loss_order")
        if has_take_profit_order:
            marker_labels.append("take_profit_order")

        if venue not in {"binance_futures", "binance_spot"}:
            classification = "ineligible"
        elif execution_mode != "paper" or status != "OPEN":
            classification = "ineligible"
        elif is_strict:
            classification = "strict_technical"
        elif source_signal_match or signal_family_match or strategy_profile_match or protection_order_match:
            classification = "legacy_technical"
        else:
            classification = "ineligible"

        return {
            "position_id": position_id,
            "classification": classification,
            "venue": venue,
            "symbol_or_market_id": symbol_or_market_id,
            "side": str(position.get("side") or "").strip().upper(),
            "instrument_type": instrument_type,
            "source_signal": source_signal,
            "signal_family": signal_family,
            "strategy_profile": strategy_profile,
            "sample_kind": sample_kind,
            "normalized_signal_family": normalized_signal_family,
            "position_age_minutes": age_minutes if age_minutes is not None else 0.0,
            "opened_at": str(position.get("opened_at") or ""),
            "has_stop_loss_order": has_stop_loss_order,
            "has_take_profit_order": has_take_profit_order,
            "protection_order_match": protection_order_match,
            "price_structure_source": price_structure_source,
            "momentum_source": momentum_source,
            "signal_family_match": signal_family_match,
            "strategy_profile_match": strategy_profile_match,
            "sample_kind_match": sample_kind_match,
            "marker_labels": marker_labels,
        }

    async def _backfill_legacy_binance_technical_position(self, position: Dict) -> bool:
        if self.db is None or self.db.conn is None:
            return False

        updates: list[str] = []
        params: list[object] = []
        if str(position.get("strategy_profile") or "").strip() != STRATEGY_PROFILE_BINANCE_TECHNICAL:
            updates.append("strategy_profile = ?")
            params.append(STRATEGY_PROFILE_BINANCE_TECHNICAL)
            position["strategy_profile"] = STRATEGY_PROFILE_BINANCE_TECHNICAL

        sample_kind = str(position.get("sample_kind") or "").strip()
        if not sample_kind:
            updates.append("sample_kind = ?")
            params.append("live_paper")
            position["sample_kind"] = "live_paper"

        signal_family = str(position.get("signal_family") or "").strip()
        if signal_family in {"", "unknown"}:
            updates.append("signal_family = ?")
            params.append("binance_technical_momentum")
            position["signal_family"] = "binance_technical_momentum"

        if not updates:
            return False

        params.append(position["id"])
        await self.db.conn.execute(
            f"UPDATE venue_positions SET {', '.join(updates)} WHERE id = ?",
            params,
        )
        await self.db.conn.commit()
        return True

    async def _has_recent_same_direction_technical_support(
        self,
        position: Dict,
        expected_direction: str,
        futures_symbol: str,
        *,
        minutes: int = 60,
    ) -> bool:
        if self.db is None or self.db.conn is None:
            return False

        symbols = {
            str(position.get("symbol_or_market_id") or "").strip(),
            str(futures_symbol or "").strip(),
        }
        symbols = {symbol for symbol in symbols if symbol}
        if not symbols:
            return False

        since_iso = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S")
        placeholders = ", ".join("?" for _ in symbols)
        async with self.db.conn.execute(
            f"""
            SELECT action, inputs_json
            FROM decision_audit
            WHERE strategy_profile = ?
              AND sample_kind = 'live_paper'
              AND venue IN ('binance_futures', 'binance_spot')
              AND action IN ('decision', 'execute')
              AND occurred_at >= ?
              AND market_id IN ({placeholders})
            ORDER BY id DESC
            """,
            [STRATEGY_PROFILE_BINANCE_TECHNICAL, since_iso, *symbols],
        ) as cursor:
            rows = await cursor.fetchall()

        for row in rows:
            try:
                inputs = json.loads(row["inputs_json"] or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                inputs = {}
            direction = str(inputs.get("signal_direction") or inputs.get("direction") or "").upper()
            if direction == expected_direction:
                return True
        return False

    async def _score_binance_technical_symbol_for_stale_review(self, futures_symbol: str):
        if self.scanner is None:
            return None, None

        futures_snapshot = await self.scanner.get_futures_market_snapshot(futures_symbol)
        if not futures_snapshot.get("is_valid"):
            return None, futures_snapshot

        futures_history = await self.scanner.get_futures_historical_data(
            futures_symbol,
            timeframe=self.settings.binance_technical_timeframe,
            limit=200,
        )
        closes = futures_history["close"] if not futures_history.empty and "close" in futures_history else pd.Series(dtype=float)
        volumes = futures_history["volume"] if not futures_history.empty and "volume" in futures_history else pd.Series(dtype=float)
        signal = self.technical_signal_engine.score(
            symbol=futures_symbol,
            closes=closes,
            volumes=volumes,
            spread_pct=float(futures_snapshot.get("spread_pct") or 0.0),
            volume_24h=float(futures_snapshot.get("volume_24h") or 0.0),
            min_score=self.settings.binance_technical_min_score,
            timeframe=self.settings.binance_technical_timeframe,
            paper_recovery=True,
            force_sample_enabled=False,
            force_min_score=self.settings.binance_technical_force_min_score,
        )
        return signal, futures_snapshot

    def _record_stale_outcome(self, position_key: str, outcome: str) -> None:
        previous = self.technical_stale_outcomes.get(position_key)
        if previous == outcome:
            return
        self.technical_stale_outcomes[position_key] = outcome
        if outcome == "executed":
            self.technical_stale_exit_executed += 1
        elif outcome == "executed_hard_timeout":
            self.technical_stale_hard_timeout_executed += 1
        elif outcome == "skip_alignment":
            self.technical_stale_exit_skipped_alignment_support += 1
        elif outcome == "skip_recent_support":
            self.technical_stale_exit_skipped_recent_support += 1
        elif outcome == "skip_profit":
            self.technical_stale_exit_skipped_profit_protection += 1

    async def _review_stale_binance_technical_positions(self) -> None:
        self.technical_stale_review_runs += 1
        self.technical_stale_review_skipped_reason = "none"
        if self.db is None:
            self.technical_stale_review_candidates_90m = 0
            self.technical_stale_exit_candidates_120m = 0
            self.technical_stale_hard_timeout_candidates_240m = 0
            self.technical_open_positions_total = 0
            self.technical_open_positions_strict = 0
            self.technical_open_positions_legacy = 0
            self.technical_open_positions_backfilled = 0
            self.technical_open_positions_ineligible = 0
            self.technical_open_positions_price_structure = 0
            self.technical_open_positions_momentum_source = 0
            self.technical_open_positions_protection_linked = 0
            self.technical_open_positions_seen_by_stale_review = 0
            self.technical_stale_review_skipped_reason = "db_missing"
            self.technical_legacy_position_shape_summary = {}
            self.technical_legacy_open_positions = []
            self.technical_stale_outcomes = {}
            return

        open_positions = []
        for venue_id in ("binance_futures", "binance_spot"):
            open_positions.extend(await self.db.get_open_positions(venue=venue_id))
        self.technical_open_positions_seen_by_stale_review = len(open_positions)

        if self.scanner is None:
            self.technical_stale_review_skipped_reason = "scanner_missing"
            self.technical_stale_review_candidates_90m = 0
            self.technical_stale_exit_candidates_120m = 0
            self.technical_stale_hard_timeout_candidates_240m = 0
            self.technical_open_positions_total = len(open_positions)
            self.technical_open_positions_strict = 0
            self.technical_open_positions_legacy = 0
            self.technical_open_positions_backfilled = 0
            self.technical_open_positions_ineligible = len(open_positions)
            self.technical_open_positions_price_structure = 0
            self.technical_open_positions_momentum_source = 0
            self.technical_open_positions_protection_linked = 0
            self.technical_legacy_position_shape_summary = {
                "open_binance_paper_positions": len(open_positions),
                "strict_technical_positions": 0,
                "legacy_technical_positions": 0,
                "ineligible_positions": len(open_positions),
                "price_structure_source_positions": 0,
                "technical_momentum_source_positions": 0,
                "protection_linked_positions": 0,
                "backfilled_positions": 0,
            }
            self.technical_legacy_open_positions = []
            return

        current_keys: set[str] = set()
        review_candidates_90m = 0
        exit_candidates_120m = 0
        hard_timeout_candidates_240m = 0
        total_positions = 0
        strict_positions = 0
        legacy_positions = 0
        backfilled_positions = 0
        ineligible_positions = 0
        price_structure_positions = 0
        momentum_source_positions = 0
        protection_linked_positions = 0
        legacy_open_positions: list[Dict[str, object]] = []

        for raw_position in open_positions:
            position = dict(raw_position)
            total_positions += 1
            position_shape = await self._describe_binance_technical_open_position(position)
            classification = str(position_shape.get("classification") or "ineligible")
            if position_shape.get("price_structure_source"):
                price_structure_positions += 1
            if position_shape.get("momentum_source"):
                momentum_source_positions += 1
            if position_shape.get("protection_order_match"):
                protection_linked_positions += 1
            legacy_open_positions.append(position_shape)
            if classification == "ineligible":
                ineligible_positions += 1
                continue
            if classification == "strict_technical":
                strict_positions += 1
            elif classification == "legacy_technical":
                legacy_positions += 1
                if await self._backfill_legacy_binance_technical_position(position):
                    backfilled_positions += 1

            position_id = position.get("id")
            venue = str(position.get("venue") or "").strip().lower()
            position_key = f"{venue}:{position_id}"
            current_keys.add(position_key)

            age_minutes = self._position_age_minutes(position)
            if age_minutes is None or age_minutes < 90:
                continue
            review_candidates_90m += 1
            if age_minutes < 120:
                self.technical_stale_outcomes[position_key] = "candidate_90m"
                continue

            exit_candidates_120m += 1
            unrealized_pnl_pct = self._position_unrealized_pnl_pct(position)
            if unrealized_pnl_pct > 0.75:
                self._record_stale_outcome(position_key, "skip_profit")
                continue

            futures_symbol = self._resolve_futures_symbol_for_technical_position(position)
            if not futures_symbol:
                continue

            signal, futures_snapshot = await self._score_binance_technical_symbol_for_stale_review(futures_symbol)
            if signal is None or futures_snapshot is None:
                continue

            expected_direction = self._expected_position_direction(position)
            support_reasons = set(signal.reasons or [])
            same_direction_supported = signal.should_trade and signal.direction == expected_direction
            if same_direction_supported:
                self._record_stale_outcome(position_key, "skip_alignment")
                continue

            recent_support = False
            if age_minutes >= 240:
                hard_timeout_candidates_240m += 1
                recent_support = await self._has_recent_same_direction_technical_support(
                    position,
                    expected_direction,
                    futures_symbol,
                    minutes=60,
                )
                if recent_support:
                    self._record_stale_outcome(position_key, "skip_recent_support")
                    continue
                hard_timeout_triggered = True
                stale_exit_reason = "stale_hard_timeout_exit"
            else:
                hard_timeout_triggered = False
                if "technical_alignment_weak" not in support_reasons and "score_below_threshold" not in support_reasons:
                    continue
                stale_exit_reason = "stale_time_exit"

            exit_price = float(futures_snapshot.get("mark_price") or futures_snapshot.get("last_price") or 0.0)
            audit_inputs = {
                "stale_position_classification": classification,
                "position_age_minutes": age_minutes,
                "stale_review_candidate": True,
                "stale_exit_candidate": True,
                "stale_exit_triggered": True,
                "stale_hard_timeout_candidate": hard_timeout_triggered,
                "stale_exit_reason": stale_exit_reason,
                "stale_exit_alignment_support": False,
                "stale_exit_recent_support": recent_support,
                "stale_exit_profit_protected": False,
                "stale_exit_signal_direction": signal.direction,
                "stale_exit_signal_score": signal.score,
                "stale_exit_signal_threshold": signal.threshold,
                "stale_exit_signal_reasons": list(signal.reasons or []),
                "stale_exit_symbol": futures_symbol,
                "unrealized_pnl_pct": unrealized_pnl_pct,
            }

            if venue == "binance_futures" and self.binance_futures_venue is not None:
                success, _ = await self.binance_futures_venue.place_exit_order(
                    position["symbol_or_market_id"],
                    exit_price=exit_price,
                    reason=stale_exit_reason,
                    source="stale_exit_review",
                    audit_inputs=audit_inputs,
                )
            elif venue == "binance_spot" and self.binance_spot_venue is not None:
                spot_snapshot = await self.scanner.get_spot_market_snapshot(str(position.get("symbol_or_market_id") or ""))
                if not spot_snapshot.get("is_valid"):
                    continue
                success, _ = await self.binance_spot_venue.place_exit_order(
                    position["symbol_or_market_id"],
                    exit_price=float(spot_snapshot.get("last_price") or 0.0),
                    reason=stale_exit_reason,
                    source="stale_exit_review",
                    audit_inputs=audit_inputs,
                )
            else:
                success = False

            if success:
                self._record_stale_outcome(
                    position_key,
                    "executed_hard_timeout" if hard_timeout_triggered else "executed",
                )

        self.technical_stale_review_candidates_90m = review_candidates_90m
        self.technical_stale_exit_candidates_120m = exit_candidates_120m
        self.technical_stale_hard_timeout_candidates_240m = hard_timeout_candidates_240m
        self.technical_open_positions_total = total_positions
        self.technical_open_positions_strict = strict_positions
        self.technical_open_positions_legacy = legacy_positions
        self.technical_open_positions_backfilled = backfilled_positions
        self.technical_open_positions_ineligible = ineligible_positions
        self.technical_open_positions_price_structure = price_structure_positions
        self.technical_open_positions_momentum_source = momentum_source_positions
        self.technical_open_positions_protection_linked = protection_linked_positions
        self.technical_legacy_position_shape_summary = {
            "open_binance_paper_positions": total_positions,
            "strict_technical_positions": strict_positions,
            "legacy_technical_positions": legacy_positions,
            "ineligible_positions": ineligible_positions,
            "price_structure_source_positions": price_structure_positions,
            "technical_momentum_source_positions": momentum_source_positions,
            "protection_linked_positions": protection_linked_positions,
            "backfilled_positions": backfilled_positions,
        }
        self.technical_legacy_open_positions = legacy_open_positions[:10]
        self.technical_stale_outcomes = {
            key: value for key, value in self.technical_stale_outcomes.items() if key in current_keys
        }

    async def log_runtime_status(self, active_markets_count: int, cycle_duration: float) -> None:
        if self.db is None:
            return

        await self._review_stale_binance_technical_positions()
        await self.refresh_sampling_state()
        total, wins, win_rate, total_pnl = await self.db.get_bot_performance()
        futures_realized, futures_unrealized = await self.db.get_venue_performance("binance_futures")
        futures_balance = await self.db.get_balance("binance_futures", self.settings.venue_configs["binance_futures"].mode)
        futures_open_positions = await self.db.get_open_positions(venue="binance_futures")
        spot_realized, spot_unrealized = await self.db.get_venue_performance("binance_spot")
        spot_balance = await self.db.get_balance("binance_spot", self.settings.venue_configs["binance_spot"].mode)
        spot_open_positions = await self.db.get_open_positions(venue="binance_spot")
        whale_tracker = self.whale_tracker
        total_orderflow_events = self.mapped_orderflow_events + self.unmapped_orderflow_events
        market_not_mapped_rate = (
            (self.unmapped_orderflow_events / total_orderflow_events) * 100.0 if total_orderflow_events else 0.0
        )
        resolver_hits = self.alias_cache_hits + self.lazy_lookup_hits
        resolver_hit_rate = (resolver_hits / self.mapped_orderflow_events) * 100.0 if self.mapped_orderflow_events else 0.0
        active_window_miss_rate = (
            (self.active_window_misses / total_orderflow_events) * 100.0 if total_orderflow_events else 0.0
        )
        sampling_mode = "enabled" if self.sampling_enabled else ("target_reached" if self.sampling_stop_reason == "target_reached" else "disabled")
        alias_integrity = await self.db.get_market_alias_integrity()
        whale_universe_summary = await self.db.get_whale_universe_summary()
        graph_discovery_summary = (
            self.activity_hunter.get_graph_discovery_summary()
            if self.activity_hunter is not None and hasattr(self.activity_hunter, "get_graph_discovery_summary")
            else {}
        )
        whale_candidate_aggregation = (
            self.copy_trader.get_whale_candidate_aggregation_summary()
            if self.copy_trader is not None and hasattr(self.copy_trader, "get_whale_candidate_aggregation_summary")
            else {}
        )
        persisted_market_alias_rows = int(alias_integrity["alias_rows"] or 0) if alias_integrity else 0
        persisted_market_alias_markets = int(alias_integrity["market_rows"] or 0) if alias_integrity else 0
        lookup_universe_markets = len(self.lookup_market_context)
        lookup_universe_aliases = self._count_lookup_universe_aliases()
        alias_persistence_gap = max(lookup_universe_aliases - persisted_market_alias_rows, 0)
        status_metrics = {
            "active_markets": active_markets_count,
            "tracked_whales": len(whale_tracker.top_whales if whale_tracker else []),
            "leaderboard_wallets": getattr(whale_tracker, "leaderboard_wallets_count", 0),
            "activity_discovered_wallets": getattr(whale_tracker, "activity_discovered_wallets_count", 0),
            "graph_discovered_wallets": getattr(whale_tracker, "graph_discovered_wallets_count", whale_universe_summary["graph_discovered_wallets"]),
            "persisted_wallets": getattr(whale_tracker, "persisted_wallets_count", 0),
            "trusted_whales": whale_universe_summary["trusted_whales"],
            "wallet_timeouts_last_cycle": getattr(whale_tracker, "wallet_timeouts_last_cycle", 0),
            "source_mode": getattr(whale_tracker, "source_mode", "uninitialized"),
            "total_trades": total,
            "win_rate": round(win_rate, 1),
            "total_pnl": round(total_pnl, 2),
            "futures_balance": round(futures_balance, 2),
            "futures_realized": round(futures_realized, 2),
            "futures_unrealized": round(futures_unrealized, 2),
            "futures_open_positions": len(futures_open_positions),
            "spot_balance": round(spot_balance, 2),
            "spot_realized": round(spot_realized, 2),
            "spot_unrealized": round(spot_unrealized, 2),
            "spot_open_positions": len(spot_open_positions),
            "mapped_orderflow_events": self.mapped_orderflow_events,
            "unmapped_orderflow_events": self.unmapped_orderflow_events,
            "alias_cache_hits": self.alias_cache_hits,
            "lazy_lookup_hits": self.lazy_lookup_hits,
            "hot_window_markets": len(self.hot_window_market_context),
            "hot_window_hits": self.hot_window_hits,
            "hot_window_promotions": self.hot_window_promotions,
            "hot_window_expiries": self.hot_window_expiry_events,
            "active_window_misses": self.active_window_misses,
            "resolver_hit_rate": round(resolver_hit_rate, 1),
            "active_window_miss_rate": round(active_window_miss_rate, 1),
            "market_not_mapped_rate": round(market_not_mapped_rate, 1),
            "lookup_universe_markets": lookup_universe_markets,
            "lookup_universe_aliases": lookup_universe_aliases,
            "persisted_market_alias_rows": persisted_market_alias_rows,
            "persisted_market_alias_markets": persisted_market_alias_markets,
            "hydrated_lookup_markets": self.hydrated_lookup_markets,
            "hydrated_lookup_aliases": self.hydrated_lookup_aliases,
            "lookup_hydration_warning": self.lookup_hydration_warning or "none",
            "alias_persistence_gap": alias_persistence_gap,
            "graph_clusters_promoted": int(graph_discovery_summary.get("graph_clusters_promoted", 0) or 0),
            "graph_skipped_missing_market_ref": int(graph_discovery_summary.get("graph_skipped_missing_market_ref", 0) or 0),
            "graph_skipped_single_wallet": int(graph_discovery_summary.get("graph_skipped_single_wallet", 0) or 0),
            "graph_skipped_low_notional": int(graph_discovery_summary.get("graph_skipped_low_notional", 0) or 0),
            "whale_copy_accumulator_buckets": int(whale_candidate_aggregation.get("accumulator_buckets", 0) or 0),
            "whale_copy_gate_ready_candidates": int(whale_candidate_aggregation.get("gate_ready_candidates", 0) or 0),
            "whale_copy_retry_candidates": int(whale_candidate_aggregation.get("retry_candidates", 0) or 0),
            "whale_copy_accumulated_buy_events": int(whale_candidate_aggregation.get("accumulated_buy_events", 0) or 0),
            "whale_copy_accumulated_total_notional": round(float(whale_candidate_aggregation.get("accumulated_total_notional", 0.0) or 0.0), 4),
            "recent_gate_ready_candidates": whale_candidate_aggregation.get("recent_gate_ready_candidates", []),
            "binance_technical_active_symbol_count": len(self.binance_technical_symbol_scope),
            "binance_technical_symbol_scope": list(self.binance_technical_symbol_scope),
            "technical_open_positions_total": self.technical_open_positions_total,
            "technical_open_positions_strict": self.technical_open_positions_strict,
            "technical_open_positions_legacy": self.technical_open_positions_legacy,
            "technical_open_positions_backfilled": self.technical_open_positions_backfilled,
            "technical_open_positions_ineligible": self.technical_open_positions_ineligible,
            "technical_open_positions_price_structure": self.technical_open_positions_price_structure,
            "technical_open_positions_momentum_source": self.technical_open_positions_momentum_source,
            "technical_open_positions_protection_linked": self.technical_open_positions_protection_linked,
            "technical_stale_review_runs": self.technical_stale_review_runs,
            "technical_open_positions_seen_by_stale_review": self.technical_open_positions_seen_by_stale_review,
            "technical_stale_review_skipped_reason": self.technical_stale_review_skipped_reason,
            "technical_legacy_position_shape_summary": self.technical_legacy_position_shape_summary,
            "technical_legacy_open_positions": self.technical_legacy_open_positions,
            "technical_stale_review_candidates_90m": self.technical_stale_review_candidates_90m,
            "technical_stale_exit_candidates_120m": self.technical_stale_exit_candidates_120m,
            "technical_stale_hard_timeout_candidates_240m": self.technical_stale_hard_timeout_candidates_240m,
            "technical_stale_exit_executed": self.technical_stale_exit_executed,
            "technical_stale_hard_timeout_executed": self.technical_stale_hard_timeout_executed,
            "technical_stale_exit_skipped_alignment_support": self.technical_stale_exit_skipped_alignment_support,
            "technical_stale_exit_skipped_recent_support": self.technical_stale_exit_skipped_recent_support,
            "technical_stale_exit_skipped_profit_protection": self.technical_stale_exit_skipped_profit_protection,
            "sampling_mode": sampling_mode,
            "sampling_closed_trades": self.sampling_closed_trades,
            "sampling_target_closed_trades": self.settings.paper_sampling_target_closed_trades,
            "sampling_stop_reason": self.sampling_stop_reason or "none",
            "scan_time": round(cycle_duration, 2),
        }
        await self.db.upsert_runtime_status_snapshot(status_metrics)
        logger.info(
            "[STATUS] active_markets=%s tracked_whales=%s leaderboard_wallets=%s activity_discovered_wallets=%s graph_discovered_wallets=%s trusted_whales=%s persisted_wallets=%s wallet_timeouts_last_cycle=%s source_mode=%s total_trades=%s win_rate=%.1f total_pnl=%.2f futures_balance=%.2f futures_realized=%.2f futures_unrealized=%.2f futures_open_positions=%s spot_balance=%.2f spot_realized=%.2f spot_unrealized=%.2f spot_open_positions=%s mapped_orderflow_events=%s unmapped_orderflow_events=%s alias_cache_hits=%s lazy_lookup_hits=%s hot_window_markets=%s hot_window_hits=%s hot_window_promotions=%s hot_window_expiries=%s active_window_misses=%s resolver_hit_rate=%.1f active_window_miss_rate=%.1f market_not_mapped_rate=%.1f lookup_universe_markets=%s lookup_universe_aliases=%s persisted_market_alias_rows=%s persisted_market_alias_markets=%s hydrated_lookup_markets=%s hydrated_lookup_aliases=%s lookup_hydration_warning=%s alias_persistence_gap=%s graph_clusters_promoted=%s graph_skipped_missing_market_ref=%s graph_skipped_single_wallet=%s graph_skipped_low_notional=%s whale_copy_accumulator_buckets=%s whale_copy_gate_ready_candidates=%s whale_copy_retry_candidates=%s whale_copy_accumulated_buy_events=%s whale_copy_accumulated_total_notional=%.2f sampling_mode=%s sampling_closed_trades=%s sampling_target_closed_trades=%s sampling_stop_reason=%s scan_time=%.2fs",
            status_metrics["active_markets"],
            status_metrics["tracked_whales"],
            status_metrics["leaderboard_wallets"],
            status_metrics["activity_discovered_wallets"],
            status_metrics["graph_discovered_wallets"],
            status_metrics["trusted_whales"],
            status_metrics["persisted_wallets"],
            status_metrics["wallet_timeouts_last_cycle"],
            status_metrics["source_mode"],
            status_metrics["total_trades"],
            status_metrics["win_rate"],
            status_metrics["total_pnl"],
            status_metrics["futures_balance"],
            status_metrics["futures_realized"],
            status_metrics["futures_unrealized"],
            status_metrics["futures_open_positions"],
            status_metrics["spot_balance"],
            status_metrics["spot_realized"],
            status_metrics["spot_unrealized"],
            status_metrics["spot_open_positions"],
            status_metrics["mapped_orderflow_events"],
            status_metrics["unmapped_orderflow_events"],
            status_metrics["alias_cache_hits"],
            status_metrics["lazy_lookup_hits"],
            status_metrics["hot_window_markets"],
            status_metrics["hot_window_hits"],
            status_metrics["hot_window_promotions"],
            status_metrics["hot_window_expiries"],
            status_metrics["active_window_misses"],
            status_metrics["resolver_hit_rate"],
            status_metrics["active_window_miss_rate"],
            status_metrics["market_not_mapped_rate"],
            status_metrics["lookup_universe_markets"],
            status_metrics["lookup_universe_aliases"],
            status_metrics["persisted_market_alias_rows"],
            status_metrics["persisted_market_alias_markets"],
            status_metrics["hydrated_lookup_markets"],
            status_metrics["hydrated_lookup_aliases"],
            status_metrics["lookup_hydration_warning"],
            status_metrics["alias_persistence_gap"],
            status_metrics["graph_clusters_promoted"],
            status_metrics["graph_skipped_missing_market_ref"],
            status_metrics["graph_skipped_single_wallet"],
            status_metrics["graph_skipped_low_notional"],
            status_metrics["whale_copy_accumulator_buckets"],
            status_metrics["whale_copy_gate_ready_candidates"],
            status_metrics["whale_copy_retry_candidates"],
            status_metrics["whale_copy_accumulated_buy_events"],
            status_metrics["whale_copy_accumulated_total_notional"],
            status_metrics["sampling_mode"],
            status_metrics["sampling_closed_trades"],
            status_metrics["sampling_target_closed_trades"],
            status_metrics["sampling_stop_reason"],
            status_metrics["scan_time"],
        )

    async def run_activity_hunter_loop(self) -> None:
        if self.activity_hunter is None:
            return

        logger.info("Ghost Intelligence v4.0: Activity Hunter active.")
        async for event in self.activity_hunter.monitor_stream():
            if self.stop_event.is_set():
                break
            await self.handle_activity_event(event)

    def _resolve_binance_technical_symbols(self) -> Tuple[Dict[str, str], ...]:
        raw_symbols = list(self.settings.binance_technical_symbols or tuple())
        if not raw_symbols:
            raw_symbols = list(DEFAULT_BINANCE_TECHNICAL_SYMBOLS)

        resolved: list[Dict[str, str]] = []
        for raw in raw_symbols:
            token = str(raw or "").strip()
            if not token:
                continue
            if "/" in token:
                futures_symbol = token
                base_symbol = token.split("/")[0].upper()
            else:
                base_symbol = token.upper()
                futures_symbol = BINANCE_FUTURES_MAPPINGS.get(base_symbol, token)
            spot_symbol = EXCHANGE_MAPPINGS["binance"].get(base_symbol)
            if not spot_symbol and "/" in futures_symbol:
                fallback_base = futures_symbol.split("/")[0].upper()
                spot_symbol = EXCHANGE_MAPPINGS["binance"].get(fallback_base, f"{fallback_base}/USDT")
            resolved.append(
                {
                    "base_symbol": base_symbol,
                    "futures_symbol": futures_symbol,
                    "spot_symbol": spot_symbol or "",
                }
            )
        return tuple(resolved)

    async def _build_binance_technical_position_plan(
        self,
        venue_id: str,
        *,
        minimum_trade_size: float = 0.0,
    ) -> Dict[str, float | int | bool]:
        config = self.settings.venue_configs[venue_id]
        base_trade_size = min(config.max_order_usd, config.max_position_usd)
        if self.db is None:
            return {
                "base_trade_size": float(base_trade_size),
                "effective_trade_size": float(base_trade_size),
                "remaining_position_capacity_usd": float(config.max_position_usd),
                "position_capacity_sized_down": False,
                "open_position_count_at_decision": 0,
                "open_position_notional_usd_at_decision": 0.0,
                "minimum_trade_size": float(minimum_trade_size),
            }

        open_positions = await self.db.get_open_positions(venue=venue_id)
        open_position_count = len(open_positions)
        open_notional = sum(float(position["notional_usd"] or 0.0) for position in open_positions)
        remaining_capacity_usd = max(0.0, float(config.max_position_usd) - open_notional)
        effective_trade_size = min(float(base_trade_size), remaining_capacity_usd)
        position_capacity_sized_down = effective_trade_size < float(base_trade_size) and effective_trade_size >= float(minimum_trade_size)
        return {
            "base_trade_size": float(base_trade_size),
            "effective_trade_size": float(effective_trade_size),
            "remaining_position_capacity_usd": float(remaining_capacity_usd),
            "position_capacity_sized_down": bool(position_capacity_sized_down),
            "open_position_count_at_decision": int(open_position_count),
            "open_position_notional_usd_at_decision": float(open_notional),
            "minimum_trade_size": float(minimum_trade_size),
        }

    async def run_binance_technical_loop(self) -> None:
        if self.scanner is None:
            return

        logger.info("Ghost Intelligence v4.0: Binance technical sampler active.")
        while not self.stop_event.is_set():
            cycle_start = time.time()
            try:
                await self.process_binance_technical_signals()
            except Exception as exc:
                logger.info("[REJECT] venue=binance_futures source=binance_technical_momentum category=CRYPTO market=unknown reasons=technical_sampler_failed inputs=%s", {"error": str(exc)})
            elapsed = time.time() - cycle_start
            delay = max(0.0, self.settings.binance_technical_interval_seconds - elapsed)
            await asyncio.sleep(delay)

    async def process_binance_technical_signals(self) -> None:
        if self.scanner is None or self.binance_futures_venue is None:
            return
        if not self.settings.venue_configs["binance_futures"].enabled:
            return

        symbols = self._resolve_binance_technical_symbols()
        self.binance_technical_symbol_scope = tuple(entry["base_symbol"] for entry in symbols)
        if not symbols:
            return

        technical_min_trade_size = 25.0 if self.sample_kind == "live_paper" else 0.0

        for entry in symbols:
            futures_symbol = entry["futures_symbol"]
            spot_symbol = entry["spot_symbol"]
            futures_snapshot = await self.scanner.get_futures_market_snapshot(futures_symbol)
            if not futures_snapshot.get("is_valid"):
                rejection = self.decision_engine.reject(
                    DecisionInputs(
                        source="binance_technical_momentum",
                        category="CRYPTO",
                        market_id=futures_symbol,
                        volume_24h=float(futures_snapshot.get("volume_24h") or 0.0),
                        mid_price=futures_snapshot.get("mid_price"),
                        spread_pct=futures_snapshot.get("spread_pct"),
                        venue="binance_futures",
                        strategy_profile=STRATEGY_PROFILE_BINANCE_TECHNICAL,
                    ),
                    futures_snapshot.get("reason", "position_sync_failed"),
                )
                self.decision_engine.log_result(rejection, logger)
                continue

            futures_history = await self.scanner.get_futures_historical_data(
                futures_symbol,
                timeframe=self.settings.binance_technical_timeframe,
                limit=200,
            )
            closes = futures_history["close"] if not futures_history.empty and "close" in futures_history else pd.Series(dtype=float)
            volumes = futures_history["volume"] if not futures_history.empty and "volume" in futures_history else pd.Series(dtype=float)
            signal = self.technical_signal_engine.score(
                symbol=futures_symbol,
                closes=closes,
                volumes=volumes,
                spread_pct=float(futures_snapshot.get("spread_pct") or 0.0),
                volume_24h=float(futures_snapshot.get("volume_24h") or 0.0),
                min_score=self.settings.binance_technical_min_score,
                timeframe=self.settings.binance_technical_timeframe,
                paper_recovery=self.sample_kind == "live_paper",
                force_sample_enabled=self.settings.binance_technical_force_sample,
                force_min_score=self.settings.binance_technical_force_min_score,
            )

            now_ts = time.time()
            force_ready = (
                self.settings.binance_technical_force_sample
                and (now_ts - self.technical_force_last_ts) >= (self.settings.binance_technical_force_cooldown_minutes * 60.0)
                and signal.score >= self.settings.binance_technical_force_min_score
            )
            should_trade = signal.should_trade or force_ready
            if force_ready:
                self.technical_force_last_ts = now_ts

            futures_position_plan = await self._build_binance_technical_position_plan(
                "binance_futures",
                minimum_trade_size=technical_min_trade_size,
            )
            snapshot_inputs = {
                "snapshot_quality": futures_snapshot.get("snapshot_quality"),
                "spread_source": futures_snapshot.get("spread_source"),
                "bid_source": futures_snapshot.get("bid_source"),
                "ask_source": futures_snapshot.get("ask_source"),
                "orderbook_fallback_used": bool(futures_snapshot.get("orderbook_fallback_used")),
                "orderbook_repriced": bool(futures_snapshot.get("orderbook_repriced")),
                "raw_ticker_bid": futures_snapshot.get("raw_ticker_bid"),
                "raw_ticker_ask": futures_snapshot.get("raw_ticker_ask"),
                "raw_info_bid": futures_snapshot.get("raw_info_bid"),
                "raw_info_ask": futures_snapshot.get("raw_info_ask"),
                "snapshot_mid_price": futures_snapshot.get("mid_price"),
                "snapshot_mark_price": futures_snapshot.get("mark_price"),
                "snapshot_last_price": futures_snapshot.get("last_price"),
                "mark_mid_gap_pct": futures_snapshot.get("mark_mid_gap_pct"),
                "last_mid_gap_pct": futures_snapshot.get("last_mid_gap_pct"),
            }
            common_inputs_payload = {
                **signal.inputs,
                **snapshot_inputs,
                "symbol": futures_symbol,
                "force_sample": bool(force_ready),
                "force_sample_ready": bool(force_ready),
                "min_score": self.settings.binance_technical_min_score,
                "force_min_score": self.settings.binance_technical_force_min_score,
                "signal_direction": signal.direction,
                "technical_symbol_scope": list(self.binance_technical_symbol_scope),
                "technical_active_symbol_count": len(self.binance_technical_symbol_scope),
            }
            futures_inputs_payload = {
                **common_inputs_payload,
                **futures_position_plan,
            }
            futures_decision = DecisionResult(
                source="binance_technical_momentum",
                category="CRYPTO",
                market_id=futures_symbol,
                score=signal.score,
                threshold=signal.threshold,
                should_trade=bool(should_trade),
                reasons=[] if should_trade else signal.reasons,
                trade_size=float(futures_position_plan["effective_trade_size"]),
                inputs=futures_inputs_payload,
                venue="binance_futures",
                direction=signal.direction,
                strategy_profile=STRATEGY_PROFILE_BINANCE_TECHNICAL,
            )
            self.decision_engine.log_result(futures_decision, logger)
            if not should_trade or signal.direction == "NEUTRAL":
                continue

            await self.binance_futures_venue.place_entry_order(
                symbol=futures_symbol,
                side=signal.direction,
                entry_price=float(futures_snapshot.get("mark_price") or futures_snapshot.get("last_price") or 0.0),
                trade_size=float(futures_position_plan["effective_trade_size"]),
                signal_score=signal.score,
                source="binance_technical_momentum",
                source_signal="binance_technical_momentum",
                spread_pct=float(futures_snapshot.get("spread_pct") or 0.0),
                market_context={},
                strategy_profile=STRATEGY_PROFILE_BINANCE_TECHNICAL,
                audit_inputs=futures_inputs_payload,
                signal_threshold=(self.settings.binance_technical_force_min_score if force_ready else signal.threshold),
                min_trade_size=technical_min_trade_size,
            )

            if not self.settings.venue_configs["binance_spot"].enabled or self.binance_spot_venue is None:
                continue
            if not spot_symbol:
                continue
            spot_position_plan = await self._build_binance_technical_position_plan(
                "binance_spot",
                minimum_trade_size=technical_min_trade_size,
            )
            spot_inputs_payload = {
                **common_inputs_payload,
                "symbol": spot_symbol,
                **spot_position_plan,
            }
            if signal.direction != "LONG":
                spot_reject = DecisionResult(
                    source="binance_technical_momentum",
                    category="CRYPTO",
                    market_id=spot_symbol,
                    score=signal.score,
                    threshold=signal.threshold,
                    should_trade=False,
                    reasons=["spot_short_not_supported"],
                    trade_size=float(spot_position_plan["effective_trade_size"]),
                    inputs=spot_inputs_payload,
                    venue="binance_spot",
                    direction=signal.direction,
                    strategy_profile=STRATEGY_PROFILE_BINANCE_TECHNICAL,
                )
                self.decision_engine.log_result(spot_reject, logger)
                continue

            spot_snapshot = await self.scanner.get_spot_market_snapshot(spot_symbol)
            if not spot_snapshot.get("is_valid"):
                spot_reject = DecisionResult(
                    source="binance_technical_momentum",
                    category="CRYPTO",
                    market_id=spot_symbol,
                    score=signal.score,
                    threshold=signal.threshold,
                    should_trade=False,
                    reasons=[spot_snapshot.get("reason", "exchange_filters_rejected")],
                    trade_size=float(spot_position_plan["effective_trade_size"]),
                    inputs=spot_inputs_payload,
                    venue="binance_spot",
                    direction=signal.direction,
                    strategy_profile=STRATEGY_PROFILE_BINANCE_TECHNICAL,
                )
                self.decision_engine.log_result(spot_reject, logger)
                continue

            spot_decision = DecisionResult(
                source="binance_technical_momentum",
                category="CRYPTO",
                market_id=spot_symbol,
                score=signal.score,
                threshold=signal.threshold,
                should_trade=bool(should_trade),
                reasons=[] if should_trade else signal.reasons,
                trade_size=float(spot_position_plan["effective_trade_size"]),
                inputs=spot_inputs_payload,
                venue="binance_spot",
                direction=signal.direction,
                strategy_profile=STRATEGY_PROFILE_BINANCE_TECHNICAL,
            )
            self.decision_engine.log_result(spot_decision, logger)
            if should_trade:
                await self.binance_spot_venue.place_entry_order(
                    symbol=spot_symbol,
                    side="LONG",
                    entry_price=float(spot_snapshot.get("last_price") or 0.0),
                    trade_size=float(spot_position_plan["effective_trade_size"]),
                    signal_score=signal.score,
                    source="binance_technical_momentum",
                    source_signal="binance_technical_momentum",
                    spread_pct=float(spot_snapshot.get("spread_pct") or 0.0),
                    market_context={},
                    strategy_profile=STRATEGY_PROFILE_BINANCE_TECHNICAL,
                    audit_inputs=spot_inputs_payload,
                    signal_threshold=(self.settings.binance_technical_force_min_score if force_ready else signal.threshold),
                    min_trade_size=technical_min_trade_size,
                )

    async def run_whale_tracker_loop(self) -> None:
        if self.whale_tracker is None:
            return

        logger.info("Ghost Intelligence v4.0: Whale Tracker active.")
        async for action in self.whale_tracker.monitor_whale_activity():
            if self.stop_event.is_set():
                break
            await self.handle_whale_action(action)

    async def process_market(self, market: Dict) -> None:
        category = market.get("category", classify_market_category(market.get("question", "")))
        if category != "CRYPTO":
            inputs = DecisionInputs(
                source="discovery",
                category=category,
                market_id=market.get("market_id", ""),
                token_id=market.get("token_id"),
                question=market.get("question", ""),
                volume_24h=float(market.get("volume_24h", 0.0)),
                venue="polymarket",
            )
            self.decision_engine.log_result(self.decision_engine.reject(inputs, "route_whale_orderflow_only"), logger)
            return

        await self.process_crypto_market(market)

    async def process_crypto_market(self, market: Dict) -> None:
        if self.scanner is None or self.polymarket_venue is None:
            return

        market_id = market.get("market_id", "")
        token_id = market.get("token_id")
        question = market.get("question", "")
        volume_24h = float(market.get("volume_24h", 0.0))

        base_inputs = DecisionInputs(
            source="discovery",
            category="CRYPTO",
            market_id=market_id,
            token_id=token_id,
            question=question,
            volume_24h=volume_24h,
            venue="polymarket",
        )

        if not token_id:
            self.decision_engine.log_result(self.decision_engine.reject(base_inputs, "market_not_mapped"), logger)
            return

        polymarket_snapshot = await self.scanner.get_orderbook_snapshot(token_id)
        if not polymarket_snapshot["is_valid"]:
            rejection = self.decision_engine.reject(
                DecisionInputs(**{**base_inputs.__dict__, "mid_price": polymarket_snapshot.get("mid_price"), "spread_pct": polymarket_snapshot.get("spread_pct")}),
                polymarket_snapshot.get("reason", "invalid_orderbook_data"),
            )
            self.decision_engine.log_result(rejection, logger)
            return

        spot_symbol = resolve_crypto_symbol(question, self.settings.exchange_id)
        venue_spot_symbol = resolve_crypto_symbol(question, "binance")
        futures_symbol = resolve_binance_futures_symbol(question)
        if not spot_symbol or not futures_symbol or not venue_spot_symbol:
            self.decision_engine.log_result(self.decision_engine.reject(base_inputs, "market_not_mapped"), logger)
            return

        current_exchange_price = self.scanner.current_prices.get(spot_symbol)
        if current_exchange_price is None:
            current_exchange_price = await self.scanner.refresh_symbol_price(spot_symbol)
        if current_exchange_price is None:
            rejection = self.decision_engine.reject(
                DecisionInputs(**{**base_inputs.__dict__, "mid_price": polymarket_snapshot["mid_price"], "spread_pct": polymarket_snapshot["spread_pct"]}),
                "missing_exchange_price",
            )
            self.decision_engine.log_result(rejection, logger)
            return

        historical_data = await self.scanner.get_historical_data(spot_symbol)
        if historical_data.empty or "close" not in historical_data:
            rejection = self.decision_engine.reject(
                DecisionInputs(**{**base_inputs.__dict__, "mid_price": polymarket_snapshot["mid_price"], "spread_pct": polymarket_snapshot["spread_pct"]}),
                "missing_exchange_price",
            )
            self.decision_engine.log_result(rejection, logger)
            return

        strike_price, expiry_dt = parse_polymarket_question(question)
        if not strike_price:
            rejection = self.decision_engine.reject(
                DecisionInputs(**{**base_inputs.__dict__, "mid_price": polymarket_snapshot["mid_price"], "spread_pct": polymarket_snapshot["spread_pct"]}),
                "market_not_mapped",
            )
            self.decision_engine.log_result(rejection, logger)
            return

        now = datetime.now(timezone.utc)
        if expiry_dt is None or expiry_dt <= now:
            rejection = self.decision_engine.reject(
                DecisionInputs(**{**base_inputs.__dict__, "mid_price": polymarket_snapshot["mid_price"], "spread_pct": polymarket_snapshot["spread_pct"]}),
                "market_expired",
            )
            self.decision_engine.log_result(rejection, logger)
            return

        spot_volatility = calculate_annualized_volatility(historical_data["close"])
        # Keep the triple-venue proof deterministic by capping debug volatility.
        if self.settings.debug_signal_mode and self.settings.debug_signal_profile == "crypto_triple_long":
            spot_volatility = min(spot_volatility, 0.65)
        time_to_expiry_years = (expiry_dt - now).total_seconds() / (24 * 365 * 3600)
        implied_probability = calculate_black_scholes_prob(
            current_exchange_price,
            strike_price,
            time_to_expiry_years,
            spot_volatility,
        )
        edge = calculate_edge(polymarket_snapshot["mid_price"], implied_probability)
        rsi_series = calculate_rsi(historical_data["close"])
        rsi_value = rsi_series.iloc[-1] if not rsi_series.empty else None
        if pd.isna(rsi_value):
            rsi_value = None

        discovery_decision = self.decision_engine.score_discovery(
            DecisionInputs(
                source="discovery",
                category="CRYPTO",
                market_id=market_id,
                token_id=token_id,
                question=question,
                volume_24h=volume_24h,
                mid_price=polymarket_snapshot["mid_price"],
                spread_pct=polymarket_snapshot["spread_pct"],
                edge=edge,
                rsi=float(rsi_value) if rsi_value is not None else None,
                venue="polymarket",
            )
        )

        futures_snapshot = await self.scanner.get_futures_market_snapshot(futures_symbol)
        if not futures_snapshot.get("is_valid"):
            futures_rejection = self.decision_engine.reject(
                DecisionInputs(
                    source="binance_futures_price_structure",
                    category="CRYPTO",
                    market_id=futures_symbol,
                    token_id=token_id,
                    question=question,
                    volume_24h=volume_24h,
                    venue="binance_futures",
                ),
                futures_snapshot.get("reason", "position_sync_failed"),
            )
            self.decision_engine.log_result(futures_rejection, logger)
            self.decision_engine.log_result(discovery_decision, logger)
            return

        futures_history = await self.scanner.get_futures_historical_data(futures_symbol)
        volatility_source = futures_history["close"] if not futures_history.empty and "close" in futures_history else historical_data["close"]
        futures_volatility = calculate_annualized_volatility(volatility_source)
        if self.settings.debug_signal_mode and self.settings.debug_signal_profile == "crypto_triple_long":
            futures_volatility = min(futures_volatility, 0.65)

        orderflow_hint = self._get_crypto_orderflow_hint(futures_symbol)
        shared_signal = self.crypto_signal_engine.score(
            CryptoSignalInputs(
                market_id=market_id,
                token_id=token_id,
                question=question,
                volume_24h=volume_24h,
                polymarket_mid_price=polymarket_snapshot["mid_price"],
                polymarket_spread_pct=polymarket_snapshot["spread_pct"],
                spot_price=current_exchange_price,
                futures_symbol=futures_symbol,
                futures_last_price=float(futures_snapshot["last_price"]),
                futures_mark_price=float(futures_snapshot["mark_price"]),
                futures_spread_pct=float(futures_snapshot["spread_pct"]),
                futures_volume_24h=float(futures_snapshot["volume_24h"]),
                funding_rate=float(futures_snapshot["funding_rate"]),
                open_interest=float(futures_snapshot["open_interest"]),
                volatility=futures_volatility,
                strike_price=strike_price,
                expiry_dt=expiry_dt,
                orderflow_bias=float(orderflow_hint.get("bias", 0.0)),
                orderflow_notional=float(orderflow_hint.get("notional", 0.0)),
            )
        )

        polymarket_decision = self.crypto_signal_engine.build_polymarket_decision(
            shared_signal,
            self.settings.venue_configs["polymarket"],
            discovery_decision,
        )
        self.decision_engine.log_result(polymarket_decision, logger)

        if polymarket_decision.should_trade:
            await self.polymarket_venue.place_entry_order(
                market_id=market_id,
                side=shared_signal.polymarket_side,
                size_usd=polymarket_decision.trade_size,
                price=polymarket_snapshot["mid_price"],
                edge=shared_signal.edge,
                confidence=polymarket_decision.score,
                source="blended_crypto",
                category="CRYPTO",
                source_signal="blended_crypto",
                entry_spread_pct=polymarket_snapshot["spread_pct"],
                slippage_proxy_bps=slippage_proxy_bps_from_spread(polymarket_snapshot["spread_pct"]),
                whale_trust_at_entry=0.5,
            )

        if not self.settings.venue_configs["binance_futures"].enabled or self.binance_futures_venue is None:
            return

        open_position = await self.binance_futures_venue.get_open_position(futures_symbol)
        if open_position is not None and open_position["side"] != shared_signal.futures_side and shared_signal.should_trade:
            await self.binance_futures_venue.place_exit_order(
                futures_symbol,
                exit_price=float(futures_snapshot["mark_price"]),
                reason="signal_exit",
                source="binance_futures_price_structure",
            )
            open_position = None

        risk_reasons = []
        trade_size = min(
            self.settings.venue_configs["binance_futures"].max_order_usd,
            self.settings.venue_configs["binance_futures"].max_position_usd,
        )
        if open_position is not None:
            risk_reasons.append("duplicate_open_trade")
        else:
            risk_reasons = await self.binance_futures_venue.risk_manager.validate_entry(
                symbol=futures_symbol,
                side=shared_signal.futures_side,
                price=float(futures_snapshot["mark_price"]),
                trade_size=trade_size,
                spread_pct=float(futures_snapshot["spread_pct"]),
                signal_score=shared_signal.score,
            )

        futures_decision = self.crypto_signal_engine.build_binance_futures_decision(
            shared_signal,
            self.settings.venue_configs["binance_futures"],
            risk_reasons,
            trade_size=trade_size,
        )
        self.decision_engine.log_result(futures_decision, logger)

        if futures_decision.should_trade:
            await self.binance_futures_venue.place_entry_order(
                symbol=futures_symbol,
                side=shared_signal.futures_side,
                entry_price=float(futures_snapshot["mark_price"]),
                trade_size=trade_size,
                signal_score=futures_decision.score,
                source="binance_futures_price_structure",
                source_signal="binance_futures_price_structure",
                spread_pct=float(futures_snapshot["spread_pct"]),
                market_context=market,
                whale_trust_at_entry=0.5,
            )

        if not self.settings.venue_configs["binance_spot"].enabled or self.binance_spot_venue is None:
            return

        spot_snapshot = await self.scanner.get_spot_market_snapshot(venue_spot_symbol)
        spot_trade_size = min(
            self.settings.venue_configs["binance_spot"].max_order_usd,
            self.settings.venue_configs["binance_spot"].max_position_usd,
        )
        open_spot_position = await self.binance_spot_venue.get_open_position(venue_spot_symbol)
        spot_risk_reasons = []

        if not spot_snapshot.get("is_valid"):
            spot_risk_reasons.append(spot_snapshot.get("reason", "exchange_filters_rejected"))
        elif shared_signal.direction == "LONG":
            if open_spot_position is not None:
                spot_risk_reasons.append("duplicate_open_trade")
            else:
                spot_risk_reasons = await self.binance_spot_venue.risk_manager.validate_entry(
                    symbol=venue_spot_symbol,
                    side="LONG",
                    price=float(spot_snapshot["last_price"]),
                    trade_size=spot_trade_size,
                    spread_pct=float(spot_snapshot["spread_pct"]),
                    signal_score=shared_signal.score,
                )
        elif open_spot_position is None:
            spot_risk_reasons.append("spot_short_not_supported")

        spot_decision = self.crypto_signal_engine.build_binance_spot_decision(
            shared_signal,
            self.settings.venue_configs["binance_spot"],
            spot_risk_reasons,
            trade_size=spot_trade_size,
            has_open_position=open_spot_position is not None,
        )
        self.decision_engine.log_result(spot_decision, logger)

        if not spot_decision.should_trade:
            return

        if shared_signal.direction == "LONG":
            await self.binance_spot_venue.place_entry_order(
                symbol=venue_spot_symbol,
                side="LONG",
                entry_price=float(spot_snapshot["last_price"]),
                trade_size=spot_trade_size,
                signal_score=spot_decision.score,
                source="binance_spot_price_structure",
                source_signal="binance_spot_price_structure",
                spread_pct=float(spot_snapshot["spread_pct"]),
                market_context=market,
                whale_trust_at_entry=0.5,
            )
            return

        await self.binance_spot_venue.place_exit_order(
            venue_spot_symbol,
            exit_price=float(spot_snapshot["last_price"]),
            reason="signal_exit",
            source="binance_spot_price_structure",
        )

    async def handle_whale_action(self, action: Dict) -> None:
        if self.copy_trader is None:
            return
        await self.refresh_sampling_state()
        self._record_crypto_orderflow_hint(action, source="whale_tracker")
        await self.copy_trader.evaluate_signal(action)

    async def handle_activity_event(self, event: Dict) -> None:
        if self.copy_trader is None:
            return
        await self.refresh_sampling_state()
        self._record_crypto_orderflow_hint(event, source=event.get("source", "activity"))
        await self.copy_trader.evaluate_activity_event(event)

    def _record_crypto_orderflow_hint(self, event: Dict, source: str) -> None:
        token_id = event.get("token_id")
        market_id = event.get("market_id")
        alias_candidates = collect_alias_candidates(
            market_id,
            token_id,
            extra=event.get("alias_candidates"),
        )
        context = self._resolve_market_context(market_id=market_id, token_id=token_id, alias_candidates=alias_candidates)
        if context is None or context.get("category") != "CRYPTO":
            return
        futures_symbol = resolve_binance_futures_symbol(context.get("question", ""))
        if not futures_symbol:
            return
        side = str(event.get("side", "BUY")).upper()
        self.crypto_orderflow_hints[futures_symbol] = {
            "bias": 1.0 if side == "BUY" else -1.0,
            "notional": float(event.get("amount", 0.0) or 0.0),
            "source": source,
            "timestamp": time.time(),
        }

    def _get_crypto_orderflow_hint(self, futures_symbol: str) -> Dict:
        hint = self.crypto_orderflow_hints.get(futures_symbol)
        if not hint:
            return {}
        if time.time() - float(hint.get("timestamp", 0.0)) > 300:
            return {}
        return hint

    def _build_trade_market_universe(self, active_markets: list[Dict]) -> list[Dict]:
        trade_markets: list[Dict] = []
        seen_market_ids: set[str] = set()

        for market in active_markets:
            normalized_market_id = normalize_market_alias(market.get("market_id"))
            if not normalized_market_id or normalized_market_id in seen_market_ids:
                continue
            seen_market_ids.add(normalized_market_id)
            trade_markets.append(market)

        for market_id, market in self.hot_window_market_context.items():
            if market_id in seen_market_ids:
                continue
            trade_markets.append(dict(market))

        return trade_markets

    def _resolve_market_context(
        self,
        market_id: Optional[str],
        token_id: Optional[str],
        alias_candidates: Optional[list[str]] = None,
    ) -> Optional[Dict]:
        normalized_market_id = normalize_market_alias(market_id)
        normalized_token_id = normalize_market_alias(token_id)
        if normalized_market_id and normalized_market_id in self.lookup_market_context:
            return self.lookup_market_context[normalized_market_id]
        if normalized_token_id and normalized_token_id in self.lookup_token_to_market_id:
            resolved_market_id = self.lookup_token_to_market_id[normalized_token_id]
            return self.lookup_market_context.get(resolved_market_id)
        for alias in alias_candidates or ():
            normalized_alias = normalize_market_alias(alias)
            if not normalized_alias:
                continue
            if normalized_alias in self.lookup_market_context:
                return self.lookup_market_context[normalized_alias]
            if normalized_alias in self.lookup_token_to_market_id:
                resolved_market_id = self.lookup_token_to_market_id[normalized_alias]
                return self.lookup_market_context.get(resolved_market_id)
        return None

    def resolve_lookup_market_context(self, alias_candidates: list[str]) -> Optional[Dict]:
        return self._resolve_market_context(None, None, alias_candidates)

    async def _refresh_active_market_context(self, active_markets: list[Dict]) -> None:
        self.active_market_context = {}
        self.token_to_market_id = {}

        for market in active_markets[: self.settings.market_limit]:
            normalized_market = self._normalize_market_context(market)
            if normalized_market is None:
                continue
            normalized_market["trade_context_source"] = "active_context"
            self._cache_market_context(normalized_market, self.active_market_context, self.token_to_market_id)
        self._publish_trade_market_contexts()

    async def _refresh_lookup_context(self, market_universe: list[Dict], source: str, *, reset: bool = True) -> None:
        if reset:
            self.lookup_market_context = {}
            self.lookup_token_to_market_id = {}

        for market in market_universe:
            await self._register_market_context(
                market,
                source=source,
                context_store=self.lookup_market_context,
                token_store=self.lookup_token_to_market_id,
            )

    async def _hydrate_lookup_context_from_db(self, *, reset: bool = True) -> int:
        if self.db is None:
            return 0
        persisted_universe = await self.db.get_persisted_lookup_universe(limit_markets=self.settings.market_lookup_limit)
        await self._refresh_lookup_context(persisted_universe, source="persisted_alias_replay", reset=reset)
        self.hydrated_lookup_markets = len(self.lookup_market_context)
        self.hydrated_lookup_aliases = self._count_lookup_universe_aliases()
        alias_integrity = await self.db.get_market_alias_integrity()
        persisted_market_alias_rows = int(alias_integrity["alias_rows"] or 0) if alias_integrity else 0
        if persisted_market_alias_rows > 0 and (
            self.hydrated_lookup_markets == 0 or self.hydrated_lookup_aliases == 0
        ):
            self.lookup_hydration_warning = "persisted_alias_rows_present_but_lookup_hydration_zero"
        else:
            self.lookup_hydration_warning = None
        return len(persisted_universe)

    async def _register_market_context(
        self,
        market: Dict,
        source: str,
        *,
        context_store: Optional[Dict[str, Dict]] = None,
        token_store: Optional[Dict[str, str]] = None,
    ) -> Optional[Dict]:
        normalized_market = self._normalize_market_context(market)
        if normalized_market is None:
            return None

        self._cache_market_context(
            normalized_market,
            context_store if context_store is not None else self.lookup_market_context,
            token_store if token_store is not None else self.lookup_token_to_market_id,
        )

        if self.db is not None:
            await self.db.upsert_market_aliases(
                market_id=normalized_market["market_id"],
                aliases=normalized_market["alias_candidates"],
                question=normalized_market.get("question"),
                category=normalized_market.get("category"),
                volume_24h=float(normalized_market.get("volume_24h", 0.0) or 0.0),
                active=bool(normalized_market.get("active", True)),
                source=source,
            )
        return normalized_market

    def _normalize_market_context(self, market: Dict) -> Optional[Dict]:
        market_id = normalize_market_alias(market.get("market_id"))
        if not market_id:
            return None

        normalized_market = dict(market)
        normalized_market["market_id"] = market_id
        normalized_market["category"] = market.get("category", classify_market_category(market.get("question", "")))
        normalized_market["active"] = bool(market.get("active", True))
        alias_candidates = build_market_aliases(
            market_id=market_id,
            token_id=market.get("token_id"),
            token_ids=market.get("token_ids", []),
            extra_aliases=[
                *(market.get("alias_candidates", []) or []),
                market.get("asset"),
                market.get("conditionId"),
                market.get("slug"),
            ],
        )
        normalized_market["alias_candidates"] = alias_candidates
        return normalized_market

    def _cache_market_context(
        self,
        normalized_market: Dict,
        context_store: Dict[str, Dict],
        token_store: Dict[str, str],
    ) -> None:
        market_id = normalized_market["market_id"]
        context_store[market_id] = dict(normalized_market)
        for alias in normalized_market["alias_candidates"]:
            if alias == market_id:
                continue
            token_store[alias] = market_id

    async def lazy_resolve_market_context(self, alias_candidates: list[str], source: str) -> Optional[Dict]:
        if self.explorer is None:
            return None

        market = await self.explorer.find_market_by_alias(
            alias_candidates,
            limit=max(self.settings.market_limit, self.settings.market_lookup_limit),
        )
        if market is None:
            await self._hydrate_lookup_context_from_db(reset=True)
            refreshed_market_universe = await self.explorer.fetch_market_lookup_universe(limit=self.settings.market_lookup_limit)
            await self._refresh_lookup_context(refreshed_market_universe, source="lazy_lookup_refresh", reset=False)
            self.last_lookup_refresh_ts = time.time()
            market = self._resolve_market_context(None, None, alias_candidates)
            if market is not None:
                return market
            market = await self.explorer.find_market_by_alias(
                alias_candidates,
                limit=self.settings.market_lookup_limit,
            )
            if market is None:
                return None

        normalized_market = await self._register_market_context(
            market,
            source="lazy_lookup",
            context_store=self.lookup_market_context,
            token_store=self.lookup_token_to_market_id,
        )
        if normalized_market is None:
            return None
        return normalized_market

    async def promote_hot_window_market(self, *, context: Dict, event: Dict, source: str, stage: str) -> bool:
        if not self.settings.orderflow_hot_window_enabled:
            return False
        if not context or not bool(context.get("active", True)):
            return False
        if not event_is_meaningful_for_lazy_lookup(event):
            return False

        market_id = normalize_market_alias(context.get("market_id"))
        if not market_id or market_id in self.active_market_context:
            return False

        hot_context = dict(context)
        hot_context["trade_context_source"] = "hot_window"
        hot_context["hot_window_source"] = source
        hot_context["hot_window_stage"] = stage
        hot_context["hot_window_promoted_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        hot_window_limit = self.settings.orderflow_hot_window_limit
        hot_window_ttl_seconds = self.settings.orderflow_hot_window_ttl_seconds
        if self.sampling_enabled:
            hot_window_limit = max(hot_window_limit, 300)
            hot_window_ttl_seconds = max(hot_window_ttl_seconds, 1800.0)

        self.hot_window_market_context[market_id] = hot_context
        self.hot_window_expiries[market_id] = time.time() + hot_window_ttl_seconds
        if self.db is not None:
            await self.db.upsert_market_aliases(
                market_id=market_id,
                aliases=collect_alias_candidates(
                    market_id,
                    context.get("token_id"),
                    event.get("asset"),
                    event.get("conditionId"),
                    event.get("slug"),
                    extra=context.get("alias_candidates", []),
                ),
                question=hot_context.get("question"),
                category=hot_context.get("category"),
                volume_24h=float(hot_context.get("volume_24h", 0.0) or 0.0),
                active=bool(hot_context.get("active", True)),
                source="hot_window_promotion",
            )

        while len(self.hot_window_market_context) > hot_window_limit:
            oldest_market_id = min(
                self.hot_window_expiries,
                key=lambda key: (self.hot_window_expiries.get(key, 0.0), key),
            )
            self.hot_window_market_context.pop(oldest_market_id, None)
            self.hot_window_expiries.pop(oldest_market_id, None)
            self.hot_window_expiry_events += 1

        self._rebuild_hot_window_token_index()
        self._publish_trade_market_contexts()
        self.hot_window_promotions += 1
        return True

    async def _prune_hot_window(self) -> None:
        if not self.hot_window_market_context:
            return

        now_ts = time.time()
        expired_market_ids = [
            market_id
            for market_id, expires_at in self.hot_window_expiries.items()
            if expires_at <= now_ts or not bool(self.hot_window_market_context.get(market_id, {}).get("active", True))
        ]
        if not expired_market_ids:
            return

        for market_id in expired_market_ids:
            self.hot_window_market_context.pop(market_id, None)
            self.hot_window_expiries.pop(market_id, None)
            self.hot_window_expiry_events += 1

        self._rebuild_hot_window_token_index()
        self._publish_trade_market_contexts()

    def _rebuild_hot_window_token_index(self) -> None:
        self.hot_window_token_to_market_id = {}
        for market_id, market in self.hot_window_market_context.items():
            for alias in market.get("alias_candidates", []):
                normalized_alias = normalize_market_alias(alias)
                if normalized_alias and normalized_alias != market_id:
                    self.hot_window_token_to_market_id[normalized_alias] = market_id

    def _publish_trade_market_contexts(self) -> None:
        if self.copy_trader is None:
            return

        published_market_context: Dict[str, Dict] = {}
        published_token_index: Dict[str, str] = {}

        for market_id, market in self.active_market_context.items():
            published_market_context[market_id] = dict(market)
            for alias in market.get("alias_candidates", []):
                normalized_alias = normalize_market_alias(alias)
                if normalized_alias and normalized_alias != market_id:
                    published_token_index[normalized_alias] = market_id

        for market_id, market in self.hot_window_market_context.items():
            if market_id in published_market_context:
                continue
            published_market_context[market_id] = dict(market)
            for alias in market.get("alias_candidates", []):
                normalized_alias = normalize_market_alias(alias)
                if normalized_alias and normalized_alias != market_id and normalized_alias not in published_token_index:
                    published_token_index[normalized_alias] = market_id

        self.copy_trader.update_market_contexts(published_market_context, published_token_index)

    async def record_mapping_event(self, *, mapped: bool, stage: str) -> None:
        if mapped:
            self.mapped_orderflow_events += 1
            if stage == "alias_cache":
                self.alias_cache_hits += 1
            elif stage in {"lazy_lookup", "unresolved_retry", "lookup_universe"}:
                self.lazy_lookup_hits += 1
            elif stage == "hot_window":
                self.hot_window_hits += 1
            return
        if stage == "active_window":
            self.active_window_misses += 1
        self.unmapped_orderflow_events += 1

    def _count_lookup_universe_aliases(self) -> int:
        return len(set(self.lookup_market_context.keys()) | set(self.lookup_token_to_market_id.keys()))

    async def _sleep_or_stop(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self.stop_event.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            return
