import aiosqlite
import os

class Database:
    def __init__(self, db_path="data/ghost_trader.db"):
        self.db_path = db_path
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)

    async def connect(self):
        self.conn = await aiosqlite.connect(self.db_path)
        await self._create_tables()
        await self._migrate_tables()

    async def _create_tables(self):
        await self.conn.execute("""
            CREATE TABLE IF NOT EXISTS wallet (
                id INTEGER PRIMARY KEY,
                balance REAL NOT NULL
            )
        """)
        await self.conn.execute("""
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
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await self.conn.execute("""
            CREATE TABLE IF NOT EXISTS whale_stats (
                address TEXT PRIMARY KEY,
                total_trades INTEGER DEFAULT 0,
                wins INTEGER DEFAULT 0,
                total_pnl REAL DEFAULT 0,
                trust_score REAL DEFAULT 0.5,
                last_active DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Initialize wallet with $1000 if empty
        async with self.conn.execute("SELECT COUNT(*) FROM wallet") as cursor:
            count = await cursor.fetchone()
            if count[0] == 0:
                await self.conn.execute("INSERT INTO wallet (id, balance) VALUES (1, 1000.0)")
        await self.conn.commit()

    async def _migrate_tables(self):
        """
        Handle schema updates for existing databases.
        """
        try:
            await self.conn.execute("ALTER TABLE trades ADD COLUMN pnl REAL DEFAULT 0")
        except:
            pass # Column already exists

        try:
            await self.conn.execute("ALTER TABLE trades ADD COLUMN whale_address TEXT")
        except:
            pass # Column already exists

    async def get_balance(self):
        async with self.conn.execute("SELECT balance FROM wallet WHERE id = 1") as cursor:
            row = await cursor.fetchone()
            return row[0]

    async def update_balance(self, new_balance):
        await self.conn.execute("UPDATE wallet SET balance = ? WHERE id = 1", (new_balance,))
        await self.conn.commit()

    async def add_trade(self, market_id, side, size, price, edge, confidence, status="OPEN", whale_address=None):
        await self.conn.execute("""
            INSERT INTO trades (market_id, side, size, price, edge, confidence, status, whale_address)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (market_id, side, size, price, edge, confidence, status, whale_address))
        await self.conn.commit()

    async def get_open_trades(self):
        self.conn.row_factory = aiosqlite.Row
        async with self.conn.execute("SELECT * FROM trades WHERE status = 'OPEN'") as cursor:
            return await cursor.fetchall()

    async def update_trade_resolution(self, trade_id, status, pnl):
        await self.conn.execute("UPDATE trades SET status = ?, pnl = ? WHERE id = ?", (status, pnl, trade_id))
        await self.conn.commit()

    async def update_whale_stats(self, address, pnl):
        win = 1 if pnl > 0 else 0
        await self.conn.execute("""
            INSERT INTO whale_stats (address, total_trades, wins, total_pnl, last_active)
            VALUES (?, 1, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(address) DO UPDATE SET
                total_trades = total_trades + 1,
                wins = wins + ?,
                total_pnl = total_pnl + ?,
                last_active = CURRENT_TIMESTAMP
        """, (address, win, pnl, win, pnl))
        # Update trust score (win rate)
        await self.conn.execute("""
            UPDATE whale_stats SET trust_score = CAST(wins AS REAL) / total_trades
            WHERE address = ?
        """, (address,))
        await self.conn.commit()

    async def get_whale_stats(self, address):
        self.conn.row_factory = aiosqlite.Row
        async with self.conn.execute("SELECT * FROM whale_stats WHERE address = ?", (address,)) as cursor:
            return await cursor.fetchone()

    async def get_bot_performance(self):
        async with self.conn.execute("""
            SELECT COUNT(*), SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END), SUM(pnl)
            FROM trades WHERE status != 'OPEN'
        """) as cursor:
            row = await cursor.fetchone()
            total = row[0] or 0
            wins = row[1] or 0
            total_pnl = row[2] or 0.0
            win_rate = (wins / total * 100) if total > 0 else 0.0
            return total, wins, win_rate, total_pnl

    async def update_trade_status(self, trade_id, status):
        # Kept for backward compatibility if needed, but update_trade_resolution is preferred
        await self.conn.execute("UPDATE trades SET status = ? WHERE id = ?", (status, trade_id))
        await self.conn.commit()

    async def close(self):
        await self.conn.close()
