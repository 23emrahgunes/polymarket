from __future__ import annotations

import json

from .config import PolymarketResearchSettings
from .runtime import PolymarketResearchRuntime


def main() -> int:
    runtime = PolymarketResearchRuntime(PolymarketResearchSettings())
    summary = runtime.run_once()
    print("POLYMARKET_RESEARCH_SUMMARY")
    print(json.dumps(summary, ensure_ascii=True, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
