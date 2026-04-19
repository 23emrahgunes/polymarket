from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


def _safe_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@dataclass(slots=True)
class MarketSnapshot:
    symbol: str
    venue: str
    best_bid: float | None
    best_ask: float | None
    last_price: float | None
    mark_price: float | None
    mid_price: float | None
    spread_pct: float | None
    volume_24h: float
    snapshot_quality: str
    bid_source: str | None
    ask_source: str | None
    spread_source: str | None
    orderbook_fallback_used: bool
    orderbook_repriced: bool
    raw_ticker_bid: float | None
    raw_ticker_ask: float | None
    raw_info_bid: float | None
    raw_info_ask: float | None
    mark_mid_gap_pct: float | None
    last_mid_gap_pct: float | None
    is_valid: bool
    invalid_reason: str | None = None


@dataclass(slots=True)
class MarketFrame:
    symbol: str
    venue: str
    timeframe: str
    closes: list[float]
    volumes: list[float]
    snapshot: MarketSnapshot


class BinanceTechnicalMarketDataProvider(Protocol):
    def fetch_market_frame(self, symbol: str, venue: str, timeframe: str, ohlcv_limit: int) -> MarketFrame:
        ...


class CCXTBinanceMarketDataProvider:
    def __init__(self) -> None:
        self._spot_exchange = None
        self._futures_exchange = None

    def fetch_market_frame(self, symbol: str, venue: str, timeframe: str, ohlcv_limit: int) -> MarketFrame:
        exchange = self._exchange_for_venue(venue)
        market_symbol = self._market_symbol(symbol, venue)
        candles = exchange.fetch_ohlcv(market_symbol, timeframe=timeframe, limit=max(ohlcv_limit, 60))
        closes = [float(row[4]) for row in candles if len(row) >= 6 and _safe_float(row[4]) is not None]
        volumes = [float(row[5]) for row in candles if len(row) >= 6 and _safe_float(row[5]) is not None]
        ticker = exchange.fetch_ticker(market_symbol)
        snapshot = self._build_snapshot(exchange, market_symbol, ticker, venue)
        return MarketFrame(
            symbol=symbol.upper(),
            venue=venue,
            timeframe=timeframe,
            closes=closes,
            volumes=volumes,
            snapshot=snapshot,
        )

    def _exchange_for_venue(self, venue: str):
        import ccxt  # lazy import

        if venue == "binance_futures":
            if self._futures_exchange is None:
                self._futures_exchange = ccxt.binanceusdm(
                    {
                        "enableRateLimit": True,
                        "options": {"defaultType": "future"},
                    }
                )
            return self._futures_exchange

        if self._spot_exchange is None:
            self._spot_exchange = ccxt.binance(
                {
                    "enableRateLimit": True,
                    "options": {"defaultType": "spot"},
                }
            )
        return self._spot_exchange

    @staticmethod
    def _market_symbol(symbol: str, venue: str) -> str:
        upper = symbol.upper()
        if venue == "binance_futures":
            return f"{upper}/USDT:USDT"
        return f"{upper}/USDT"

    def _build_snapshot(self, exchange: Any, market_symbol: str, ticker: dict[str, Any], venue: str) -> MarketSnapshot:
        info = ticker.get("info") if isinstance(ticker, dict) else {}
        info = info if isinstance(info, dict) else {}

        raw_ticker_bid = _safe_float(ticker.get("bid"))
        raw_ticker_ask = _safe_float(ticker.get("ask"))
        raw_info_bid = _safe_float(info.get("bidPrice"))
        raw_info_ask = _safe_float(info.get("askPrice"))
        last_price = _safe_float(ticker.get("last")) or _safe_float(info.get("lastPrice"))
        mark_price = _safe_float(info.get("markPrice")) or last_price

        volume_24h = (
            _safe_float(ticker.get("quoteVolume"))
            or _safe_float(info.get("quoteVolume"))
            or (
                (_safe_float(ticker.get("baseVolume")) or _safe_float(info.get("volume")) or 0.0)
                * (last_price or 0.0)
            )
        )

        best_bid = raw_ticker_bid if raw_ticker_bid and raw_ticker_bid > 0 else raw_info_bid
        best_ask = raw_ticker_ask if raw_ticker_ask and raw_ticker_ask > 0 else raw_info_ask
        bid_source = (
            "ticker.bid"
            if raw_ticker_bid and raw_ticker_bid > 0
            else ("info.bidPrice" if raw_info_bid and raw_info_bid > 0 else None)
        )
        ask_source = (
            "ticker.ask"
            if raw_ticker_ask and raw_ticker_ask > 0
            else ("info.askPrice" if raw_info_ask and raw_info_ask > 0 else None)
        )
        if bid_source == "ticker.bid" and ask_source == "ticker.ask":
            snapshot_quality = "trusted_ticker_book"
        else:
            snapshot_quality = "trusted_info_book"
        orderbook_fallback_used = False
        orderbook_repriced = False

        initial_valid = best_bid is not None and best_ask is not None and best_bid > 0 and best_ask > 0 and best_ask >= best_bid
        initial_mid = ((best_bid + best_ask) / 2.0) if initial_valid else None
        initial_spread_pct = ((best_ask - best_bid) / initial_mid) if initial_valid and initial_mid else None

        needs_orderbook = (
            not initial_valid
            or initial_spread_pct is None
            or initial_spread_pct > 0.018
        )

        if needs_orderbook:
            orderbook_fallback_used = True
            orderbook = exchange.fetch_order_book(market_symbol, limit=5)
            bids = orderbook.get("bids") if isinstance(orderbook, dict) else None
            asks = orderbook.get("asks") if isinstance(orderbook, dict) else None
            book_bid = _safe_float(bids[0][0]) if bids else None
            book_ask = _safe_float(asks[0][0]) if asks else None
            book_valid = book_bid is not None and book_ask is not None and book_bid > 0 and book_ask > 0 and book_ask >= book_bid
            if book_valid:
                book_mid = (book_bid + book_ask) / 2.0
                book_spread_pct = (book_ask - book_bid) / book_mid if book_mid else None
                if not initial_valid or (book_spread_pct is not None and initial_spread_pct is not None and book_spread_pct < initial_spread_pct):
                    orderbook_repriced = bool(initial_valid)
                    best_bid = book_bid
                    best_ask = book_ask
                    bid_source = "orderbook.bid"
                    ask_source = "orderbook.ask"
                    snapshot_quality = "trusted_orderbook_book"
            elif initial_valid and best_ask is not None and best_bid is not None and best_ask < best_bid:
                return self._invalid_snapshot(
                    symbol=market_symbol,
                    venue=venue,
                    last_price=last_price,
                    mark_price=mark_price,
                    volume_24h=volume_24h,
                    raw_ticker_bid=raw_ticker_bid,
                    raw_ticker_ask=raw_ticker_ask,
                    raw_info_bid=raw_info_bid,
                    raw_info_ask=raw_info_ask,
                    reason="futures_snapshot_untrusted" if venue == "binance_futures" else "spot_snapshot_untrusted",
                )

        if best_bid is None or best_ask is None or best_bid <= 0 or best_ask <= 0:
            return self._invalid_snapshot(
                symbol=market_symbol,
                venue=venue,
                last_price=last_price,
                mark_price=mark_price,
                volume_24h=volume_24h,
                raw_ticker_bid=raw_ticker_bid,
                raw_ticker_ask=raw_ticker_ask,
                raw_info_bid=raw_info_bid,
                raw_info_ask=raw_info_ask,
                reason="futures_bid_ask_missing" if venue == "binance_futures" else "spot_bid_ask_missing",
            )

        if best_ask < best_bid:
            return self._invalid_snapshot(
                symbol=market_symbol,
                venue=venue,
                last_price=last_price,
                mark_price=mark_price,
                volume_24h=volume_24h,
                raw_ticker_bid=raw_ticker_bid,
                raw_ticker_ask=raw_ticker_ask,
                raw_info_bid=raw_info_bid,
                raw_info_ask=raw_info_ask,
                reason="futures_snapshot_untrusted" if venue == "binance_futures" else "spot_snapshot_untrusted",
            )

        mid_price = (best_bid + best_ask) / 2.0
        spread_pct = (best_ask - best_bid) / mid_price if mid_price else None
        if bid_source == "orderbook.bid" or ask_source == "orderbook.ask":
            spread_source = "orderbook_book"
        elif bid_source == "info.bidPrice" or ask_source == "info.askPrice":
            spread_source = "info_book"
        else:
            spread_source = "ticker_book"

        mark_mid_gap_pct = None
        if mark_price and mid_price:
            mark_mid_gap_pct = abs(mark_price - mid_price) / mid_price

        last_mid_gap_pct = None
        if last_price and mid_price:
            last_mid_gap_pct = abs(last_price - mid_price) / mid_price

        return MarketSnapshot(
            symbol=market_symbol,
            venue=venue,
            best_bid=best_bid,
            best_ask=best_ask,
            last_price=last_price,
            mark_price=mark_price,
            mid_price=mid_price,
            spread_pct=spread_pct,
            volume_24h=float(volume_24h or 0.0),
            snapshot_quality=snapshot_quality,
            bid_source=bid_source,
            ask_source=ask_source,
            spread_source=spread_source,
            orderbook_fallback_used=orderbook_fallback_used,
            orderbook_repriced=orderbook_repriced,
            raw_ticker_bid=raw_ticker_bid,
            raw_ticker_ask=raw_ticker_ask,
            raw_info_bid=raw_info_bid,
            raw_info_ask=raw_info_ask,
            mark_mid_gap_pct=mark_mid_gap_pct,
            last_mid_gap_pct=last_mid_gap_pct,
            is_valid=True,
            invalid_reason=None,
        )

    @staticmethod
    def _invalid_snapshot(
        *,
        symbol: str,
        venue: str,
        last_price: float | None,
        mark_price: float | None,
        volume_24h: float | None,
        raw_ticker_bid: float | None,
        raw_ticker_ask: float | None,
        raw_info_bid: float | None,
        raw_info_ask: float | None,
        reason: str,
    ) -> MarketSnapshot:
        return MarketSnapshot(
            symbol=symbol,
            venue=venue,
            best_bid=None,
            best_ask=None,
            last_price=last_price,
            mark_price=mark_price,
            mid_price=None,
            spread_pct=None,
            volume_24h=float(volume_24h or 0.0),
            snapshot_quality="invalid_missing_bid_ask",
            bid_source=None,
            ask_source=None,
            spread_source=None,
            orderbook_fallback_used=False,
            orderbook_repriced=False,
            raw_ticker_bid=raw_ticker_bid,
            raw_ticker_ask=raw_ticker_ask,
            raw_info_bid=raw_info_bid,
            raw_info_ask=raw_info_ask,
            mark_mid_gap_pct=None,
            last_mid_gap_pct=None,
            is_valid=False,
            invalid_reason=reason,
        )
