#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

if [[ -f "$REPO_ROOT/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "$REPO_ROOT/.env"
    set +a
fi

if [[ -f "$REPO_ROOT/.venv/bin/activate" ]]; then
    # shellcheck disable=SC1091
    source "$REPO_ROOT/.venv/bin/activate"
fi

DB_PATH="${GHOST_TRADER_DB_PATH:-data/research_v2.db}"
REPORT_DIR="$REPO_ROOT/reports/polymarket_research"
LOG_DIR="$REPO_ROOT/logs"
mkdir -p "$REPORT_DIR" "$LOG_DIR"

python "$REPO_ROOT/scripts/query_polymarket_research.py" summary --db-path "$DB_PATH" \
    > "$REPORT_DIR/latest_summary.json"
