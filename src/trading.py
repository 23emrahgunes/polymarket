import asyncio
import inspect
import logging
import os
from typing import Any, Awaitable, Callable, Dict, Optional

from py_clob_client.client import ClobClient
from py_clob_client.constants import POLYGON

from src.env_utils import resolve_polygon_private_key
from src.evaluation_utils import (
    STRATEGY_PROFILE_BASELINE,
    infer_sample_kind,
    normalize_signal_family,
    normalize_strategy_profile,
    slippage_proxy_bps_from_spread,
)


logger = logging.getLogger(__name__)


TradeInsertCallback = Callable[[Dict[str, Any]], Awaitable[None] | None]


class TradeExecutor:
    """
    Unified Trade Executor for both Paper and Live Trading.
    Defaults to PAPER mode for safety.
    """

    def __init__(
        self,
        db,
        live_mode: bool = False,
        trade_insert_callback: Optional[TradeInsertCallback] = None,
        sample_kind: str | None = None,
        debug_profile: str | None = None,
    ):
        self.db = db
        self.lock = asyncio.Lock()
        self.live_mode = live_mode
        self.trade_insert_callback = trade_insert_callback
        self.sample_kind = sample_kind or infer_sample_kind(False)
        self.debug_profile = debug_profile

        private_key = resolve_polygon_private_key()
        self.polymarket = None
        if os.getenv("ENV") != "test":
            self.polymarket = ClobClient(
                host="https://clob.polymarket.com",
                chain_id=POLYGON,
                key=private_key,
            )

    async def execute_trade(
        self,
        market_id,
        side,
        size,
        price,
        edge,
        confidence,
        whale_address=None,
        source: str = "runtime",
        category: str = "UNKNOWN",
        venue: str = "polymarket",
        instrument_type: str = "prediction",
        position_id: int | None = None,
        source_signal: str = "runtime",
        execution_mode: str = "paper",
        signal_family: str | None = None,
        strategy_profile: str = STRATEGY_PROFILE_BASELINE,
        sample_kind: str | None = None,
        debug_profile: str | None = None,
        entry_spread_pct: float | None = None,
        slippage_proxy_bps: float | None = None,
        whale_trust_at_entry: float | None = None,
    ):
        async with self.lock:
            normalized_sample_kind = sample_kind or self.sample_kind
            normalized_signal_family = signal_family or normalize_signal_family(source_signal)
            normalized_strategy_profile = normalize_strategy_profile(strategy_profile)
            normalized_slippage_proxy = slippage_proxy_bps if slippage_proxy_bps is not None else slippage_proxy_bps_from_spread(entry_spread_pct)
            current_balance = await self.db.get_balance(venue, execution_mode)
            if current_balance < size:
                await self.db.add_decision_audit(
                    venue=venue,
                    market_id=market_id,
                    category=category,
                    signal_family=normalized_signal_family,
                    strategy_profile=normalized_strategy_profile,
                    raw_source_signal=source_signal,
                    sample_kind=normalized_sample_kind,
                    decision_score=confidence,
                    trade_size=size,
                    action="reject",
                    reason="insufficient_balance",
                    confidence=confidence,
                    whale_trust=whale_trust_at_entry,
                    spread_pct=entry_spread_pct,
                    slippage_proxy_bps=normalized_slippage_proxy,
                    inputs_json=str({"balance": current_balance, "trade_size": size, "side": side}),
                )
                logger.info(
                    "[REJECT] venue=%s source=%s category=%s market=%s reasons=insufficient_balance inputs=%s",
                    venue,
                    source,
                    category,
                    market_id,
                    {"balance": current_balance, "trade_size": size},
                )
                return False, f"Insufficient balance: {current_balance} < {size}"

            if await self.db.has_open_trade(market_id, side, venue=venue):
                await self.db.add_decision_audit(
                    venue=venue,
                    market_id=market_id,
                    category=category,
                    signal_family=normalized_signal_family,
                    strategy_profile=normalized_strategy_profile,
                    raw_source_signal=source_signal,
                    sample_kind=normalized_sample_kind,
                    decision_score=confidence,
                    trade_size=size,
                    action="reject",
                    reason="duplicate_open_trade",
                    confidence=confidence,
                    whale_trust=whale_trust_at_entry,
                    spread_pct=entry_spread_pct,
                    slippage_proxy_bps=normalized_slippage_proxy,
                    inputs_json=str({"side": side, "trade_size": size}),
                )
                logger.info(
                    "[REJECT] venue=%s source=%s category=%s market=%s reasons=duplicate_open_trade inputs=%s",
                    venue,
                    source,
                    category,
                    market_id,
                    {"side": side, "trade_size": size},
                )
                return False, f"Open trade already exists for {market_id} {side}"

            if self.live_mode and self.polymarket:
                try:
                    logger.info("LIVE_MODE: real API trade path would execute on %s", market_id)
                except Exception as exc:
                    logger.error("Live API execution failed: %s", exc)
                    return False, f"Live execution error: {exc}"

            new_balance = current_balance - size
            await self.db.update_balance(new_balance, venue, execution_mode)
            trade_id = await self.db.add_trade(
                market_id,
                side,
                size,
                price,
                edge,
                confidence,
                status="OPEN",
                whale_address=whale_address,
                venue=venue,
                instrument_type=instrument_type,
                position_id=position_id,
                source_signal=source_signal,
                category=category,
                signal_family=normalized_signal_family,
                strategy_profile=normalized_strategy_profile,
                sample_kind=normalized_sample_kind,
                debug_profile=debug_profile or self.debug_profile,
                entry_spread_pct=entry_spread_pct,
                slippage_proxy_bps=normalized_slippage_proxy,
                whale_trust_at_entry=whale_trust_at_entry,
                execution_mode=execution_mode,
            )
            await self.db.add_decision_audit(
                venue=venue,
                market_id=market_id,
                category=category,
                signal_family=normalized_signal_family,
                strategy_profile=normalized_strategy_profile,
                raw_source_signal=source_signal,
                sample_kind=normalized_sample_kind,
                decision_score=confidence,
                threshold=None,
                trade_size=size,
                action="execute",
                reason=None,
                confidence=confidence,
                whale_trust=whale_trust_at_entry,
                spread_pct=entry_spread_pct,
                slippage_proxy_bps=normalized_slippage_proxy,
                inputs_json=str({"trade_id": trade_id, "side": side, "price": price, "edge": edge}),
            )

            mode_prefix = "LIVE" if self.live_mode else "PAPER"
            logger.info(
                "[%s-TRADE-RUNTIME] venue=%s inserted trade id=%s market=%s side=%s balance_before=%.2f balance_after=%.2f source=%s category=%s",
                mode_prefix,
                venue,
                trade_id,
                market_id,
                side,
                current_balance,
                new_balance,
                source,
                category,
            )

            if self.trade_insert_callback is not None:
                callback_result = self.trade_insert_callback(
                    {
                        "trade_id": trade_id,
                        "market_id": market_id,
                        "side": side,
                        "balance_before": current_balance,
                        "balance_after": new_balance,
                        "source": source,
                        "category": category,
                        "venue": venue,
                        "instrument_type": instrument_type,
                        "source_signal": source_signal,
                        "execution_mode": execution_mode,
                        "sample_kind": normalized_sample_kind,
                        "strategy_profile": normalized_strategy_profile,
                    }
                )
                if inspect.isawaitable(callback_result):
                    await callback_result

            return True, f"[{mode_prefix}] Trade executed: id={trade_id} spent ${size} on {market_id}"

    async def check_resolutions(self, scanner):
        if not self.polymarket:
            return

        open_trades = await self.db.get_open_trades(venue="polymarket", instrument_type="prediction")
        for trade in open_trades:
            trade_id = trade["id"]
            market_id = trade["market_id"]
            side = trade["side"]
            size = trade["size"]
            entry_price = trade["price"]
            whale_address = trade["whale_address"]

            try:
                market_info = await asyncio.to_thread(self.polymarket.get_market, market_id)
                if market_info and market_info.get("closed"):
                    outcome = market_info.get("outcome")
                    if outcome:
                        shares = size / entry_price
                        payout = shares if outcome == side else 0
                        pnl = payout - size

                        async with self.lock:
                            current_balance = await self.db.get_balance("polymarket", "paper")
                            await self.db.update_balance(current_balance + payout, "polymarket", "paper")

                            status = "CLOSED_WIN" if pnl > 0 else "CLOSED_LOSS"
                            await self.db.update_trade_resolution(trade_id, status, pnl)
                            await self.db.add_decision_audit(
                                venue="polymarket",
                                market_id=market_id,
                                category=trade["category"] if "category" in trade.keys() else None,
                                signal_family=trade["signal_family"] if "signal_family" in trade.keys() else None,
                                strategy_profile=trade["strategy_profile"] if "strategy_profile" in trade.keys() else STRATEGY_PROFILE_BASELINE,
                                raw_source_signal=trade["source_signal"] if "source_signal" in trade.keys() else "runtime",
                                sample_kind=trade["sample_kind"] if "sample_kind" in trade.keys() else self.sample_kind,
                                decision_score=trade["confidence"],
                                trade_size=trade["size"],
                                action="exit",
                                reason=status,
                                confidence=trade["confidence"],
                                whale_trust=trade["whale_trust_at_entry"] if "whale_trust_at_entry" in trade.keys() else None,
                                spread_pct=trade["entry_spread_pct"] if "entry_spread_pct" in trade.keys() else None,
                                slippage_proxy_bps=trade["slippage_proxy_bps"] if "slippage_proxy_bps" in trade.keys() else None,
                                inputs_json=str({"trade_id": trade_id, "pnl": pnl, "outcome": outcome}),
                            )

                            if whale_address:
                                await self.db.update_whale_stats(whale_address, pnl)

                            logger.info(
                                "Trade %s resolved. market=%s outcome=%s pnl=%.2f status=%s",
                                trade_id,
                                market_id,
                                outcome,
                                pnl,
                                status,
                            )
            except Exception as exc:
                logger.error("Error resolving trade %s: %s", trade_id, exc)

    async def get_total_value(self):
        return await self.db.get_balance("polymarket", "paper")


PaperTrader = TradeExecutor
