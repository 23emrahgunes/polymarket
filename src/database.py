import aiosqlite
import os

class Database:
    def __init__(self, db_path="data/ghost_trader.db"):
        self.db_path = db_path
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)

    async def connect(self):
        self.conn = await aiosqlite.connect(self.db_path)
        await self._create_tables()

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
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Initialize wallet with $1000 if empty
        async with self.conn.execute("SELECT COUNT(*) FROM wallet") as cursor:
            count = await cursor.fetchone()
            if count[0] == 0:
                await self.conn.execute("INSERT INTO wallet (id, balance) VALUES (1, 1000.0)")
        await self.conn.commit()

    async def get_balance(self):
        async with self.conn.execute("SELECT balance FROM wallet WHERE id = 1") as cursor:
            row = await cursor.fetchone()
            return row[0]

    async def update_balance(self, new_balance):
        await self.conn.execute("UPDATE wallet SET balance = ? WHERE id = 1", (new_balance,))
        await self.conn.commit()

    async def add_trade(self, market_id, side, size, price, edge, confidence, status="OPEN"):
        await self.conn.execute("""
            INSERT INTO trades (market_id, side, size, price, edge, confidence, status)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (market_id, side, size, price, edge, confidence, status))
        await self.conn.commit()

    async def get_open_trades(self):
        self.conn.row_factory = aiosqlite.Row
        async with self.conn.execute("SELECT * FROM trades WHERE status = 'OPEN'") as cursor:
            return await cursor.fetchall()

    async def update_trade_status(self, trade_id, status):
        await self.conn.execute("UPDATE trades SET status = ? WHERE id = ?", (status, trade_id))
        await self.conn.commit()

    async def close(self):
        await self.conn.close()
