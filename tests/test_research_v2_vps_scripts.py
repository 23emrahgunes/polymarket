from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def test_bootstrap_research_v2_script_exists_and_contains_expected_flow() -> None:
    text = _read("scripts/bootstrap_research_v2_vps.sh")
    assert "#!/usr/bin/env bash" in text
    assert "set -euo pipefail" in text
    assert "SOURCE_REPO_DIR" in text
    assert "ghost-trader-research-dashboard" in text
    assert "ghost-trader-polymarket-copy" in text
    assert "DASHBOARD_LANE_MODE polymarket_research" in text
    assert "refresh_polymarket_research.sh" in text
    assert "refresh_polymarket_copy.sh" in text
    assert "POLYMARKET_COPY_DB_PATH" in text
    assert "ghost-trader-polymarket-copy.service.template" in text
    assert "query_polymarket_copy_lane.py summary" in text
    assert "import_research_seed_data.py" in text
    assert "ensure_polymarket_runtime_pilot.sh" in text
    assert "RESEARCH_DASHBOARD_PORT" in text
    assert "8082" in text
    assert 'git clone --branch "$RESEARCH_BRANCH" "$SOURCE_REPO_DIR" "$RESEARCH_REPO_DIR"' in text
    assert 'git -C "$RESEARCH_REPO_DIR" fetch "$SOURCE_REPO_DIR" "$RESEARCH_BRANCH"' in text
    assert 'reset_managed_worktree_drift "$RESEARCH_REPO_DIR"' in text


def test_polymarket_runtime_pilot_helper_exists_and_seeds_linked_priority_wallet() -> None:
    text = _read("scripts/ensure_polymarket_runtime_pilot.sh")
    assert "#!/usr/bin/env bash" in text
    assert "set -euo pipefail" in text
    assert "load_dotenv_file" in text
    assert "operator_approved_pilot" in text
    assert "runtime_pilot_seed" in text
    assert "watchlist_row_id" in text
    assert "PRAGMA table_info(" in text
    assert "_fallback_value" in text


def test_final_acceptance_vps_script_requires_runtime_green() -> None:
    text = _read("scripts/run_final_acceptance_vps.sh")
    assert "ensure_polymarket_runtime_pilot.sh" in text
    assert "query_polymarket_copy_lane.py run-once" in text
    assert 'poly_runtime.get("all_checks_passed")' in text
    assert 'poly_runtime.get("runtime_open_action_observed")' in text
    assert 'poly_runtime.get("runtime_open_position_observed")' in text
    assert 'binance_runtime.get("all_checks_passed")' in text
    assert 'binance_runtime.get("runtime_futures_long_execute")' in text
    assert 'binance_runtime.get("runtime_futures_short_execute")' in text
    assert 'binance_runtime.get("runtime_spot_long_execute")' in text
    assert 'binance_runtime.get("runtime_spot_short_reject")' in text


def test_bootstrap_binance_v2_script_exists_and_contains_expected_flow() -> None:
    text = _read("scripts/bootstrap_binance_technical_v2_vps.sh")
    assert "#!/usr/bin/env bash" in text
    assert "set -euo pipefail" in text
    assert "SOURCE_REPO_DIR" in text
    assert "BINANCE_REPO_DIR" in text
    assert "ghost-trader-binance-technical" in text
    assert "ghost-trader-binance-dashboard" in text
    assert "DASHBOARD_LANE_MODE binance_technical" in text
    assert "refresh_binance_technical.sh" in text
    assert "query_binance_technical_lane.py summary" in text
    assert "BINANCE_SPOT_ENABLED true" in text
    assert "BINANCE_DASHBOARD_PORT" in text
    assert "8083" in text
    assert 'git clone --branch "$BINANCE_BRANCH" "$SOURCE_REPO_DIR" "$BINANCE_REPO_DIR"' in text
    assert 'git -C "$BINANCE_REPO_DIR" fetch "$SOURCE_REPO_DIR" "$BINANCE_BRANCH"' in text
    assert 'reset_managed_worktree_drift "$BINANCE_REPO_DIR"' in text


def test_rebuild_research_v2_script_exists_and_uses_safe_absolute_bootstrap() -> None:
    text = _read("scripts/rebuild_research_v2_vps.sh")
    assert "#!/usr/bin/env bash" in text
    assert 'cd "$SOURCE_REPO_DIR"' in text
    assert 'rm -rf "$RESEARCH_REPO_DIR"' in text
    assert 'exec bash "$SOURCE_REPO_DIR/scripts/bootstrap_research_v2_vps.sh"' in text
    assert 'systemctl disable --now "${RESEARCH_DASHBOARD_SERVICE_NAME}.service"' in text
    assert 'systemctl disable --now "${POLYMARKET_COPY_SERVICE_NAME}.service"' in text


def test_rebuild_binance_v2_script_exists_and_uses_safe_absolute_bootstrap() -> None:
    text = _read("scripts/rebuild_binance_technical_v2_vps.sh")
    assert "#!/usr/bin/env bash" in text
    assert 'cd "$SOURCE_REPO_DIR"' in text
    assert 'rm -rf "$BINANCE_REPO_DIR"' in text
    assert 'exec bash "$SOURCE_REPO_DIR/scripts/bootstrap_binance_technical_v2_vps.sh"' in text
    assert 'systemctl disable --now "${BINANCE_RUNTIME_SERVICE_NAME}.service"' in text


def test_research_v2_systemd_templates_exist() -> None:
    dashboard_service = _read("deploy/systemd/ghost-trader-research-dashboard.service.template")
    refresh_service = _read("deploy/systemd/ghost-trader-research-refresh.service.template")
    refresh_timer = _read("deploy/systemd/ghost-trader-research-refresh.timer.template")
    copy_service = _read("deploy/systemd/ghost-trader-polymarket-copy.service.template")

    assert "scripts/start_dashboard.sh" in dashboard_service
    assert "scripts/refresh_polymarket_research.sh" in refresh_service
    assert "OnUnitActiveSec=15min" in refresh_timer
    assert "scripts/start_polymarket_copy.sh" in copy_service


def test_binance_v2_systemd_templates_exist() -> None:
    runtime_service = _read("deploy/systemd/ghost-trader-binance-technical.service.template")
    dashboard_service = _read("deploy/systemd/ghost-trader-binance-dashboard.service.template")

    assert "scripts/start_binance_technical.sh" in runtime_service
    assert "scripts/start_dashboard.sh" in dashboard_service
    assert "ghost-trader-binance-technical.service" in dashboard_service


def test_safe_dotenv_loader_is_used_by_research_scripts() -> None:
    loader = _read("scripts/lib/load_dotenv.sh")
    refresh_script = _read("scripts/refresh_polymarket_research.sh")
    dashboard_script = _read("scripts/start_dashboard.sh")
    binance_refresh_script = _read("scripts/refresh_binance_technical.sh")
    binance_start_script = _read("scripts/start_binance_technical.sh")
    copy_refresh_script = _read("scripts/refresh_polymarket_copy.sh")
    copy_start_script = _read("scripts/start_polymarket_copy.sh")

    assert "load_dotenv_file()" in loader
    assert "load_dotenv_file \"$REPO_ROOT/.env\"" in refresh_script
    assert "source \"$REPO_ROOT/.env\"" not in refresh_script
    assert "load_dotenv_file \"$ENV_FILE\"" in dashboard_script
    assert "source \"$ENV_FILE\"" not in dashboard_script
    assert "load_dotenv_file \"$REPO_ROOT/.env\"" in binance_refresh_script
    assert "source \"$REPO_ROOT/.env\"" not in binance_refresh_script
    assert "load_dotenv_file \"$ENV_FILE\"" in binance_start_script
    assert "source \"$ENV_FILE\"" not in binance_start_script
    assert "load_dotenv_file \"$REPO_ROOT/.env\"" in copy_refresh_script
    assert "source \"$REPO_ROOT/.env\"" not in copy_refresh_script
    assert "load_dotenv_file \"$ENV_FILE\"" in copy_start_script
    assert "source \"$ENV_FILE\"" not in copy_start_script


def test_final_split_bootstrap_script_installs_both_lanes() -> None:
    text = _read("scripts/bootstrap_split_final_v2_vps.sh")
    assert "#!/usr/bin/env bash" in text
    assert 'export RESEARCH_BRANCH="${RESEARCH_BRANCH:-$SPLIT_BRANCH}"' in text
    assert 'export BINANCE_BRANCH="${BINANCE_BRANCH:-$SPLIT_BRANCH}"' in text
    assert 'bash "$SOURCE_REPO_DIR/scripts/bootstrap_research_v2_vps.sh"' in text
    assert 'bash "$SOURCE_REPO_DIR/scripts/bootstrap_binance_technical_v2_vps.sh"' in text
    assert "ghost-trader-polymarket-copy" in text
    assert "Final split lanes installed." in text


def test_final_split_rebuild_script_reinstalls_both_lanes_safely() -> None:
    text = _read("scripts/rebuild_split_final_v2_vps.sh")
    assert "#!/usr/bin/env bash" in text
    assert 'rm -rf "$RESEARCH_REPO_DIR" "$BINANCE_REPO_DIR"' in text
    assert 'systemctl disable --now "${RESEARCH_DASHBOARD_SERVICE_NAME}.service"' in text
    assert 'systemctl disable --now "${RESEARCH_REFRESH_SERVICE_NAME}.service"' in text
    assert 'systemctl disable --now "${POLYMARKET_COPY_SERVICE_NAME}.service"' in text
    assert 'systemctl disable --now "${BINANCE_RUNTIME_SERVICE_NAME}.service"' in text
    assert 'systemctl disable --now "${BINANCE_DASHBOARD_SERVICE_NAME}.service"' in text
    assert 'exec bash "$SOURCE_REPO_DIR/scripts/bootstrap_split_final_v2_vps.sh"' in text


def test_final_local_acceptance_script_exists_and_covers_both_lanes() -> None:
    text = _read("scripts/run_final_acceptance_local.py")
    assert "#!/usr/bin/env python3" in text
    assert "FINAL_LOCAL_ACCEPTANCE_SUMMARY" in text
    assert "query_polymarket_research.py" in text
    assert "query_polymarket_copy_lane.py" in text
    assert "query_binance_technical_lane.py" in text
    assert "_binance_run_once_case" in text
    assert "copy_open_action_observed" in text
    assert "futures_long_execute" in text
    assert "spot_short_reject" in text


def test_query_wrapper_scripts_add_repo_root_to_sys_path() -> None:
    research_script = _read("scripts/query_polymarket_research.py")
    copy_script = _read("scripts/query_polymarket_copy_lane.py")
    binance_script = _read("scripts/query_binance_technical_lane.py")

    assert "REPO_ROOT = Path(__file__).resolve().parents[1]" in research_script
    assert "sys.path.insert(0, str(REPO_ROOT))" in research_script
    assert "REPO_ROOT = Path(__file__).resolve().parents[1]" in copy_script
    assert "sys.path.insert(0, str(REPO_ROOT))" in copy_script
    assert "REPO_ROOT = Path(__file__).resolve().parents[1]" in binance_script
    assert "sys.path.insert(0, str(REPO_ROOT))" in binance_script
