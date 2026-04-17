from __future__ import annotations

import os
from dataclasses import dataclass, field


def _parse_symbols(raw: str | None) -> list[str]:
    if raw is None or raw.strip() == "":
        return ["BTC", "ETH", "SOL"]
    symbols = [item.strip().upper() for item in raw.split(",") if item.strip()]
    return symbols or ["BTC", "ETH", "SOL"]


@dataclass(slots=True)
class BinanceTechnicalSettings:
    db_path: str = field(default_factory=lambda: os.getenv("GHOST_TRADER_DB_PATH", "data/ghost_trader.db"))
    fresh_window_days: int = field(default_factory=lambda: int(os.getenv("BINANCE_TECHNICAL_FRESH_WINDOW_DAYS", "7")))
    symbols: list[str] = field(default_factory=lambda: _parse_symbols(os.getenv("BINANCE_TECHNICAL_SYMBOLS")))
