#!/usr/bin/env bash
set -euo pipefail

BRANCH="${BRANCH:-ghost-trader-v1-0-impl-1884939158518981932}"
REPO="${REPO:-/root/polymarket}"
REMOTE="${REMOTE:-https://github.com/23emrahgunes/polymarket.git}"
SERVICE_NAME="${SERVICE_NAME:-ghost-trader}"
DASHBOARD_SERVICE_NAME="${DASHBOARD_SERVICE_NAME:-ghost-trader-dashboard}"

if [[ "$EUID" -eq 0 ]]; then
  SUDO=""
else
  SUDO="sudo"
fi

section() {
  local title="$1"
  echo
  echo "============================================================"
  echo "$title"
  echo "============================================================"
}

enable_flag() {
  local key="$1"
  local value="$2"
  local env_file="$3"
  if [[ -f "$env_file" ]] && grep -q "^${key}=" "$env_file"; then
    sed -i "s/^${key}=.*/${key}=${value}/" "$env_file"
  else
    echo "${key}=${value}" >> "$env_file"
  fi
}

recover_from_live_process() {
  local pid="$1"
  if [[ "${pid:-0}" -gt 1 ]] && [[ -d "/proc/$pid/cwd" ]]; then
    cp -a "/proc/$pid/cwd/." "$REPO/" 2>/dev/null || true
  fi
}

main() {
  section "Live Recovery Denemesi"
  local bot_pid dashboard_pid
  bot_pid="$($SUDO systemctl show -p MainPID --value "$SERVICE_NAME" 2>/dev/null || echo 0)"
  dashboard_pid="$($SUDO systemctl show -p MainPID --value "$DASHBOARD_SERVICE_NAME" 2>/dev/null || echo 0)"

  mkdir -p "$REPO"
  recover_from_live_process "$bot_pid"
  recover_from_live_process "$dashboard_pid"

  section "Repo Restore"
  if [[ ! -d "$REPO/.git" ]]; then
    rm -rf "$REPO"
    git clone -b "$BRANCH" "$REMOTE" "$REPO"
  fi

  cd "$REPO"
  git pull --ff-only origin "$BRANCH" || true

  section "Runtime Layout"
  mkdir -p data logs

  section "Env Recovery"
  if [[ ! -f .env ]]; then
    cp .env.example .env
  fi
  enable_flag "PAPER_SAMPLING_MODE" "true" ".env"
  enable_flag "BINANCE_FUTURES_ENABLED" "true" ".env"
  enable_flag "BINANCE_SPOT_ENABLED" "true" ".env"
  enable_flag "BINANCE_TECHNICAL_PAPER_ENABLED" "true" ".env"
  enable_flag "BINANCE_TECHNICAL_FORCE_SAMPLE" "true" ".env"

  section "Virtualenv"
  if [[ ! -x .venv/bin/python ]]; then
    python3 -m venv .venv
  fi
  . .venv/bin/activate
  python -m pip install --upgrade pip
  python -m pip install -r requirements.txt

  section "Service Restart"
  $SUDO systemctl restart "$SERVICE_NAME"
  if $SUDO systemctl cat "$DASHBOARD_SERVICE_NAME" >/dev/null 2>&1; then
    $SUDO systemctl restart "$DASHBOARD_SERVICE_NAME"
  fi

  section "Service Status"
  $SUDO systemctl status "$SERVICE_NAME" --no-pager || true
  if $SUDO systemctl cat "$DASHBOARD_SERVICE_NAME" >/dev/null 2>&1; then
    echo
    $SUDO systemctl status "$DASHBOARD_SERVICE_NAME" --no-pager || true
  fi

  section "Runtime Ozet"
  . .venv/bin/activate
  python scripts/query_runtime_state.py | grep -A 12 -E "MAPPING_RATE_SUMMARY|WHALE_COPY_SUMMARY|BINANCE_TECHNICAL_SUMMARY" || true

  section "Kritik Dosyalar"
  ls -lah .env data 2>/dev/null || true
}

main "$@"
