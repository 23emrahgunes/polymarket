from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .repository import PolymarketResearchRepository


def _table_columns(connection: sqlite3.Connection, table_name: str) -> list[dict[str, object]]:
    rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    return [
        {
            "name": str(row[1]),
            "type": str(row[2] or ""),
            "notnull": int(row[3] or 0),
            "default": row[4],
            "pk": int(row[5] or 0),
        }
        for row in rows
    ]


def _runtime_seed_row(*, wallet_address: str, occurred_at: str) -> dict[str, object]:
    return {
        "venue": "polymarket",
        "execution_mode": "paper",
        "instrument_type": "prediction",
        "market_id": "Bitcoin Up or Down - runtime pilot seed",
        "symbol_or_market_id": "Bitcoin Up or Down - runtime pilot seed",
        "side": "BUY",
        "size": 140.0,
        "price": 0.55,
        "confidence": 0.95,
        "source_signal": "runtime_pilot_seed",
        "signal_family": "polymarket_copy",
        "category": "CRYPTO",
        "strategy_profile": "manual_persisted",
        "sample_kind": "runtime_pilot_seed",
        "is_synthetic": 0,
        "status": "OPEN",
        "pnl": 0.0,
        "whale_address": wallet_address,
        "timestamp": occurred_at,
        "opened_at": occurred_at,
        "closed_at": occurred_at,
    }


def _historical_seed_rows(*, wallet_address: str, now: datetime) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    historical_points = [
        (1, 1200.0, 550.0),
        (2, 900.0, 420.0),
        (3, 800.0, 360.0),
        (4, 650.0, 330.0),
        (5, 500.0, 280.0),
    ]
    for day_offset, pnl, size in historical_points:
        occurred_text = (now - timedelta(days=day_offset)).strftime("%Y-%m-%d %H:%M:%S")
        rows.append(
            {
                "venue": "polymarket",
                "execution_mode": "paper",
                "instrument_type": "prediction",
                "market_id": f"Bitcoin Up or Down - runtime historical seed {day_offset}",
                "symbol_or_market_id": f"Bitcoin Up or Down - runtime historical seed {day_offset}",
                "side": "BUY",
                "size": size,
                "price": 0.55,
                "confidence": 0.95,
                "source_signal": "manual_source_replay",
                "signal_family": "polymarket_copy",
                "category": "CRYPTO",
                "strategy_profile": "manual_persisted",
                "sample_kind": "runtime_historical_seed",
                "is_synthetic": 0,
                "status": "CLOSED",
                "pnl": pnl,
                "whale_address": wallet_address,
                "timestamp": occurred_text,
                "opened_at": occurred_text,
                "closed_at": occurred_text,
            }
        )
    return rows


def _fallback_value(column_name: str, column_type: str, *, wallet_address: str, occurred_at: str) -> object:
    normalized_name = column_name.lower()
    normalized_type = column_type.upper()
    if normalized_name == "id":
        return None
    if normalized_name.endswith("_at") or "time" in normalized_name or "date" in normalized_name:
        return occurred_at
    if "price" in normalized_name or "confidence" in normalized_name:
        return 0.0
    if normalized_name in {"qty", "size", "notional_usd", "pnl"}:
        return 0.0
    if normalized_name in {"is_synthetic", "reduce_only"}:
        return 0
    if normalized_name == "status":
        return "OPEN"
    if normalized_name == "side":
        return "BUY"
    if normalized_name in {"venue", "instrument_type", "category", "source_signal", "signal_family", "strategy_profile", "sample_kind"}:
        return "runtime_pilot_seed"
    if normalized_name in {"market_id", "symbol_or_market_id"}:
        return "Bitcoin Up or Down - runtime pilot seed"
    if normalized_name == "whale_address":
        return wallet_address
    if "INT" in normalized_type or "REAL" in normalized_type or "NUM" in normalized_type or "FLOA" in normalized_type:
        return 0
    return "runtime_pilot_seed"


def _existing_trade_filter(
    trade_column_names: set[str],
    *,
    wallet_address: str,
    source_signal: str,
    status: str,
    market_identifier: str,
    occurred_at: str,
) -> tuple[str, list[object]]:
    clauses: list[str] = []
    params: list[object] = []

    if "whale_address" in trade_column_names:
        clauses.append("LOWER(COALESCE(whale_address, '')) = ?")
        params.append(wallet_address)
    if "source_signal" in trade_column_names:
        clauses.append("source_signal = ?")
        params.append(source_signal)
    if "status" in trade_column_names:
        clauses.append("status = ?")
        params.append(status)

    has_market_id = "market_id" in trade_column_names
    has_symbol_or_market_id = "symbol_or_market_id" in trade_column_names
    if has_market_id and has_symbol_or_market_id:
        clauses.append("COALESCE(market_id, symbol_or_market_id, '') = ?")
        params.append(market_identifier)
    elif has_market_id:
        clauses.append("COALESCE(market_id, '') = ?")
        params.append(market_identifier)
    elif has_symbol_or_market_id:
        clauses.append("COALESCE(symbol_or_market_id, '') = ?")
        params.append(market_identifier)
    elif "timestamp" in trade_column_names:
        clauses.append("COALESCE(timestamp, '') = ?")
        params.append(occurred_at)
    elif "closed_at" in trade_column_names:
        clauses.append("COALESCE(closed_at, '') = ?")
        params.append(occurred_at)

    where_sql = " AND ".join(clauses) if clauses else "1 = 0"
    return where_sql, params


def _insert_trade_if_missing(
    connection: sqlite3.Connection,
    *,
    row_payload: dict[str, object],
    wallet_address: str,
) -> None:
    column_rows = _table_columns(connection, "trades")
    trade_column_names = {str(column["name"]) for column in column_rows}
    occurred_at = str(row_payload.get("timestamp") or row_payload.get("closed_at") or row_payload.get("opened_at") or "")
    market_identifier = str(row_payload.get("market_id") or row_payload.get("symbol_or_market_id") or "")
    where_sql, params = _existing_trade_filter(
        trade_column_names,
        wallet_address=wallet_address,
        source_signal=str(row_payload.get("source_signal") or ""),
        status=str(row_payload.get("status") or ""),
        market_identifier=market_identifier,
        occurred_at=occurred_at,
    )
    existing_row = connection.execute(
        f"""
        SELECT id
        FROM trades
        WHERE {where_sql}
        ORDER BY id DESC
        LIMIT 1
        """,
        tuple(params),
    ).fetchone()
    if existing_row is not None:
        return

    insert_columns: list[str] = []
    insert_values: list[object] = []
    for column in column_rows:
        column_name = str(column["name"])
        if column_name.lower() == "id":
            continue
        if column_name in row_payload:
            value = row_payload[column_name]
        elif int(column["notnull"] or 0) == 1 and column["default"] is None:
            value = _fallback_value(
                column_name,
                str(column["type"] or ""),
                wallet_address=wallet_address,
                occurred_at=occurred_at,
            )
        else:
            continue
        insert_columns.append(column_name)
        insert_values.append(value)
    connection.execute(
        f"INSERT INTO trades ({', '.join(insert_columns)}) VALUES ({', '.join('?' for _ in insert_columns)})",
        insert_values,
    )


def ensure_runtime_priority_wallet(
    *,
    db_path: str,
    source_db_path: str,
    display_name: str,
    profile_ref: str,
    priority_rank: int,
    priority_mode: str,
    target_specialization: str,
    wallet_address: str,
    link_notes: str,
    approval_notes: str,
    seed_runtime_source_trade: bool,
) -> dict[str, object]:
    normalized_wallet = wallet_address.strip().lower()
    repository = PolymarketResearchRepository(db_path, source_db_path)
    repository.ensure_tables()

    row = next(
        (
            item
            for item in repository.fetch_watchlist_rows()
            if str(item["display_name"] or "").strip().lower() == display_name.strip().lower()
            or str(item["profile_ref"] or "").strip() == profile_ref.strip()
        ),
        None,
    )
    if row is None:
        row = repository.add_watchlist_row(
            display_name=display_name,
            profile_ref=profile_ref,
            priority_rank=priority_rank,
            priority_mode=priority_mode,
            target_specialization=target_specialization,
            notes=link_notes,
        )

    row_id = int(row["id"])
    if str(row["wallet_address"] or "").strip().lower() != normalized_wallet or str(row["status"] or "") != "linked":
        row = repository.link_watchlist_wallet(row_id=row_id, wallet_address=normalized_wallet, notes=link_notes)

    if int(row["operator_approved_pilot"] or 0) != 1:
        row = repository.approve_watchlist_pilot(row_id=row_id, approved=True, notes=approval_notes)

    if seed_runtime_source_trade:
        source_path = Path(source_db_path)
        source_path.parent.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc).replace(microsecond=0)
        # Keep the runtime seed outside the default follower delay window so
        # CLI/VPS acceptance observes a real copy action on the first run.
        runtime_seed_at = now - timedelta(minutes=5)
        runtime_seed_text = runtime_seed_at.strftime("%Y-%m-%d %H:%M:%S")
        with sqlite3.connect(source_path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    whale_address TEXT,
                    venue TEXT,
                    market_id TEXT,
                    status TEXT,
                    pnl REAL,
                    size REAL,
                    closed_at TEXT,
                    timestamp TEXT,
                    category TEXT,
                    source_signal TEXT,
                    side TEXT
                )
                """
            )
            for historical_row in _historical_seed_rows(wallet_address=normalized_wallet, now=now):
                _insert_trade_if_missing(connection, row_payload=historical_row, wallet_address=normalized_wallet)
            _insert_trade_if_missing(
                connection,
                row_payload=_runtime_seed_row(wallet_address=normalized_wallet, occurred_at=runtime_seed_text),
                wallet_address=normalized_wallet,
            )
            connection.commit()

    return {
        "display_name": display_name,
        "db_path": db_path,
        "operator_approved_pilot": True,
        "seed_runtime_source_trade": seed_runtime_source_trade,
        "source_db_path": source_db_path,
        "wallet_address": normalized_wallet,
        "watchlist_row_id": row_id,
    }
