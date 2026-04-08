#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="${1:-ghost-trader}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$REPO_ROOT/.env}"
DASHBOARD_SERVICE_NAME="${DASHBOARD_SERVICE_NAME:-${SERVICE_NAME}-dashboard}"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

echo "== systemd status =="
systemctl --no-pager --full status "$SERVICE_NAME" || true

if systemctl cat "$DASHBOARD_SERVICE_NAME" >/dev/null 2>&1; then
  echo
  echo "== dashboard status =="
  systemctl --no-pager --full status "$DASHBOARD_SERVICE_NAME" || true
  echo
  echo "== dashboard url =="
  echo "http://${DASHBOARD_HOST:-0.0.0.0}:${DASHBOARD_PORT:-8081}/"
fi

echo

echo "== recent logs =="
journalctl -u "$SERVICE_NAME" -n 50 --no-pager || true

echo

echo "== sqlite state =="
python3 "$REPO_ROOT/scripts/query_runtime_state.py"
