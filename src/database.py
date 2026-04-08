from __future__ import annotations

import os
from typing import Iterable, List, Optional

import aiosqlite


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
                execution_mode TEXT DEFAULT 'paper',
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
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
            "ALTER TABLE trades ADD COLUMN execution_mode TEXT DEFAULT 'paper'",
            "ALTER TABLE venue_positions ADD COLUMN source_signal TEXT",
            "ALTER TABLE venue_positions ADD COLUMN linked_market_id TEXT",
            "ALTER TABLE venue_positions ADD COLUMN mark_price REAL",
        ]:
            try:
                await self.conn.execute(statement)
            except Exception:
                pass
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
    ) -> int:
        cursor = await self.conn.execute(
            """
            INSERT INTO trades (
                market_id, side, size, price, edge, confidence, status, whale_address,
                venue, instrument_type, position_id, source_signal, execution_mode
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                execution_mode,
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
        await self.conn.execute("UPDATE trades SET status = ?, pnl = ? WHERE id = ?", (status, pnl, trade_id))
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
        await self.conn.execute("UPDATE trades SET status = ? WHERE id = ?", (status, trade_id))
        await self.conn.commit()

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
        linked_market_id: str | None = None,
        status: str = "OPEN",
    ) -> int:
        cursor = await self.conn.execute(
            """
            INSERT INTO venue_positions (
                venue, execution_mode, instrument_type, symbol_or_market_id, side, qty_or_shares,
                entry_price, mark_price, notional_usd, leverage, status, source_signal, linked_market_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        await self.conn.execute(
            """
            UPDATE venue_positions
            SET mark_price = ?, realized_pnl = ?, unrealized_pnl = 0, status = ?, closed_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (exit_price, realized_pnl, status, position_id),
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
