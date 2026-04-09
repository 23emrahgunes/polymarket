from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.analyze_performance import analyze_performance
from scripts.run_backtest import run_backtest
from scripts.swot_report import build_swot_report
from src.database import Database
from src.evaluation_utils import STRATEGY_PROFILE_SAMPLING_RELAXED


@pytest.mark.asyncio
async def test_evaluation_attribution_persists_to_db(tmp_path):
    db_path = tmp_path / "evaluation.db"
    db = Database(str(db_path))
    await db.connect()

    trade_id = await db.add_trade(
        "BTC/USDT",
        "LONG",
        100.0,
        100_000.0,
        0.05,
        0.82,
        venue="binance_futures",
        instrument_type="futures",
        source_signal="binance_futures_price_structure",
        category="CRYPTO",
        sample_kind="live_paper",
        debug_profile=None,
        entry_spread_pct=0.001,
        slippage_proxy_bps=5.0,
        whale_trust_at_entry=0.55,
    )
    position_id = await db.create_venue_position(
        venue="binance_futures",
        execution_mode="paper",
        instrument_type="futures",
        symbol_or_market_id="BTC/USDT:USDT",
        side="LONG",
        qty_or_shares=0.001,
        entry_price=100_000.0,
        notional_usd=100.0,
        source_signal="binance_futures_price_structure",
        category="CRYPTO",
        sample_kind="live_paper",
        entry_spread_pct=0.001,
        slippage_proxy_bps=5.0,
        whale_trust_at_entry=0.55,
    )
    await db.add_decision_audit(
        venue="binance_futures",
        market_id="BTC/USDT:USDT",
        category="CRYPTO",
        raw_source_signal="binance_futures_price_structure",
        sample_kind="live_paper",
        decision_score=0.82,
        threshold=0.70,
        trade_size=100.0,
        action="decision",
        confidence=0.82,
        whale_trust=0.55,
        spread_pct=0.001,
        slippage_proxy_bps=5.0,
        inputs_json='{"direction":"LONG"}',
    )

    trade = await db.get_trade(trade_id)
    position = await db.get_position(position_id)
    async with db.conn.execute("SELECT * FROM decision_audit LIMIT 1") as cursor:
        audit = await cursor.fetchone()

    assert trade["category"] == "CRYPTO"
    assert trade["signal_family"] == "discovery"
    assert trade["sample_kind"] == "live_paper"
    assert trade["strategy_profile"] == "baseline"
    assert trade["entry_spread_pct"] == 0.001
    assert trade["slippage_proxy_bps"] == 5.0
    assert trade["whale_trust_at_entry"] == 0.55

    assert position["category"] == "CRYPTO"
    assert position["signal_family"] == "discovery"
    assert position["sample_kind"] == "live_paper"
    assert position["strategy_profile"] == "baseline"
    assert position["slippage_proxy_bps"] == 5.0

    assert audit["signal_family"] == "discovery"
    assert audit["sample_kind"] == "live_paper"
    assert audit["strategy_profile"] == "baseline"
    assert audit["action"] == "decision"
    await db.close()


@pytest.mark.asyncio
async def test_analyze_performance_excludes_synthetic_by_default(tmp_path):
    db_path = tmp_path / "performance.db"
    db = Database(str(db_path))
    await db.connect()

    live_trade_id = await db.add_trade(
        "LIVE-1",
        "YES",
        40.0,
        0.60,
        0.03,
        0.74,
        source_signal="activity",
        category="SPORTS",
        sample_kind="live_paper",
        whale_trust_at_entry=0.6,
    )
    await db.update_trade_resolution(live_trade_id, "CLOSED_WIN", 12.0)

    await db.add_trade(
        "VERIFY-1",
        "YES",
        40.0,
        0.58,
        0.00,
        0.73,
        source_signal="activity",
        category="SPORTS",
        sample_kind="synthetic_verify",
        debug_profile="sports",
        status="OPEN",
    )
    await db.add_decision_audit(
        venue="polymarket",
        market_id="LIVE-1",
        category="SPORTS",
        raw_source_signal="activity",
        sample_kind="live_paper",
        action="reject",
        reason="cluster_threshold_not_reached",
    )
    await db.close()

    summary = analyze_performance(db_paths=[str(db_path)], output_dir=str(tmp_path / "reports"))

    assert summary["core"]["total_trades"] == 1
    assert summary["core"]["closed_trades"] == 1
    assert summary["synthetic_appendix"]["trade_count"] == 1
    assert summary["rejection_counts_by_reason"][0]["reason"] == "cluster_threshold_not_reached"


@pytest.mark.asyncio
async def test_analyze_performance_separates_sampling_profile_from_baseline(tmp_path):
    db_path = tmp_path / "sampling_performance.db"
    db = Database(str(db_path))
    await db.connect()

    baseline_trade_id = await db.add_trade(
        "BASELINE-1",
        "YES",
        40.0,
        0.60,
        0.03,
        0.74,
        source_signal="activity",
        category="SPORTS",
        sample_kind="live_paper",
    )
    await db.update_trade_resolution(baseline_trade_id, "CLOSED_WIN", 12.0)

    sampling_trade_id = await db.add_trade(
        "SAMPLE-1",
        "YES",
        35.0,
        0.54,
        0.01,
        0.69,
        source_signal="whale_tracker",
        category="POLITICS",
        sample_kind="live_paper",
        strategy_profile=STRATEGY_PROFILE_SAMPLING_RELAXED,
    )
    await db.update_trade_resolution(sampling_trade_id, "CLOSED_LOSS", -4.0)
    await db.close()

    summary = analyze_performance(db_paths=[str(db_path)], output_dir=str(tmp_path / "reports"))

    assert summary["core"]["closed_trades"] == 1
    assert summary["core"]["realized_pnl"] == 12.0
    assert summary["sampling_summary"]["strategy_profile"] == STRATEGY_PROFILE_SAMPLING_RELAXED
    assert summary["sampling_summary"]["core_metrics"]["closed_trades"] == 1
    assert summary["sampling_summary"]["core_metrics"]["realized_pnl"] == -4.0
    profiles = {row["strategy_profile"]: row for row in summary["strategy_profile_comparison"]}
    assert profiles["baseline"]["closed_trades"] == 1
    assert profiles[STRATEGY_PROFILE_SAMPLING_RELAXED]["closed_trades"] == 1


def test_run_backtest_reports_insufficient_evidence_without_dataset(tmp_path):
    report = run_backtest(output_dir=str(tmp_path))
    assert report["status"] == "insufficient_evidence"
    assert report["sensitivity"]["status"] == "insufficient_evidence"


def test_run_backtest_is_deterministic_with_structured_dataset(tmp_path):
    dataset_path = tmp_path / "replay.csv"
    dataset_path.write_text(
        "\n".join(
            [
                "timestamp,category,venue,signal_score,direction,entry_price,exit_price,trade_size,spread_pct",
                "2026-01-01T00:00:00Z,CRYPTO,binance_futures,0.82,LONG,100,103,50,0.001",
                "2026-01-02T00:00:00Z,CRYPTO,binance_futures,0.80,SHORT,102,100,50,0.001",
                "2026-01-03T00:00:00Z,CRYPTO,binance_spot,0.79,LONG,50,52,100,0.001",
                "2026-01-04T00:00:00Z,CRYPTO,binance_spot,0.81,LONG,60,57,100,0.001",
                "2026-01-05T00:00:00Z,CRYPTO,binance_futures,0.77,LONG,110,111,50,0.002",
                "2026-01-06T00:00:00Z,CRYPTO,binance_spot,0.76,LONG,70,73,100,0.002",
                "2026-01-07T00:00:00Z,CRYPTO,binance_futures,0.84,SHORT,115,109,50,0.001",
                "2026-01-08T00:00:00Z,CRYPTO,binance_spot,0.83,LONG,80,79,100,0.001",
            ]
        ),
        encoding="utf-8",
    )

    first = run_backtest(dataset_path=str(dataset_path), output_dir=str(tmp_path / "first"), seed=42)
    second = run_backtest(dataset_path=str(dataset_path), output_dir=str(tmp_path / "second"), seed=42)

    assert first["status"] == "ok"
    assert first["train"] == second["train"]
    assert first["validation"] == second["validation"]
    assert first["test"] == second["test"]
    assert first["sensitivity"]["status"] == "ok"


def test_swot_report_defaults_to_improve_first_on_insufficient_evidence(tmp_path):
    analysis_path = tmp_path / "summary.json"
    backtest_path = tmp_path / "backtest.json"
    analysis_path.write_text(
        json.dumps(
            {
                "db_paths": ["data/ghost_trader.db"],
                "what_was_measurable": ["paper trade counts"],
                "what_was_not_measurable": ["live paper alpha evidence"],
                "core": {"expectancy": None, "profit_factor": None, "max_drawdown": 0.0},
                "venue_comparison": [],
                "category_comparison": [],
                "signal_source_comparison": [],
                "evidence": {"live_paper_closed": 0},
            }
        ),
        encoding="utf-8",
    )
    backtest_path.write_text(
        json.dumps(
            {
                "status": "insufficient_evidence",
                "sensitivity": {"status": "insufficient_evidence", "parameter_effects": []},
            }
        ),
        encoding="utf-8",
    )

    report = build_swot_report(
        analysis_json_path=str(analysis_path),
        backtest_json_path=str(backtest_path),
        output_dir=str(tmp_path / "reports"),
    )

    assert report["final_verdict"]["verdict"] == "IMPROVE FIRST"
