from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "prune_runtime_data.py"


def _create_runtime_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE decision_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            occurred_at TEXT,
            raw_source_signal TEXT,
            signal_family TEXT,
            action TEXT,
            reason TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE market_aliases (
            alias TEXT PRIMARY KEY,
            market_id TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            last_seen_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE runtime_status_snapshot (
            id INTEGER PRIMARY KEY,
            updated_at TEXT,
            metrics_json TEXT
        )
        """
    )
    cur.execute("CREATE TABLE trades (id INTEGER PRIMARY KEY, status TEXT)")
    cur.execute("CREATE TABLE venue_positions (id INTEGER PRIMARY KEY, status TEXT)")

    cur.executemany(
        "INSERT INTO decision_audit (occurred_at, raw_source_signal, signal_family, action, reason) VALUES (?, ?, ?, ?, ?)",
        [
            ("2026-01-01 00:00:00", "discovery", "discovery", "reject", "route_whale_orderflow_only"),
            ("2026-04-10 00:00:00", "discovery", "discovery", "reject", "route_whale_orderflow_only"),
            ("2026-01-01 00:00:00", "discovery", "discovery", "reject", "market_expired"),
            ("2026-04-10 00:00:00", "discovery", "discovery", "reject", "market_expired"),
            ("2026-01-01 00:00:00", "whale_tracker", "whale", "reject", "market_not_mapped_lazy_lookup_failed"),
            ("2026-04-10 00:00:00", "whale_tracker", "whale", "reject", "market_not_mapped_lazy_lookup_failed"),
            ("2025-01-01 00:00:00", "activity", "activity_orderflow", "decision", "score_ready"),
            ("2026-04-10 00:00:00", "activity", "activity_orderflow", "decision", "score_ready"),
        ],
    )
    cur.executemany(
        "INSERT INTO market_aliases (alias, market_id, active, last_seen_at) VALUES (?, ?, ?, ?)",
        [
            ("alias-old-inactive", "market-1", 0, "2025-01-01 00:00:00"),
            ("alias-new-inactive", "market-2", 0, "2026-04-10 00:00:00"),
            ("alias-old-active", "market-3", 1, "2025-01-01 00:00:00"),
        ],
    )
    cur.execute(
        "INSERT INTO runtime_status_snapshot (id, updated_at, metrics_json) VALUES (1, '2026-04-10 00:00:00', '{}')"
    )
    conn.commit()
    conn.close()


def _count_rows(path: Path, table: str) -> int:
    conn = sqlite3.connect(path)
    try:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    finally:
        conn.close()


def test_prune_runtime_data_dry_run_does_not_delete(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.db"
    _create_runtime_db(db_path)

    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--db-path", str(db_path)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=True,
    )

    assert "mode=dry_run" in result.stdout
    assert "prune_plan" in result.stdout
    assert "discovery_route_only_rejects: 2" in result.stdout
    assert "stale_discovery_rejects: 1" in result.stdout
    assert "stale_orderflow_rejects: 1" in result.stdout
    assert "stale_non_reject_audit: 1" in result.stdout
    assert _count_rows(db_path, "decision_audit") == 8
    assert _count_rows(db_path, "market_aliases") == 3


def test_prune_runtime_data_apply_and_optional_alias_prune(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.db"
    _create_runtime_db(db_path)

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--db-path",
            str(db_path),
            "--apply",
            "--prune-market-aliases",
            "--vacuum",
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=True,
    )

    assert "mode=apply" in result.stdout
    assert "deleted_rows" in result.stdout
    assert "vacuum=running" in result.stdout
    assert "discovery_route_only_rejects: 2" in result.stdout
    assert "stale_discovery_rejects: 1" in result.stdout
    assert "stale_orderflow_rejects: 1" in result.stdout
    assert "stale_non_reject_audit: 1" in result.stdout
    assert "inactive_market_aliases: 1" in result.stdout

    assert _count_rows(db_path, "decision_audit") == 3
    assert _count_rows(db_path, "market_aliases") == 2
    assert _count_rows(db_path, "runtime_status_snapshot") == 1
