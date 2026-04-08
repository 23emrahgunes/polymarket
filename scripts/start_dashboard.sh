#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$REPO_ROOT/.env}"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

HOST="${DASHBOARD_HOST:-0.0.0.0}"
PORT="${DASHBOARD_PORT:-8081}"
DOC_ROOT="$REPO_ROOT/dashboard/public"

command -v php >/dev/null 2>&1 || {
  echo "php is required to run the Ghost Trader dashboard." >&2
  exit 1
}

mkdir -p "$REPO_ROOT/logs"
exec php -S "${HOST}:${PORT}" -t "$DOC_ROOT"
