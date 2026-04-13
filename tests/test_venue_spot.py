import os

import pytest

from src.database import Database
from src.venue_config import VenueConfig
from src.venues import BinanceSpotVenue


class DummySpotScanner:
    def __init__(self, prices):
        self.prices = prices

    async def get_spot_market_snapshot(self, symbol: str):
        data = self.prices[symbol]
        return {
            "symbol": symbol,
            "last_price": data["price"],
            "best_bid": data["price"] - 1,
            "best_ask": data["price"] + 1,
            "spread_pct": data.get("spread_pct", 0.0002),
            "volume_24h": 200000.0,
            "is_valid": True,
            "reason": "test",
            "fetched_at": 0.0,
        }


@pytest.mark.asyncio
async def test_binance_spot_paper_entry_and_take_profit_sync():
    db_path = "tests/test_binance_spot_paper.db"
    if os.path.exists(db_path):
        os.remove(db_path)

    db = Database(db_path)
    await db.connect()
    config = VenueConfig(
        venue_id="binance_spot",
        enabled=True,
        mode="paper",
        instrument_type="spot",
        max_order_usd=100.0,
        max_position_usd=200.0,
        max_daily_loss_usd=100.0,
        max_open_positions=3,
        fee_bps=10.0,
        slippage_limit_bps=20.0,
        signal_threshold=0.72,
        leverage=1,
        margin_mode="isolated",
        stop_loss_pct=0.02,
        take_profit_pct=0.03,
    )
    scanner = DummySpotScanner({"BTC/USDT": {"price": 100000.0}})
    venue = BinanceSpotVenue(config, db, scanner)

    success, _ = await venue.place_entry_order(
        symbol="BTC/USDT",
        side="LONG",
        entry_price=100000.0,
        trade_size=100.0,
        signal_score=0.9,
        source="binance_spot_price_structure",
        source_signal="binance_spot_price_structure",
        spread_pct=0.0002,
        market_context={"market_id": "pm-btc-95k"},
    )

    assert success is True
    assert await db.get_balance("binance_spot", "paper") == 900.0
    assert len(await db.get_open_positions(venue="binance_spot")) == 1
    assert len(await db.get_open_venue_orders("binance_spot", "BTC/USDT")) == 2

    scanner.prices["BTC/USDT"]["price"] = 103100.0
    await venue.sync_account_state(scanner)

    assert len(await db.get_open_positions(venue="binance_spot")) == 0
    realized, unrealized = await db.get_venue_performance("binance_spot")
    assert realized > 0
    assert unrealized == 0.0

    await db.close()
    os.remove(db_path)


@pytest.mark.asyncio
async def test_binance_spot_short_signal_exit_closes_long():
    db_path = "tests/test_binance_spot_signal_exit.db"
    if os.path.exists(db_path):
        os.remove(db_path)

    db = Database(db_path)
    await db.connect()
    config = VenueConfig(
        venue_id="binance_spot",
        enabled=True,
        mode="paper",
        instrument_type="spot",
        max_order_usd=100.0,
        max_position_usd=200.0,
        max_daily_loss_usd=100.0,
        max_open_positions=3,
        fee_bps=10.0,
        slippage_limit_bps=20.0,
        signal_threshold=0.72,
        leverage=1,
        margin_mode="isolated",
        stop_loss_pct=0.02,
        take_profit_pct=0.03,
    )
    scanner = DummySpotScanner({"BTC/USDT": {"price": 100000.0}})
    venue = BinanceSpotVenue(config, db, scanner)

    success, _ = await venue.place_entry_order(
        symbol="BTC/USDT",
        side="LONG",
        entry_price=100000.0,
        trade_size=100.0,
        signal_score=0.9,
        source="binance_spot_price_structure",
        source_signal="binance_spot_price_structure",
        spread_pct=0.0002,
        market_context={"market_id": "pm-btc-95k"},
    )
    assert success is True

    exit_success, _ = await venue.place_exit_order(
        "BTC/USDT",
        exit_price=99000.0,
        reason="signal_exit",
        source="binance_spot_price_structure",
    )

    assert exit_success is True
    assert len(await db.get_open_positions(venue="binance_spot")) == 0
    realized, _ = await db.get_venue_performance("binance_spot")
    assert realized < 0

    await db.close()
    os.remove(db_path)


@pytest.mark.asyncio
async def test_binance_spot_rejects_short_entries(caplog):
    db_path = "tests/test_binance_spot_short_reject.db"
    if os.path.exists(db_path):
        os.remove(db_path)

    db = Database(db_path)
    await db.connect()
    config = VenueConfig(
        venue_id="binance_spot",
        enabled=True,
        mode="paper",
        instrument_type="spot",
        max_order_usd=100.0,
        max_position_usd=200.0,
        max_daily_loss_usd=100.0,
        max_open_positions=3,
        fee_bps=10.0,
        slippage_limit_bps=20.0,
        signal_threshold=0.72,
        leverage=1,
        margin_mode="isolated",
        stop_loss_pct=0.02,
        take_profit_pct=0.03,
    )
    scanner = DummySpotScanner({"BTC/USDT": {"price": 100000.0}})
    venue = BinanceSpotVenue(config, db, scanner)

    with caplog.at_level("INFO"):
        success, reason = await venue.place_entry_order(
            symbol="BTC/USDT",
            side="SHORT",
            entry_price=100000.0,
            trade_size=100.0,
            signal_score=0.9,
            source="binance_spot_price_structure",
            source_signal="binance_spot_price_structure",
            spread_pct=0.0002,
            market_context={"market_id": "pm-btc-95k"},
        )

    assert success is False
    assert reason == "spot_short_not_supported"
    assert "spot_short_not_supported" in caplog.text

    await db.close()
    os.remove(db_path)


@pytest.mark.asyncio
async def test_binance_spot_rejects_min_trade_size_with_split_reason():
    db_path = "tests/test_binance_spot_min_trade_size.db"
    if os.path.exists(db_path):
        os.remove(db_path)

    db = Database(db_path)
    await db.connect()
    config = VenueConfig(
        venue_id="binance_spot",
        enabled=True,
        mode="paper",
        instrument_type="spot",
        max_order_usd=100.0,
        max_position_usd=200.0,
        max_daily_loss_usd=100.0,
        max_open_positions=3,
        fee_bps=10.0,
        slippage_limit_bps=20.0,
        signal_threshold=0.72,
        leverage=1,
        margin_mode="isolated",
        stop_loss_pct=0.02,
        take_profit_pct=0.03,
    )
    scanner = DummySpotScanner({"BTC/USDT": {"price": 100000.0}})
    venue = BinanceSpotVenue(config, db, scanner)

    success, reason = await venue.place_entry_order(
        symbol="BTC/USDT",
        side="LONG",
        entry_price=100000.0,
        trade_size=20.0,
        signal_score=0.9,
        source="binance_spot_price_structure",
        source_signal="binance_spot_price_structure",
        spread_pct=0.0002,
        market_context={"market_id": "pm-btc-95k"},
        min_trade_size=25.0,
    )

    assert success is False
    assert reason == "max_total_position_usd_exceeded"

    await db.close()
    os.remove(db_path)
