#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Dict, Iterable, List


ABS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(ABS_ROOT)

from src.database import Database
from src.runtime import GhostBotRuntime, RuntimeSettings


DEFAULT_DB_PATH = os.path.join("data", "runtime_crypto_dual_venue.db")


logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


class MemoryLogHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.messages: List[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(self.format(record))


def resolve_db_path() -> str:
    requested_path = os.getenv("RUNTIME_VERIFY_CRYPTO_DB_PATH", DEFAULT_DB_PATH)
    if not os.path.exists(requested_path):
        return requested_path

    try:
        os.remove(requested_path)
        return requested_path
    except PermissionError:
        timestamp = int(time.time())
        return os.path.join("data", f"runtime_crypto_dual_venue_{timestamp}.db")


def _apply_env(overrides: Dict[str, str]) -> Dict[str, str | None]:
    original = {key: os.environ.get(key) for key in overrides}
    for key, value in overrides.items():
        os.environ[key] = value
    return original


def _restore_env(original: Dict[str, str | None]) -> None:
    for key, value in original.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def _fetch_account_balance(cursor: sqlite3.Cursor, venue: str) -> float:
    row = cursor.execute(
        """
        SELECT cash_balance
        FROM venue_accounts
        WHERE venue = ? AND execution_mode = 'paper'
        """,
        (venue,),
    ).fetchone()
    return float(row["cash_balance"]) if row else 0.0


def _fetch_dict_rows(cursor: sqlite3.Cursor, query: str, params: Iterable | None = None) -> List[Dict]:
    rows = cursor.execute(query, tuple(params or ())).fetchall()
    return [dict(row) for row in rows]


def _render_report(report: Dict) -> str:
    lines = [
        "DB_PATH",
        str(report["db_path"]),
        "POLYMARKET_WALLET_BEFORE",
        str(report["polymarket_wallet_before"]),
        "POLYMARKET_WALLET_AFTER",
        str(report["polymarket_wallet_after"]),
        "BINANCE_FUTURES_WALLET_BEFORE",
        str(report["binance_futures_wallet_before"]),
        "BINANCE_FUTURES_WALLET_AFTER",
        str(report["binance_futures_wallet_after"]),
        "TRADES",
    ]
    lines.extend(str(trade) for trade in report["trades"])
    lines.append("POSITIONS")
    lines.extend(str(position) for position in report["positions"])
    lines.append("ORDERS")
    lines.extend(str(order) for order in report["orders"])
    lines.append("LOG_EXCERPT")
    lines.extend(report["log_excerpt"])
    return "\n".join(lines)


def _validate_report(report: Dict) -> None:
    trades = report["trades"]
    orders = report["orders"]
    positions = report["positions"]
    log_excerpt = report["log_excerpt"]

    polymarket_trades = [
        trade
        for trade in trades
        if trade["venue"] == "polymarket"
        and trade["instrument_type"] == "prediction"
        and trade["market_id"] == "debug-crypto-btc-100k-2026"
        and trade["source_signal"] == "blended_crypto"
    ]
    futures_trades = [
        trade
        for trade in trades
        if trade["venue"] == "binance_futures"
        and trade["instrument_type"] == "futures"
        and trade["market_id"] == "BTC/USDT:USDT"
    ]
    futures_positions = [
        position
        for position in positions
        if position["venue"] == "binance_futures"
        and position["symbol_or_market_id"] == "BTC/USDT:USDT"
        and position["status"] == "OPEN"
    ]
    open_order_types = {order["order_type"] for order in orders if order["venue"] == "binance_futures"}

    if not polymarket_trades:
        raise SystemExit("No Polymarket crypto trade was inserted during dual-venue verification.")
    if not futures_trades:
        raise SystemExit("No Binance Futures crypto trade was inserted during dual-venue verification.")
    if report["polymarket_wallet_after"] >= report["polymarket_wallet_before"]:
        raise SystemExit("Polymarket paper wallet did not decrease during dual-venue verification.")
    if report["binance_futures_wallet_after"] >= report["binance_futures_wallet_before"]:
        raise SystemExit("Binance Futures paper wallet did not decrease during dual-venue verification.")
    if not futures_positions:
        raise SystemExit("No open Binance Futures paper position was created during dual-venue verification.")
    if {"STOP_LOSS", "TAKE_PROFIT"} - open_order_types:
        raise SystemExit("Required Binance Futures protection orders were not created.")
    if not any("[DECISION] venue=polymarket" in line for line in log_excerpt):
        raise SystemExit("Missing Polymarket crypto decision log in dual-venue verification.")
    if not any("[DECISION] venue=binance_futures" in line for line in log_excerpt):
        raise SystemExit("Missing Binance Futures decision log in dual-venue verification.")
    if not any("[PAPER-TRADE-RUNTIME] venue=polymarket" in line for line in log_excerpt):
        raise SystemExit("Missing Polymarket runtime insert log in dual-venue verification.")
    if not any("[PAPER-TRADE-RUNTIME] venue=binance_futures" in line for line in log_excerpt):
        raise SystemExit("Missing Binance Futures runtime insert log in dual-venue verification.")


async def collect_dual_venue_verification(
    db_path: str | None = None,
    timeout_seconds: float = 20.0,
) -> Dict:
    resolved_db_path = db_path or resolve_db_path()
    env_overrides = {
        "DEBUG_SIGNAL_MODE": "true",
        "DEBUG_SIGNAL_PROFILE": "crypto_dual",
        "RUNTIME_VERIFY_ONCE": "true",
        "VERIFY_REQUIRED_VENUES": "polymarket,binance_futures",
        "VERIFY_REQUIRED_CATEGORY": "CRYPTO",
        "BINANCE_FUTURES_ENABLED": "true",
        "BINANCE_SPOT_ENABLED": "false",
        "GHOST_TRADER_DB_PATH": resolved_db_path,
    }
    original_env = _apply_env(env_overrides)

    log_handler = MemoryLogHandler()
    log_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
    root_logger = logging.getLogger()
    previous_level = root_logger.level
    root_logger.addHandler(log_handler)
    if previous_level > logging.INFO:
        root_logger.setLevel(logging.INFO)

    try:
        database = Database(resolved_db_path)
        await database.connect()
        polymarket_wallet_before = await database.get_balance("polymarket", "paper")
        binance_futures_wallet_before = await database.get_balance("binance_futures", "paper")
        await database.close()

        runtime = GhostBotRuntime(RuntimeSettings.from_env())
        await asyncio.wait_for(runtime.run(), timeout=timeout_seconds)

        connection = sqlite3.connect(resolved_db_path)
        connection.row_factory = sqlite3.Row
        cursor = connection.cursor()

        polymarket_wallet_after = _fetch_account_balance(cursor, "polymarket")
        binance_futures_wallet_after = _fetch_account_balance(cursor, "binance_futures")
        trades = _fetch_dict_rows(
            cursor,
            """
            SELECT id, venue, instrument_type, market_id, side, size, price, confidence, source_signal, execution_mode, timestamp
            FROM trades
            ORDER BY id ASC
            """,
        )
        positions = _fetch_dict_rows(
            cursor,
            """
            SELECT id, venue, instrument_type, symbol_or_market_id, side, entry_price, notional_usd, leverage, status, source_signal
            FROM venue_positions
            ORDER BY id ASC
            """,
        )
        orders = _fetch_dict_rows(
            cursor,
            """
            SELECT id, venue, symbol_or_market_id, order_type, side, qty, price, stop_price, reduce_only, status
            FROM venue_orders
            ORDER BY id ASC
            """,
        )
        connection.close()

        filtered_logs = [
            line
            for line in log_handler.messages
            if ("[DECISION]" in line or "[PAPER-TRADE-RUNTIME]" in line)
            and ("venue=polymarket" in line or "venue=binance_futures" in line)
        ]

        report = {
            "db_path": Path(resolved_db_path),
            "polymarket_wallet_before": polymarket_wallet_before,
            "polymarket_wallet_after": polymarket_wallet_after,
            "binance_futures_wallet_before": binance_futures_wallet_before,
            "binance_futures_wallet_after": binance_futures_wallet_after,
            "trades": trades,
            "positions": positions,
            "orders": orders,
            "log_excerpt": filtered_logs[-12:],
        }
        _validate_report(report)
        return report
    finally:
        root_logger.removeHandler(log_handler)
        root_logger.setLevel(previous_level)
        _restore_env(original_env)


async def verify_crypto_dual_venue(
    db_path: str | None = None,
    timeout_seconds: float = 20.0,
    emit_report: bool = True,
) -> Dict:
    report = await collect_dual_venue_verification(db_path=db_path, timeout_seconds=timeout_seconds)
    if emit_report:
        print(_render_report(report))
    return report


if __name__ == "__main__":
    asyncio.run(verify_crypto_dual_venue())
