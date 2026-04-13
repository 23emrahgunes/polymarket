import os
from unittest.mock import AsyncMock, MagicMock

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


@pytest.mark.asyncio
async def test_get_futures_market_snapshot_prefers_trusted_ticker_book(monkeypatch):
    scanner = MarketScanner(debug_signal_mode=False)
    monkeypatch.setattr(
        scanner.futures_exchange,
        "fetch_ticker",
        AsyncMock(
            return_value={
                "bid": 100.0,
                "ask": 100.2,
                "last": 100.1,
                "quoteVolume": 2_500_000,
                "info": {"markPrice": "100.12"},
            }
        ),
    )
    monkeypatch.setattr(scanner.futures_exchange, "fetch_funding_rate", AsyncMock(return_value={"fundingRate": 0.0001}))
    monkeypatch.setattr(scanner.futures_exchange, "fetch_open_interest", AsyncMock(return_value={"openInterest": 5000}))
    monkeypatch.setattr(scanner.futures_exchange, "fetch_order_book", AsyncMock(return_value={"bids": [], "asks": []}))

    snapshot = await scanner.get_futures_market_snapshot("BTC/USDT:USDT")

    assert snapshot["is_valid"] is True
    assert snapshot["snapshot_quality"] == "trusted_ticker_book"
    assert snapshot["spread_source"] == "ticker_book"
    assert snapshot["orderbook_fallback_used"] is False
    assert snapshot["best_bid"] == 100.0
    assert snapshot["best_ask"] == 100.2

    await scanner.close()


@pytest.mark.asyncio
async def test_get_futures_market_snapshot_uses_info_book_when_ticker_bid_ask_missing(monkeypatch):
    scanner = MarketScanner(debug_signal_mode=False)
    monkeypatch.setattr(
        scanner.futures_exchange,
        "fetch_ticker",
        AsyncMock(
            return_value={
                "bid": None,
                "ask": None,
                "last": 200.1,
                "quoteVolume": 1_500_000,
                "info": {"markPrice": "200.05", "bidPrice": "200.0", "askPrice": "200.2"},
            }
        ),
    )
    monkeypatch.setattr(scanner.futures_exchange, "fetch_funding_rate", AsyncMock(return_value={"fundingRate": 0.0001}))
    monkeypatch.setattr(scanner.futures_exchange, "fetch_open_interest", AsyncMock(return_value={"openInterest": 7000}))
    monkeypatch.setattr(scanner.futures_exchange, "fetch_order_book", AsyncMock(return_value={"bids": [], "asks": []}))

    snapshot = await scanner.get_futures_market_snapshot("ETH/USDT:USDT")

    assert snapshot["is_valid"] is True
    assert snapshot["snapshot_quality"] == "trusted_info_book"
    assert snapshot["bid_source"] == "info.bidPrice"
    assert snapshot["ask_source"] == "info.askPrice"
    assert snapshot["orderbook_fallback_used"] is False

    await scanner.close()


@pytest.mark.asyncio
async def test_get_futures_market_snapshot_falls_back_to_orderbook_when_bid_ask_missing(monkeypatch):
    scanner = MarketScanner(debug_signal_mode=False)
    monkeypatch.setattr(
        scanner.futures_exchange,
        "fetch_ticker",
        AsyncMock(
            return_value={
                "bid": None,
                "ask": None,
                "last": 300.0,
                "quoteVolume": 3_000_000,
                "info": {"markPrice": "300.1"},
            }
        ),
    )
    monkeypatch.setattr(scanner.futures_exchange, "fetch_funding_rate", AsyncMock(return_value={"fundingRate": 0.0002}))
    monkeypatch.setattr(scanner.futures_exchange, "fetch_open_interest", AsyncMock(return_value={"openInterest": 9000}))
    monkeypatch.setattr(
        scanner.futures_exchange,
        "fetch_order_book",
        AsyncMock(return_value={"bids": [[299.9, 10]], "asks": [[300.1, 8]]}),
    )

    snapshot = await scanner.get_futures_market_snapshot("SOL/USDT:USDT")

    assert snapshot["is_valid"] is True
    assert snapshot["snapshot_quality"] == "trusted_orderbook_book"
    assert snapshot["orderbook_fallback_used"] is True
    assert snapshot["orderbook_repriced"] is False
    assert snapshot["spread_source"] == "orderbook"

    await scanner.close()


@pytest.mark.asyncio
async def test_get_futures_market_snapshot_reprices_wide_ticker_spread_with_orderbook(monkeypatch):
    scanner = MarketScanner(debug_signal_mode=False)
    monkeypatch.setattr(
        scanner.futures_exchange,
        "fetch_ticker",
        AsyncMock(
            return_value={
                "bid": 100.0,
                "ask": 104.0,
                "last": 102.0,
                "quoteVolume": 4_000_000,
                "info": {"markPrice": "102.1"},
            }
        ),
    )
    monkeypatch.setattr(scanner.futures_exchange, "fetch_funding_rate", AsyncMock(return_value={"fundingRate": 0.0001}))
    monkeypatch.setattr(scanner.futures_exchange, "fetch_open_interest", AsyncMock(return_value={"openInterest": 12000}))
    monkeypatch.setattr(
        scanner.futures_exchange,
        "fetch_order_book",
        AsyncMock(return_value={"bids": [[101.9, 10]], "asks": [[102.1, 8]]}),
    )

    snapshot = await scanner.get_futures_market_snapshot("BTC/USDT:USDT")

    assert snapshot["is_valid"] is True
    assert snapshot["orderbook_fallback_used"] is True
    assert snapshot["orderbook_repriced"] is True
    assert snapshot["snapshot_quality"] == "trusted_orderbook_book"
    assert snapshot["spread_pct"] < 0.018

    await scanner.close()


@pytest.mark.asyncio
async def test_get_futures_market_snapshot_returns_missing_bid_ask_when_no_trusted_book(monkeypatch):
    scanner = MarketScanner(debug_signal_mode=False)
    monkeypatch.setattr(
        scanner.futures_exchange,
        "fetch_ticker",
        AsyncMock(
            return_value={
                "bid": None,
                "ask": None,
                "last": 250.0,
                "quoteVolume": 1_100_000,
                "info": {"markPrice": "250.0"},
            }
        ),
    )
    monkeypatch.setattr(scanner.futures_exchange, "fetch_funding_rate", AsyncMock(return_value={"fundingRate": 0.0001}))
    monkeypatch.setattr(scanner.futures_exchange, "fetch_open_interest", AsyncMock(return_value={"openInterest": 8000}))
    monkeypatch.setattr(scanner.futures_exchange, "fetch_order_book", AsyncMock(return_value={"bids": [], "asks": []}))

    snapshot = await scanner.get_futures_market_snapshot("ETH/USDT:USDT")

    assert snapshot["is_valid"] is False
    assert snapshot["reason"] == "futures_bid_ask_missing"

    await scanner.close()


@pytest.mark.asyncio
async def test_get_futures_market_snapshot_returns_untrusted_for_crossed_book(monkeypatch):
    scanner = MarketScanner(debug_signal_mode=False)
    monkeypatch.setattr(
        scanner.futures_exchange,
        "fetch_ticker",
        AsyncMock(
            return_value={
                "bid": 105.0,
                "ask": 104.0,
                "last": 104.5,
                "quoteVolume": 1_200_000,
                "info": {"markPrice": "104.6"},
            }
        ),
    )
    monkeypatch.setattr(scanner.futures_exchange, "fetch_funding_rate", AsyncMock(return_value={"fundingRate": 0.0001}))
    monkeypatch.setattr(scanner.futures_exchange, "fetch_open_interest", AsyncMock(return_value={"openInterest": 9000}))
    monkeypatch.setattr(
        scanner.futures_exchange,
        "fetch_order_book",
        AsyncMock(return_value={"bids": [[103.0, 5]], "asks": [[102.5, 5]]}),
    )

    snapshot = await scanner.get_futures_market_snapshot("BTC/USDT:USDT")

    assert snapshot["is_valid"] is False
    assert snapshot["reason"] == "futures_snapshot_untrusted"

    await scanner.close()
