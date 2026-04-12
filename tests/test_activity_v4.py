import asyncio

import pytest

from src.scrapers.activity import ActivityHunter


def test_activity_whale_event_normalization():
    hunter = ActivityHunter()
    events = hunter._normalize_activities(
        [
            {
                "id": "tx1",
                "market_id": "TOKEN_1",
                "side": "buy",
                "size": "10000",
                "price": "0.5",
                "address": "0xWhale",
            }
        ]
    )

    whale_events = [event for event in events if event["type"] == "WHALE_EVENT"]
    assert len(whale_events) == 1
    assert whale_events[0]["amount"] == 5000.0
    assert whale_events[0]["token_id"] == "TOKEN_1"


def test_activity_normalizes_data_api_trade_payload():
    hunter = ActivityHunter()
    events = hunter._normalize_activities(
        [
            {
                "transactionHash": "0xTX1",
                "conditionId": "0xMARKET1",
                "asset": "0xTOKEN1",
                "side": "BUY",
                "size": 2500,
                "usdcSize": 2500,
                "price": 0.51,
                "proxyWallet": "0xWhale",
                "title": "Will Team A win the championship?",
            }
        ]
    )

    whale_events = [event for event in events if event["type"] == "WHALE_EVENT"]
    assert len(whale_events) == 1
    assert whale_events[0]["market_id"] == "0xMARKET1"
    assert whale_events[0]["token_id"] == "0xTOKEN1"
    assert whale_events[0]["amount"] == 2500.0
    assert "0xmarket1" in whale_events[0]["alias_candidates"]
    assert "0xtoken1" in whale_events[0]["alias_candidates"]


def test_activity_cluster_detection_emits_wallet_counts():
    hunter = ActivityHunter()
    events = hunter._normalize_activities(
        [
            {"id": "c1", "market_id": "TOKEN_2", "side": "buy", "size": "10", "price": "0.5", "address": "0x1"},
            {"id": "c2", "market_id": "TOKEN_2", "side": "buy", "size": "10", "price": "0.5", "address": "0x2"},
            {"id": "c3", "market_id": "TOKEN_2", "side": "buy", "size": "10", "price": "0.5", "address": "0x3"},
        ]
    )

    cluster_events = [event for event in events if event["type"] == "CLUSTER_DETECTED"]
    assert cluster_events
    assert cluster_events[-1]["wallets_count"] == 3


@pytest.mark.asyncio
async def test_activity_hunter_has_no_synthetic_events_in_normal_mode():
    hunter = ActivityHunter(debug_signal_mode=False)
    assert hunter.debug_signal_mode is False
    assert hunter.debug_event_emitted is False
    assert hunter._normalize_activities([]) == []


@pytest.mark.asyncio
async def test_record_discovery_candidates_accepts_data_api_trade_payload(tmp_path):
    from src.database import Database

    db_path = str(tmp_path / "activity_discovery_data_api.db")
    db = Database(db_path)
    await db.connect()

    hunter = ActivityHunter(db=db)
    await hunter.record_discovery_candidates(
        [
            {
                "transactionHash": "0xTX2",
                "proxyWallet": "0xDISCOVERYA",
                "price": 0.4,
                "size": 3000,
                "usdcSize": 3000,
                "title": "Will BTC be above $95,000 on December 31, 2026?",
            }
        ]
    )

    wallet = await db.get_whale_wallet("0xDISCOVERYA")
    await db.close()

    assert wallet is not None
    assert wallet["source_type"] == "activity_discovery"


@pytest.mark.asyncio
async def test_record_discovery_candidates_promotes_graph_clusters_below_activity_threshold(tmp_path):
    from src.database import Database

    db_path = str(tmp_path / "graph_discovery_low_threshold.db")
    db = Database(db_path)
    await db.connect()

    hunter = ActivityHunter(db=db)
    await hunter.record_discovery_candidates(
        [
            {
                "transactionHash": "0xTX3",
                "proxyWallet": "0xGRAPH1",
                "price": 0.5,
                "size": 2000,
                "usdcSize": 1000,
                "conditionId": "0xMARKET-GRAPH",
                "asset": "0xTOKEN-GRAPH",
                "side": "BUY",
                "title": "Will Team Graph win?",
            },
            {
                "transactionHash": "0xTX4",
                "proxyWallet": "0xGRAPH2",
                "price": 0.5,
                "size": 2000,
                "usdcSize": 1000,
                "conditionId": "0xMARKET-GRAPH",
                "asset": "0xTOKEN-GRAPH",
                "side": "BUY",
                "title": "Will Team Graph win?",
            },
        ]
    )

    counts = await db.get_whale_wallet_counts()
    ranked = await db.get_ranked_whale_wallets(limit=10, source_type="graph_discovery", min_event_count_24h=1)
    activity_wallet = await db.get_whale_wallet("0xgraph1")
    graph_summary = hunter.get_graph_discovery_summary()
    await db.close()

    assert counts["graph_discovered_wallets"] == 2
    assert counts["activity_discovered_wallets"] == 0
    assert {row["address"] for row in ranked} == {"0xgraph1", "0xgraph2"}
    assert activity_wallet is not None
    assert activity_wallet["source_type"] == "graph_discovery"
    assert graph_summary["graph_clusters_promoted"] == 1
    assert graph_summary["graph_skipped_missing_market_ref"] == 0
    assert graph_summary["graph_skipped_single_wallet"] == 0
    assert graph_summary["graph_skipped_low_notional"] == 0


@pytest.mark.asyncio
async def test_record_discovery_candidates_tracks_graph_skip_reasons(tmp_path):
    from src.database import Database

    db_path = str(tmp_path / "graph_discovery_skip_reasons.db")
    db = Database(db_path)
    await db.connect()

    hunter = ActivityHunter(db=db)
    await hunter.record_discovery_candidates(
        [
            {
                "transactionHash": "0xTX5",
                "proxyWallet": "0xGRAPHMISS",
                "price": 0.5,
                "size": 2000,
                "usdcSize": 1000,
                "side": "BUY",
                "title": "Missing market ref",
            },
            {
                "transactionHash": "0xTX6",
                "proxyWallet": "0xGRAPHSOLO",
                "price": 0.5,
                "size": 2000,
                "usdcSize": 1000,
                "conditionId": "0xMARKET-SOLO",
                "asset": "0xTOKEN-SOLO",
                "side": "BUY",
                "title": "Single wallet only",
            },
            {
                "transactionHash": "0xTX7",
                "proxyWallet": "0xGRAPHLOW1",
                "price": 0.5,
                "size": 1500,
                "usdcSize": 750,
                "conditionId": "0xMARKET-LOW",
                "asset": "0xTOKEN-LOW",
                "side": "BUY",
                "title": "Low notional graph",
            },
            {
                "transactionHash": "0xTX8",
                "proxyWallet": "0xGRAPHLOW2",
                "price": 0.5,
                "size": 1500,
                "usdcSize": 750,
                "conditionId": "0xMARKET-LOW",
                "asset": "0xTOKEN-LOW",
                "side": "BUY",
                "title": "Low notional graph",
            },
        ]
    )

    graph_summary = hunter.get_graph_discovery_summary()
    await db.close()

    assert graph_summary["graph_clusters_promoted"] == 0
    assert graph_summary["graph_skipped_missing_market_ref"] == 1
    assert graph_summary["graph_skipped_single_wallet"] == 1
    assert graph_summary["graph_skipped_low_notional"] == 0
