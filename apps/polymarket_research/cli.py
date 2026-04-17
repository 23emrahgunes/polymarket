from __future__ import annotations

import json

from .config import PolymarketResearchSettings
from .runtime import PolymarketResearchRuntime


def main() -> int:
    runtime = PolymarketResearchRuntime(PolymarketResearchSettings())
    summary = runtime.run_once()
    sections = [
        "DISCOVERY_SOURCE_SUMMARY",
        "SHADOW_PROMOTION_SUMMARY",
        "WALLET_PROVENANCE_SUMMARY",
    ]
    for name in sections:
        print(name)
        print(json.dumps(summary.get(name.lower(), {}), ensure_ascii=True, indent=2, sort_keys=True))
    print("POLYMARKET_RESEARCH_SUMMARY_JSON")
    print(json.dumps(summary, ensure_ascii=True, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
