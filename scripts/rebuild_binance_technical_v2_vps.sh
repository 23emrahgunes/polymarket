#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_REPO_DIR="${SOURCE_REPO_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
BINANCE_REPO_DIR="${BINANCE_REPO_DIR:-/root/binance-technical-v2}"
BINANCE_RUNTIME_SERVICE_NAME="${BINANCE_RUNTIME_SERVICE_NAME:-ghost-trader-binance-technical}"
BINANCE_DASHBOARD_SERVICE_NAME="${BINANCE_DASHBOARD_SERVICE_NAME:-ghost-trader-binance-dashboard}"

if [[ ! -d "$SOURCE_REPO_DIR/.git" ]]; then
    echo "SOURCE_REPO_DIR does not point to a git repo: $SOURCE_REPO_DIR" >&2
    exit 1
fi

if [[ "$BINANCE_REPO_DIR" == "/" || "$BINANCE_REPO_DIR" == "$SOURCE_REPO_DIR" ]]; then
    echo "Refusing to rebuild unsafe target directory: $BINANCE_REPO_DIR" >&2
    exit 1
fi

cd "$SOURCE_REPO_DIR"

systemctl disable --now "${BINANCE_RUNTIME_SERVICE_NAME}.service" >/dev/null 2>&1 || true
systemctl disable --now "${BINANCE_DASHBOARD_SERVICE_NAME}.service" >/dev/null 2>&1 || true

rm -rf "$BINANCE_REPO_DIR"

exec bash "$SOURCE_REPO_DIR/scripts/bootstrap_binance_technical_v2_vps.sh"
