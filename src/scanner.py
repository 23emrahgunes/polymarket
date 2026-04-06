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
import random

load_dotenv()
logger = logging.getLogger(__name__)

class MarketScanner:
    def __init__(self, exchange_id='binance'):
        # Allow switching exchanges if restricted (e.g., to 'coinbase')
        exchange_class = getattr(ccxt, exchange_id)
        self.exchange = exchange_class({
            'options': {
                'defaultType': 'future' if exchange_id == 'binance' else 'spot',
                'adjustForTimeDifference': True,
            }
        })

        private_key = os.getenv("POLYGON_PRIVATE_KEY", "0x0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef")
        self.polymarket = ClobClient(
            host="https://clob.polymarket.com",
            chain_id=POLYGON,
            key=private_key
        )

        self.current_prices = {}
        self._ohlcv_cache = {}
        self._last_ohlcv_update = {}
        self.monitored_symbols = set()
        self.ticker_task = None
        self.poly_price_cache = {}

        # Negative Cache: {token_id: blacklist_expiry_timestamp}
        self.negative_cache = {}

    async def update_monitored_symbols(self, symbols):
        new_symbols = set(symbols)
        if new_symbols != self.monitored_symbols:
            self.monitored_symbols = new_symbols
            if self.ticker_task:
                self.ticker_task.cancel()
            self.ticker_task = asyncio.create_task(self.watch_tickers(list(self.monitored_symbols)))

    async def watch_tickers(self, symbols):
        if not symbols: return
        while True:
            try:
                tickers = await self.exchange.watch_tickers(symbols)
                for symbol, ticker in tickers.items():
                    if symbol in symbols:
                        self.current_prices[symbol] = float(ticker['last'])
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"WebSocket error: {e}")
                await asyncio.sleep(5)

    @backoff.on_exception(backoff.expo, Exception, max_tries=5)
    async def get_historical_data(self, symbol, timeframe='1m', limit=1440):
        limit = int(limit)
        now = time.time()
        last_update = self._last_ohlcv_update.get(symbol, 0)

        if symbol not in self._ohlcv_cache or (now - last_update) > 60:
            ohlcv = await self.exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
            self._ohlcv_cache[symbol] = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            self._last_ohlcv_update[symbol] = now

        return self._ohlcv_cache[symbol]

    async def get_token_price(self, token_id):
        """
        Fetches the mid-price for a specific Polymarket token with robust data parsing.
        Includes Negative Caching for 404s and muted warnings.
        """
        now = time.time()

        # Check Negative Cache (blacklist)
        if token_id in self.negative_cache:
            if now < self.negative_cache[token_id]:
                return None
            else:
                del self.negative_cache[token_id] # Expired

        # Check Price Cache
        if token_id in self.poly_price_cache:
            ts, price = self.poly_price_cache[token_id]
            if now - ts < 2:
                return price

        try:
            # Fallback URL Logging for Browser Testing
            clob_url = f"https://clob.polymarket.com/book?token_id={token_id}"
            logger.debug(f"Fetching Orderbook: {clob_url}")

            await asyncio.sleep(random.uniform(0.1, 0.3))
            orderbook = await asyncio.to_thread(self.polymarket.get_order_book, token_id)

            if hasattr(orderbook, 'bids') and hasattr(orderbook, 'asks'):
                if orderbook.bids and orderbook.asks:
                    best_bid = float(getattr(orderbook.bids[0], 'price', orderbook.bids[0].get('price', 0)))
                    best_ask = float(getattr(orderbook.asks[0], 'price', orderbook.asks[0].get('price', 0)))
                    if best_bid > 0 and best_ask > 0:
                        mid_price = (best_bid + best_ask) / 2
                        self.poly_price_cache[token_id] = (now, mid_price)
                        return mid_price
            elif isinstance(orderbook, dict):
                bids = orderbook.get("bids", [])
                asks = orderbook.get("asks", [])
                if bids and asks:
                    best_bid = float(bids[0].get("price", 0))
                    best_ask = float(asks[0].get("price", 0))
                    if best_bid > 0 and best_ask > 0:
                        mid_price = (best_bid + best_ask) / 2
                        self.poly_price_cache[token_id] = (now, mid_price)
                        return mid_price

            # Auto-Blacklist invalid price data or missing orderbooks for 24h
            self.negative_cache[token_id] = now + (24 * 3600)
            logger.debug(f"Invalid or missing price for {token_id}. Blacklisting for 24h.")
            return None

        except Exception as e:
            # Handle 404 (PolyApiException or generic HTTP error)
            error_msg = str(e)
            status_code = getattr(e, 'status', 0)

            if "404" in error_msg or "not found" in error_msg.lower() or status_code == 404:
                # Silent Catch: Auto-Blacklist 404s for 24 hours
                self.negative_cache[token_id] = now + (24 * 3600)
                logger.debug(f"Orderbook for {token_id} not found. Blacklisting for 24h.")
            elif status_code >= 500 or "timeout" in error_msg.lower():
                # Only log ERROR for 5xx Server Errors or timeouts
                logger.error(f"Server Error (5xx) or Timeout for {token_id}: {e}")
            else:
                # Mute individual market 404s/Warnings
                logger.debug(f"Muted fetch error for {token_id}: {e}")
            return None

    async def close(self):
        if self.ticker_task:
            self.ticker_task.cancel()
        await self.exchange.close()
