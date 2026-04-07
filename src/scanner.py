import asyncio
import logging
import os
import time
from typing import Dict, List, Optional

import backoff
import ccxt.async_support as ccxt
import pandas as pd
from dotenv import load_dotenv
from py_clob_client.client import ClobClient
from py_clob_client.constants import POLYGON

from src.env_utils import resolve_polygon_private_key


load_dotenv()
logger = logging.getLogger(__name__)


class MarketScanner:
    def __init__(self, exchange_id: str = "coinbase", debug_signal_mode: bool = False):
        exchange_class = getattr(ccxt, exchange_id)
        self.exchange_id = exchange_id
        self.debug_signal_mode = debug_signal_mode
        self.exchange = exchange_class(
            {
                "enableRateLimit": True,
                "options": {
                    "defaultType": "spot",
                    "adjustForTimeDifference": True,
                },
            }
        )

        private_key = resolve_polygon_private_key()
        self.polymarket = ClobClient(
            host="https://clob.polymarket.com",
            chain_id=POLYGON,
            key=private_key,
        )

        self.current_prices: Dict[str, float] = {}
        self._ohlcv_cache: Dict[str, pd.DataFrame] = {}
        self._last_ohlcv_update: Dict[str, float] = {}
        self.monitored_symbols = set()
        self.ticker_task: Optional[asyncio.Task] = None
        self.negative_cache: Dict[str, float] = {}
        self.orderbook_cache: Dict[str, Dict] = {}

        self._debug_orderbooks = {
            "debug_sports_token_yes": {"best_bid": 0.575, "best_ask": 0.585},
            "debug_sports_token_no": {"best_bid": 0.415, "best_ask": 0.425},
            "debug_crypto_token_yes": {"best_bid": 0.44, "best_ask": 0.46},
        }
        self._debug_tickers = {
            "BTC/USD": 102000.0,
            "ETH/USD": 5200.0,
            "SOL/USD": 240.0,
            "XRP/USD": 1.45,
            "DOGE/USD": 0.38,
            "BNB/USD": 980.0,
            "BTC/USDT": 102000.0,
            "ETH/USDT": 5200.0,
            "SOL/USDT": 240.0,
            "XRP/USDT": 1.45,
            "DOGE/USDT": 0.38,
            "BNB/USDT": 980.0,
        }

    async def update_monitored_symbols(self, symbols: List[str]) -> None:
        new_symbols = {symbol for symbol in symbols if symbol}
        if new_symbols == self.monitored_symbols:
            return

        self.monitored_symbols = new_symbols
        if self.ticker_task is not None:
            self.ticker_task.cancel()
            await asyncio.gather(self.ticker_task, return_exceptions=True)
        if self.monitored_symbols:
            self.ticker_task = asyncio.create_task(self.poll_tickers(list(self.monitored_symbols)))

    async def poll_tickers(self, symbols: List[str]) -> None:
        while True:
            try:
                await self.refresh_symbol_prices(symbols)
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Exchange polling error for %s: %s", symbols, exc)
                await asyncio.sleep(5)

    async def refresh_symbol_price(self, symbol: str) -> Optional[float]:
        await self.refresh_symbol_prices([symbol])
        return self.current_prices.get(symbol)

    async def refresh_symbol_prices(self, symbols: List[str]) -> None:
        if not symbols:
            return

        if self.debug_signal_mode:
            for symbol in symbols:
                if symbol in self._debug_tickers:
                    self.current_prices[symbol] = self._debug_tickers[symbol]

        try:
            tickers = await self.exchange.fetch_tickers(symbols)
            for symbol in symbols:
                ticker = tickers.get(symbol)
                last_price = ticker.get("last") if isinstance(ticker, dict) else None
                if last_price is not None:
                    self.current_prices[symbol] = float(last_price)
        except Exception:
            for symbol in symbols:
                try:
                    ticker = await self.exchange.fetch_ticker(symbol)
                    if ticker and ticker.get("last") is not None:
                        self.current_prices[symbol] = float(ticker["last"])
                except Exception as exc:
                    logger.info(
                        "[REJECT] source=scanner category=CRYPTO market=%s reasons=missing_exchange_price inputs=%s",
                        symbol,
                        {"exchange_id": self.exchange_id, "symbol": symbol, "error": str(exc)},
                    )

    @backoff.on_exception(backoff.expo, Exception, max_tries=5)
    async def get_historical_data(self, symbol: str, timeframe: str = "1m", limit: int = 1440) -> pd.DataFrame:
        limit = int(limit)
        now = time.time()
        last_update = self._last_ohlcv_update.get(symbol, 0.0)

        if self.debug_signal_mode and symbol in self._debug_tickers:
            if symbol not in self._ohlcv_cache or (now - last_update) > 60:
                self._ohlcv_cache[symbol] = self._build_debug_ohlcv(symbol, limit)
                self._last_ohlcv_update[symbol] = now
            return self._ohlcv_cache[symbol]

        if symbol not in self._ohlcv_cache or (now - last_update) > 60:
            ohlcv = await self.exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
            self._ohlcv_cache[symbol] = pd.DataFrame(
                ohlcv,
                columns=["timestamp", "open", "high", "low", "close", "volume"],
            )
            self._last_ohlcv_update[symbol] = now

        return self._ohlcv_cache[symbol]

    def _build_debug_ohlcv(self, symbol: str, limit: int) -> pd.DataFrame:
        base_price = self._debug_tickers[symbol]
        rows = []
        for index in range(limit):
            delta = ((index % 10) - 5) * 0.0025
            close_price = base_price * (1 + delta)
            rows.append(
                [
                    index * 60_000,
                    close_price * 0.998,
                    close_price * 1.002,
                    close_price * 0.996,
                    close_price,
                    1000 + index,
                ]
            )
        return pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])

    def _extract_price(self, entry) -> float:
        try:
            if isinstance(entry, dict):
                return float(entry.get("price", 0))
            return float(getattr(entry, "price", 0))
        except (AttributeError, TypeError, ValueError):
            return 0.0

    async def get_orderbook_snapshot(self, token_id: str) -> Dict:
        now = time.time()
        if token_id in self.negative_cache and now < self.negative_cache[token_id]:
            logger.info(
                "[REJECT] source=orderbook category=UNKNOWN market=%s reasons=missing_polymarket_token_price inputs=%s",
                token_id,
                {"token_id": token_id, "negative_cache": True},
            )
            return self._invalid_snapshot(token_id, "missing_polymarket_token_price")

        if token_id in self.orderbook_cache:
            cached = self.orderbook_cache[token_id]
            if now - cached["fetched_at"] < 2:
                return cached

        if self.debug_signal_mode and token_id in self._debug_orderbooks:
            best_bid = self._debug_orderbooks[token_id]["best_bid"]
            best_ask = self._debug_orderbooks[token_id]["best_ask"]
            snapshot = self._build_snapshot(token_id, best_bid, best_ask, reason="debug_orderbook")
            self.orderbook_cache[token_id] = snapshot
            return snapshot

        try:
            orderbook = await asyncio.to_thread(self.polymarket.get_order_book, token_id)
            bids, asks = [], []
            if hasattr(orderbook, "bids") and hasattr(orderbook, "asks"):
                bids, asks = orderbook.bids, orderbook.asks
            elif isinstance(orderbook, dict):
                bids = orderbook.get("bids", [])
                asks = orderbook.get("asks", [])

            if not bids or not asks:
                logger.info(
                    "[REJECT] source=orderbook category=UNKNOWN market=%s reasons=invalid_orderbook_data inputs=%s",
                    token_id,
                    {"token_id": token_id, "detail": "missing_bids_or_asks"},
                )
                self.negative_cache[token_id] = now + (24 * 3600)
                return self._invalid_snapshot(token_id, "invalid_orderbook_data")

            best_bid = self._extract_price(bids[0])
            best_ask = self._extract_price(asks[0])
            if best_bid <= 0 or best_ask <= 0 or best_ask < best_bid:
                logger.info(
                    "[REJECT] source=orderbook category=UNKNOWN market=%s reasons=invalid_orderbook_data inputs=%s",
                    token_id,
                    {"token_id": token_id, "best_bid": best_bid, "best_ask": best_ask},
                )
                self.negative_cache[token_id] = now + (24 * 3600)
                return self._invalid_snapshot(token_id, "invalid_orderbook_data")

            snapshot = self._build_snapshot(token_id, best_bid, best_ask)
            self.orderbook_cache[token_id] = snapshot
            return snapshot
        except Exception as exc:
            error_message = str(exc)
            if "404" in error_message or "not found" in error_message.lower():
                self.negative_cache[token_id] = now + (24 * 3600)
                logger.info(
                    "[REJECT] source=orderbook category=UNKNOWN market=%s reasons=missing_polymarket_token_price inputs=%s",
                    token_id,
                    {"token_id": token_id, "error": error_message},
                )
                return self._invalid_snapshot(token_id, "missing_polymarket_token_price")

            logger.info(
                "[REJECT] source=orderbook category=UNKNOWN market=%s reasons=invalid_orderbook_data inputs=%s",
                token_id,
                {"token_id": token_id, "error": error_message},
            )
            return self._invalid_snapshot(token_id, "invalid_orderbook_data")

    def _build_snapshot(self, token_id: str, best_bid: float, best_ask: float, reason: str = "ok") -> Dict:
        mid_price = (best_bid + best_ask) / 2
        spread_pct = (best_ask - best_bid) / mid_price if mid_price > 0 else 1.0
        return {
            "token_id": token_id,
            "best_bid": best_bid,
            "best_ask": best_ask,
            "mid_price": mid_price,
            "spread_pct": spread_pct,
            "is_valid": True,
            "reason": reason,
            "fetched_at": time.time(),
        }

    def _invalid_snapshot(self, token_id: str, reason: str) -> Dict:
        return {
            "token_id": token_id,
            "best_bid": None,
            "best_ask": None,
            "mid_price": None,
            "spread_pct": None,
            "is_valid": False,
            "reason": reason,
            "fetched_at": time.time(),
        }

    async def get_token_price(self, token_id: str) -> Optional[float]:
        snapshot = await self.get_orderbook_snapshot(token_id)
        return snapshot["mid_price"] if snapshot["is_valid"] else None

    async def close(self) -> None:
        if self.ticker_task is not None:
            self.ticker_task.cancel()
            await asyncio.gather(self.ticker_task, return_exceptions=True)
        await self.exchange.close()
