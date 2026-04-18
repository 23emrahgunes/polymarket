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
    assert "DASHBOARD_LANE_MODE polymarket_research" in text
    assert "refresh_polymarket_research.sh" in text
    assert "import_research_seed_data.py" in text
    assert "RESEARCH_DASHBOARD_PORT" in text
    assert "8082" in text
    assert 'git clone --branch "$RESEARCH_BRANCH" "$SOURCE_REPO_DIR" "$RESEARCH_REPO_DIR"' in text
    assert 'git -C "$RESEARCH_REPO_DIR" fetch "$SOURCE_REPO_DIR" "$RESEARCH_BRANCH"' in text
    assert 'reset_managed_worktree_drift "$RESEARCH_REPO_DIR"' in text


def test_research_v2_systemd_templates_exist() -> None:
    dashboard_service = _read("deploy/systemd/ghost-trader-research-dashboard.service.template")
    refresh_service = _read("deploy/systemd/ghost-trader-research-refresh.service.template")
    refresh_timer = _read("deploy/systemd/ghost-trader-research-refresh.timer.template")

    assert "scripts/start_dashboard.sh" in dashboard_service
    assert "scripts/refresh_polymarket_research.sh" in refresh_service
    assert "OnUnitActiveSec=15min" in refresh_timer


def test_safe_dotenv_loader_is_used_by_research_scripts() -> None:
    loader = _read("scripts/lib/load_dotenv.sh")
    refresh_script = _read("scripts/refresh_polymarket_research.sh")
    dashboard_script = _read("scripts/start_dashboard.sh")

    assert "load_dotenv_file()" in loader
    assert "load_dotenv_file \"$REPO_ROOT/.env\"" in refresh_script
    assert "source \"$REPO_ROOT/.env\"" not in refresh_script
    assert "load_dotenv_file \"$ENV_FILE\"" in dashboard_script
    assert "source \"$ENV_FILE\"" not in dashboard_script


def test_query_wrapper_scripts_add_repo_root_to_sys_path() -> None:
    research_script = _read("scripts/query_polymarket_research.py")
    binance_script = _read("scripts/query_binance_technical_lane.py")

    assert "REPO_ROOT = Path(__file__).resolve().parents[1]" in research_script
    assert "sys.path.insert(0, str(REPO_ROOT))" in research_script
    assert "REPO_ROOT = Path(__file__).resolve().parents[1]" in binance_script
    assert "sys.path.insert(0, str(REPO_ROOT))" in binance_script
