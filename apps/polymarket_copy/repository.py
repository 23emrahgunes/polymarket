from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Sequence


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%d %H:%M:%S")


def _json_dumps(value: dict[str, Any] | None) -> str:
    return json.dumps(value or {}, ensure_ascii=True, sort_keys=True)


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    row = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1",
        (table,),
    ).fetchone()
    return row is not None


def _column_names(connection: sqlite3.Connection, table: str) -> set[str]:
    if not _table_exists(connection, table):
        return set()
    return {str(row["name"]) for row in connection.execute(f"PRAGMA table_info({table})")}


def _value_expr(columns: set[str], column: str, default_sql: str) -> str:
    return column if column in columns else default_sql


class PolymarketCopyRepository:
    def __init__(self, db_path: str, source_db_path: str | None = None):
        self.db_path = db_path
        self.source_db_path = source_db_path or db_path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def connect_source(self) -> Iterator[sqlite3.Connection]:
        source_path = self.source_db_path or self.db_path
        if not Path(source_path).exists():
            source_path = self.db_path
        Path(source_path).parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(source_path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
        finally:
            connection.close()

    def ensure_tables(self) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS polymarket_copy_actions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_trade_key TEXT NOT NULL,
                    wallet_address TEXT NOT NULL,
                    market_id TEXT NOT NULL,
                    category TEXT NOT NULL DEFAULT 'UNKNOWN',
                    action_type TEXT NOT NULL,
                    reason TEXT NOT NULL DEFAULT '',
                    source_status TEXT NOT NULL DEFAULT '',
                    side TEXT NOT NULL DEFAULT '',
                    source_notional_usd REAL NOT NULL DEFAULT 0,
                    follower_notional_usd REAL NOT NULL DEFAULT 0,
                    source_pnl REAL NOT NULL DEFAULT 0,
                    follower_pnl REAL NOT NULL DEFAULT 0,
                    delayed_seconds INTEGER NOT NULL DEFAULT 0,
                    source_opened_at TEXT,
                    source_closed_at TEXT,
                    executed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    notes_json TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS polymarket_copy_actions_unique
                ON polymarket_copy_actions (source_trade_key, action_type)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS polymarket_copy_positions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_trade_key TEXT NOT NULL UNIQUE,
                    wallet_address TEXT NOT NULL,
                    market_id TEXT NOT NULL,
                    category TEXT NOT NULL DEFAULT 'UNKNOWN',
                    side TEXT NOT NULL DEFAULT '',
                    source_notional_usd REAL NOT NULL DEFAULT 0,
                    follower_notional_usd REAL NOT NULL DEFAULT 0,
                    source_pnl REAL NOT NULL DEFAULT 0,
                    follower_pnl REAL NOT NULL DEFAULT 0,
                    source_status TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'OPEN',
                    source_opened_at TEXT,
                    source_closed_at TEXT,
                    opened_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    closed_at TEXT,
                    notes_json TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            connection.commit()

    def fetch_copy_ready_wallets(self, limit: int | None = None) -> list[sqlite3.Row]:
        with self.connect() as connection:
            if not _table_exists(connection, "polymarket_research_wallets"):
                return []
            wallet_columns = _column_names(connection, "polymarket_research_wallets")
            copy_ready_expr = _value_expr(wallet_columns, "copy_ready_gate_status", "'blocked'")
            watchlist_mode_expr = _value_expr(wallet_columns, "watchlist_mode", "''")
            watchlist_status_expr = _value_expr(wallet_columns, "watchlist_status", "''")
            identity_status_expr = _value_expr(wallet_columns, "identity_resolution_status", "''")
            evidence_expr = _value_expr(
                wallet_columns,
                "historical_trade_evidence_status",
                "'no_historical_evidence'",
            )
            pilot_copy_expr = _value_expr(wallet_columns, "pilot_copy_gate_status", "'blocked'")
            pilot_reason_expr = _value_expr(wallet_columns, "pilot_copy_gate_reason", "''")
            long_status_expr = _value_expr(wallet_columns, "long_horizon_status", "'untracked'")
            operator_approved_expr = _value_expr(wallet_columns, "operator_approved_pilot", "0")
            target_specialization_expr = _value_expr(wallet_columns, "target_specialization", "''")
            primary_source_expr = _value_expr(wallet_columns, "primary_source", "''")
            shadow_edge_expr = _value_expr(wallet_columns, "shadow_edge", "0")
            closed_shadow_expr = _value_expr(wallet_columns, "closed_shadow_trades", "0")
            copy_ready_rank_expr = _value_expr(wallet_columns, "copy_ready_rank", "999999")
            watchlist_rank_expr = _value_expr(wallet_columns, "watchlist_priority_rank", "999999")
            trust_score_expr = _value_expr(wallet_columns, "trust_score", "0")
            specialization_expr = _value_expr(wallet_columns, "specialization", "''")
            pilot_copy_condition = f"COALESCE({pilot_copy_expr}, 'blocked') = 'promoted'"
            wallet_mirror_condition = f"""
                COALESCE({operator_approved_expr}, 0) = 1
                AND COALESCE({identity_status_expr}, '') = 'linked'
                AND UPPER(COALESCE(NULLIF({target_specialization_expr}, ''), NULLIF({specialization_expr}, ''), '')) = 'CRYPTO'
                AND COALESCE({evidence_expr}, 'no_historical_evidence') IN ('detailed_trade_history', 'stats_only')
            """
            sql = f"""
                SELECT
                    address,
                    COALESCE({specialization_expr}, '') AS specialization,
                    COALESCE({target_specialization_expr}, '') AS target_specialization,
                    COALESCE({trust_score_expr}, 0) AS trust_score,
                    COALESCE({shadow_edge_expr}, 0) AS shadow_edge,
                    COALESCE({closed_shadow_expr}, 0) AS closed_shadow_trades,
                    COALESCE({copy_ready_rank_expr}, 999999) AS copy_ready_rank,
                    COALESCE({watchlist_rank_expr}, 999999) AS watchlist_priority_rank,
                    COALESCE({primary_source_expr}, '') AS primary_source,
                    COALESCE({watchlist_mode_expr}, '') AS watchlist_mode,
                    COALESCE({watchlist_status_expr}, '') AS watchlist_status,
                    COALESCE({identity_status_expr}, '') AS identity_resolution_status,
                    COALESCE({evidence_expr}, 'no_historical_evidence') AS historical_trade_evidence_status,
                    COALESCE({pilot_copy_expr}, 'blocked') AS pilot_copy_gate_status,
                    COALESCE({pilot_reason_expr}, '') AS pilot_copy_gate_reason,
                    COALESCE({long_status_expr}, 'untracked') AS long_horizon_status,
                    COALESCE({operator_approved_expr}, 0) AS operator_approved_pilot,
                    CASE
                        WHEN COALESCE({copy_ready_expr}, 'blocked') = 'promoted' THEN 'shadow_proven'
                        WHEN {pilot_copy_condition} THEN 'pilot_copy_ready'
                        WHEN {wallet_mirror_condition} THEN 'wallet_mirror'
                        ELSE ''
                    END AS cohort_source
                FROM polymarket_research_wallets
                WHERE COALESCE({copy_ready_expr}, 'blocked') = 'promoted'
                   OR ({pilot_copy_condition})
                   OR ({wallet_mirror_condition})
                ORDER BY
                    CASE
                        WHEN COALESCE({copy_ready_expr}, 'blocked') = 'promoted' THEN 0
                        WHEN {pilot_copy_condition} THEN 1
                        WHEN {wallet_mirror_condition} THEN 2
                        ELSE 3
                    END ASC,
                    CASE
                        WHEN COALESCE({copy_ready_expr}, 'blocked') = 'promoted' THEN COALESCE({copy_ready_rank_expr}, 999999)
                        ELSE COALESCE({watchlist_rank_expr}, 999999)
                    END ASC,
                    COALESCE({shadow_edge_expr}, 0) DESC,
                    COALESCE({trust_score_expr}, 0) DESC,
                    address ASC
            """
            params: list[Any] = []
            if limit is not None:
                sql += " LIMIT ?"
                params.append(limit)
            return list(connection.execute(sql, params))

    def fetch_copy_admission_counts(self) -> dict[str, int | str]:
        with self.connect() as connection:
            if not _table_exists(connection, "polymarket_research_wallets"):
                return {
                    "shadow_proven_wallets": 0,
                    "pilot_copy_wallets": 0,
                    "watch_only_wallets": 0,
                    "copy_blocker_reason": "no_research_wallets",
                }
            wallet_columns = _column_names(connection, "polymarket_research_wallets")
            copy_ready_expr = _value_expr(wallet_columns, "copy_ready_gate_status", "'blocked'")
            pilot_copy_expr = _value_expr(wallet_columns, "pilot_copy_gate_status", "'blocked'")
            watchlist_rank_expr = _value_expr(wallet_columns, "watchlist_priority_rank", "0")
            identity_status_expr = _value_expr(wallet_columns, "identity_resolution_status", "''")
            evidence_expr = _value_expr(wallet_columns, "historical_trade_evidence_status", "'no_historical_evidence'")
            operator_approved_expr = _value_expr(wallet_columns, "operator_approved_pilot", "0")
            target_specialization_expr = _value_expr(wallet_columns, "target_specialization", "''")
            specialization_expr = _value_expr(wallet_columns, "specialization", "''")
            wallet_mirror_condition = f"""
                COALESCE({operator_approved_expr}, 0) = 1
                AND COALESCE({identity_status_expr}, '') = 'linked'
                AND UPPER(COALESCE(NULLIF({target_specialization_expr}, ''), NULLIF({specialization_expr}, ''), '')) = 'CRYPTO'
                AND COALESCE({evidence_expr}, 'no_historical_evidence') IN ('detailed_trade_history', 'stats_only')
            """
            rows = connection.execute(
                f"""
                SELECT
                    SUM(CASE WHEN COALESCE({copy_ready_expr}, 'blocked') = 'promoted' THEN 1 ELSE 0 END) AS shadow_proven_wallets,
                    SUM(CASE WHEN COALESCE({pilot_copy_expr}, 'blocked') = 'promoted' THEN 1 ELSE 0 END) AS pilot_copy_wallets,
                    SUM(CASE WHEN {wallet_mirror_condition} THEN 1 ELSE 0 END) AS wallet_mirror_wallets,
                    SUM(
                        CASE
                            WHEN COALESCE({copy_ready_expr}, 'blocked') != 'promoted'
                             AND COALESCE({pilot_copy_expr}, 'blocked') != 'promoted'
                             AND NOT ({wallet_mirror_condition})
                             AND (COALESCE({watchlist_rank_expr}, 0) > 0 OR COALESCE({identity_status_expr}, '') = 'linked')
                            THEN 1 ELSE 0
                        END
                    ) AS watch_only_wallets
                FROM polymarket_research_wallets
                """
            ).fetchone()
            shadow_proven = int((rows or {})["shadow_proven_wallets"] or 0)
            pilot_copy = int((rows or {})["pilot_copy_wallets"] or 0)
            wallet_mirror = int((rows or {})["wallet_mirror_wallets"] or 0)
            watch_only = int((rows or {})["watch_only_wallets"] or 0)
            if shadow_proven + pilot_copy + wallet_mirror > 0:
                reason = "eligible_copy_wallets_available"
            elif watch_only > 0:
                reason = "watch_only_needs_shadow_or_pilot_proof"
            else:
                reason = "no_eligible_copy_wallets"
            return {
                "shadow_proven_wallets": shadow_proven,
                "pilot_copy_wallets": pilot_copy,
                "wallet_mirror_wallets": wallet_mirror,
                "watch_only_wallets": watch_only,
                "copy_blocker_reason": reason,
            }

    def fetch_source_trade_rows(self, wallet_addresses: Sequence[str], lookback_days: int) -> list[dict[str, Any]]:
        addresses = [address.strip().lower() for address in wallet_addresses if address and address.strip()]
        if not addresses:
            return []
        with self.connect_source() as connection:
            if not _table_exists(connection, "trades"):
                return []
            trade_columns = _column_names(connection, "trades")
            placeholders = ",".join("?" for _ in addresses)
            id_expr = _value_expr(trade_columns, "id", "NULL")
            status_expr = _value_expr(trade_columns, "status", "'UNKNOWN'")
            pnl_expr = _value_expr(trade_columns, "pnl", "0")
            size_expr = _value_expr(trade_columns, "size", "0")
            category_expr = _value_expr(trade_columns, "category", "'UNKNOWN'")
            side_expr = _value_expr(trade_columns, "side", "''")
            if "market_id" in trade_columns and "symbol_or_market_id" in trade_columns:
                market_expr = "COALESCE(market_id, symbol_or_market_id, '')"
            elif "market_id" in trade_columns:
                market_expr = "COALESCE(market_id, '')"
            elif "symbol_or_market_id" in trade_columns:
                market_expr = "COALESCE(symbol_or_market_id, '')"
            else:
                market_expr = "''"
            timestamp_expr = _value_expr(trade_columns, "timestamp", "CURRENT_TIMESTAMP")
            opened_expr = _value_expr(trade_columns, "opened_at", timestamp_expr)
            closed_expr = _value_expr(trade_columns, "closed_at", timestamp_expr)
            venue_expr = _value_expr(trade_columns, "venue", "'polymarket'")
            source_signal_expr = _value_expr(trade_columns, "source_signal", "''")
            whale_expr = _value_expr(trade_columns, "whale_address", "''")
            rows = connection.execute(
                f"""
                SELECT
                    {id_expr} AS id,
                    LOWER(TRIM(COALESCE({whale_expr}, ''))) AS whale_address,
                    {venue_expr} AS venue,
                    {market_expr} AS market_id,
                    {status_expr} AS status,
                    {pnl_expr} AS pnl,
                    {size_expr} AS size,
                    {category_expr} AS category,
                    {side_expr} AS side,
                    {timestamp_expr} AS occurred_at,
                    {opened_expr} AS source_opened_at,
                    {closed_expr} AS source_closed_at,
                    {source_signal_expr} AS source_signal
                FROM trades
                WHERE LOWER(TRIM(COALESCE({whale_expr}, ''))) IN ({placeholders})
                  AND COALESCE({closed_expr}, {timestamp_expr}) >= datetime('now', ?)
                ORDER BY COALESCE({closed_expr}, {timestamp_expr}) DESC, {id_expr} DESC
                """,
                [*addresses, f"-{max(1, lookback_days)} days"],
            ).fetchall()

        result: list[dict[str, Any]] = []
        for row in rows:
            trade_id = row["id"]
            source_trade_key = f"trade:{trade_id}" if trade_id is not None else (
                f"{row['whale_address']}|{row['market_id']}|{row['occurred_at']}"
            )
            result.append(
                {
                    "source_trade_key": source_trade_key,
                    "wallet_address": str(row["whale_address"] or "").lower(),
                    "venue": str(row["venue"] or "polymarket"),
                    "market_id": str(row["market_id"] or ""),
                    "status": str(row["status"] or "UNKNOWN"),
                    "source_pnl": float(row["pnl"] or 0.0),
                    "source_notional_usd": abs(float(row["size"] or 0.0)),
                    "category": str(row["category"] or "UNKNOWN"),
                    "side": str(row["side"] or ""),
                    "occurred_at": str(row["occurred_at"] or ""),
                    "source_opened_at": str(row["source_opened_at"] or ""),
                    "source_closed_at": str(row["source_closed_at"] or ""),
                    "source_signal": str(row["source_signal"] or ""),
                }
            )
        return result

    def fetch_copy_actions(self) -> list[sqlite3.Row]:
        with self.connect() as connection:
            if not _table_exists(connection, "polymarket_copy_actions"):
                return []
            return list(
                connection.execute(
                    """
                    SELECT *
                    FROM polymarket_copy_actions
                    ORDER BY executed_at DESC, id DESC
                    """
                )
            )

    def fetch_recent_copy_actions(self, limit: int = 12) -> list[sqlite3.Row]:
        with self.connect() as connection:
            if not _table_exists(connection, "polymarket_copy_actions"):
                return []
            return list(
                connection.execute(
                    """
                    SELECT *
                    FROM polymarket_copy_actions
                    ORDER BY executed_at DESC, id DESC
                    LIMIT ?
                    """,
                    (max(1, limit),),
                )
            )

    def fetch_active_copy_positions(self) -> list[sqlite3.Row]:
        with self.connect() as connection:
            if not _table_exists(connection, "polymarket_copy_positions"):
                return []
            return list(
                connection.execute(
                    """
                    SELECT *
                    FROM polymarket_copy_positions
                    WHERE status = 'OPEN'
                    ORDER BY opened_at DESC, id DESC
                    """
                )
            )

    def fetch_wallet_follower_pnl_rows(self) -> list[sqlite3.Row]:
        with self.connect() as connection:
            if not _table_exists(connection, "polymarket_copy_actions"):
                return []
            return list(
                connection.execute(
                    """
                    SELECT
                        wallet_address,
                        COUNT(*) FILTER (WHERE action_type IN ('close', 'replay_closed')) AS closed_actions,
                        ROUND(COALESCE(SUM(CASE WHEN action_type IN ('close', 'replay_closed') THEN follower_pnl ELSE 0 END), 0), 4) AS follower_realized_pnl,
                        ROUND(COALESCE(SUM(CASE WHEN action_type IN ('close', 'replay_closed') THEN source_pnl ELSE 0 END), 0), 4) AS source_realized_pnl,
                        ROUND(COALESCE(SUM(CASE WHEN action_type = 'open' THEN follower_notional_usd ELSE 0 END), 0), 4) AS opened_notional_usd,
                        MAX(executed_at) AS last_action_at
                    FROM polymarket_copy_actions
                    GROUP BY wallet_address
                    ORDER BY follower_realized_pnl DESC, wallet_address ASC
                    """
                )
            )

    def fetch_copy_action_reason_counts(self) -> list[sqlite3.Row]:
        with self.connect() as connection:
            if not _table_exists(connection, "polymarket_copy_actions"):
                return []
            return list(
                connection.execute(
                    """
                    SELECT reason, COUNT(*) AS count
                    FROM polymarket_copy_actions
                    WHERE action_type = 'reject'
                    GROUP BY reason
                    ORDER BY count DESC, reason ASC
                    """
                )
            )

    def source_trade_action_exists(self, source_trade_key: str, action_type: str) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT 1
                FROM polymarket_copy_actions
                WHERE source_trade_key = ?
                  AND action_type = ?
                LIMIT 1
                """,
                (source_trade_key, action_type),
            ).fetchone()
            return row is not None

    def fetch_open_position_by_source_trade_key(self, source_trade_key: str) -> sqlite3.Row | None:
        with self.connect() as connection:
            return connection.execute(
                """
                SELECT *
                FROM polymarket_copy_positions
                WHERE source_trade_key = ?
                  AND status = 'OPEN'
                LIMIT 1
                """,
                (source_trade_key,),
            ).fetchone()

    def fetch_open_position_for_wallet_market(self, wallet_address: str, market_id: str) -> sqlite3.Row | None:
        with self.connect() as connection:
            return connection.execute(
                """
                SELECT *
                FROM polymarket_copy_positions
                WHERE wallet_address = ?
                  AND market_id = ?
                  AND status = 'OPEN'
                LIMIT 1
                """,
                (wallet_address.lower(), market_id),
            ).fetchone()

    def fetch_open_wallet_notional(self, wallet_address: str) -> float:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT ROUND(COALESCE(SUM(follower_notional_usd), 0), 4) AS total
                FROM polymarket_copy_positions
                WHERE wallet_address = ?
                  AND status = 'OPEN'
                """,
                (wallet_address.lower(),),
            ).fetchone()
            return float((row["total"] if row is not None else 0.0) or 0.0)

    def fetch_open_wallet_position_count(self, wallet_address: str) -> int:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS total
                FROM polymarket_copy_positions
                WHERE wallet_address = ?
                  AND status = 'OPEN'
                """,
                (wallet_address.lower(),),
            ).fetchone()
            return int((row["total"] if row is not None else 0) or 0)

    def fetch_open_market_notional(self, market_id: str) -> float:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT ROUND(COALESCE(SUM(follower_notional_usd), 0), 4) AS total
                FROM polymarket_copy_positions
                WHERE market_id = ?
                  AND status = 'OPEN'
                """,
                (market_id,),
            ).fetchone()
            return float((row["total"] if row is not None else 0.0) or 0.0)

    def insert_copy_action(
        self,
        *,
        source_trade_key: str,
        wallet_address: str,
        market_id: str,
        category: str,
        action_type: str,
        reason: str,
        source_status: str,
        side: str,
        source_notional_usd: float,
        follower_notional_usd: float,
        source_pnl: float,
        follower_pnl: float,
        delayed_seconds: int,
        source_opened_at: str,
        source_closed_at: str,
        executed_at: str | None = None,
        notes: dict[str, Any] | None = None,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO polymarket_copy_actions (
                    source_trade_key,
                    wallet_address,
                    market_id,
                    category,
                    action_type,
                    reason,
                    source_status,
                    side,
                    source_notional_usd,
                    follower_notional_usd,
                    source_pnl,
                    follower_pnl,
                    delayed_seconds,
                    source_opened_at,
                    source_closed_at,
                    executed_at,
                    notes_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source_trade_key,
                    wallet_address.lower(),
                    market_id,
                    category,
                    action_type,
                    reason,
                    source_status,
                    side,
                    source_notional_usd,
                    follower_notional_usd,
                    source_pnl,
                    follower_pnl,
                    delayed_seconds,
                    source_opened_at,
                    source_closed_at,
                    executed_at or _utc_now(),
                    _json_dumps(notes),
                ),
            )
            connection.commit()

    def create_or_replace_position(
        self,
        *,
        source_trade_key: str,
        wallet_address: str,
        market_id: str,
        category: str,
        side: str,
        source_notional_usd: float,
        follower_notional_usd: float,
        source_pnl: float,
        follower_pnl: float,
        source_status: str,
        status: str,
        source_opened_at: str,
        source_closed_at: str,
        opened_at: str | None = None,
        closed_at: str | None = None,
        notes: dict[str, Any] | None = None,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO polymarket_copy_positions (
                    source_trade_key,
                    wallet_address,
                    market_id,
                    category,
                    side,
                    source_notional_usd,
                    follower_notional_usd,
                    source_pnl,
                    follower_pnl,
                    source_status,
                    status,
                    source_opened_at,
                    source_closed_at,
                    opened_at,
                    closed_at,
                    notes_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_trade_key) DO UPDATE SET
                    wallet_address = excluded.wallet_address,
                    market_id = excluded.market_id,
                    category = excluded.category,
                    side = excluded.side,
                    source_notional_usd = excluded.source_notional_usd,
                    follower_notional_usd = excluded.follower_notional_usd,
                    source_pnl = excluded.source_pnl,
                    follower_pnl = excluded.follower_pnl,
                    source_status = excluded.source_status,
                    status = excluded.status,
                    source_opened_at = excluded.source_opened_at,
                    source_closed_at = excluded.source_closed_at,
                    opened_at = excluded.opened_at,
                    closed_at = excluded.closed_at,
                    notes_json = excluded.notes_json
                """,
                (
                    source_trade_key,
                    wallet_address.lower(),
                    market_id,
                    category,
                    side,
                    source_notional_usd,
                    follower_notional_usd,
                    source_pnl,
                    follower_pnl,
                    source_status,
                    status,
                    source_opened_at,
                    source_closed_at,
                    opened_at or _utc_now(),
                    closed_at,
                    _json_dumps(notes),
                ),
            )
            connection.commit()

    def close_position_by_source_trade_key(
        self,
        *,
        source_trade_key: str,
        source_status: str,
        source_closed_at: str,
        source_pnl: float,
        follower_pnl: float,
        notes: dict[str, Any] | None = None,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE polymarket_copy_positions
                SET source_status = ?,
                    source_closed_at = ?,
                    source_pnl = ?,
                    follower_pnl = ?,
                    status = 'CLOSED',
                    closed_at = ?,
                    notes_json = ?
                WHERE source_trade_key = ?
                  AND status = 'OPEN'
                """,
                (
                    source_status,
                    source_closed_at,
                    source_pnl,
                    follower_pnl,
                    source_closed_at or _utc_now(),
                    _json_dumps(notes),
                    source_trade_key,
                ),
            )
            connection.commit()
