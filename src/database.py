from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Iterable, List, Optional

import aiosqlite

from src.evaluation_utils import (
    infer_debug_profile_from_db_path,
    infer_sample_kind_from_db_path,
    is_synthetic_sample,
    normalize_signal_family,
    normalize_strategy_profile,
)
from src.market_mapping import choose_primary_token_alias, normalize_market_alias


class Database:
    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or os.getenv("GHOST_TRADER_DB_PATH", "data/ghost_trader.db")
        parent_dir = os.path.dirname(self.db_path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)
        self.conn: aiosqlite.Connection | None = None

    async def connect(self):
        self.conn = await aiosqlite.connect(self.db_path)
        self.conn.row_factory = aiosqlite.Row
        await self._create_tables()
        await self._migrate_tables()
        await self._backfill_evaluation_defaults()
        await self.ensure_venue_account("polymarket", "paper")
        await self.ensure_venue_account("binance_futures", "paper")
        await self.ensure_venue_account("binance_spot", "paper")

    async def _create_tables(self):
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS wallet (
                id INTEGER PRIMARY KEY,
                balance REAL NOT NULL
            )
            """
        )
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market_id TEXT NOT NULL,
                side TEXT NOT NULL,
                size REAL NOT NULL,
                price REAL NOT NULL,
                edge REAL NOT NULL,
                confidence REAL NOT NULL,
                status TEXT NOT NULL,
                pnl REAL DEFAULT 0,
                whale_address TEXT,
                venue TEXT DEFAULT 'polymarket',
                instrument_type TEXT DEFAULT 'prediction',
                position_id INTEGER,
                source_signal TEXT DEFAULT 'runtime',
                category TEXT,
                signal_family TEXT DEFAULT 'unknown',
                strategy_profile TEXT DEFAULT 'baseline',
                sample_kind TEXT DEFAULT 'live_paper',
                is_synthetic INTEGER DEFAULT 0,
                debug_profile TEXT,
                entry_spread_pct REAL,
                slippage_proxy_bps REAL,
                whale_trust_at_entry REAL,
                execution_mode TEXT DEFAULT 'paper',
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                opened_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                closed_at DATETIME,
                hold_seconds REAL
            )
            """
        )
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS whale_stats (
                address TEXT PRIMARY KEY,
                total_trades INTEGER DEFAULT 0,
                wins INTEGER DEFAULT 0,
                total_pnl REAL DEFAULT 0,
                trust_score REAL DEFAULT 0.5,
                last_active DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS venue_accounts (
                venue TEXT NOT NULL,
                execution_mode TEXT NOT NULL,
                cash_balance REAL NOT NULL,
                equity REAL NOT NULL,
                available_balance REAL NOT NULL,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (venue, execution_mode)
            )
            """
        )
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS venue_positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                venue TEXT NOT NULL,
                execution_mode TEXT NOT NULL,
                instrument_type TEXT NOT NULL,
                symbol_or_market_id TEXT NOT NULL,
                side TEXT NOT NULL,
                qty_or_shares REAL NOT NULL,
                entry_price REAL NOT NULL,
                mark_price REAL,
                notional_usd REAL NOT NULL,
                unrealized_pnl REAL DEFAULT 0,
                realized_pnl REAL DEFAULT 0,
                leverage INTEGER DEFAULT 1,
                status TEXT NOT NULL DEFAULT 'OPEN',
                source_signal TEXT,
                category TEXT,
                signal_family TEXT DEFAULT 'unknown',
                strategy_profile TEXT DEFAULT 'baseline',
                sample_kind TEXT DEFAULT 'live_paper',
                is_synthetic INTEGER DEFAULT 0,
                debug_profile TEXT,
                entry_spread_pct REAL,
                slippage_proxy_bps REAL,
                whale_trust_at_entry REAL,
                linked_market_id TEXT,
                opened_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                closed_at DATETIME
            )
            """
        )
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS venue_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                venue TEXT NOT NULL,
                execution_mode TEXT NOT NULL,
                position_id INTEGER,
                symbol_or_market_id TEXT NOT NULL,
                client_order_id TEXT,
                external_order_id TEXT,
                order_type TEXT NOT NULL,
                side TEXT NOT NULL,
                qty REAL NOT NULL,
                price REAL,
                status TEXT NOT NULL,
                reduce_only INTEGER DEFAULT 0,
                stop_price REAL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS whale_wallets (
                address TEXT PRIMARY KEY,
                source_type TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                first_seen_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                last_seen_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                last_event_at DATETIME,
                last_success_at DATETIME,
                last_failure_at DATETIME,
                failure_streak INTEGER NOT NULL DEFAULT 0,
                discovery_score REAL NOT NULL DEFAULT 0,
                last_event_amount REAL NOT NULL DEFAULT 0,
                last_event_category TEXT,
                event_count_24h INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS decision_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                occurred_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                venue TEXT,
                market_id TEXT NOT NULL,
                category TEXT,
                signal_family TEXT DEFAULT 'unknown',
                strategy_profile TEXT DEFAULT 'baseline',
                raw_source_signal TEXT,
                sample_kind TEXT DEFAULT 'live_paper',
                is_synthetic INTEGER DEFAULT 0,
                decision_score REAL,
                threshold REAL,
                trade_size REAL,
                action TEXT NOT NULL,
                reason TEXT,
                confidence REAL,
                whale_trust REAL,
                spread_pct REAL,
                slippage_proxy_bps REAL,
                inputs_json TEXT,
                mapping_stage TEXT,
                alias_candidates_json TEXT,
                lazy_lookup_attempted INTEGER DEFAULT 0,
                lazy_lookup_hit INTEGER DEFAULT 0,
                hot_window_promoted INTEGER DEFAULT 0
            )
            """
        )
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS market_aliases (
                alias TEXT PRIMARY KEY,
                alias_type TEXT NOT NULL,
                market_id TEXT NOT NULL,
                question TEXT,
                category TEXT,
                volume_24h REAL DEFAULT 0,
                active INTEGER NOT NULL DEFAULT 1,
                last_seen_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                source TEXT
            )
            """
        )
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS runtime_status_snapshot (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                metrics_json TEXT NOT NULL
            )
            """
        )

        async with self.conn.execute("SELECT COUNT(*) AS count FROM wallet") as cursor:
            row = await cursor.fetchone()
            if row["count"] == 0:
                default_balance = float(os.getenv("VIRTUAL_BALANCE", "1000.0"))
                await self.conn.execute("INSERT INTO wallet (id, balance) VALUES (1, ?)", (default_balance,))
        await self.conn.commit()

    async def _migrate_tables(self):
        for statement in [
            "ALTER TABLE trades ADD COLUMN pnl REAL DEFAULT 0",
            "ALTER TABLE trades ADD COLUMN whale_address TEXT",
            "ALTER TABLE trades ADD COLUMN venue TEXT DEFAULT 'polymarket'",
            "ALTER TABLE trades ADD COLUMN instrument_type TEXT DEFAULT 'prediction'",
            "ALTER TABLE trades ADD COLUMN position_id INTEGER",
            "ALTER TABLE trades ADD COLUMN source_signal TEXT DEFAULT 'runtime'",
            "ALTER TABLE trades ADD COLUMN category TEXT",
            "ALTER TABLE trades ADD COLUMN signal_family TEXT DEFAULT 'unknown'",
            "ALTER TABLE trades ADD COLUMN strategy_profile TEXT DEFAULT 'baseline'",
            "ALTER TABLE trades ADD COLUMN sample_kind TEXT DEFAULT 'live_paper'",
            "ALTER TABLE trades ADD COLUMN is_synthetic INTEGER DEFAULT 0",
            "ALTER TABLE trades ADD COLUMN debug_profile TEXT",
            "ALTER TABLE trades ADD COLUMN entry_spread_pct REAL",
            "ALTER TABLE trades ADD COLUMN slippage_proxy_bps REAL",
            "ALTER TABLE trades ADD COLUMN whale_trust_at_entry REAL",
            "ALTER TABLE trades ADD COLUMN execution_mode TEXT DEFAULT 'paper'",
            "ALTER TABLE trades ADD COLUMN opened_at DATETIME",
            "ALTER TABLE trades ADD COLUMN closed_at DATETIME",
            "ALTER TABLE trades ADD COLUMN hold_seconds REAL",
            "ALTER TABLE venue_positions ADD COLUMN source_signal TEXT",
            "ALTER TABLE venue_positions ADD COLUMN category TEXT",
            "ALTER TABLE venue_positions ADD COLUMN signal_family TEXT DEFAULT 'unknown'",
            "ALTER TABLE venue_positions ADD COLUMN strategy_profile TEXT DEFAULT 'baseline'",
            "ALTER TABLE venue_positions ADD COLUMN sample_kind TEXT DEFAULT 'live_paper'",
            "ALTER TABLE venue_positions ADD COLUMN is_synthetic INTEGER DEFAULT 0",
            "ALTER TABLE venue_positions ADD COLUMN debug_profile TEXT",
            "ALTER TABLE venue_positions ADD COLUMN entry_spread_pct REAL",
            "ALTER TABLE venue_positions ADD COLUMN slippage_proxy_bps REAL",
            "ALTER TABLE venue_positions ADD COLUMN whale_trust_at_entry REAL",
            "ALTER TABLE venue_positions ADD COLUMN linked_market_id TEXT",
            "ALTER TABLE venue_positions ADD COLUMN mark_price REAL",
            "ALTER TABLE whale_wallets ADD COLUMN last_event_at DATETIME",
            "ALTER TABLE whale_wallets ADD COLUMN last_success_at DATETIME",
            "ALTER TABLE whale_wallets ADD COLUMN last_failure_at DATETIME",
            "ALTER TABLE whale_wallets ADD COLUMN failure_streak INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE whale_wallets ADD COLUMN discovery_score REAL NOT NULL DEFAULT 0",
            "ALTER TABLE whale_wallets ADD COLUMN last_event_amount REAL NOT NULL DEFAULT 0",
            "ALTER TABLE whale_wallets ADD COLUMN last_event_category TEXT",
            "ALTER TABLE whale_wallets ADD COLUMN event_count_24h INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE decision_audit ADD COLUMN mapping_stage TEXT",
            "ALTER TABLE decision_audit ADD COLUMN alias_candidates_json TEXT",
            "ALTER TABLE decision_audit ADD COLUMN lazy_lookup_attempted INTEGER DEFAULT 0",
            "ALTER TABLE decision_audit ADD COLUMN lazy_lookup_hit INTEGER DEFAULT 0",
            "ALTER TABLE decision_audit ADD COLUMN hot_window_promoted INTEGER DEFAULT 0",
            "ALTER TABLE decision_audit ADD COLUMN strategy_profile TEXT DEFAULT 'baseline'",
        ]:
            try:
                await self.conn.execute(statement)
            except Exception:
                pass
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS market_aliases (
                alias TEXT PRIMARY KEY,
                alias_type TEXT NOT NULL,
                market_id TEXT NOT NULL,
                question TEXT,
                category TEXT,
                volume_24h REAL DEFAULT 0,
                active INTEGER NOT NULL DEFAULT 1,
                last_seen_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                source TEXT
            )
            """
        )
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS runtime_status_snapshot (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                metrics_json TEXT NOT NULL
            )
            """
        )
        await self.conn.commit()

    async def _backfill_evaluation_defaults(self) -> None:
        sample_kind = infer_sample_kind_from_db_path(self.db_path)
        debug_profile = infer_debug_profile_from_db_path(self.db_path)
        signal_family_updates = {
            "discovery": ("discovery", "blended_crypto", "binance_futures_price_structure", "binance_spot_price_structure"),
            "whale": ("whale_tracker",),
            "activity_orderflow": ("activity", "cluster_detected"),
        }

        await self.conn.execute("UPDATE trades SET opened_at = COALESCE(opened_at, timestamp)")
        await self.conn.execute("UPDATE venue_positions SET opened_at = COALESCE(opened_at, CURRENT_TIMESTAMP)")
        await self.conn.execute(
            "UPDATE trades SET sample_kind = COALESCE(NULLIF(sample_kind, ''), ?) WHERE sample_kind IS NULL OR sample_kind = ''",
            (sample_kind,),
        )
        await self.conn.execute(
            "UPDATE trades SET strategy_profile = COALESCE(NULLIF(strategy_profile, ''), 'baseline') WHERE strategy_profile IS NULL OR strategy_profile = ''"
        )
        await self.conn.execute(
            "UPDATE trades SET debug_profile = COALESCE(debug_profile, ?) WHERE debug_profile IS NULL",
            (debug_profile,),
        )
        await self.conn.execute(
            "UPDATE trades SET is_synthetic = CASE WHEN sample_kind = 'live_paper' THEN 0 ELSE 1 END"
        )
        await self.conn.execute(
            "UPDATE venue_positions SET sample_kind = COALESCE(NULLIF(sample_kind, ''), ?) WHERE sample_kind IS NULL OR sample_kind = ''",
            (sample_kind,),
        )
        await self.conn.execute(
            "UPDATE venue_positions SET strategy_profile = COALESCE(NULLIF(strategy_profile, ''), 'baseline') WHERE strategy_profile IS NULL OR strategy_profile = ''"
        )
        await self.conn.execute(
            "UPDATE venue_positions SET debug_profile = COALESCE(debug_profile, ?) WHERE debug_profile IS NULL",
            (debug_profile,),
        )
        await self.conn.execute(
            "UPDATE venue_positions SET is_synthetic = CASE WHEN sample_kind = 'live_paper' THEN 0 ELSE 1 END"
        )
        await self.conn.execute(
            """
            UPDATE trades
            SET category = 'CRYPTO'
            WHERE (category IS NULL OR category = '')
              AND instrument_type IN ('futures', 'spot')
            """
        )
        await self.conn.execute(
            """
            UPDATE venue_positions
            SET category = 'CRYPTO'
            WHERE (category IS NULL OR category = '')
              AND instrument_type IN ('futures', 'spot')
            """
        )

        for normalized, source_signals in signal_family_updates.items():
            placeholders = ", ".join("?" for _ in source_signals)
            await self.conn.execute(
                f"""
                UPDATE trades
                SET signal_family = ?
                WHERE source_signal IN ({placeholders}) AND (signal_family IS NULL OR signal_family = '' OR signal_family = 'unknown')
                """,
                (normalized, *source_signals),
            )
            await self.conn.execute(
                f"""
                UPDATE venue_positions
                SET signal_family = ?
                WHERE source_signal IN ({placeholders}) AND (signal_family IS NULL OR signal_family = '' OR signal_family = 'unknown')
                """,
                (normalized, *source_signals),
            )

        await self.conn.execute(
            "UPDATE decision_audit SET strategy_profile = COALESCE(NULLIF(strategy_profile, ''), 'baseline') WHERE strategy_profile IS NULL OR strategy_profile = ''"
        )

        await self.conn.commit()

    async def ensure_venue_account(self, venue: str, execution_mode: str = "paper", initial_balance: float | None = None):
        if initial_balance is None and venue == "polymarket" and execution_mode == "paper":
            async with self.conn.execute("SELECT balance FROM wallet WHERE id = 1") as cursor:
                wallet_row = await cursor.fetchone()
                initial_balance = float(wallet_row["balance"]) if wallet_row else float(os.getenv("VIRTUAL_BALANCE", "1000.0"))
        elif initial_balance is None:
            initial_balance = float(os.getenv("VIRTUAL_BALANCE", "1000.0"))

        await self.conn.execute(
            """
            INSERT OR IGNORE INTO venue_accounts (venue, execution_mode, cash_balance, equity, available_balance)
            VALUES (?, ?, ?, ?, ?)
            """,
            (venue, execution_mode, initial_balance, initial_balance, initial_balance),
        )
        await self.conn.commit()

    async def get_venue_account(self, venue: str = "polymarket", execution_mode: str = "paper"):
        await self.ensure_venue_account(venue, execution_mode)
        async with self.conn.execute(
            "SELECT * FROM venue_accounts WHERE venue = ? AND execution_mode = ?",
            (venue, execution_mode),
        ) as cursor:
            return await cursor.fetchone()

    async def get_all_venue_accounts(self) -> List[aiosqlite.Row]:
        async with self.conn.execute("SELECT * FROM venue_accounts ORDER BY venue, execution_mode") as cursor:
            return await cursor.fetchall()

    async def get_balance(self, venue: str = "polymarket", execution_mode: str = "paper") -> float:
        if venue == "polymarket" and execution_mode == "paper":
            async with self.conn.execute("SELECT balance FROM wallet WHERE id = 1") as cursor:
                row = await cursor.fetchone()
                return float(row["balance"])
        account = await self.get_venue_account(venue, execution_mode)
        return float(account["cash_balance"]) if account else 0.0

    async def update_balance(self, new_balance: float, venue: str = "polymarket", execution_mode: str = "paper"):
        await self.ensure_venue_account(venue, execution_mode)
        await self.conn.execute(
            """
            UPDATE venue_accounts
            SET cash_balance = ?, equity = ?, available_balance = ?, updated_at = CURRENT_TIMESTAMP
            WHERE venue = ? AND execution_mode = ?
            """,
            (new_balance, new_balance, new_balance, venue, execution_mode),
        )
        if venue == "polymarket" and execution_mode == "paper":
            await self.conn.execute("UPDATE wallet SET balance = ? WHERE id = 1", (new_balance,))
        await self.conn.commit()

    async def add_trade(
        self,
        market_id,
        side,
        size,
        price,
        edge,
        confidence,
        status="OPEN",
        whale_address=None,
        venue: str = "polymarket",
        instrument_type: str = "prediction",
        position_id: int | None = None,
        source_signal: str = "runtime",
        execution_mode: str = "paper",
        category: str | None = None,
        signal_family: str | None = None,
        strategy_profile: str | None = None,
        sample_kind: str | None = None,
        is_synthetic: bool | None = None,
        debug_profile: str | None = None,
        entry_spread_pct: float | None = None,
        slippage_proxy_bps: float | None = None,
        whale_trust_at_entry: float | None = None,
        opened_at: str | None = None,
        closed_at: str | None = None,
        hold_seconds: float | None = None,
    ) -> int:
        normalized_signal_family = signal_family or normalize_signal_family(source_signal)
        normalized_strategy_profile = normalize_strategy_profile(strategy_profile)
        normalized_sample_kind = sample_kind or infer_sample_kind_from_db_path(self.db_path)
        normalized_is_synthetic = int(is_synthetic_sample(normalized_sample_kind) if is_synthetic is None else bool(is_synthetic))
        normalized_opened_at = opened_at or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        cursor = await self.conn.execute(
            """
            INSERT INTO trades (
                market_id, side, size, price, edge, confidence, status, whale_address,
                venue, instrument_type, position_id, source_signal, category, signal_family, strategy_profile,
                sample_kind, is_synthetic, debug_profile, entry_spread_pct, slippage_proxy_bps,
                whale_trust_at_entry, execution_mode, opened_at, closed_at, hold_seconds
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                market_id,
                side,
                size,
                price,
                edge,
                confidence,
                status,
                whale_address,
                venue,
                instrument_type,
                position_id,
                source_signal,
                category,
                normalized_signal_family,
                normalized_strategy_profile,
                normalized_sample_kind,
                normalized_is_synthetic,
                debug_profile,
                entry_spread_pct,
                slippage_proxy_bps,
                whale_trust_at_entry,
                execution_mode,
                normalized_opened_at,
                closed_at,
                hold_seconds,
            ),
        )
        await self.conn.commit()
        return int(cursor.lastrowid)

    async def get_open_trades(
        self,
        venue: str | None = None,
        instrument_type: str | None = None,
    ) -> List[aiosqlite.Row]:
        query = "SELECT * FROM trades WHERE status = 'OPEN'"
        params: list = []
        if venue is not None:
            query += " AND venue = ?"
            params.append(venue)
        if instrument_type is not None:
            query += " AND instrument_type = ?"
            params.append(instrument_type)
        query += " ORDER BY id ASC"
        async with self.conn.execute(query, params) as cursor:
            return await cursor.fetchall()

    async def get_recent_trades(self, limit: int = 10) -> List[aiosqlite.Row]:
        async with self.conn.execute("SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,)) as cursor:
            return await cursor.fetchall()

    async def get_trade(self, trade_id: int) -> Optional[aiosqlite.Row]:
        async with self.conn.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)) as cursor:
            return await cursor.fetchone()

    async def has_open_trade(self, market_id: str, side: str, venue: str = "polymarket") -> bool:
        async with self.conn.execute(
            "SELECT 1 FROM trades WHERE market_id = ? AND side = ? AND venue = ? AND status = 'OPEN' LIMIT 1",
            (market_id, side, venue),
        ) as cursor:
            return await cursor.fetchone() is not None

    async def has_open_trade_any_side(self, market_id: str, venue: str = "polymarket") -> bool:
        async with self.conn.execute(
            "SELECT 1 FROM trades WHERE market_id = ? AND venue = ? AND status = 'OPEN' LIMIT 1",
            (market_id, venue),
        ) as cursor:
            return await cursor.fetchone() is not None

    async def update_trade_resolution(self, trade_id, status, pnl):
        trade = await self.get_trade(trade_id)
        closed_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        hold_seconds = self._calculate_hold_seconds(trade["opened_at"] if trade else None, closed_at)
        await self.conn.execute(
            "UPDATE trades SET status = ?, pnl = ?, closed_at = ?, hold_seconds = ? WHERE id = ?",
            (status, pnl, closed_at, hold_seconds, trade_id),
        )
        await self.conn.commit()

    async def update_whale_stats(self, address, pnl):
        win = 1 if pnl > 0 else 0
        await self.conn.execute(
            """
            INSERT INTO whale_stats (address, total_trades, wins, total_pnl, last_active)
            VALUES (?, 1, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(address) DO UPDATE SET
                total_trades = total_trades + 1,
                wins = wins + ?,
                total_pnl = total_pnl + ?,
                last_active = CURRENT_TIMESTAMP
            """,
            (address, win, pnl, win, pnl),
        )
        await self.conn.execute(
            """
            UPDATE whale_stats SET trust_score = CAST(wins AS REAL) / total_trades
            WHERE address = ?
            """,
            (address,),
        )
        await self.conn.commit()

    async def get_whale_stats(self, address):
        async with self.conn.execute("SELECT * FROM whale_stats WHERE address = ?", (address,)) as cursor:
            return await cursor.fetchone()

    async def get_whale_wallet(self, address: str):
        async with self.conn.execute("SELECT * FROM whale_wallets WHERE address = ?", (address,)) as cursor:
            return await cursor.fetchone()

    async def upsert_market_aliases(
        self,
        market_id: str,
        aliases: Iterable[str],
        *,
        question: str | None = None,
        category: str | None = None,
        volume_24h: float | None = None,
        active: bool = True,
        source: str = "explorer",
        seen_at: str | None = None,
    ) -> None:
        normalized_market_id = normalize_market_alias(market_id)
        if not normalized_market_id:
            return

        seen_at = seen_at or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        normalized_aliases: list[str] = []
        for alias in aliases:
            normalized_alias = normalize_market_alias(alias)
            if normalized_alias and normalized_alias not in normalized_aliases:
                normalized_aliases.append(normalized_alias)

        if not normalized_aliases:
            return

        for alias in normalized_aliases:
            alias_type = "market_id" if alias == normalized_market_id else "token_id"
            await self.conn.execute(
                """
                INSERT INTO market_aliases (
                    alias, alias_type, market_id, question, category, volume_24h, active, last_seen_at, source
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(alias) DO UPDATE SET
                    alias_type = excluded.alias_type,
                    market_id = excluded.market_id,
                    question = COALESCE(excluded.question, market_aliases.question),
                    category = COALESCE(excluded.category, market_aliases.category),
                    volume_24h = excluded.volume_24h,
                    active = excluded.active,
                    last_seen_at = excluded.last_seen_at,
                    source = excluded.source
                """,
                (
                    alias,
                    alias_type,
                    normalized_market_id,
                    question,
                    category,
                    float(volume_24h or 0.0),
                    1 if active else 0,
                    seen_at,
                    source,
                ),
            )
        await self.conn.commit()

    async def resolve_market_alias(self, aliases: Iterable[str]) -> Optional[aiosqlite.Row]:
        normalized_aliases = [normalize_market_alias(alias) for alias in aliases]
        normalized_aliases = [alias for alias in normalized_aliases if alias]
        if not normalized_aliases:
            return None

        placeholders = ", ".join("?" for _ in normalized_aliases)
        async with self.conn.execute(
            f"""
            SELECT *
            FROM market_aliases
            WHERE alias IN ({placeholders})
            ORDER BY active DESC, volume_24h DESC, last_seen_at DESC
            LIMIT 1
            """,
            normalized_aliases,
        ) as cursor:
            return await cursor.fetchone()

    async def get_market_alias_counts(self) -> List[aiosqlite.Row]:
        async with self.conn.execute(
            """
            SELECT source, COUNT(*) AS count
            FROM market_aliases
            GROUP BY source
            ORDER BY source
            """
        ) as cursor:
            return await cursor.fetchall()

    async def get_top_market_aliases(self, limit: int = 10) -> List[aiosqlite.Row]:
        async with self.conn.execute(
            """
            SELECT alias, alias_type, market_id, category, volume_24h, active, source, last_seen_at
            FROM market_aliases
            ORDER BY volume_24h DESC, last_seen_at DESC
            LIMIT ?
            """,
            (limit,),
        ) as cursor:
            return await cursor.fetchall()

    async def get_market_aliases_for_market(self, market_id: str) -> List[aiosqlite.Row]:
        normalized_market_id = normalize_market_alias(market_id)
        if not normalized_market_id:
            return []
        async with self.conn.execute(
            """
            SELECT *
            FROM market_aliases
            WHERE market_id = ?
            ORDER BY CASE WHEN alias_type = 'token_id' THEN 0 ELSE 1 END, last_seen_at DESC
            """,
            (normalized_market_id,),
        ) as cursor:
            return await cursor.fetchall()

    async def get_market_alias_integrity(self) -> Optional[aiosqlite.Row]:
        async with self.conn.execute(
            """
            SELECT
                COUNT(*) AS alias_rows,
                COUNT(DISTINCT market_id) AS market_rows
            FROM market_aliases
            """
        ) as cursor:
            return await cursor.fetchone()

    async def get_persisted_lookup_universe(self, limit_markets: int = 15000) -> List[dict]:
        async with self.conn.execute(
            """
            SELECT
                market_id,
                MAX(question) AS question,
                MAX(category) AS category,
                MAX(volume_24h) AS volume_24h,
                MAX(active) AS active,
                GROUP_CONCAT(alias) AS aliases,
                GROUP_CONCAT(CASE WHEN alias != market_id THEN alias END) AS token_aliases
            FROM market_aliases
            GROUP BY market_id
            ORDER BY MAX(volume_24h) DESC, MAX(last_seen_at) DESC
            LIMIT ?
            """,
            (limit_markets,),
        ) as cursor:
            rows = await cursor.fetchall()

        universe: List[dict] = []
        for row in rows:
            market_id = normalize_market_alias(row["market_id"])
            if not market_id:
                continue

            aliases = [
                normalized
                for alias in str(row["aliases"] or "").split(",")
                if (normalized := normalize_market_alias(alias))
            ]
            token_aliases = [
                normalized
                for alias in str(row["token_aliases"] or "").split(",")
                if (normalized := normalize_market_alias(alias))
            ]

            deduped_aliases: list[str] = []
            for alias in aliases:
                if alias not in deduped_aliases:
                    deduped_aliases.append(alias)

            deduped_token_aliases: list[str] = []
            for alias in token_aliases:
                if alias != market_id and alias not in deduped_token_aliases:
                    deduped_token_aliases.append(alias)

            primary_token_alias = choose_primary_token_alias(
                deduped_token_aliases,
                market_id=market_id,
            )

            universe.append(
                {
                    "market_id": market_id,
                    "question": row["question"] or "",
                    "category": row["category"] or None,
                    "volume_24h": float(row["volume_24h"] or 0.0),
                    "active": bool(row["active"]),
                    "token_id": primary_token_alias,
                    "token_ids": deduped_token_aliases,
                    "alias_candidates": deduped_aliases,
                }
            )
        return universe

    async def upsert_runtime_status_snapshot(self, metrics: dict) -> None:
        metrics_json = json.dumps(metrics, separators=(",", ":"), ensure_ascii=False, sort_keys=True)
        await self.conn.execute(
            """
            INSERT INTO runtime_status_snapshot (id, updated_at, metrics_json)
            VALUES (1, CURRENT_TIMESTAMP, ?)
            ON CONFLICT(id) DO UPDATE SET
                updated_at = CURRENT_TIMESTAMP,
                metrics_json = excluded.metrics_json
            """,
            (metrics_json,),
        )
        await self.conn.commit()

    async def get_runtime_status_snapshot(self) -> Optional[dict]:
        async with self.conn.execute(
            """
            SELECT updated_at, metrics_json
            FROM runtime_status_snapshot
            WHERE id = 1
            """
        ) as cursor:
            row = await cursor.fetchone()

        if row is None or not row["metrics_json"]:
            return None

        try:
            metrics = json.loads(row["metrics_json"])
        except json.JSONDecodeError:
            return None

        if not isinstance(metrics, dict):
            return None

        metrics["updated_at"] = row["updated_at"]
        return metrics

    async def upsert_whale_wallet(
        self,
        address: str,
        source_type: str,
        event_amount: float | None = None,
        event_category: str | None = None,
        seen_at: str | None = None,
    ) -> None:
        seen_at = seen_at or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        existing = await self.get_whale_wallet(address)
        source_type = self._prefer_whale_source(existing["source_type"] if existing else None, source_type)

        if existing is None:
            event_count_24h = 1 if event_amount is not None else 0
            await self.conn.execute(
                """
                INSERT INTO whale_wallets (
                    address, source_type, enabled, first_seen_at, last_seen_at, last_event_at,
                    last_event_amount, last_event_category, event_count_24h
                )
                VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?)
                """,
                (
                    address,
                    source_type,
                    seen_at,
                    seen_at,
                    seen_at if event_amount is not None else None,
                    float(event_amount or 0.0),
                    event_category,
                    event_count_24h,
                ),
            )
            await self.conn.commit()
            return

        event_count_24h = int(existing["event_count_24h"] or 0)
        last_event_at = existing["last_event_at"]
        if event_amount is not None:
            if last_event_at:
                last_event_dt = datetime.strptime(str(last_event_at), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                if datetime.now(timezone.utc) - last_event_dt > timedelta(hours=24):
                    event_count_24h = 1
                else:
                    event_count_24h += 1
            else:
                event_count_24h = 1

        await self.conn.execute(
            """
            UPDATE whale_wallets
            SET source_type = ?,
                enabled = 1,
                last_seen_at = ?,
                last_event_at = COALESCE(?, last_event_at),
                last_event_amount = CASE WHEN ? IS NULL THEN last_event_amount ELSE ? END,
                last_event_category = COALESCE(?, last_event_category),
                event_count_24h = CASE WHEN ? IS NULL THEN event_count_24h ELSE ? END
            WHERE address = ?
            """,
            (
                source_type,
                seen_at,
                seen_at if event_amount is not None else None,
                event_amount,
                float(event_amount or 0.0),
                event_category,
                event_amount,
                event_count_24h,
                address,
            ),
        )
        await self.conn.commit()

    async def record_whale_wallet_success(self, address: str, seen_at: str | None = None) -> None:
        seen_at = seen_at or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        await self.conn.execute(
            """
            UPDATE whale_wallets
            SET enabled = 1,
                last_success_at = ?,
                failure_streak = 0
            WHERE address = ?
            """,
            (seen_at, address),
        )
        await self.conn.commit()

    async def record_whale_wallet_failure(self, address: str, seen_at: str | None = None) -> None:
        seen_at = seen_at or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        await self.conn.execute(
            """
            UPDATE whale_wallets
            SET last_failure_at = ?,
                failure_streak = failure_streak + 1
            WHERE address = ?
            """,
            (seen_at, address),
        )
        await self.conn.commit()

    async def disable_stale_whale_wallets(self, stale_days: int = 7) -> None:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=stale_days)).strftime("%Y-%m-%d %H:%M:%S")
        await self.conn.execute(
            """
            UPDATE whale_wallets
            SET enabled = 0
            WHERE enabled = 1
              AND last_seen_at < ?
              AND (last_success_at IS NULL OR last_success_at < ?)
            """,
            (cutoff, cutoff),
        )
        await self.conn.commit()

    async def update_whale_wallet_score(self, address: str, discovery_score: float) -> None:
        await self.conn.execute(
            "UPDATE whale_wallets SET discovery_score = ? WHERE address = ?",
            (discovery_score, address),
        )
        await self.conn.commit()

    async def get_ranked_whale_wallets(
        self,
        limit: int = 50,
        source_type: str | None = None,
        min_event_count_24h: int = 2,
        single_event_min_usd: float = 10_000.0,
    ) -> List[aiosqlite.Row]:
        query = """
            SELECT whale_wallets.*, COALESCE(whale_stats.trust_score, 0.5) AS trust_score
            FROM whale_wallets
            LEFT JOIN whale_stats ON whale_stats.address = whale_wallets.address
            WHERE whale_wallets.enabled = 1
        """
        params: list = []
        if source_type is not None:
            query += " AND whale_wallets.source_type = ?"
            params.append(source_type)
        if source_type == "activity_discovery":
            query += " AND (whale_wallets.event_count_24h >= ? OR whale_wallets.last_event_amount >= ?)"
            params.extend([min_event_count_24h, single_event_min_usd])

        async with self.conn.execute(query, params) as cursor:
            rows = await cursor.fetchall()

        ranked = sorted(rows, key=self._rank_whale_wallet_row, reverse=True)
        return ranked[:limit]

    async def get_whale_wallet_counts(self) -> dict:
        counts = {
            "persisted_wallets": 0,
            "activity_discovered_wallets": 0,
            "leaderboard_wallets": 0,
            "manual_seed_wallets": 0,
            "static_seed_wallets": 0,
        }
        async with self.conn.execute(
            """
            SELECT source_type, COUNT(*) AS count
            FROM whale_wallets
            WHERE enabled = 1
            GROUP BY source_type
            """
        ) as cursor:
            rows = await cursor.fetchall()

        total = 0
        for row in rows:
            source_type = str(row["source_type"])
            count = int(row["count"])
            total += count
            if source_type == "activity_discovery":
                counts["activity_discovered_wallets"] = count
            elif source_type == "leaderboard":
                counts["leaderboard_wallets"] = count
            elif source_type == "manual_seed":
                counts["manual_seed_wallets"] = count
            elif source_type == "static_seed":
                counts["static_seed_wallets"] = count
        counts["persisted_wallets"] = total
        return counts

    @staticmethod
    def _prefer_whale_source(existing_source: str | None, incoming_source: str) -> str:
        precedence = {
            None: 0,
            "static_seed": 1,
            "manual_seed": 2,
            "leaderboard": 3,
            "activity_discovery": 4,
        }
        if precedence.get(incoming_source, 0) >= precedence.get(existing_source, 0):
            return incoming_source
        return str(existing_source)

    @staticmethod
    def _rank_whale_wallet_row(row: aiosqlite.Row) -> float:
        trust_score = float(row["trust_score"] or 0.5)
        event_amount = float(row["last_event_amount"] or 0.0)
        event_count = int(row["event_count_24h"] or 0)
        failure_streak = int(row["failure_streak"] or 0)

        recency_reference = row["last_success_at"] or row["last_event_at"] or row["last_seen_at"]
        recency_component = 0.0
        if recency_reference:
            recency_dt = datetime.strptime(str(recency_reference), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            age_hours = max((datetime.now(timezone.utc) - recency_dt).total_seconds() / 3600, 0.0)
            recency_component = max(0.0, 1.0 - min(age_hours / 168.0, 1.0))

        event_amount_component = min(event_amount / 10_000.0, 1.0)
        event_count_component = min(event_count / 5.0, 1.0)
        failure_penalty = min(failure_streak * 0.1, 0.5)
        return round(
            (0.35 * event_amount_component)
            + (0.25 * event_count_component)
            + (0.25 * trust_score)
            + (0.15 * recency_component)
            - failure_penalty,
            4,
        )

    async def get_bot_performance(self):
        async with self.conn.execute(
            """
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) AS wins,
                   SUM(pnl) AS total_pnl
            FROM trades
            WHERE status != 'OPEN' AND venue = 'polymarket'
            """
        ) as cursor:
            row = await cursor.fetchone()
            total = row["total"] or 0
            wins = row["wins"] or 0
            total_pnl = row["total_pnl"] or 0.0
            win_rate = (wins / total * 100) if total > 0 else 0.0
            return total, wins, win_rate, float(total_pnl)

    async def count_closed_trades(
        self,
        *,
        sample_kind: str | None = None,
        strategy_profile: str | None = None,
        is_synthetic: bool | None = None,
        venue: str | None = None,
    ) -> int:
        query = "SELECT COUNT(*) AS total FROM trades WHERE status != 'OPEN'"
        params: list = []
        if sample_kind is not None:
            query += " AND sample_kind = ?"
            params.append(sample_kind)
        if strategy_profile is not None:
            query += " AND strategy_profile = ?"
            params.append(normalize_strategy_profile(strategy_profile))
        if is_synthetic is not None:
            query += " AND is_synthetic = ?"
            params.append(1 if is_synthetic else 0)
        if venue is not None:
            query += " AND venue = ?"
            params.append(venue)
        async with self.conn.execute(query, params) as cursor:
            row = await cursor.fetchone()
            return int(row["total"] or 0)

    async def get_venue_performance(self, venue: str | None = None):
        query = """
            SELECT
                COALESCE(SUM(realized_pnl), 0) AS realized_pnl,
                COALESCE(SUM(unrealized_pnl), 0) AS unrealized_pnl
            FROM venue_positions
            WHERE 1 = 1
        """
        params: list = []
        if venue is not None:
            query += " AND venue = ?"
            params.append(venue)
        async with self.conn.execute(query, params) as cursor:
            row = await cursor.fetchone()
            return float(row["realized_pnl"]), float(row["unrealized_pnl"])

    async def get_realized_pnl_since(self, venue: str, since_iso: str) -> float:
        async with self.conn.execute(
            """
            SELECT COALESCE(SUM(realized_pnl), 0) AS total
            FROM venue_positions
            WHERE venue = ? AND closed_at IS NOT NULL AND closed_at >= ?
            """,
            (venue, since_iso),
        ) as cursor:
            row = await cursor.fetchone()
            return float(row["total"])

    async def update_trade_status(self, trade_id, status):
        trade = await self.get_trade(trade_id)
        closed_at = None
        hold_seconds = None
        if status != "OPEN":
            closed_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            hold_seconds = self._calculate_hold_seconds(trade["opened_at"] if trade else None, closed_at)
        await self.conn.execute(
            "UPDATE trades SET status = ?, closed_at = COALESCE(?, closed_at), hold_seconds = COALESCE(?, hold_seconds) WHERE id = ?",
            (status, closed_at, hold_seconds, trade_id),
        )
        await self.conn.commit()

    async def add_decision_audit(
        self,
        market_id: str,
        action: str,
        venue: str | None = None,
        category: str | None = None,
        signal_family: str | None = None,
        strategy_profile: str | None = None,
        raw_source_signal: str | None = None,
        sample_kind: str | None = None,
        is_synthetic: bool | None = None,
        decision_score: float | None = None,
        threshold: float | None = None,
        trade_size: float | None = None,
        reason: str | None = None,
        confidence: float | None = None,
        whale_trust: float | None = None,
        spread_pct: float | None = None,
        slippage_proxy_bps: float | None = None,
        inputs_json: str | None = None,
        mapping_stage: str | None = None,
        alias_candidates_json: str | None = None,
        lazy_lookup_attempted: bool = False,
        lazy_lookup_hit: bool = False,
        hot_window_promoted: bool = False,
        occurred_at: str | None = None,
    ) -> int:
        normalized_signal_family = signal_family or normalize_signal_family(raw_source_signal)
        normalized_strategy_profile = normalize_strategy_profile(strategy_profile)
        normalized_sample_kind = sample_kind or infer_sample_kind_from_db_path(self.db_path)
        normalized_is_synthetic = int(is_synthetic_sample(normalized_sample_kind) if is_synthetic is None else bool(is_synthetic))
        cursor = await self.conn.execute(
            """
            INSERT INTO decision_audit (
                occurred_at, venue, market_id, category, signal_family, strategy_profile, raw_source_signal, sample_kind,
                is_synthetic, decision_score, threshold, trade_size, action, reason, confidence,
                whale_trust, spread_pct, slippage_proxy_bps, inputs_json, mapping_stage,
                alias_candidates_json, lazy_lookup_attempted, lazy_lookup_hit, hot_window_promoted
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                occurred_at or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                venue,
                market_id,
                category,
                normalized_signal_family,
                normalized_strategy_profile,
                raw_source_signal,
                normalized_sample_kind,
                normalized_is_synthetic,
                decision_score,
                threshold,
                trade_size,
                action,
                reason,
                confidence,
                whale_trust,
                spread_pct,
                slippage_proxy_bps,
                inputs_json,
                mapping_stage,
                alias_candidates_json,
                1 if lazy_lookup_attempted else 0,
                1 if lazy_lookup_hit else 0,
                1 if hot_window_promoted else 0,
            ),
        )
        await self.conn.commit()
        return int(cursor.lastrowid)

    async def create_venue_position(
        self,
        venue: str,
        execution_mode: str,
        instrument_type: str,
        symbol_or_market_id: str,
        side: str,
        qty_or_shares: float,
        entry_price: float,
        notional_usd: float,
        leverage: int = 1,
        source_signal: str | None = None,
        category: str | None = None,
        signal_family: str | None = None,
        strategy_profile: str | None = None,
        sample_kind: str | None = None,
        is_synthetic: bool | None = None,
        debug_profile: str | None = None,
        entry_spread_pct: float | None = None,
        slippage_proxy_bps: float | None = None,
        whale_trust_at_entry: float | None = None,
        linked_market_id: str | None = None,
        status: str = "OPEN",
    ) -> int:
        normalized_signal_family = signal_family or normalize_signal_family(source_signal)
        normalized_strategy_profile = normalize_strategy_profile(strategy_profile)
        normalized_sample_kind = sample_kind or infer_sample_kind_from_db_path(self.db_path)
        normalized_is_synthetic = int(is_synthetic_sample(normalized_sample_kind) if is_synthetic is None else bool(is_synthetic))
        cursor = await self.conn.execute(
            """
            INSERT INTO venue_positions (
                venue, execution_mode, instrument_type, symbol_or_market_id, side, qty_or_shares,
                entry_price, mark_price, notional_usd, leverage, status, source_signal, category,
                signal_family, strategy_profile, sample_kind, is_synthetic, debug_profile, entry_spread_pct,
                slippage_proxy_bps, whale_trust_at_entry, linked_market_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                venue,
                execution_mode,
                instrument_type,
                symbol_or_market_id,
                side,
                qty_or_shares,
                entry_price,
                entry_price,
                notional_usd,
                leverage,
                status,
                source_signal,
                category,
                normalized_signal_family,
                normalized_strategy_profile,
                normalized_sample_kind,
                normalized_is_synthetic,
                debug_profile,
                entry_spread_pct,
                slippage_proxy_bps,
                whale_trust_at_entry,
                linked_market_id,
            ),
        )
        await self.conn.commit()
        return int(cursor.lastrowid)

    async def get_open_positions(
        self,
        venue: str | None = None,
        symbol_or_market_id: str | None = None,
    ) -> List[aiosqlite.Row]:
        query = "SELECT * FROM venue_positions WHERE status = 'OPEN'"
        params: list = []
        if venue is not None:
            query += " AND venue = ?"
            params.append(venue)
        if symbol_or_market_id is not None:
            query += " AND symbol_or_market_id = ?"
            params.append(symbol_or_market_id)
        query += " ORDER BY id ASC"
        async with self.conn.execute(query, params) as cursor:
            return await cursor.fetchall()

    async def get_position(self, position_id: int) -> Optional[aiosqlite.Row]:
        async with self.conn.execute("SELECT * FROM venue_positions WHERE id = ?", (position_id,)) as cursor:
            return await cursor.fetchone()

    async def update_position_mark(self, position_id: int, mark_price: float, unrealized_pnl: float):
        position = await self.get_position(position_id)
        if position is None:
            return
        await self.conn.execute(
            """
            UPDATE venue_positions
            SET mark_price = ?, unrealized_pnl = ?
            WHERE id = ?
            """,
            (mark_price, unrealized_pnl, position_id),
        )
        await self.conn.commit()

        rows = await self.get_open_positions(venue=position["venue"])
        account = await self.get_venue_account(position["venue"], position["execution_mode"])
        cash_balance = float(account["cash_balance"]) if account else 0.0
        total_unrealized = sum(float(row["unrealized_pnl"]) for row in rows)
        await self.conn.execute(
            """
            UPDATE venue_accounts
            SET equity = ?, available_balance = ?, updated_at = CURRENT_TIMESTAMP
            WHERE venue = ? AND execution_mode = ?
            """,
            (
                cash_balance + total_unrealized,
                cash_balance,
                position["venue"],
                position["execution_mode"],
            ),
        )
        await self.conn.commit()

    async def close_venue_position(self, position_id: int, exit_price: float, realized_pnl: float, status: str):
        closed_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        position = await self.get_position(position_id)
        await self.conn.execute(
            """
            UPDATE venue_positions
            SET mark_price = ?, realized_pnl = ?, unrealized_pnl = 0, status = ?, closed_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (exit_price, realized_pnl, status, position_id),
        )

        async with self.conn.execute(
            "SELECT id, opened_at FROM trades WHERE position_id = ? AND status = 'OPEN'",
            (position_id,),
        ) as cursor:
            linked_trades = await cursor.fetchall()

        trade_status = "CLOSED_FLAT"
        if realized_pnl > 0:
            trade_status = "CLOSED_WIN"
        elif realized_pnl < 0:
            trade_status = "CLOSED_LOSS"

        for trade in linked_trades:
            hold_seconds = self._calculate_hold_seconds(trade["opened_at"], closed_at)
            await self.conn.execute(
                """
                UPDATE trades
                SET status = ?, pnl = ?, closed_at = ?, hold_seconds = ?
                WHERE id = ?
                """,
                (trade_status, realized_pnl, closed_at, hold_seconds, trade["id"]),
            )

        if position is not None:
            open_rows = await self.get_open_positions(venue=position["venue"])
            account = await self.get_venue_account(position["venue"], position["execution_mode"])
            cash_balance = float(account["cash_balance"]) if account else 0.0
            total_unrealized = sum(float(row["unrealized_pnl"]) for row in open_rows)
            await self.conn.execute(
                """
                UPDATE venue_accounts
                SET equity = ?, available_balance = ?, updated_at = CURRENT_TIMESTAMP
                WHERE venue = ? AND execution_mode = ?
                """,
                (
                    cash_balance + total_unrealized,
                    cash_balance,
                    position["venue"],
                    position["execution_mode"],
                ),
            )
        await self.conn.commit()

    async def add_venue_order(
        self,
        venue: str,
        execution_mode: str,
        position_id: int | None,
        symbol_or_market_id: str,
        order_type: str,
        side: str,
        qty: float,
        price: float | None,
        stop_price: float | None,
        reduce_only: bool,
        status: str,
        client_order_id: str | None = None,
        external_order_id: str | None = None,
    ) -> int:
        cursor = await self.conn.execute(
            """
            INSERT INTO venue_orders (
                venue, execution_mode, position_id, symbol_or_market_id, client_order_id, external_order_id,
                order_type, side, qty, price, status, reduce_only, stop_price
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                venue,
                execution_mode,
                position_id,
                symbol_or_market_id,
                client_order_id,
                external_order_id,
                order_type,
                side,
                qty,
                price,
                status,
                1 if reduce_only else 0,
                stop_price,
            ),
        )
        await self.conn.commit()
        return int(cursor.lastrowid)

    async def get_open_venue_orders(
        self,
        venue: str,
        symbol_or_market_id: str | None = None,
    ) -> List[aiosqlite.Row]:
        query = "SELECT * FROM venue_orders WHERE venue = ? AND status = 'OPEN'"
        params: list = [venue]
        if symbol_or_market_id is not None:
            query += " AND symbol_or_market_id = ?"
            params.append(symbol_or_market_id)
        query += " ORDER BY id ASC"
        async with self.conn.execute(query, params) as cursor:
            return await cursor.fetchall()

    async def update_venue_order_status(self, order_id: int, status: str):
        await self.conn.execute("UPDATE venue_orders SET status = ? WHERE id = ?", (status, order_id))
        await self.conn.commit()

    async def cancel_open_venue_orders(self, venue: str, symbol_or_market_id: str):
        await self.conn.execute(
            """
            UPDATE venue_orders
            SET status = 'CANCELLED'
            WHERE venue = ? AND symbol_or_market_id = ? AND status = 'OPEN'
            """,
            (venue, symbol_or_market_id),
        )
        await self.conn.commit()

    async def close(self):
        if self.conn is not None:
            await self.conn.close()

    @staticmethod
    def _calculate_hold_seconds(opened_at: str | None, closed_at: str | None) -> float | None:
        if not opened_at or not closed_at:
            return None
        try:
            opened_dt = datetime.strptime(str(opened_at), "%Y-%m-%d %H:%M:%S")
            closed_dt = datetime.strptime(str(closed_at), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
        return max((closed_dt - opened_dt).total_seconds(), 0.0)
