#!/usr/bin/env python3
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from apps.binance_technical.config import BinanceTechnicalSettings
from apps.binance_technical.repository import BinanceTechnicalRepository
from apps.binance_technical.runtime import BinanceTechnicalRuntime
from apps.polymarket_research.runtime_pilot import ensure_runtime_priority_wallet
from tests.test_split_lanes import (
    _FakeMarketDataProvider,
    _FakeSignalEngine,
    _create_binance_lane_db,
    _create_polymarket_research_db,
    _create_polymarket_source_evidence_db,
    _market_frame,
    _technical_signal,
)


def _run_command(args: list[str], *, cwd: Path | None = None, expect_success: bool = True) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        args,
        cwd=str(cwd or REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    if expect_success and completed.returncode != 0:
        raise RuntimeError(
            f"Command failed ({completed.returncode}): {' '.join(args)}\nSTDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}"
        )
    return completed


def _extract_json_after_header(raw: str, header: str) -> Any:
    lines = raw.splitlines()
    for index, line in enumerate(lines):
        if line.strip() == header:
            payload = "\n".join(lines[index + 1 :]).strip()
            if not payload:
                raise RuntimeError(f"Missing JSON payload after header {header}")
            return json.loads(payload)
    raise RuntimeError(f"Header {header} not found in output:\n{raw}")


def _binance_run_once_case(*, db_path: Path, venue: str, symbol: str, direction: str) -> dict[str, Any]:
    repository = BinanceTechnicalRepository(str(db_path))
    settings = BinanceTechnicalSettings(
        db_path=str(db_path),
        symbols=[symbol],
        futures_enabled=(venue == "binance_futures"),
        spot_enabled=(venue == "binance_spot"),
    )
    frames = {
        (venue, symbol.upper()): _market_frame(
            symbol,
            venue,
            mark_price=50000.0 if symbol.upper() == "BTC" else 2500.0 if symbol.upper() == "ETH" else 150.0,
        ),
    }
    signals = {
        (venue, symbol.upper()): _technical_signal(
            symbol=symbol,
            direction=direction,
            should_trade=True,
            score=0.78,
            threshold=0.54,
            inputs={"score_blocker_labels": [], "snapshot_quality": "trusted_ticker_book"},
        ),
    }
    runtime = BinanceTechnicalRuntime(
        settings,
        repository=repository,
        provider=_FakeMarketDataProvider(frames),
        signal_engine=_FakeSignalEngine(signals),
    )
    return runtime.run_once()


def _create_polymarket_source_profile_db(db_path: Path, profile: str) -> None:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    if profile == "minimal_market_id":
        cur.execute(
            """
            CREATE TABLE trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                whale_address TEXT,
                venue TEXT,
                market_id TEXT,
                status TEXT,
                pnl REAL,
                size REAL,
                closed_at TEXT,
                timestamp TEXT,
                category TEXT,
                source_signal TEXT,
                side TEXT
            )
            """
        )
    elif profile == "symbol_only":
        cur.execute(
            """
            CREATE TABLE trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                whale_address TEXT,
                venue TEXT,
                symbol_or_market_id TEXT,
                status TEXT,
                pnl REAL,
                size REAL,
                closed_at TEXT,
                timestamp TEXT,
                category TEXT,
                source_signal TEXT,
                side TEXT
            )
            """
        )
    elif profile == "extended_with_stats":
        cur.execute(
            """
            CREATE TABLE trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                whale_address TEXT,
                venue TEXT,
                market_id TEXT,
                symbol_or_market_id TEXT,
                execution_mode TEXT NOT NULL,
                instrument_type TEXT NOT NULL,
                status TEXT NOT NULL,
                pnl REAL NOT NULL,
                size REAL NOT NULL,
                price REAL NOT NULL,
                confidence REAL NOT NULL,
                source_signal TEXT NOT NULL,
                signal_family TEXT,
                category TEXT,
                strategy_profile TEXT,
                sample_kind TEXT,
                is_synthetic INTEGER NOT NULL DEFAULT 0,
                side TEXT,
                timestamp TEXT,
                opened_at TEXT,
                closed_at TEXT
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE whale_wallets (
                address TEXT PRIMARY KEY,
                source_type TEXT,
                discovery_score REAL,
                event_count_24h INTEGER,
                last_event_amount REAL,
                last_event_category TEXT,
                last_seen_at TEXT,
                enabled INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE whale_stats (
                address TEXT PRIMARY KEY,
                trust_score REAL,
                total_trades INTEGER,
                total_pnl REAL
            )
            """
        )
    else:
        raise RuntimeError(f"Unknown Polymarket schema profile: {profile}")
    conn.commit()
    conn.close()


def main() -> int:
    code_gate: dict[str, Any] = {}
    lane_gate: dict[str, Any] = {}
    schema_profile_results: list[dict[str, Any]] = []

    code_gate["pytest"] = _run_command([sys.executable, "-m", "pytest", "-q"]).returncode == 0
    code_gate["php_index_lint"] = _run_command(["php", "-l", str(REPO_ROOT / "dashboard" / "public" / "index.php")]).returncode == 0
    code_gate["php_presenter_lint"] = _run_command(["php", "-l", str(REPO_ROOT / "dashboard" / "public" / "presenter.php")]).returncode == 0

    with tempfile.TemporaryDirectory(prefix="final-local-acceptance-", ignore_cleanup_errors=True) as tmp_dir_name:
        tmp_dir = Path(tmp_dir_name)

        research_db = tmp_dir / "research_v2.db"
        source_db = tmp_dir / "source_ghost_trader.db"
        binance_acceptance_db = tmp_dir / "binance_acceptance.db"
        binance_futures_long_db = tmp_dir / "binance_futures_long.db"
        binance_futures_short_db = tmp_dir / "binance_futures_short.db"
        binance_spot_long_db = tmp_dir / "binance_spot_long.db"

        detailed_address = "0x89b5cdaaa4866c1e738406712012a630b4078beb"
        stats_address = "0x1111111111111111111111111111111111111111"
        _create_polymarket_research_db(research_db)
        _create_polymarket_source_evidence_db(
            source_db,
            detailed_address=detailed_address,
            stats_address=stats_address,
        )

        _run_command(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "query_polymarket_research.py"),
                "watchlist-add",
                "--display-name",
                "ohanism",
                "--profile-ref",
                "https://polymarket.com/tr/@ohanism",
                "--priority-rank",
                "1",
                "--priority-mode",
                "fast_track_shadow",
                "--target-specialization",
                "CRYPTO",
                "--notes",
                "final local acceptance",
                "--db-path",
                str(research_db),
                "--source-db-path",
                str(source_db),
            ]
        )
        invalid_link = _run_command(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "query_polymarket_research.py"),
                "watchlist-link",
                "--id",
                "1",
                "--wallet-address",
                "0xINVALID",
                "--notes",
                "invalid validation",
                "--db-path",
                str(research_db),
                "--source-db-path",
                str(source_db),
            ],
            expect_success=False,
        )
        lane_gate["research_watchlist_link_validation"] = invalid_link.returncode != 0
        watchlist_list = _run_command(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "query_polymarket_research.py"),
                "watchlist-list",
                "--db-path",
                str(research_db),
                "--source-db-path",
                str(source_db),
            ]
        )
        lane_gate["research_watchlist_list"] = "WATCHLIST_ROWS" in watchlist_list.stdout
        linked_row = _run_command(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "query_polymarket_research.py"),
                "watchlist-link",
                "--id",
                "1",
                "--wallet-address",
                detailed_address,
                "--notes",
                "verified manually",
                "--db-path",
                str(research_db),
                "--source-db-path",
                str(source_db),
            ]
        )
        lane_gate["research_watchlist_link"] = "WATCHLIST_ROW_LINKED" in linked_row.stdout
        pilot_approval = _run_command(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "query_polymarket_research.py"),
                "watchlist-approve-pilot",
                "--id",
                "1",
                "--notes",
                "local acceptance pilot approval",
                "--db-path",
                str(research_db),
                "--source-db-path",
                str(source_db),
            ]
        )
        lane_gate["research_watchlist_pilot_approval"] = "WATCHLIST_ROW_PILOT_APPROVAL_UPDATED" in pilot_approval.stdout
        research_summary_raw = _run_command(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "query_polymarket_research.py"),
                "summary",
                "--db-path",
                str(research_db),
                "--source-db-path",
                str(source_db),
            ]
        )
        research_summary = _extract_json_after_header(research_summary_raw.stdout, "POLYMARKET_RESEARCH_SUMMARY_JSON")
        lane_gate["research_summary"] = int(research_summary.get("identity_resolution_summary", {}).get("linked_entries", 0)) >= 1
        lane_gate["ohanism_linked"] = any(
            row.get("display_name") == "ohanism" and row.get("wallet_address") == detailed_address
            for row in research_summary.get("priority_watchlist_rows", [])
        )
        lane_gate["ohanism_pilot_approved"] = any(
            row.get("display_name") == "ohanism"
            and bool(row.get("operator_approved_pilot"))
            and row.get("pilot_copy_gate_reason") in {"eligible", "shadow_proven"}
            for row in research_summary.get("priority_watchlist_rows", [])
        )

        copy_summary_raw = _run_command(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "query_polymarket_copy_lane.py"),
                "summary",
                "--db-path",
                str(research_db),
                "--source-db-path",
                str(source_db),
            ]
        )
        copy_summary = _extract_json_after_header(copy_summary_raw.stdout, "POLYMARKET_COPY_LANE_SUMMARY")
        lane_gate["copy_summary"] = int(copy_summary.get("copy_execution_summary", {}).get("eligible_copy_wallets_total", 0)) > 0
        lane_gate["copy_runtime_eligible_wallets"] = int(copy_summary.get("copy_execution_summary", {}).get("eligible_copy_wallets_total", 0)) > 0

        now_text = (datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
        with sqlite3.connect(source_db) as connection:
            connection.execute(
                """
                INSERT INTO trades (
                    whale_address, venue, market_id, status, pnl, size,
                    closed_at, timestamp, category, source_signal, side
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    detailed_address,
                    "polymarket",
                    "Bitcoin Up or Down - local runtime source",
                    "OPEN",
                    0.0,
                    140.0,
                    now_text,
                    now_text,
                    "CRYPTO",
                    "local_runtime_source",
                    "BUY",
                ),
            )
            connection.commit()
        copy_run_once = _run_command(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "query_polymarket_copy_lane.py"),
                "run-once",
                "--db-path",
                str(research_db),
                "--source-db-path",
                str(source_db),
            ]
        )
        lane_gate["copy_run_once"] = "POLYMARKET_COPY_LANE_SUMMARY" in copy_run_once.stdout
        _run_command(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "query_polymarket_copy_lane.py"),
                "acceptance-run",
                "--db-path",
                str(research_db),
                "--source-db-path",
                str(source_db),
            ]
        )
        copy_acceptance_raw = _run_command(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "query_polymarket_copy_lane.py"),
                "acceptance-summary",
                "--db-path",
                str(research_db),
                "--source-db-path",
                str(source_db),
            ]
        )
        copy_acceptance = _extract_json_after_header(copy_acceptance_raw.stdout, "POLYMARKET_COPY_ACCEPTANCE_SUMMARY")
        lane_gate["copy_acceptance_all_checks"] = bool(copy_acceptance.get("all_checks_passed"))
        lane_gate["copy_open_action_observed"] = bool(copy_acceptance.get("copy_open_action_observed"))
        lane_gate["copy_open_position_observed"] = bool(copy_acceptance.get("copy_open_position_observed"))
        copy_runtime_acceptance_raw = _run_command(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "query_polymarket_copy_lane.py"),
                "runtime-acceptance-summary",
                "--db-path",
                str(research_db),
                "--source-db-path",
                str(source_db),
            ]
        )
        copy_runtime_acceptance = _extract_json_after_header(
            copy_runtime_acceptance_raw.stdout,
            "POLYMARKET_COPY_RUNTIME_ACCEPTANCE_SUMMARY",
        )
        lane_gate["copy_runtime_open_action"] = bool(copy_runtime_acceptance.get("runtime_open_action_observed"))
        lane_gate["copy_runtime_open_position"] = bool(copy_runtime_acceptance.get("runtime_open_position_observed"))
        lane_gate["copy_runtime_all_checks"] = bool(copy_runtime_acceptance.get("all_checks_passed"))

        schema_profiles = ["minimal_market_id", "symbol_only", "extended_with_stats"]
        for profile in schema_profiles:
            profile_research_db = tmp_dir / f"{profile}_research.db"
            profile_source_db = tmp_dir / f"{profile}_source.db"
            _create_polymarket_research_db(profile_research_db)
            _create_polymarket_source_profile_db(profile_source_db, profile)
            ensure_runtime_priority_wallet(
                db_path=str(profile_research_db),
                source_db_path=str(profile_source_db),
                display_name="ohanism",
                profile_ref="https://polymarket.com/tr/@ohanism",
                priority_rank=1,
                priority_mode="fast_track_shadow",
                target_specialization="CRYPTO",
                wallet_address=detailed_address,
                link_notes="schema profile validation",
                approval_notes="schema profile validation",
                seed_runtime_source_trade=True,
            )
            ensure_runtime_priority_wallet(
                db_path=str(profile_research_db),
                source_db_path=str(profile_source_db),
                display_name="ohanism",
                profile_ref="https://polymarket.com/tr/@ohanism",
                priority_rank=1,
                priority_mode="fast_track_shadow",
                target_specialization="CRYPTO",
                wallet_address=detailed_address,
                link_notes="schema profile validation",
                approval_notes="schema profile validation",
                seed_runtime_source_trade=True,
            )
            profile_research_summary_raw = _run_command(
                [
                    sys.executable,
                    str(REPO_ROOT / "scripts" / "query_polymarket_research.py"),
                    "summary",
                    "--db-path",
                    str(profile_research_db),
                    "--source-db-path",
                    str(profile_source_db),
                ]
            )
            profile_research_summary = _extract_json_after_header(
                profile_research_summary_raw.stdout,
                "POLYMARKET_RESEARCH_SUMMARY_JSON",
            )
            profile_copy_run_once_raw = _run_command(
                [
                    sys.executable,
                    str(REPO_ROOT / "scripts" / "query_polymarket_copy_lane.py"),
                    "run-once",
                    "--db-path",
                    str(profile_research_db),
                    "--source-db-path",
                    str(profile_source_db),
                ]
            )
            profile_copy_summary = _extract_json_after_header(
                profile_copy_run_once_raw.stdout,
                "POLYMARKET_COPY_LANE_SUMMARY",
            )
            profile_runtime = profile_copy_summary.get("copy_runtime_acceptance_summary", {})
            with sqlite3.connect(profile_source_db) as connection:
                historical_seed_count = int(
                    connection.execute(
                        "SELECT COUNT(*) FROM trades WHERE source_signal = 'manual_source_replay' AND status = 'CLOSED'"
                    ).fetchone()[0]
                )
                runtime_seed_count = int(
                    connection.execute(
                        "SELECT COUNT(*) FROM trades WHERE source_signal = 'runtime_pilot_seed' AND status = 'OPEN'"
                    ).fetchone()[0]
                )
            profile_row = next(
                row for row in profile_research_summary["priority_watchlist_rows"] if row["display_name"] == "ohanism"
            )
            profile_result = {
                "profile": profile,
                "historical_seed_count": historical_seed_count,
                "runtime_seed_count": runtime_seed_count,
                "historical_trade_evidence_status": profile_row.get("historical_trade_evidence_status", ""),
                "pilot_copy_gate_status": profile_row.get("pilot_copy_gate_status", ""),
                "pilot_copy_gate_reason": profile_row.get("pilot_copy_gate_reason", ""),
                "eligible_copy_wallets_total": int(
                    profile_copy_summary.get("copy_execution_summary", {}).get("eligible_copy_wallets_total", 0)
                ),
                "runtime_open_action_observed": bool(profile_runtime.get("runtime_open_action_observed")),
                "runtime_open_position_observed": bool(profile_runtime.get("runtime_open_position_observed")),
            }
            profile_result["all_checks_passed"] = (
                profile_result["historical_seed_count"] == 5
                and profile_result["runtime_seed_count"] == 1
                and profile_result["historical_trade_evidence_status"] == "detailed_trade_history"
                and profile_result["pilot_copy_gate_status"] in {"promoted", "shadow_proven"}
                and profile_result["eligible_copy_wallets_total"] > 0
                and profile_result["runtime_open_action_observed"]
                and profile_result["runtime_open_position_observed"]
            )
            schema_profile_results.append(profile_result)

        lane_gate["schema_matrix_profiles"] = all(result["all_checks_passed"] for result in schema_profile_results)

        _create_binance_lane_db(binance_acceptance_db)
        binance_summary_raw = _run_command(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "query_binance_technical_lane.py"),
                "summary",
                "--db-path",
                str(binance_acceptance_db),
            ]
        )
        binance_summary = _extract_json_after_header(binance_summary_raw.stdout, "BINANCE_TECHNICAL_LANE_SUMMARY")
        lane_gate["binance_summary"] = "fresh_pnl_summary_7d" in binance_summary

        futures_long_summary = _binance_run_once_case(
            db_path=binance_futures_long_db,
            venue="binance_futures",
            symbol="BTC",
            direction="LONG",
        )
        futures_short_summary = _binance_run_once_case(
            db_path=binance_futures_short_db,
            venue="binance_futures",
            symbol="ETH",
            direction="SHORT",
        )
        spot_long_summary = _binance_run_once_case(
            db_path=binance_spot_long_db,
            venue="binance_spot",
            symbol="SOL",
            direction="LONG",
        )
        lane_gate["binance_run_once_futures_long"] = int(futures_long_summary["fresh_technical_summary"]["fresh_execute_count"]) >= 1
        lane_gate["binance_run_once_futures_short"] = int(futures_short_summary["fresh_technical_summary"]["fresh_execute_count"]) >= 1
        lane_gate["binance_run_once_spot_long"] = int(spot_long_summary["fresh_technical_summary"]["fresh_execute_count"]) >= 1
        binance_runtime_db = tmp_dir / "binance_runtime_acceptance.db"
        _create_binance_lane_db(binance_runtime_db)
        _binance_run_once_case(db_path=binance_runtime_db, venue="binance_futures", symbol="BTC", direction="LONG")
        _binance_run_once_case(db_path=binance_runtime_db, venue="binance_futures", symbol="ETH", direction="SHORT")
        _binance_run_once_case(db_path=binance_runtime_db, venue="binance_spot", symbol="SOL", direction="LONG")
        runtime_repository = BinanceTechnicalRepository(str(binance_runtime_db))
        spot_short_runtime = BinanceTechnicalRuntime(
            BinanceTechnicalSettings(
                db_path=str(binance_runtime_db),
                symbols=["SOL"],
                futures_enabled=False,
                spot_enabled=True,
            ),
            repository=runtime_repository,
            provider=_FakeMarketDataProvider({("binance_spot", "SOL"): _market_frame("SOL", "binance_spot", mark_price=150.0)}),
            signal_engine=_FakeSignalEngine({
                ("binance_spot", "SOL"): _technical_signal(
                    symbol="SOL",
                    direction="SHORT",
                    should_trade=True,
                    score=0.81,
                    threshold=0.54,
                    inputs={"score_blocker_labels": []},
                )
            }),
        )
        spot_short_runtime.run_once()
        binance_runtime_acceptance_raw = _run_command(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "query_binance_technical_lane.py"),
                "runtime-acceptance-summary",
                "--db-path",
                str(binance_runtime_db),
            ]
        )
        binance_runtime_acceptance = _extract_json_after_header(
            binance_runtime_acceptance_raw.stdout,
            "BINANCE_TECHNICAL_RUNTIME_ACCEPTANCE_SUMMARY",
        )
        lane_gate["binance_runtime_futures_long"] = bool(binance_runtime_acceptance.get("runtime_futures_long_execute"))
        lane_gate["binance_runtime_futures_short"] = bool(binance_runtime_acceptance.get("runtime_futures_short_execute"))
        lane_gate["binance_runtime_spot_long"] = bool(binance_runtime_acceptance.get("runtime_spot_long_execute"))
        lane_gate["binance_runtime_spot_short_reject"] = bool(binance_runtime_acceptance.get("runtime_spot_short_reject"))

        _run_command(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "query_binance_technical_lane.py"),
                "acceptance-run",
                "--db-path",
                str(binance_acceptance_db),
            ]
        )
        binance_acceptance_raw = _run_command(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "query_binance_technical_lane.py"),
                "acceptance-summary",
                "--db-path",
                str(binance_acceptance_db),
            ]
        )
        binance_acceptance = _extract_json_after_header(
            binance_acceptance_raw.stdout,
            "BINANCE_TECHNICAL_ACCEPTANCE_SUMMARY",
        )
        lane_gate["binance_acceptance_all_checks"] = bool(binance_acceptance.get("all_checks_passed"))
        lane_gate["binance_futures_long_execute"] = bool(binance_acceptance.get("futures_long_execute"))
        lane_gate["binance_futures_short_execute"] = bool(binance_acceptance.get("futures_short_execute"))
        lane_gate["binance_spot_long_execute"] = bool(binance_acceptance.get("spot_long_execute"))
        lane_gate["binance_spot_short_reject"] = bool(binance_acceptance.get("spot_short_reject"))

    report = {
        "all_checks_passed": all(code_gate.values()) and all(lane_gate.values()),
        "code_gate": code_gate,
        "lane_gate": lane_gate,
        "polymarket_schema_profiles_tested": schema_profile_results,
        "polymarket_copy_acceptance_summary": copy_acceptance,
        "polymarket_copy_runtime_acceptance_summary": copy_runtime_acceptance,
        "binance_technical_acceptance_summary": binance_acceptance,
        "binance_technical_runtime_acceptance_summary": binance_runtime_acceptance,
    }
    print("FINAL_LOCAL_ACCEPTANCE_SUMMARY")
    print(json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True))
    return 0 if report["all_checks_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
