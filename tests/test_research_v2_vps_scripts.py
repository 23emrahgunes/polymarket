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


def test_research_v2_systemd_templates_exist() -> None:
    dashboard_service = _read("deploy/systemd/ghost-trader-research-dashboard.service.template")
    refresh_service = _read("deploy/systemd/ghost-trader-research-refresh.service.template")
    refresh_timer = _read("deploy/systemd/ghost-trader-research-refresh.timer.template")

    assert "scripts/start_dashboard.sh" in dashboard_service
    assert "scripts/refresh_polymarket_research.sh" in refresh_service
    assert "OnUnitActiveSec=15min" in refresh_timer
