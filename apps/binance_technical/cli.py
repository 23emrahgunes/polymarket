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
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("summary", help="Print summary only without running the trading loop.")
    subparsers.add_parser("run-once", help="Run a single paper cycle and then print summary.")
    loop_parser = subparsers.add_parser("run-loop", help="Run the paper loop continuously or for a fixed number of iterations.")
    loop_parser.add_argument("--iterations", type=int, help="Optional number of iterations before stopping.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    settings = BinanceTechnicalSettings()
    if args.db_path:
        settings.db_path = str(args.db_path)

    runtime = BinanceTechnicalRuntime(settings)
    command = args.command or "summary"
    if command == "run-once":
        summary = runtime.run_once()
    elif command == "run-loop":
        summary = runtime.run_loop(iterations=args.iterations)
    else:
        summary = runtime.build_summary()
    print("BINANCE_TECHNICAL_LANE_SUMMARY")
    print(json.dumps(summary, ensure_ascii=True, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
