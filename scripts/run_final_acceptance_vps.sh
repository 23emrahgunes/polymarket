#!/usr/bin/env bash
set -euo pipefail

RESEARCH_REPO_DIR="${RESEARCH_REPO_DIR:-/root/polymarket-research-v2}"
BINANCE_REPO_DIR="${BINANCE_REPO_DIR:-/root/binance-technical-v2}"
RESEARCH_DB_PATH="${RESEARCH_DB_PATH:-$RESEARCH_REPO_DIR/data/research_v2.db}"
RESEARCH_SOURCE_DB_PATH="${RESEARCH_SOURCE_DB_PATH:-/root/polymarket/data/ghost_trader.db}"
BINANCE_DB_PATH="${BINANCE_DB_PATH:-$BINANCE_REPO_DIR/data/binance_technical_v2.db}"

REQUIRED_SERVICES=(
  "ghost-trader-research-dashboard"
  "ghost-trader-research-refresh.timer"
  "ghost-trader-polymarket-copy"
  "ghost-trader-binance-technical"
  "ghost-trader-binance-dashboard"
)

for service_name in "${REQUIRED_SERVICES[@]}"; do
  service_state="$(systemctl is-active "$service_name" 2>/dev/null || true)"
  if [[ "$service_state" != "active" ]]; then
    echo "Acceptance failed: service '$service_name' is '$service_state'." >&2
    exit 1
  fi
done

if [[ ! -d "$RESEARCH_REPO_DIR" ]]; then
  echo "Acceptance failed: research repo missing at $RESEARCH_REPO_DIR" >&2
  exit 1
fi

if [[ ! -d "$BINANCE_REPO_DIR" ]]; then
  echo "Acceptance failed: Binance repo missing at $BINANCE_REPO_DIR" >&2
  exit 1
fi

pushd "$RESEARCH_REPO_DIR" >/dev/null
POLYMARKET_RUNTIME_SEED_OPEN_TRADE=true bash scripts/ensure_polymarket_runtime_pilot.sh >/tmp/polymarket_runtime_pilot.out
python scripts/query_polymarket_research.py summary --db-path "$RESEARCH_DB_PATH" --source-db-path "$RESEARCH_SOURCE_DB_PATH" >/tmp/polymarket_research_summary.out
python scripts/query_polymarket_copy_lane.py acceptance-run --db-path "$RESEARCH_DB_PATH" --source-db-path "$RESEARCH_SOURCE_DB_PATH" >/tmp/polymarket_acceptance_run.out
python scripts/query_polymarket_copy_lane.py acceptance-summary --db-path "$RESEARCH_DB_PATH" --source-db-path "$RESEARCH_SOURCE_DB_PATH" >/tmp/polymarket_acceptance_summary.out
python scripts/query_polymarket_copy_lane.py run-once --db-path "$RESEARCH_DB_PATH" --source-db-path "$RESEARCH_SOURCE_DB_PATH" >/tmp/polymarket_run_once.out
python scripts/query_polymarket_copy_lane.py runtime-acceptance-summary --db-path "$RESEARCH_DB_PATH" --source-db-path "$RESEARCH_SOURCE_DB_PATH" >/tmp/polymarket_runtime_acceptance_summary.out
popd >/dev/null

pushd "$BINANCE_REPO_DIR" >/dev/null
python scripts/query_binance_technical_lane.py acceptance-run --db-path "$BINANCE_DB_PATH" >/tmp/binance_acceptance_run.out
python scripts/query_binance_technical_lane.py acceptance-summary --db-path "$BINANCE_DB_PATH" >/tmp/binance_acceptance_summary.out
python scripts/query_binance_technical_lane.py runtime-acceptance-summary --db-path "$BINANCE_DB_PATH" >/tmp/binance_runtime_acceptance_summary.out
popd >/dev/null

python - <<'PY'
import json
import pathlib
import sys


def load_payload(path_str: str) -> dict:
    raw = pathlib.Path(path_str).read_text(encoding="utf-8").strip()
    if not raw:
        raise SystemExit(f"Acceptance failed: empty output from {path_str}")
    lines = raw.splitlines()
    payload_text = "\n".join(lines[1:]).strip() if len(lines) > 1 else lines[0]
    if not payload_text:
        raise SystemExit(f"Acceptance failed: missing JSON payload in {path_str}")
    try:
        return json.loads(payload_text)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Acceptance failed: invalid JSON in {path_str}: {exc}")


poly = load_payload("/tmp/polymarket_acceptance_summary.out")
poly_runtime = load_payload("/tmp/polymarket_runtime_acceptance_summary.out")
binance = load_payload("/tmp/binance_acceptance_summary.out")
binance_runtime = load_payload("/tmp/binance_runtime_acceptance_summary.out")

poly_required = {
    "copy_open_action_observed": "Polymarket copy open action",
    "copy_open_position_observed": "Polymarket copy open position",
}
binance_required = {
    "futures_long_execute": "Binance futures LONG execute",
    "futures_short_execute": "Binance futures SHORT execute",
    "spot_long_execute": "Binance spot LONG execute",
    "spot_short_reject": "Binance spot SHORT reject",
}

failures: list[str] = []
if not poly.get("all_checks_passed"):
    failures.append("Polymarket acceptance checklist is not green")
for key, label in poly_required.items():
    if not poly.get(key):
        failures.append(f"{label} missing")
if not poly_runtime.get("all_checks_passed"):
    failures.append("Polymarket runtime checklist is not green")
if int(poly_runtime.get("eligible_copy_wallets_total", 0) or 0) <= 0:
    failures.append("Polymarket runtime has no eligible copy wallets")
if not poly_runtime.get("runtime_open_action_observed"):
    failures.append("Polymarket runtime open action missing")
if not poly_runtime.get("runtime_open_position_observed"):
    failures.append("Polymarket runtime open position missing")

if not binance.get("all_checks_passed"):
    failures.append("Binance acceptance checklist is not green")
for key, label in binance_required.items():
    if not binance.get(key):
        failures.append(f"{label} missing")
if not binance_runtime.get("all_checks_passed"):
    failures.append("Binance runtime checklist is not green")
if not binance_runtime.get("runtime_futures_long_execute"):
    failures.append("Binance runtime futures LONG missing")
if not binance_runtime.get("runtime_futures_short_execute"):
    failures.append("Binance runtime futures SHORT missing")
if not binance_runtime.get("runtime_spot_long_execute"):
    failures.append("Binance runtime spot LONG missing")
if not binance_runtime.get("runtime_spot_short_reject"):
    failures.append("Binance runtime spot SHORT reject missing")

if failures:
    raise SystemExit("Acceptance failed:\n- " + "\n- ".join(failures))

report = {
    "polymarket": poly,
    "polymarket_runtime": poly_runtime,
    "binance": binance,
    "binance_runtime": binance_runtime,
}
print("FINAL_ACCEPTANCE_SUMMARY")
print(json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True))
PY
