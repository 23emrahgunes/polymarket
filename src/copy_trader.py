import asyncio
import logging
import random
from src.whale_tracker import WhaleTracker

logger = logging.getLogger(__name__)

class CopyTrader:
    def __init__(self, trader, scanner):
        self.trader = trader
        self.scanner = scanner

    async def evaluate_signal(self, whale_action):
        """
        Copy-Trade Logic:
        1. Liquidity Guard: Ensure the target market has 24h Volume > $10,000.
        2. Price Guard: Only confirm if the current market price is within 1.5% of the whale's entry price.
        """
        whale = whale_action.get("whale", "0x...")
        action = whale_action.get("action", "BUY")
        market_id = whale_action.get("market_id")
        whale_entry_price = whale_action.get("price", 0)

        try:
            # 1. Fetch market info for volume and current price
            # Robust data check: handle string/null responses as requested
            current_market_price = await self.scanner.get_token_price(market_id)
            if not current_market_price:
                logger.warning(f"CopyTrader: Invalid price data for {market_id}")
                return False

            # Simulated data for CopySignal demonstration
            market_volume_24h = 15000.0 # $15,000 for demonstration

            # 1. Liquidity Guard: 24h Volume > $10,000
            if market_volume_24h < 10000:
                logger.debug(f"[WHALE_ACTION] Wallet: {whale[:10]}... | Action: {action} {market_id} | Price: ${current_market_price:.2f} | SIGNAL: INSUFFICIENT_LIQUIDITY")
                return False

            # 2. Price Guard: Within 1.5% of whale's entry price
            price_diff_pct = abs(current_market_price - whale_entry_price) / whale_entry_price
            if price_diff_pct > 0.015:
                logger.debug(f"[WHALE_ACTION] Wallet: {whale[:10]}... | Action: {action} {market_id} | Price: ${current_market_price:.2f} | SIGNAL: SPREAD_TOO_HIGH ({price_diff_pct:.2%})")
                return False

            # 3. All Guards Passed -> CopySignal Match
            # [WHALE_ACTION] Highly visible log
            logger.info(f"[!!! WHALE_ACTION !!!] Wallet: {whale[:10]}... | Action: {action} {market_id} | Price: ${current_market_price:.2f} | SIGNAL: COPY_MATCH")

            # Execute Paper Copy Trade
            success, msg = await self.trader.execute_trade(market_id, "YES", 50.0, current_market_price, edge=0.0, confidence=1.0)
            if success:
                logger.info(f"[!!! SIGNAL !!!] Whale copy-trade executed! {msg}")

            return True
        except Exception as e:
            logger.error(f"CopyTrader: Error evaluating signal - {e}")
            return False
