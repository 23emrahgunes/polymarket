import asyncio
import logging
import math
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
        self.binance_spot_exchange = ccxt.binance(
            {
                "enableRateLimit": True,
                "options": {
                    "defaultType": "spot",
                    "adjustForTimeDifference": True,
                },
            }
        )
        self.futures_exchange = ccxt.binanceusdm(
            {
                "enableRateLimit": True,
                "options": {
                    "defaultType": "future",
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
        self.spot_snapshot_cache: Dict[str, Dict] = {}
        self.futures_snapshot_cache: Dict[str, Dict] = {}
        self.futures_ohlcv_cache: Dict[str, pd.DataFrame] = {}
        self._futures_last_ohlcv_update: Dict[str, float] = {}

        self._debug_orderbooks = {
            "debug_sports_token_yes": {"best_bid": 0.575, "best_ask": 0.585},
            "debug_sports_token_no": {"best_bid": 0.415, "best_ask": 0.425},
            "debug_crypto_token_yes": {"best_bid": 0.447, "best_ask": 0.453},
            "debug_crypto_long_token_yes": {"best_bid": 0.347, "best_ask": 0.353},
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
        self._debug_futures = {
            "BTC/USDT:USDT": {
                "last_price": 103200.0,
                "mark_price": 103100.0,
                "best_bid": 103050.0,
                "best_ask": 103150.0,
                "volume_24h": 220000.0,
                "open_interest": 1800000.0,
                "funding_rate": 0.0002,
            },
            "ETH/USDT:USDT": {
                "last_price": 5250.0,
                "mark_price": 5246.0,
                "best_bid": 5245.0,
                "best_ask": 5247.0,
                "volume_24h": 180000.0,
                "open_interest": 950000.0,
                "funding_rate": 0.0001,
            },
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
                self._ohlcv_cache[symbol] = self._build_debug_ohlcv(self._debug_tickers[symbol], limit)
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

    @backoff.on_exception(backoff.expo, Exception, max_tries=5)
    async def get_futures_historical_data(self, symbol: str, timeframe: str = "1m", limit: int = 1440) -> pd.DataFrame:
        limit = int(limit)
        now = time.time()
        last_update = self._futures_last_ohlcv_update.get(symbol, 0.0)

        if self.debug_signal_mode and symbol in self._debug_futures:
            if symbol not in self.futures_ohlcv_cache or (now - last_update) > 60:
                self.futures_ohlcv_cache[symbol] = self._build_debug_ohlcv(self._debug_futures[symbol]["mark_price"], limit)
                self._futures_last_ohlcv_update[symbol] = now
            return self.futures_ohlcv_cache[symbol]

        if symbol not in self.futures_ohlcv_cache or (now - last_update) > 60:
            ohlcv = await self.futures_exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
            self.futures_ohlcv_cache[symbol] = pd.DataFrame(
                ohlcv,
                columns=["timestamp", "open", "high", "low", "close", "volume"],
            )
            self._futures_last_ohlcv_update[symbol] = now
        return self.futures_ohlcv_cache[symbol]

    def _build_debug_ohlcv(self, base_price: float, limit: int) -> pd.DataFrame:
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
        number = self._coerce_float(entry)
        return number if number is not None else 0.0

    def _coerce_float(self, value) -> Optional[float]:
        try:
            if value is None:
                return None
            if isinstance(value, dict):
                value = value.get("price")
            elif isinstance(value, (list, tuple)):
                if not value:
                    return None
                value = value[0]
            elif hasattr(value, "price"):
                value = getattr(value, "price")
            number = float(value)
            if not math.isfinite(number):
                return None
            return number
        except (AttributeError, TypeError, ValueError):
            return None

    def _extract_book_levels(self, orderbook) -> tuple[list, list]:
        bids, asks = [], []
        if hasattr(orderbook, "bids") and hasattr(orderbook, "asks"):
            bids, asks = orderbook.bids, orderbook.asks
        elif isinstance(orderbook, dict):
            bids = orderbook.get("bids", [])
            asks = orderbook.get("asks", [])
        return list(bids or []), list(asks or [])

    def _gap_pct(self, reference_price: float, mid_price: float) -> Optional[float]:
        if reference_price <= 0 or mid_price <= 0:
            return None
        return abs(reference_price - mid_price) / mid_price

    def _invalid_futures_snapshot(
        self,
        symbol: str,
        reason: str,
        *,
        fetched_at: float,
        last_price: float = 0.0,
        mark_price: float = 0.0,
        volume_24h: float = 0.0,
        open_interest: float = 0.0,
        funding_rate: float = 0.0,
        bid_source: Optional[str] = None,
        ask_source: Optional[str] = None,
        spread_source: Optional[str] = None,
        orderbook_fallback_used: bool = False,
        orderbook_repriced: bool = False,
        raw_ticker_bid: Optional[float] = None,
        raw_ticker_ask: Optional[float] = None,
        raw_info_bid: Optional[float] = None,
        raw_info_ask: Optional[float] = None,
        snapshot_quality: str = "invalid_missing_bid_ask",
    ) -> Dict:
        return {
            "symbol": symbol,
            "last_price": last_price,
            "mark_price": mark_price,
            "best_bid": None,
            "best_ask": None,
            "mid_price": None,
            "spread_pct": None,
            "volume_24h": volume_24h,
            "open_interest": open_interest,
            "funding_rate": funding_rate,
            "bid_source": bid_source,
            "ask_source": ask_source,
            "spread_source": spread_source,
            "orderbook_fallback_used": orderbook_fallback_used,
            "orderbook_repriced": orderbook_repriced,
            "raw_ticker_bid": raw_ticker_bid,
            "raw_ticker_ask": raw_ticker_ask,
            "raw_info_bid": raw_info_bid,
            "raw_info_ask": raw_info_ask,
            "mark_mid_gap_pct": None,
            "last_mid_gap_pct": None,
            "snapshot_quality": snapshot_quality,
            "is_valid": False,
            "reason": reason,
            "fetched_at": fetched_at,
        }

    def _build_futures_snapshot(
        self,
        *,
        symbol: str,
        last_price: float,
        mark_price: float,
        best_bid: float,
        best_ask: float,
        volume_24h: float,
        open_interest: float,
        funding_rate: float,
        fetched_at: float,
        bid_source: str,
        ask_source: str,
        spread_source: str,
        orderbook_fallback_used: bool,
        orderbook_repriced: bool,
        raw_ticker_bid: Optional[float],
        raw_ticker_ask: Optional[float],
        raw_info_bid: Optional[float],
        raw_info_ask: Optional[float],
        snapshot_quality: str,
        reason: str = "ok",
    ) -> Dict:
        mid_price = (best_bid + best_ask) / 2
        spread_pct = (best_ask - best_bid) / mid_price if mid_price > 0 else 1.0
        return {
            "symbol": symbol,
            "last_price": last_price,
            "mark_price": mark_price,
            "best_bid": best_bid,
            "best_ask": best_ask,
            "mid_price": mid_price,
            "spread_pct": spread_pct,
            "volume_24h": volume_24h,
            "open_interest": open_interest,
            "funding_rate": funding_rate,
            "bid_source": bid_source,
            "ask_source": ask_source,
            "spread_source": spread_source,
            "orderbook_fallback_used": orderbook_fallback_used,
            "orderbook_repriced": orderbook_repriced,
            "raw_ticker_bid": raw_ticker_bid,
            "raw_ticker_ask": raw_ticker_ask,
            "raw_info_bid": raw_info_bid,
            "raw_info_ask": raw_info_ask,
            "mark_mid_gap_pct": self._gap_pct(mark_price, mid_price),
            "last_mid_gap_pct": self._gap_pct(last_price, mid_price),
            "snapshot_quality": snapshot_quality,
            "is_valid": True,
            "reason": reason,
            "fetched_at": fetched_at,
        }

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

    async def get_futures_market_snapshot(self, symbol: str) -> Dict:
        now = time.time()
        cached = self.futures_snapshot_cache.get(symbol)
        if cached and now - cached["fetched_at"] < 2:
            return cached

        if self.debug_signal_mode and symbol in self._debug_futures:
            data = self._debug_futures[symbol]
            snapshot = self._build_futures_snapshot(
                symbol=symbol,
                last_price=data["last_price"],
                mark_price=data["mark_price"],
                best_bid=data["best_bid"],
                best_ask=data["best_ask"],
                volume_24h=data["volume_24h"],
                open_interest=data["open_interest"],
                funding_rate=data["funding_rate"],
                fetched_at=now,
                bid_source="debug.bid",
                ask_source="debug.ask",
                spread_source="debug_book",
                orderbook_fallback_used=False,
                orderbook_repriced=False,
                raw_ticker_bid=data["best_bid"],
                raw_ticker_ask=data["best_ask"],
                raw_info_bid=None,
                raw_info_ask=None,
                snapshot_quality="trusted_ticker_book",
                reason="debug_futures",
            )
            self.futures_snapshot_cache[symbol] = snapshot
            return snapshot

        try:
            ticker = await self.futures_exchange.fetch_ticker(symbol)
            info = ticker.get("info", {}) or {}
            raw_ticker_bid = self._coerce_float(ticker.get("bid"))
            raw_ticker_ask = self._coerce_float(ticker.get("ask"))
            raw_info_bid = self._coerce_float(info.get("bidPrice"))
            raw_info_ask = self._coerce_float(info.get("askPrice"))

            best_bid = 0.0
            best_ask = 0.0
            bid_source = None
            ask_source = None
            if raw_ticker_bid is not None and raw_ticker_bid > 0:
                best_bid = raw_ticker_bid
                bid_source = "ticker.bid"
            elif raw_info_bid is not None and raw_info_bid > 0:
                best_bid = raw_info_bid
                bid_source = "info.bidPrice"

            if raw_ticker_ask is not None and raw_ticker_ask > 0:
                best_ask = raw_ticker_ask
                ask_source = "ticker.ask"
            elif raw_info_ask is not None and raw_info_ask > 0:
                best_ask = raw_info_ask
                ask_source = "info.askPrice"

            last_price = self._coerce_float(ticker.get("last")) or self._coerce_float(ticker.get("close")) or 0.0
            mark_price = self._coerce_float(info.get("markPrice")) or last_price or 0.0
            if last_price <= 0 < mark_price:
                last_price = mark_price
            elif mark_price <= 0 < last_price:
                mark_price = last_price
            volume_24h = self._coerce_float(ticker.get("quoteVolume")) or self._coerce_float(info.get("quoteVolume")) or 0.0

            funding_rate = 0.0
            open_interest = 0.0
            try:
                funding = await self.futures_exchange.fetch_funding_rate(symbol)
                funding_rate = float(funding.get("fundingRate") or funding.get("info", {}).get("lastFundingRate") or 0.0)
            except Exception:
                funding_rate = 0.0
            try:
                oi = await self.futures_exchange.fetch_open_interest(symbol)
                open_interest = float(oi.get("openInterestAmount") or oi.get("openInterest") or oi.get("info", {}).get("openInterest") or 0.0)
            except Exception:
                open_interest = 0.0

            if last_price <= 0 or mark_price <= 0:
                return self._invalid_futures_snapshot(
                    symbol,
                    "position_sync_failed",
                    fetched_at=now,
                    last_price=last_price,
                    mark_price=mark_price,
                    volume_24h=volume_24h,
                    open_interest=open_interest,
                    funding_rate=funding_rate,
                    bid_source=bid_source,
                    ask_source=ask_source,
                    raw_ticker_bid=raw_ticker_bid,
                    raw_ticker_ask=raw_ticker_ask,
                    raw_info_bid=raw_info_bid,
                    raw_info_ask=raw_info_ask,
                    snapshot_quality="invalid_missing_bid_ask",
                )

            initial_book_valid = best_bid > 0 and best_ask > 0 and best_ask >= best_bid
            initial_spread_pct = None
            if initial_book_valid:
                initial_mid_price = (best_bid + best_ask) / 2
                initial_spread_pct = (best_ask - best_bid) / initial_mid_price if initial_mid_price > 0 else 1.0

            snapshot_quality = None
            spread_source = None
            if initial_book_valid:
                if bid_source == "ticker.bid" and ask_source == "ticker.ask":
                    snapshot_quality = "trusted_ticker_book"
                    spread_source = "ticker_book"
                else:
                    snapshot_quality = "trusted_info_book"
                    spread_source = "info_book"

            orderbook_fallback_used = False
            orderbook_repriced = False
            needs_orderbook_validation = (not initial_book_valid) or (
                initial_spread_pct is not None and initial_spread_pct > 0.018
            )

            if needs_orderbook_validation:
                orderbook_fallback_used = True
                try:
                    orderbook = await self.futures_exchange.fetch_order_book(symbol, limit=5)
                    bids, asks = self._extract_book_levels(orderbook)
                except Exception:
                    bids, asks = [], []

                orderbook_bid = self._extract_price(bids[0]) if bids else 0.0
                orderbook_ask = self._extract_price(asks[0]) if asks else 0.0
                orderbook_valid = orderbook_bid > 0 and orderbook_ask > 0 and orderbook_ask >= orderbook_bid

                if not initial_book_valid:
                    if orderbook_valid:
                        best_bid = orderbook_bid
                        best_ask = orderbook_ask
                        bid_source = "orderbook.bid"
                        ask_source = "orderbook.ask"
                        spread_source = "orderbook"
                        snapshot_quality = "trusted_orderbook_book"
                    else:
                        invalid_reason = "futures_bid_ask_missing"
                        if (best_bid > 0 and best_ask > 0 and best_ask < best_bid) or (
                            (bids or asks) and (orderbook_bid <= 0 or orderbook_ask <= 0 or orderbook_ask < orderbook_bid)
                        ):
                            invalid_reason = "futures_snapshot_untrusted"
                        return self._invalid_futures_snapshot(
                            symbol,
                            invalid_reason,
                            fetched_at=now,
                            last_price=last_price,
                            mark_price=mark_price,
                            volume_24h=volume_24h,
                            open_interest=open_interest,
                            funding_rate=funding_rate,
                            bid_source=bid_source,
                            ask_source=ask_source,
                            spread_source=spread_source,
                            orderbook_fallback_used=orderbook_fallback_used,
                            raw_ticker_bid=raw_ticker_bid,
                            raw_ticker_ask=raw_ticker_ask,
                            raw_info_bid=raw_info_bid,
                            raw_info_ask=raw_info_ask,
                            snapshot_quality="invalid_missing_bid_ask" if invalid_reason == "futures_bid_ask_missing" else "invalid_untrusted",
                        )
                elif orderbook_valid:
                    orderbook_mid_price = (orderbook_bid + orderbook_ask) / 2
                    orderbook_spread_pct = (orderbook_ask - orderbook_bid) / orderbook_mid_price if orderbook_mid_price > 0 else 1.0
                    if initial_spread_pct is not None and orderbook_spread_pct < initial_spread_pct:
                        best_bid = orderbook_bid
                        best_ask = orderbook_ask
                        bid_source = "orderbook.bid"
                        ask_source = "orderbook.ask"
                        spread_source = "orderbook"
                        snapshot_quality = "trusted_orderbook_book"
                        orderbook_repriced = True

            if best_bid <= 0 or best_ask <= 0 or best_ask < best_bid or snapshot_quality is None or spread_source is None:
                return self._invalid_futures_snapshot(
                    symbol,
                    "futures_bid_ask_missing" if best_bid <= 0 or best_ask <= 0 else "futures_snapshot_untrusted",
                    fetched_at=now,
                    last_price=last_price,
                    mark_price=mark_price,
                    volume_24h=volume_24h,
                    open_interest=open_interest,
                    funding_rate=funding_rate,
                    bid_source=bid_source,
                    ask_source=ask_source,
                    spread_source=spread_source,
                    orderbook_fallback_used=orderbook_fallback_used,
                    orderbook_repriced=orderbook_repriced,
                    raw_ticker_bid=raw_ticker_bid,
                    raw_ticker_ask=raw_ticker_ask,
                    raw_info_bid=raw_info_bid,
                    raw_info_ask=raw_info_ask,
                    snapshot_quality="invalid_missing_bid_ask" if best_bid <= 0 or best_ask <= 0 else "invalid_untrusted",
                )

            snapshot = self._build_futures_snapshot(
                symbol=symbol,
                last_price=last_price,
                mark_price=mark_price,
                best_bid=best_bid,
                best_ask=best_ask,
                volume_24h=volume_24h,
                open_interest=open_interest,
                funding_rate=funding_rate,
                fetched_at=now,
                bid_source=bid_source,
                ask_source=ask_source,
                spread_source=spread_source,
                orderbook_fallback_used=orderbook_fallback_used,
                orderbook_repriced=orderbook_repriced,
                raw_ticker_bid=raw_ticker_bid,
                raw_ticker_ask=raw_ticker_ask,
                raw_info_bid=raw_info_bid,
                raw_info_ask=raw_info_ask,
                snapshot_quality=snapshot_quality,
            )
            self.futures_snapshot_cache[symbol] = snapshot
            return snapshot
        except Exception as exc:
            logger.info(
                "[REJECT] source=futures category=CRYPTO market=%s reasons=position_sync_failed inputs=%s",
                symbol,
                {"symbol": symbol, "error": str(exc)},
            )
            return self._invalid_futures_snapshot(symbol, "position_sync_failed", fetched_at=now)

    async def get_spot_market_snapshot(self, symbol: str) -> Dict:
        now = time.time()
        cached = self.spot_snapshot_cache.get(symbol)
        if cached and now - cached["fetched_at"] < 2:
            return cached

        if self.debug_signal_mode and symbol in self._debug_tickers:
            last_price = self._debug_tickers[symbol]
            best_bid = round(last_price * 0.9995, 6)
            best_ask = round(last_price * 1.0005, 6)
            mid_price = (best_bid + best_ask) / 2
            snapshot = {
                "symbol": symbol,
                "last_price": last_price,
                "best_bid": best_bid,
                "best_ask": best_ask,
                "spread_pct": (best_ask - best_bid) / mid_price if mid_price > 0 else 1.0,
                "volume_24h": 220000.0,
                "is_valid": True,
                "reason": "debug_spot",
                "fetched_at": now,
            }
            self.spot_snapshot_cache[symbol] = snapshot
            return snapshot

        try:
            ticker = await self.binance_spot_exchange.fetch_ticker(symbol)
            best_bid = float(ticker.get("bid") or ticker.get("last") or 0.0)
            best_ask = float(ticker.get("ask") or ticker.get("last") or 0.0)
            last_price = float(ticker.get("last") or 0.0)
            volume_24h = float(ticker.get("quoteVolume") or 0.0)

            if best_bid <= 0 or best_ask <= 0 or last_price <= 0:
                return {
                    "symbol": symbol,
                    "is_valid": False,
                    "reason": "exchange_filters_rejected",
                    "fetched_at": now,
                }

            mid_price = (best_bid + best_ask) / 2
            snapshot = {
                "symbol": symbol,
                "last_price": last_price,
                "best_bid": best_bid,
                "best_ask": best_ask,
                "spread_pct": (best_ask - best_bid) / mid_price if mid_price > 0 else 1.0,
                "volume_24h": volume_24h,
                "is_valid": True,
                "reason": "ok",
                "fetched_at": now,
            }
            self.spot_snapshot_cache[symbol] = snapshot
            return snapshot
        except Exception as exc:
            logger.info(
                "[REJECT] source=spot category=CRYPTO market=%s reasons=exchange_filters_rejected inputs=%s",
                symbol,
                {"symbol": symbol, "error": str(exc)},
            )
            return {
                "symbol": symbol,
                "is_valid": False,
                "reason": "exchange_filters_rejected",
                "fetched_at": now,
            }

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
        await self.binance_spot_exchange.close()
        await self.futures_exchange.close()
