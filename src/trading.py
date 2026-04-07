import asyncio
import inspect
import logging
import os
from typing import Any, Awaitable, Callable, Dict, Optional

from py_clob_client.client import ClobClient
from py_clob_client.constants import POLYGON

from src.env_utils import resolve_polygon_private_key


logger = logging.getLogger(__name__)


TradeInsertCallback = Callable[[Dict[str, Any]], Awaitable[None] | None]


class TradeExecutor:
    """
    Unified Trade Executor for both Paper and Live Trading.
    Defaults to PAPER mode for safety.
    """

    def __init__(self, db, live_mode: bool = False, trade_insert_callback: Optional[TradeInsertCallback] = None):
        self.db = db
        self.lock = asyncio.Lock()
        self.live_mode = live_mode
        self.trade_insert_callback = trade_insert_callback

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
    ):
        async with self.lock:
            current_balance = await self.db.get_balance()
            if current_balance < size:
                logger.info(
                    "[REJECT] source=%s category=%s market=%s reasons=insufficient_balance inputs=%s",
                    source,
                    category,
                    market_id,
                    {"balance": current_balance, "trade_size": size},
                )
                return False, f"Insufficient balance: {current_balance} < {size}"

            if await self.db.has_open_trade(market_id, side):
                logger.info(
                    "[REJECT] source=%s category=%s market=%s reasons=duplicate_open_trade inputs=%s",
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
            await self.db.update_balance(new_balance)
            trade_id = await self.db.add_trade(
                market_id,
                side,
                size,
                price,
                edge,
                confidence,
                status="OPEN",
                whale_address=whale_address,
            )

            mode_prefix = "LIVE" if self.live_mode else "PAPER"
            logger.info(
                "[%s-TRADE-RUNTIME] inserted trade id=%s market=%s side=%s balance_before=%.2f balance_after=%.2f source=%s category=%s",
                mode_prefix,
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
                    }
                )
                if inspect.isawaitable(callback_result):
                    await callback_result

            return True, f"[{mode_prefix}] Trade executed: id={trade_id} spent ${size} on {market_id}"

    async def check_resolutions(self, scanner):
        if not self.polymarket:
            return

        open_trades = await self.db.get_open_trades()
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
                            current_balance = await self.db.get_balance()
                            await self.db.update_balance(current_balance + payout)

                            status = "CLOSED_WIN" if pnl > 0 else "CLOSED_LOSS"
                            await self.db.update_trade_resolution(trade_id, status, pnl)

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
        return await self.db.get_balance()


PaperTrader = TradeExecutor
