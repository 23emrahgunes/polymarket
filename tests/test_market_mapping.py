import pytest

from src.copy_trader import CopyTrader
from src.database import Database
from src.decision_engine import DecisionEngine
from src.runtime import GhostBotRuntime, RuntimeSettings


class _FakeScanner:
    async def get_orderbook_snapshot(self, token_id):
        return {
            "token_id": token_id,
            "best_bid": 0.57,
            "best_ask": 0.58,
            "mid_price": 0.575,
            "spread_pct": 0.017,
            "is_valid": True,
            "reason": "ok",
        }


class _FakeTrader:
    def __init__(self):
        self.calls = []

    async def execute_trade(self, *args, **kwargs):
        self.calls.append({"args": args, "kwargs": kwargs})
        return True, "ok"


@pytest.mark.asyncio
async def test_copy_trader_uses_alias_cache_to_execute_trade(tmp_path):
    db_path = str(tmp_path / "alias_cache_trade.db")
    db = Database(db_path)
    await db.connect()
    await db.upsert_market_aliases(
        market_id="0xMARKET1",
        aliases=["0xMARKET1", "0xTOKEN1"],
        question="Will Team A win the championship?",
        category="SPORTS",
        volume_24h=75000.0,
        active=True,
        source="explorer",
    )

    trader = _FakeTrader()
    copy_trader = CopyTrader(trader, _FakeScanner(), db, DecisionEngine())

    success = await copy_trader.evaluate_activity_event(
        {
            "type": "WHALE_EVENT",
            "token_id": "0xTOKEN1",
            "side": "BUY",
            "amount": 2500.0,
            "wallet": "0xWHALE",
            "source": "activity",
            "alias_candidates": ["0xMARKET1", "0xTOKEN1"],
        }
    )

    cached_alias = await db.resolve_market_alias(["0xTOKEN1"])
    await db.close()

    assert success is True
    assert len(trader.calls) == 1
    assert trader.calls[0]["args"][0] == "0xmarket1"
    assert trader.calls[0]["args"][1] == "YES"
    assert cached_alias is not None


@pytest.mark.asyncio
async def test_copy_trader_lazy_resolves_meaningful_unmapped_event(tmp_path):
    db_path = str(tmp_path / "lazy_lookup_trade.db")
    db = Database(db_path)
    await db.connect()

    trader = _FakeTrader()
    lazy_calls = []

    async def fake_lazy_resolver(alias_candidates, source):
        lazy_calls.append((tuple(alias_candidates), source))
        return {
            "market_id": "0xMARKET2",
            "token_id": "0xTOKEN2",
            "token_ids": ["0xTOKEN2"],
            "question": "Will Team B win the championship?",
            "category": "SPORTS",
            "volume_24h": 80000.0,
            "active": True,
        }

    copy_trader = CopyTrader(
        trader,
        _FakeScanner(),
        db,
        DecisionEngine(),
        market_resolver=fake_lazy_resolver,
    )

    success = await copy_trader.evaluate_signal(
        {
            "whale": "0xWHALE2",
            "action": "BUY",
            "market_id": "0xMARKET2",
            "token_id": None,
            "amount": 5000.0,
            "price": 0.58,
            "alias_candidates": ["0xMARKET2", "0xTOKEN2"],
        }
    )

    cached_alias = await db.resolve_market_alias(["0xTOKEN2"])
    await db.close()

    assert success is True
    assert len(lazy_calls) == 1
    assert len(trader.calls) == 1
    assert cached_alias is not None


@pytest.mark.asyncio
async def test_copy_trader_skips_lazy_lookup_for_tiny_unmapped_cluster(tmp_path):
    db_path = str(tmp_path / "tiny_unmapped.db")
    db = Database(db_path)
    await db.connect()

    lazy_calls = []

    async def fake_lazy_resolver(alias_candidates, source):
        lazy_calls.append((tuple(alias_candidates), source))
        return None

    copy_trader = CopyTrader(
        _FakeTrader(),
        _FakeScanner(),
        db,
        DecisionEngine(),
        market_resolver=fake_lazy_resolver,
    )

    success = await copy_trader.evaluate_activity_event(
        {
            "type": "CLUSTER_DETECTED",
            "market_id": "0xUNKNOWN",
            "token_id": "0xTOKENX",
            "side": "BUY",
            "amount": 0.02,
            "wallets_count": 2,
            "source": "activity",
            "alias_candidates": ["0xUNKNOWN", "0xTOKENX"],
        }
    )
    await db.close()

    assert success is False
    assert lazy_calls == []


@pytest.mark.asyncio
async def test_runtime_bootstrap_persists_market_aliases(tmp_path):
    db_path = str(tmp_path / "runtime_alias_cache.db")
    runtime = GhostBotRuntime(
        RuntimeSettings(
            exchange_id="coinbase",
            db_path=db_path,
            debug_signal_mode=False,
            runtime_verify_once=False,
        )
    )
    await runtime.initialize()

    async def fake_fetch_active_markets(limit=200):
        return [
            {
                "market_id": "0xMARKET3",
                "token_id": "0xTOKEN3",
                "token_ids": ["0xTOKEN3", "0xTOKEN3NO"],
                "alias_candidates": ["0xMARKET3", "0xTOKEN3", "0xTOKEN3NO"],
                "question": "Will BTC be above $100,000 on December 31, 2026?",
                "category": "CRYPTO",
                "volume_24h": 200000.0,
                "active": True,
            }
        ]

    runtime.explorer.fetch_active_markets = fake_fetch_active_markets
    await runtime.bootstrap_market_context()

    alias_row = await runtime.db.resolve_market_alias(["0xTOKEN3"])
    await runtime.close()

    assert alias_row is not None
    assert alias_row["market_id"] == "0xmarket3"
