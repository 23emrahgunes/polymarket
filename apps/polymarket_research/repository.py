from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class PolymarketResearchRepository:
    def __init__(self, db_path: str):
        self.db_path = db_path

    @staticmethod
    def _table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
        row = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table_name,),
        ).fetchone()
        return row is not None

    @staticmethod
    def _table_columns(connection: sqlite3.Connection, table_name: str) -> set[str]:
        if not PolymarketResearchRepository._table_exists(connection, table_name):
            return set()
        rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
        return {str(row[1]) for row in rows}

    def _ensure_column(self, connection: sqlite3.Connection, table_name: str, column_name: str, column_def: str) -> None:
        if column_name not in self._table_columns(connection, table_name):
            connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_def}")

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
                    replay_key TEXT,
                    raw_wallet_pnl REAL DEFAULT 0,
                    raw_notional_usd REAL DEFAULT 0,
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
            self._ensure_column(connection, "polymarket_shadow_actions", "replay_key", "TEXT")
            self._ensure_column(connection, "polymarket_shadow_actions", "raw_notional_usd", "REAL DEFAULT 0")
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_polymarket_shadow_actions_replay_key
                ON polymarket_shadow_actions (replay_key)
                WHERE replay_key IS NOT NULL
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS polymarket_research_watchlist (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    display_name TEXT NOT NULL,
                    profile_ref TEXT,
                    wallet_address TEXT,
                    priority_rank INTEGER NOT NULL DEFAULT 0,
                    priority_mode TEXT NOT NULL DEFAULT 'normal',
                    target_specialization TEXT NOT NULL DEFAULT 'UNKNOWN',
                    status TEXT NOT NULL DEFAULT 'pending_resolution',
                    notes TEXT,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS polymarket_research_wallets (
                    address TEXT PRIMARY KEY,
                    source_type TEXT NOT NULL DEFAULT 'unknown',
                    primary_source TEXT NOT NULL DEFAULT 'unknown',
                    source_labels TEXT NOT NULL DEFAULT '[]',
                    source_count INTEGER NOT NULL DEFAULT 0,
                    discovery_bucket TEXT NOT NULL DEFAULT 'unassigned',
                    cohort TEXT NOT NULL DEFAULT 'discovery',
                    discovery_rank INTEGER NOT NULL DEFAULT 0,
                    shadow_rank INTEGER NOT NULL DEFAULT 0,
                    copy_ready_rank INTEGER NOT NULL DEFAULT 0,
                    discovery_score REAL NOT NULL DEFAULT 0,
                    trust_score REAL NOT NULL DEFAULT 0.5,
                    consistency_score REAL NOT NULL DEFAULT 0,
                    profit_consistency_score REAL NOT NULL DEFAULT 0,
                    recency_score REAL NOT NULL DEFAULT 0,
                    frequency_score REAL NOT NULL DEFAULT 0,
                    drawdown_estimate_pct REAL NOT NULL DEFAULT 0,
                    active_days INTEGER NOT NULL DEFAULT 0,
                    closed_trade_count INTEGER NOT NULL DEFAULT 0,
                    realized_pnl REAL NOT NULL DEFAULT 0,
                    crypto_participation_ratio REAL NOT NULL DEFAULT 0,
                    specialization TEXT NOT NULL DEFAULT 'UNKNOWN',
                    event_count_24h INTEGER NOT NULL DEFAULT 0,
                    last_event_amount REAL NOT NULL DEFAULT 0,
                    last_seen_at TEXT,
                    closed_shadow_trades INTEGER NOT NULL DEFAULT 0,
                    shadow_pnl REAL NOT NULL DEFAULT 0,
                    shadow_edge REAL NOT NULL DEFAULT 0,
                    worst_drawdown_pct REAL NOT NULL DEFAULT 0,
                    shadow_gate_status TEXT NOT NULL DEFAULT 'blocked',
                    shadow_gate_reason TEXT NOT NULL DEFAULT 'low_consistency',
                    copy_ready_gate_status TEXT NOT NULL DEFAULT 'blocked',
                    copy_ready_gate_reason TEXT NOT NULL DEFAULT 'needs_shadow_history',
                    shadow_eligible INTEGER NOT NULL DEFAULT 0,
                    copy_ready_eligible INTEGER NOT NULL DEFAULT 0,
                    watchlist_priority_rank INTEGER NOT NULL DEFAULT 0,
                    watchlist_status TEXT NOT NULL DEFAULT '',
                    watchlist_mode TEXT NOT NULL DEFAULT '',
                    identity_resolution_status TEXT NOT NULL DEFAULT 'untracked',
                    priority_pinned INTEGER NOT NULL DEFAULT 0,
                    refreshed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            for column_name, column_def in [
                ("primary_source", "TEXT NOT NULL DEFAULT 'unknown'"),
                ("source_labels", "TEXT NOT NULL DEFAULT '[]'"),
                ("source_count", "INTEGER NOT NULL DEFAULT 0"),
                ("discovery_bucket", "TEXT NOT NULL DEFAULT 'unassigned'"),
                ("shadow_gate_status", "TEXT NOT NULL DEFAULT 'blocked'"),
                ("shadow_gate_reason", "TEXT NOT NULL DEFAULT 'low_consistency'"),
                ("copy_ready_gate_status", "TEXT NOT NULL DEFAULT 'blocked'"),
                ("copy_ready_gate_reason", "TEXT NOT NULL DEFAULT 'needs_shadow_history'"),
                ("watchlist_priority_rank", "INTEGER NOT NULL DEFAULT 0"),
                ("watchlist_status", "TEXT NOT NULL DEFAULT ''"),
                ("watchlist_mode", "TEXT NOT NULL DEFAULT ''"),
                ("identity_resolution_status", "TEXT NOT NULL DEFAULT 'untracked'"),
                ("priority_pinned", "INTEGER NOT NULL DEFAULT 0"),
            ]:
                self._ensure_column(connection, "polymarket_research_wallets", column_name, column_def)
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_polymarket_research_wallets_cohort_rank
                ON polymarket_research_wallets (cohort, discovery_rank, shadow_rank, copy_ready_rank)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_polymarket_research_watchlist_priority
                ON polymarket_research_watchlist (priority_rank ASC, updated_at DESC)
                """
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO polymarket_research_watchlist (
                    id,
                    display_name,
                    profile_ref,
                    wallet_address,
                    priority_rank,
                    priority_mode,
                    target_specialization,
                    status,
                    notes
                ) VALUES (
                    1,
                    'ohanism',
                    'https://polymarket.com/tr/@ohanism',
                    NULL,
                    1,
                    'fast_track_shadow',
                    'CRYPTO',
                    'pending_resolution',
                    'Seeded priority specialist candidate'
                )
                """
            )
            connection.commit()

    def fetch_candidate_wallets(
        self,
        limit: int | None = None,
        source_types: list[str] | None = None,
    ) -> list[sqlite3.Row]:
        with self.connect() as connection:
            if not self._table_exists(connection, "whale_wallets"):
                return []

            has_whale_stats = self._table_exists(connection, "whale_stats")
            has_trades = self._table_exists(connection, "trades")
            trade_closed_expr = "COALESCE(closed_at, timestamp)"

            trade_cte = ""
            trade_join = ""
            if has_trades:
                trade_cte = f"""
                WITH trade_stats AS (
                    SELECT
                        LOWER(COALESCE(whale_address, '')) AS address,
                        COUNT(CASE WHEN status LIKE 'CLOSED%' THEN 1 END) AS closed_trades,
                        ROUND(COALESCE(SUM(COALESCE(pnl, 0)), 0), 4) AS realized_pnl,
                        COUNT(DISTINCT substr({trade_closed_expr}, 1, 10)) AS active_days,
                        COUNT(*) AS total_trade_rows,
                        SUM(CASE WHEN UPPER(COALESCE(category, '')) = 'CRYPTO' THEN 1 ELSE 0 END) AS crypto_trade_rows,
                        MAX(COALESCE(category, '')) AS specialization_hint
                    FROM trades
                    WHERE TRIM(COALESCE(whale_address, '')) != ''
                    GROUP BY LOWER(COALESCE(whale_address, ''))
                )
                """
                trade_join = "LEFT JOIN trade_stats ON trade_stats.address = LOWER(whale_wallets.address)"

            whale_stats_join = ""
            trust_score_expr = "0.5 AS trust_score"
            closed_trade_count_expr = "0 AS closed_trade_count"
            realized_pnl_expr = "0.0 AS realized_pnl"
            if has_whale_stats:
                whale_stats_join = "LEFT JOIN whale_stats ON LOWER(whale_stats.address) = LOWER(whale_wallets.address)"
                trust_score_expr = "COALESCE(whale_stats.trust_score, 0.5) AS trust_score"
                closed_trade_count_expr = "COALESCE(whale_stats.total_trades, 0) AS closed_trade_count"
                realized_pnl_expr = "ROUND(COALESCE(whale_stats.total_pnl, 0), 4) AS realized_pnl"

            where_clauses = ["whale_wallets.enabled = 1"]
            params: list[object] = []
            if source_types:
                normalized_sources = [source_type for source_type in source_types if str(source_type).strip()]
                if normalized_sources:
                    placeholders = ", ".join("?" for _ in normalized_sources)
                    where_clauses.append(f"whale_wallets.source_type IN ({placeholders})")
                    params.extend(normalized_sources)

            query = f"""
                {trade_cte}
                SELECT
                    whale_wallets.address,
                    whale_wallets.source_type,
                    whale_wallets.discovery_score,
                    whale_wallets.event_count_24h,
                    whale_wallets.last_event_amount,
                    whale_wallets.last_event_category,
                    whale_wallets.last_seen_at,
                    {trust_score_expr},
                    {closed_trade_count_expr},
                    {realized_pnl_expr},
                    COALESCE(trade_stats.active_days, 0) AS active_days,
                    COALESCE(trade_stats.total_trade_rows, 0) AS total_trade_rows,
                    COALESCE(trade_stats.crypto_trade_rows, 0) AS crypto_trade_rows,
                    COALESCE(trade_stats.specialization_hint, whale_wallets.last_event_category, 'UNKNOWN') AS specialization_hint
                FROM whale_wallets
                {whale_stats_join}
                {trade_join}
                WHERE {" AND ".join(where_clauses)}
                ORDER BY whale_wallets.discovery_score DESC, whale_wallets.last_event_amount DESC, whale_wallets.last_seen_at DESC
            """
            if limit is not None:
                query += "\nLIMIT ?"
                params.append(max(limit, 1))

            cursor = connection.execute(query, tuple(params))
            return cursor.fetchall()

    def fetch_wallet_provenance(self, addresses: list[str]) -> list[sqlite3.Row]:
        if not addresses:
            return []

        normalized_addresses = [address.lower() for address in addresses if str(address).strip()]
        if not normalized_addresses:
            return []

        with self.connect() as connection:
            if not self._table_exists(connection, "whale_wallets"):
                return []

            placeholders = ", ".join("?" for _ in normalized_addresses)
            if self._table_exists(connection, "whale_wallet_sources"):
                params = tuple(normalized_addresses + normalized_addresses)
                query = f"""
                    SELECT
                        address,
                        source_type,
                        MAX(last_seen_at) AS last_seen_at
                    FROM (
                        SELECT LOWER(address) AS address, source_type, last_seen_at
                        FROM whale_wallet_sources
                        WHERE LOWER(address) IN ({placeholders})
                        UNION ALL
                        SELECT LOWER(address) AS address, source_type, last_seen_at
                        FROM whale_wallets
                        WHERE LOWER(address) IN ({placeholders})
                    ) provenance
                    WHERE TRIM(COALESCE(source_type, '')) != ''
                    GROUP BY address, source_type
                    ORDER BY address ASC, source_type ASC
                """
                cursor = connection.execute(query, params)
                return cursor.fetchall()

            cursor = connection.execute(
                f"""
                    SELECT
                        LOWER(address) AS address,
                        source_type,
                        last_seen_at
                    FROM whale_wallets
                    WHERE LOWER(address) IN ({placeholders})
                      AND TRIM(COALESCE(source_type, '')) != ''
                    ORDER BY LOWER(address) ASC, source_type ASC
                """,
                tuple(normalized_addresses),
            )
            return cursor.fetchall()

    def fetch_wallet_trade_rows(self, addresses: list[str]) -> list[sqlite3.Row]:
        if not addresses:
            return []

        with self.connect() as connection:
            if not self._table_exists(connection, "trades"):
                return []

            normalized_addresses = [address.lower() for address in addresses if address.strip()]
            if not normalized_addresses:
                return []

            placeholders = ", ".join("?" for _ in normalized_addresses)
            cursor = connection.execute(
                f"""
                SELECT
                    LOWER(COALESCE(whale_address, '')) AS address,
                    COALESCE(pnl, 0) AS pnl,
                    COALESCE(closed_at, timestamp) AS occurred_at,
                    COALESCE(category, 'UNKNOWN') AS category,
                    COALESCE(status, '') AS status
                FROM trades
                WHERE LOWER(COALESCE(whale_address, '')) IN ({placeholders})
                  AND status LIKE 'CLOSED%'
                ORDER BY COALESCE(closed_at, timestamp) ASC, id ASC
                """,
                tuple(normalized_addresses),
            )
            return cursor.fetchall()

    def fetch_replay_trade_rows(self, addresses: list[str], window_days: int) -> list[sqlite3.Row]:
        if not addresses:
            return []

        with self.connect() as connection:
            if not self._table_exists(connection, "trades"):
                return []

            normalized_addresses = [address.lower() for address in addresses if address.strip()]
            if not normalized_addresses:
                return []

            trade_columns = self._table_columns(connection, "trades")

            def column_or_fallback(name: str, fallback: str) -> str:
                return name if name in trade_columns else fallback

            venue_column = column_or_fallback("venue", "'polymarket'")
            venue_expr = f"COALESCE({venue_column}, 'polymarket')"
            market_expr = f"COALESCE({column_or_fallback('market_id', 'NULL')}, {column_or_fallback('symbol_or_market_id', 'NULL')}, 'unknown-market')"
            size_expr = f"COALESCE({column_or_fallback('size', 'NULL')}, ABS(COALESCE(pnl, 0)), 0)"
            occurred_expr = f"COALESCE({column_or_fallback('closed_at', 'NULL')}, {column_or_fallback('timestamp', 'NULL')}, CURRENT_TIMESTAMP)"
            category_expr = f"COALESCE({column_or_fallback('category', 'NULL')}, 'UNKNOWN')"
            status_expr = f"COALESCE({column_or_fallback('status', 'NULL')}, '')"
            side_expr = f"COALESCE({column_or_fallback('side', 'NULL')}, '')"
            source_signal_expr = f"COALESCE({column_or_fallback('source_signal', 'NULL')}, '')"

            placeholders = ", ".join("?" for _ in normalized_addresses)
            cursor = connection.execute(
                f"""
                SELECT
                    id,
                    LOWER(COALESCE(whale_address, '')) AS wallet_address,
                    {venue_expr} AS venue,
                    {market_expr} AS market_id,
                    {category_expr} AS category,
                    {status_expr} AS status,
                    COALESCE(pnl, 0) AS pnl,
                    {size_expr} AS size,
                    {occurred_expr} AS occurred_at,
                    {side_expr} AS side,
                    {source_signal_expr} AS source_signal
                FROM trades
                WHERE LOWER(COALESCE(whale_address, '')) IN ({placeholders})
                  AND {status_expr} LIKE 'CLOSED%'
                  AND {venue_expr} = 'polymarket'
                  AND {occurred_expr} >= datetime('now', ?)
                ORDER BY {occurred_expr} ASC, id ASC
                """,
                tuple(normalized_addresses) + (f"-{max(window_days, 1)} days",),
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
                    replay_key,
                    raw_wallet_pnl,
                    raw_notional_usd,
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
                    replay_key,
                    raw_wallet_pnl,
                    raw_notional_usd,
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

    def fetch_watchlist_rows(self) -> list[sqlite3.Row]:
        self.ensure_tables()
        with self.connect() as connection:
            cursor = connection.execute(
                """
                SELECT
                    id,
                    display_name,
                    profile_ref,
                    wallet_address,
                    priority_rank,
                    priority_mode,
                    target_specialization,
                    status,
                    notes,
                    created_at,
                    updated_at
                FROM polymarket_research_watchlist
                ORDER BY priority_rank ASC, updated_at DESC, id ASC
                """
            )
            return cursor.fetchall()

    def replace_wallet_snapshots(self, rows: list[dict[str, object]]) -> None:
        self.ensure_tables()
        with self.connect() as connection:
            connection.execute("DELETE FROM polymarket_research_wallets")
            if rows:
                connection.executemany(
                    """
                    INSERT INTO polymarket_research_wallets (
                        address,
                        source_type,
                        primary_source,
                        source_labels,
                        source_count,
                        discovery_bucket,
                        cohort,
                        discovery_rank,
                        shadow_rank,
                        copy_ready_rank,
                        discovery_score,
                        trust_score,
                        consistency_score,
                        profit_consistency_score,
                        recency_score,
                        frequency_score,
                        drawdown_estimate_pct,
                        active_days,
                        closed_trade_count,
                        realized_pnl,
                        crypto_participation_ratio,
                        specialization,
                        event_count_24h,
                        last_event_amount,
                        last_seen_at,
                        closed_shadow_trades,
                        shadow_pnl,
                        shadow_edge,
                        worst_drawdown_pct,
                        shadow_gate_status,
                        shadow_gate_reason,
                        copy_ready_gate_status,
                        copy_ready_gate_reason,
                        shadow_eligible,
                        copy_ready_eligible,
                        watchlist_priority_rank,
                        watchlist_status,
                        watchlist_mode,
                        identity_resolution_status,
                        priority_pinned,
                        refreshed_at
                    ) VALUES (
                        :address,
                        :source_type,
                        :primary_source,
                        :source_labels,
                        :source_count,
                        :discovery_bucket,
                        :cohort,
                        :discovery_rank,
                        :shadow_rank,
                        :copy_ready_rank,
                        :discovery_score,
                        :trust_score,
                        :consistency_score,
                        :profit_consistency_score,
                        :recency_score,
                        :frequency_score,
                        :drawdown_estimate_pct,
                        :active_days,
                        :closed_trade_count,
                        :realized_pnl,
                        :crypto_participation_ratio,
                        :specialization,
                        :event_count_24h,
                        :last_event_amount,
                        :last_seen_at,
                        :closed_shadow_trades,
                        :shadow_pnl,
                        :shadow_edge,
                        :worst_drawdown_pct,
                        :shadow_gate_status,
                        :shadow_gate_reason,
                        :copy_ready_gate_status,
                        :copy_ready_gate_reason,
                        :shadow_eligible,
                        :copy_ready_eligible,
                        :watchlist_priority_rank,
                        :watchlist_status,
                        :watchlist_mode,
                        :identity_resolution_status,
                        :priority_pinned,
                        CURRENT_TIMESTAMP
                    )
                    """,
                    rows,
                )
            connection.commit()

    def fetch_persisted_wallet_snapshots(self, limit: int | None = None) -> list[sqlite3.Row]:
        self.ensure_tables()
        with self.connect() as connection:
            query = """
                SELECT
                    address,
                    source_type,
                    primary_source,
                    source_labels,
                    source_count,
                    discovery_bucket,
                    cohort,
                    discovery_rank,
                    shadow_rank,
                    copy_ready_rank,
                    discovery_score,
                    trust_score,
                    consistency_score,
                    profit_consistency_score,
                    recency_score,
                    frequency_score,
                    drawdown_estimate_pct,
                    active_days,
                    closed_trade_count,
                    realized_pnl,
                    crypto_participation_ratio,
                    specialization,
                    event_count_24h,
                    last_event_amount,
                    last_seen_at,
                    closed_shadow_trades,
                    shadow_pnl,
                    shadow_edge,
                    worst_drawdown_pct,
                    shadow_gate_status,
                    shadow_gate_reason,
                    copy_ready_gate_status,
                    copy_ready_gate_reason,
                    shadow_eligible,
                    copy_ready_eligible,
                    watchlist_priority_rank,
                    watchlist_status,
                    watchlist_mode,
                    identity_resolution_status,
                    priority_pinned,
                    refreshed_at
                FROM polymarket_research_wallets
                ORDER BY discovery_rank ASC, consistency_score DESC, trust_score DESC
            """
            params: tuple[object, ...] = ()
            if limit is not None:
                query += " LIMIT ?"
                params = (max(limit, 1),)
            cursor = connection.execute(query, params)
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
        raw_notional_usd: float = 0.0,
        delayed_seconds: int = 0,
        status: str = "CLOSED",
        action_type: str = "shadow_trade",
        replay_key: str | None = None,
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
                    action_type,
                    replay_key,
                    raw_wallet_pnl,
                    raw_notional_usd,
                    shadow_pnl,
                    shadow_edge,
                    drawdown_pct,
                    delayed_seconds,
                    notes_json,
                    status,
                    opened_at,
                    closed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (
                    wallet_address,
                    market_id,
                    category,
                    source_type,
                    action_type,
                    replay_key,
                    raw_wallet_pnl,
                    raw_notional_usd,
                    shadow_pnl,
                    shadow_edge,
                    drawdown_pct,
                    delayed_seconds,
                    json.dumps(notes or {}, ensure_ascii=True),
                    status,
                ),
            )
            connection.commit()

    def replace_shadow_replay_actions(self, rows: list[dict[str, object]]) -> None:
        self.ensure_tables()
        with self.connect() as connection:
            connection.execute("DELETE FROM polymarket_shadow_actions WHERE action_type = 'shadow_replay'")
            if rows:
                connection.executemany(
                    """
                    INSERT INTO polymarket_shadow_actions (
                        wallet_address,
                        market_id,
                        category,
                        source_type,
                        action_type,
                        replay_key,
                        raw_wallet_pnl,
                        raw_notional_usd,
                        shadow_pnl,
                        shadow_edge,
                        drawdown_pct,
                        delayed_seconds,
                        notes_json,
                        status,
                        opened_at,
                        closed_at
                    ) VALUES (
                        :wallet_address,
                        :market_id,
                        :category,
                        :source_type,
                        'shadow_replay',
                        :replay_key,
                        :raw_wallet_pnl,
                        :raw_notional_usd,
                        :shadow_pnl,
                        :shadow_edge,
                        :drawdown_pct,
                        :delayed_seconds,
                        :notes_json,
                        :status,
                        :opened_at,
                        :closed_at
                    )
                    """,
                    rows,
                )
            connection.commit()
