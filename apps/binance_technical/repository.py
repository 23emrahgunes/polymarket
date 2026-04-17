from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


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

    def fetch_fresh_trade_rows(self, fresh_window_days: int) -> list[sqlite3.Row]:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                SELECT
                    venue,
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
                ORDER BY COALESCE(closed_at, opened_at, timestamp) DESC
                """,
                (f"-{max(fresh_window_days, 1)} days",),
            )
            return cursor.fetchall()

    def fetch_technical_decision_rows(self, fresh_window_days: int) -> list[sqlite3.Row]:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                SELECT
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
        with self.connect() as connection:
            cursor = connection.execute(
                """
                SELECT
                    venue,
                    execution_mode,
                    symbol_or_market_id,
                    strategy_profile,
                    sample_kind,
                    status,
                    source_signal,
                    signal_family,
                    notional_usd,
                    unrealized_pnl,
                    opened_at
                FROM venue_positions
                WHERE status = 'OPEN'
                  AND venue IN ('binance_futures', 'binance_spot')
                ORDER BY opened_at ASC, id ASC
                """
            )
            return cursor.fetchall()
