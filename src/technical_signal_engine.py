from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

import pandas as pd

from src.logic import calculate_rsi


def _clamp(value: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    return max(minimum, min(maximum, value))


def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def _macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[pd.Series, pd.Series, pd.Series]:
    ema_fast = _ema(series, fast)
    ema_slow = _ema(series, slow)
    macd_line = ema_fast - ema_slow
    signal_line = _ema(macd_line, signal)
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


DEFAULT_MAX_SPREAD_PCT = 0.012
DEFAULT_SPREAD_NORMALIZER = 0.006
PAPER_RECOVERY_MAX_SPREAD_PCT = 0.018
PAPER_RECOVERY_SPREAD_NORMALIZER = 0.010
PAPER_RECOVERY_MIN_SCORE_FLOOR = 0.54
PAPER_RECOVERY_MIN_SCORE_DISCOUNT = 0.08
PAPER_RECOVERY_MOMENTUM_TOLERANCE = 0.0015
PAPER_MICROSTRUCTURE_RECOVERY_MAX_SPREAD_PCT = 0.026
PAPER_MICROSTRUCTURE_RECOVERY_SPREAD_NORMALIZER = 0.014
PAPER_MICROSTRUCTURE_RECOVERY_V2_MAX_SPREAD_PCT = 0.032
PAPER_MICROSTRUCTURE_RECOVERY_V2_SPREAD_NORMALIZER = 0.018
PAPER_MICROSTRUCTURE_RECOVERY_MIN_VOLUME_24H = 1_000_000.0
PAPER_MICROSTRUCTURE_RECOVERY_CANDIDATE_FLOOR = 0.42
PAPER_FINAL_SCORE_RECOVERY_MAX_GAP = 0.08
PAPER_NEAR_THRESHOLD_GAP = 0.06
PAPER_FINAL_SCORE_RECOVERY_MIN_MICRO_COMPONENT = 0.25
PAPER_FINAL_SCORE_RECOVERY_MIN_VOLUME_COMPONENT = 0.30
PAPER_FINAL_SCORE_RECOVERY_BASE_BONUS = 0.04
PAPER_FINAL_SCORE_RECOVERY_ALIGNMENT_BONUS = 0.01
PAPER_FINAL_SCORE_RECOVERY_MICROSTRUCTURE_BONUS = 0.01
PAPER_FINAL_SCORE_RECOVERY_MAX_BONUS = 0.06
SCORE_BLOCKER_COMPONENT_FLOOR = 0.35


def _technical_score(
    macd_component: float,
    momentum_component: float,
    rsi_component: float,
    volume_component: float,
    micro_component: float,
) -> float:
    return (
        0.30 * macd_component
        + 0.25 * momentum_component
        + 0.20 * rsi_component
        + 0.15 * volume_component
        + 0.10 * micro_component
    )


def _score_blocker_labels(
    *,
    rsi_component: float,
    macd_component: float,
    momentum_component: float,
    volume_component: float,
    micro_component: float,
) -> list[str]:
    blockers: list[str] = []
    if micro_component < SCORE_BLOCKER_COMPONENT_FLOOR:
        blockers.append("microstructure_drag")
    if momentum_component < SCORE_BLOCKER_COMPONENT_FLOOR:
        blockers.append("momentum_drag")
    if macd_component < SCORE_BLOCKER_COMPONENT_FLOOR:
        blockers.append("macd_drag")
    if volume_component < SCORE_BLOCKER_COMPONENT_FLOOR:
        blockers.append("volume_drag")
    if rsi_component < SCORE_BLOCKER_COMPONENT_FLOOR:
        blockers.append("rsi_drag")
    if len(blockers) >= 2:
        blockers.append("multi_factor_drag")
    return blockers


@dataclass
class TechnicalSignal:
    symbol: str
    score: float
    threshold: float
    should_trade: bool
    direction: str
    reasons: List[str]
    inputs: Dict[str, Any]


class TechnicalSignalEngine:
    def score(
        self,
        symbol: str,
        closes: pd.Series,
        volumes: pd.Series,
        spread_pct: float,
        volume_24h: float,
        min_score: float,
        timeframe: str,
        paper_recovery: bool = False,
        force_sample_enabled: bool = False,
        force_min_score: float = 0.5,
    ) -> TechnicalSignal:
        reasons: List[str] = []
        direction = "NEUTRAL"
        technical_recovery_applied = bool(paper_recovery)
        effective_min_score = max(PAPER_RECOVERY_MIN_SCORE_FLOOR, min_score - PAPER_RECOVERY_MIN_SCORE_DISCOUNT) if paper_recovery else min_score
        effective_max_spread_pct = PAPER_RECOVERY_MAX_SPREAD_PCT if paper_recovery else DEFAULT_MAX_SPREAD_PCT
        spread_normalizer = PAPER_RECOVERY_SPREAD_NORMALIZER if paper_recovery else DEFAULT_SPREAD_NORMALIZER
        microstructure_recovery_applied = False
        spread_recovery_applied = False
        microstructure_recovery_v2_applied = False
        spread_recovery_v2_applied = False
        force_recovery_candidate = False
        effective_spread_cap_stage = "paper_recovery" if paper_recovery else "default"

        if closes is None or closes.empty or len(closes) < 60:
            reasons.append("missing_price_history")
            score_gap_to_threshold = effective_min_score
            return TechnicalSignal(
                symbol=symbol,
                score=0.0,
                threshold=effective_min_score,
                should_trade=False,
                direction=direction,
                reasons=reasons,
                inputs={
                    "timeframe": timeframe,
                    "technical_recovery_applied": technical_recovery_applied,
                    "alignment_recovery_applied": False,
                    "technical_alignment_recovered": False,
                    "microstructure_recovery_applied": microstructure_recovery_applied,
                    "spread_recovery_applied": spread_recovery_applied,
                    "microstructure_recovery_v2_applied": microstructure_recovery_v2_applied,
                    "spread_recovery_v2_applied": spread_recovery_v2_applied,
                    "force_recovery_candidate": force_recovery_candidate,
                    "effective_spread_cap_stage": effective_spread_cap_stage,
                    "microstructure_candidate_floor": round(PAPER_MICROSTRUCTURE_RECOVERY_CANDIDATE_FLOOR, 4),
                    "pre_microstructure_score": 0.0,
                    "post_microstructure_score": 0.0,
                    "pre_spread_recovery_score": 0.0,
                    "post_spread_recovery_score": 0.0,
                    "pre_final_score_recovery_score": 0.0,
                    "post_final_score_recovery_score": 0.0,
                    "score_gap_to_threshold": round(score_gap_to_threshold, 4),
                    "near_threshold_candidate": False,
                    "score_recovery_candidate": False,
                    "score_recovery_passed": False,
                    "final_score_recovery_applied": False,
                    "final_score_recovery_bonus": 0.0,
                    "final_score_recovery_reason": "not_applicable",
                    "score_blocker_labels": [],
                    "effective_min_score": round(effective_min_score, 4),
                    "effective_max_spread_pct": round(effective_max_spread_pct, 6),
                    "effective_spread_normalizer": round(spread_normalizer, 6),
                },
            )

        rsi_series = calculate_rsi(closes)
        rsi_value = float(rsi_series.iloc[-1]) if not rsi_series.empty else None
        if rsi_value is None or pd.isna(rsi_value):
            reasons.append("missing_rsi")

        ema_fast = _ema(closes, 20)
        ema_slow = _ema(closes, 50)
        macd_line, macd_signal, macd_hist = _macd(closes)

        ema_fast_value = float(ema_fast.iloc[-1])
        ema_slow_value = float(ema_slow.iloc[-1])
        macd_value = float(macd_line.iloc[-1])
        macd_signal_value = float(macd_signal.iloc[-1])
        macd_hist_value = float(macd_hist.iloc[-1])

        momentum_window = 3
        if len(closes) > momentum_window:
            base_price = float(closes.iloc[-1 - momentum_window])
            last_price = float(closes.iloc[-1])
            momentum = (last_price - base_price) / base_price if base_price else 0.0
        else:
            momentum = 0.0
            reasons.append("missing_momentum")

        strict_long = ema_fast_value > ema_slow_value and macd_hist_value > 0 and momentum > 0
        strict_short = ema_fast_value < ema_slow_value and macd_hist_value < 0 and momentum < 0
        recovered_long = (
            paper_recovery
            and ema_fast_value > ema_slow_value
            and macd_hist_value > 0
            and momentum >= -PAPER_RECOVERY_MOMENTUM_TOLERANCE
        )
        recovered_short = (
            paper_recovery
            and ema_fast_value < ema_slow_value
            and macd_hist_value < 0
            and momentum <= PAPER_RECOVERY_MOMENTUM_TOLERANCE
        )
        alignment_recovery_applied = False

        if strict_long:
            direction = "LONG"
        elif strict_short:
            direction = "SHORT"
        elif recovered_long:
            direction = "LONG"
            alignment_recovery_applied = True
        elif recovered_short:
            direction = "SHORT"
            alignment_recovery_applied = True
        else:
            reasons.append("technical_alignment_weak")

        volume_ratio = 0.0
        if volumes is not None and not volumes.empty:
            lookback = volumes.tail(20)
            volume_mean = float(lookback.mean()) if not lookback.empty else 0.0
            last_volume = float(volumes.iloc[-1])
            volume_ratio = (last_volume / volume_mean) if volume_mean else 0.0
        else:
            reasons.append("missing_volume")

        if volume_24h <= 0:
            reasons.append("missing_futures_volume")

        close_value = float(closes.iloc[-1])
        macd_hist_pct = (macd_hist_value / close_value) if close_value else 0.0

        if direction == "LONG":
            rsi_component = _clamp(((rsi_value or 50.0) - 45.0) / 25.0)
        else:
            rsi_component = _clamp((55.0 - (rsi_value or 50.0)) / 25.0)

        macd_component = _clamp(abs(macd_hist_pct) / 0.003)
        momentum_component = _clamp(abs(momentum) / 0.01)
        volume_component = _clamp(volume_ratio / 1.5)
        micro_component = 1.0 - _clamp(spread_pct / spread_normalizer)

        score = _technical_score(
            macd_component=macd_component,
            momentum_component=momentum_component,
            rsi_component=rsi_component,
            volume_component=volume_component,
            micro_component=micro_component,
        )
        pre_microstructure_score = score
        post_microstructure_score = score
        pre_spread_recovery_score = score
        post_spread_recovery_score = score
        force_recovery_candidate = (
            bool(paper_recovery)
            and bool(force_sample_enabled)
            and direction in {"LONG", "SHORT"}
            and volume_24h >= PAPER_MICROSTRUCTURE_RECOVERY_MIN_VOLUME_24H
            and pre_microstructure_score >= PAPER_MICROSTRUCTURE_RECOVERY_CANDIDATE_FLOOR
        )
        if (
            force_recovery_candidate
            and spread_pct > 0
            and spread_pct <= PAPER_MICROSTRUCTURE_RECOVERY_MAX_SPREAD_PCT
        ):
            microstructure_recovery_applied = True
            spread_recovery_applied = spread_pct > PAPER_RECOVERY_MAX_SPREAD_PCT
            effective_max_spread_pct = PAPER_MICROSTRUCTURE_RECOVERY_MAX_SPREAD_PCT
            spread_normalizer = PAPER_MICROSTRUCTURE_RECOVERY_SPREAD_NORMALIZER
            effective_spread_cap_stage = "microstructure_recovery_v1"
            micro_component = 1.0 - _clamp(spread_pct / spread_normalizer)
            score = _technical_score(
                macd_component=macd_component,
                momentum_component=momentum_component,
                rsi_component=rsi_component,
                volume_component=volume_component,
                micro_component=micro_component,
            )
            post_microstructure_score = score
            post_spread_recovery_score = score
        elif (
            force_recovery_candidate
            and spread_pct > PAPER_MICROSTRUCTURE_RECOVERY_MAX_SPREAD_PCT
            and spread_pct <= PAPER_MICROSTRUCTURE_RECOVERY_V2_MAX_SPREAD_PCT
        ):
            microstructure_recovery_applied = True
            spread_recovery_applied = True
            microstructure_recovery_v2_applied = True
            spread_recovery_v2_applied = True
            effective_max_spread_pct = PAPER_MICROSTRUCTURE_RECOVERY_V2_MAX_SPREAD_PCT
            spread_normalizer = PAPER_MICROSTRUCTURE_RECOVERY_V2_SPREAD_NORMALIZER
            effective_spread_cap_stage = "microstructure_recovery_v2"
            micro_component = 1.0 - _clamp(spread_pct / spread_normalizer)
            score = _technical_score(
                macd_component=macd_component,
                momentum_component=momentum_component,
                rsi_component=rsi_component,
                volume_component=volume_component,
                micro_component=micro_component,
            )
            post_microstructure_score = score
            post_spread_recovery_score = score

        score_blocker_labels = _score_blocker_labels(
            rsi_component=rsi_component,
            macd_component=macd_component,
            momentum_component=momentum_component,
            volume_component=volume_component,
            micro_component=micro_component,
        )
        pre_final_score_recovery_score = score
        post_final_score_recovery_score = score
        score_gap_to_threshold = max(effective_min_score - score, 0.0)
        near_threshold_candidate = 0.0 < score_gap_to_threshold <= PAPER_NEAR_THRESHOLD_GAP
        spread_rejected = spread_pct <= 0 or spread_pct > effective_max_spread_pct
        score_recovery_candidate = (
            bool(paper_recovery)
            and bool(force_sample_enabled)
            and bool(force_recovery_candidate)
            and direction in {"LONG", "SHORT"}
            and not spread_rejected
            and 0.0 < score_gap_to_threshold <= PAPER_FINAL_SCORE_RECOVERY_MAX_GAP
            and micro_component >= PAPER_FINAL_SCORE_RECOVERY_MIN_MICRO_COMPONENT
            and volume_component >= PAPER_FINAL_SCORE_RECOVERY_MIN_VOLUME_COMPONENT
        )
        final_score_recovery_applied = False
        score_recovery_passed = False
        final_score_recovery_bonus = 0.0
        final_score_recovery_reason = "not_applicable"
        if score_recovery_candidate:
            final_score_recovery_reason = "near_threshold_components_ok"
            final_score_recovery_bonus = PAPER_FINAL_SCORE_RECOVERY_BASE_BONUS
            if alignment_recovery_applied:
                final_score_recovery_bonus += PAPER_FINAL_SCORE_RECOVERY_ALIGNMENT_BONUS
            if microstructure_recovery_applied or microstructure_recovery_v2_applied:
                final_score_recovery_bonus += PAPER_FINAL_SCORE_RECOVERY_MICROSTRUCTURE_BONUS
            final_score_recovery_bonus = min(
                final_score_recovery_bonus,
                PAPER_FINAL_SCORE_RECOVERY_MAX_BONUS,
            )
            post_final_score_recovery_score = min(score + final_score_recovery_bonus, 1.0)
            score = post_final_score_recovery_score
            final_score_recovery_applied = True
            score_recovery_passed = score >= effective_min_score
            score_gap_to_threshold = max(effective_min_score - score, 0.0)

        if spread_rejected:
            reasons.append("futures_spread_wide")

        if score < effective_min_score:
            reasons.append("score_below_threshold")

        inputs = {
            "timeframe": timeframe,
            "rsi": round(float(rsi_value), 4) if rsi_value is not None else None,
            "ema_fast": round(ema_fast_value, 6),
            "ema_slow": round(ema_slow_value, 6),
            "macd": round(macd_value, 6),
            "macd_signal": round(macd_signal_value, 6),
            "macd_hist": round(macd_hist_value, 6),
            "macd_hist_pct": round(macd_hist_pct, 6),
            "momentum_3": round(momentum, 6),
            "volume_ratio": round(volume_ratio, 4),
            "spread_pct": round(spread_pct, 6),
            "volume_24h": round(volume_24h, 2),
            "direction": direction,
            "technical_recovery_applied": technical_recovery_applied,
            "alignment_recovery_applied": alignment_recovery_applied,
            "technical_alignment_recovered": alignment_recovery_applied,
            "microstructure_recovery_applied": microstructure_recovery_applied,
            "spread_recovery_applied": spread_recovery_applied,
            "microstructure_recovery_v2_applied": microstructure_recovery_v2_applied,
            "spread_recovery_v2_applied": spread_recovery_v2_applied,
            "force_recovery_candidate": force_recovery_candidate,
            "effective_spread_cap_stage": effective_spread_cap_stage,
            "microstructure_candidate_floor": round(PAPER_MICROSTRUCTURE_RECOVERY_CANDIDATE_FLOOR, 4),
            "pre_microstructure_score": round(pre_microstructure_score, 4),
            "post_microstructure_score": round(post_microstructure_score, 4),
            "pre_spread_recovery_score": round(pre_spread_recovery_score, 4),
            "post_spread_recovery_score": round(post_spread_recovery_score, 4),
            "pre_final_score_recovery_score": round(pre_final_score_recovery_score, 4),
            "post_final_score_recovery_score": round(post_final_score_recovery_score, 4),
            "score_gap_to_threshold": round(score_gap_to_threshold, 4),
            "near_threshold_candidate": near_threshold_candidate,
            "score_recovery_candidate": score_recovery_candidate,
            "score_recovery_passed": score_recovery_passed,
            "final_score_recovery_applied": final_score_recovery_applied,
            "final_score_recovery_bonus": round(final_score_recovery_bonus, 4),
            "final_score_recovery_reason": final_score_recovery_reason,
            "score_blocker_labels": score_blocker_labels,
            "effective_min_score": round(effective_min_score, 4),
            "effective_max_spread_pct": round(effective_max_spread_pct, 6),
            "effective_spread_normalizer": round(spread_normalizer, 6),
            "rsi_component": round(rsi_component, 4),
            "macd_component": round(macd_component, 4),
            "momentum_component": round(momentum_component, 4),
            "volume_component": round(volume_component, 4),
            "microstructure_component": round(micro_component, 4),
        }

        cleaned_inputs = {key: value for key, value in inputs.items() if value is not None}
        should_trade = not reasons

        return TechnicalSignal(
            symbol=symbol,
            score=round(score, 4),
            threshold=effective_min_score,
            should_trade=should_trade,
            direction=direction,
            reasons=list(dict.fromkeys(reasons)),
            inputs=cleaned_inputs,
        )
