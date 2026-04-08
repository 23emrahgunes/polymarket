from __future__ import annotations

import pytest

from src.gamma_client import GammaApiClient


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


@pytest.mark.asyncio
async def test_gamma_client_uses_data_api_endpoints(monkeypatch):
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append((url, params, timeout))
        return _FakeResponse([])

    monkeypatch.setattr("src.gamma_client.requests.get", fake_get)
    client = GammaApiClient()

    await client.fetch_global_activity(limit=25)
    await client.fetch_wallet_activity("0x56687bf447db6ffa42ffe2204a05edaa20f55839", limit=5)
    await client.fetch_leaderboard(limit=10)

    assert calls[0][0] == "https://data-api.polymarket.com/trades"
    assert calls[0][1] == {"limit": 25}
    assert calls[1][0] == "https://data-api.polymarket.com/activity"
    assert calls[1][1] == {"user": "0x56687bf447db6ffa42ffe2204a05edaa20f55839", "limit": 5}
    assert calls[2][0] == "https://data-api.polymarket.com/v1/leaderboard"
    assert calls[2][1] == {"limit": 10}
