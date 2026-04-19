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

DB_PATH="${GHOST_TRADER_DB_PATH:-data/binance_technical_v2.db}"
REPORT_DIR="$REPO_ROOT/reports/binance_technical"
LOG_DIR="$REPO_ROOT/logs"
mkdir -p "$REPORT_DIR" "$LOG_DIR"

python "$REPO_ROOT/scripts/query_binance_technical_lane.py" summary --db-path "$DB_PATH" \
    > "$REPORT_DIR/latest_summary.json"
