import asyncio
import logging
import os

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
