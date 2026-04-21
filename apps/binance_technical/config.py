from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _parse_symbols(raw: str | None) -> list[str]:
    if raw is None or raw.strip() == "":
        return ["BTC", "ETH", "SOL"]
    symbols = [item.strip().upper() for item in raw.split(",") if item.strip()]
    return symbols or ["BTC", "ETH", "SOL"]


@dataclass(slots=True)
class BinanceTechnicalSettings:
    db_path: str = field(default_factory=lambda: os.getenv("GHOST_TRADER_DB_PATH", "data/binance_technical_v2.db"))
    fresh_window_days: int = field(default_factory=lambda: _env_int("BINANCE_TECHNICAL_FRESH_WINDOW_DAYS", 7))
    symbols: list[str] = field(default_factory=lambda: _parse_symbols(os.getenv("BINANCE_TECHNICAL_SYMBOLS")))
    timeframe: str = field(default_factory=lambda: os.getenv("BINANCE_TECHNICAL_TIMEFRAME", "5m"))
    ohlcv_limit: int = field(default_factory=lambda: _env_int("BINANCE_TECHNICAL_OHLCV_LIMIT", 180))
    loop_interval_seconds: int = field(default_factory=lambda: _env_int("BINANCE_TECHNICAL_LOOP_INTERVAL_SECONDS", 60))
    score_threshold: float = field(default_factory=lambda: _env_float("BINANCE_TECHNICAL_SCORE_THRESHOLD", 0.62))
    fresh_only: bool = field(default_factory=lambda: _env_bool("BINANCE_TECHNICAL_FRESH_ONLY", True))
    paper_recovery_enabled: bool = field(default_factory=lambda: _env_bool("BINANCE_TECHNICAL_PAPER_ENABLED", True))
    force_sample_enabled: bool = field(default_factory=lambda: _env_bool("BINANCE_TECHNICAL_FORCE_SAMPLE", True))
    force_min_score: float = field(default_factory=lambda: _env_float("BINANCE_TECHNICAL_FORCE_MIN_SCORE", 0.50))
    force_cooldown_minutes: float = field(default_factory=lambda: _env_float("BINANCE_TECHNICAL_FORCE_COOLDOWN_MINUTES", 30.0))
    futures_enabled: bool = field(default_factory=lambda: _env_bool("BINANCE_FUTURES_ENABLED", True))
    spot_enabled: bool = field(default_factory=lambda: _env_bool("BINANCE_SPOT_ENABLED", False))
    max_open_positions: int = field(default_factory=lambda: _env_int("BINANCE_TECHNICAL_MAX_OPEN_POSITIONS", 5))
    max_position_usd: float = field(default_factory=lambda: _env_float("BINANCE_TECHNICAL_MAX_POSITION_USD", 450.0))
    max_order_usd: float = field(default_factory=lambda: _env_float("BINANCE_TECHNICAL_MAX_ORDER_USD", 100.0))
    min_trade_size_usd: float = field(default_factory=lambda: _env_float("BINANCE_TECHNICAL_MIN_TRADE_SIZE_USD", 25.0))
    stop_loss_pct: float = field(default_factory=lambda: _env_float("BINANCE_TECHNICAL_STOP_LOSS_PCT", 0.03))
    take_profit_pct: float = field(default_factory=lambda: _env_float("BINANCE_TECHNICAL_TAKE_PROFIT_PCT", 0.06))

    @property
    def enabled_venues(self) -> list[str]:
        venues: list[str] = []
        if self.futures_enabled:
            venues.append("binance_futures")
        if self.spot_enabled:
            venues.append("binance_spot")
        return venues
