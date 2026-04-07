import asyncio
import logging
import os

import pytest

from src.database import Database
from src.runtime import GhostBotRuntime, RuntimeSettings


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
