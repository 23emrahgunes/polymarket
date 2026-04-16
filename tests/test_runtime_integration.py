import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import pandas as pd

from src.database import Database
from src.runtime import GhostBotRuntime, RuntimeSettings
from src.venue_config import build_default_venue_configs


@pytest.mark.asyncio
async def test_runtime_debug_mode_creates_trade_and_decreases_wallet(tmp_path):
    db_path = str(tmp_path / "test_runtime_debug.db")

    settings = RuntimeSettings(
        exchange_id="coinbase",
        db_path=db_path,
        debug_signal_mode=True,
        runtime_verify_once=True,
    )
    runtime = GhostBotRuntime(settings)
    await runtime.run()

    db = Database(db_path)
    await db.connect()
    balance = await db.get_balance()
    trades = await db.get_recent_trades()
    await db.close()

    assert balance < 1000.0
    assert len(trades) >= 1
    assert trades[0]["market_id"] == "debug-sports-finals-2026"

@pytest.mark.asyncio
async def test_discovery_logs_missing_exchange_price(caplog, tmp_path):
    db_path = str(tmp_path / "test_runtime_logs.db")

    runtime = GhostBotRuntime(
        RuntimeSettings(
            exchange_id="coinbase",
            db_path=db_path,
            debug_signal_mode=False,
            runtime_verify_once=False,
        )
    )
    await runtime.initialize()

    market = {
        "market_id": "crypto-market-1",
        "question": "Will BTC be above $100,000 on December 31, 2026?",
        "token_id": "token-1",
        "token_ids": ["token-1"],
        "category": "CRYPTO",
        "volume_24h": 200000.0,
    }

    async def fake_snapshot(token_id):
        return {
            "token_id": token_id,
            "best_bid": 0.4,
            "best_ask": 0.41,
            "mid_price": 0.405,
            "spread_pct": 0.024,
            "is_valid": True,
            "reason": "ok",
        }

    async def fake_refresh(symbol):
        return None

    runtime.scanner.get_orderbook_snapshot = fake_snapshot
    runtime.scanner.refresh_symbol_price = fake_refresh
    runtime.scanner.current_prices = {}

    with caplog.at_level(logging.INFO):
        await runtime.process_market(market)

    await runtime.close()
    assert "missing_exchange_price" in caplog.text


@pytest.mark.asyncio
async def test_non_crypto_discovery_logs_route_only(caplog, tmp_path):
    db_path = str(tmp_path / "test_runtime_route.db")

    runtime = GhostBotRuntime(
        RuntimeSettings(
            exchange_id="coinbase",
            db_path=db_path,
            debug_signal_mode=False,
            runtime_verify_once=False,
        )
    )
    await runtime.initialize()

    with caplog.at_level(logging.INFO):
        await runtime.process_market(
            {
                "market_id": "sports-market-1",
                "question": "Will Team A win the championship match?",
                "token_id": "sports-token-1",
                "token_ids": ["sports-token-1"],
                "category": "SPORTS",
                "volume_24h": 90000.0,
            }
        )

    await runtime.close()
    assert "route_whale_orderflow_only" in caplog.text


@pytest.mark.asyncio
async def test_crypto_market_routes_to_polymarket_and_binance_futures(tmp_path):
    db_path = str(tmp_path / "test_crypto_multi_venue.db")
    venue_configs = build_default_venue_configs()
    venue_configs["binance_futures"] = venue_configs["binance_futures"].__class__(
        **{**venue_configs["binance_futures"].__dict__, "enabled": True}
    )
    runtime = GhostBotRuntime(
        RuntimeSettings(
            exchange_id="coinbase",
            db_path=db_path,
            debug_signal_mode=False,
            runtime_verify_once=False,
            venue_configs=venue_configs,
        )
    )
    await runtime.initialize()

    market = {
        "market_id": "crypto-market-dual",
        "question": "Will BTC be above $100,000 on December 31, 2026?",
        "token_id": "token-btc-dual",
        "token_ids": ["token-btc-dual", "token-btc-dual-no"],
        "category": "CRYPTO",
        "volume_24h": 250000.0,
    }

    async def fake_orderbook(token_id):
        return {
            "token_id": token_id,
            "best_bid": 0.405,
            "best_ask": 0.415,
            "mid_price": 0.41,
            "spread_pct": 0.024,
            "is_valid": True,
            "reason": "ok",
        }

    async def fake_refresh(symbol):
        return 102000.0

    async def fake_hist(symbol, timeframe="1m", limit=1440):
        rows = []
        for index in range(100):
            close_price = 100000.0 + (index * 40.0)
            rows.append([index, close_price * 0.998, close_price * 1.002, close_price * 0.996, close_price, 1000.0 + index])
        return pd.DataFrame(
            rows,
            columns=["timestamp", "open", "high", "low", "close", "volume"],
        )

    async def fake_futures_snapshot(symbol):
        return {
            "symbol": symbol,
            "last_price": 103200.0,
            "mark_price": 103100.0,
            "best_bid": 103050.0,
            "best_ask": 103150.0,
            "spread_pct": 0.00097,
            "volume_24h": 300000.0,
            "open_interest": 1800000.0,
            "funding_rate": 0.0002,
            "is_valid": True,
            "reason": "ok",
        }

    runtime.scanner.get_orderbook_snapshot = fake_orderbook
    runtime.scanner.refresh_symbol_price = fake_refresh
    runtime.scanner.get_historical_data = fake_hist
    runtime.scanner.get_futures_historical_data = fake_hist
    runtime.scanner.get_futures_market_snapshot = fake_futures_snapshot
    runtime.scanner.current_prices = {"BTC/USD": 102000.0}

    await runtime.process_market(market)

    db = Database(db_path)
    await db.connect()
    polymarket_trades = [row for row in await db.get_open_trades(venue="polymarket") if row["market_id"] == "crypto-market-dual"]
    futures_trades = [row for row in await db.get_open_trades(venue="binance_futures") if row["market_id"] == "BTC/USDT:USDT"]
    futures_positions = await db.get_open_positions(venue="binance_futures", symbol_or_market_id="BTC/USDT:USDT")

    assert len(polymarket_trades) == 1
    assert polymarket_trades[0]["side"] in {"YES", "NO"}
    assert len(futures_trades) == 1
    assert futures_trades[0]["instrument_type"] == "futures"
    assert len(futures_positions) == 1

    await db.close()
    await runtime.close()


@pytest.mark.asyncio
async def test_runtime_verify_once_waits_for_dual_crypto_venues(tmp_path):
    db_path = str(tmp_path / "test_crypto_dual_verify.db")
    venue_configs = build_default_venue_configs()
    venue_configs["binance_futures"] = venue_configs["binance_futures"].__class__(
        **{**venue_configs["binance_futures"].__dict__, "enabled": True}
    )

    runtime = GhostBotRuntime(
        RuntimeSettings(
            exchange_id="coinbase",
            db_path=db_path,
            debug_signal_mode=True,
            debug_signal_profile="crypto_dual",
            runtime_verify_once=True,
            verify_required_venues=("polymarket", "binance_futures"),
            verify_required_category="CRYPTO",
            venue_configs=venue_configs,
        )
    )
    await asyncio.wait_for(runtime.run(), timeout=20)

    db = Database(db_path)
    await db.connect()
    trades = await db.get_recent_trades(limit=10)
    futures_positions = await db.get_open_positions(venue="binance_futures", symbol_or_market_id="BTC/USDT:USDT")
    await db.close()

    assert runtime.verify_completed_venues == {"polymarket", "binance_futures"}
    assert any(
        trade["venue"] == "polymarket"
        and trade["market_id"] == "debug-crypto-btc-100k-2026"
        and trade["source_signal"] == "blended_crypto"
        for trade in trades
    )
    assert any(
        trade["venue"] == "binance_futures"
        and trade["market_id"] == "BTC/USDT:USDT"
        and trade["instrument_type"] == "futures"
        for trade in trades
    )
    assert len(futures_positions) == 1


@pytest.mark.asyncio
async def test_runtime_builds_sized_down_position_plan_for_binance_technical(tmp_path):
    db_path = str(tmp_path / "test_runtime_position_plan.db")
    venue_configs = build_default_venue_configs()
    venue_configs["binance_futures"] = venue_configs["binance_futures"].__class__(
        **{**venue_configs["binance_futures"].__dict__, "enabled": True}
    )
    runtime = GhostBotRuntime(
        RuntimeSettings(
            exchange_id="coinbase",
            db_path=db_path,
            debug_signal_mode=False,
            runtime_verify_once=False,
            venue_configs=venue_configs,
        )
    )
    await runtime.initialize()

    position_id = await runtime.db.create_venue_position(
        venue="binance_futures",
        execution_mode="paper",
        instrument_type="futures",
        symbol_or_market_id="BTC/USDT:USDT",
        side="LONG",
        qty_or_shares=0.001,
        entry_price=100000.0,
        notional_usd=200.0,
        leverage=2,
        status="OPEN",
    )

    plan = await runtime._build_binance_technical_position_plan("binance_futures", minimum_trade_size=25.0)

    assert plan["base_trade_size"] == 100.0
    assert plan["effective_trade_size"] == 50.0
    assert plan["remaining_position_capacity_usd"] == 50.0
    assert plan["position_capacity_sized_down"] is True
    assert plan["open_position_count_at_decision"] == 1
    assert plan["open_position_notional_usd_at_decision"] == 200.0

    await runtime.close()


@pytest.mark.asyncio
async def test_runtime_reviews_stale_binance_technical_positions_and_executes_time_exit(tmp_path):
    db_path = str(tmp_path / "test_runtime_stale_exit.db")
    venue_configs = build_default_venue_configs()
    venue_configs["binance_futures"] = venue_configs["binance_futures"].__class__(
        **{**venue_configs["binance_futures"].__dict__, "enabled": True}
    )
    runtime = GhostBotRuntime(
        RuntimeSettings(
            exchange_id="coinbase",
            db_path=db_path,
            debug_signal_mode=False,
            runtime_verify_once=False,
            binance_technical_paper_enabled=True,
            venue_configs=venue_configs,
        )
    )
    await runtime.initialize()

    opened_at = (datetime.now(timezone.utc) - timedelta(minutes=150)).strftime("%Y-%m-%d %H:%M:%S")
    position_id = await runtime.db.create_venue_position(
        venue="binance_futures",
        execution_mode="paper",
        instrument_type="futures",
        symbol_or_market_id="BTC/USDT:USDT",
        side="LONG",
        qty_or_shares=0.001,
        entry_price=100000.0,
        notional_usd=100.0,
        leverage=2,
        status="OPEN",
        strategy_profile="binance_technical_sampling",
        sample_kind="live_paper",
    )
    await runtime.db.conn.execute(
        "UPDATE venue_positions SET opened_at = ? WHERE id = ?",
        (opened_at, position_id),
    )
    await runtime.db.conn.commit()

    async def fake_score(symbol):
        signal = SimpleNamespace(
            should_trade=False,
            direction="LONG",
            reasons=["score_below_threshold"],
            score=0.41,
            threshold=0.54,
        )
        return signal, {"mark_price": 100100.0, "last_price": 100100.0}

    exit_calls = []

    async def fake_exit_order(*args, **kwargs):
        exit_calls.append({"args": args, "kwargs": kwargs})
        return True, {"reason": kwargs.get("reason")}

    runtime._score_binance_technical_symbol_for_stale_review = fake_score
    runtime.binance_futures_venue.place_exit_order = fake_exit_order

    await runtime._review_stale_binance_technical_positions()

    assert runtime.technical_stale_review_candidates_90m == 1
    assert runtime.technical_stale_exit_candidates_120m == 1
    assert runtime.technical_open_positions_total == 1
    assert runtime.technical_open_positions_strict == 1
    assert runtime.technical_open_positions_legacy == 0
    assert runtime.technical_stale_exit_executed == 1
    assert exit_calls
    assert exit_calls[0]["kwargs"]["reason"] == "stale_time_exit"

    await runtime.close()


@pytest.mark.asyncio
async def test_runtime_backfills_legacy_binance_technical_positions_for_stale_review(tmp_path):
    db_path = str(tmp_path / "test_runtime_stale_legacy.db")
    venue_configs = build_default_venue_configs()
    venue_configs["binance_futures"] = venue_configs["binance_futures"].__class__(
        **{**venue_configs["binance_futures"].__dict__, "enabled": True}
    )
    runtime = GhostBotRuntime(
        RuntimeSettings(
            exchange_id="coinbase",
            db_path=db_path,
            debug_signal_mode=False,
            runtime_verify_once=False,
            binance_technical_paper_enabled=True,
            venue_configs=venue_configs,
        )
    )
    await runtime.initialize()

    opened_at = (datetime.now(timezone.utc) - timedelta(minutes=150)).strftime("%Y-%m-%d %H:%M:%S")
    position_id = await runtime.db.create_venue_position(
        venue="binance_futures",
        execution_mode="paper",
        instrument_type="futures",
        symbol_or_market_id="ETH/USDT:USDT",
        side="LONG",
        qty_or_shares=0.01,
        entry_price=3000.0,
        notional_usd=75.0,
        leverage=2,
        source_signal="binance_technical_momentum",
        signal_family="unknown",
        strategy_profile="baseline",
        sample_kind="live_paper",
        status="OPEN",
    )
    await runtime.db.conn.execute(
        "UPDATE venue_positions SET opened_at = ?, sample_kind = '', signal_family = '', strategy_profile = 'baseline' WHERE id = ?",
        (opened_at, position_id),
    )
    await runtime.db.conn.commit()

    async def fake_score(symbol):
        signal = SimpleNamespace(
            should_trade=False,
            direction="LONG",
            reasons=["technical_alignment_weak"],
            score=0.39,
            threshold=0.54,
        )
        return signal, {"mark_price": 3010.0, "last_price": 3010.0}

    exit_calls = []

    async def fake_exit_order(*args, **kwargs):
        exit_calls.append({"args": args, "kwargs": kwargs})
        return True, {"reason": kwargs.get("reason")}

    runtime._score_binance_technical_symbol_for_stale_review = fake_score
    runtime.binance_futures_venue.place_exit_order = fake_exit_order

    await runtime._review_stale_binance_technical_positions()

    assert runtime.technical_open_positions_total == 1
    assert runtime.technical_open_positions_strict == 0
    assert runtime.technical_open_positions_legacy == 1
    assert runtime.technical_open_positions_backfilled == 1
    cursor = await runtime.db.conn.execute(
        "SELECT strategy_profile, sample_kind, signal_family FROM venue_positions WHERE id = ?",
        (position_id,),
    )
    row = await cursor.fetchone()
    assert row["strategy_profile"] == "binance_technical_sampling"
    assert row["sample_kind"] == "live_paper"
    assert row["signal_family"] == "binance_technical_momentum"
    assert exit_calls
    assert exit_calls[0]["kwargs"]["reason"] == "stale_time_exit"

    await runtime.close()


@pytest.mark.asyncio
async def test_runtime_executes_hard_timeout_stale_exit_for_old_technical_position(tmp_path):
    db_path = str(tmp_path / "test_runtime_stale_hard_timeout.db")
    venue_configs = build_default_venue_configs()
    venue_configs["binance_futures"] = venue_configs["binance_futures"].__class__(
        **{**venue_configs["binance_futures"].__dict__, "enabled": True}
    )
    runtime = GhostBotRuntime(
        RuntimeSettings(
            exchange_id="coinbase",
            db_path=db_path,
            debug_signal_mode=False,
            runtime_verify_once=False,
            binance_technical_paper_enabled=True,
            venue_configs=venue_configs,
        )
    )
    await runtime.initialize()

    opened_at = (datetime.now(timezone.utc) - timedelta(minutes=300)).strftime("%Y-%m-%d %H:%M:%S")
    position_id = await runtime.db.create_venue_position(
        venue="binance_futures",
        execution_mode="paper",
        instrument_type="futures",
        symbol_or_market_id="SOL/USDT:USDT",
        side="LONG",
        qty_or_shares=1.0,
        entry_price=140.0,
        notional_usd=80.0,
        leverage=2,
        source_signal="binance_technical_momentum",
        strategy_profile="binance_technical_sampling",
        sample_kind="live_paper",
        status="OPEN",
    )
    await runtime.db.conn.execute(
        "UPDATE venue_positions SET opened_at = ? WHERE id = ?",
        (opened_at, position_id),
    )
    await runtime.db.conn.commit()

    async def fake_score(symbol):
        signal = SimpleNamespace(
            should_trade=False,
            direction="LONG",
            reasons=["score_below_threshold"],
            score=0.37,
            threshold=0.54,
        )
        return signal, {"mark_price": 141.0, "last_price": 141.0}

    async def no_recent_support(*args, **kwargs):
        return False

    exit_calls = []

    async def fake_exit_order(*args, **kwargs):
        exit_calls.append({"args": args, "kwargs": kwargs})
        return True, {"reason": kwargs.get("reason")}

    runtime._score_binance_technical_symbol_for_stale_review = fake_score
    runtime._has_recent_same_direction_technical_support = no_recent_support
    runtime.binance_futures_venue.place_exit_order = fake_exit_order

    await runtime._review_stale_binance_technical_positions()

    assert runtime.technical_stale_hard_timeout_candidates_240m == 1
    assert runtime.technical_stale_hard_timeout_executed == 1
    assert exit_calls
    assert exit_calls[0]["kwargs"]["reason"] == "stale_hard_timeout_exit"

    await runtime.close()


@pytest.mark.asyncio
async def test_runtime_rescues_unclassified_old_binance_paper_position_for_stale_review(tmp_path):
    db_path = str(tmp_path / "test_runtime_stale_unclassified_rescue.db")
    venue_configs = build_default_venue_configs()
    venue_configs["binance_futures"] = venue_configs["binance_futures"].__class__(
        **{**venue_configs["binance_futures"].__dict__, "enabled": True}
    )
    runtime = GhostBotRuntime(
        RuntimeSettings(
            exchange_id="coinbase",
            db_path=db_path,
            debug_signal_mode=False,
            runtime_verify_once=False,
            binance_technical_paper_enabled=True,
            venue_configs=venue_configs,
        )
    )
    await runtime.initialize()

    opened_at = (datetime.now(timezone.utc) - timedelta(minutes=300)).strftime("%Y-%m-%d %H:%M:%S")
    position_id = await runtime.db.create_venue_position(
        venue="binance_futures",
        execution_mode="paper",
        instrument_type="futures",
        symbol_or_market_id="SOL/USDT:USDT",
        side="LONG",
        qty_or_shares=1.0,
        entry_price=140.0,
        notional_usd=80.0,
        leverage=2,
        source_signal="manual_recovered_position",
        signal_family="unknown",
        strategy_profile="baseline",
        sample_kind="",
        status="OPEN",
    )
    await runtime.db.conn.execute(
        "UPDATE venue_positions SET opened_at = ?, sample_kind = '', signal_family = 'unknown', strategy_profile = 'baseline' WHERE id = ?",
        (opened_at, position_id),
    )
    await runtime.db.conn.commit()

    async def fake_score(symbol):
        signal = SimpleNamespace(
            should_trade=False,
            direction="LONG",
            reasons=["score_below_threshold"],
            score=0.37,
            threshold=0.54,
        )
        return signal, {"mark_price": 141.0, "last_price": 141.0}

    async def no_recent_support(*args, **kwargs):
        return False

    exit_calls = []

    async def fake_exit_order(*args, **kwargs):
        exit_calls.append({"args": args, "kwargs": kwargs})
        return True, {"reason": kwargs.get("reason")}

    runtime._score_binance_technical_symbol_for_stale_review = fake_score
    runtime._has_recent_same_direction_technical_support = no_recent_support
    runtime.binance_futures_venue.place_exit_order = fake_exit_order

    await runtime._review_stale_binance_technical_positions()

    assert runtime.technical_open_positions_total == 1
    assert runtime.technical_open_positions_legacy == 1
    assert runtime.technical_open_positions_rescue == 1
    assert runtime.technical_open_positions_backfilled == 1
    assert runtime.technical_legacy_open_positions[0]["classification"] == "legacy_technical_unclassified"
    assert runtime.technical_stale_hard_timeout_candidates_240m == 1
    assert runtime.technical_stale_hard_timeout_executed == 1
    assert exit_calls
    assert exit_calls[0]["kwargs"]["reason"] == "stale_hard_timeout_exit"

    await runtime.close()


@pytest.mark.asyncio
async def test_runtime_skips_hard_timeout_when_recent_same_direction_support_exists(tmp_path):
    db_path = str(tmp_path / "test_runtime_stale_recent_support.db")
    venue_configs = build_default_venue_configs()
    venue_configs["binance_futures"] = venue_configs["binance_futures"].__class__(
        **{**venue_configs["binance_futures"].__dict__, "enabled": True}
    )
    runtime = GhostBotRuntime(
        RuntimeSettings(
            exchange_id="coinbase",
            db_path=db_path,
            debug_signal_mode=False,
            runtime_verify_once=False,
            binance_technical_paper_enabled=True,
            venue_configs=venue_configs,
        )
    )
    await runtime.initialize()

    opened_at = (datetime.now(timezone.utc) - timedelta(minutes=300)).strftime("%Y-%m-%d %H:%M:%S")
    position_id = await runtime.db.create_venue_position(
        venue="binance_futures",
        execution_mode="paper",
        instrument_type="futures",
        symbol_or_market_id="BTC/USDT:USDT",
        side="LONG",
        qty_or_shares=0.001,
        entry_price=100000.0,
        notional_usd=100.0,
        leverage=2,
        source_signal="binance_technical_momentum",
        strategy_profile="binance_technical_sampling",
        sample_kind="live_paper",
        status="OPEN",
    )
    await runtime.db.conn.execute(
        "UPDATE venue_positions SET opened_at = ? WHERE id = ?",
        (opened_at, position_id),
    )
    await runtime.db.conn.commit()

    async def fake_score(symbol):
        signal = SimpleNamespace(
            should_trade=False,
            direction="LONG",
            reasons=["score_below_threshold"],
            score=0.35,
            threshold=0.54,
        )
        return signal, {"mark_price": 100050.0, "last_price": 100050.0}

    async def has_recent_support(*args, **kwargs):
        return True

    exit_calls = []

    async def fake_exit_order(*args, **kwargs):
        exit_calls.append({"args": args, "kwargs": kwargs})
        return True, {"reason": kwargs.get("reason")}

    runtime._score_binance_technical_symbol_for_stale_review = fake_score
    runtime._has_recent_same_direction_technical_support = has_recent_support
    runtime.binance_futures_venue.place_exit_order = fake_exit_order

    await runtime._review_stale_binance_technical_positions()

    assert runtime.technical_stale_hard_timeout_candidates_240m == 1
    assert runtime.technical_stale_exit_skipped_recent_support == 1
    assert not exit_calls

    await runtime.close()


@pytest.mark.asyncio
async def test_runtime_verify_once_waits_for_triple_crypto_venues(tmp_path):
    db_path = str(tmp_path / "test_crypto_triple_verify.db")
    venue_configs = build_default_venue_configs()
    venue_configs["binance_futures"] = venue_configs["binance_futures"].__class__(
        **{**venue_configs["binance_futures"].__dict__, "enabled": True}
    )
    venue_configs["binance_spot"] = venue_configs["binance_spot"].__class__(
        **{**venue_configs["binance_spot"].__dict__, "enabled": True}
    )

    runtime = GhostBotRuntime(
        RuntimeSettings(
            exchange_id="coinbase",
            db_path=db_path,
            debug_signal_mode=True,
            debug_signal_profile="crypto_triple_long",
            runtime_verify_once=True,
            verify_required_venues=("polymarket", "binance_futures", "binance_spot"),
            verify_required_category="CRYPTO",
            venue_configs=venue_configs,
        )
    )
    await asyncio.wait_for(runtime.run(), timeout=20)

    db = Database(db_path)
    await db.connect()
    trades = await db.get_recent_trades(limit=10)
    futures_positions = await db.get_open_positions(venue="binance_futures", symbol_or_market_id="BTC/USDT:USDT")
    spot_positions = await db.get_open_positions(venue="binance_spot", symbol_or_market_id="BTC/USDT")
    await db.close()

    assert runtime.verify_completed_venues == {"polymarket", "binance_futures", "binance_spot"}
    assert any(trade["venue"] == "polymarket" and trade["market_id"] == "debug-crypto-btc-95k-2026" for trade in trades)
    assert any(trade["venue"] == "binance_futures" and trade["market_id"] == "BTC/USDT:USDT" for trade in trades)
    assert any(trade["venue"] == "binance_spot" and trade["market_id"] == "BTC/USDT" and trade["instrument_type"] == "spot" for trade in trades)
    assert len(futures_positions) == 1
    assert len(spot_positions) == 1
