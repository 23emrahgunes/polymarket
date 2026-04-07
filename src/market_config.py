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


def resolve_crypto_symbol(question: str, exchange_id: str) -> str | None:
    normalized = (question or "").upper()
    exchange_map = EXCHANGE_MAPPINGS.get(exchange_id, EXCHANGE_MAPPINGS["coinbase"])
    for base_symbol, exchange_symbol in exchange_map.items():
        if base_symbol in normalized:
            return exchange_symbol
    return None
