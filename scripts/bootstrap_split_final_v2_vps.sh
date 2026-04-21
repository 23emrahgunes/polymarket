#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_REPO_DIR="${SOURCE_REPO_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
SPLIT_BRANCH="${SPLIT_BRANCH:-codex/clean-split-rebuild-v1}"

if [[ ! -d "$SOURCE_REPO_DIR/.git" ]]; then
    echo "SOURCE_REPO_DIR does not point to a git repo: $SOURCE_REPO_DIR" >&2
    exit 1
fi

export SOURCE_REPO_DIR
export RESEARCH_BRANCH="${RESEARCH_BRANCH:-$SPLIT_BRANCH}"
export BINANCE_BRANCH="${BINANCE_BRANCH:-$SPLIT_BRANCH}"

bash "$SOURCE_REPO_DIR/scripts/bootstrap_research_v2_vps.sh"
bash "$SOURCE_REPO_DIR/scripts/bootstrap_binance_technical_v2_vps.sh"

cat <<EOF

Final split lanes installed.

Research:
  repo: ${RESEARCH_REPO_DIR:-/root/polymarket-research-v2}
  dashboard: http://$(hostname -I | awk '{print $1}'):${RESEARCH_DASHBOARD_PORT:-8082}

Binance:
  repo: ${BINANCE_REPO_DIR:-/root/binance-technical-v2}
  dashboard: http://$(hostname -I | awk '{print $1}'):${BINANCE_DASHBOARD_PORT:-8083}

Recommended smoke checks:
  systemctl status ghost-trader-research-dashboard --no-pager
  systemctl status ghost-trader-research-refresh --no-pager
  systemctl status ghost-trader-research-refresh.timer --no-pager
  systemctl status ghost-trader-polymarket-copy --no-pager
  systemctl status ghost-trader-binance-technical --no-pager
  systemctl status ghost-trader-binance-dashboard --no-pager
EOF
