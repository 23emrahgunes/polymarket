import logging

import pytest

from src.decision_engine import DecisionEngine, DecisionInputs


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
