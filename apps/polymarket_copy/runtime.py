from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from .config import PolymarketCopySettings
from .repository import PolymarketCopyRepository
from .service import PolymarketCopyService


@dataclass(slots=True)
class PolymarketCopyRuntime:
    settings: PolymarketCopySettings
    repository: PolymarketCopyRepository | None = None
    service: PolymarketCopyService | None = None

    def __post_init__(self) -> None:
        if self.repository is None:
            self.repository = PolymarketCopyRepository(
                self.settings.db_path,
                self.settings.source_db_path,
            )
        if self.service is None:
            self.service = PolymarketCopyService(self.settings, self.repository)

    def build_summary(self) -> dict[str, Any]:
        assert self.service is not None
        return self.service.build_summary()

    def run_once(self) -> dict[str, Any]:
        assert self.service is not None
        self.service.sync_copy_actions()
        return self.service.build_summary()

    def run_acceptance(self) -> dict[str, Any]:
        assert self.service is not None
        return self.service.run_acceptance_fixture()

    def run_loop(self, iterations: int | None = None) -> dict[str, Any]:
        final_summary: dict[str, Any] = {}
        completed = 0
        while iterations is None or completed < iterations:
            final_summary = self.run_once()
            completed += 1
            if iterations is not None and completed >= iterations:
                break
            time.sleep(max(self.settings.loop_interval_seconds, 1))
        return final_summary
