from __future__ import annotations

import argparse
import json
import sys
from typing import Sequence

from .config import PolymarketCopySettings
from .runtime import PolymarketCopyRuntime


def _add_db_path_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--db-path",
        help="Override copy lane SQLite DB path. Defaults to POLYMARKET_COPY_DB_PATH or GHOST_TRADER_DB_PATH.",
    )
    parser.add_argument(
        "--source-db-path",
        help="Override legacy/source SQLite DB path for historical source trades.",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Polymarket paper copy lane CLI")
    _add_db_path_arguments(parser)
    subparsers = parser.add_subparsers(dest="command")
    summary_parser = subparsers.add_parser("summary", help="Print current copy lane summary without mutating state.")
    _add_db_path_arguments(summary_parser)
    run_once_parser = subparsers.add_parser("run-once", help="Run a single copy sync cycle and print the resulting summary.")
    _add_db_path_arguments(run_once_parser)
    loop_parser = subparsers.add_parser("run-loop", help="Run the copy sync loop continuously or for a fixed number of iterations.")
    _add_db_path_arguments(loop_parser)
    loop_parser.add_argument("--iterations", type=int, help="Optional number of iterations before stopping.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    raw_args = list(sys.argv[1:] if argv is None else argv)
    if not raw_args:
        raw_args = ["summary"]
    args = parser.parse_args(raw_args)
    settings = PolymarketCopySettings()
    if getattr(args, "db_path", None):
        settings.db_path = str(args.db_path)
    if getattr(args, "source_db_path", None):
        settings.source_db_path = str(args.source_db_path)

    runtime = PolymarketCopyRuntime(settings)
    command = args.command or "summary"
    if command == "run-once":
        summary = runtime.run_once()
    elif command == "run-loop":
        summary = runtime.run_loop(iterations=args.iterations)
    else:
        summary = runtime.build_summary()
    print("POLYMARKET_COPY_LANE_SUMMARY")
    print(json.dumps(summary, ensure_ascii=True, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
