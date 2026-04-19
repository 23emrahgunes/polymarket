from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from time import sleep, time
from typing import Any

from .config import BinanceTechnicalSettings
from .provider import BinanceTechnicalMarketDataProvider, CCXTBinanceMarketDataProvider, MarketFrame
from .repository import BinanceTechnicalRepository
from .signal_engine import BinanceTechnicalSignalEngine, TechnicalSignal
from .service import BinanceTechnicalService


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%d %H:%M:%S")


def _base_symbol(symbol_or_market_id: str) -> str:
    normalized = (symbol_or_market_id or "").strip().upper()
    if not normalized:
        return ""
    return normalized.split("/")[0].split(":")[0]


@dataclass(slots=True)
class BinanceTechnicalRuntime:
    settings: BinanceTechnicalSettings
    repository: BinanceTechnicalRepository | None = None
    provider: BinanceTechnicalMarketDataProvider | None = None
    signal_engine: BinanceTechnicalSignalEngine | None = None
    service: BinanceTechnicalService = field(init=False)
    technical_force_last_ts: float = field(init=False, default=0.0)

    def __post_init__(self) -> None:
        self.repository = self.repository or BinanceTechnicalRepository(self.settings.db_path)
        self.provider = self.provider or CCXTBinanceMarketDataProvider()
        self.signal_engine = self.signal_engine or BinanceTechnicalSignalEngine()
        self.repository.ensure_tables()
        self.repository.ensure_runtime_rows(self.settings.enabled_venues)
        self.service = BinanceTechnicalService(self.settings, self.repository)

    def build_summary(self) -> dict[str, Any]:
        return self.service.build_summary()

    def run_once(self) -> dict[str, Any]:
        frames = self._fetch_frames()
        self._sync_open_positions(frames)
        self._evaluate_entries(frames)
        self.repository.refresh_account_snapshots(self.settings.enabled_venues)
        summary = self.service.build_summary()
        self._write_runtime_snapshot(summary)
        return summary

    def run_loop(self, iterations: int | None = None) -> dict[str, Any]:
        completed = 0
        last_summary = self.build_summary()
        while iterations is None or completed < iterations:
            last_summary = self.run_once()
            completed += 1
            if iterations is not None and completed >= iterations:
                break
            sleep(max(self.settings.loop_interval_seconds, 1))
        return last_summary

    def _fetch_frames(self) -> dict[tuple[str, str], MarketFrame]:
        frames: dict[tuple[str, str], MarketFrame] = {}
        for venue in self.settings.enabled_venues:
            for symbol in self.settings.symbols:
                frame = self.provider.fetch_market_frame(
                    symbol=symbol,
                    venue=venue,
                    timeframe=self.settings.timeframe,
                    ohlcv_limit=self.settings.ohlcv_limit,
                )
                frames[(venue, frame.symbol.upper())] = frame
        return frames

    def _frame_for_position(self, position: Any, frames: dict[tuple[str, str], MarketFrame]) -> MarketFrame | None:
        venue = str(position["venue"] or "")
        symbol = _base_symbol(str(position["symbol_or_market_id"] or ""))
        return frames.get((venue, symbol))

    def _sync_open_positions(self, frames: dict[tuple[str, str], MarketFrame]) -> None:
        now = _utc_now()
        open_positions = self.repository.fetch_open_positions_for_venues(self.settings.enabled_venues)
        for row in open_positions:
            frame = self._frame_for_position(row, frames)
            if frame is None or not frame.snapshot.is_valid:
                continue

            mark_price = float(
                frame.snapshot.mark_price
                or frame.snapshot.last_price
                or frame.snapshot.mid_price
                or frame.snapshot.best_bid
                or frame.snapshot.best_ask
                or row["mark_price"]
                or row["entry_price"]
                or 0.0
            )
            if mark_price <= 0:
                continue

            qty = float(row["qty"] or 0.0)
            entry_price = float(row["entry_price"] or 0.0)
            direction = str(row["side"] or "LONG").upper()
            notional_usd = qty * mark_price
            unrealized_pnl = ((mark_price - entry_price) * qty) if direction == "LONG" else ((entry_price - mark_price) * qty)
            self.repository.update_open_position_mark(
                position_id=int(row["id"]),
                mark_price=mark_price,
                notional_usd=notional_usd,
                unrealized_pnl=unrealized_pnl,
            )

            take_profit_price = float(row["take_profit_price"] or 0.0)
            stop_loss_price = float(row["stop_loss_price"] or 0.0)
            exit_reason = None
            if direction == "LONG":
                if take_profit_price > 0 and mark_price >= take_profit_price:
                    exit_reason = "TAKE_PROFIT"
                elif stop_loss_price > 0 and mark_price <= stop_loss_price:
                    exit_reason = "STOP_LOSS"
            elif direction == "SHORT":
                if take_profit_price > 0 and mark_price <= take_profit_price:
                    exit_reason = "TAKE_PROFIT"
                elif stop_loss_price > 0 and mark_price >= stop_loss_price:
                    exit_reason = "STOP_LOSS"

            if exit_reason is None:
                continue

            closed = self.repository.close_position(
                position_id=int(row["id"]),
                close_price=mark_price,
                closed_reason=exit_reason,
                closed_at=now,
            )
            if closed is None:
                continue

            exit_inputs = {
                "symbol": _base_symbol(str(row["symbol_or_market_id"] or "")),
                "signal_direction": direction,
                "entry_price": entry_price,
                "close_price": round(mark_price, 6),
                "qty": round(qty, 8),
                "take_profit_price": take_profit_price,
                "stop_loss_price": stop_loss_price,
                "exit_trigger": exit_reason,
            }
            self.repository.insert_decision_audit(
                venue=str(row["venue"] or ""),
                market_id=str(row["symbol_or_market_id"] or ""),
                action="exit",
                reason=exit_reason,
                decision_score=float(row["mark_price"] or mark_price),
                threshold=0.0,
                trade_size=float(row["notional_usd"] or notional_usd),
                inputs=exit_inputs,
                confidence=float(abs(unrealized_pnl)),
            )

    def _evaluate_entries(self, frames: dict[tuple[str, str], MarketFrame]) -> None:
        for venue in self.settings.enabled_venues:
            instrument_type = "futures" if venue == "binance_futures" else "spot"
            leverage = 2.0 if venue == "binance_futures" else 1.0
            for symbol in self.settings.symbols:
                frame = frames.get((venue, symbol.upper()))
                if frame is None:
                    continue

                signal = self.signal_engine.score(
                    symbol=symbol,
                    closes=frame.closes,
                    volumes=frame.volumes,
                    snapshot=frame.snapshot,
                    min_score=self.settings.score_threshold,
                    timeframe=self.settings.timeframe,
                    paper_recovery=self.settings.paper_recovery_enabled,
                    force_sample_enabled=self.settings.force_sample_enabled,
                    force_min_score=self.settings.force_min_score,
                )

                position_plan = self._build_position_plan(venue)
                current_position = self.repository.fetch_open_position(venue, frame.snapshot.symbol)
                force_ready = self._force_ready(signal)
                signal_threshold = self.settings.force_min_score if force_ready else signal.threshold
                base_inputs = self._build_entry_inputs(
                    frame=frame,
                    signal=signal,
                    position_plan=position_plan,
                    force_ready=force_ready,
                    signal_threshold=signal_threshold,
                )

                reasons = list(signal.reasons)
                if current_position is not None:
                    reasons.append("duplicate_open_trade")
                if signal.direction == "SHORT" and venue == "binance_spot":
                    reasons.append("spot_short_not_supported")
                if position_plan["open_position_count_at_decision"] >= self.settings.max_open_positions:
                    reasons.append("max_open_positions_exceeded")
                elif position_plan["effective_trade_size"] < self.settings.min_trade_size_usd:
                    reasons.append("max_total_position_usd_exceeded")
                elif self.settings.max_order_usd < self.settings.min_trade_size_usd:
                    reasons.append("max_order_usd_exceeded")

                hard_reasons = [reason for reason in reasons if reason != "score_below_threshold"]
                if signal.direction not in {"LONG", "SHORT"}:
                    hard_reasons = list(dict.fromkeys(hard_reasons + ["technical_alignment_weak"]))
                should_trade = (signal.should_trade or force_ready) and not hard_reasons

                if not should_trade:
                    final_reasons = list(dict.fromkeys(hard_reasons or reasons or ["score_below_threshold"]))
                    self.repository.insert_decision_audit(
                        venue=venue,
                        market_id=frame.snapshot.symbol,
                        action="reject",
                        reason=",".join(final_reasons),
                        decision_score=float(signal.score),
                        threshold=float(signal_threshold),
                        trade_size=float(position_plan["effective_trade_size"]),
                        inputs=base_inputs,
                        confidence=float(signal.score),
                    )
                    continue

                entry_price = float(
                    frame.snapshot.mark_price
                    or frame.snapshot.last_price
                    or frame.snapshot.mid_price
                    or frame.snapshot.best_ask
                    or frame.snapshot.best_bid
                    or 0.0
                )
                if entry_price <= 0:
                    reject_inputs = dict(base_inputs)
                    reject_inputs["entry_price_missing"] = True
                    self.repository.insert_decision_audit(
                        venue=venue,
                        market_id=frame.snapshot.symbol,
                        action="reject",
                        reason="market_snapshot_invalid",
                        decision_score=float(signal.score),
                        threshold=float(signal_threshold),
                        trade_size=float(position_plan["effective_trade_size"]),
                        inputs=reject_inputs,
                        confidence=float(signal.score),
                    )
                    continue

                trade_size = float(position_plan["effective_trade_size"])
                stop_loss_price, take_profit_price = self._exit_prices(
                    direction=signal.direction,
                    entry_price=entry_price,
                )
                decision_inputs = dict(base_inputs)
                decision_inputs.update(
                    {
                        "entry_price": round(entry_price, 6),
                        "instrument_type": instrument_type,
                        "leverage": leverage,
                        "take_profit_price": round(take_profit_price, 6),
                        "stop_loss_price": round(stop_loss_price, 6),
                    }
                )
                self.repository.insert_decision_audit(
                    venue=venue,
                    market_id=frame.snapshot.symbol,
                    action="decision",
                    reason="",
                    decision_score=float(signal.score),
                    threshold=float(signal_threshold),
                    trade_size=trade_size,
                    inputs=decision_inputs,
                    confidence=float(signal.score),
                )
                created = self.repository.create_entry(
                    venue=venue,
                    symbol_or_market_id=frame.snapshot.symbol,
                    execution_mode="paper",
                    instrument_type=instrument_type,
                    direction=signal.direction,
                    entry_price=entry_price,
                    trade_size_usd=trade_size,
                    leverage=leverage,
                    confidence=float(signal.score),
                    strategy_profile="binance_technical_sampling",
                    sample_kind="live_paper",
                    source_signal="binance_technical_momentum",
                    signal_family="binance_technical_momentum",
                    take_profit_price=take_profit_price,
                    stop_loss_price=stop_loss_price,
                )
                execute_inputs = dict(decision_inputs)
                execute_inputs.update(
                    {
                        "order_qty": round(float(created["qty"]), 8),
                        "entry_side": created["entry_side"],
                        "exit_side": created["exit_side"],
                    }
                )
                self.repository.insert_decision_audit(
                    venue=venue,
                    market_id=frame.snapshot.symbol,
                    action="execute",
                    reason="",
                    decision_score=float(signal.score),
                    threshold=float(signal_threshold),
                    trade_size=trade_size,
                    inputs=execute_inputs,
                    confidence=float(signal.score),
                )

    def _force_ready(self, signal: TechnicalSignal) -> bool:
        if not self.settings.force_sample_enabled or signal.direction not in {"LONG", "SHORT"}:
            return False
        if signal.score < self.settings.force_min_score:
            return False
        hard_reasons = {reason for reason in signal.reasons if reason != "score_below_threshold"}
        if hard_reasons:
            return False
        now_ts = time()
        if (now_ts - self.technical_force_last_ts) < (self.settings.force_cooldown_minutes * 60.0):
            return False
        self.technical_force_last_ts = now_ts
        return True

    def _build_position_plan(self, venue: str) -> dict[str, Any]:
        base_trade_size = min(self.settings.max_order_usd, self.settings.max_position_usd)
        open_positions = self.repository.fetch_open_positions_for_venues((venue,))
        open_position_count = len(open_positions)
        open_notional = sum(float(position["notional_usd"] or 0.0) for position in open_positions)
        remaining_capacity_usd = max(0.0, float(self.settings.max_position_usd) - open_notional)
        effective_trade_size = min(float(base_trade_size), remaining_capacity_usd)
        position_capacity_sized_down = effective_trade_size < float(base_trade_size) and effective_trade_size >= float(self.settings.min_trade_size_usd)
        return {
            "base_trade_size": float(base_trade_size),
            "effective_trade_size": float(effective_trade_size),
            "remaining_position_capacity_usd": float(remaining_capacity_usd),
            "position_capacity_sized_down": bool(position_capacity_sized_down),
            "open_position_count_at_decision": int(open_position_count),
            "open_position_notional_usd_at_decision": float(open_notional),
            "minimum_trade_size": float(self.settings.min_trade_size_usd),
        }

    def _build_entry_inputs(
        self,
        *,
        frame: MarketFrame,
        signal: TechnicalSignal,
        position_plan: dict[str, Any],
        force_ready: bool,
        signal_threshold: float,
    ) -> dict[str, Any]:
        inputs = dict(signal.inputs)
        inputs.update(
            {
                "symbol": frame.symbol,
                "signal_direction": signal.direction,
                "force_sample": bool(force_ready),
                "force_sample_ready": bool(force_ready),
                "force_min_score": round(self.settings.force_min_score, 4),
                "signal_threshold": round(signal_threshold, 4),
                "technical_symbol_scope": list(self.settings.symbols),
                "technical_active_symbol_count": len(self.settings.symbols),
                "base_trade_size": round(float(position_plan["base_trade_size"]), 4),
                "effective_trade_size": round(float(position_plan["effective_trade_size"]), 4),
                "remaining_position_capacity_usd": round(float(position_plan["remaining_position_capacity_usd"]), 4),
                "position_capacity_sized_down": bool(position_plan["position_capacity_sized_down"]),
                "open_position_count_at_decision": int(position_plan["open_position_count_at_decision"]),
                "open_position_notional_usd_at_decision": round(float(position_plan["open_position_notional_usd_at_decision"]), 4),
                "minimum_trade_size": round(float(position_plan["minimum_trade_size"]), 4),
            }
        )
        return inputs

    def _write_runtime_snapshot(self, summary: dict[str, Any]) -> None:
        position_pressure = summary.get("position_pressure_summary", {})
        legacy_summary = summary.get("legacy_position_summary", {})
        metrics = {
            "binance_technical_active_symbol_count": len(self.settings.symbols),
            "binance_technical_symbol_scope": list(self.settings.symbols),
            "technical_open_positions_total": int(position_pressure.get("open_positions", 0)),
            "technical_open_positions_strict": int(position_pressure.get("fresh_open_positions", 0)),
            "technical_open_positions_legacy": int(position_pressure.get("legacy_open_positions", 0)),
            "technical_open_positions_backfilled": 0,
            "technical_open_positions_ineligible": 0,
            "technical_open_positions_rescue": 0,
            "technical_stale_review_runs": 0,
            "technical_open_positions_seen_by_stale_review": 0,
            "technical_stale_review_skipped_reason": "none",
            "technical_stale_review_candidates_90m": 0,
            "technical_stale_exit_candidates_120m": 0,
            "technical_stale_hard_timeout_candidates_240m": 0,
            "technical_stale_exit_executed": 0,
            "technical_stale_hard_timeout_executed": 0,
            "technical_stale_exit_skipped_alignment_support": 0,
            "technical_stale_exit_skipped_recent_support": 0,
            "technical_stale_exit_skipped_profit_protection": 0,
            "technical_open_positions_price_structure": 0,
            "technical_open_positions_momentum_source": 0,
            "technical_open_positions_protection_linked": 0,
            "technical_legacy_position_shape_summary": {
                "open_binance_paper_positions": int(position_pressure.get("open_positions", 0)),
                "strict_technical_positions": int(position_pressure.get("fresh_open_positions", 0)),
                "legacy_technical_positions": int(position_pressure.get("legacy_open_positions", 0)),
                "ineligible_positions": 0,
                "price_structure_source_positions": 0,
                "technical_momentum_source_positions": 0,
                "protection_linked_positions": 0,
                "rescue_age_positions": 0,
                "backfilled_positions": 0,
            },
            "technical_legacy_open_positions": legacy_summary.get("legacy_symbols", []),
        }
        self.repository.upsert_runtime_status_snapshot(metrics)

    def _exit_prices(self, *, direction: str, entry_price: float) -> tuple[float, float]:
        if direction == "SHORT":
            stop_loss_price = entry_price * (1.0 + self.settings.stop_loss_pct)
            take_profit_price = entry_price * (1.0 - self.settings.take_profit_pct)
        else:
            stop_loss_price = entry_price * (1.0 - self.settings.stop_loss_pct)
            take_profit_price = entry_price * (1.0 + self.settings.take_profit_pct)
        return float(stop_loss_price), float(take_profit_price)
