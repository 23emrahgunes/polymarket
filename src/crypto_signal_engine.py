from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List

from src.decision_engine import DecisionResult, clamp
from src.logic import calculate_black_scholes_prob
from src.venue_config import VenueConfig


@dataclass
class CryptoSignalInputs:
    market_id: str
    token_id: str
    question: str
    volume_24h: float
    polymarket_mid_price: float
    polymarket_spread_pct: float
    spot_price: float
    futures_symbol: str
    futures_last_price: float
    futures_mark_price: float
    futures_spread_pct: float
    futures_volume_24h: float
    funding_rate: float
    open_interest: float
    volatility: float
    strike_price: float
    expiry_dt: datetime
    orderflow_bias: float = 0.0
    orderflow_notional: float = 0.0


@dataclass
class SharedCryptoSignal:
    market_id: str
    token_id: str
    symbol: str
    score: float
    threshold: float
    should_trade: bool
    direction: str
    polymarket_side: str
    futures_side: str
    edge: float
    implied_probability: float
    reasons: List[str]
    inputs: Dict[str, Any]


class CryptoSignalEngine:
    def __init__(self, shared_threshold: float = 0.68):
        self.shared_threshold = shared_threshold

    def score(self, inputs: CryptoSignalInputs) -> SharedCryptoSignal:
        reasons: List[str] = []
        now = datetime.now(timezone.utc)
        if inputs.expiry_dt <= now:
            reasons.append("market_expired")

        time_to_expiry_years = max((inputs.expiry_dt - now).total_seconds() / (24 * 365 * 3600), 0.0)
        implied_probability = calculate_black_scholes_prob(
            inputs.futures_mark_price,
            inputs.strike_price,
            time_to_expiry_years,
            inputs.volatility,
        )
        edge = implied_probability - inputs.polymarket_mid_price
        direction = "LONG" if edge >= 0 else "SHORT"
        polymarket_side = "YES" if direction == "LONG" else "NO"
        futures_side = "LONG" if direction == "LONG" else "SHORT"

        if abs(edge) < 0.05:
            reasons.append("edge_below_threshold")
        if inputs.futures_spread_pct > 0.01:
            reasons.append("futures_microstructure_weak")
        if inputs.futures_volume_24h <= 0:
            reasons.append("missing_futures_volume")

        edge_component = clamp(abs(edge) / 0.05)
        volume_component = clamp(inputs.futures_volume_24h / max(inputs.volume_24h, 1.0))
        microstructure_component = 1.0 - clamp(inputs.futures_spread_pct / 0.01)
        open_interest_component = clamp(inputs.open_interest / 1_000_000.0)
        funding_alignment = self._funding_alignment(direction, inputs.funding_rate)
        orderflow_alignment = clamp(inputs.orderflow_bias, 0.0, 1.0) if direction == "LONG" else clamp(-inputs.orderflow_bias, 0.0, 1.0)
        orderflow_component = clamp(inputs.orderflow_notional / 2_500.0) * orderflow_alignment

        score = (
            0.35 * edge_component
            + 0.15 * volume_component
            + 0.15 * microstructure_component
            + 0.15 * open_interest_component
            + 0.10 * funding_alignment
            + 0.10 * orderflow_component
        )
        if score < self.shared_threshold:
            reasons.append("shared_crypto_score_below_threshold")

        payload = {
            "token_id": inputs.token_id,
            "symbol": inputs.futures_symbol,
            "question": inputs.question[:80],
            "polymarket_mid_price": round(inputs.polymarket_mid_price, 4),
            "spot_price": round(inputs.spot_price, 4),
            "futures_last_price": round(inputs.futures_last_price, 4),
            "futures_mark_price": round(inputs.futures_mark_price, 4),
            "futures_spread_pct": round(inputs.futures_spread_pct, 4),
            "futures_volume_24h": round(inputs.futures_volume_24h, 4),
            "funding_rate": round(inputs.funding_rate, 6),
            "open_interest": round(inputs.open_interest, 4),
            "edge": round(edge, 4),
            "implied_probability": round(implied_probability, 4),
            "orderflow_bias": round(inputs.orderflow_bias, 4),
            "orderflow_notional": round(inputs.orderflow_notional, 4),
            "edge_component": round(edge_component, 4),
            "volume_component": round(volume_component, 4),
            "microstructure_component": round(microstructure_component, 4),
            "open_interest_component": round(open_interest_component, 4),
            "funding_alignment": round(funding_alignment, 4),
            "orderflow_component": round(orderflow_component, 4),
            "orderflow_alignment": round(orderflow_alignment, 4),
            "direction": direction,
        }

        return SharedCryptoSignal(
            market_id=inputs.market_id,
            token_id=inputs.token_id,
            symbol=inputs.futures_symbol,
            score=round(score, 4),
            threshold=self.shared_threshold,
            should_trade=not reasons,
            direction=direction,
            polymarket_side=polymarket_side,
            futures_side=futures_side,
            edge=round(edge, 4),
            implied_probability=round(implied_probability, 4),
            reasons=self._dedupe(reasons),
            inputs=payload,
        )

    def build_polymarket_decision(
        self,
        signal: SharedCryptoSignal,
        venue_config: VenueConfig,
        discovery_decision: DecisionResult,
    ) -> DecisionResult:
        reasons = list(signal.reasons)
        reasons.extend(discovery_decision.reasons)
        if signal.score < venue_config.signal_threshold:
            reasons.append("venue_signal_threshold_not_met")
        return DecisionResult(
            source="blended_crypto",
            category="CRYPTO",
            market_id=signal.market_id,
            score=signal.score,
            threshold=max(venue_config.signal_threshold, discovery_decision.threshold),
            should_trade=not self._dedupe(reasons),
            reasons=self._dedupe(reasons),
            trade_size=discovery_decision.trade_size,
            inputs={**discovery_decision.inputs, **signal.inputs, "direction": signal.direction},
            venue="polymarket",
            direction=signal.direction,
        )

    def build_binance_futures_decision(
        self,
        signal: SharedCryptoSignal,
        venue_config: VenueConfig,
        risk_reasons: List[str],
        trade_size: float,
    ) -> DecisionResult:
        reasons = list(signal.reasons)
        reasons.extend(risk_reasons)
        if signal.score < venue_config.signal_threshold:
            reasons.append("venue_signal_threshold_not_met")
        return DecisionResult(
            source="binance_futures_price_structure",
            category="CRYPTO",
            market_id=signal.symbol,
            score=signal.score,
            threshold=venue_config.signal_threshold,
            should_trade=not self._dedupe(reasons),
            reasons=self._dedupe(reasons),
            trade_size=trade_size,
            inputs={**signal.inputs, "direction": signal.direction},
            venue="binance_futures",
            direction=signal.direction,
        )

    @staticmethod
    def _funding_alignment(direction: str, funding_rate: float) -> float:
        if direction == "LONG":
            if funding_rate <= 0:
                return 1.0
            return 1.0 - clamp(funding_rate / 0.0015)
        if funding_rate >= 0:
            return 1.0
        return 1.0 - clamp(abs(funding_rate) / 0.0015)

    @staticmethod
    def _dedupe(values: List[str]) -> List[str]:
        seen = set()
        result: List[str] = []
        for value in values:
            if value not in seen:
                seen.add(value)
                result.append(value)
        return result
