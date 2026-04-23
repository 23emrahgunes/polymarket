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

import sys
from pathlib import Path

REPO_ROOT = Path.cwd()
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from apps.polymarket_research.runtime_pilot import ensure_runtime_priority_wallet


result = ensure_runtime_priority_wallet(
    db_path=sys.argv[1],
    source_db_path=sys.argv[2],
    display_name=sys.argv[3],
    profile_ref=sys.argv[4],
    priority_rank=int(sys.argv[5]),
    priority_mode=sys.argv[6],
    target_specialization=sys.argv[7],
    wallet_address=sys.argv[8],
    link_notes=sys.argv[9],
    approval_notes=sys.argv[10],
    seed_runtime_source_trade=sys.argv[11].strip().lower() in {"1", "true", "yes", "on"},
)

print("POLYMARKET_RUNTIME_PILOT_READY")
print(result)
PY
