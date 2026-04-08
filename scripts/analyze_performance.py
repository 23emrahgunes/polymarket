#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.performance_evaluator import analyze_dataset, load_performance_dataset, write_analysis_outputs


DEFAULT_DB_PATHS = [
    "data/ghost_trader.db",
    "data/runtime_verification.db",
    "data/runtime_crypto_dual_venue.db",
    "data/runtime_crypto_triple_venue.db",
]


def _existing_default_db_paths() -> list[str]:
    return [path for path in DEFAULT_DB_PATHS if Path(path).exists()]


def analyze_performance(
    db_paths: list[str] | None = None,
    output_dir: str = "reports/performance",
    include_synthetic: bool = False,
) -> dict:
    resolved_db_paths = db_paths or _existing_default_db_paths() or [os.getenv("GHOST_TRADER_DB_PATH", "data/ghost_trader.db")]
    dataset = load_performance_dataset(resolved_db_paths)
    summary = analyze_dataset(dataset, include_synthetic=include_synthetic)
    outputs = write_analysis_outputs(summary, output_dir)
    summary["report_files"] = outputs
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze Ghost Trader PAPER performance.")
    parser.add_argument("--db-path", action="append", dest="db_paths", help="SQLite DB path to include. Repeat for multiple DBs.")
    parser.add_argument("--output-dir", default="reports/performance", help="Directory for JSON/CSV/Markdown outputs.")
    parser.add_argument("--include-synthetic", action="store_true", help="Include synthetic verification samples in the core metrics.")
    args = parser.parse_args()

    summary = analyze_performance(
        db_paths=args.db_paths,
        output_dir=args.output_dir,
        include_synthetic=args.include_synthetic,
    )
    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
