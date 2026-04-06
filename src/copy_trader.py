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
        Original Whale Tracker Signal Evaluation.
        """
        whale = whale_action.get("whale", "0x...")
        action = whale_action.get("action", "BUY")
        market_id = whale_action.get("market_id")
        whale_entry_price = whale_action.get("price", 0)

        try:
            current_market_price = await self.scanner.get_token_price(market_id)
            if not current_market_price: return False

            category = await self._get_market_category(market_id)
            slippage_limit = CATEGORY_SLIPPAGE.get(category, CATEGORY_SLIPPAGE["DEFAULT"])

            # Liquidity Guard
            market_volume_24h = 15000.0
            if market_volume_24h < 10000: return False

            # Price Guard
            price_diff_pct = abs(current_market_price - whale_entry_price) / whale_entry_price
            if price_diff_pct > slippage_limit: return False

            logger.info(f"[!!! WHALE_ACTION !!!] Wallet: {whale[:10]}... | Action: {action} {market_id} | SIGNAL: COPY_MATCH")

            success, msg = await self.trader.execute_trade(
                market_id, "YES", 50.0, current_market_price,
                edge=0.0, confidence=1.0, whale_address=whale
            )
            if success:
                logger.info(f"[!!! SIGNAL !!!] Whale copy-trade executed! {msg}")
            return True
        except Exception as e:
            logger.error(f"CopyTrader: Error evaluating whale signal - {e}")
            return False

    async def evaluate_activity_event(self, event):
        """
        Ghost Intelligence v4.0: Activity Hunter Signal Evaluation.
        Handles Cluster detection and Big Whale events.
        """
        event_type = event.get("type")
        market_id = event.get("market_id")
        side = event.get("side", "BUY")
        price = event.get("price") or event.get("avg_price", 0)

        try:
            # Common verification for all activity events
            current_market_price = await self.scanner.get_token_price(market_id)
            if not current_market_price: return False

            # Liquidity Guard (>$10k)
            # market_info = await asyncio.to_thread(self.scanner.polymarket.get_market, market_id)
            # market_volume_24h = float(market_info.get("volume_24h", 0))
            market_volume_24h = 15000.0 # Demo
            if market_volume_24h < 10000: return False

            if event_type == "WHALE_EVENT":
                amount = event.get("amount", 0)
                wallet = event.get("wallet", "0x...")
                logger.info(f"[!!! WHALE_ACTION !!!] Large Move Detected! ${amount:,.0f} by {wallet[:10]}... on {market_id}")

                # Big Whale Event Strategy: Execute trade
                success, msg = await self.trader.execute_trade(
                    market_id, side, 50.0, current_market_price,
                    edge=0.0, confidence=0.9, whale_address=wallet
                )
                if success:
                    logger.info(f"[!!! SIGNAL !!!] Big Whale trade executed! {msg}")

            elif event_type == "CLUSTER_DETECTED":
                wallets_count = event.get("wallets_count", 0)
                logger.info(f"[!!! SIGNAL !!!] ACTIVITY CLUSTER! {wallets_count} wallets betting on {market_id} {side} within 120s.")

                # Cluster Strategy: High confidence execute
                success, msg = await self.trader.execute_trade(
                    market_id, side, 50.0, current_market_price,
                    edge=0.0, confidence=0.95, whale_address="CLUSTER"
                )
                if success:
                    logger.info(f"[!!! SIGNAL !!!] Cluster copy-trade executed! {msg}")

            return True
        except Exception as e:
            logger.error(f"CopyTrader: Error evaluating activity event - {e}")
            return False
