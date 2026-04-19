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


def _column_names(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row["name"]) for row in connection.execute(f"PRAGMA table_info({table})")}


def _ensure_columns(connection: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    existing = _column_names(connection, table)
    for name, definition in columns.items():
        if name not in existing:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


class BinanceTechnicalRepository:
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
                CREATE TABLE IF NOT EXISTS wallet (
                    id INTEGER PRIMARY KEY,
                    balance REAL NOT NULL DEFAULT 1000.0,
                    updated_at TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS venue_accounts (
                    venue TEXT NOT NULL,
                    execution_mode TEXT NOT NULL,
                    cash_balance REAL NOT NULL DEFAULT 1000.0,
                    equity REAL NOT NULL DEFAULT 1000.0,
                    available_balance REAL NOT NULL DEFAULT 1000.0,
                    updated_at TEXT,
                    PRIMARY KEY (venue, execution_mode)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    venue TEXT,
                    execution_mode TEXT,
                    instrument_type TEXT,
                    market_id TEXT,
                    side TEXT,
                    size REAL,
                    price REAL,
                    confidence REAL,
                    source_signal TEXT,
                    category TEXT,
                    strategy_profile TEXT,
                    sample_kind TEXT,
                    status TEXT,
                    pnl REAL,
                    whale_address TEXT,
                    timestamp TEXT,
                    opened_at TEXT,
                    closed_at TEXT,
                    is_synthetic INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS venue_positions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    venue TEXT,
                    execution_mode TEXT,
                    instrument_type TEXT,
                    symbol_or_market_id TEXT,
                    side TEXT,
                    qty REAL,
                    entry_price REAL,
                    mark_price REAL,
                    notional_usd REAL,
                    unrealized_pnl REAL,
                    realized_pnl REAL,
                    leverage REAL,
                    strategy_profile TEXT,
                    sample_kind TEXT,
                    source_signal TEXT,
                    signal_family TEXT,
                    status TEXT,
                    opened_at TEXT,
                    closed_at TEXT,
                    take_profit_price REAL,
                    stop_loss_price REAL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS venue_orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    venue TEXT,
                    execution_mode TEXT,
                    symbol_or_market_id TEXT,
                    order_type TEXT,
                    side TEXT,
                    qty REAL,
                    price REAL,
                    stop_price REAL,
                    reduce_only INTEGER NOT NULL DEFAULT 0,
                    status TEXT,
                    created_at TEXT,
                    closed_at TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS decision_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    occurred_at TEXT,
                    venue TEXT,
                    market_id TEXT,
                    category TEXT,
                    signal_family TEXT,
                    strategy_profile TEXT,
                    raw_source_signal TEXT,
                    action TEXT,
                    reason TEXT,
                    decision_score REAL,
                    threshold REAL,
                    trade_size REAL,
                    confidence REAL,
                    mapping_stage TEXT,
                    lazy_lookup_attempted INTEGER NOT NULL DEFAULT 0,
                    lazy_lookup_hit INTEGER NOT NULL DEFAULT 0,
                    hot_window_promoted INTEGER NOT NULL DEFAULT 0,
                    inputs_json TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS runtime_status_snapshot (
                    id INTEGER PRIMARY KEY,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    metrics_json TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS whale_wallets (
                    address TEXT PRIMARY KEY,
                    source_type TEXT,
                    discovery_score REAL,
                    event_count_24h INTEGER,
                    last_event_amount REAL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    last_event_category TEXT,
                    last_seen_at TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS whale_stats (
                    address TEXT PRIMARY KEY,
                    trust_score REAL,
                    total_trades INTEGER,
                    wins INTEGER,
                    total_pnl REAL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS whale_wallet_sources (
                    address TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    last_seen_at TEXT,
                    PRIMARY KEY (address, source_type)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS market_aliases (
                    alias TEXT NOT NULL,
                    alias_type TEXT,
                    market_id TEXT,
                    question TEXT,
                    category TEXT,
                    volume_24h REAL,
                    active INTEGER NOT NULL DEFAULT 1,
                    source TEXT,
                    last_seen_at TEXT,
                    PRIMARY KEY (alias, market_id)
                )
                """
            )

            _ensure_columns(
                connection,
                "trades",
                {
                    "execution_mode": "TEXT",
                    "side": "TEXT",
                    "price": "REAL",
                    "confidence": "REAL",
                    "source_signal": "TEXT",
                    "category": "TEXT",
                    "strategy_profile": "TEXT",
                    "sample_kind": "TEXT",
                    "pnl": "REAL DEFAULT 0",
                    "whale_address": "TEXT",
                    "opened_at": "TEXT",
                    "closed_at": "TEXT",
                    "is_synthetic": "INTEGER NOT NULL DEFAULT 0",
                },
            )
            _ensure_columns(
                connection,
                "venue_positions",
                {
                    "instrument_type": "TEXT",
                    "side": "TEXT",
                    "qty": "REAL",
                    "entry_price": "REAL",
                    "mark_price": "REAL",
                    "notional_usd": "REAL DEFAULT 0",
                    "unrealized_pnl": "REAL DEFAULT 0",
                    "realized_pnl": "REAL DEFAULT 0",
                    "leverage": "REAL DEFAULT 1",
                    "strategy_profile": "TEXT",
                    "sample_kind": "TEXT",
                    "source_signal": "TEXT",
                    "signal_family": "TEXT",
                    "closed_at": "TEXT",
                    "take_profit_price": "REAL",
                    "stop_loss_price": "REAL",
                    "linked_trade_id": "INTEGER",
                },
            )
            _ensure_columns(
                connection,
                "venue_orders",
                {
                    "execution_mode": "TEXT",
                    "closed_at": "TEXT",
                    "linked_position_id": "INTEGER",
                },
            )
            _ensure_columns(
                connection,
                "decision_audit",
                {
                    "category": "TEXT",
                    "signal_family": "TEXT",
                    "raw_source_signal": "TEXT",
                    "confidence": "REAL",
                    "mapping_stage": "TEXT",
                    "lazy_lookup_attempted": "INTEGER NOT NULL DEFAULT 0",
                    "lazy_lookup_hit": "INTEGER NOT NULL DEFAULT 0",
                    "hot_window_promoted": "INTEGER NOT NULL DEFAULT 0",
                    "inputs_json": "TEXT",
                },
            )
            _ensure_columns(connection, "whale_stats", {"wins": "INTEGER DEFAULT 0"})
            connection.commit()

    def ensure_runtime_rows(self, venues: Sequence[str]) -> None:
        self.ensure_tables()
        now = _utc_now()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO wallet (id, balance, updated_at)
                VALUES (1, 1000.0, ?)
                ON CONFLICT(id) DO NOTHING
                """,
                (now,),
            )
            for venue in venues:
                connection.execute(
                    """
                    INSERT INTO venue_accounts (
                        venue, execution_mode, cash_balance, equity, available_balance, updated_at
                    ) VALUES (?, 'paper', 1000.0, 1000.0, 1000.0, ?)
                    ON CONFLICT(venue, execution_mode) DO NOTHING
                    """,
                    (venue, now),
                )
            connection.execute(
                """
                INSERT INTO runtime_status_snapshot (id, updated_at, metrics_json)
                VALUES (1, ?, ?)
                ON CONFLICT(id) DO NOTHING
                """,
                (now, _json_dumps({})),
            )
            connection.commit()

    def fetch_fresh_trade_rows(self, fresh_window_days: int) -> list[sqlite3.Row]:
        self.ensure_tables()
        with self.connect() as connection:
            cursor = connection.execute(
                """
                SELECT
                    venue,
                    execution_mode,
                    instrument_type,
                    market_id,
                    status,
                    pnl,
                    timestamp,
                    opened_at,
                    closed_at
                FROM trades
                WHERE strategy_profile = 'binance_technical_sampling'
                  AND sample_kind = 'live_paper'
                  AND venue IN ('binance_futures', 'binance_spot')
                  AND COALESCE(opened_at, timestamp) >= datetime('now', ?)
                ORDER BY COALESCE(closed_at, opened_at, timestamp) DESC, id DESC
                """,
                (f"-{max(fresh_window_days, 1)} days",),
            )
            return cursor.fetchall()

    def fetch_technical_decision_rows(self, fresh_window_days: int) -> list[sqlite3.Row]:
        self.ensure_tables()
        with self.connect() as connection:
            cursor = connection.execute(
                """
                SELECT
                    id,
                    occurred_at,
                    venue,
                    market_id,
                    action,
                    reason,
                    decision_score,
                    threshold,
                    trade_size,
                    inputs_json
                FROM decision_audit
                WHERE strategy_profile = 'binance_technical_sampling'
                  AND occurred_at >= datetime('now', ?)
                ORDER BY occurred_at DESC, id DESC
                """,
                (f"-{max(fresh_window_days, 1)} days",),
            )
            return cursor.fetchall()

    def fetch_open_positions(self) -> list[sqlite3.Row]:
        return self.fetch_open_positions_for_venues(("binance_futures", "binance_spot"))

    def fetch_open_positions_for_venues(self, venues: Sequence[str] | None = None) -> list[sqlite3.Row]:
        self.ensure_tables()
        clauses = ["status = 'OPEN'"]
        params: list[Any] = []
        if venues:
            placeholders = ",".join("?" for _ in venues)
            clauses.append(f"venue IN ({placeholders})")
            params.extend(venues)
        where_clause = " AND ".join(clauses)
        with self.connect() as connection:
            cursor = connection.execute(
                f"""
                SELECT
                    id,
                    venue,
                    execution_mode,
                    instrument_type,
                    symbol_or_market_id,
                    side,
                    qty,
                    entry_price,
                    mark_price,
                    notional_usd,
                    unrealized_pnl,
                    realized_pnl,
                    leverage,
                    strategy_profile,
                    sample_kind,
                    source_signal,
                    signal_family,
                    status,
                    opened_at,
                    closed_at,
                    take_profit_price,
                    stop_loss_price,
                    linked_trade_id
                FROM venue_positions
                WHERE {where_clause}
                ORDER BY opened_at ASC, id ASC
                """,
                tuple(params),
            )
            return cursor.fetchall()

    def fetch_open_position(self, venue: str, symbol_or_market_id: str) -> sqlite3.Row | None:
        self.ensure_tables()
        with self.connect() as connection:
            cursor = connection.execute(
                """
                SELECT *
                FROM venue_positions
                WHERE venue = ?
                  AND symbol_or_market_id = ?
                  AND status = 'OPEN'
                ORDER BY id DESC
                LIMIT 1
                """,
                (venue, symbol_or_market_id),
            )
            return cursor.fetchone()

    def fetch_open_orders(
        self,
        venue: str | None = None,
        symbol_or_market_id: str | None = None,
    ) -> list[sqlite3.Row]:
        self.ensure_tables()
        clauses = ["status = 'OPEN'"]
        params: list[Any] = []
        if venue:
            clauses.append("venue = ?")
            params.append(venue)
        if symbol_or_market_id:
            clauses.append("symbol_or_market_id = ?")
            params.append(symbol_or_market_id)
        where_clause = " AND ".join(clauses)
        with self.connect() as connection:
            cursor = connection.execute(
                f"""
                SELECT *
                FROM venue_orders
                WHERE {where_clause}
                ORDER BY id ASC
                """,
                tuple(params),
            )
            return cursor.fetchall()

    def fetch_runtime_status_snapshot(self) -> dict[str, Any]:
        self.ensure_tables()
        with self.connect() as connection:
            row = connection.execute(
                "SELECT metrics_json FROM runtime_status_snapshot WHERE id = 1"
            ).fetchone()
        if row is None:
            return {}
        try:
            metrics = json.loads(row["metrics_json"] or "{}")
        except (TypeError, ValueError):
            return {}
        return metrics if isinstance(metrics, dict) else {}

    def insert_decision_audit(
        self,
        *,
        venue: str,
        market_id: str,
        action: str,
        reason: str,
        decision_score: float,
        threshold: float,
        trade_size: float,
        inputs: dict[str, Any] | None,
        category: str = "CRYPTO",
        signal_family: str = "binance_technical_momentum",
        strategy_profile: str = "binance_technical_sampling",
        raw_source_signal: str = "binance_technical_momentum",
        confidence: float | None = None,
    ) -> int:
        self.ensure_tables()
        now = _utc_now()
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO decision_audit (
                    occurred_at,
                    venue,
                    market_id,
                    category,
                    signal_family,
                    strategy_profile,
                    raw_source_signal,
                    action,
                    reason,
                    decision_score,
                    threshold,
                    trade_size,
                    confidence,
                    inputs_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    now,
                    venue,
                    market_id,
                    category,
                    signal_family,
                    strategy_profile,
                    raw_source_signal,
                    action,
                    reason,
                    float(decision_score),
                    float(threshold),
                    float(trade_size),
                    float(confidence) if confidence is not None else None,
                    _json_dumps(inputs),
                ),
            )
            connection.commit()
            return int(cursor.lastrowid)

    def create_entry(
        self,
        *,
        venue: str,
        symbol_or_market_id: str,
        execution_mode: str,
        instrument_type: str,
        direction: str,
        entry_price: float,
        trade_size_usd: float,
        leverage: float,
        confidence: float,
        strategy_profile: str,
        sample_kind: str,
        source_signal: str,
        signal_family: str,
        take_profit_price: float,
        stop_loss_price: float,
        occurred_at: str | None = None,
    ) -> dict[str, Any]:
        self.ensure_tables()
        now = occurred_at or _utc_now()
        qty = (float(trade_size_usd) / float(entry_price)) if entry_price else 0.0
        entry_side = "BUY" if direction == "LONG" else "SELL"
        exit_side = "SELL" if direction == "LONG" else "BUY"

        with self.connect() as connection:
            trade_cursor = connection.execute(
                """
                INSERT INTO trades (
                    venue,
                    execution_mode,
                    instrument_type,
                    market_id,
                    side,
                    size,
                    price,
                    confidence,
                    source_signal,
                    category,
                    strategy_profile,
                    sample_kind,
                    status,
                    pnl,
                    whale_address,
                    timestamp,
                    opened_at,
                    closed_at,
                    is_synthetic
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'CRYPTO', ?, ?, 'OPEN', 0, NULL, ?, ?, NULL, 0)
                """,
                (
                    venue,
                    execution_mode,
                    instrument_type,
                    symbol_or_market_id,
                    entry_side,
                    float(trade_size_usd),
                    float(entry_price),
                    float(confidence),
                    source_signal,
                    strategy_profile,
                    sample_kind,
                    now,
                    now,
                ),
            )
            trade_id = int(trade_cursor.lastrowid)

            position_cursor = connection.execute(
                """
                INSERT INTO venue_positions (
                    venue,
                    execution_mode,
                    instrument_type,
                    symbol_or_market_id,
                    side,
                    qty,
                    entry_price,
                    mark_price,
                    notional_usd,
                    unrealized_pnl,
                    realized_pnl,
                    leverage,
                    strategy_profile,
                    sample_kind,
                    source_signal,
                    signal_family,
                    status,
                    opened_at,
                    closed_at,
                    take_profit_price,
                    stop_loss_price,
                    linked_trade_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?, ?, ?, ?, 'OPEN', ?, NULL, ?, ?, ?)
                """,
                (
                    venue,
                    execution_mode,
                    instrument_type,
                    symbol_or_market_id,
                    direction,
                    float(qty),
                    float(entry_price),
                    float(entry_price),
                    float(trade_size_usd),
                    float(leverage),
                    strategy_profile,
                    sample_kind,
                    source_signal,
                    signal_family,
                    now,
                    float(take_profit_price),
                    float(stop_loss_price),
                    trade_id,
                ),
            )
            position_id = int(position_cursor.lastrowid)

            connection.execute(
                """
                INSERT INTO venue_orders (
                    venue,
                    execution_mode,
                    symbol_or_market_id,
                    order_type,
                    side,
                    qty,
                    price,
                    stop_price,
                    reduce_only,
                    status,
                    created_at,
                    closed_at,
                    linked_position_id
                ) VALUES (?, ?, ?, 'TAKE_PROFIT', ?, ?, ?, NULL, 1, 'OPEN', ?, NULL, ?)
                """,
                (
                    venue,
                    execution_mode,
                    symbol_or_market_id,
                    exit_side,
                    float(qty),
                    float(take_profit_price),
                    now,
                    position_id,
                ),
            )
            connection.execute(
                """
                INSERT INTO venue_orders (
                    venue,
                    execution_mode,
                    symbol_or_market_id,
                    order_type,
                    side,
                    qty,
                    price,
                    stop_price,
                    reduce_only,
                    status,
                    created_at,
                    closed_at,
                    linked_position_id
                ) VALUES (?, ?, ?, 'STOP_LOSS', ?, ?, ?, ?, 1, 'OPEN', ?, NULL, ?)
                """,
                (
                    venue,
                    execution_mode,
                    symbol_or_market_id,
                    exit_side,
                    float(qty),
                    float(stop_loss_price),
                    float(stop_loss_price),
                    now,
                    position_id,
                ),
            )
            connection.commit()

        return {
            "trade_id": trade_id,
            "position_id": position_id,
            "qty": qty,
            "entry_side": entry_side,
            "exit_side": exit_side,
            "entry_price": float(entry_price),
            "trade_size_usd": float(trade_size_usd),
            "take_profit_price": float(take_profit_price),
            "stop_loss_price": float(stop_loss_price),
        }

    def update_open_position_mark(
        self,
        *,
        position_id: int,
        mark_price: float,
        notional_usd: float,
        unrealized_pnl: float,
    ) -> None:
        self.ensure_tables()
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE venue_positions
                SET mark_price = ?, notional_usd = ?, unrealized_pnl = ?
                WHERE id = ? AND status = 'OPEN'
                """,
                (float(mark_price), float(notional_usd), float(unrealized_pnl), int(position_id)),
            )
            connection.commit()

    def close_position(
        self,
        *,
        position_id: int,
        close_price: float,
        closed_reason: str,
        closed_at: str | None = None,
    ) -> dict[str, Any] | None:
        self.ensure_tables()
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM venue_positions
                WHERE id = ? AND status = 'OPEN'
                """,
                (int(position_id),),
            ).fetchone()
            if row is None:
                return None

            closed_ts = closed_at or _utc_now()
            qty = float(row["qty"] or 0.0)
            entry_price = float(row["entry_price"] or 0.0)
            side = str(row["side"] or "LONG").upper()
            notional = qty * float(close_price)
            pnl = ((float(close_price) - entry_price) * qty) if side == "LONG" else ((entry_price - float(close_price)) * qty)
            trade_status = "CLOSED_WIN" if pnl > 0 else ("CLOSED_LOSS" if pnl < 0 else "CLOSED_FLAT")

            connection.execute(
                """
                UPDATE venue_positions
                SET status = 'CLOSED',
                    mark_price = ?,
                    notional_usd = ?,
                    unrealized_pnl = 0,
                    realized_pnl = ?,
                    closed_at = ?
                WHERE id = ?
                """,
                (float(close_price), float(notional), float(pnl), closed_ts, int(position_id)),
            )

            linked_trade_id = row["linked_trade_id"]
            if linked_trade_id is not None:
                connection.execute(
                    """
                    UPDATE trades
                    SET status = ?,
                        pnl = ?,
                        closed_at = ?
                    WHERE id = ?
                    """,
                    (trade_status, float(pnl), closed_ts, int(linked_trade_id)),
                )

            connection.execute(
                """
                UPDATE venue_orders
                SET status = 'CLOSED',
                    closed_at = ?
                WHERE linked_position_id = ?
                  AND status = 'OPEN'
                """,
                (closed_ts, int(position_id)),
            )
            connection.commit()

        return {
            "position_id": int(position_id),
            "venue": str(row["venue"] or ""),
            "symbol_or_market_id": str(row["symbol_or_market_id"] or ""),
            "side": side,
            "qty": qty,
            "entry_price": entry_price,
            "close_price": float(close_price),
            "realized_pnl": float(round(pnl, 6)),
            "closed_reason": closed_reason,
            "closed_at": closed_ts,
        }

    def refresh_account_snapshots(self, venues: Sequence[str]) -> None:
        self.ensure_tables()
        now = _utc_now()
        with self.connect() as connection:
            wallet_balance = 1000.0
            for venue in venues:
                realized_row = connection.execute(
                    """
                    SELECT COALESCE(SUM(pnl), 0) AS value
                    FROM trades
                    WHERE venue = ?
                      AND execution_mode = 'paper'
                      AND status LIKE 'CLOSED%'
                    """,
                    (venue,),
                ).fetchone()
                unrealized_row = connection.execute(
                    """
                    SELECT
                        COALESCE(SUM(unrealized_pnl), 0) AS unrealized,
                        COALESCE(SUM(notional_usd), 0) AS notional
                    FROM venue_positions
                    WHERE venue = ?
                      AND execution_mode = 'paper'
                      AND status = 'OPEN'
                    """,
                    (venue,),
                ).fetchone()
                realized_pnl = float(realized_row["value"] or 0.0)
                unrealized_pnl = float(unrealized_row["unrealized"] or 0.0)
                open_notional = float(unrealized_row["notional"] or 0.0)
                cash_balance = 1000.0 + realized_pnl
                equity = cash_balance + unrealized_pnl
                available_balance = max(cash_balance - open_notional, 0.0)
                wallet_balance += realized_pnl + unrealized_pnl
                connection.execute(
                    """
                    INSERT INTO venue_accounts (
                        venue, execution_mode, cash_balance, equity, available_balance, updated_at
                    ) VALUES (?, 'paper', ?, ?, ?, ?)
                    ON CONFLICT(venue, execution_mode) DO UPDATE SET
                        cash_balance = excluded.cash_balance,
                        equity = excluded.equity,
                        available_balance = excluded.available_balance,
                        updated_at = excluded.updated_at
                    """,
                    (venue, cash_balance, equity, available_balance, now),
                )

            connection.execute(
                """
                INSERT INTO wallet (id, balance, updated_at)
                VALUES (1, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    balance = excluded.balance,
                    updated_at = excluded.updated_at
                """,
                (wallet_balance, now),
            )
            connection.commit()

    def upsert_runtime_status_snapshot(self, metrics: dict[str, Any]) -> None:
        self.ensure_tables()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO runtime_status_snapshot (id, updated_at, metrics_json)
                VALUES (1, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    updated_at = excluded.updated_at,
                    metrics_json = excluded.metrics_json
                """,
                (_utc_now(), _json_dumps(metrics)),
            )
            connection.commit()
