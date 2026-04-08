from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "vps_refresh_and_evaluate.sh"


def _script_text() -> str:
    return SCRIPT_PATH.read_text(encoding="utf-8")


def test_vps_refresh_script_exists() -> None:
    assert SCRIPT_PATH.exists()


def test_vps_refresh_script_uses_fail_fast_shell_settings() -> None:
    text = _script_text()
    assert "#!/usr/bin/env bash" in text
    assert "set -euo pipefail" in text


def test_vps_refresh_script_supports_required_flags() -> None:
    text = _script_text()
    for flag in (
        "--branch",
        "--quick",
        "--skip-tests",
        "--skip-proofs",
        "--skip-analysis",
        "--service",
    ):
        assert flag in text


def test_vps_refresh_script_contains_expected_full_sequence() -> None:
    text = _script_text()
    expected_commands = (
        'git -C "$REPO_ROOT" fetch origin',
        'git -C "$REPO_ROOT" pull --ff-only origin "$TARGET_BRANCH"',
        'python -m pip install -r "$REPO_ROOT/requirements.txt"',
        'python -m pytest -q',
        'python "$REPO_ROOT/scripts/verify_runtime_trade.py"',
        'python "$REPO_ROOT/scripts/verify_crypto_dual_venue.py"',
        'python "$REPO_ROOT/scripts/verify_crypto_triple_venue.py"',
        '$SUDO systemctl restart "$SERVICE_NAME"',
        'python "$REPO_ROOT/scripts/analyze_performance.py"',
        'python "$REPO_ROOT/scripts/run_backtest.py"',
        'python "$REPO_ROOT/scripts/swot_report.py"',
        'python "$REPO_ROOT/scripts/query_runtime_state.py"',
        '$SUDO systemctl status "$SERVICE_NAME" --no-pager',
        '$SUDO journalctl -u "$SERVICE_NAME" -n 50 --no-pager',
    )
    for command in expected_commands:
        assert command in text


def test_vps_refresh_script_fails_on_dirty_git_state() -> None:
    text = _script_text()
    assert 'git -C "$REPO_ROOT" status --porcelain' in text
    assert "Local git changes detected." in text
    assert "git stash" not in text.lower()
    assert "git reset --hard" not in text.lower()


def test_vps_refresh_script_writes_operation_log_and_reads_reports() -> None:
    text = _script_text()
    assert 'LOG_FILE="$REPO_ROOT/logs/vps_refresh_latest.log"' in text
    assert 'reports/performance/summary.json' in text
    assert 'reports/performance/swot_report.json' in text
    assert 'final_verdict.verdict' in text
    assert 'evidence.live_paper_closed' in text
