from __future__ import annotations

import asyncio
import inspect
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from src.venue_config import VenueConfig


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProtectionOrders:
    stop_loss_price: float
    take_profit_price: float


class ExecutionVenue(ABC):
    def __init__(self, venue_id: str, config: VenueConfig, db):
        self.venue_id = venue_id
        self.config = config
        self.db = db
        self.lock = asyncio.Lock()

    @abstractmethod
    async def sync_account_state(self, scanner=None) -> None:
        raise NotImplementedError

    @abstractmethod
    async def get_open_positions(self, symbol_or_market_id: Optional[str] = None) -> List[Dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    async def place_entry_order(self, **kwargs) -> Tuple[bool, str]:
        raise NotImplementedError

    @abstractmethod
    async def place_exit_order(self, **kwargs) -> Tuple[bool, str]:
        raise NotImplementedError

    @abstractmethod
    async def cancel_protection_orders(self, symbol_or_market_id: str) -> None:
        raise NotImplementedError

    @abstractmethod
    async def refresh_order_status(self) -> None:
        raise NotImplementedError


class VenueRiskManager(ABC):
    @abstractmethod
    async def validate_entry(self, **kwargs) -> List[str]:
        raise NotImplementedError

    @abstractmethod
    async def build_protection_orders(self, **kwargs) -> ProtectionOrders:
        raise NotImplementedError

    @abstractmethod
    async def validate_exit(self, **kwargs) -> List[str]:
        raise NotImplementedError


class PolymarketVenue(ExecutionVenue):
    def __init__(self, config: VenueConfig, db, trader):
        super().__init__("polymarket", config, db)
        self.trader = trader

    async def sync_account_state(self, scanner=None) -> None:
        return None

    async def get_open_positions(self, symbol_or_market_id: Optional[str] = None) -> List[Dict[str, Any]]:
        trades = await self.db.get_open_trades(venue="polymarket", instrument_type="prediction")
        if symbol_or_market_id is None:
            return [dict(row) for row in trades]
        return [dict(row) for row in trades if row["market_id"] == symbol_or_market_id]

    async def place_entry_order(
        self,
        market_id: str,
        side: str,
        size_usd: float,
        price: float,
        edge: float,
        confidence: float,
        source: str,
        category: str,
        source_signal: str,
    ) -> Tuple[bool, str]:
        existing = await self.get_open_positions(market_id)
        if existing:
            logger.info(
                "[REJECT] venue=polymarket source=%s category=%s market=%s reasons=duplicate_market_exposure inputs=%s",
                source,
                category,
                market_id,
                {"open_count": len(existing), "requested_side": side},
            )
            return False, "Open Polymarket position already exists for market"

        return await self.trader.execute_trade(
            market_id,
            side,
            size_usd,
            price,
            edge=edge,
            confidence=confidence,
            source=source,
            category=category,
            venue="polymarket",
            instrument_type="prediction",
            source_signal=source_signal,
            execution_mode=self.config.mode,
        )

    async def place_exit_order(self, **kwargs) -> Tuple[bool, str]:
        return False, "Polymarket exits are resolution-driven in v1"

    async def cancel_protection_orders(self, symbol_or_market_id: str) -> None:
        return None

    async def refresh_order_status(self) -> None:
        return None


class BinanceFuturesRiskManager(VenueRiskManager):
    def __init__(self, config: VenueConfig, db):
        self.config = config
        self.db = db

    async def validate_entry(
        self,
        symbol: str,
        side: str,
        price: float,
        trade_size: float,
        spread_pct: float,
        signal_score: float,
    ) -> List[str]:
        reasons: List[str] = []
        account = await self.db.get_venue_account("binance_futures", self.config.mode)
        available_balance = float(account["available_balance"]) if account else 0.0
        required_margin = trade_size / max(self.config.leverage, 1)

        if not symbol:
            reasons.append("missing_futures_symbol_mapping")
        if self.config.margin_mode != "isolated":
            reasons.append("isolated_margin_unavailable")
        if self.config.leverage != 2:
            reasons.append("leverage_config_invalid")
        if signal_score < self.config.signal_threshold:
            reasons.append("venue_signal_threshold_not_met")
        if spread_pct * 10_000 > self.config.slippage_limit_bps:
            reasons.append("exchange_filters_rejected")
        if trade_size > self.config.max_order_usd:
            reasons.append("max_position_exceeded")
        if available_balance < required_margin:
            reasons.append("insufficient_futures_balance")

        open_positions = await self.db.get_open_positions(venue="binance_futures")
        if len(open_positions) >= self.config.max_open_positions:
            reasons.append("max_position_exceeded")

        current_notional = sum(float(position["notional_usd"]) for position in open_positions)
        if current_notional + trade_size > self.config.max_position_usd:
            reasons.append("max_position_exceeded")

        realized_pnl_today = await self.db.get_realized_pnl_since(
            "binance_futures",
            datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).strftime("%Y-%m-%d %H:%M:%S"),
        )
        if realized_pnl_today <= -abs(self.config.max_daily_loss_usd):
            reasons.append("max_daily_loss_exceeded")

        if price <= 0:
            reasons.append("exchange_filters_rejected")
        if side not in {"LONG", "SHORT"}:
            reasons.append("exchange_filters_rejected")

        return self._dedupe(reasons)

    async def build_protection_orders(self, side: str, entry_price: float) -> ProtectionOrders:
        if self.config.stop_loss_pct <= 0:
            raise ValueError("stop_distance_invalid")
        if self.config.take_profit_pct <= 0:
            raise ValueError("tp_distance_invalid")

        if side == "LONG":
            stop_loss_price = entry_price * (1 - self.config.stop_loss_pct)
            take_profit_price = entry_price * (1 + self.config.take_profit_pct)
        else:
            stop_loss_price = entry_price * (1 + self.config.stop_loss_pct)
            take_profit_price = entry_price * (1 - self.config.take_profit_pct)

        if stop_loss_price <= 0:
            raise ValueError("stop_distance_invalid")
        if take_profit_price <= 0:
            raise ValueError("tp_distance_invalid")

        return ProtectionOrders(
            stop_loss_price=round(stop_loss_price, 6),
            take_profit_price=round(take_profit_price, 6),
        )

    async def validate_exit(self, position: Optional[Dict[str, Any]]) -> List[str]:
        if position is None:
            return ["position_sync_failed"]
        return []

    @staticmethod
    def _dedupe(values: List[str]) -> List[str]:
        seen = set()
        ordered: List[str] = []
        for value in values:
            if value not in seen:
                seen.add(value)
                ordered.append(value)
        return ordered


class BinanceFuturesPaperVenue(ExecutionVenue):
    def __init__(self, config: VenueConfig, db, scanner, trade_insert_callback=None):
        super().__init__("binance_futures", config, db)
        self.scanner = scanner
        self.risk_manager = BinanceFuturesRiskManager(config, db)
        self.trade_insert_callback = trade_insert_callback

    async def sync_account_state(self, scanner=None) -> None:
        active_scanner = scanner or self.scanner
        open_positions = await self.db.get_open_positions(venue="binance_futures")
        for position in open_positions:
            symbol = position["symbol_or_market_id"]
            try:
                snapshot = await active_scanner.get_futures_market_snapshot(symbol)
            except Exception as exc:
                logger.info(
                    "[REJECT] venue=binance_futures source=sync category=CRYPTO market=%s reasons=position_sync_failed inputs=%s",
                    symbol,
                    {"error": str(exc)},
                )
                continue

            if not snapshot.get("is_valid"):
                logger.info(
                    "[REJECT] venue=binance_futures source=sync category=CRYPTO market=%s reasons=position_sync_failed inputs=%s",
                    symbol,
                    snapshot,
                )
                continue

            mark_price = float(snapshot["mark_price"])
            entry_price = float(position["entry_price"])
            quantity = float(position["qty_or_shares"])
            side = position["side"]
            notional = float(position["notional_usd"])
            leverage = int(position["leverage"])

            if side == "LONG":
                unrealized_pnl = quantity * (mark_price - entry_price)
            else:
                unrealized_pnl = quantity * (entry_price - mark_price)

            await self.db.update_position_mark(position["id"], mark_price, unrealized_pnl)

            open_orders = await self.db.get_open_venue_orders("binance_futures", symbol_or_market_id=symbol)
            stop_order = next((dict(order) for order in open_orders if order["order_type"] == "STOP_LOSS"), None)
            tp_order = next((dict(order) for order in open_orders if order["order_type"] == "TAKE_PROFIT"), None)

            if stop_order and self._should_trigger_stop(side, mark_price, float(stop_order["stop_price"])):
                await self._close_position(position, mark_price, "STOP_LOSS", leverage, notional, stop_order["id"])
                continue
            if tp_order and self._should_trigger_take_profit(side, mark_price, float(tp_order["stop_price"])):
                await self._close_position(position, mark_price, "TAKE_PROFIT", leverage, notional, tp_order["id"])

    async def get_open_positions(self, symbol_or_market_id: Optional[str] = None) -> List[Dict[str, Any]]:
        rows = await self.db.get_open_positions(venue="binance_futures", symbol_or_market_id=symbol_or_market_id)
        return [dict(row) for row in rows]

    async def get_open_position(self, symbol_or_market_id: str) -> Optional[Dict[str, Any]]:
        positions = await self.get_open_positions(symbol_or_market_id)
        return positions[0] if positions else None

    async def place_entry_order(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        trade_size: float,
        signal_score: float,
        source: str,
        source_signal: str,
        spread_pct: float,
        market_context: Dict[str, Any],
    ) -> Tuple[bool, str]:
        async with self.lock:
            reasons = await self.risk_manager.validate_entry(
                symbol=symbol,
                side=side,
                price=entry_price,
                trade_size=trade_size,
                spread_pct=spread_pct,
                signal_score=signal_score,
            )
            if reasons:
                logger.info(
                    "[REJECT] venue=binance_futures source=%s category=CRYPTO market=%s reasons=%s inputs=%s",
                    source,
                    symbol,
                    ",".join(reasons),
                    {"side": side, "trade_size": trade_size, "entry_price": entry_price, "signal_score": signal_score},
                )
                return False, reasons[0]

            existing = await self.get_open_position(symbol)
            if existing is not None:
                logger.info(
                    "[REJECT] venue=binance_futures source=%s category=CRYPTO market=%s reasons=duplicate_open_trade inputs=%s",
                    source,
                    symbol,
                    {"side": side, "existing_side": existing["side"]},
                )
                return False, "duplicate_open_trade"

            try:
                protection = await self.risk_manager.build_protection_orders(side=side, entry_price=entry_price)
            except ValueError as exc:
                reason = str(exc)
                logger.info(
                    "[REJECT] venue=binance_futures source=%s category=CRYPTO market=%s reasons=%s inputs=%s",
                    source,
                    symbol,
                    reason,
                    {"side": side, "entry_price": entry_price},
                )
                return False, reason

            quantity = trade_size / entry_price
            initial_margin = trade_size / max(self.config.leverage, 1)
            balance_before = await self.db.get_balance("binance_futures", self.config.mode)
            balance_after = balance_before - initial_margin
            await self.db.update_balance(balance_after, "binance_futures", self.config.mode)

            position_id = await self.db.create_venue_position(
                venue="binance_futures",
                execution_mode=self.config.mode,
                instrument_type="futures",
                symbol_or_market_id=symbol,
                side=side,
                qty_or_shares=quantity,
                entry_price=entry_price,
                notional_usd=trade_size,
                leverage=self.config.leverage,
                source_signal=source_signal,
                linked_market_id=market_context.get("market_id"),
            )
            await self.db.add_venue_order(
                venue="binance_futures",
                execution_mode=self.config.mode,
                position_id=position_id,
                symbol_or_market_id=symbol,
                order_type="STOP_LOSS",
                side="SELL" if side == "LONG" else "BUY",
                qty=quantity,
                price=entry_price,
                stop_price=protection.stop_loss_price,
                reduce_only=True,
                status="OPEN",
            )
            await self.db.add_venue_order(
                venue="binance_futures",
                execution_mode=self.config.mode,
                position_id=position_id,
                symbol_or_market_id=symbol,
                order_type="TAKE_PROFIT",
                side="SELL" if side == "LONG" else "BUY",
                qty=quantity,
                price=entry_price,
                stop_price=protection.take_profit_price,
                reduce_only=True,
                status="OPEN",
            )
            trade_id = await self.db.add_trade(
                symbol,
                side,
                initial_margin,
                entry_price,
                edge=0.0,
                confidence=signal_score,
                status="OPEN",
                whale_address=None,
                venue="binance_futures",
                instrument_type="futures",
                position_id=position_id,
                source_signal=source_signal,
                execution_mode=self.config.mode,
            )
            logger.info(
                "[PAPER-TRADE-RUNTIME] venue=binance_futures inserted trade id=%s market=%s side=%s balance_before=%.2f balance_after=%.2f source=%s protection=%s",
                trade_id,
                symbol,
                side,
                balance_before,
                balance_after,
                source,
                {"stop_loss": protection.stop_loss_price, "take_profit": protection.take_profit_price},
            )
            if self.trade_insert_callback is not None:
                callback_result = self.trade_insert_callback(
                    {
                        "trade_id": trade_id,
                        "market_id": symbol,
                        "side": side,
                        "balance_before": balance_before,
                        "balance_after": balance_after,
                        "source": source,
                        "category": "CRYPTO",
                        "venue": "binance_futures",
                        "instrument_type": "futures",
                        "source_signal": source_signal,
                        "execution_mode": self.config.mode,
                    }
                )
                if inspect.isawaitable(callback_result):
                    await callback_result
            return True, f"[PAPER] Binance Futures {side} entry on {symbol}"

    async def place_exit_order(
        self,
        symbol_or_market_id: str,
        exit_price: float,
        reason: str,
        source: str = "signal_exit",
    ) -> Tuple[bool, str]:
        async with self.lock:
            position = await self.get_open_position(symbol_or_market_id)
            exit_reasons = await self.risk_manager.validate_exit(position)
            if exit_reasons:
                logger.info(
                    "[REJECT] venue=binance_futures source=%s category=CRYPTO market=%s reasons=%s inputs=%s",
                    source,
                    symbol_or_market_id,
                    ",".join(exit_reasons),
                    {"reason": reason},
                )
                return False, exit_reasons[0]

            await self._close_position(position, exit_price, reason, int(position["leverage"]), float(position["notional_usd"]), None)
            return True, f"Position exited via {reason}"

    async def cancel_protection_orders(self, symbol_or_market_id: str) -> None:
        await self.db.cancel_open_venue_orders("binance_futures", symbol_or_market_id)

    async def refresh_order_status(self) -> None:
        return None

    async def _close_position(
        self,
        position: Dict[str, Any],
        exit_price: float,
        reason: str,
        leverage: int,
        notional_usd: float,
        triggered_order_id: Optional[int],
    ) -> None:
        entry_price = float(position["entry_price"])
        quantity = float(position["qty_or_shares"])
        margin = notional_usd / max(leverage, 1)
        if position["side"] == "LONG":
            realized_pnl = quantity * (exit_price - entry_price)
        else:
            realized_pnl = quantity * (entry_price - exit_price)

        balance_before = await self.db.get_balance("binance_futures", self.config.mode)
        balance_after = balance_before + margin + realized_pnl
        await self.db.update_balance(balance_after, "binance_futures", self.config.mode)
        await self.db.close_venue_position(position["id"], exit_price, realized_pnl, reason)
        await self.cancel_protection_orders(position["symbol_or_market_id"])
        if triggered_order_id is not None:
            await self.db.update_venue_order_status(triggered_order_id, "FILLED")
        logger.info(
            "[POSITION-CLOSED] venue=binance_futures market=%s side=%s reason=%s exit_price=%.4f pnl=%.2f balance_before=%.2f balance_after=%.2f",
            position["symbol_or_market_id"],
            position["side"],
            reason,
            exit_price,
            realized_pnl,
            balance_before,
            balance_after,
        )

    @staticmethod
    def _should_trigger_stop(side: str, current_price: float, stop_price: float) -> bool:
        if side == "LONG":
            return current_price <= stop_price
        return current_price >= stop_price

    @staticmethod
    def _should_trigger_take_profit(side: str, current_price: float, take_profit_price: float) -> bool:
        if side == "LONG":
            return current_price >= take_profit_price
        return current_price <= take_profit_price


class BinanceSpotVenue(ExecutionVenue):
    def __init__(self, config: VenueConfig, db):
        super().__init__("binance_spot", config, db)

    async def sync_account_state(self, scanner=None) -> None:
        return None

    async def get_open_positions(self, symbol_or_market_id: Optional[str] = None) -> List[Dict[str, Any]]:
        return []

    async def place_entry_order(self, **kwargs) -> Tuple[bool, str]:
        logger.info(
            "[REJECT] venue=binance_spot source=runtime category=CRYPTO market=%s reasons=spot_executor_not_enabled inputs=%s",
            kwargs.get("symbol", "unknown"),
            {"phase": "scaffold_only"},
        )
        return False, "spot_executor_not_enabled"

    async def place_exit_order(self, **kwargs) -> Tuple[bool, str]:
        return False, "spot_executor_not_enabled"

    async def cancel_protection_orders(self, symbol_or_market_id: str) -> None:
        return None

    async def refresh_order_status(self) -> None:
        return None
