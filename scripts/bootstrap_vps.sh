#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_NAME="${SERVICE_NAME:-ghost-trader}"
APP_USER="${APP_USER:-${SUDO_USER:-$USER}}"
APP_GROUP="${APP_GROUP:-$(id -gn "$APP_USER")}"
ENV_FILE="$REPO_ROOT/.env"
VENV_DIR="$REPO_ROOT/.venv"
START_SCRIPT="$REPO_ROOT/scripts/start_bot.sh"
SYSTEMD_TEMPLATE="$REPO_ROOT/deploy/systemd/ghost-trader.service.template"
SYSTEMD_TARGET="/etc/systemd/system/${SERVICE_NAME}.service"
RUN_TESTS="${RUN_TESTS:-1}"
RUN_DEBUG_VERIFY="${RUN_DEBUG_VERIFY:-1}"

if [[ "$EUID" -eq 0 && -z "${SUDO_USER:-}" ]]; then
  echo "Run this script as the repo-owning user, not directly as root." >&2
  exit 1
fi

if [[ "$EUID" -eq 0 ]]; then
  SUDO=""
else
  SUDO="sudo"
fi

run_as_app_user() {
  local command="$1"
  if [[ "$EUID" -eq 0 && -n "${SUDO_USER:-}" ]]; then
    sudo -u "$APP_USER" bash -lc "cd '$REPO_ROOT' && $command"
  else
    bash -lc "cd '$REPO_ROOT' && $command"
  fi
}

install_packages() {
  if command -v apt-get >/dev/null 2>&1; then
    $SUDO apt-get update
    $SUDO apt-get install -y python3 python3-venv python3-pip sqlite3 git curl
    return
  fi

  if command -v dnf >/dev/null 2>&1; then
    $SUDO dnf install -y python3 python3-virtualenv python3-pip sqlite git curl
    return
  fi

  if command -v yum >/dev/null 2>&1; then
    $SUDO yum install -y python3 python3-pip sqlite git curl
    return
  fi

  echo "Unsupported package manager. Install python3, python3-venv, python3-pip, sqlite3, git manually." >&2
  exit 1
}

ensure_env_file() {
  if [[ ! -f "$ENV_FILE" ]]; then
    run_as_app_user "cp '$REPO_ROOT/.env.example' '$ENV_FILE'"
  fi

  run_as_app_user "grep -q '^EXCHANGE_ID=' '$ENV_FILE' || echo 'EXCHANGE_ID=coinbase' >> '$ENV_FILE'"
  run_as_app_user "grep -q '^DEBUG_SIGNAL_MODE=' '$ENV_FILE' || echo 'DEBUG_SIGNAL_MODE=false' >> '$ENV_FILE'"
  run_as_app_user "grep -q '^RUNTIME_VERIFY_ONCE=' '$ENV_FILE' || echo 'RUNTIME_VERIFY_ONCE=false' >> '$ENV_FILE'"
  run_as_app_user "grep -q '^GHOST_TRADER_DB_PATH=' '$ENV_FILE' || echo 'GHOST_TRADER_DB_PATH=data/ghost_trader.db' >> '$ENV_FILE'"
}

create_virtualenv() {
  run_as_app_user "python3 -m venv '$VENV_DIR'"
  run_as_app_user "source '$VENV_DIR/bin/activate' && python -m pip install --upgrade pip && python -m pip install -r '$REPO_ROOT/requirements.txt'"
}

run_checks() {
  if [[ "$RUN_TESTS" == "1" ]]; then
    run_as_app_user "source '$VENV_DIR/bin/activate' && python -m pytest -q"
  fi
  if [[ "$RUN_DEBUG_VERIFY" == "1" ]]; then
    run_as_app_user "source '$VENV_DIR/bin/activate' && python '$REPO_ROOT/scripts/verify_runtime_trade.py'"
  fi
}

install_service() {
  local rendered
  rendered="$(
    sed \
      -e "s|__APP_USER__|$APP_USER|g" \
      -e "s|__APP_GROUP__|$APP_GROUP|g" \
      -e "s|__REPO_ROOT__|$REPO_ROOT|g" \
      -e "s|__ENV_FILE__|$ENV_FILE|g" \
      -e "s|__START_SCRIPT__|$START_SCRIPT|g" \
      "$SYSTEMD_TEMPLATE"
  )"

  printf '%s\n' "$rendered" | $SUDO tee "$SYSTEMD_TARGET" >/dev/null
  $SUDO chmod 644 "$SYSTEMD_TARGET"
  $SUDO systemctl daemon-reload
  $SUDO systemctl enable --now "$SERVICE_NAME"
}

print_summary() {
  echo
  echo "Ghost Trader bootstrap complete."
  echo "Service name: $SERVICE_NAME"
  echo "Repo root:    $REPO_ROOT"
  echo "Env file:     $ENV_FILE"
  echo "Venv:         $VENV_DIR"
  echo
  echo "Useful commands:"
  echo "  sudo systemctl status $SERVICE_NAME"
  echo "  sudo journalctl -u $SERVICE_NAME -f"
  echo "  $REPO_ROOT/scripts/check_runtime.sh $SERVICE_NAME"
}

main() {
  cd "$REPO_ROOT"
  run_as_app_user "mkdir -p '$REPO_ROOT/data' '$REPO_ROOT/logs'"
  chmod +x "$REPO_ROOT/scripts/start_bot.sh" "$REPO_ROOT/scripts/check_runtime.sh" "$REPO_ROOT/scripts/bootstrap_vps.sh" "$REPO_ROOT/scripts/vps_refresh_and_evaluate.sh"
  install_packages
  ensure_env_file
  create_virtualenv
  run_checks
  install_service
  print_summary
}

main "$@"
