import json
import time

import pytest

from src.copy_trader import CopyTrader
from src.database import Database
from src.decision_engine import DecisionEngine
from src.evaluation_utils import STRATEGY_PROFILE_SAMPLING_RELAXED
from src.market_mapping import collect_alias_candidates
from src.runtime import GhostBotRuntime, RuntimeSettings
from src.trading import TradeExecutor


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


class _CountingScanner:
    def __init__(self):
        self.calls = []

    async def get_orderbook_snapshot(self, token_id):
        self.calls.append(token_id)
        return {
            "token_id": token_id,
            "best_bid": 0.57,
            "best_ask": 0.58,
            "mid_price": 0.575,
            "spread_pct": 0.017,
            "is_valid": True,
            "reason": "ok",
        }


class _TokenFallbackScanner:
    def __init__(self, valid_token: str | None):
        self.valid_token = valid_token
        self.calls = []

    async def get_orderbook_snapshot(self, token_id):
        self.calls.append(token_id)
        if token_id == self.valid_token:
            return {
                "token_id": token_id,
                "best_bid": 0.57,
                "best_ask": 0.58,
                "mid_price": 0.575,
                "spread_pct": 0.017,
                "is_valid": True,
                "reason": "ok",
            }
        return {
            "token_id": token_id,
            "best_bid": None,
            "best_ask": None,
            "mid_price": None,
            "spread_pct": None,
            "is_valid": False,
            "reason": "missing_polymarket_token_price",
        }


class _FakeTrader:
    def __init__(self):
        self.calls = []

    async def execute_trade(self, *args, **kwargs):
        self.calls.append({"args": args, "kwargs": kwargs})
        return True, "ok"


@pytest.mark.asyncio
async def test_trade_executor_persists_gated_whale_copy_execute_inputs(tmp_path):
    db_path = str(tmp_path / "gated_execute_audit.db")
    db = Database(db_path)
    await db.connect()

    trader = TradeExecutor(db, live_mode=False)
    success, _ = await trader.execute_trade(
        "0xMARKET-EXEC",
        "YES",
        25.0,
        0.57,
        0.04,
        0.81,
        source="activity",
        category="SPORTS",
        venue="polymarket",
        source_signal="whale_tracker",
        signal_family="whale",
        strategy_profile=STRATEGY_PROFILE_SAMPLING_RELAXED,
        audit_inputs={
            "copy_policy": "gated_whale_copy",
            "whale_copy_relaxed_gate": True,
            "token_recovery_attempted": True,
            "token_recovery_hit": True,
            "gated_whale_event_count": 3,
            "gated_total_notional": 1500.0,
            "gated_unique_wallets": 2,
            "gated_source_count": 2,
            "gated_max_trust": 0.71,
        },
    )

    async with db.conn.execute(
        """
        SELECT action, inputs_json
        FROM decision_audit
        WHERE action = 'execute'
        ORDER BY id DESC
        LIMIT 1
        """
    ) as cursor:
        execute_row = await cursor.fetchone()
    await db.close()

    assert success is True
    assert execute_row is not None
    execute_inputs = json.loads(execute_row["inputs_json"])
    assert execute_inputs["copy_policy"] == "gated_whale_copy"
    assert execute_inputs["whale_copy_relaxed_gate"] is True
    assert execute_inputs["token_recovery_attempted"] is True
    assert execute_inputs["token_recovery_hit"] is True
    assert execute_inputs["gated_whale_event_count"] == 3
    assert execute_inputs["gated_total_notional"] == 1500.0
    assert execute_inputs["trade_id"] >= 1


def test_collect_alias_candidates_expands_hex_variants():
    aliases = collect_alias_candidates("0xABCDEF1234567890", extra=["abcdef1234567890"])

    assert "0xabcdef1234567890" in aliases
    assert "abcdef1234567890" in aliases


@pytest.mark.asyncio
async def test_whale_graph_discovery_records_source_counts(tmp_path):
    db_path = str(tmp_path / "whale_graph_discovery.db")
    db = Database(db_path)
    await db.connect()

    await db.upsert_whale_wallet_graph_cluster(
        ["0xGraphA", "0xGraphB", "0xGraphA"],
        market_ref="0xMARKET-GRAPH",
        side="BUY",
        total_notional=2_000.0,
        event_category="SPORTS",
    )

    counts = await db.get_whale_wallet_counts()
    summary = await db.get_whale_universe_summary()
    ranked = await db.get_ranked_whale_wallets(
        limit=10,
        source_type="graph_discovery",
        min_event_count_24h=1,
        single_event_min_usd=1_000.0,
    )
    await db.close()

    assert counts["graph_discovered_wallets"] == 2
    assert summary["graph_discovered_wallets"] == 2
    assert {row["address"] for row in ranked} == {"0xgrapha", "0xgraphb"}


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
async def test_copy_trader_persists_aliases_before_filtering_sell_side(tmp_path):
    db_path = str(tmp_path / "sell_side_alias_persist.db")
    db = Database(db_path)
    await db.connect()
    await db.upsert_market_aliases(
        market_id="0xMARKET-SELL",
        aliases=["0xMARKET-SELL", "0xTOKEN-SELL"],
        question="Will Team Sell lose?",
        category="SPORTS",
        volume_24h=80_000.0,
        active=True,
        source="explorer",
    )

    audits = []
    trader = _FakeTrader()
    scanner = _CountingScanner()
    copy_trader = CopyTrader(trader, scanner, db, DecisionEngine(audit_sink=audits.append))

    success = await copy_trader.evaluate_activity_event(
        {
            "type": "WHALE_EVENT",
            "market_id": "0xMARKET-SELL",
            "token_id": "0xTOKEN-SELL",
            "side": "SELL",
            "amount": 2_500.0,
            "wallet": "0xSELLWHALE",
            "source": "activity",
            "alias_candidates": ["sell-market-slug"],
        }
    )

    persisted_alias = await db.resolve_market_alias(["sell-market-slug"])
    await db.close()

    assert success is False
    assert trader.calls == []
    assert scanner.calls == []
    assert persisted_alias is not None
    assert audits[-1].reasons == ["unsupported_side_filtered"]
    assert audits[-1].inputs["original_side"] == "SELL"
    assert audits[-1].inputs["copy_eligible"] is False
    assert audits[-1].inputs["side_filter_stage"] == "post_mapping_pre_orderbook"


@pytest.mark.asyncio
async def test_copy_trader_recovers_orderbook_token_from_same_market_aliases(tmp_path):
    db_path = str(tmp_path / "token_recovery_hit.db")
    db = Database(db_path)
    await db.connect()
    bad_token = "0xaaaaaaaaaaaaaaaa"
    good_token = "0xbbbbbbbbbbbbbbbb"
    await db.upsert_market_aliases(
        market_id="0xcccccccccccccccc",
        aliases=["0xcccccccccccccccc", bad_token, good_token],
        question="Will Team Token win the championship?",
        category="SPORTS",
        volume_24h=75_000.0,
        active=True,
        source="explorer",
    )

    trader = _FakeTrader()
    scanner = _TokenFallbackScanner(valid_token=good_token)
    copy_trader = CopyTrader(trader, scanner, db, DecisionEngine())
    copy_trader.update_sampling_state(
        enabled=True,
        target_reached=False,
        closed_trades=0,
        target_closed_trades=20,
    )

    success = await copy_trader.evaluate_activity_event(
        {
            "type": "WHALE_EVENT",
            "token_id": bad_token,
            "side": "BUY",
            "amount": 2500.0,
            "wallet": "0xWHALE",
            "source": "activity",
            "alias_candidates": ["0xcccccccccccccccc", bad_token, good_token],
        }
    )
    await db.close()

    assert success is True
    assert scanner.calls[0] == bad_token
    assert good_token in scanner.calls
    assert len(trader.calls) == 1
    assert trader.calls[0]["kwargs"]["strategy_profile"] == STRATEGY_PROFILE_SAMPLING_RELAXED
    assert trader.calls[0]["args"][3] == 0.575


@pytest.mark.asyncio
async def test_copy_trader_marks_token_recovery_failed_when_alias_tokens_miss(tmp_path):
    db_path = str(tmp_path / "token_recovery_failed.db")
    db = Database(db_path)
    await db.connect()
    bad_token = "0xaaaaaaaaaaaaaaaa"
    alternate_token = "0xbbbbbbbbbbbbbbbb"
    await db.upsert_market_aliases(
        market_id="0xcccccccccccccccc",
        aliases=["0xcccccccccccccccc", bad_token, alternate_token],
        question="Will Team Token fail?",
        category="SPORTS",
        volume_24h=75_000.0,
        active=True,
        source="explorer",
    )

    audits = []
    trader = _FakeTrader()
    scanner = _TokenFallbackScanner(valid_token=None)
    copy_trader = CopyTrader(trader, scanner, db, DecisionEngine(audit_sink=audits.append))
    copy_trader.update_sampling_state(
        enabled=True,
        target_reached=False,
        closed_trades=0,
        target_closed_trades=20,
    )

    success = await copy_trader.evaluate_activity_event(
        {
            "type": "WHALE_EVENT",
            "token_id": bad_token,
            "side": "BUY",
            "amount": 2500.0,
            "wallet": "0xWHALE",
            "source": "activity",
            "alias_candidates": ["0xcccccccccccccccc", bad_token, alternate_token],
        }
    )
    await db.close()

    assert success is False
    assert len(trader.calls) == 0
    assert scanner.calls[0] == bad_token
    assert alternate_token in scanner.calls
    assert "missing_polymarket_token_price" in audits[-1].reasons
    assert "token_recovery_failed" in audits[-1].reasons
    assert audits[-1].inputs["token_recovery_attempted"] is True
    assert audits[-1].inputs["token_recovery_failed"] is True
    assert audits[-1].inputs["copy_policy"] == "gated_whale_copy"


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
async def test_copy_trader_passes_whale_alias_candidates_into_lazy_lookup(tmp_path):
    db_path = str(tmp_path / "whale_alias_candidates.db")
    db = Database(db_path)
    await db.connect()

    trader = _FakeTrader()
    lazy_calls = []

    async def fake_lazy_resolver(alias_candidates, source):
        lazy_calls.append((tuple(alias_candidates), source))
        return {
            "market_id": "0xMARKET4",
            "token_id": "0xTOKEN4",
            "token_ids": ["0xTOKEN4"],
            "question": "Will Team D win the championship?",
            "category": "SPORTS",
            "volume_24h": 91000.0,
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
            "whale": "0xWHALE4",
            "action": "BUY",
            "market_id": None,
            "token_id": None,
            "amount": 5000.0,
            "price": 0.58,
            "alias_candidates": ["0xMARKET4", "0xTOKEN4"],
        }
    )

    await db.close()

    assert success is True
    assert lazy_calls == [(("0xmarket4", "0xtoken4"), "whale_tracker")]
    assert len(trader.calls) == 1


@pytest.mark.asyncio
async def test_copy_trader_promotes_market_after_successful_lazy_lookup(tmp_path):
    db_path = str(tmp_path / "hot_window_promotion.db")
    db = Database(db_path)
    await db.connect()

    trader = _FakeTrader()
    promotion_calls = []

    async def fake_lazy_resolver(alias_candidates, source):
        return {
            "market_id": "0xMARKET5",
            "token_id": "0xTOKEN5",
            "token_ids": ["0xTOKEN5"],
            "question": "Will Team E win the championship?",
            "category": "SPORTS",
            "volume_24h": 99000.0,
            "active": True,
        }

    async def fake_promotion_callback(**kwargs):
        promotion_calls.append(kwargs)
        return True

    copy_trader = CopyTrader(
        trader,
        _FakeScanner(),
        db,
        DecisionEngine(),
        market_resolver=fake_lazy_resolver,
        market_promotion_callback=fake_promotion_callback,
    )

    success = await copy_trader.evaluate_signal(
        {
            "whale": "0xWHALE5",
            "action": "BUY",
            "market_id": "0xMARKET5",
            "token_id": None,
            "amount": 5000.0,
            "price": 0.58,
            "alias_candidates": ["0xMARKET5", "0xTOKEN5"],
        }
    )

    await db.close()

    assert success is True
    assert len(promotion_calls) == 1
    assert promotion_calls[0]["stage"] == "lazy_lookup"
    assert promotion_calls[0]["source"] == "whale_tracker"
    assert promotion_calls[0]["context"]["market_id"] == "0xMARKET5"


@pytest.mark.asyncio
async def test_copy_trader_uses_lookup_universe_context_before_lazy_retry(tmp_path):
    db_path = str(tmp_path / "lookup_universe_trade.db")
    db = Database(db_path)
    await db.connect()

    trader = _FakeTrader()
    lazy_calls = []

    async def fake_lazy_resolver(alias_candidates, source):
        lazy_calls.append((tuple(alias_candidates), source))
        return None

    copy_trader = CopyTrader(
        trader,
        _FakeScanner(),
        db,
        DecisionEngine(),
        market_resolver=fake_lazy_resolver,
        lookup_context_resolver=lambda aliases: {
            "market_id": "0xMARKET-LOOKUP",
            "token_id": "0xTOKEN-LOOKUP",
            "token_ids": ["0xTOKEN-LOOKUP"],
            "question": "Will Team Lookup win the championship?",
            "category": "SPORTS",
            "volume_24h": 90000.0,
            "active": True,
        },
    )

    success = await copy_trader.evaluate_activity_event(
        {
            "type": "WHALE_EVENT",
            "market_id": None,
            "token_id": "0xTOKEN-LOOKUP",
            "side": "BUY",
            "amount": 2500.0,
            "wallet": "0xLOOKUP-WHALE",
            "wallets_count": 3,
            "source": "activity",
            "alias_candidates": ["0xMARKET-LOOKUP", "0xTOKEN-LOOKUP"],
        }
    )

    await db.close()

    assert success is True
    assert len(trader.calls) == 1
    assert lazy_calls == []


@pytest.mark.asyncio
async def test_copy_trader_uses_sampling_profile_when_baseline_rejects(tmp_path):
    db_path = str(tmp_path / "sampling_profile_trade.db")
    db = Database(db_path)
    await db.connect()
    await db.upsert_market_aliases(
        market_id="0xMARKETS",
        aliases=["0xMARKETS", "0xTOKENS"],
        question="Will Team Sample win the championship?",
        category="SPORTS",
        volume_24h=16_000.0,
        active=True,
        source="explorer",
    )

    trader = _FakeTrader()
    copy_trader = CopyTrader(trader, _FakeScanner(), db, DecisionEngine())
    copy_trader.update_sampling_state(
        enabled=True,
        target_reached=False,
        closed_trades=0,
        target_closed_trades=20,
    )

    success = await copy_trader.evaluate_activity_event(
        {
            "type": "CLUSTER_DETECTED",
            "token_id": "0xTOKENS",
            "side": "BUY",
            "amount": 600.0,
            "wallets_count": 2,
            "wallet": "0xWHALE-SAMPLE",
            "source": "activity",
            "alias_candidates": ["0xMARKETS", "0xTOKENS"],
        }
    )

    await db.close()

    assert success is True
    assert len(trader.calls) == 1
    assert trader.calls[0]["kwargs"]["strategy_profile"] == STRATEGY_PROFILE_SAMPLING_RELAXED


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
async def test_copy_trader_retries_repeated_unresolved_aliases_before_active_window_reject(tmp_path):
    db_path = str(tmp_path / "unresolved_retry.db")
    db = Database(db_path)
    await db.connect()

    lazy_calls = []

    async def fake_lazy_resolver(alias_candidates, source):
        lazy_calls.append((tuple(alias_candidates), source))
        return {
            "market_id": "0xMARKET-RETRY",
            "token_id": "0xTOKEN-RETRY",
            "token_ids": ["0xTOKEN-RETRY"],
            "question": "Will Team Retry win the championship?",
            "category": "SPORTS",
            "volume_24h": 91000.0,
            "active": True,
        }

    copy_trader = CopyTrader(
        _FakeTrader(),
        _FakeScanner(),
        db,
        DecisionEngine(),
        market_resolver=fake_lazy_resolver,
    )

    first = await copy_trader.evaluate_activity_event(
        {
            "type": "WHALE_EVENT",
            "market_id": "0xMARKET-RETRY",
            "token_id": "0xTOKEN-RETRY",
            "side": "BUY",
            "amount": 50.0,
            "wallet": "0xwallet-a",
            "wallets_count": 1,
            "source": "activity",
            "alias_candidates": ["0xMARKET-RETRY", "0xTOKEN-RETRY"],
        }
    )
    second = await copy_trader.evaluate_activity_event(
        {
            "type": "WHALE_EVENT",
            "market_id": "0xMARKET-RETRY",
            "token_id": "0xTOKEN-RETRY",
            "side": "BUY",
            "amount": 40.0,
            "wallet": "0xwallet-b",
            "wallets_count": 1,
            "source": "activity",
            "alias_candidates": ["0xMARKET-RETRY", "0xTOKEN-RETRY"],
        }
    )

    await db.close()

    assert first is False
    assert second is False
    assert lazy_calls == [(("0xmarket-retry", "0xtoken-retry"), "activity")]


@pytest.mark.asyncio
async def test_copy_trader_sampling_accumulates_market_orderflow_before_relaxed_retry(tmp_path):
    db_path = str(tmp_path / "sampling_accumulator.db")
    db = Database(db_path)
    await db.connect()
    await db.upsert_market_aliases(
        market_id="0xMARKET-ACC",
        aliases=["0xMARKET-ACC", "0xTOKEN-ACC"],
        question="Will Team Aggregate win the championship?",
        category="SPORTS",
        volume_24h=16_000.0,
        active=True,
        source="explorer",
    )

    trader = _FakeTrader()
    copy_trader = CopyTrader(trader, _FakeScanner(), db, DecisionEngine())
    copy_trader.update_sampling_state(
        enabled=True,
        target_reached=False,
        closed_trades=0,
        target_closed_trades=20,
    )

    first = await copy_trader.evaluate_activity_event(
        {
            "type": "WHALE_EVENT",
            "token_id": "0xTOKEN-ACC",
            "side": "BUY",
            "amount": 150.0,
            "wallet": "0xagg-a",
            "wallets_count": 1,
            "source": "activity",
            "alias_candidates": ["0xMARKET-ACC", "0xTOKEN-ACC"],
        }
    )
    second = await copy_trader.evaluate_activity_event(
        {
            "type": "WHALE_EVENT",
            "token_id": "0xTOKEN-ACC",
            "side": "BUY",
            "amount": 150.0,
            "wallet": "0xagg-b",
            "wallets_count": 1,
            "source": "whale_tracker",
            "alias_candidates": ["0xMARKET-ACC", "0xTOKEN-ACC"],
        }
    )

    await db.close()

    assert first is False
    assert second is True
    assert len(trader.calls) == 1
    assert trader.calls[0]["kwargs"]["strategy_profile"] == STRATEGY_PROFILE_SAMPLING_RELAXED


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
    runtime.explorer.fetch_market_lookup_universe = fake_fetch_active_markets
    await runtime.bootstrap_market_context()

    alias_row = await runtime.db.resolve_market_alias(["0xTOKEN3"])
    await runtime.close()

    assert alias_row is not None
    assert alias_row["market_id"] == "0xmarket3"
    assert runtime.resolve_lookup_market_context(["0xMARKET3", "0xTOKEN3"]) is not None


@pytest.mark.asyncio
async def test_runtime_separates_trade_and_lookup_contexts(tmp_path):
    db_path = str(tmp_path / "runtime_lookup_context.db")
    runtime = GhostBotRuntime(
        RuntimeSettings(
            exchange_id="coinbase",
            db_path=db_path,
            debug_signal_mode=False,
            runtime_verify_once=False,
            market_limit=1,
            market_lookup_limit=3,
        )
    )
    await runtime.initialize()

    universe = [
        {
            "market_id": "0xTRADE1",
            "token_id": "0xTOKENT1",
            "token_ids": ["0xTOKENT1"],
            "alias_candidates": ["0xTRADE1", "0xTOKENT1"],
            "question": "Will Team A win?",
            "category": "SPORTS",
            "volume_24h": 100000.0,
            "active": True,
        },
        {
            "market_id": "0xLOOKUP2",
            "token_id": "0xTOKENL2",
            "token_ids": ["0xTOKENL2"],
            "alias_candidates": ["0xLOOKUP2", "0xTOKENL2"],
            "question": "Will Team B win?",
            "category": "SPORTS",
            "volume_24h": 90000.0,
            "active": True,
        },
        {
            "market_id": "0xLOOKUP3",
            "token_id": "0xTOKENL3",
            "token_ids": ["0xTOKENL3"],
            "alias_candidates": ["0xLOOKUP3", "0xTOKENL3"],
            "question": "Will Team C win?",
            "category": "SPORTS",
            "volume_24h": 80000.0,
            "active": True,
        },
    ]

    async def fake_fetch_active_markets(limit=200):
        return universe[:limit]

    async def fake_fetch_lookup_markets(limit=5000):
        return universe[:limit]

    runtime.explorer.fetch_active_markets = fake_fetch_active_markets
    runtime.explorer.fetch_market_lookup_universe = fake_fetch_lookup_markets
    await runtime.bootstrap_market_context()

    lookup_context = runtime._resolve_market_context("0xLOOKUP2", None, ["0xTOKENL2"])
    await runtime.close()

    assert len(runtime.active_market_context) == 1
    assert len(runtime.lookup_market_context) == 3
    assert lookup_context is not None
    assert lookup_context["market_id"] == "0xlookup2"


@pytest.mark.asyncio
async def test_runtime_hydrates_lookup_context_from_persisted_aliases(tmp_path):
    db_path = str(tmp_path / "runtime_persisted_hydration.db")
    runtime = GhostBotRuntime(
        RuntimeSettings(
            exchange_id="coinbase",
            db_path=db_path,
            debug_signal_mode=False,
            runtime_verify_once=False,
            market_lookup_limit=10,
        )
    )
    await runtime.initialize()
    await runtime.db.upsert_market_aliases(
        market_id="0xPERSISTED1",
        aliases=["0xPERSISTED1", "0xTOKENP1", "persisted-slug-1"],
        question="Will Team Persisted win?",
        category="SPORTS",
        volume_24h=91000.0,
        active=True,
        source="persisted_alias_replay",
    )

    async def fake_fetch_active_markets(limit=200):
        return []

    async def fake_fetch_lookup_markets(limit=5000):
        return []

    runtime.explorer.fetch_active_markets = fake_fetch_active_markets
    runtime.explorer.fetch_market_lookup_universe = fake_fetch_lookup_markets
    await runtime.bootstrap_market_context()

    lookup_context = runtime.resolve_lookup_market_context(["0xTOKENP1", "persisted-slug-1"])
    snapshot = await runtime.db.get_runtime_status_snapshot()
    await runtime.close()

    assert lookup_context is not None
    assert lookup_context["market_id"] == "0xpersisted1"
    assert len(runtime.lookup_market_context) == 1
    assert runtime.hydrated_lookup_markets == 1
    assert runtime.hydrated_lookup_aliases >= 2
    assert snapshot is not None
    assert snapshot["hydrated_lookup_markets"] == 1
    assert snapshot["hydrated_lookup_aliases"] >= 2
    assert snapshot["lookup_hydration_warning"] == "none"


@pytest.mark.asyncio
async def test_runtime_emits_lookup_hydration_warning_when_persisted_aliases_do_not_hydrate(tmp_path):
    db_path = str(tmp_path / "runtime_hydration_warning.db")
    runtime = GhostBotRuntime(
        RuntimeSettings(
            exchange_id="coinbase",
            db_path=db_path,
            debug_signal_mode=False,
            runtime_verify_once=False,
            market_lookup_limit=10,
        )
    )
    await runtime.initialize()
    await runtime.db.upsert_market_aliases(
        market_id="0xPERSISTED-WARN",
        aliases=["0xPERSISTED-WARN", "0xTOKEN-WARN"],
        question="Will Team Warning win?",
        category="SPORTS",
        volume_24h=72000.0,
        active=True,
        source="persisted_alias_replay",
    )

    async def fake_refresh_lookup_context(*args, **kwargs):
        return None

    runtime._refresh_lookup_context = fake_refresh_lookup_context  # type: ignore[method-assign]
    hydrated_count = await runtime._hydrate_lookup_context_from_db(reset=True)
    await runtime.log_runtime_status(0, 0.0)
    snapshot = await runtime.db.get_runtime_status_snapshot()
    await runtime.close()

    assert hydrated_count == 1
    assert runtime.hydrated_lookup_markets == 0
    assert runtime.hydrated_lookup_aliases == 0
    assert runtime.lookup_hydration_warning == "persisted_alias_rows_present_but_lookup_hydration_zero"
    assert snapshot is not None
    assert snapshot["lookup_hydration_warning"] == "persisted_alias_rows_present_but_lookup_hydration_zero"


@pytest.mark.asyncio
async def test_runtime_promotes_and_expires_hot_window_market(tmp_path):
    db_path = str(tmp_path / "runtime_hot_window.db")
    runtime = GhostBotRuntime(
        RuntimeSettings(
            exchange_id="coinbase",
            db_path=db_path,
            debug_signal_mode=False,
            runtime_verify_once=False,
            orderflow_hot_window_enabled=True,
            orderflow_hot_window_limit=2,
            orderflow_hot_window_ttl_seconds=120.0,
        )
    )
    await runtime.initialize()

    runtime.active_market_context = {
        "0xtrade1": {
            "market_id": "0xtrade1",
            "token_id": "0xtokent1",
            "token_ids": ["0xtokent1"],
            "alias_candidates": ["0xtrade1", "0xtokent1"],
            "question": "Will Team A win?",
            "category": "SPORTS",
            "volume_24h": 100000.0,
            "active": True,
            "trade_context_source": "active_context",
        }
    }
    runtime._publish_trade_market_contexts()

    promoted = await runtime.promote_hot_window_market(
        context={
            "market_id": "0xlookup2",
            "token_id": "0xtokenl2",
            "token_ids": ["0xtokenl2"],
            "alias_candidates": ["0xlookup2", "0xtokenl2"],
            "question": "Will Team B win?",
            "category": "SPORTS",
            "volume_24h": 90000.0,
            "active": True,
        },
        event={
            "type": "WHALE_EVENT",
            "amount": 5000.0,
            "wallet": "0xwhale",
            "wallets_count": 1,
        },
        source="whale_tracker",
        stage="lazy_lookup",
    )

    trade_universe = runtime._build_trade_market_universe(list(runtime.active_market_context.values()))

    assert promoted is True
    assert "0xlookup2" in runtime.hot_window_market_context
    assert len(trade_universe) == 2
    assert runtime.hot_window_promotions == 1
    alias_row = await runtime.db.resolve_market_alias(["0xtokenl2"])

    runtime.hot_window_expiries["0xlookup2"] = 0.0
    await runtime._prune_hot_window()
    await runtime.close()

    assert "0xlookup2" not in runtime.hot_window_market_context
    assert alias_row is not None


@pytest.mark.asyncio
async def test_runtime_sampling_extends_hot_window_ttl(tmp_path):
    db_path = str(tmp_path / "runtime_sampling_hot_window.db")
    runtime = GhostBotRuntime(
        RuntimeSettings(
            exchange_id="coinbase",
            db_path=db_path,
            debug_signal_mode=False,
            runtime_verify_once=False,
            paper_sampling_mode=True,
            orderflow_hot_window_enabled=True,
            orderflow_hot_window_limit=2,
            orderflow_hot_window_ttl_seconds=120.0,
        )
    )
    await runtime.initialize()

    started = time.time()
    promoted = await runtime.promote_hot_window_market(
        context={
            "market_id": "0xlookup-sampling",
            "token_id": "0xtoken-sampling",
            "token_ids": ["0xtoken-sampling"],
            "alias_candidates": ["0xlookup-sampling", "0xtoken-sampling"],
            "question": "Will Team Sample win?",
            "category": "SPORTS",
            "volume_24h": 90000.0,
            "active": True,
        },
        event={
            "type": "WHALE_EVENT",
            "amount": 5000.0,
            "wallet": "0xwhale",
            "wallets_count": 2,
        },
        source="activity",
        stage="lazy_lookup",
    )
    expiry = runtime.hot_window_expiries["0xlookup-sampling"]
    await runtime.close()

    assert promoted is True
    assert expiry > started
    assert expiry - started >= 1700.0


@pytest.mark.asyncio
async def test_runtime_sampling_state_disables_after_target_closed_trades(tmp_path):
    db_path = str(tmp_path / "runtime_sampling_state.db")
    runtime = GhostBotRuntime(
        RuntimeSettings(
            exchange_id="coinbase",
            db_path=db_path,
            debug_signal_mode=False,
            runtime_verify_once=False,
            paper_sampling_mode=True,
            paper_sampling_target_closed_trades=1,
        )
    )
    await runtime.initialize()

    trade_id = await runtime.db.add_trade(
        "SAMPLE-MARKET",
        "YES",
        35.0,
        0.55,
        0.0,
        0.69,
        venue="polymarket",
        source_signal="whale_tracker",
        category="POLITICS",
        sample_kind="live_paper",
        strategy_profile=STRATEGY_PROFILE_SAMPLING_RELAXED,
    )
    await runtime.db.update_trade_resolution(trade_id, "CLOSED_WIN", 7.5)

    await runtime.refresh_sampling_state()
    await runtime.close()

    assert runtime.sampling_closed_trades == 1
    assert runtime.sampling_enabled is False
    assert runtime.sampling_stop_reason == "target_reached"
