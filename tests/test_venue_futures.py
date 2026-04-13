import os

import pytest

from src.database import Database
from src.venue_config import VenueConfig
from src.venues import BinanceFuturesPaperVenue


class DummyScanner:
    def __init__(self, prices):
        self.prices = prices

    async def get_futures_market_snapshot(self, symbol: str):
        data = self.prices[symbol]
        return {
            "symbol": symbol,
            "last_price": data["price"],
            "mark_price": data["price"],
            "best_bid": data["price"] - 1,
            "best_ask": data["price"] + 1,
            "spread_pct": data.get("spread_pct", 0.0002),
            "volume_24h": 200000.0,
            "open_interest": 1800000.0,
            "funding_rate": 0.0002,
            "is_valid": True,
            "reason": "test",
            "fetched_at": 0.0,
        }


@pytest.mark.asyncio
async def test_binance_futures_paper_entry_and_take_profit_sync():
    db_path = "tests/test_binance_futures_paper.db"
    if os.path.exists(db_path):
        os.remove(db_path)

    db = Database(db_path)
    await db.connect()
    config = VenueConfig(
        venue_id="binance_futures",
        enabled=True,
        mode="paper",
        instrument_type="futures",
        max_order_usd=100.0,
        max_position_usd=250.0,
        max_daily_loss_usd=150.0,
        max_open_positions=3,
        fee_bps=4.0,
        slippage_limit_bps=35.0,
        signal_threshold=0.70,
        leverage=2,
        margin_mode="isolated",
        stop_loss_pct=0.02,
        take_profit_pct=0.03,
    )
    scanner = DummyScanner({"BTC/USDT:USDT": {"price": 100000.0}})
    venue = BinanceFuturesPaperVenue(config, db, scanner)

    success, _ = await venue.place_entry_order(
        symbol="BTC/USDT:USDT",
        side="LONG",
        entry_price=100000.0,
        trade_size=100.0,
        signal_score=0.9,
        source="binance_futures_price_structure",
        source_signal="binance_futures_price_structure",
        spread_pct=0.0002,
        market_context={"market_id": "pm-btc-100k"},
    )

    assert success is True
    assert await db.get_balance("binance_futures", "paper") == 950.0
    assert len(await db.get_open_positions(venue="binance_futures")) == 1
    assert len(await db.get_open_venue_orders("binance_futures", "BTC/USDT:USDT")) == 2

    scanner.prices["BTC/USDT:USDT"]["price"] = 103100.0
    await venue.sync_account_state(scanner)

    assert len(await db.get_open_positions(venue="binance_futures")) == 0
    realized, unrealized = await db.get_venue_performance("binance_futures")
    assert realized > 0
    assert unrealized == 0.0

    await db.close()
    os.remove(db_path)


@pytest.mark.asyncio
async def test_binance_futures_rejects_invalid_leverage_config():
    db_path = "tests/test_binance_futures_invalid_config.db"
    if os.path.exists(db_path):
        os.remove(db_path)

    db = Database(db_path)
    await db.connect()
    config = VenueConfig(
        venue_id="binance_futures",
        enabled=True,
        mode="paper",
        instrument_type="futures",
        max_order_usd=100.0,
        max_position_usd=250.0,
        max_daily_loss_usd=150.0,
        max_open_positions=3,
        fee_bps=4.0,
        slippage_limit_bps=35.0,
        signal_threshold=0.70,
        leverage=3,
        margin_mode="isolated",
        stop_loss_pct=0.02,
        take_profit_pct=0.03,
    )
    scanner = DummyScanner({"BTC/USDT:USDT": {"price": 100000.0}})
    venue = BinanceFuturesPaperVenue(config, db, scanner)

    success, reason = await venue.place_entry_order(
        symbol="BTC/USDT:USDT",
        side="LONG",
        entry_price=100000.0,
        trade_size=100.0,
        signal_score=0.9,
        source="binance_futures_price_structure",
        source_signal="binance_futures_price_structure",
        spread_pct=0.0002,
        market_context={"market_id": "pm-btc-100k"},
    )

    assert success is False
    assert reason == "leverage_config_invalid"

    await db.close()
    os.remove(db_path)


@pytest.mark.asyncio
async def test_binance_futures_signal_exit_closes_position():
    db_path = "tests/test_binance_futures_signal_exit.db"
    if os.path.exists(db_path):
        os.remove(db_path)

    db = Database(db_path)
    await db.connect()
    config = VenueConfig(
        venue_id="binance_futures",
        enabled=True,
        mode="paper",
        instrument_type="futures",
        max_order_usd=100.0,
        max_position_usd=250.0,
        max_daily_loss_usd=150.0,
        max_open_positions=3,
        fee_bps=4.0,
        slippage_limit_bps=35.0,
        signal_threshold=0.70,
        leverage=2,
        margin_mode="isolated",
        stop_loss_pct=0.02,
        take_profit_pct=0.03,
    )
    scanner = DummyScanner({"BTC/USDT:USDT": {"price": 100000.0}})
    venue = BinanceFuturesPaperVenue(config, db, scanner)

    success, _ = await venue.place_entry_order(
        symbol="BTC/USDT:USDT",
        side="SHORT",
        entry_price=100000.0,
        trade_size=100.0,
        signal_score=0.9,
        source="binance_futures_price_structure",
        source_signal="binance_futures_price_structure",
        spread_pct=0.0002,
        market_context={"market_id": "pm-btc-100k"},
    )
    assert success is True

    exit_success, _ = await venue.place_exit_order(
        "BTC/USDT:USDT",
        exit_price=99000.0,
        reason="signal_exit",
        source="binance_futures_price_structure",
    )

    assert exit_success is True
    assert len(await db.get_open_positions(venue="binance_futures")) == 0
    realized, _ = await db.get_venue_performance("binance_futures")
    assert realized > 0

    await db.close()
    os.remove(db_path)


@pytest.mark.asyncio
async def test_binance_futures_rejects_min_trade_size_with_split_reason():
    db_path = "tests/test_binance_futures_min_trade_size.db"
    if os.path.exists(db_path):
        os.remove(db_path)

    db = Database(db_path)
    await db.connect()
    config = VenueConfig(
        venue_id="binance_futures",
        enabled=True,
        mode="paper",
        instrument_type="futures",
        max_order_usd=100.0,
        max_position_usd=250.0,
        max_daily_loss_usd=150.0,
        max_open_positions=3,
        fee_bps=4.0,
        slippage_limit_bps=35.0,
        signal_threshold=0.70,
        leverage=2,
        margin_mode="isolated",
        stop_loss_pct=0.02,
        take_profit_pct=0.03,
    )
    scanner = DummyScanner({"BTC/USDT:USDT": {"price": 100000.0}})
    venue = BinanceFuturesPaperVenue(config, db, scanner)

    success, reason = await venue.place_entry_order(
        symbol="BTC/USDT:USDT",
        side="LONG",
        entry_price=100000.0,
        trade_size=20.0,
        signal_score=0.9,
        source="binance_futures_price_structure",
        source_signal="binance_futures_price_structure",
        spread_pct=0.0002,
        market_context={"market_id": "pm-btc-100k"},
        min_trade_size=25.0,
    )

    assert success is False
    assert reason == "max_total_position_usd_exceeded"

    await db.close()
    os.remove(db_path)


@pytest.mark.asyncio
async def test_binance_futures_rejects_max_open_positions_with_split_reason():
    db_path = "tests/test_binance_futures_max_open_positions.db"
    if os.path.exists(db_path):
        os.remove(db_path)

    db = Database(db_path)
    await db.connect()
    config = VenueConfig(
        venue_id="binance_futures",
        enabled=True,
        mode="paper",
        instrument_type="futures",
        max_order_usd=100.0,
        max_position_usd=250.0,
        max_daily_loss_usd=150.0,
        max_open_positions=1,
        fee_bps=4.0,
        slippage_limit_bps=35.0,
        signal_threshold=0.70,
        leverage=2,
        margin_mode="isolated",
        stop_loss_pct=0.02,
        take_profit_pct=0.03,
    )
    scanner = DummyScanner({"BTC/USDT:USDT": {"price": 100000.0}, "ETH/USDT:USDT": {"price": 3000.0}})
    venue = BinanceFuturesPaperVenue(config, db, scanner)

    first_success, _ = await venue.place_entry_order(
        symbol="BTC/USDT:USDT",
        side="LONG",
        entry_price=100000.0,
        trade_size=100.0,
        signal_score=0.9,
        source="binance_futures_price_structure",
        source_signal="binance_futures_price_structure",
        spread_pct=0.0002,
        market_context={"market_id": "pm-btc-100k"},
    )
    assert first_success is True

    second_success, second_reason = await venue.place_entry_order(
        symbol="ETH/USDT:USDT",
        side="LONG",
        entry_price=3000.0,
        trade_size=100.0,
        signal_score=0.9,
        source="binance_futures_price_structure",
        source_signal="binance_futures_price_structure",
        spread_pct=0.0002,
        market_context={"market_id": "pm-eth-3k"},
    )

    assert second_success is False
    assert second_reason == "max_open_positions_exceeded"

    await db.close()
    os.remove(db_path)
