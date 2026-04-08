#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${VENV_DIR:-$REPO_ROOT/.venv}"
ENV_FILE="${ENV_FILE:-$REPO_ROOT/.env}"

if [[ ! -d "$VENV_DIR" ]]; then
  echo "Missing virtual environment at $VENV_DIR" >&2
  exit 1
fi

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

mkdir -p "$REPO_ROOT/data" "$REPO_ROOT/logs"

source "$VENV_DIR/bin/activate"
exec python -u -m src.main
