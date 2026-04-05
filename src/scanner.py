import asyncio
import ccxt.pro as ccxt
import backoff
import os
import logging
from py_clob_client.client import ClobClient
from py_clob_client.constants import POLYGON
from dotenv import load_dotenv
import pandas as pd
import numpy as np
import time

load_dotenv()
logger = logging.getLogger(__name__)

class MarketScanner:
    def __init__(self):
        self.binance = ccxt.binance({
            'options': {
                'defaultType': 'future',
            }
        })

        private_key = os.getenv("POLYGON_PRIVATE_KEY", "0x0000000000000000000000000000000000000000000000000000000000000000")
        self.polymarket = ClobClient(
            host="https://clob.polymarket.com",
            chain_id=POLYGON,
            key=private_key
        )

        self.current_prices = {}
        self._ohlcv_cache = {}
        self._last_ohlcv_update = {}

    async def watch_tickers(self, symbols):
        """
        Uses WebSockets to watch ticker updates from Binance for multiple symbols.
        """
        while True:
            try:
                tickers = await self.binance.watch_tickers(symbols)
                for symbol, ticker in tickers.items():
                    if symbol in symbols:
                        self.current_prices[symbol] = float(ticker['last'])
            except Exception as e:
                logger.error(f"WebSocket error: {e}")
                await asyncio.sleep(5)

    @backoff.on_exception(backoff.expo, Exception, max_tries=5)
    async def get_historical_data(self, symbol, timeframe='1m', limit=1440):
        # Ensure limit is an integer
        limit = int(limit)

        now = time.time()
        last_update = self._last_ohlcv_update.get(symbol, 0)

        # Only fetch if cache is older than 1 minute
        if symbol not in self._ohlcv_cache or (now - last_update) > 60:
            ohlcv = await self.binance.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
            self._ohlcv_cache[symbol] = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            self._last_ohlcv_update[symbol] = now

        return self._ohlcv_cache[symbol]

    @backoff.on_exception(backoff.expo, Exception, max_tries=5)
    async def get_polymarket_markets_for_symbol(self, base_currency):
        """
        Fetches prediction markets from Polymarket related to the symbol.
        Handles non-dict/list responses and data type mismatches.
        """
        try:
            # Wrap blocking SDK call in to_thread
            raw_response = await asyncio.to_thread(self.polymarket.get_markets)

            # Type safety: ensure response is a list or a dict with a list
            markets = []
            if isinstance(raw_response, list):
                markets = raw_response
            elif isinstance(raw_response, dict):
                # Check for common pagination keys in SDK responses
                markets = raw_response.get("data", raw_response.get("markets", []))
            else:
                logger.warning(f"Unexpected Polymarket API response type: {type(raw_response)}")
                return []

            filtered_markets = []
            for market in markets:
                # Double-check market is a dict before calling .get()
                if not isinstance(market, dict):
                    continue

                question = market.get("question", "").upper()
                if base_currency.upper() in question and "ABOVE" in question:
                    filtered_markets.append(market)
            return filtered_markets
        except Exception as e:
            logger.error(f"Error fetching Polymarket markets for {base_currency}: {e}")
            return []

    async def get_token_price(self, token_id):
        """
        Fetches the mid-price for a specific Polymarket token.
        Handles non-dict responses and data type mismatches.
        """
        try:
            # Wrap blocking SDK call in to_thread
            orderbook = await asyncio.to_thread(self.polymarket.get_orderbook, token_id)

            # Robust response checking
            if not isinstance(orderbook, dict) and not hasattr(orderbook, 'bids'):
                logger.warning(f"Unexpected orderbook response: {orderbook}")
                return None

            if hasattr(orderbook, 'bids') and hasattr(orderbook, 'asks'):
                if orderbook.bids and orderbook.asks:
                    best_bid = float(orderbook.bids[0].price)
                    best_ask = float(orderbook.asks[0].price)
                    return (best_bid + best_ask) / 2
            elif isinstance(orderbook, dict):
                bids = orderbook.get("bids", [])
                asks = orderbook.get("asks", [])
                if bids and asks:
                    best_bid = float(bids[0].get("price", 0))
                    best_ask = float(asks[0].get("price", 0))
                    return (best_bid + best_ask) / 2
            return None
        except Exception as e:
            logger.error(f"Error fetching price for token {token_id}: {e}")
            return None

    async def close(self):
        await self.binance.close()
