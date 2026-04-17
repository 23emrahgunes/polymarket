from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .config import PolymarketResearchSettings
from .repository import PolymarketResearchRepository
from .service import PolymarketResearchService


@dataclass(slots=True)
class PolymarketResearchRuntime:
    settings: PolymarketResearchSettings

    def __post_init__(self) -> None:
        repository = PolymarketResearchRepository(self.settings.db_path)
        self.service = PolymarketResearchService(self.settings, repository)

    def run_once(self) -> dict[str, Any]:
        return self.service.build_summary()
