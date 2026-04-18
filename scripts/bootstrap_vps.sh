#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_NAME="${SERVICE_NAME:-ghost-trader}"
DASHBOARD_SERVICE_NAME="${DASHBOARD_SERVICE_NAME:-ghost-trader-dashboard}"
APP_USER="${APP_USER:-${SUDO_USER:-$USER}}"
APP_GROUP="${APP_GROUP:-$(id -gn "$APP_USER")}"
ENV_FILE="$REPO_ROOT/.env"
VENV_DIR="$REPO_ROOT/.venv"
START_SCRIPT="$REPO_ROOT/scripts/start_bot.sh"
DASHBOARD_START_SCRIPT="$REPO_ROOT/scripts/start_dashboard.sh"
SYSTEMD_TEMPLATE="$REPO_ROOT/deploy/systemd/ghost-trader.service.template"
DASHBOARD_TEMPLATE="$REPO_ROOT/deploy/systemd/ghost-trader-dashboard.service.template"
SYSTEMD_TARGET="/etc/systemd/system/${SERVICE_NAME}.service"
DASHBOARD_TARGET="/etc/systemd/system/${DASHBOARD_SERVICE_NAME}.service"
RUN_TESTS="${RUN_TESTS:-1}"
RUN_DEBUG_VERIFY="${RUN_DEBUG_VERIFY:-1}"
DASHBOARD_DEFAULT_HASH='$2y$10$ycVVdHE7aM4FCpXKhwIg2.lP64iQndfqYEI2uvcj7FQ.gXo9umPzy'

# shellcheck disable=SC1091
source "$REPO_ROOT/scripts/lib/load_dotenv.sh"

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
    $SUDO apt-get install -y python3 python3-venv python3-pip sqlite3 git curl php-cli
    return
  fi

  if command -v dnf >/dev/null 2>&1; then
    $SUDO dnf install -y python3 python3-virtualenv python3-pip sqlite git curl php-cli
    return
  fi

  if command -v yum >/dev/null 2>&1; then
    $SUDO yum install -y python3 python3-pip sqlite git curl php-cli
    return
  fi

  echo "Unsupported package manager. Install python3, python3-venv, python3-pip, sqlite3, git, curl, and php-cli manually." >&2
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
  run_as_app_user "grep -q '^DASHBOARD_ENABLED=' '$ENV_FILE' || echo 'DASHBOARD_ENABLED=true' >> '$ENV_FILE'"
  run_as_app_user "grep -q '^DASHBOARD_HOST=' '$ENV_FILE' || echo 'DASHBOARD_HOST=0.0.0.0' >> '$ENV_FILE'"
  run_as_app_user "grep -q '^DASHBOARD_PORT=' '$ENV_FILE' || echo 'DASHBOARD_PORT=8081' >> '$ENV_FILE'"
  run_as_app_user "grep -q '^DASHBOARD_USER=' '$ENV_FILE' || echo 'DASHBOARD_USER=admin' >> '$ENV_FILE'"
  run_as_app_user "grep -q '^DASHBOARD_PASSWORD_HASH=' '$ENV_FILE' || printf \"DASHBOARD_PASSWORD_HASH='%s'\\n\" '$DASHBOARD_DEFAULT_HASH' >> '$ENV_FILE'"
  run_as_app_user "grep -q '^DASHBOARD_REFRESH_SECONDS=' '$ENV_FILE' || echo 'DASHBOARD_REFRESH_SECONDS=5' >> '$ENV_FILE'"
  run_as_app_user "grep -q '^DASHBOARD_LOG_LINES=' '$ENV_FILE' || echo 'DASHBOARD_LOG_LINES=40' >> '$ENV_FILE'"
}

load_env_settings() {
  load_dotenv_file "$ENV_FILE"
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

install_service_from_template() {
  local template_path="$1"
  local target_path="$2"
  local start_script="$3"

  local rendered
  rendered="$({
    sed \
      -e "s|__APP_USER__|$APP_USER|g" \
      -e "s|__APP_GROUP__|$APP_GROUP|g" \
      -e "s|__REPO_ROOT__|$REPO_ROOT|g" \
      -e "s|__ENV_FILE__|$ENV_FILE|g" \
      -e "s|__START_SCRIPT__|$start_script|g" \
      "$template_path"
  })"

  printf '%s\n' "$rendered" | $SUDO tee "$target_path" >/dev/null
  $SUDO chmod 644 "$target_path"
}

install_bot_service() {
  install_service_from_template "$SYSTEMD_TEMPLATE" "$SYSTEMD_TARGET" "$START_SCRIPT"
}

install_dashboard_service() {
  install_service_from_template "$DASHBOARD_TEMPLATE" "$DASHBOARD_TARGET" "$DASHBOARD_START_SCRIPT"
}

print_summary() {
  echo
  echo "Ghost Trader bootstrap complete."
  echo "Service name: $SERVICE_NAME"
  echo "Repo root:    $REPO_ROOT"
  echo "Env file:     $ENV_FILE"
  echo "Venv:         $VENV_DIR"
  if [[ "${DASHBOARD_ENABLED:-false}" == "true" ]]; then
    echo "Dashboard:    http://${DASHBOARD_HOST:-0.0.0.0}:${DASHBOARD_PORT:-8081}/"
    echo "Dashboard user: ${DASHBOARD_USER:-admin}"
    echo "Dashboard password hash copied from .env.example; rotate it before public exposure."
  fi
  echo
  echo "Useful commands:"
  echo "  sudo systemctl status $SERVICE_NAME"
  echo "  sudo journalctl -u $SERVICE_NAME -f"
  if [[ "${DASHBOARD_ENABLED:-false}" == "true" ]]; then
    echo "  sudo systemctl status $DASHBOARD_SERVICE_NAME"
  fi
  echo "  $REPO_ROOT/scripts/check_runtime.sh $SERVICE_NAME"
}

main() {
  cd "$REPO_ROOT"
  run_as_app_user "mkdir -p '$REPO_ROOT/data' '$REPO_ROOT/logs' '$REPO_ROOT/dashboard/public' '$REPO_ROOT/dashboard/lib'"
  chmod +x "$REPO_ROOT/scripts/start_bot.sh" "$REPO_ROOT/scripts/start_dashboard.sh" "$REPO_ROOT/scripts/check_runtime.sh" "$REPO_ROOT/scripts/bootstrap_vps.sh" "$REPO_ROOT/scripts/vps_refresh_and_evaluate.sh"
  install_packages
  ensure_env_file
  load_env_settings
  create_virtualenv
  run_checks
  install_bot_service
  if [[ "${DASHBOARD_ENABLED:-false}" == "true" ]]; then
    install_dashboard_service
  fi
  $SUDO systemctl daemon-reload
  $SUDO systemctl enable --now "$SERVICE_NAME"
  if [[ "${DASHBOARD_ENABLED:-false}" == "true" ]]; then
    $SUDO systemctl enable --now "$DASHBOARD_SERVICE_NAME"
  fi
  print_summary
}

main "$@"
