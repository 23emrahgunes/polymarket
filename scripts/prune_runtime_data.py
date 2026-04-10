#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class PruneRule:
    name: str
    table: str
    where_sql: str
    params: tuple[object, ...]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def cutoff_iso(days: int, *, now: datetime | None = None) -> str:
    anchor = now or utc_now()
    return (anchor - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")


def bytes_to_human(num_bytes: int) -> str:
    value = float(num_bytes)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if value < 1024.0 or unit == "TB":
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{num_bytes} B"


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def fetch_count(conn: sqlite3.Connection, table: str, where_sql: str = "", params: tuple[object, ...] = ()) -> int:
    if not table_exists(conn, table):
        return 0
    query = f"SELECT COUNT(*) FROM {table}"
    if where_sql:
        query += f" WHERE {where_sql}"
    return int(conn.execute(query, params).fetchone()[0] or 0)


def db_stats(conn: sqlite3.Connection) -> dict[str, int]:
    page_count = int(conn.execute("PRAGMA page_count").fetchone()[0])
    page_size = int(conn.execute("PRAGMA page_size").fetchone()[0])
    freelist_count = int(conn.execute("PRAGMA freelist_count").fetchone()[0])
    return {
        "page_count": page_count,
        "page_size": page_size,
        "freelist_count": freelist_count,
        "db_bytes": page_count * page_size,
        "free_bytes": freelist_count * page_size,
    }


def build_prune_rules(
    *,
    now: datetime | None = None,
    discovery_route_only_days: int = 7,
    discovery_reject_days: int = 14,
    orderflow_reject_days: int = 30,
    keep_decisions_days: int = 180,
    prune_market_aliases: bool = False,
    market_alias_inactive_days: int = 90,
) -> list[PruneRule]:
    rules = [
        PruneRule(
            name="discovery_route_only_rejects",
            table="decision_audit",
            where_sql=(
                "action = 'reject' "
                "AND raw_source_signal = 'discovery' "
                "AND reason = 'route_whale_orderflow_only' "
                "AND COALESCE(occurred_at, '1970-01-01 00:00:00') < ?"
            ),
            params=(cutoff_iso(discovery_route_only_days, now=now),),
        ),
        PruneRule(
            name="stale_discovery_rejects",
            table="decision_audit",
            where_sql=(
                "action = 'reject' "
                "AND raw_source_signal = 'discovery' "
                "AND COALESCE(reason, '') <> 'route_whale_orderflow_only' "
                "AND COALESCE(occurred_at, '1970-01-01 00:00:00') < ?"
            ),
            params=(cutoff_iso(discovery_reject_days, now=now),),
        ),
        PruneRule(
            name="stale_orderflow_rejects",
            table="decision_audit",
            where_sql=(
                "action = 'reject' "
                "AND signal_family IN ('activity_orderflow', 'whale') "
                "AND COALESCE(occurred_at, '1970-01-01 00:00:00') < ?"
            ),
            params=(cutoff_iso(orderflow_reject_days, now=now),),
        ),
        PruneRule(
            name="stale_non_reject_audit",
            table="decision_audit",
            where_sql=(
                "action <> 'reject' "
                "AND COALESCE(occurred_at, '1970-01-01 00:00:00') < ?"
            ),
            params=(cutoff_iso(keep_decisions_days, now=now),),
        ),
    ]

    if prune_market_aliases:
        rules.append(
            PruneRule(
                name="inactive_market_aliases",
                table="market_aliases",
                where_sql=(
                    "active = 0 "
                    "AND COALESCE(last_seen_at, '1970-01-01 00:00:00') < ?"
                ),
                params=(cutoff_iso(market_alias_inactive_days, now=now),),
            )
        )

    return rules


def summarize_rules(conn: sqlite3.Connection, rules: Iterable[PruneRule]) -> list[tuple[PruneRule, int]]:
    summary: list[tuple[PruneRule, int]] = []
    for rule in rules:
        count = fetch_count(conn, rule.table, rule.where_sql, rule.params)
        summary.append((rule, count))
    return summary


def apply_rules(conn: sqlite3.Connection, rule_counts: Iterable[tuple[PruneRule, int]]) -> dict[str, int]:
    deleted: dict[str, int] = {}
    for rule, _ in rule_counts:
        if not table_exists(conn, rule.table):
            deleted[rule.name] = 0
            continue
        cursor = conn.execute(f"DELETE FROM {rule.table} WHERE {rule.where_sql}", rule.params)
        deleted[rule.name] = int(cursor.rowcount if cursor.rowcount != -1 else 0)
    conn.commit()
    return deleted


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Safely prune large runtime tables with retention-based rules.",
    )
    parser.add_argument(
        "--db-path",
        default=os.getenv("GHOST_TRADER_DB_PATH", "data/ghost_trader.db"),
        help="SQLite DB path. Defaults to GHOST_TRADER_DB_PATH or data/ghost_trader.db.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually delete data. Without this flag the script only reports what would be removed.",
    )
    parser.add_argument(
        "--vacuum",
        action="store_true",
        help="Run VACUUM after apply to compact the DB file.",
    )
    parser.add_argument("--discovery-route-only-days", type=int, default=7)
    parser.add_argument("--discovery-reject-days", type=int, default=14)
    parser.add_argument("--orderflow-reject-days", type=int, default=30)
    parser.add_argument("--keep-decisions-days", type=int, default=180)
    parser.add_argument(
        "--prune-market-aliases",
        action="store_true",
        help="Also delete inactive market_aliases older than --market-alias-inactive-days.",
    )
    parser.add_argument("--market-alias-inactive-days", type=int, default=90)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    db_path = Path(args.db_path)
    if not db_path.exists():
        print(f"SQLite database not found: {db_path}")
        return 1

    conn = sqlite3.connect(db_path)
    try:
        rules = build_prune_rules(
            discovery_route_only_days=args.discovery_route_only_days,
            discovery_reject_days=args.discovery_reject_days,
            orderflow_reject_days=args.orderflow_reject_days,
            keep_decisions_days=args.keep_decisions_days,
            prune_market_aliases=args.prune_market_aliases,
            market_alias_inactive_days=args.market_alias_inactive_days,
        )
        rule_counts = summarize_rules(conn, rules)

        before = db_stats(conn)
        print(f"db_path={db_path}")
        print(f"db_size={before['db_bytes']} ({bytes_to_human(before['db_bytes'])})")
        print(f"free_pages={before['freelist_count']} free_bytes={before['free_bytes']} ({bytes_to_human(before['free_bytes'])})")
        print("table_counts")
        for table in ["decision_audit", "market_aliases", "trades", "venue_positions", "runtime_status_snapshot"]:
            print(f"- {table}: {fetch_count(conn, table)}")

        print("prune_plan")
        total_candidates = 0
        for rule, count in rule_counts:
            total_candidates += count
            print(f"- {rule.name}: {count}")

        if not args.apply:
            print("mode=dry_run")
            print(f"total_candidates={total_candidates}")
            print("Nothing was deleted. Re-run with --apply to execute the plan.")
            return 0

        deleted = apply_rules(conn, rule_counts)
        after_delete = db_stats(conn)
        print("mode=apply")
        print("deleted_rows")
        total_deleted = 0
        for rule_name, count in deleted.items():
            total_deleted += count
            print(f"- {rule_name}: {count}")
        print(f"total_deleted={total_deleted}")

        if args.vacuum:
            print("vacuum=running")
            conn.execute("VACUUM")

        final_stats = db_stats(conn)
        print(f"db_size_after={final_stats['db_bytes']} ({bytes_to_human(final_stats['db_bytes'])})")
        print(f"free_pages_after={final_stats['freelist_count']} free_bytes_after={final_stats['free_bytes']} ({bytes_to_human(final_stats['free_bytes'])})")
        print("table_counts_after")
        for table in ["decision_audit", "market_aliases", "trades", "venue_positions", "runtime_status_snapshot"]:
            print(f"- {table}: {fetch_count(conn, table)}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
