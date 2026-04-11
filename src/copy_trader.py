import logging
import time
from typing import Awaitable, Callable, Dict, Optional

from src.decision_engine import COPY_POLICY_GATED_WHALE_COPY, DecisionEngine, DecisionInputs, classify_market_category
from src.evaluation_utils import (
    STRATEGY_PROFILE_BASELINE,
    STRATEGY_PROFILE_SAMPLING_RELAXED,
    normalize_signal_family,
)
from src.market_mapping import (
    choose_primary_token_alias,
    collect_alias_candidates,
    event_is_meaningful_for_lazy_lookup,
    normalize_market_alias,
    TOKENISH_ALIAS_PATTERN,
)


logger = logging.getLogger(__name__)
SAMPLING_ELIGIBLE_CATEGORIES = {"SPORTS", "POLITICS", "OTHER"}
UNRESOLVED_ALIAS_RETRY_WINDOW_SECONDS = 900.0
UNRESOLVED_ALIAS_RETRY_MIN_EVENTS = 2
UNRESOLVED_ALIAS_RETRY_MIN_TOTAL_AMOUNT = 250.0
UNRESOLVED_ALIAS_RETRY_MIN_UNIQUE_WALLETS = 2
SAMPLING_ORDERFLOW_WINDOW_SECONDS = 300.0
SAMPLING_ORDERFLOW_MIN_EVENTS = 2
SAMPLING_ORDERFLOW_MIN_TOTAL_AMOUNT = 500.0
WHALE_COPY_WINDOW_SECONDS = 600.0
WHALE_COPY_MIN_TOTAL_AMOUNT = 500.0
WHALE_COPY_MIN_UNIQUE_WALLETS = 2


class CopyTrader:
    def __init__(
        self,
        trader,
        scanner,
        db,
        decision_engine: DecisionEngine,
        market_resolver: Optional[Callable[[list[str], str], Awaitable[Optional[Dict]]]] = None,
        lookup_context_resolver: Optional[Callable[[list[str]], Optional[Dict]]] = None,
        mapping_event_callback: Optional[Callable[..., Awaitable[None]]] = None,
        market_promotion_callback: Optional[Callable[..., Awaitable[bool]]] = None,
    ):
        self.trader = trader
        self.scanner = scanner
        self.db = db
        self.decision_engine = decision_engine
        self.market_resolver = market_resolver
        self.lookup_context_resolver = lookup_context_resolver
        self.mapping_event_callback = mapping_event_callback
        self.market_promotion_callback = market_promotion_callback
        self.market_context_by_id: Dict[str, Dict] = {}
        self.token_to_market_id: Dict[str, str] = {}
        self.sampling_enabled = False
        self.sampling_target_reached = False
        self.sampling_closed_trades = 0
        self.sampling_target_closed_trades = 20
        self.sampling_stop_reason: Optional[str] = None
        self.unresolved_alias_retry_state: Dict[str, Dict] = {}
        self.sampling_orderflow_state: Dict[str, Dict] = {}
        self.whale_copy_state: Dict[str, Dict] = {}

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

    def update_sampling_state(
        self,
        *,
        enabled: bool,
        target_reached: bool,
        closed_trades: int,
        target_closed_trades: int,
        stop_reason: Optional[str] = None,
    ) -> None:
        self.sampling_enabled = enabled
        self.sampling_target_reached = target_reached
        self.sampling_closed_trades = closed_trades
        self.sampling_target_closed_trades = target_closed_trades
        self.sampling_stop_reason = stop_reason

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
            event.get("asset"),
            event.get("conditionId"),
            event.get("slug"),
            extra=event.get("alias_candidates"),
        )
        context, mapping_stage, mapping_reason, lazy_lookup_attempted, lazy_lookup_hit = await self._resolve_market_context(
            market_id=market_id,
            token_id=token_id,
            alias_candidates=alias_candidates,
            source=source,
            event=event,
        )
        hot_window_hit = mapping_stage == "hot_window"
        hot_window_promoted = False
        if (
            context is not None
            and mapping_stage in {"alias_cache", "lazy_lookup", "lookup_universe", "unresolved_retry"}
            and self.market_promotion_callback is not None
        ):
            hot_window_promoted = bool(
                await self.market_promotion_callback(
                    context=context,
                    event=event,
                    source=source,
                    stage=mapping_stage,
                )
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
            hot_window_hit=hot_window_hit,
            hot_window_promoted=hot_window_promoted,
            strategy_profile=STRATEGY_PROFILE_BASELINE,
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
            decision = self.decision_engine.reject(base_inputs, "unsupported_side_filtered")
            self.decision_engine.log_result(decision, logger)
            return False

        if event.get("type") == "WHALE_EVENT" and not wallet:
            decision = self.decision_engine.reject(base_inputs, "whale_source_unavailable")
            self.decision_engine.log_result(decision, logger)
            return False

        whale_trust = 0.5
        if wallet:
            stats = await self.db.get_whale_stats(wallet)
            whale_trust = float(stats["trust_score"]) if stats else 0.5

        whale_copy_summary = None
        whale_copy_applicable = category in SAMPLING_ELIGIBLE_CATEGORIES and wallet is not None and source in {"whale_tracker", "activity"}
        if whale_copy_applicable:
            whale_copy_summary = self._record_whale_copy_candidate(
                context=context,
                event=event,
                source=source,
                wallet=wallet,
                whale_trust=whale_trust,
            )
        copy_policy = COPY_POLICY_GATED_WHALE_COPY if whale_copy_summary is not None else None

        snapshot, orderbook_token_id, token_recovery_meta = await self._get_orderbook_snapshot_with_recovery(
            context=context,
            event=event,
            primary_token_id=resolved_token_id,
        )
        if not snapshot["is_valid"]:
            reject_reasons = [snapshot.get("reason", "invalid_orderbook_data")]
            if token_recovery_meta.get("token_recovery_failed"):
                reject_reasons.append("token_recovery_failed")
            decision = self.decision_engine.reject(
                DecisionInputs(
                    **{
                    **base_inputs.__dict__,
                        "token_id": orderbook_token_id or resolved_token_id,
                        "mid_price": snapshot.get("mid_price"),
                        "spread_pct": snapshot.get("spread_pct"),
                        "venue": "polymarket",
                        "copy_policy": copy_policy,
                    }
                ),
                *self._dedupe_reasons(reject_reasons),
            )
            decision.inputs.update(token_recovery_meta)
            self.decision_engine.log_result(decision, logger)
            return False

        reference_price = event.get("price") or event.get("avg_price") or snapshot["mid_price"]
        price_drift_pct = 0.0
        if reference_price:
            price_drift_pct = abs(snapshot["mid_price"] - reference_price) / reference_price

        scored_inputs = DecisionInputs(
            source=source,
            category=category,
            market_id=context["market_id"],
            token_id=orderbook_token_id or resolved_token_id,
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
            hot_window_hit=hot_window_hit,
            hot_window_promoted=hot_window_promoted,
            strategy_profile=STRATEGY_PROFILE_BASELINE,
        )
        sampling_applicable = category in SAMPLING_ELIGIBLE_CATEGORIES
        sampling_orderflow_summary = None
        if sampling_applicable and self.sampling_enabled:
            sampling_orderflow_summary = self._record_sampling_orderflow(
                context=context,
                event=event,
                source=source,
                wallet=wallet,
                whale_trust=whale_trust,
        )
        evaluation_inputs = scored_inputs
        if whale_copy_summary is not None and whale_copy_summary.get("ready_for_gate"):
            evaluation_inputs = self._build_whale_copy_inputs(
                scored_inputs,
                whale_copy_summary,
                sampling_enabled=self.sampling_enabled,
            )
        decision = self.decision_engine.score_orderflow(evaluation_inputs)

        if not decision.should_trade and sampling_applicable:
            if self.sampling_enabled:
                retry_summary = None
                if whale_copy_summary is not None and whale_copy_summary.get("ready_for_retry"):
                    retry_summary = whale_copy_summary
                elif sampling_orderflow_summary is not None and sampling_orderflow_summary.get("ready_for_retry"):
                    retry_summary = sampling_orderflow_summary

                if retry_summary is not None:
                    sampling_inputs = self._build_sampling_orderflow_inputs(evaluation_inputs, retry_summary)
                    decision = self.decision_engine.score_orderflow(sampling_inputs)
                    decision.inputs.update(
                        {
                            "copy_policy": "gated_whale_copy" if retry_summary is whale_copy_summary else "sampling_relaxed",
                            "aggregated_orderflow_events": retry_summary["event_count"],
                            "aggregated_total_notional": round(retry_summary["total_amount"], 4),
                            "aggregated_unique_wallets": retry_summary["unique_wallets"],
                            "aggregated_source_count": retry_summary["source_count"],
                        }
                    )
            elif self.sampling_target_reached:
                decision = self.decision_engine.reject(
                    DecisionInputs(**{**scored_inputs.__dict__, "strategy_profile": STRATEGY_PROFILE_SAMPLING_RELAXED}),
                    *self._dedupe_reasons([*decision.reasons, "sampling_target_reached"]),
                )
        if whale_copy_summary is not None:
            decision.inputs.update(
                {
                    "copy_policy": COPY_POLICY_GATED_WHALE_COPY,
                    "gated_whale_event_count": whale_copy_summary["event_count"],
                    "gated_total_notional": round(whale_copy_summary["total_amount"], 4),
                    "gated_unique_wallets": whale_copy_summary["unique_wallets"],
                    "gated_max_trust": round(whale_copy_summary["max_trust"], 4),
                    "gated_source_count": whale_copy_summary["source_count"],
                }
            )
        decision.inputs.update(token_recovery_meta)
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
            strategy_profile=decision.strategy_profile,
            entry_spread_pct=snapshot["spread_pct"],
            whale_trust_at_entry=whale_trust,
            execution_mode="paper",
            audit_inputs=decision.inputs,
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

        context, memory_stage = self._resolve_from_memory(market_id, token_id, alias_candidates)
        if context is not None:
            return context, memory_stage, "mapped", False, False

        cached_context = await self._resolve_from_alias_cache(alias_candidates)
        if cached_context is not None:
            self._clear_unresolved_alias_retry(alias_candidates)
            self._cache_context(cached_context, alias_candidates)
            return cached_context, "alias_cache", "mapped", False, False

        if self.lookup_context_resolver is not None:
            lookup_context = self.lookup_context_resolver(alias_candidates)
            if lookup_context is not None:
                self._clear_unresolved_alias_retry(alias_candidates)
                self._cache_context(lookup_context, alias_candidates)
                return lookup_context, "lookup_universe", "mapped", False, False

        should_retry, retry_stage = self._should_attempt_unresolved_alias_retry(
            alias_candidates=alias_candidates,
            event=event,
            source=source,
        )
        if not should_retry:
            fallback_stage = "active_window" if self.lookup_context_resolver is None else "lazy_lookup"
            fallback_reason = "market_not_mapped_active_window" if self.lookup_context_resolver is None else "market_not_mapped_lazy_lookup_failed"
            return None, fallback_stage, fallback_reason, False, False

        if self.market_resolver is None:
            return None, retry_stage, "market_not_mapped_lazy_lookup_failed", False, False

        lazy_context = await self.market_resolver(alias_candidates, source)
        if lazy_context is not None:
            self._clear_unresolved_alias_retry(alias_candidates)
            self._cache_context(lazy_context, alias_candidates)
            return lazy_context, retry_stage, "mapped", True, True
        return None, retry_stage, "market_not_mapped_lazy_lookup_failed", True, False

    def _resolve_from_memory(
        self,
        market_id: Optional[str],
        token_id: Optional[str],
        alias_candidates: list[str],
    ) -> tuple[Optional[Dict], str]:
        normalized_market_id = normalize_market_alias(market_id)
        normalized_token_id = normalize_market_alias(token_id)

        if normalized_market_id and normalized_market_id in self.market_context_by_id:
            context = self.market_context_by_id[normalized_market_id]
            self._clear_unresolved_alias_retry(alias_candidates)
            return context, str(context.get("trade_context_source", "active_context"))

        if normalized_token_id and normalized_token_id in self.token_to_market_id:
            resolved_market_id = self.token_to_market_id[normalized_token_id]
            context = self.market_context_by_id.get(resolved_market_id)
            if context is not None:
                self._clear_unresolved_alias_retry(alias_candidates)
                return context, str(context.get("trade_context_source", "active_context"))

        if normalized_market_id and normalized_token_id is None and normalized_market_id in self.token_to_market_id:
            resolved_market_id = self.token_to_market_id[normalized_market_id]
            context = self.market_context_by_id.get(resolved_market_id)
            if context is not None:
                self._clear_unresolved_alias_retry(alias_candidates)
                return context, str(context.get("trade_context_source", "active_context"))

        for alias in alias_candidates:
            normalized_alias = normalize_market_alias(alias)
            if not normalized_alias:
                continue
            if normalized_alias in self.market_context_by_id:
                context = self.market_context_by_id[normalized_alias]
                self._clear_unresolved_alias_retry(alias_candidates)
                return context, str(context.get("trade_context_source", "active_context"))
            if normalized_alias in self.token_to_market_id:
                resolved_market_id = self.token_to_market_id[normalized_alias]
                context = self.market_context_by_id.get(resolved_market_id)
                if context is not None:
                    self._clear_unresolved_alias_retry(alias_candidates)
                    return context, str(context.get("trade_context_source", "active_context"))
        return None, "active_context"

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

        market_alias_rows = await self.db.get_market_aliases_for_market(normalized_market_id)
        all_aliases = [market_alias_row["alias"] for market_alias_row in market_alias_rows if market_alias_row["alias"]]

        token_alias = choose_primary_token_alias(
            [market_alias_row["alias"] for market_alias_row in market_alias_rows],
            market_id=normalized_market_id,
            fallback=alias_row["alias"] if alias_row["alias_type"] == "token_id" else None,
        )

        hydrated_aliases = collect_alias_candidates(
            normalized_market_id,
            token_alias,
            extra=[*all_aliases, *alias_candidates],
        )

        return {
            "market_id": normalized_market_id,
            "token_id": token_alias,
            "token_ids": [alias for alias in all_aliases if alias != normalized_market_id],
            "question": alias_row["question"] or "",
            "category": alias_row["category"] or classify_market_category(alias_row["question"] or ""),
            "volume_24h": float(alias_row["volume_24h"] or 0.0),
            "active": bool(alias_row["active"]),
            "alias_candidates": hydrated_aliases,
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
            aliases=collect_alias_candidates(
                context.get("market_id"),
                context.get("token_id"),
                context.get("token_ids", []),
                context.get("asset"),
                context.get("conditionId"),
                context.get("slug"),
                extra=[*alias_candidates, *(context.get("alias_candidates", []) or [])],
            ),
            question=context.get("question"),
            category=context.get("category"),
            volume_24h=float(context.get("volume_24h", 0.0) or 0.0),
            active=bool(context.get("active", True)),
            source=source,
        )

    def _should_attempt_unresolved_alias_retry(
        self,
        *,
        alias_candidates: list[str],
        event: Dict,
        source: str,
    ) -> tuple[bool, str]:
        if event_is_meaningful_for_lazy_lookup(event):
            self._register_unresolved_alias_retry(alias_candidates, event, source)
            return True, "lazy_lookup"

        state = self._register_unresolved_alias_retry(alias_candidates, event, source)
        if state is None:
            return False, "active_window"

        retry_ready = (
            state["count"] >= UNRESOLVED_ALIAS_RETRY_MIN_EVENTS
            and (
                state["total_amount"] >= UNRESOLVED_ALIAS_RETRY_MIN_TOTAL_AMOUNT
                or len(state["wallets"]) >= UNRESOLVED_ALIAS_RETRY_MIN_UNIQUE_WALLETS
                or int(state["max_wallets_count"]) >= UNRESOLVED_ALIAS_RETRY_MIN_UNIQUE_WALLETS
                or len(state["sources"]) >= UNRESOLVED_ALIAS_RETRY_MIN_UNIQUE_WALLETS
            )
        )
        return retry_ready, ("unresolved_retry" if retry_ready else "active_window")

    def _register_unresolved_alias_retry(self, alias_candidates: list[str], event: Dict, source: str) -> Optional[Dict]:
        signature = self._alias_signature(alias_candidates)
        if signature is None:
            return None

        now_ts = time.time()
        self._prune_unresolved_alias_retries(now_ts)
        state = self.unresolved_alias_retry_state.setdefault(
            signature,
            {
                "count": 0,
                "total_amount": 0.0,
                "wallets": set(),
                "sources": set(),
                "max_wallets_count": 1,
                "last_seen_ts": now_ts,
            },
        )
        state["count"] += 1
        state["total_amount"] += float(event.get("amount", 0.0) or 0.0)
        wallet = normalize_market_alias(event.get("wallet"))
        if wallet:
            state["wallets"].add(wallet)
        state["sources"].add(str(source or event.get("source") or "unknown"))
        state["max_wallets_count"] = max(int(state["max_wallets_count"]), int(event.get("wallets_count", 1) or 1))
        state["last_seen_ts"] = now_ts
        return state

    def _clear_unresolved_alias_retry(self, alias_candidates: list[str]) -> None:
        signature = self._alias_signature(alias_candidates)
        if signature is not None:
            self.unresolved_alias_retry_state.pop(signature, None)

    def _prune_unresolved_alias_retries(self, now_ts: Optional[float] = None) -> None:
        now_ts = now_ts or time.time()
        expired_signatures = [
            signature
            for signature, state in self.unresolved_alias_retry_state.items()
            if now_ts - float(state.get("last_seen_ts", 0.0)) > UNRESOLVED_ALIAS_RETRY_WINDOW_SECONDS
        ]
        for signature in expired_signatures:
            self.unresolved_alias_retry_state.pop(signature, None)

    def _record_sampling_orderflow(
        self,
        *,
        context: Dict,
        event: Dict,
        source: str,
        wallet: Optional[str],
        whale_trust: float,
    ) -> Dict:
        market_id = normalize_market_alias(context.get("market_id"))
        if not market_id:
            return {
                "event_count": 1,
                "total_amount": float(event.get("amount", 0.0) or 0.0),
                "unique_wallets": int(event.get("wallets_count", 1) or 1),
                "source_count": 1,
                "max_trust": whale_trust,
                "max_wallets_count": int(event.get("wallets_count", 1) or 1),
            }

        now_ts = time.time()
        state = self.sampling_orderflow_state.setdefault(market_id, {"events": []})
        events = [
            item
            for item in state["events"]
            if now_ts - float(item.get("ts", 0.0)) <= SAMPLING_ORDERFLOW_WINDOW_SECONDS
        ]
        events.append(
            {
                "ts": now_ts,
                "amount": float(event.get("amount", 0.0) or 0.0),
                "wallet": normalize_market_alias(wallet),
                "source": str(source or event.get("source") or "unknown"),
                "wallets_count": int(event.get("wallets_count", 1) or 1),
                "trust": float(whale_trust),
            }
        )
        state["events"] = events

        wallets = {item["wallet"] for item in events if item.get("wallet")}
        total_amount = sum(float(item.get("amount", 0.0) or 0.0) for item in events)
        max_wallets_count = max(int(item.get("wallets_count", 1) or 1) for item in events)
        return {
            "event_count": len(events),
            "total_amount": total_amount,
            "unique_wallets": max(len(wallets), max_wallets_count),
            "source_count": len({item.get("source") for item in events if item.get("source")}),
            "max_trust": max(float(item.get("trust", whale_trust) or whale_trust) for item in events),
            "max_wallets_count": max_wallets_count,
            "ready_for_retry": len(events) >= SAMPLING_ORDERFLOW_MIN_EVENTS or total_amount >= SAMPLING_ORDERFLOW_MIN_TOTAL_AMOUNT,
        }

    def _record_whale_copy_candidate(
        self,
        *,
        context: Dict,
        event: Dict,
        source: str,
        wallet: Optional[str],
        whale_trust: float,
    ) -> Dict:
        market_id = normalize_market_alias(context.get("market_id"))
        if not market_id:
            return {
                "event_count": 1,
                "total_amount": float(event.get("amount", 0.0) or 0.0),
                "unique_wallets": max(int(event.get("wallets_count", 1) or 1), 1),
                "source_count": 1,
                "max_trust": whale_trust,
                "max_wallets_count": int(event.get("wallets_count", 1) or 1),
                "ready_for_gate": False,
                "ready_for_retry": False,
            }

        now_ts = time.time()
        state = self.whale_copy_state.setdefault(market_id, {"events": []})
        events = [
            item
            for item in state["events"]
            if now_ts - float(item.get("ts", 0.0)) <= WHALE_COPY_WINDOW_SECONDS
        ]
        events.append(
            {
                "ts": now_ts,
                "amount": float(event.get("amount", 0.0) or 0.0),
                "wallet": normalize_market_alias(wallet),
                "source": str(source or event.get("source") or "unknown"),
                "wallets_count": int(event.get("wallets_count", 1) or 1),
                "trust": float(whale_trust),
            }
        )
        state["events"] = events

        wallets = {item["wallet"] for item in events if item.get("wallet")}
        total_amount = sum(float(item.get("amount", 0.0) or 0.0) for item in events)
        max_wallets_count = max(int(item.get("wallets_count", 1) or 1) for item in events)
        unique_wallets = max(len(wallets), max_wallets_count)
        ready_for_gate = total_amount >= WHALE_COPY_MIN_TOTAL_AMOUNT or unique_wallets >= WHALE_COPY_MIN_UNIQUE_WALLETS
        return {
            "event_count": len(events),
            "total_amount": total_amount,
            "unique_wallets": unique_wallets,
            "source_count": len({item.get("source") for item in events if item.get("source")}),
            "max_trust": max(float(item.get("trust", whale_trust) or whale_trust) for item in events),
            "max_wallets_count": max_wallets_count,
            "ready_for_gate": ready_for_gate,
            "ready_for_retry": ready_for_gate,
        }

    @staticmethod
    def _build_whale_copy_inputs(inputs: DecisionInputs, summary: Dict, *, sampling_enabled: bool = False) -> DecisionInputs:
        overrides = {}
        if sampling_enabled:
            overrides = {
                "strategy_profile": STRATEGY_PROFILE_SAMPLING_RELAXED,
                "copy_policy": COPY_POLICY_GATED_WHALE_COPY,
            }
        return DecisionInputs(
            **{
                **inputs.__dict__,
                "event_amount": max(float(inputs.event_amount or 0.0), float(summary["total_amount"])),
                "wallets_count": max(int(inputs.wallets_count or 1), int(summary["unique_wallets"]), int(summary["max_wallets_count"])),
                "whale_trust": max(float(inputs.whale_trust), float(summary["max_trust"])),
                **overrides,
            }
        )

    @staticmethod
    def _build_sampling_orderflow_inputs(inputs: DecisionInputs, summary: Optional[Dict]) -> DecisionInputs:
        if not summary:
            return DecisionInputs(**{**inputs.__dict__, "strategy_profile": STRATEGY_PROFILE_SAMPLING_RELAXED})
        return DecisionInputs(
            **{
                **inputs.__dict__,
                "event_amount": max(float(inputs.event_amount or 0.0), float(summary["total_amount"])),
                "wallets_count": max(int(inputs.wallets_count or 1), int(summary["unique_wallets"]), int(summary["max_wallets_count"])),
                "whale_trust": max(float(inputs.whale_trust), float(summary["max_trust"])),
                "strategy_profile": STRATEGY_PROFILE_SAMPLING_RELAXED,
            }
        )

    async def _get_orderbook_snapshot_with_recovery(
        self,
        *,
        context: Dict,
        event: Dict,
        primary_token_id: Optional[str],
    ) -> tuple[Dict, Optional[str], Dict]:
        candidates = self._build_orderbook_token_candidates(
            context=context,
            event=event,
            primary_token_id=primary_token_id,
        )
        metadata = {
            "token_recovery_attempted": False,
            "token_recovery_hit": False,
            "token_recovery_failed": False,
            "orderbook_token_id": candidates[0] if candidates else normalize_market_alias(primary_token_id),
        }
        if not candidates:
            return self._invalid_orderbook_snapshot(primary_token_id, "missing_polymarket_token_price"), None, metadata

        primary_token = candidates[0]
        primary_snapshot = await self.scanner.get_orderbook_snapshot(primary_token)
        metadata["orderbook_token_id"] = primary_snapshot.get("token_id") or primary_token
        if primary_snapshot["is_valid"]:
            return primary_snapshot, primary_token, metadata

        if primary_snapshot.get("reason") != "missing_polymarket_token_price" or len(candidates) == 1:
            return primary_snapshot, primary_token, metadata

        metadata["token_recovery_attempted"] = True
        metadata["token_recovery_candidates"] = candidates[1:]
        for candidate in candidates[1:]:
            snapshot = await self.scanner.get_orderbook_snapshot(candidate)
            if snapshot["is_valid"]:
                metadata["token_recovery_hit"] = True
                metadata["orderbook_token_id"] = snapshot.get("token_id") or candidate
                return snapshot, candidate, metadata

        metadata["token_recovery_failed"] = True
        return primary_snapshot, primary_token, metadata

    @staticmethod
    def _build_orderbook_token_candidates(
        *,
        context: Dict,
        event: Dict,
        primary_token_id: Optional[str],
    ) -> list[str]:
        market_id = normalize_market_alias(context.get("market_id"))
        candidates: list[str] = []

        def _add(value, *, require_tokenish: bool = False) -> None:
            for alias in collect_alias_candidates(value):
                if not alias or alias == market_id:
                    continue
                if require_tokenish and not TOKENISH_ALIAS_PATTERN.fullmatch(alias):
                    continue
                if alias not in candidates:
                    candidates.append(alias)

        # Prefer the event token/asset first: it is closest to the whale action we are mirroring.
        _add(event.get("token_id"))
        _add(event.get("asset"))
        _add(primary_token_id)
        _add(context.get("token_id"))
        _add(context.get("token_ids", []))
        _add(context.get("alias_candidates", []), require_tokenish=True)
        return candidates[:8]

    @staticmethod
    def _invalid_orderbook_snapshot(token_id: Optional[str], reason: str) -> Dict:
        return {
            "token_id": token_id,
            "best_bid": None,
            "best_ask": None,
            "mid_price": None,
            "spread_pct": None,
            "is_valid": False,
            "reason": reason,
            "fetched_at": time.time(),
        }

    @staticmethod
    def _alias_signature(alias_candidates: list[str]) -> Optional[str]:
        aliases = sorted({alias for alias in alias_candidates if normalize_market_alias(alias)})
        if not aliases:
            return None
        return "|".join(aliases)

    @staticmethod
    def _dedupe_reasons(reasons: list[str]) -> list[str]:
        ordered: list[str] = []
        seen: set[str] = set()
        for reason in reasons:
            if reason and reason not in seen:
                seen.add(reason)
                ordered.append(reason)
        return ordered
