from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class PolymarketResearchRepository:
    def __init__(self, db_path: str):
        self.db_path = db_path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
        finally:
            connection.close()

    def ensure_tables(self) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS polymarket_shadow_actions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    wallet_address TEXT NOT NULL,
                    market_id TEXT NOT NULL,
                    category TEXT,
                    source_type TEXT,
                    action_type TEXT NOT NULL DEFAULT 'shadow_trade',
                    raw_wallet_pnl REAL DEFAULT 0,
                    shadow_pnl REAL DEFAULT 0,
                    shadow_edge REAL DEFAULT 0,
                    drawdown_pct REAL DEFAULT 0,
                    delayed_seconds INTEGER DEFAULT 0,
                    notes_json TEXT,
                    opened_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    closed_at DATETIME,
                    status TEXT NOT NULL DEFAULT 'OPEN'
                )
                """
            )
            connection.commit()

    def fetch_candidate_wallets(self, limit: int) -> list[sqlite3.Row]:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                WITH trade_stats AS (
                    SELECT
                        LOWER(COALESCE(whale_address, '')) AS address,
                        COUNT(CASE WHEN status LIKE 'CLOSED%' THEN 1 END) AS closed_trades,
                        ROUND(COALESCE(SUM(COALESCE(pnl, 0)), 0), 4) AS realized_pnl,
                        COUNT(DISTINCT substr(COALESCE(closed_at, timestamp), 1, 10)) AS active_days,
                        COUNT(*) AS total_trade_rows,
                        SUM(CASE WHEN UPPER(COALESCE(category, '')) = 'CRYPTO' THEN 1 ELSE 0 END) AS crypto_trade_rows,
                        MAX(COALESCE(category, '')) AS specialization_hint
                    FROM trades
                    WHERE TRIM(COALESCE(whale_address, '')) != ''
                    GROUP BY LOWER(COALESCE(whale_address, ''))
                )
                SELECT
                    whale_wallets.address,
                    whale_wallets.source_type,
                    whale_wallets.discovery_score,
                    whale_wallets.event_count_24h,
                    whale_wallets.last_event_amount,
                    whale_wallets.last_event_category,
                    whale_wallets.last_seen_at,
                    COALESCE(whale_stats.trust_score, 0.5) AS trust_score,
                    COALESCE(whale_stats.total_trades, 0) AS closed_trade_count,
                    ROUND(COALESCE(whale_stats.total_pnl, 0), 4) AS realized_pnl,
                    COALESCE(trade_stats.active_days, 0) AS active_days,
                    COALESCE(trade_stats.total_trade_rows, 0) AS total_trade_rows,
                    COALESCE(trade_stats.crypto_trade_rows, 0) AS crypto_trade_rows,
                    COALESCE(trade_stats.specialization_hint, whale_wallets.last_event_category, 'UNKNOWN') AS specialization_hint
                FROM whale_wallets
                LEFT JOIN whale_stats ON LOWER(whale_stats.address) = LOWER(whale_wallets.address)
                LEFT JOIN trade_stats ON trade_stats.address = LOWER(whale_wallets.address)
                WHERE whale_wallets.enabled = 1
                ORDER BY whale_wallets.discovery_score DESC, whale_wallets.last_event_amount DESC, whale_wallets.last_seen_at DESC
                LIMIT ?
                """,
                (max(limit, 1),),
            )
            return cursor.fetchall()

    def fetch_shadow_action_rows(self, window_days: int) -> list[sqlite3.Row]:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                SELECT
                    wallet_address,
                    market_id,
                    category,
                    source_type,
                    action_type,
                    raw_wallet_pnl,
                    shadow_pnl,
                    shadow_edge,
                    drawdown_pct,
                    delayed_seconds,
                    status,
                    opened_at,
                    closed_at,
                    notes_json
                FROM polymarket_shadow_actions
                WHERE opened_at >= datetime('now', ?)
                ORDER BY COALESCE(closed_at, opened_at) DESC, id DESC
                """,
                (f"-{max(window_days, 1)} days",),
            )
            return cursor.fetchall()

    def fetch_recent_shadow_actions(self, limit: int) -> list[sqlite3.Row]:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                SELECT
                    wallet_address,
                    market_id,
                    category,
                    source_type,
                    action_type,
                    raw_wallet_pnl,
                    shadow_pnl,
                    shadow_edge,
                    drawdown_pct,
                    delayed_seconds,
                    status,
                    opened_at,
                    closed_at,
                    notes_json
                FROM polymarket_shadow_actions
                ORDER BY COALESCE(closed_at, opened_at) DESC, id DESC
                LIMIT ?
                """,
                (max(limit, 1),),
            )
            return cursor.fetchall()

    def seed_shadow_action(
        self,
        *,
        wallet_address: str,
        market_id: str,
        category: str,
        source_type: str,
        shadow_pnl: float,
        shadow_edge: float,
        drawdown_pct: float = 0.0,
        raw_wallet_pnl: float = 0.0,
        delayed_seconds: int = 0,
        status: str = "CLOSED",
        notes: dict | None = None,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO polymarket_shadow_actions (
                    wallet_address,
                    market_id,
                    category,
                    source_type,
                    raw_wallet_pnl,
                    shadow_pnl,
                    shadow_edge,
                    drawdown_pct,
                    delayed_seconds,
                    notes_json,
                    status,
                    opened_at,
                    closed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (
                    wallet_address,
                    market_id,
                    category,
                    source_type,
                    raw_wallet_pnl,
                    shadow_pnl,
                    shadow_edge,
                    drawdown_pct,
                    delayed_seconds,
                    json.dumps(notes or {}, ensure_ascii=True),
                    status,
                ),
            )
            connection.commit()
