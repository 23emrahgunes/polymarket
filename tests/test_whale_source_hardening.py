import logging

import pytest

from src.database import Database
from src.gamma_client import GammaFetchResult
from src.runtime import GhostBotRuntime, RuntimeSettings
from src.scrapers.activity import ActivityHunter
from src.whale_tracker import WhaleTracker


class FakeGammaClient:
    def __init__(self, leaderboard_result=None, activity_result=None, wallet_activity_map=None):
        self.leaderboard_result = leaderboard_result or GammaFetchResult(data=[])
        self.activity_result = activity_result or GammaFetchResult(data=[])
        self.wallet_activity_map = wallet_activity_map or {}

    async def fetch_leaderboard(self, limit: int = 20):
        return self.leaderboard_result

    async def fetch_global_activity(self, limit: int = 50):
        return self.activity_result

    async def fetch_wallet_activity(self, address: str, limit: int = 5):
        return self.wallet_activity_map.get(address, GammaFetchResult(data=[]))


@pytest.mark.asyncio
async def test_activity_discovery_persists_wallets_and_reload_survives_restart(tmp_path):
    db_path = str(tmp_path / "whale_discovery_restart.db")
    db = Database(db_path)
    await db.connect()

    hunter = ActivityHunter(db=db)
    await hunter.record_discovery_candidates(
        [
            {
                "id": "a1",
                "address": "0xDISCOVERYA",
                "price": 0.5,
                "size": 6000,
                "question": "Will Team A win the championship?",
            },
            {
                "id": "a2",
                "address": "0xDISCOVERYA",
                "price": 0.6,
                "size": 5000,
                "question": "Will Team A win the championship?",
            },
        ]
    )
    await db.close()

    reloaded_db = Database(db_path)
    await reloaded_db.connect()
    tracker = WhaleTracker(
        polymarket_client=object(),
        db=reloaded_db,
        gamma_client=FakeGammaClient(
            leaderboard_result=GammaFetchResult(data=[], reason="leaderboard_unavailable", timed_out=True)
        ),
        target_wallet_count=10,
    )
    whales = await tracker.fetch_top_whales(limit=10)
    counts = await reloaded_db.get_whale_wallet_counts()

    assert "0xDISCOVERYA" in whales
    assert counts["activity_discovered_wallets"] >= 1
    assert tracker.source_mode in {"cache_only", "hybrid_cache", "persisted_cache"}

    await reloaded_db.close()


@pytest.mark.asyncio
async def test_whale_tracker_uses_persisted_cache_when_leaderboard_unavailable(tmp_path):
    db_path = str(tmp_path / "whale_cache_fallback.db")
    db = Database(db_path)
    await db.connect()

    for index in range(55):
        wallet = f"0xACT{index:036d}"
        await db.upsert_whale_wallet(wallet, "activity_discovery", event_amount=12000.0, event_category="CRYPTO")

    tracker = WhaleTracker(
        polymarket_client=object(),
        db=db,
        gamma_client=FakeGammaClient(
            leaderboard_result=GammaFetchResult(data=[], reason="leaderboard_unavailable", timed_out=True)
        ),
        target_wallet_count=50,
    )

    whales = await tracker.fetch_top_whales(limit=50)
    assert len(whales) == 50
    assert tracker.leaderboard_wallets_count == 0
    assert tracker.activity_discovered_wallets_count >= 50
    assert tracker.persisted_wallets_count >= 50
    assert tracker.source_mode in {"cache_only", "persisted_cache"}

    await db.close()


@pytest.mark.asyncio
async def test_wallet_activity_timeout_does_not_break_poll_cycle(tmp_path):
    db_path = str(tmp_path / "whale_timeout_cycle.db")
    db = Database(db_path)
    await db.connect()

    wallet = "0xTIMEOUT0000000000000000000000000000001"
    await db.upsert_whale_wallet(wallet, "activity_discovery", event_amount=12000.0, event_category="CRYPTO")

    tracker = WhaleTracker(
        polymarket_client=object(),
        db=db,
        gamma_client=FakeGammaClient(
            leaderboard_result=GammaFetchResult(data=[], reason="leaderboard_unavailable"),
            wallet_activity_map={
                wallet: GammaFetchResult(data=[], reason="wallet_activity_timeout", timed_out=True)
            },
        ),
        target_wallet_count=5,
    )

    await tracker.fetch_top_whales(limit=5)
    actions = await tracker.poll_whales_once()
    wallet_row = await db.get_whale_wallet(wallet)

    assert actions == []
    assert tracker.wallet_timeouts_last_cycle == 1
    assert int(wallet_row["failure_streak"]) >= 1

    await db.close()


@pytest.mark.asyncio
async def test_whale_tracker_merges_manual_and_static_seed_when_cache_empty(tmp_path, monkeypatch):
    db_path = str(tmp_path / "whale_seed_mode.db")
    db = Database(db_path)
    await db.connect()
    manual_wallets = [
        "0xMANUAL000000000000000000000000000000001",
        "0xMANUAL000000000000000000000000000000002",
    ]
    monkeypatch.setenv("WHALE_LIST", ",".join(manual_wallets))

    tracker = WhaleTracker(
        polymarket_client=object(),
        db=db,
        gamma_client=FakeGammaClient(
            leaderboard_result=GammaFetchResult(data=[], reason="leaderboard_unavailable", timed_out=True)
        ),
        target_wallet_count=25,
    )

    whales = await tracker.fetch_top_whales(limit=25)

    assert tracker.source_mode == "seed_only_mode"
    assert all(wallet in whales for wallet in manual_wallets)
    assert len(whales) >= len(manual_wallets) + 10

    await db.close()


@pytest.mark.asyncio
async def test_cache_empty_logs_whale_source_unavailable(tmp_path, caplog, monkeypatch):
    db_path = str(tmp_path / "whale_empty_cache.db")
    db = Database(db_path)
    await db.connect()
    monkeypatch.delenv("WHALE_LIST", raising=False)

    tracker = WhaleTracker(
        polymarket_client=object(),
        db=db,
        gamma_client=FakeGammaClient(
            leaderboard_result=GammaFetchResult(data=[], reason="leaderboard_unavailable", timed_out=True)
        ),
        target_wallet_count=10,
    )
    tracker._deterministic_fallback_wallets = lambda: []

    with caplog.at_level(logging.INFO):
        whales = await tracker.fetch_top_whales(limit=10)

    assert whales == []
    assert "whale_source_unavailable" in caplog.text
    assert "cache_empty" in caplog.text

    await db.close()


@pytest.mark.asyncio
async def test_runtime_status_includes_whale_source_breakdown(tmp_path, caplog):
    db_path = str(tmp_path / "runtime_status_breakdown.db")
    runtime = GhostBotRuntime(
        RuntimeSettings(
            exchange_id="coinbase",
            db_path=db_path,
            debug_signal_mode=False,
            runtime_verify_once=False,
        )
    )
    await runtime.initialize()

    runtime.whale_tracker.top_whales = ["0x1", "0x2", "0x3"]
    runtime.whale_tracker.leaderboard_wallets_count = 12
    runtime.whale_tracker.activity_discovered_wallets_count = 27
    runtime.whale_tracker.persisted_wallets_count = 44
    runtime.whale_tracker.wallet_timeouts_last_cycle = 2
    runtime.whale_tracker.source_mode = "hybrid_cache"

    with caplog.at_level(logging.INFO):
        await runtime.log_runtime_status(active_markets_count=200, cycle_duration=0.42)

    await runtime.close()

    assert "tracked_whales=3" in caplog.text
    assert "leaderboard_wallets=12" in caplog.text
    assert "activity_discovered_wallets=27" in caplog.text
    assert "persisted_wallets=44" in caplog.text
    assert "wallet_timeouts_last_cycle=2" in caplog.text
    assert "source_mode=hybrid_cache" in caplog.text
