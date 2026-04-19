#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_REPO_DIR="${SOURCE_REPO_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
RESEARCH_REPO_DIR="${RESEARCH_REPO_DIR:-/root/polymarket-research-v2}"
BINANCE_REPO_DIR="${BINANCE_REPO_DIR:-/root/binance-technical-v2}"
RESEARCH_DASHBOARD_SERVICE_NAME="${RESEARCH_DASHBOARD_SERVICE_NAME:-ghost-trader-research-dashboard}"
RESEARCH_REFRESH_SERVICE_NAME="${RESEARCH_REFRESH_SERVICE_NAME:-ghost-trader-research-refresh}"
RESEARCH_REFRESH_TIMER_NAME="${RESEARCH_REFRESH_TIMER_NAME:-ghost-trader-research-refresh.timer}"
BINANCE_RUNTIME_SERVICE_NAME="${BINANCE_RUNTIME_SERVICE_NAME:-ghost-trader-binance-technical}"
BINANCE_DASHBOARD_SERVICE_NAME="${BINANCE_DASHBOARD_SERVICE_NAME:-ghost-trader-binance-dashboard}"

if [[ ! -d "$SOURCE_REPO_DIR/.git" ]]; then
    echo "SOURCE_REPO_DIR does not point to a git repo: $SOURCE_REPO_DIR" >&2
    exit 1
fi

for target_dir in "$RESEARCH_REPO_DIR" "$BINANCE_REPO_DIR"; do
    if [[ "$target_dir" == "/" || "$target_dir" == "$SOURCE_REPO_DIR" ]]; then
        echo "Refusing to rebuild unsafe target directory: $target_dir" >&2
        exit 1
    fi
done

cd "$SOURCE_REPO_DIR"

systemctl disable --now "${RESEARCH_DASHBOARD_SERVICE_NAME}.service" >/dev/null 2>&1 || true
systemctl disable --now "$RESEARCH_REFRESH_TIMER_NAME" >/dev/null 2>&1 || true
systemctl disable --now "${RESEARCH_REFRESH_SERVICE_NAME}.service" >/dev/null 2>&1 || true
systemctl disable --now "${BINANCE_RUNTIME_SERVICE_NAME}.service" >/dev/null 2>&1 || true
systemctl disable --now "${BINANCE_DASHBOARD_SERVICE_NAME}.service" >/dev/null 2>&1 || true

rm -rf "$RESEARCH_REPO_DIR" "$BINANCE_REPO_DIR"

exec bash "$SOURCE_REPO_DIR/scripts/bootstrap_split_final_v2_vps.sh"
