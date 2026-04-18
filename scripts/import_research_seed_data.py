#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any


SOURCE_TABLES = (
    "whale_wallets",
    "whale_stats",
    "whale_wallet_sources",
)


def table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def table_columns(connection: sqlite3.Connection, table_name: str) -> set[str]:
    if not table_exists(connection, table_name):
        return set()
    rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {str(row[1]) for row in rows}


def table_schema_sql(connection: sqlite3.Connection, table_name: str) -> str | None:
    row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    if row is None:
        return None
    sql = row[0]
    return str(sql) if sql is not None else None


def copy_table(source: sqlite3.Connection, target: sqlite3.Connection, table_name: str) -> int:
    schema_sql = table_schema_sql(source, table_name)
    if not schema_sql:
        return 0

    target.execute(f"DROP TABLE IF EXISTS {table_name}")
    target.execute(schema_sql)

    rows = source.execute(f"SELECT * FROM {table_name}").fetchall()
    if not rows:
        return 0

    placeholders = ", ".join("?" for _ in rows[0].keys())
    target.executemany(
        f"INSERT INTO {table_name} VALUES ({placeholders})",
        [tuple(row) for row in rows],
    )
    return len(rows)


def copy_polymarket_trade_seed(source: sqlite3.Connection, target: sqlite3.Connection) -> int:
    table_name = "trades"
    schema_sql = table_schema_sql(source, table_name)
    if not schema_sql:
        return 0

    source_columns = table_columns(source, table_name)
    target.execute(f"DROP TABLE IF EXISTS {table_name}")
    target.execute(schema_sql)

    where_clauses = [
        "TRIM(COALESCE(whale_address, '')) != ''",
        "status LIKE 'CLOSED%'",
    ]
    if "venue" in source_columns:
        where_clauses.append("LOWER(COALESCE(venue, 'polymarket')) = 'polymarket'")

    rows = source.execute(
        f"SELECT * FROM {table_name} WHERE {' AND '.join(where_clauses)}"
    ).fetchall()
    if not rows:
        return 0

    placeholders = ", ".join("?" for _ in rows[0].keys())
    target.executemany(
        f"INSERT INTO {table_name} VALUES ({placeholders})",
        [tuple(row) for row in rows],
    )
    return len(rows)


def import_research_seed_data(source_db: str, target_db: str) -> dict[str, Any]:
    source_path = Path(source_db)
    target_path = Path(target_db)
    target_path.parent.mkdir(parents=True, exist_ok=True)

    result: dict[str, Any] = {
        "source_db": str(source_path),
        "target_db": str(target_path),
        "copied_tables": {},
        "skipped": False,
        "reason": None,
    }

    if not source_path.exists():
        result["skipped"] = True
        result["reason"] = "source_db_missing"
        return result

    source = sqlite3.connect(str(source_path))
    source.row_factory = sqlite3.Row
    target = sqlite3.connect(str(target_path))
    target.row_factory = sqlite3.Row

    try:
        for table_name in SOURCE_TABLES:
            if table_exists(source, table_name):
                result["copied_tables"][table_name] = copy_table(source, target, table_name)

        if table_exists(source, "trades"):
            result["copied_tables"]["trades"] = copy_polymarket_trade_seed(source, target)

        target.commit()
        return result
    finally:
        target.close()
        source.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Import research seed data from a legacy SQLite database.")
    parser.add_argument("--source-db", required=True, help="Legacy SQLite database path")
    parser.add_argument("--target-db", required=True, help="Research-v2 SQLite database path")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = import_research_seed_data(args.source_db, args.target_db)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
