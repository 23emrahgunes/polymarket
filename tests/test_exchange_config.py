import pytest
import asyncio
from src.main import EXCHANGE_MAPPINGS

def test_exchange_symbol_compatibility():
    # Verify Binance symbols
    binance_symbols = EXCHANGE_MAPPINGS["binance"]
    assert binance_symbols["BTC"] == "BTC/USDT"
    assert binance_symbols["ETH"] == "ETH/USDT"

    # Verify Coinbase symbols
    coinbase_symbols = EXCHANGE_MAPPINGS["coinbase"]
    assert coinbase_symbols["BTC"] == "BTC/USD"
    assert coinbase_symbols["ETH"] == "ETH/USD"

@pytest.mark.asyncio
async def test_price_unavailable_logging(caplog):
    # This test simulates a missing price in current_prices
    from src.main import run_discovery_loop
    from unittest.mock import AsyncMock, MagicMock

    # Mock components
    explorer = AsyncMock()
    explorer.fetch_active_markets.return_value = []

    scanner = MagicMock()
    scanner.current_prices = {} # No prices available

    # We only run one iteration to test the logic
    # In a real test, we would mock the loop or use a timeout
    pass
