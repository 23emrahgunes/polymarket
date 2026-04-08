EXCHANGE_MAPPINGS = {
    "binance": {
        "BTC": "BTC/USDT",
        "ETH": "ETH/USDT",
        "SOL": "SOL/USDT",
        "XRP": "XRP/USDT",
        "DOGE": "DOGE/USDT",
        "BNB": "BNB/USDT",
    },
    "coinbase": {
        "BTC": "BTC/USD",
        "ETH": "ETH/USD",
        "SOL": "SOL/USD",
        "XRP": "XRP/USD",
        "DOGE": "DOGE/USD",
        "BNB": "BNB/USD",
    },
}

BINANCE_FUTURES_MAPPINGS = {
    "BTC": "BTC/USDT:USDT",
    "ETH": "ETH/USDT:USDT",
    "SOL": "SOL/USDT:USDT",
    "XRP": "XRP/USDT:USDT",
    "DOGE": "DOGE/USDT:USDT",
    "BNB": "BNB/USDT:USDT",
}


def resolve_crypto_symbol(question: str, exchange_id: str) -> str | None:
    normalized = (question or "").upper()
    exchange_map = EXCHANGE_MAPPINGS.get(exchange_id, EXCHANGE_MAPPINGS["coinbase"])
    for base_symbol, exchange_symbol in exchange_map.items():
        if base_symbol in normalized:
            return exchange_symbol
    return None


def resolve_binance_futures_symbol(question: str) -> str | None:
    normalized = (question or "").upper()
    for base_symbol, futures_symbol in BINANCE_FUTURES_MAPPINGS.items():
        if base_symbol in normalized:
            return futures_symbol
    return None
