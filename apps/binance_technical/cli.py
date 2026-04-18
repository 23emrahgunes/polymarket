from __future__ import annotations

import argparse
import json
import sys
from typing import Sequence

from .config import BinanceTechnicalSettings
from .runtime import BinanceTechnicalRuntime


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Binance technical lane CLI")
    parser.add_argument(
        "--db-path",
        help="Override SQLite DB path. Defaults to GHOST_TRADER_DB_PATH or data/ghost_trader.db.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    settings = BinanceTechnicalSettings()
    if args.db_path:
        settings.db_path = str(args.db_path)

    runtime = BinanceTechnicalRuntime(settings)
    summary = runtime.run_once()
    print("BINANCE_TECHNICAL_LANE_SUMMARY")
    print(json.dumps(summary, ensure_ascii=True, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
