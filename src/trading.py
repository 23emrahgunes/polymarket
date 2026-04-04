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

    async def execute_trade(self, market_id, side, size, price, edge, confidence):
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
            await self.db.add_trade(market_id, side, size, price, edge, confidence, status="OPEN")

            return True, f"Trade executed: Spent ${size} on {market_id} (Side: {side}) at {price}"

    async def check_resolutions(self, scanner):
        """
        Polls for the resolved status of open trades using the Polymarket API.
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

            try:
                market_info = await asyncio.to_thread(self.polymarket.get_market, market_id)
                if market_info and market_info.get("closed"):
                    outcome = market_info.get("outcome")
                    if outcome:
                        shares = size / entry_price
                        payout = shares if outcome == side else 0

                        async with self.lock:
                            current_balance = await self.db.get_balance()
                            await self.db.update_balance(current_balance + payout)
                            status = "CLOSED_WIN" if payout > 0 else "CLOSED_LOSS"
                            await self.db.update_trade_status(trade_id, status)
                            logger.info(f"Trade {trade_id} (Market: {market_id}) resolved! Outcome: {outcome}. Payout: ${payout:.2f}. Status: {status}")
            except Exception as e:
                logger.error(f"Error resolving trade {trade_id}: {e}")

    async def get_total_value(self):
        balance = await self.db.get_balance()
        return balance
