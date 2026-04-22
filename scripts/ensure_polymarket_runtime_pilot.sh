#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# shellcheck disable=SC1091
source "$REPO_ROOT/scripts/lib/load_dotenv.sh"

if [[ -f "$REPO_ROOT/.env" ]]; then
    load_dotenv_file "$REPO_ROOT/.env"
fi

if [[ -f "$REPO_ROOT/.venv/bin/activate" ]]; then
    # shellcheck disable=SC1091
    source "$REPO_ROOT/.venv/bin/activate"
fi

DB_PATH="${POLYMARKET_COPY_DB_PATH:-${POLYMARKET_RESEARCH_DB_PATH:-${GHOST_TRADER_DB_PATH:-data/research_v2.db}}}"
SOURCE_DB_PATH="${POLYMARKET_COPY_SOURCE_DB_PATH:-${POLYMARKET_RESEARCH_SOURCE_DB_PATH:-$DB_PATH}}"
WATCHLIST_DISPLAY_NAME="${POLYMARKET_PRIORITY_WALLET_NAME:-ohanism}"
WATCHLIST_PROFILE_REF="${POLYMARKET_PRIORITY_WALLET_PROFILE_REF:-https://polymarket.com/tr/@ohanism}"
WATCHLIST_PRIORITY_RANK="${POLYMARKET_PRIORITY_WALLET_RANK:-1}"
WATCHLIST_PRIORITY_MODE="${POLYMARKET_PRIORITY_WALLET_MODE:-fast_track_shadow}"
WATCHLIST_TARGET_SPECIALIZATION="${POLYMARKET_PRIORITY_WALLET_SPECIALIZATION:-CRYPTO}"
WATCHLIST_WALLET_ADDRESS="${POLYMARKET_PRIORITY_WALLET_ADDRESS:-0x89b5cdaaa4866c1e738406712012a630b4078beb}"
WATCHLIST_NOTES="${POLYMARKET_PRIORITY_WALLET_NOTES:-verified manually}"
WATCHLIST_APPROVAL_NOTES="${POLYMARKET_PRIORITY_WALLET_APPROVAL_NOTES:-bootstrap pilot approval}"
SEED_RUNTIME_SOURCE_TRADE="${POLYMARKET_RUNTIME_SEED_OPEN_TRADE:-false}"

python - "$DB_PATH" "$SOURCE_DB_PATH" "$WATCHLIST_DISPLAY_NAME" "$WATCHLIST_PROFILE_REF" "$WATCHLIST_PRIORITY_RANK" "$WATCHLIST_PRIORITY_MODE" "$WATCHLIST_TARGET_SPECIALIZATION" "$WATCHLIST_WALLET_ADDRESS" "$WATCHLIST_NOTES" "$WATCHLIST_APPROVAL_NOTES" "$SEED_RUNTIME_SOURCE_TRADE" <<'PY'
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path.cwd()
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from apps.polymarket_research.repository import PolymarketResearchRepository


db_path = sys.argv[1]
source_db_path = sys.argv[2]
display_name = sys.argv[3]
profile_ref = sys.argv[4]
priority_rank = int(sys.argv[5])
priority_mode = sys.argv[6]
target_specialization = sys.argv[7]
wallet_address = sys.argv[8]
link_notes = sys.argv[9]
approval_notes = sys.argv[10]
seed_runtime_source_trade = sys.argv[11].strip().lower() in {"1", "true", "yes", "on"}

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
if str(row["wallet_address"] or "").strip().lower() != wallet_address.strip().lower() or str(row["status"] or "") != "linked":
    row = repository.link_watchlist_wallet(row_id=row_id, wallet_address=wallet_address, notes=link_notes)

if int(row["operator_approved_pilot"] or 0) != 1:
    row = repository.approve_watchlist_pilot(row_id=row_id, approved=True, notes=approval_notes)

if seed_runtime_source_trade:
    source_path = Path(source_db_path)
    source_path.parent.mkdir(parents=True, exist_ok=True)
    now_text = datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%d %H:%M:%S")

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

    def _runtime_seed_row() -> dict[str, object]:
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
            "whale_address": wallet_address.strip().lower(),
            "timestamp": now_text,
            "opened_at": now_text,
            "closed_at": now_text,
        }

    def _fallback_value(column_name: str, column_type: str) -> object:
        normalized_name = column_name.lower()
        normalized_type = column_type.upper()
        if normalized_name == "id":
            return None
        if normalized_name.endswith("_at") or "time" in normalized_name or "date" in normalized_name:
            return now_text
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
            return str(_runtime_seed_row().get(normalized_name, "runtime_pilot_seed"))
        if normalized_name in {"market_id", "symbol_or_market_id"}:
            return "Bitcoin Up or Down - runtime pilot seed"
        if normalized_name == "whale_address":
            return wallet_address.strip().lower()
        if "INT" in normalized_type or "REAL" in normalized_type or "NUM" in normalized_type or "FLOA" in normalized_type:
            return 0
        return "runtime_pilot_seed"

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
        existing_row = connection.execute(
            """
            SELECT id
            FROM trades
            WHERE LOWER(COALESCE(whale_address, '')) = ?
              AND source_signal = 'runtime_pilot_seed'
              AND status = 'OPEN'
            ORDER BY id DESC
            LIMIT 1
            """,
            (wallet_address.strip().lower(),),
        ).fetchone()
        if existing_row is None:
            column_rows = _table_columns(connection, "trades")
            seed_row = _runtime_seed_row()
            insert_columns: list[str] = []
            insert_values: list[object] = []
            for column in column_rows:
                column_name = str(column["name"])
                if str(column_name).lower() == "id":
                    continue
                if column_name in seed_row:
                    value = seed_row[column_name]
                elif int(column["notnull"] or 0) == 1 and column["default"] is None:
                    value = _fallback_value(column_name, str(column["type"] or ""))
                else:
                    continue
                insert_columns.append(column_name)
                insert_values.append(value)
            connection.execute(
                f"INSERT INTO trades ({', '.join(insert_columns)}) VALUES ({', '.join('?' for _ in insert_columns)})",
                insert_values,
            )
        connection.commit()

print("POLYMARKET_RUNTIME_PILOT_READY")
print(
    {
        "display_name": display_name,
        "db_path": db_path,
        "operator_approved_pilot": True,
        "seed_runtime_source_trade": seed_runtime_source_trade,
        "source_db_path": source_db_path,
        "wallet_address": wallet_address.strip().lower(),
        "watchlist_row_id": row_id,
    }
)
PY
