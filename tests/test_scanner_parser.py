import os
from unittest.mock import MagicMock

import pytest

from src.scanner import MarketScanner


class OrderSummary:
    def __init__(self, price):
        self.price = price


def test_extract_price_dict():
    scanner = MarketScanner(debug_signal_mode=True)
    assert scanner._extract_price({"price": "0.5"}) == 0.5


def test_extract_price_object():
    scanner = MarketScanner(debug_signal_mode=True)
    assert scanner._extract_price(OrderSummary("0.8")) == 0.8


@pytest.mark.asyncio
async def test_get_orderbook_snapshot_mixed_formats(monkeypatch):
    scanner = MarketScanner(debug_signal_mode=False)
    mock_get_order_book = MagicMock()
    monkeypatch.setattr(scanner.polymarket, "get_order_book", mock_get_order_book)

    class MockOrderbook:
        def __init__(self, bids, asks):
            self.bids = bids
            self.asks = asks

    mock_get_order_book.return_value = MockOrderbook(
        bids=[OrderSummary(0.50)],
        asks=[OrderSummary(0.52)],
    )
    snapshot = await scanner.get_orderbook_snapshot("test_token_1")
    assert snapshot["is_valid"] is True
    assert snapshot["mid_price"] == 0.51

    mock_get_order_book.return_value = {
        "bids": [{"price": "0.60"}],
        "asks": [{"price": "0.62"}],
    }
    snapshot = await scanner.get_orderbook_snapshot("test_token_2")
    assert snapshot["is_valid"] is True
    assert snapshot["mid_price"] == 0.61

    await scanner.close()


@pytest.mark.asyncio
async def test_get_orderbook_snapshot_invalid_data(monkeypatch):
    scanner = MarketScanner(debug_signal_mode=False)
    mock_get_order_book = MagicMock()
    monkeypatch.setattr(scanner.polymarket, "get_order_book", mock_get_order_book)

    mock_get_order_book.return_value = {"bids": [], "asks": [{"price": 0.5}]}
    snapshot = await scanner.get_orderbook_snapshot("test_token_empty")
    assert snapshot["is_valid"] is False
    assert snapshot["reason"] == "invalid_orderbook_data"

    await scanner.close()
