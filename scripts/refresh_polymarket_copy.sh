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

DB_PATH="${POLYMARKET_COPY_DB_PATH:-${GHOST_TRADER_DB_PATH:-data/research_v2.db}}"
SOURCE_DB_PATH="${POLYMARKET_COPY_SOURCE_DB_PATH:-${POLYMARKET_RESEARCH_SOURCE_DB_PATH:-$DB_PATH}}"
REPORT_DIR="$REPO_ROOT/reports/polymarket_copy"
LOG_DIR="$REPO_ROOT/logs"
mkdir -p "$REPORT_DIR" "$LOG_DIR"

python "$REPO_ROOT/scripts/query_polymarket_copy_lane.py" \
    --db-path "$DB_PATH" \
    --source-db-path "$SOURCE_DB_PATH" \
    run-once \
    > "$REPORT_DIR/latest_summary.json"
