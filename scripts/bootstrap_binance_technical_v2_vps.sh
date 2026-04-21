#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_REPO_DIR="${SOURCE_REPO_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
BINANCE_REPO_DIR="${BINANCE_REPO_DIR:-/root/binance-technical-v2}"
BINANCE_BRANCH="${BINANCE_BRANCH:-codex/clean-split-rebuild-v1}"
BINANCE_DASHBOARD_PORT="${BINANCE_DASHBOARD_PORT:-8083}"
BINANCE_DB_PATH="${BINANCE_DB_PATH:-data/binance_technical_v2.db}"
BINANCE_RUNTIME_SERVICE_NAME="${BINANCE_RUNTIME_SERVICE_NAME:-ghost-trader-binance-technical}"
BINANCE_DASHBOARD_SERVICE_NAME="${BINANCE_DASHBOARD_SERVICE_NAME:-ghost-trader-binance-dashboard}"
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
    local paths=(
        "scripts/start_binance_technical.sh"
        "scripts/refresh_binance_technical.sh"
    )

    [[ -d "$repo_dir/.git" ]] || return 0

    for path in "${paths[@]}"; do
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

if [[ -d "$BINANCE_REPO_DIR/.git" ]]; then
    reset_managed_worktree_drift "$BINANCE_REPO_DIR"
    if git -C "$BINANCE_REPO_DIR" fetch "$SOURCE_REPO_DIR" "$BINANCE_BRANCH"; then
        git -C "$BINANCE_REPO_DIR" checkout "$BINANCE_BRANCH"
        git -C "$BINANCE_REPO_DIR" merge --ff-only FETCH_HEAD
    else
        git -C "$BINANCE_REPO_DIR" fetch origin
        git -C "$BINANCE_REPO_DIR" checkout "$BINANCE_BRANCH"
        git -C "$BINANCE_REPO_DIR" pull --ff-only origin "$BINANCE_BRANCH"
    fi
else
    git clone --branch "$BINANCE_BRANCH" "$SOURCE_REPO_DIR" "$BINANCE_REPO_DIR" \
        || git clone --branch "$BINANCE_BRANCH" "$REPO_URL" "$BINANCE_REPO_DIR"
fi

cd "$BINANCE_REPO_DIR"

python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

mkdir -p "$(dirname "$BINANCE_DB_PATH")"
touch .env
upsert_env .env GHOST_TRADER_DB_PATH "$BINANCE_DB_PATH"
upsert_env .env DASHBOARD_ENABLED true
upsert_env .env DASHBOARD_HOST 0.0.0.0
upsert_env .env DASHBOARD_PORT "$BINANCE_DASHBOARD_PORT"
upsert_env .env DASHBOARD_USER admin
upsert_env .env DASHBOARD_PASSWORD_HASH "$DASHBOARD_HASH"
upsert_env .env DASHBOARD_REFRESH_SECONDS 10
upsert_env .env DASHBOARD_API_CACHE_SECONDS 0
upsert_env .env DASHBOARD_LANE_MODE binance_technical
upsert_env .env DASHBOARD_TARGET_SERVICE "$BINANCE_RUNTIME_SERVICE_NAME"
upsert_env .env DASHBOARD_SERVICE_NAME "$BINANCE_DASHBOARD_SERVICE_NAME"
upsert_env .env BINANCE_TECHNICAL_PAPER_ENABLED true
upsert_env .env BINANCE_TECHNICAL_FORCE_SAMPLE true
upsert_env .env BINANCE_TECHNICAL_FRESH_ONLY true
upsert_env .env BINANCE_TECHNICAL_SYMBOLS BTC,ETH,SOL
upsert_env .env BINANCE_FUTURES_ENABLED true
upsert_env .env BINANCE_SPOT_ENABLED false

python scripts/query_binance_technical_lane.py summary --db-path "$BINANCE_DB_PATH" >/dev/null
bash scripts/refresh_binance_technical.sh

render_template \
    deploy/systemd/ghost-trader-binance-technical.service.template \
    "/etc/systemd/system/${BINANCE_RUNTIME_SERVICE_NAME}.service" \
    "__WORKDIR__=$BINANCE_REPO_DIR" \
    "__ENV_FILE__=$BINANCE_REPO_DIR/.env" \
    "__USER__=root" \
    "__GROUP__=root"

render_template \
    deploy/systemd/ghost-trader-binance-dashboard.service.template \
    "/etc/systemd/system/${BINANCE_DASHBOARD_SERVICE_NAME}.service" \
    "__WORKDIR__=$BINANCE_REPO_DIR" \
    "__ENV_FILE__=$BINANCE_REPO_DIR/.env" \
    "__USER__=root" \
    "__GROUP__=root"

systemctl daemon-reload
systemctl enable --now "${BINANCE_RUNTIME_SERVICE_NAME}.service"
systemctl enable --now "${BINANCE_DASHBOARD_SERVICE_NAME}.service"

cat <<EOF

Binance technical v2 installed.
Repo: $BINANCE_REPO_DIR
Source repo: $SOURCE_REPO_DIR
Dashboard: http://$(hostname -I | awk '{print $1}'):${BINANCE_DASHBOARD_PORT}
DB: $BINANCE_DB_PATH

Useful commands:
  systemctl status ${BINANCE_RUNTIME_SERVICE_NAME} --no-pager
  systemctl status ${BINANCE_DASHBOARD_SERVICE_NAME} --no-pager
  cd $BINANCE_REPO_DIR && bash scripts/refresh_binance_technical.sh
  cd $BINANCE_REPO_DIR && python scripts/query_binance_technical_lane.py summary --db-path $BINANCE_DB_PATH
EOF
