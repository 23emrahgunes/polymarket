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
