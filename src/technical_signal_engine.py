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
    ) -> TechnicalSignal:
        reasons: List[str] = []
        direction = "NEUTRAL"

        if closes is None or closes.empty or len(closes) < 60:
            reasons.append("missing_price_history")
            return TechnicalSignal(
                symbol=symbol,
                score=0.0,
                threshold=min_score,
                should_trade=False,
                direction=direction,
                reasons=reasons,
                inputs={"timeframe": timeframe},
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

        if ema_fast_value > ema_slow_value and macd_hist_value > 0 and momentum > 0:
            direction = "LONG"
        elif ema_fast_value < ema_slow_value and macd_hist_value < 0 and momentum < 0:
            direction = "SHORT"
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

        if spread_pct <= 0 or spread_pct > 0.012:
            reasons.append("futures_spread_wide")
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
        micro_component = 1.0 - _clamp(spread_pct / 0.006)

        score = (
            0.30 * macd_component
            + 0.25 * momentum_component
            + 0.20 * rsi_component
            + 0.15 * volume_component
            + 0.10 * micro_component
        )

        if score < min_score:
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
            threshold=min_score,
            should_trade=should_trade,
            direction=direction,
            reasons=list(dict.fromkeys(reasons)),
            inputs=cleaned_inputs,
        )
