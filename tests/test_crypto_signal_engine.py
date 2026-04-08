from datetime import datetime, timezone

from src.crypto_signal_engine import CryptoSignalEngine, CryptoSignalInputs
from src.decision_engine import DecisionResult
from src.venue_config import build_default_venue_configs


def _future_expiry():
    return datetime(2026, 12, 31, 23, 59, 59, tzinfo=timezone.utc)


def test_crypto_signal_engine_is_deterministic_and_long_biased():
    engine = CryptoSignalEngine()
    inputs = CryptoSignalInputs(
        market_id="pm-btc-100k",
        token_id="token-btc-yes",
        question="Will BTC be above $100,000 on December 31, 2026?",
        volume_24h=200000.0,
        polymarket_mid_price=0.41,
        polymarket_spread_pct=0.02,
        spot_price=102000.0,
        futures_symbol="BTC/USDT:USDT",
        futures_last_price=103200.0,
        futures_mark_price=103100.0,
        futures_spread_pct=0.001,
        futures_volume_24h=250000.0,
        funding_rate=0.0002,
        open_interest=1800000.0,
        volatility=0.55,
        strike_price=100000.0,
        expiry_dt=_future_expiry(),
        orderflow_bias=1.0,
        orderflow_notional=3000.0,
    )

    first = engine.score(inputs)
    second = engine.score(inputs)

    assert first.score == second.score
    assert first.direction == "LONG"
    assert first.polymarket_side == "YES"
    assert first.futures_side == "LONG"
    assert first.score >= first.threshold


def test_crypto_signal_engine_builds_venue_decisions():
    engine = CryptoSignalEngine()
    shared_signal = engine.score(
        CryptoSignalInputs(
            market_id="pm-btc-100k",
            token_id="token-btc-yes",
            question="Will BTC be above $100,000 on December 31, 2026?",
            volume_24h=200000.0,
            polymarket_mid_price=0.30,
            polymarket_spread_pct=0.02,
            spot_price=102000.0,
            futures_symbol="BTC/USDT:USDT",
            futures_last_price=103200.0,
            futures_mark_price=103100.0,
            futures_spread_pct=0.001,
            futures_volume_24h=250000.0,
            funding_rate=0.0002,
            open_interest=1800000.0,
            volatility=0.55,
            strike_price=100000.0,
            expiry_dt=_future_expiry(),
            orderflow_bias=1.0,
            orderflow_notional=3000.0,
        )
    )
    discovery_decision = DecisionResult(
        source="discovery",
        category="CRYPTO",
        market_id="pm-btc-100k",
        score=0.82,
        threshold=0.68,
        should_trade=True,
        reasons=[],
        trade_size=50.0,
        inputs={"edge": 0.1},
        venue="polymarket",
        direction="LONG",
    )
    venue_configs = build_default_venue_configs()

    polymarket_decision = engine.build_polymarket_decision(shared_signal, venue_configs["polymarket"], discovery_decision)
    futures_decision = engine.build_binance_futures_decision(shared_signal, venue_configs["binance_futures"], [], 100.0)

    assert polymarket_decision.should_trade is True
    assert polymarket_decision.venue == "polymarket"
    assert futures_decision.venue == "binance_futures"
    assert futures_decision.direction == "LONG"
