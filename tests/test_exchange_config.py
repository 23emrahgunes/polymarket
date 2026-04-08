from src.main import EXCHANGE_MAPPINGS
from src.market_config import BINANCE_FUTURES_MAPPINGS, resolve_binance_futures_symbol, resolve_crypto_symbol


def test_exchange_symbol_compatibility():
    assert EXCHANGE_MAPPINGS["binance"]["BTC"] == "BTC/USDT"
    assert EXCHANGE_MAPPINGS["coinbase"]["BTC"] == "BTC/USD"
    assert BINANCE_FUTURES_MAPPINGS["BTC"] == "BTC/USDT:USDT"


def test_exchange_specific_symbol_mapping():
    assert resolve_crypto_symbol("Will BTC be above $100,000?", "coinbase") == "BTC/USD"
    assert resolve_crypto_symbol("Will ETH be above $5,000?", "binance") == "ETH/USDT"
    assert resolve_binance_futures_symbol("Will ETH be above $5,000?") == "ETH/USDT:USDT"
    assert resolve_crypto_symbol("Will Team A win the final?", "coinbase") is None
