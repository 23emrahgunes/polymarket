from src.database import Database
import asyncio
import logging
import random

logger = logging.getLogger(__name__)

class PaperTrader:
    def __init__(self, db: Database):
        self.db = db
        self.lock = asyncio.Lock()

    async def execute_trade(self, market_id, side, size, price, edge, confidence):
        """
        Executes a paper trade with double-spending prevention.
        Each share pays $1 if the outcome is YES, $0 otherwise.
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
            # Status is OPEN initially
            await self.db.add_trade(market_id, side, size, price, edge, confidence, status="OPEN")

            return True, f"Trade executed: Spent ${size} on {market_id} (Side: {side}) at {price}"

    async def check_resolutions(self, scanner):
        """
        Polls for the resolved status of open trades and calculates P&L.
        """
        open_trades = await self.db.get_open_trades()
        for trade in open_trades:
            trade_id = trade["id"]
            market_id = trade["market_id"]
            side = trade["side"]
            size = trade["size"]
            entry_price = trade["price"]

            try:
                # Actual Polymarket resolution check via SDK would go here:
                # status = scanner.polymarket.get_market_status(market_id)
                # For v1.0, we'll mock the resolution logic to show P&L flow
                # (Actual polling requires matching market_id to on-chain state)

                # Mock resolution logic (for simulation/testing)
                # In a real environment, we'd poll the Polymarket API/graph
                is_resolved = random.choice([True, False, False, False]) # 25% chance per scan for demo
                if is_resolved:
                    outcome = random.choice(["YES", "NO"]) # Random outcome for demo
                    shares = size / entry_price
                    payout = shares if outcome == side else 0

                    async with self.lock:
                        current_balance = await self.db.get_balance()
                        await self.db.update_balance(current_balance + payout)
                        status = "CLOSED_WIN" if payout > 0 else "CLOSED_LOSS"
                        await self.db.update_trade_status(trade_id, status)
                        logger.info(f"Trade {trade_id} resolved! Outcome: {outcome}. Payout: ${payout:.2f}. Status: {status}")

            except Exception as e:
                logger.error(f"Error resolving trade {trade_id}: {e}")

    async def get_total_value(self):
        balance = await self.db.get_balance()
        return balance
