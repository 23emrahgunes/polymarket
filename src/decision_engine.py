from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


logger = logging.getLogger(__name__)


def clamp(value: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    return max(minimum, min(maximum, value))


def classify_market_category(question: str) -> str:
    normalized = (question or "").upper()
    if any(symbol in normalized for symbol in ["BTC", "ETH", "SOL", "XRP", "DOGE", "BNB"]):
        return "CRYPTO"
    if any(keyword in normalized for keyword in ["NBA", "NFL", "SOCCER", "MATCH", "TEAM", "SCORE", "GOAL", "CHAMPIONSHIP"]):
        return "SPORTS"
    if any(keyword in normalized for keyword in ["TRUMP", "BIDEN", "ELECTION", "PRESIDENT", "SENATE", "HOUSE", "GOVERNOR", "PRIME MINISTER"]):
        return "POLITICS"
    return "OTHER"


@dataclass(frozen=True)
class CategoryRiskProfile:
    category: str
    min_volume_24h: float
    min_whale_notional: float
    min_cluster_wallets: int
    max_spread_pct: float
    max_price_drift_pct: float
    min_score: float
    paper_trade_size: float


@dataclass
class DecisionInputs:
    source: str
    category: str
    market_id: str
    token_id: Optional[str] = None
    event_type: Optional[str] = None
    question: str = ""
    volume_24h: float = 0.0
    mid_price: Optional[float] = None
    spread_pct: Optional[float] = None
    edge: Optional[float] = None
    rsi: Optional[float] = None
    event_amount: Optional[float] = None
    wallets_count: Optional[int] = None
    whale_trust: float = 0.5
    price_drift_pct: Optional[float] = None


@dataclass
class DecisionResult:
    source: str
    category: str
    market_id: str
    score: float
    threshold: float
    should_trade: bool
    reasons: List[str]
    trade_size: float
    inputs: Dict[str, Any]


RISK_PROFILES: Dict[str, CategoryRiskProfile] = {
    "CRYPTO": CategoryRiskProfile("CRYPTO", 50_000.0, 1_500.0, 2, 0.03, 0.02, 0.68, 50.0),
    "SPORTS": CategoryRiskProfile("SPORTS", 25_000.0, 1_000.0, 3, 0.04, 0.03, 0.72, 40.0),
    "POLITICS": CategoryRiskProfile("POLITICS", 40_000.0, 1_250.0, 3, 0.035, 0.025, 0.74, 35.0),
    "OTHER": CategoryRiskProfile("OTHER", 30_000.0, 1_500.0, 4, 0.05, 0.03, 0.78, 25.0),
}

DISCOVERY_TRADE_CATEGORIES = {"CRYPTO"}


class DecisionEngine:
    def get_profile(self, category: str) -> CategoryRiskProfile:
        return RISK_PROFILES.get(category or "OTHER", RISK_PROFILES["OTHER"])

    def reject(self, inputs: DecisionInputs, *reasons: str) -> DecisionResult:
        profile = self.get_profile(inputs.category)
        return DecisionResult(
            source=inputs.source,
            category=inputs.category,
            market_id=inputs.market_id,
            score=0.0,
            threshold=profile.min_score,
            should_trade=False,
            reasons=list(reasons) or ["rejected"],
            trade_size=profile.paper_trade_size,
            inputs=self._inputs_for_log(inputs),
        )

    def score_discovery(self, inputs: DecisionInputs) -> DecisionResult:
        profile = self.get_profile(inputs.category)
        reasons: List[str] = []

        if inputs.category not in DISCOVERY_TRADE_CATEGORIES:
            reasons.append("route_whale_orderflow_only")
        if inputs.mid_price is None or inputs.spread_pct is None:
            reasons.append("invalid_orderbook_data")
        if inputs.edge is None:
            reasons.append("missing_edge")
        elif inputs.edge < 0.05:
            reasons.append("edge_below_threshold")
        if inputs.rsi is None:
            reasons.append("missing_rsi")
        if inputs.volume_24h < profile.min_volume_24h:
            reasons.append("liquidity_guard_rejection")
        if inputs.spread_pct is not None and inputs.spread_pct > profile.max_spread_pct:
            reasons.append("slippage_guard_rejection")

        edge_component = clamp((inputs.edge or 0.0) / 0.05)
        rsi_component = self._rsi_component(inputs.rsi)
        volume_component = clamp(inputs.volume_24h / profile.min_volume_24h) if profile.min_volume_24h else 0.0
        microstructure_component = 0.0
        if inputs.spread_pct is not None:
            microstructure_component = 1.0 - clamp(inputs.spread_pct / profile.max_spread_pct)

        score = (
            0.45 * edge_component
            + 0.20 * rsi_component
            + 0.20 * volume_component
            + 0.15 * microstructure_component
        )
        if score < profile.min_score:
            reasons.append("score_below_threshold")

        return DecisionResult(
            source=inputs.source,
            category=inputs.category,
            market_id=inputs.market_id,
            score=round(score, 4),
            threshold=profile.min_score,
            should_trade=not reasons,
            reasons=self._dedupe(reasons),
            trade_size=profile.paper_trade_size,
            inputs=self._inputs_for_log(
                inputs,
                edge_component=round(edge_component, 4),
                rsi_component=round(rsi_component, 4),
                volume_component=round(volume_component, 4),
                microstructure_component=round(microstructure_component, 4),
            ),
        )

    def score_orderflow(self, inputs: DecisionInputs) -> DecisionResult:
        profile = self.get_profile(inputs.category)
        reasons: List[str] = []

        if not inputs.market_id:
            reasons.append("market_not_mapped")
        if inputs.mid_price is None or inputs.spread_pct is None:
            reasons.append("invalid_orderbook_data")
        if inputs.volume_24h < profile.min_volume_24h:
            reasons.append("liquidity_guard_rejection")
        if inputs.spread_pct is not None and inputs.spread_pct > profile.max_spread_pct:
            reasons.append("slippage_guard_rejection")
        if inputs.price_drift_pct is not None and inputs.price_drift_pct > profile.max_price_drift_pct:
            reasons.append("slippage_guard_rejection")
        if (
            inputs.event_type == "CLUSTER_DETECTED"
            and inputs.wallets_count is not None
            and inputs.wallets_count > 0
            and inputs.wallets_count < profile.min_cluster_wallets
        ):
            reasons.append("cluster_threshold_not_reached")

        event_amount = inputs.event_amount or 0.0
        wallets_count = inputs.wallets_count or 1

        notional_component = clamp(event_amount / profile.min_whale_notional) if profile.min_whale_notional else 0.0
        cluster_component = clamp(wallets_count / profile.min_cluster_wallets) if profile.min_cluster_wallets else 0.0
        liquidity_component = clamp(inputs.volume_24h / profile.min_volume_24h) if profile.min_volume_24h else 0.0
        trust_component = clamp(inputs.whale_trust)

        drift_penalty = 0.0
        if inputs.price_drift_pct is not None and inputs.price_drift_pct > 0.75 * profile.max_price_drift_pct:
            drift_penalty = 0.15

        spread_penalty = 0.0
        if inputs.spread_pct is not None and inputs.spread_pct > 0.75 * profile.max_spread_pct:
            spread_penalty = 0.15

        score = (
            0.35 * notional_component
            + 0.25 * cluster_component
            + 0.20 * liquidity_component
            + 0.20 * trust_component
            - drift_penalty
            - spread_penalty
        )
        if score < profile.min_score:
            reasons.append("score_below_threshold")

        return DecisionResult(
            source=inputs.source,
            category=inputs.category,
            market_id=inputs.market_id,
            score=round(score, 4),
            threshold=profile.min_score,
            should_trade=not reasons,
            reasons=self._dedupe(reasons),
            trade_size=profile.paper_trade_size,
            inputs=self._inputs_for_log(
                inputs,
                notional_component=round(notional_component, 4),
                cluster_component=round(cluster_component, 4),
                liquidity_component=round(liquidity_component, 4),
                trust_component=round(trust_component, 4),
                drift_penalty=round(drift_penalty, 4),
                spread_penalty=round(spread_penalty, 4),
            ),
        )

    def log_result(self, decision: DecisionResult, runtime_logger: Optional[logging.Logger] = None) -> None:
        active_logger = runtime_logger or logger
        if decision.should_trade:
            active_logger.info(
                "[DECISION] source=%s category=%s market=%s score=%.2f threshold=%.2f trade_size=%.2f inputs=%s",
                decision.source,
                decision.category,
                decision.market_id,
                decision.score,
                decision.threshold,
                decision.trade_size,
                decision.inputs,
            )
            return

        active_logger.info(
            "[REJECT] source=%s category=%s market=%s reasons=%s score=%.2f threshold=%.2f inputs=%s",
            decision.source,
            decision.category,
            decision.market_id,
            ",".join(decision.reasons),
            decision.score,
            decision.threshold,
            decision.inputs,
        )

    def _inputs_for_log(self, inputs: DecisionInputs, **extras: Any) -> Dict[str, Any]:
        payload = {
            "token_id": inputs.token_id,
            "question": inputs.question[:80] if inputs.question else "",
            "event_type": inputs.event_type,
            "volume_24h": round(inputs.volume_24h, 4),
            "mid_price": self._round_or_none(inputs.mid_price),
            "spread_pct": self._round_or_none(inputs.spread_pct),
            "edge": self._round_or_none(inputs.edge),
            "rsi": self._round_or_none(inputs.rsi),
            "event_amount": self._round_or_none(inputs.event_amount),
            "wallets_count": inputs.wallets_count,
            "whale_trust": self._round_or_none(inputs.whale_trust),
            "price_drift_pct": self._round_or_none(inputs.price_drift_pct),
        }
        for key, value in extras.items():
            payload[key] = value
        return {key: value for key, value in payload.items() if value not in (None, "", [])}

    @staticmethod
    def _round_or_none(value: Optional[float]) -> Optional[float]:
        if value is None:
            return None
        return round(float(value), 4)

    @staticmethod
    def _rsi_component(rsi: Optional[float]) -> float:
        if rsi is None:
            return 0.0
        if 40 <= rsi <= 60:
            return 1.0
        if 30 <= rsi < 40 or 60 < rsi <= 70:
            return 0.7
        return 0.3

    @staticmethod
    def _dedupe(values: List[str]) -> List[str]:
        seen = set()
        ordered: List[str] = []
        for value in values:
            if value not in seen:
                seen.add(value)
                ordered.append(value)
        return ordered
