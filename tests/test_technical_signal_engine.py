from __future__ import annotations

import pandas as pd

from src.technical_signal_engine import TechnicalSignalEngine


def test_technical_signal_engine_flags_strong_long_setup():
    engine = TechnicalSignalEngine()
    closes = pd.Series([100 + (index * 0.8) for index in range(80)], dtype=float)
    volumes = pd.Series([1_000 + (index * 20) for index in range(80)], dtype=float)

    signal = engine.score(
        symbol="BTC/USDT:USDT",
        closes=closes,
        volumes=volumes,
        spread_pct=0.002,
        volume_24h=2_500_000.0,
        min_score=0.55,
        timeframe="5m",
    )

    assert signal.direction == "LONG"
    assert signal.should_trade is True
    assert signal.score >= 0.55
    assert signal.reasons == []


def test_technical_signal_engine_rejects_when_history_is_missing():
    engine = TechnicalSignalEngine()
    closes = pd.Series([100 + index for index in range(10)], dtype=float)
    volumes = pd.Series([1_000 for _ in range(10)], dtype=float)

    signal = engine.score(
        symbol="ETH/USDT:USDT",
        closes=closes,
        volumes=volumes,
        spread_pct=0.002,
        volume_24h=1_500_000.0,
        min_score=0.55,
        timeframe="5m",
    )

    assert signal.should_trade is False
    assert signal.direction == "NEUTRAL"
    assert "missing_price_history" in signal.reasons
