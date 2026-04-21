#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$REPO_ROOT/.env}"

# shellcheck disable=SC1091
source "$REPO_ROOT/scripts/lib/load_dotenv.sh"

if [[ -f "$ENV_FILE" ]]; then
    load_dotenv_file "$ENV_FILE"
fi

if [[ -f "$REPO_ROOT/.venv/bin/activate" ]]; then
    # shellcheck disable=SC1091
    source "$REPO_ROOT/.venv/bin/activate"
fi

DB_PATH="${POLYMARKET_COPY_DB_PATH:-${GHOST_TRADER_DB_PATH:-data/research_v2.db}}"
SOURCE_DB_PATH="${POLYMARKET_COPY_SOURCE_DB_PATH:-${POLYMARKET_RESEARCH_SOURCE_DB_PATH:-$DB_PATH}}"
mkdir -p "$REPO_ROOT/logs"

ITERATION_ARGS=()
if [[ -n "${POLYMARKET_COPY_LOOP_ITERATIONS:-}" ]]; then
    ITERATION_ARGS=(--iterations "$POLYMARKET_COPY_LOOP_ITERATIONS")
fi

exec python "$REPO_ROOT/scripts/query_polymarket_copy_lane.py" \
    --db-path "$DB_PATH" \
    --source-db-path "$SOURCE_DB_PATH" \
    run-loop \
    "${ITERATION_ARGS[@]}"
