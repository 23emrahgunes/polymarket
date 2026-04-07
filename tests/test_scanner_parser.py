import pytest
from src.scanner import MarketScanner
from unittest.mock import MagicMock

class OrderSummary:
    def __init__(self, price):
        self.price = price

def test_extract_price_dict():
    scanner = MarketScanner()
    entry = {"price": "0.5"}
    assert scanner._extract_price(entry) == 0.5

    entry_float = {"price": 0.6}
    assert scanner._extract_price(entry_float) == 0.6

def test_extract_price_object():
    scanner = MarketScanner()
    entry = OrderSummary(0.7)
    assert scanner._extract_price(entry) == 0.7

    entry_str = OrderSummary("0.8")
    assert scanner._extract_price(entry_str) == 0.8

def test_extract_price_malformed():
    scanner = MarketScanner()
    # Missing price
    assert scanner._extract_price({}) == 0
    assert scanner._extract_price(OrderSummary(None)) == 0

    # Invalid type
    assert scanner._extract_price(None) == 0
    assert scanner._extract_price("not a dict") == 0

@pytest.mark.asyncio
async def test_get_token_price_mixed_formats(monkeypatch):
    scanner = MarketScanner()

    # Mock polymarket.get_order_book
    mock_get_order_book = MagicMock()
    monkeypatch.setattr(scanner.polymarket, "get_order_book", mock_get_order_book)

    # Test Object-style orderbook
    class MockOrderbook:
        def __init__(self, bids, asks):
            self.bids = bids
            self.asks = asks

    mock_get_order_book.return_value = MockOrderbook(
        bids=[OrderSummary(0.50)],
        asks=[OrderSummary(0.52)]
    )

    price = await scanner.get_token_price("test_token_1")
    assert price == 0.51

    # Test Dict-style orderbook
    mock_get_order_book.return_value = {
        "bids": [{"price": "0.60"}],
        "asks": [{"price": "0.62"}]
    }
    # Clear cache or use different token_id
    price = await scanner.get_token_price("test_token_2")
    assert price == 0.61

@pytest.mark.asyncio
async def test_get_token_price_empty_or_invalid(monkeypatch):
    scanner = MarketScanner()
    mock_get_order_book = MagicMock()
    monkeypatch.setattr(scanner.polymarket, "get_order_book", mock_get_order_book)

    # Empty bids
    mock_get_order_book.return_value = {"bids": [], "asks": [{"price": 0.5}]}
    assert await scanner.get_token_price("test_token_empty") is None

    # Non-positive prices
    mock_get_order_book.return_value = {
        "bids": [{"price": 0}],
        "asks": [{"price": 0.5}]
    }
    assert await scanner.get_token_price("test_token_zero") is None
