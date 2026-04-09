import logging
from typing import Awaitable, Callable, Dict, Optional

from src.decision_engine import DecisionEngine, DecisionInputs, classify_market_category
from src.evaluation_utils import normalize_signal_family
from src.market_mapping import collect_alias_candidates, event_is_meaningful_for_lazy_lookup, normalize_market_alias


logger = logging.getLogger(__name__)


class CopyTrader:
    def __init__(
        self,
        trader,
        scanner,
        db,
        decision_engine: DecisionEngine,
        market_resolver: Optional[Callable[[list[str], str], Awaitable[Optional[Dict]]]] = None,
        mapping_event_callback: Optional[Callable[..., Awaitable[None]]] = None,
    ):
        self.trader = trader
        self.scanner = scanner
        self.db = db
        self.decision_engine = decision_engine
        self.market_resolver = market_resolver
        self.mapping_event_callback = mapping_event_callback
        self.market_context_by_id: Dict[str, Dict] = {}
        self.token_to_market_id: Dict[str, str] = {}

    def update_market_contexts(self, market_context_by_id: Dict[str, Dict], token_to_market_id: Dict[str, str]) -> None:
        self.market_context_by_id = {
            normalized_key: dict(value)
            for key, value in market_context_by_id.items()
            if (normalized_key := normalize_market_alias(key))
        }
        self.token_to_market_id = {}
        for token_alias, market_id in token_to_market_id.items():
            normalized_alias = normalize_market_alias(token_alias)
            normalized_market_id = normalize_market_alias(market_id)
            if normalized_alias and normalized_market_id:
                self.token_to_market_id[normalized_alias] = normalized_market_id

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
            "alias_candidates": whale_action.get("alias_candidates"),
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
        alias_candidates = collect_alias_candidates(
            market_id,
            token_id,
            extra=event.get("alias_candidates"),
        )
        context, mapping_stage, mapping_reason, lazy_lookup_attempted, lazy_lookup_hit = await self._resolve_market_context(
            market_id=market_id,
            token_id=token_id,
            alias_candidates=alias_candidates,
            source=source,
            event=event,
        )
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
            mapping_stage=mapping_stage,
            alias_candidates=alias_candidates,
            lazy_lookup_attempted=lazy_lookup_attempted,
            lazy_lookup_hit=lazy_lookup_hit,
        )

        if context is None:
            if self.mapping_event_callback is not None:
                await self.mapping_event_callback(mapped=False, stage=mapping_stage)
            decision = self.decision_engine.reject(base_inputs, mapping_reason)
            self.decision_engine.log_result(decision, logger)
            return False
        if self.mapping_event_callback is not None:
            await self.mapping_event_callback(mapped=True, stage=mapping_stage)

        await self._persist_event_aliases(context, alias_candidates, source)

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
                mapping_stage=mapping_stage,
                alias_candidates=alias_candidates,
                lazy_lookup_attempted=lazy_lookup_attempted,
                lazy_lookup_hit=lazy_lookup_hit,
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

    async def _resolve_market_context(
        self,
        market_id: Optional[str],
        token_id: Optional[str],
        alias_candidates: list[str],
        source: str,
        event: Dict,
    ) -> tuple[Optional[Dict], str, str, bool, bool]:
        if not alias_candidates:
            return None, "unknown_token", "market_not_mapped_unknown_token", False, False

        context = self._resolve_from_memory(market_id, token_id, alias_candidates)
        if context is not None:
            return context, "active_context", "mapped", False, False

        cached_context = await self._resolve_from_alias_cache(alias_candidates)
        if cached_context is not None:
            self._cache_context(cached_context, alias_candidates)
            return cached_context, "alias_cache", "mapped", False, False

        if not event_is_meaningful_for_lazy_lookup(event):
            return None, "active_window", "market_not_mapped_active_window", False, False

        if self.market_resolver is None:
            return None, "lazy_lookup", "market_not_mapped_lazy_lookup_failed", False, False

        lazy_context = await self.market_resolver(alias_candidates, source)
        if lazy_context is not None:
            self._cache_context(lazy_context, alias_candidates)
            return lazy_context, "lazy_lookup", "mapped", True, True
        return None, "lazy_lookup", "market_not_mapped_lazy_lookup_failed", True, False

    def _resolve_from_memory(
        self,
        market_id: Optional[str],
        token_id: Optional[str],
        alias_candidates: list[str],
    ) -> Optional[Dict]:
        normalized_market_id = normalize_market_alias(market_id)
        normalized_token_id = normalize_market_alias(token_id)

        if normalized_market_id and normalized_market_id in self.market_context_by_id:
            return self.market_context_by_id[normalized_market_id]

        if normalized_token_id and normalized_token_id in self.token_to_market_id:
            resolved_market_id = self.token_to_market_id[normalized_token_id]
            return self.market_context_by_id.get(resolved_market_id)

        if normalized_market_id and normalized_token_id is None and normalized_market_id in self.token_to_market_id:
            resolved_market_id = self.token_to_market_id[normalized_market_id]
            return self.market_context_by_id.get(resolved_market_id)

        for alias in alias_candidates:
            normalized_alias = normalize_market_alias(alias)
            if not normalized_alias:
                continue
            if normalized_alias in self.market_context_by_id:
                return self.market_context_by_id[normalized_alias]
            if normalized_alias in self.token_to_market_id:
                resolved_market_id = self.token_to_market_id[normalized_alias]
                return self.market_context_by_id.get(resolved_market_id)
        return None

    async def _resolve_from_alias_cache(self, alias_candidates: list[str]) -> Optional[Dict]:
        if self.db is None:
            return None

        alias_row = await self.db.resolve_market_alias(alias_candidates)
        if alias_row is None:
            return None

        if int(alias_row["active"] or 0) != 1:
            return None

        normalized_market_id = normalize_market_alias(alias_row["market_id"])
        if normalized_market_id in self.market_context_by_id:
            return self.market_context_by_id[normalized_market_id]

        token_alias = None
        if alias_row["alias_type"] == "token_id":
            token_alias = alias_row["alias"]
        else:
            market_alias_rows = await self.db.get_market_aliases_for_market(normalized_market_id)
            for market_alias_row in market_alias_rows:
                if market_alias_row["alias_type"] == "token_id":
                    token_alias = market_alias_row["alias"]
                    break

        return {
            "market_id": normalized_market_id,
            "token_id": token_alias,
            "token_ids": [token_alias] if token_alias else [],
            "question": alias_row["question"] or "",
            "category": alias_row["category"] or classify_market_category(alias_row["question"] or ""),
            "volume_24h": float(alias_row["volume_24h"] or 0.0),
            "active": bool(alias_row["active"]),
            "alias_candidates": alias_candidates,
        }

    def _cache_context(self, context: Dict, alias_candidates: list[str]) -> None:
        normalized_market_id = normalize_market_alias(context.get("market_id"))
        if not normalized_market_id:
            return

        normalized_context = dict(context)
        normalized_context["market_id"] = normalized_market_id
        normalized_context["token_id"] = normalize_market_alias(context.get("token_id")) or normalized_context.get("token_id")
        normalized_context["alias_candidates"] = collect_alias_candidates(
            normalized_market_id,
            normalized_context.get("token_id"),
            extra=alias_candidates,
        )
        self.market_context_by_id[normalized_market_id] = normalized_context
        for alias in normalized_context["alias_candidates"]:
            if alias == normalized_market_id:
                continue
            self.token_to_market_id[alias] = normalized_market_id

    async def _persist_event_aliases(self, context: Dict, alias_candidates: list[str], source: str) -> None:
        if self.db is None:
            return
        await self.db.upsert_market_aliases(
            market_id=context["market_id"],
            aliases=collect_alias_candidates(context.get("market_id"), context.get("token_id"), extra=alias_candidates),
            question=context.get("question"),
            category=context.get("category"),
            volume_24h=float(context.get("volume_24h", 0.0) or 0.0),
            active=bool(context.get("active", True)),
            source=source,
        )
