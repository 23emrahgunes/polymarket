from src.database import Database
import asyncio
import logging
import os
from py_clob_client.client import ClobClient
from py_clob_client.constants import POLYGON

logger = logging.getLogger(__name__)

class PaperTrader:
    def __init__(self, db):
        self.db = db
        self.lock = asyncio.Lock()
        private_key = os.getenv("POLYGON_PRIVATE_KEY", "0x0000000000000000000000000000000000000000000000000000000000000000")

        # Initialize CLOB client only if not in testing (avoids timeouts in test env)
        self.polymarket = None
        if os.getenv("ENV") != "test":
            self.polymarket = ClobClient(
                host="https://clob.polymarket.com",
                chain_id=POLYGON,
                key=private_key
            )

    async def execute_trade(self, market_id, side, size, price, edge, confidence, whale_address=None):
        """
        Executes a paper trade with double-spending prevention.
        """
        async with self.lock:
            # Check balance
            current_balance = await self.db.get_balance()
            if current_balance < size:
                return False, f"Insufficient balance: {current_balance} < {size}"

            # Deduct balance
            new_balance = current_balance - size
            await self.db.update_balance(new_balance)

            # Record trade
            await self.db.add_trade(market_id, side, size, price, edge, confidence, status="OPEN", whale_address=whale_address)

            return True, f"Trade executed: Spent ${size} on {market_id} (Side: {side}) at {price}"

    async def check_resolutions(self, scanner):
        """
        Polls for the resolved status of open trades using the Polymarket API.
        Updated: Record P&L and update whale analytics.
        """
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
                        # Payout is $1 per share if correct, $0 otherwise
                        payout = shares if outcome == side else 0
                        pnl = payout - size

                        async with self.lock:
                            current_balance = await self.db.get_balance()
                            await self.db.update_balance(current_balance + payout)

                            # Log detailed performance
                            status = "CLOSED_WIN" if pnl > 0 else "CLOSED_LOSS"
                            await self.db.update_trade_resolution(trade_id, status, pnl)

                            # Update Whale accuracy if it was a copy trade
                            if whale_address:
                                await self.db.update_whale_stats(whale_address, pnl)
                                from src.analytics import log_whale_score
                                await log_whale_score(self.db, whale_address)

                            logger.info(f"Trade {trade_id} (Market: {market_id}) resolved! Outcome: {outcome}. P&L: ${pnl:.2f}. Status: {status}")
            except Exception as e:
                logger.error(f"Error resolving trade {trade_id}: {e}")

    async def get_total_value(self):
        balance = await self.db.get_balance()
        return balance
