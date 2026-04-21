#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_REPO_DIR="${SOURCE_REPO_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
RESEARCH_REPO_DIR="${RESEARCH_REPO_DIR:-/root/polymarket-research-v2}"
RESEARCH_DASHBOARD_SERVICE_NAME="${RESEARCH_DASHBOARD_SERVICE_NAME:-ghost-trader-research-dashboard}"
RESEARCH_REFRESH_SERVICE_NAME="${RESEARCH_REFRESH_SERVICE_NAME:-ghost-trader-research-refresh}"
RESEARCH_REFRESH_TIMER_NAME="${RESEARCH_REFRESH_TIMER_NAME:-ghost-trader-research-refresh.timer}"
POLYMARKET_COPY_SERVICE_NAME="${POLYMARKET_COPY_SERVICE_NAME:-ghost-trader-polymarket-copy}"

if [[ ! -d "$SOURCE_REPO_DIR/.git" ]]; then
    echo "SOURCE_REPO_DIR does not point to a git repo: $SOURCE_REPO_DIR" >&2
    exit 1
fi

if [[ "$RESEARCH_REPO_DIR" == "/" || "$RESEARCH_REPO_DIR" == "$SOURCE_REPO_DIR" ]]; then
    echo "Refusing to rebuild unsafe target directory: $RESEARCH_REPO_DIR" >&2
    exit 1
fi

cd "$SOURCE_REPO_DIR"

systemctl disable --now "${RESEARCH_DASHBOARD_SERVICE_NAME}.service" >/dev/null 2>&1 || true
systemctl disable --now "$RESEARCH_REFRESH_TIMER_NAME" >/dev/null 2>&1 || true
systemctl disable --now "${RESEARCH_REFRESH_SERVICE_NAME}.service" >/dev/null 2>&1 || true
systemctl disable --now "${POLYMARKET_COPY_SERVICE_NAME}.service" >/dev/null 2>&1 || true

rm -rf "$RESEARCH_REPO_DIR"

exec bash "$SOURCE_REPO_DIR/scripts/bootstrap_research_v2_vps.sh"
