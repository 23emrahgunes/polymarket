import logging
from typing import Dict, Optional

from src.decision_engine import DecisionEngine, DecisionInputs, classify_market_category
from src.evaluation_utils import normalize_signal_family


logger = logging.getLogger(__name__)


class CopyTrader:
    def __init__(self, trader, scanner, db, decision_engine: DecisionEngine):
        self.trader = trader
        self.scanner = scanner
        self.db = db
        self.decision_engine = decision_engine
        self.market_context_by_id: Dict[str, Dict] = {}
        self.token_to_market_id: Dict[str, str] = {}

    def update_market_contexts(self, market_context_by_id: Dict[str, Dict], token_to_market_id: Dict[str, str]) -> None:
        self.market_context_by_id = dict(market_context_by_id)
        self.token_to_market_id = dict(token_to_market_id)

    async def evaluate_signal(self, whale_action: Dict):
        normalized_event = {
            "type": "WHALE_EVENT",
            "wallet": whale_action.get("whale"),
            "market_id": whale_action.get("market_id"),
            "token_id": whale_action.get("token_id"),
            "side": str(whale_action.get("action", "BUY")).upper(),
            "amount": float(whale_action.get("amount", 0.0) or 0.0),
            "price": whale_action.get("price"),
            "source": "whale_tracker",
        }
        return await self._evaluate_orderflow_event(normalized_event)

    async def evaluate_activity_event(self, event: Dict):
        normalized_event = dict(event)
        normalized_event.setdefault("source", "activity")
        return await self._evaluate_orderflow_event(normalized_event)

    async def _evaluate_orderflow_event(self, event: Dict):
        source = event.get("source", "activity")
        side = str(event.get("side", "BUY")).upper()
        wallet = event.get("wallet")
        token_id = event.get("token_id")
        market_id = event.get("market_id")

        context = self._resolve_market_context(market_id=market_id, token_id=token_id)
        category = context.get("category", "OTHER") if context else "OTHER"
        question = context.get("question", "") if context else ""
        resolved_market_id = context.get("market_id") if context else market_id
        resolved_token_id = context.get("token_id") if context else token_id

        base_inputs = DecisionInputs(
            source=source,
            category=category,
            market_id=resolved_market_id or market_id or "",
            token_id=resolved_token_id,
            event_type=event.get("type"),
            question=question,
            volume_24h=float(context.get("volume_24h", 0.0) if context else 0.0),
            event_amount=float(event.get("amount", 0.0) or 0.0),
            wallets_count=int(event.get("wallets_count", 1) or 1),
            venue="polymarket",
        )

        if context is None:
            decision = self.decision_engine.reject(base_inputs, "market_not_mapped")
            self.decision_engine.log_result(decision, logger)
            return False

        if side != "BUY":
            decision = self.decision_engine.reject(base_inputs, "sell_side_not_supported")
            self.decision_engine.log_result(decision, logger)
            return False

        if event.get("type") == "WHALE_EVENT" and not wallet:
            decision = self.decision_engine.reject(base_inputs, "whale_source_unavailable")
            self.decision_engine.log_result(decision, logger)
            return False

        snapshot = await self.scanner.get_orderbook_snapshot(resolved_token_id)
        if not snapshot["is_valid"]:
            decision = self.decision_engine.reject(
                DecisionInputs(
                    **{
                        **base_inputs.__dict__,
                        "mid_price": snapshot.get("mid_price"),
                        "spread_pct": snapshot.get("spread_pct"),
                        "venue": "polymarket",
                    }
                ),
                snapshot.get("reason", "invalid_orderbook_data"),
            )
            self.decision_engine.log_result(decision, logger)
            return False

        reference_price = event.get("price") or event.get("avg_price") or snapshot["mid_price"]
        price_drift_pct = 0.0
        if reference_price:
            price_drift_pct = abs(snapshot["mid_price"] - reference_price) / reference_price

        whale_trust = 0.5
        if wallet:
            stats = await self.db.get_whale_stats(wallet)
            whale_trust = float(stats["trust_score"]) if stats else 0.5

        decision = self.decision_engine.score_orderflow(
            DecisionInputs(
                source=source,
                category=category,
                market_id=context["market_id"],
                token_id=resolved_token_id,
                event_type=event.get("type"),
                question=question,
                volume_24h=float(context.get("volume_24h", 0.0)),
                mid_price=snapshot["mid_price"],
                spread_pct=snapshot["spread_pct"],
                event_amount=float(event.get("amount", 0.0) or 0.0),
                wallets_count=int(event.get("wallets_count", 1) or 1),
                whale_trust=whale_trust,
                price_drift_pct=price_drift_pct,
                venue="polymarket",
            )
        )
        self.decision_engine.log_result(decision, logger)

        if not decision.should_trade:
            return False

        whale_address = wallet or ("CLUSTER" if event.get("type") == "CLUSTER_DETECTED" else None)
        success, message = await self.trader.execute_trade(
            context["market_id"],
            "YES",
            decision.trade_size,
            snapshot["mid_price"],
            edge=0.0,
            confidence=decision.score,
            whale_address=whale_address,
            source=source,
            category=category,
            venue="polymarket",
            instrument_type="prediction",
            source_signal=source,
            signal_family=normalize_signal_family(source),
            entry_spread_pct=snapshot["spread_pct"],
            whale_trust_at_entry=whale_trust,
            execution_mode="paper",
        )
        if success:
            logger.info(
                "[EXECUTED] source=%s category=%s market=%s detail=%s",
                source,
                category,
                context["market_id"],
                message,
            )
        return success

    def _resolve_market_context(self, market_id: Optional[str], token_id: Optional[str]) -> Optional[Dict]:
        if market_id and market_id in self.market_context_by_id:
            return self.market_context_by_id[market_id]

        if token_id and token_id in self.token_to_market_id:
            resolved_market_id = self.token_to_market_id[token_id]
            return self.market_context_by_id.get(resolved_market_id)

        if market_id and token_id is None and market_id in self.token_to_market_id:
            resolved_market_id = self.token_to_market_id[market_id]
            return self.market_context_by_id.get(resolved_market_id)

        return None
