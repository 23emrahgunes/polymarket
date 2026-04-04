import asyncio
import ccxt.pro as ccxt
import backoff
from py_clob_client.client import ClobClient
from py_clob_client.constants import POLYGON
import pandas as pd
import numpy as np
import time

class MarketScanner:
    def __init__(self, binance_symbol="BTC/USDT"):
        self.binance_symbol = binance_symbol
        self.binance = ccxt.binance({
            'options': {
                'defaultType': 'future',
            }
        })
        self.polymarket = ClobClient("https://clob.polymarket.com", chain_id=POLYGON)
        self.current_price = None
        self._ohlcv_cache = None
        self._last_ohlcv_update = 0

    async def watch_ticker(self):
        """
        Uses WebSockets to watch ticker updates from Binance.
        """
        while True:
            try:
                ticker = await self.binance.watch_ticker(self.binance_symbol)
                self.current_price = float(ticker['last'])
            except Exception as e:
                print(f"WebSocket error: {e}")
                await asyncio.sleep(5)

    @backoff.on_exception(backoff.expo, Exception, max_tries=5)
    async def get_historical_data(self, timeframe='1m', limit=1440):
        # Only fetch if cache is older than 1 minute
        if self._ohlcv_cache is None or (time.time() - self._last_ohlcv_update) > 60:
            ohlcv = await self.binance.fetch_ohlcv(self.binance_symbol, timeframe=timeframe, limit=limit)
            self._ohlcv_cache = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            self._last_ohlcv_update = time.time()
        return self._ohlcv_cache

    @backoff.on_exception(backoff.expo, Exception, max_tries=5)
    async def get_polymarket_btc_markets(self):
        """
        Fetches BTC-related prediction markets from Polymarket.
        """
        try:
            # Wrap blocking SDK call in to_thread
            markets = await asyncio.to_thread(self.polymarket.get_markets)
            btc_markets = []
            for market in markets:
                question = market.get("question", "").upper()
                if "BTC" in question and "ABOVE" in question:
                    btc_markets.append(market)
            return btc_markets
        except Exception as e:
            print(f"Error fetching Polymarket markets: {e}")
            return []

    async def get_token_price(self, token_id):
        """
        Fetches the mid-price for a specific Polymarket token.
        """
        try:
            # Wrap blocking SDK call in to_thread
            orderbook = await asyncio.to_thread(self.polymarket.get_orderbook, token_id)
            if orderbook and orderbook.bids and orderbook.asks:
                best_bid = float(orderbook.bids[0].price)
                best_ask = float(orderbook.asks[0].price)
                return (best_bid + best_ask) / 2
            return None
        except Exception as e:
            print(f"Error fetching price for token {token_id}: {e}")
            return None

    async def close(self):
        await self.binance.close()
