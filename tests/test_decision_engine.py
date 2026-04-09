import logging

import pytest

from src.decision_engine import DecisionEngine, DecisionInputs
from src.evaluation_utils import STRATEGY_PROFILE_SAMPLING_RELAXED


def test_discovery_score_is_deterministic():
    engine = DecisionEngine()
    inputs = DecisionInputs(
        source="discovery",
        category="CRYPTO",
        market_id="m1",
        token_id="t1",
        question="Will BTC be above $100,000 on December 31, 2026?",
        volume_24h=120000.0,
        mid_price=0.42,
        spread_pct=0.01,
        edge=0.08,
        rsi=52.0,
    )

    first = engine.score_discovery(inputs)
    second = engine.score_discovery(inputs)

    assert first.score == second.score
    assert first.should_trade is True


def test_orderflow_rejection_logs_cluster_threshold(caplog):
    engine = DecisionEngine()
    inputs = DecisionInputs(
        source="activity",
        category="POLITICS",
        market_id="market-politics",
        token_id="token-politics",
        event_type="CLUSTER_DETECTED",
        question="Will candidate X win the election?",
        volume_24h=50000.0,
        mid_price=0.62,
        spread_pct=0.01,
        event_amount=2000.0,
        wallets_count=2,
        whale_trust=0.7,
        price_drift_pct=0.01,
    )

    decision = engine.score_orderflow(inputs)
    with caplog.at_level(logging.INFO):
        engine.log_result(decision)

    assert decision.should_trade is False
    assert "cluster_threshold_not_reached" in decision.reasons
    assert "cluster_threshold_not_reached" in caplog.text


def test_sampling_relaxed_orderflow_can_pass_when_baseline_rejects():
    engine = DecisionEngine()
    baseline_inputs = DecisionInputs(
        source="activity",
        category="SPORTS",
        market_id="market-sports",
        token_id="token-sports",
        event_type="CLUSTER_DETECTED",
        question="Will Team X win the championship?",
        volume_24h=16_000.0,
        mid_price=0.62,
        spread_pct=0.01,
        event_amount=600.0,
        wallets_count=2,
        whale_trust=0.5,
        price_drift_pct=0.01,
    )

    baseline = engine.score_orderflow(baseline_inputs)
    sampling = engine.score_orderflow(
        DecisionInputs(**{**baseline_inputs.__dict__, "strategy_profile": STRATEGY_PROFILE_SAMPLING_RELAXED})
    )

    assert baseline.should_trade is False
    assert "cluster_threshold_not_reached" in baseline.reasons
    assert sampling.should_trade is True
    assert sampling.strategy_profile == STRATEGY_PROFILE_SAMPLING_RELAXED


def test_sampling_relaxed_applies_spread_multiplier_without_changing_baseline():
    engine = DecisionEngine()
    baseline_inputs = DecisionInputs(
        source="activity",
        category="SPORTS",
        market_id="market-sports-spread",
        token_id="token-sports-spread",
        event_type="CLUSTER_DETECTED",
        question="Will Team Y win the championship?",
        volume_24h=20_000.0,
        mid_price=0.61,
        spread_pct=0.045,
        event_amount=1_000.0,
        wallets_count=2,
        whale_trust=0.9,
        price_drift_pct=0.01,
    )

    baseline = engine.score_orderflow(baseline_inputs)
    sampling = engine.score_orderflow(
        DecisionInputs(**{**baseline_inputs.__dict__, "strategy_profile": STRATEGY_PROFILE_SAMPLING_RELAXED})
    )

    assert baseline.should_trade is False
    assert "slippage_guard_rejection" in baseline.reasons
    assert sampling.should_trade is True
    assert sampling.strategy_profile == STRATEGY_PROFILE_SAMPLING_RELAXED
    assert sampling.inputs["effective_max_spread_pct"] == pytest.approx(0.048, rel=1e-4)
