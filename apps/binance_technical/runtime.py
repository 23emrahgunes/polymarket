from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .config import BinanceTechnicalSettings
from .repository import BinanceTechnicalRepository
from .service import BinanceTechnicalService


@dataclass(slots=True)
class BinanceTechnicalRuntime:
    settings: BinanceTechnicalSettings

    def __post_init__(self) -> None:
        repository = BinanceTechnicalRepository(self.settings.db_path)
        self.service = BinanceTechnicalService(self.settings, repository)

    def run_once(self) -> dict[str, Any]:
        return self.service.build_summary()
