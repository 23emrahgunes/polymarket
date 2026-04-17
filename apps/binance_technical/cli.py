from __future__ import annotations

import json

from .config import BinanceTechnicalSettings
from .runtime import BinanceTechnicalRuntime


def main() -> int:
    runtime = BinanceTechnicalRuntime(BinanceTechnicalSettings())
    summary = runtime.run_once()
    print("BINANCE_TECHNICAL_LANE_SUMMARY")
    print(json.dumps(summary, ensure_ascii=True, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
