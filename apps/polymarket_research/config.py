from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(slots=True)
class PolymarketResearchSettings:
    db_path: str = field(default_factory=lambda: os.getenv("GHOST_TRADER_DB_PATH", "data/ghost_trader.db"))
    discovery_pool_size: int = field(default_factory=lambda: int(os.getenv("POLYMARKET_RESEARCH_DISCOVERY_POOL", "50")))
    shadow_pool_size: int = field(default_factory=lambda: int(os.getenv("POLYMARKET_RESEARCH_SHADOW_POOL", "20")))
    copy_ready_size: int = field(default_factory=lambda: int(os.getenv("POLYMARKET_RESEARCH_COPY_READY_POOL", "5")))
    shadow_window_days: int = field(default_factory=lambda: int(os.getenv("POLYMARKET_RESEARCH_SHADOW_WINDOW_DAYS", "14")))

