import pytest
import asyncio
import time
import os
import sys

# Handle path
ABS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(ABS_ROOT)

from src.scrapers.activity import ActivityHunter

@pytest.mark.asyncio
async def test_activity_whale_event():
    hunter = ActivityHunter()

    # Mock some activity data
    mock_activities = [
        {
            "id": "tx1",
            "market_id": "M1",
            "side": "buy",
            "size": "10000",
            "price": "0.5",
            "address": "0xWhale"
        }
    ]

    # Manually inject data into detection logic
    # We'll use a wrapper or mock fetch_latest_activity
    async def mock_fetch(*args, **kwargs):
        return mock_activities

    hunter.fetch_latest_activity = mock_fetch

    events = []
    async for event in hunter.monitor_stream():
        events.append(event)
        break # Just get one

    assert len(events) > 0
    assert events[0]["type"] == "WHALE_EVENT"
    assert events[0]["amount"] == 5000.0 # 10000 * 0.5

@pytest.mark.asyncio
async def test_activity_cluster_detection():
    hunter = ActivityHunter()

    # Mock 3 different wallets on same market in 120s
    mock_activities = [
        {"id": "c1", "market_id": "M2", "side": "buy", "size": "10", "price": "0.5", "address": "0x1"},
        {"id": "c2", "market_id": "M2", "side": "buy", "size": "10", "price": "0.5", "address": "0x2"},
        {"id": "c3", "market_id": "M2", "side": "buy", "size": "10", "price": "0.5", "address": "0x3"}
    ]

    async def mock_fetch(*args, **kwargs):
        return mock_activities

    hunter.fetch_latest_activity = mock_fetch

    events = []
    # Note: monitor_stream might yield both WHALE and CLUSTER if size is large.
    # Here size is small, so only CLUSTER should trigger eventually.
    async for event in hunter.monitor_stream():
        if event["type"] == "CLUSTER_DETECTED":
            events.append(event)
            break

    assert len(events) > 0
    assert events[0]["wallets_count"] == 3
    assert events[0]["market_id"] == "M2"
