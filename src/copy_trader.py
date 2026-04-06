import asyncio
import logging
import random
import os
from py_clob_client.client import ClobClient

logger = logging.getLogger(__name__)

# Category-based slippage configuration
CATEGORY_SLIPPAGE = {
    "POLITICS": 0.025, # 2.5%
    "CRYPTO": 0.025,   # 2.5%
    "SPORTS": 0.035,   # 3.5%
    "DEFAULT": 0.025   # Global base limit 2.5%
}

class CopyTrader:
    def __init__(self, trader, scanner):
        self.trader = trader
        self.scanner = scanner

    async def _get_market_category(self, market_id):
        """
        Determines the category of a market by fetching its info.
        """
        try:
            market_info = await asyncio.to_thread(self.scanner.polymarket.get_market, market_id)
            if not isinstance(market_info, dict):
                return "DEFAULT"

            question = market_info.get("question", "").upper()
            if any(kw in question for kw in ["TRUMP", "BIDEN", "ELECTION", "PRESIDENT"]):
                return "POLITICS"
            if any(sym in question for sym in ["BTC", "ETH", "SOL", "XRP", "DOGE", "BNB"]):
                return "CRYPTO"
            if any(kw in question for kw in ["NBA", "NFL", "SOCCER", "TEAM", "MATCH", "SCORE", "GOAL"]):
                return "SPORTS"

            return "DEFAULT"
        except:
            return "DEFAULT"

    async def evaluate_signal(self, whale_action):
        """
        Copy-Trade Logic:
        1. Liquidity Guard: Ensure the target market has 24h Volume > $10,000.
        2. Dynamic Price Guard: Category-based slippage override.
        """
        whale = whale_action.get("whale", "0x...")
        action = whale_action.get("action", "BUY")
        market_id = whale_action.get("market_id")
        whale_entry_price = whale_action.get("price", 0)

        try:
            # 1. Fetch current market price
            current_market_price = await self.scanner.get_token_price(market_id)
            if not current_market_price:
                logger.debug(f"CopyTrader: Invalid price data for {market_id}")
                return False

            # Determine slippage limit based on category
            category = await self._get_market_category(market_id)
            slippage_limit = CATEGORY_SLIPPAGE.get(category, CATEGORY_SLIPPAGE["DEFAULT"])

            # 1. Liquidity Guard: 24h Volume > $10,000
            # (In production we'd fetch actual volume, using 15k for demo)
            market_volume_24h = 15000.0
            if market_volume_24h < 10000:
                logger.debug(f"[WHALE_ACTION] Wallet: {whale[:10]}... | Action: {action} {market_id} | SIGNAL: INSUFFICIENT_LIQUIDITY")
                return False

            # 2. Dynamic Price Guard: Within slippage_limit of whale's entry price
            price_diff_pct = abs(current_market_price - whale_entry_price) / whale_entry_price
            if price_diff_pct > slippage_limit:
                logger.debug(f"[WHALE_ACTION] Wallet: {whale[:10]}... | Action: {action} {market_id} | SIGNAL: SPREAD_TOO_HIGH ({price_diff_pct:.2%}, Limit: {slippage_limit:.2%})")
                return False

            # 3. All Guards Passed -> CopySignal Match
            logger.info(f"[!!! WHALE_ACTION !!!] Wallet: {whale[:10]}... | Action: {action} {market_id} | Price: ${current_market_price:.2f} | Category: {category} | SIGNAL: COPY_MATCH")

            # Execute Paper Copy Trade
            success, msg = await self.trader.execute_trade(
                market_id, "YES", 50.0, current_market_price,
                edge=0.0, confidence=1.0, whale_address=whale
            )
            if success:
                logger.info(f"[!!! SIGNAL !!!] Whale copy-trade executed! {msg}")

            return True
        except Exception as e:
            logger.error(f"CopyTrader: Error evaluating signal - {e}")
            return False
