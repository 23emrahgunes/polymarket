#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SERVICE_NAME="${SERVICE_NAME:-ghost-trader}"
DASHBOARD_SERVICE_NAME="${DASHBOARD_SERVICE_NAME:-ghost-trader-dashboard}"
TARGET_BRANCH="${TARGET_BRANCH:-ghost-trader-v1-0-impl-1884939158518981932}"
PRUNE_DISCOVERY_ROUTE_ONLY_DAYS="${PRUNE_DISCOVERY_ROUTE_ONLY_DAYS:-1}"
PRUNE_DISCOVERY_REJECT_DAYS="${PRUNE_DISCOVERY_REJECT_DAYS:-3}"
PRUNE_ORDERFLOW_REJECT_DAYS="${PRUNE_ORDERFLOW_REJECT_DAYS:-7}"
PRUNE_KEEP_DECISIONS_DAYS="${PRUNE_KEEP_DECISIONS_DAYS:-60}"
SNAPSHOT_WAIT_SECONDS="${SNAPSHOT_WAIT_SECONDS:-70}"

if [[ "$EUID" -eq 0 ]]; then
  SUDO=""
else
  SUDO="sudo"
fi

run_python() {
  if [[ -f "$REPO_ROOT/.venv/bin/python" ]]; then
    "$REPO_ROOT/.venv/bin/python" "$@"
  else
    python3 "$@"
  fi
}

section() {
  local title="$1"
  echo
  echo "============================================================"
  echo "$title"
  echo "============================================================"
}

main() {
  cd "$REPO_ROOT"

  section "Git Refresh"
  git stash push -u -m "vps-before-final-runtime-cleanup" || true
  git pull --ff-only origin "$TARGET_BRANCH"

  section "Stop Services"
  $SUDO systemctl stop "$SERVICE_NAME"
  if $SUDO systemctl cat "$DASHBOARD_SERVICE_NAME" >/dev/null 2>&1; then
    $SUDO systemctl stop "$DASHBOARD_SERVICE_NAME"
  fi

  section "Dry Run Cleanup"
  run_python "$REPO_ROOT/scripts/prune_runtime_data.py" \
    --discovery-route-only-days "$PRUNE_DISCOVERY_ROUTE_ONLY_DAYS" \
    --discovery-reject-days "$PRUNE_DISCOVERY_REJECT_DAYS" \
    --orderflow-reject-days "$PRUNE_ORDERFLOW_REJECT_DAYS" \
    --keep-decisions-days "$PRUNE_KEEP_DECISIONS_DAYS"

  section "Apply Cleanup"
  run_python "$REPO_ROOT/scripts/prune_runtime_data.py" \
    --apply \
    --vacuum \
    --discovery-route-only-days "$PRUNE_DISCOVERY_ROUTE_ONLY_DAYS" \
    --discovery-reject-days "$PRUNE_DISCOVERY_REJECT_DAYS" \
    --orderflow-reject-days "$PRUNE_ORDERFLOW_REJECT_DAYS" \
    --keep-decisions-days "$PRUNE_KEEP_DECISIONS_DAYS"

  section "Restart Services"
  $SUDO systemctl restart "$SERVICE_NAME"
  if $SUDO systemctl cat "$DASHBOARD_SERVICE_NAME" >/dev/null 2>&1; then
    $SUDO systemctl restart "$DASHBOARD_SERVICE_NAME"
  fi

  section "Wait For Fresh Snapshot"
  sleep "$SNAPSHOT_WAIT_SECONDS"

  section "Runtime State"
  run_python "$REPO_ROOT/scripts/query_runtime_state.py"

  section "Service Status"
  $SUDO systemctl status "$SERVICE_NAME" --no-pager || true
  if $SUDO systemctl cat "$DASHBOARD_SERVICE_NAME" >/dev/null 2>&1; then
    echo
    $SUDO systemctl status "$DASHBOARD_SERVICE_NAME" --no-pager || true
  fi
}

main "$@"
