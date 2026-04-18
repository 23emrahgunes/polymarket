from __future__ import annotations

import sqlite3
from pathlib import Path

from scripts.import_research_seed_data import import_research_seed_data


def _create_source_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE whale_wallets (address TEXT PRIMARY KEY, source_type TEXT, enabled INTEGER, discovery_score REAL)"
    )
    conn.execute(
        "CREATE TABLE whale_stats (address TEXT PRIMARY KEY, trust_score REAL, total_trades INTEGER, total_pnl REAL)"
    )
    conn.execute(
        "CREATE TABLE whale_wallet_sources (address TEXT, source_type TEXT, last_seen_at TEXT)"
    )
    conn.execute(
        """
        CREATE TABLE trades (
            id INTEGER PRIMARY KEY,
            whale_address TEXT,
            venue TEXT,
            status TEXT,
            pnl REAL
        )
        """
    )
    conn.execute("INSERT INTO whale_wallets VALUES ('0xaaa', 'leaderboard', 1, 0.91)")
    conn.execute("INSERT INTO whale_stats VALUES ('0xaaa', 0.7, 4, 12.5)")
    conn.execute("INSERT INTO whale_wallet_sources VALUES ('0xaaa', 'leaderboard', '2026-04-18 10:00:00')")
    conn.executemany(
        "INSERT INTO trades VALUES (?, ?, ?, ?, ?)",
        [
            (1, '0xaaa', 'polymarket', 'CLOSED_WIN', 10.0),
            (2, '0xaaa', 'binance_futures', 'CLOSED_WIN', 20.0),
            (3, '0xaaa', 'polymarket', 'OPEN', 0.0),
            (4, '', 'polymarket', 'CLOSED_WIN', 4.0),
        ],
    )
    conn.commit()
    conn.close()


def _create_target_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE polymarket_research_watchlist (
            id INTEGER PRIMARY KEY,
            display_name TEXT NOT NULL
        )
        """
    )
    conn.execute("INSERT INTO polymarket_research_watchlist VALUES (1, 'ohanism')")
    conn.commit()
    conn.close()


def test_import_research_seed_data_copies_only_research_seed_tables(tmp_path: Path) -> None:
    source_db = tmp_path / "legacy.db"
    target_db = tmp_path / "research_v2.db"
    _create_source_db(source_db)
    _create_target_db(target_db)

    result = import_research_seed_data(str(source_db), str(target_db))

    assert result["skipped"] is False
    assert result["copied_tables"]["whale_wallets"] == 1
    assert result["copied_tables"]["whale_stats"] == 1
    assert result["copied_tables"]["whale_wallet_sources"] == 1
    assert result["copied_tables"]["trades"] == 1

    conn = sqlite3.connect(target_db)
    try:
        trade_rows = conn.execute("SELECT venue, status, whale_address FROM trades").fetchall()
        assert trade_rows == [("polymarket", "CLOSED_WIN", "0xaaa")]
        watchlist_rows = conn.execute(
            "SELECT display_name FROM polymarket_research_watchlist"
        ).fetchall()
        assert watchlist_rows == [("ohanism",)]
        wallet_rows = conn.execute("SELECT address FROM whale_wallets").fetchall()
        assert wallet_rows == [("0xaaa",)]
    finally:
        conn.close()


def test_import_research_seed_data_skips_missing_source_db(tmp_path: Path) -> None:
    source_db = tmp_path / "missing.db"
    target_db = tmp_path / "research_v2.db"

    result = import_research_seed_data(str(source_db), str(target_db))

    assert result["skipped"] is True
    assert result["reason"] == "source_db_missing"
