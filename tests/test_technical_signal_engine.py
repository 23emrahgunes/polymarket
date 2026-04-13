from __future__ import annotations

import pandas as pd

from src.runtime import GhostBotRuntime, RuntimeSettings
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


def test_binance_technical_default_scope_uses_liquid_symbols():
    runtime = GhostBotRuntime(RuntimeSettings(binance_technical_symbols=tuple()))

    symbols = runtime._resolve_binance_technical_symbols()

    assert [entry["base_symbol"] for entry in symbols] == ["BTC", "ETH", "SOL"]


def test_technical_signal_engine_paper_recovery_allows_wider_spread():
    engine = TechnicalSignalEngine()
    closes = pd.Series([100 + (index * 0.8) for index in range(80)], dtype=float)
    volumes = pd.Series([1_000 for _ in range(79)] + [3_000], dtype=float)

    baseline = engine.score(
        symbol="BTC/USDT:USDT",
        closes=closes,
        volumes=volumes,
        spread_pct=0.015,
        volume_24h=2_500_000.0,
        min_score=0.62,
        timeframe="5m",
    )
    recovered = engine.score(
        symbol="BTC/USDT:USDT",
        closes=closes,
        volumes=volumes,
        spread_pct=0.015,
        volume_24h=2_500_000.0,
        min_score=0.62,
        timeframe="5m",
        paper_recovery=True,
    )

    assert "futures_spread_wide" in baseline.reasons
    assert "futures_spread_wide" not in recovered.reasons
    assert recovered.should_trade is True
    assert recovered.threshold == 0.54
    assert recovered.inputs["technical_recovery_applied"] is True


def test_technical_signal_engine_paper_recovery_keeps_extreme_spread_rejected():
    engine = TechnicalSignalEngine()
    closes = pd.Series([100 + (index * 0.8) for index in range(80)], dtype=float)
    volumes = pd.Series([1_000 for _ in range(79)] + [3_000], dtype=float)

    signal = engine.score(
        symbol="BTC/USDT:USDT",
        closes=closes,
        volumes=volumes,
        spread_pct=0.019,
        volume_24h=2_500_000.0,
        min_score=0.62,
        timeframe="5m",
        paper_recovery=True,
    )

    assert "futures_spread_wide" in signal.reasons


def test_technical_signal_engine_microstructure_recovery_requires_force_context():
    engine = TechnicalSignalEngine()
    closes = pd.Series([100 + (index * 0.8) for index in range(80)], dtype=float)
    volumes = pd.Series([1_000 for _ in range(79)] + [3_000], dtype=float)

    no_force = engine.score(
        symbol="BTC/USDT:USDT",
        closes=closes,
        volumes=volumes,
        spread_pct=0.022,
        volume_24h=2_500_000.0,
        min_score=0.62,
        timeframe="5m",
        paper_recovery=True,
    )
    recovered = engine.score(
        symbol="BTC/USDT:USDT",
        closes=closes,
        volumes=volumes,
        spread_pct=0.022,
        volume_24h=2_500_000.0,
        min_score=0.62,
        timeframe="5m",
        paper_recovery=True,
        force_sample_enabled=True,
        force_min_score=0.50,
    )

    assert "futures_spread_wide" in no_force.reasons
    assert no_force.inputs["microstructure_recovery_applied"] is False
    assert "futures_spread_wide" not in recovered.reasons
    assert recovered.should_trade is True
    assert recovered.inputs["force_recovery_candidate"] is True
    assert recovered.inputs["microstructure_recovery_applied"] is True
    assert recovered.inputs["spread_recovery_applied"] is True
    assert recovered.inputs["effective_max_spread_pct"] == 0.026
    assert recovered.inputs["effective_spread_normalizer"] == 0.014


def test_technical_signal_engine_microstructure_recovery_keeps_hard_spread_cap():
    engine = TechnicalSignalEngine()
    closes = pd.Series([100 + (index * 0.8) for index in range(80)], dtype=float)
    volumes = pd.Series([1_000 for _ in range(79)] + [3_000], dtype=float)

    signal = engine.score(
        symbol="BTC/USDT:USDT",
        closes=closes,
        volumes=volumes,
        spread_pct=0.027,
        volume_24h=2_500_000.0,
        min_score=0.62,
        timeframe="5m",
        paper_recovery=True,
        force_sample_enabled=True,
        force_min_score=0.50,
    )

    assert "futures_spread_wide" in signal.reasons
    assert signal.inputs["microstructure_recovery_applied"] is False
    assert signal.inputs["spread_recovery_applied"] is False


def test_technical_signal_engine_microstructure_recovery_is_paper_only():
    engine = TechnicalSignalEngine()
    closes = pd.Series([100 + (index * 0.8) for index in range(80)], dtype=float)
    volumes = pd.Series([1_000 for _ in range(79)] + [3_000], dtype=float)

    signal = engine.score(
        symbol="BTC/USDT:USDT",
        closes=closes,
        volumes=volumes,
        spread_pct=0.015,
        volume_24h=2_500_000.0,
        min_score=0.62,
        timeframe="5m",
        force_sample_enabled=True,
        force_min_score=0.50,
    )

    assert "futures_spread_wide" in signal.reasons
    assert signal.inputs["force_recovery_candidate"] is False
    assert signal.inputs["microstructure_recovery_applied"] is False


def test_technical_signal_engine_recovers_near_long_alignment_for_paper_only():
    engine = TechnicalSignalEngine()
    closes = pd.Series([100 * (2.718281828 ** (0.02 * index)) for index in range(80)], dtype=float)
    closes.iloc[-1] = closes.iloc[-4] * 0.9999
    volumes = pd.Series([1_000 for _ in range(79)] + [10_000], dtype=float)

    baseline = engine.score(
        symbol="BTC/USDT:USDT",
        closes=closes,
        volumes=volumes,
        spread_pct=0.001,
        volume_24h=2_500_000.0,
        min_score=0.62,
        timeframe="5m",
    )
    recovered = engine.score(
        symbol="BTC/USDT:USDT",
        closes=closes,
        volumes=volumes,
        spread_pct=0.001,
        volume_24h=2_500_000.0,
        min_score=0.62,
        timeframe="5m",
        paper_recovery=True,
    )

    assert baseline.direction == "NEUTRAL"
    assert "technical_alignment_weak" in baseline.reasons
    assert recovered.direction == "LONG"
    assert recovered.should_trade is True
    assert recovered.inputs["alignment_recovery_applied"] is True
    assert recovered.inputs["technical_alignment_recovered"] is True
