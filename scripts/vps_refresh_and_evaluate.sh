#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

SERVICE_NAME="ghost-trader"
TARGET_BRANCH=""
QUICK_MODE=0
SKIP_TESTS=0
SKIP_PROOFS=0
SKIP_ANALYSIS=0

CURRENT_STEP="initialization"
TEST_STATUS="skipped"
PROOF_STATUS="skipped"
ANALYSIS_STATUS="skipped"
SERVICE_STATUS_SUMMARY="unknown"

usage() {
  cat <<'EOF'
Usage: ./scripts/vps_refresh_and_evaluate.sh [options]

Options:
  --branch <name>    Pull and switch to the given branch before refresh.
  --quick            Pull + restart + status only. Skips dependency sync, tests, proofs, and analysis.
  --skip-tests       Skip pytest.
  --skip-proofs      Skip verification harnesses.
  --skip-analysis    Skip performance analysis and SWOT report.
  --service <name>   systemd service name. Default: ghost-trader
  --help             Show this help.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --branch)
      [[ $# -ge 2 ]] || { echo "Missing value for --branch" >&2; exit 1; }
      TARGET_BRANCH="$2"
      shift 2
      ;;
    --quick)
      QUICK_MODE=1
      shift
      ;;
    --skip-tests)
      SKIP_TESTS=1
      shift
      ;;
    --skip-proofs)
      SKIP_PROOFS=1
      shift
      ;;
    --skip-analysis)
      SKIP_ANALYSIS=1
      shift
      ;;
    --service)
      [[ $# -ge 2 ]] || { echo "Missing value for --service" >&2; exit 1; }
      SERVICE_NAME="$2"
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if [[ "$QUICK_MODE" == "1" ]]; then
  SKIP_TESTS=1
  SKIP_PROOFS=1
  SKIP_ANALYSIS=1
fi

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

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

on_error() {
  local exit_code=$?
  echo
  echo "FAILED at step: $CURRENT_STEP" >&2
  echo "See log file for full output: $LOG_FILE" >&2
  exit "$exit_code"
}

require_file() {
  local path="$1"
  local hint="$2"
  [[ -f "$path" ]] || fail "$path is missing. $hint"
}

read_json_field() {
  local json_path="$1"
  local key_path="$2"
  python - "$json_path" "$key_path" <<'PY'
import json
import sys
from pathlib import Path

json_path = Path(sys.argv[1])
key_path = sys.argv[2].split(".")

if not json_path.exists():
    print("missing")
    raise SystemExit(0)

with json_path.open("r", encoding="utf-8") as fh:
    data = json.load(fh)

value = data
for key in key_path:
    if isinstance(value, dict) and key in value:
        value = value[key]
    else:
        print("missing")
        raise SystemExit(0)

if value is None:
    print("null")
elif isinstance(value, (dict, list)):
    print(json.dumps(value))
else:
    print(value)
PY
}

CURRENT_BRANCH="$(git -C "$REPO_ROOT" branch --show-current 2>/dev/null || true)"
if [[ -z "$TARGET_BRANCH" ]]; then
  TARGET_BRANCH="$CURRENT_BRANCH"
fi

[[ -n "$TARGET_BRANCH" ]] || fail "Could not determine git branch from current checkout. Re-run with --branch <name>."

mkdir -p "$REPO_ROOT/logs"
LOG_FILE="$REPO_ROOT/logs/vps_refresh_latest.log"
exec > >(tee "$LOG_FILE") 2>&1
trap on_error ERR

section "Ghost Trader VPS Refresh"
echo "Repo root: $REPO_ROOT"
echo "Service:   $SERVICE_NAME"
echo "Branch:    $TARGET_BRANCH"
echo "Mode:      $([[ "$QUICK_MODE" == "1" ]] && echo "quick" || echo "full")"
echo "Log file:  $LOG_FILE"

CURRENT_STEP="sanity checks"
section "Sanity Checks"
[[ -d "$REPO_ROOT/.git" ]] || fail "This does not look like a git repo. Clone the repo first."
require_file "$REPO_ROOT/requirements.txt" "Run from a bootstrapped Ghost Trader repo."
[[ -d "$REPO_ROOT/scripts" ]] || fail "scripts/ directory is missing. Clone the repo again."
[[ -d "$REPO_ROOT/.venv" ]] || fail ".venv is missing. Run scripts/bootstrap_vps.sh first."
[[ -f "$REPO_ROOT/.env" ]] || fail ".env is missing. Run scripts/bootstrap_vps.sh first."
if [[ ! -f "/etc/systemd/system/${SERVICE_NAME}.service" ]] && ! $SUDO systemctl cat "$SERVICE_NAME" >/dev/null 2>&1; then
  fail "systemd service '$SERVICE_NAME' is missing. Run scripts/bootstrap_vps.sh first."
fi
if [[ -n "$(git -C "$REPO_ROOT" status --porcelain)" ]]; then
  fail "Local git changes detected. Commit/stash them manually before running this script."
fi

CURRENT_STEP="git fetch"
section "Git Fetch"
git -C "$REPO_ROOT" fetch origin

CURRENT_STEP="git checkout"
section "Git Checkout"
CURRENT_BRANCH="$(git -C "$REPO_ROOT" branch --show-current)"
if [[ "$CURRENT_BRANCH" != "$TARGET_BRANCH" ]]; then
  git -C "$REPO_ROOT" checkout "$TARGET_BRANCH"
fi

CURRENT_STEP="git pull"
section "Git Pull"
git -C "$REPO_ROOT" pull --ff-only origin "$TARGET_BRANCH"

CURRENT_STEP="activate venv"
section "Activate Virtualenv"
cd "$REPO_ROOT"
source "$REPO_ROOT/.venv/bin/activate"
python --version

if [[ "$QUICK_MODE" != "1" ]]; then
  CURRENT_STEP="dependency sync"
  section "Dependency Sync"
  python -m pip install -r "$REPO_ROOT/requirements.txt"
fi

if [[ "$SKIP_TESTS" != "1" ]]; then
  CURRENT_STEP="pytest"
  section "Test Suite"
  python -m pytest -q
  TEST_STATUS="passed"
else
  TEST_STATUS="skipped"
fi

if [[ "$SKIP_PROOFS" != "1" ]]; then
  CURRENT_STEP="sports proof"
  section "Verification: Sports Runtime"
  python "$REPO_ROOT/scripts/verify_runtime_trade.py"

  CURRENT_STEP="dual venue proof"
  section "Verification: Crypto Dual Venue"
  python "$REPO_ROOT/scripts/verify_crypto_dual_venue.py"

  CURRENT_STEP="triple venue proof"
  section "Verification: Crypto Triple Venue"
  python "$REPO_ROOT/scripts/verify_crypto_triple_venue.py"
  PROOF_STATUS="passed"
else
  PROOF_STATUS="skipped"
fi

CURRENT_STEP="service restart"
section "Service Restart"
$SUDO systemctl restart "$SERVICE_NAME"

if [[ "$SKIP_ANALYSIS" != "1" ]]; then
  CURRENT_STEP="performance analysis"
  section "Performance Analysis"
  python "$REPO_ROOT/scripts/analyze_performance.py"

  CURRENT_STEP="backtest"
  section "Backtest / Replay"
  python "$REPO_ROOT/scripts/run_backtest.py"

  CURRENT_STEP="swot report"
  section "SWOT / Verdict"
  python "$REPO_ROOT/scripts/swot_report.py"
  ANALYSIS_STATUS="passed"
else
  ANALYSIS_STATUS="skipped"
fi

CURRENT_STEP="runtime state"
section "Runtime State"
python "$REPO_ROOT/scripts/query_runtime_state.py"

CURRENT_STEP="service status"
section "Service Status"
$SUDO systemctl status "$SERVICE_NAME" --no-pager
SERVICE_STATUS_SUMMARY="running"

CURRENT_STEP="recent logs"
section "Recent Logs"
$SUDO journalctl -u "$SERVICE_NAME" -n 50 --no-pager

CURRENT_STEP="summary"
section "Summary"
CURRENT_COMMIT="$(git -C "$REPO_ROOT" rev-parse --short HEAD)"
CURRENT_BRANCH="$(git -C "$REPO_ROOT" branch --show-current)"
SUMMARY_JSON="$REPO_ROOT/reports/performance/summary.json"
SWOT_JSON="$REPO_ROOT/reports/performance/swot_report.json"
LIVE_PAPER_CLOSED="$(read_json_field "$SUMMARY_JSON" "evidence.live_paper_closed")"
SYNTHETIC_TOTAL="$(read_json_field "$SUMMARY_JSON" "evidence.synthetic_total")"
CORE_EXPECTANCY="$(read_json_field "$SUMMARY_JSON" "core.expectancy")"
FINAL_VERDICT="$(read_json_field "$SWOT_JSON" "final_verdict.verdict")"
FINAL_REASON="$(read_json_field "$SWOT_JSON" "final_verdict.reason")"

echo "Commit:               $CURRENT_COMMIT"
echo "Branch:               $CURRENT_BRANCH"
echo "Tests:                $TEST_STATUS"
echo "Proofs:               $PROOF_STATUS"
echo "Analysis:             $ANALYSIS_STATUS"
echo "Service:              $SERVICE_STATUS_SUMMARY"
echo "Live paper closed:    $LIVE_PAPER_CLOSED"
echo "Synthetic samples:    $SYNTHETIC_TOTAL"
echo "Core expectancy:      $CORE_EXPECTANCY"
echo "Final verdict:        $FINAL_VERDICT"
echo "Verdict reason:       $FINAL_REASON"
echo "Operation log:        $LOG_FILE"
