#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_REPO_DIR="${SOURCE_REPO_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
RESEARCH_REPO_DIR="${RESEARCH_REPO_DIR:-/root/polymarket-research-v2}"
RESEARCH_BRANCH="${RESEARCH_BRANCH:-codex/clean-split-rebuild-v1}"
RESEARCH_DASHBOARD_PORT="${RESEARCH_DASHBOARD_PORT:-8082}"
RESEARCH_DB_PATH="${RESEARCH_DB_PATH:-data/research_v2.db}"
LEGACY_DB_PATH="${LEGACY_DB_PATH:-/root/polymarket/data/ghost_trader.db}"
RESEARCH_DASHBOARD_SERVICE_NAME="${RESEARCH_DASHBOARD_SERVICE_NAME:-ghost-trader-research-dashboard}"
RESEARCH_REFRESH_SERVICE_NAME="${RESEARCH_REFRESH_SERVICE_NAME:-ghost-trader-research-refresh}"
RESEARCH_REFRESH_TIMER_NAME="${RESEARCH_REFRESH_TIMER_NAME:-ghost-trader-research-refresh.timer}"
POLYMARKET_COPY_SERVICE_NAME="${POLYMARKET_COPY_SERVICE_NAME:-ghost-trader-polymarket-copy}"
REPO_URL="${REPO_URL:-https://github.com/23emrahgunes/polymarket.git}"
DASHBOARD_HASH="${DASHBOARD_PASSWORD_HASH:-\$2y\$10\$ycVVdHE7aM4FCpXKhwIg2.lP64iQndfqYEI2uvcj7FQ.gXo9umPzy}"

install_packages() {
    if command -v apt-get >/dev/null 2>&1; then
        apt-get update
        apt-get install -y git python3 python3-venv python3-pip sqlite3 curl php-cli
        return
    fi
    if command -v dnf >/dev/null 2>&1; then
        dnf install -y git python3 python3-pip sqlite curl php-cli
        return
    fi
    if command -v yum >/dev/null 2>&1; then
        yum install -y git python3 python3-pip sqlite curl php-cli
        return
    fi
    echo "Unsupported package manager. Install git/python3/sqlite3/php-cli manually." >&2
    exit 1
}

upsert_env() {
    local env_file="$1"
    local key="$2"
    local value="$3"
    python3 - "$env_file" "$key" "$value" <<'PY'
from pathlib import Path
import sys

env_path = Path(sys.argv[1])
key = sys.argv[2]
value = sys.argv[3]
text = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
lines = text.splitlines()
updated = False
out = []
for line in lines:
    if line.startswith(f"{key}="):
        out.append(f"{key}={value}")
        updated = True
    else:
        out.append(line)
if not updated:
    out.append(f"{key}={value}")
env_path.write_text("\n".join(out).strip() + "\n", encoding="utf-8")
PY
}

render_template() {
    local template_path="$1"
    local destination="$2"
    shift 2
    python3 - "$template_path" "$destination" "$@" <<'PY'
from pathlib import Path
import sys

template_path = Path(sys.argv[1])
destination = Path(sys.argv[2])
text = template_path.read_text(encoding="utf-8")
for replacement in sys.argv[3:]:
    key, value = replacement.split("=", 1)
    text = text.replace(key, value)
destination.write_text(text, encoding="utf-8")
PY
}

reset_managed_worktree_drift() {
    local repo_dir="$1"
    local managed_paths=(
        "scripts/refresh_polymarket_research.sh"
        "scripts/refresh_polymarket_copy.sh"
        "scripts/start_polymarket_copy.sh"
    )

    [[ -d "$repo_dir/.git" ]] || return 0

    for path in "${managed_paths[@]}"; do
        if git -C "$repo_dir" diff --quiet -- "$path" 2>/dev/null; then
            continue
        fi

        echo "Resetting managed worktree drift for $path"
        git -C "$repo_dir" restore --source=HEAD --worktree -- "$path" 2>/dev/null \
            || git -C "$repo_dir" checkout -- "$path"
    done
}

install_packages

if [[ ! -d "$SOURCE_REPO_DIR/.git" ]]; then
    echo "SOURCE_REPO_DIR does not point to a git repo: $SOURCE_REPO_DIR" >&2
    echo "Either run this script from inside /root/polymarket or export SOURCE_REPO_DIR=/root/polymarket." >&2
    exit 1
fi

if [[ -d "$RESEARCH_REPO_DIR/.git" ]]; then
    reset_managed_worktree_drift "$RESEARCH_REPO_DIR"
    if git -C "$RESEARCH_REPO_DIR" fetch "$SOURCE_REPO_DIR" "$RESEARCH_BRANCH"; then
        git -C "$RESEARCH_REPO_DIR" checkout "$RESEARCH_BRANCH"
        git -C "$RESEARCH_REPO_DIR" merge --ff-only FETCH_HEAD
    else
        git -C "$RESEARCH_REPO_DIR" fetch origin
        git -C "$RESEARCH_REPO_DIR" checkout "$RESEARCH_BRANCH"
        git -C "$RESEARCH_REPO_DIR" pull --ff-only origin "$RESEARCH_BRANCH"
    fi
else
    git clone --branch "$RESEARCH_BRANCH" "$SOURCE_REPO_DIR" "$RESEARCH_REPO_DIR" \
        || git clone --branch "$RESEARCH_BRANCH" "$REPO_URL" "$RESEARCH_REPO_DIR"
fi

cd "$RESEARCH_REPO_DIR"

python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

touch .env
upsert_env .env GHOST_TRADER_DB_PATH "$RESEARCH_DB_PATH"
upsert_env .env DASHBOARD_ENABLED true
upsert_env .env DASHBOARD_HOST 0.0.0.0
upsert_env .env DASHBOARD_PORT "$RESEARCH_DASHBOARD_PORT"
upsert_env .env DASHBOARD_USER admin
upsert_env .env DASHBOARD_PASSWORD_HASH "$DASHBOARD_HASH"
upsert_env .env DASHBOARD_REFRESH_SECONDS 10
upsert_env .env DASHBOARD_API_CACHE_SECONDS 0
upsert_env .env DASHBOARD_LANE_MODE polymarket_research
upsert_env .env DASHBOARD_TARGET_SERVICE "$RESEARCH_DASHBOARD_SERVICE_NAME"
upsert_env .env DASHBOARD_SERVICE_NAME "$RESEARCH_DASHBOARD_SERVICE_NAME"
upsert_env .env POLYMARKET_RESEARCH_SOURCE_DB_PATH "$LEGACY_DB_PATH"
upsert_env .env POLYMARKET_RESEARCH_DISCOVERY_POOL 50
upsert_env .env POLYMARKET_RESEARCH_SHADOW_POOL 20
upsert_env .env POLYMARKET_RESEARCH_COPY_READY_POOL 5
upsert_env .env POLYMARKET_RESEARCH_SHADOW_WINDOW_DAYS 14
upsert_env .env POLYMARKET_COPY_DB_PATH "$RESEARCH_DB_PATH"
upsert_env .env POLYMARKET_COPY_SOURCE_DB_PATH "$LEGACY_DB_PATH"
upsert_env .env POLYMARKET_COPY_READY_LIMIT 5
upsert_env .env POLYMARKET_COPY_LOOKBACK_DAYS 14
upsert_env .env POLYMARKET_COPY_DELAY_SECONDS 90
upsert_env .env POLYMARKET_COPY_MIN_TRADE_SIZE_USD 25
upsert_env .env POLYMARKET_COPY_MAX_TRADE_SIZE_USD 50
upsert_env .env POLYMARKET_COPY_WALLET_RISK_LIMIT_USD 100
upsert_env .env POLYMARKET_COPY_MARKET_RISK_LIMIT_USD 150

if [[ -f "$LEGACY_DB_PATH" ]]; then
    python scripts/import_research_seed_data.py --source-db "$LEGACY_DB_PATH" --target-db "$RESEARCH_DB_PATH"
fi

bash scripts/ensure_polymarket_runtime_pilot.sh
bash scripts/refresh_polymarket_research.sh
bash scripts/refresh_polymarket_copy.sh

render_template \
    deploy/systemd/ghost-trader-research-dashboard.service.template \
    "/etc/systemd/system/${RESEARCH_DASHBOARD_SERVICE_NAME}.service" \
    "__WORKDIR__=$RESEARCH_REPO_DIR" \
    "__ENV_FILE__=$RESEARCH_REPO_DIR/.env" \
    "__USER__=root" \
    "__GROUP__=root"

render_template \
    deploy/systemd/ghost-trader-research-refresh.service.template \
    "/etc/systemd/system/${RESEARCH_REFRESH_SERVICE_NAME}.service" \
    "__WORKDIR__=$RESEARCH_REPO_DIR" \
    "__ENV_FILE__=$RESEARCH_REPO_DIR/.env" \
    "__USER__=root" \
    "__GROUP__=root"

render_template \
    deploy/systemd/ghost-trader-research-refresh.timer.template \
    "/etc/systemd/system/${RESEARCH_REFRESH_TIMER_NAME}" \
    "__REFRESH_SERVICE__=$RESEARCH_REFRESH_SERVICE_NAME"

render_template \
    deploy/systemd/ghost-trader-polymarket-copy.service.template \
    "/etc/systemd/system/${POLYMARKET_COPY_SERVICE_NAME}.service" \
    "__WORKDIR__=$RESEARCH_REPO_DIR" \
    "__ENV_FILE__=$RESEARCH_REPO_DIR/.env" \
    "__USER__=root" \
    "__GROUP__=root"

systemctl daemon-reload
systemctl enable --now "${RESEARCH_DASHBOARD_SERVICE_NAME}.service"
systemctl enable --now "$RESEARCH_REFRESH_TIMER_NAME"
systemctl enable --now "${POLYMARKET_COPY_SERVICE_NAME}.service"

cat <<EOF

Research v2 dashboard installed.
Repo: $RESEARCH_REPO_DIR
Source repo: $SOURCE_REPO_DIR
Dashboard: http://$(hostname -I | awk '{print $1}'):${RESEARCH_DASHBOARD_PORT}
DB: $RESEARCH_DB_PATH

Useful commands:
  systemctl status ${RESEARCH_DASHBOARD_SERVICE_NAME} --no-pager
  systemctl status ${RESEARCH_REFRESH_SERVICE_NAME} --no-pager
  systemctl status ${RESEARCH_REFRESH_TIMER_NAME} --no-pager
  systemctl status ${POLYMARKET_COPY_SERVICE_NAME} --no-pager
  cd $RESEARCH_REPO_DIR && bash scripts/refresh_polymarket_research.sh
  cd $RESEARCH_REPO_DIR && bash scripts/refresh_polymarket_copy.sh
  cd $RESEARCH_REPO_DIR && python scripts/query_polymarket_research.py summary --db-path $RESEARCH_DB_PATH
  cd $RESEARCH_REPO_DIR && python scripts/query_polymarket_copy_lane.py summary --db-path $RESEARCH_DB_PATH --source-db-path $LEGACY_DB_PATH
EOF
