from __future__ import annotations

import os
from dataclasses import dataclass, field


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


def _default_db_path() -> str:
    return (
        os.getenv("POLYMARKET_COPY_DB_PATH")
        or os.getenv("GHOST_TRADER_DB_PATH")
        or "data/research_v2.db"
    )


def _default_source_db_path() -> str:
    return (
        os.getenv("POLYMARKET_COPY_SOURCE_DB_PATH")
        or os.getenv("POLYMARKET_RESEARCH_SOURCE_DB_PATH")
        or os.getenv("GHOST_TRADER_DB_PATH")
        or "data/research_v2.db"
    )


@dataclass(slots=True)
class PolymarketCopySettings:
    db_path: str = field(default_factory=_default_db_path)
    source_db_path: str = field(default_factory=_default_source_db_path)
    lookback_days: int = field(default_factory=lambda: _env_int("POLYMARKET_COPY_LOOKBACK_DAYS", 14))
    loop_interval_seconds: int = field(default_factory=lambda: _env_int("POLYMARKET_COPY_LOOP_INTERVAL_SECONDS", 60))
    follower_delay_seconds: int = field(default_factory=lambda: _env_int("POLYMARKET_COPY_DELAY_SECONDS", 90))
    min_trade_size_usd: float = field(default_factory=lambda: _env_float("POLYMARKET_COPY_MIN_TRADE_SIZE_USD", 25.0))
    max_trade_size_usd: float = field(default_factory=lambda: _env_float("POLYMARKET_COPY_MAX_TRADE_SIZE_USD", 50.0))
    wallet_risk_limit_usd: float = field(default_factory=lambda: _env_float("POLYMARKET_COPY_WALLET_RISK_LIMIT_USD", 100.0))
    market_risk_limit_usd: float = field(default_factory=lambda: _env_float("POLYMARKET_COPY_MARKET_RISK_LIMIT_USD", 150.0))
    copy_ready_limit: int = field(default_factory=lambda: _env_int("POLYMARKET_COPY_READY_LIMIT", 5))
