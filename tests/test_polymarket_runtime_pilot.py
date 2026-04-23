from __future__ import annotations

import sqlite3
from pathlib import Path

from apps.polymarket_copy.config import PolymarketCopySettings
from apps.polymarket_copy.runtime import PolymarketCopyRuntime
from apps.polymarket_research.config import PolymarketResearchSettings
from apps.polymarket_research.runtime_pilot import ensure_runtime_priority_wallet
from apps.polymarket_research.service import PolymarketResearchService
from apps.polymarket_research.repository import PolymarketResearchRepository
from tests.test_split_lanes import _create_polymarket_research_db


def _create_source_profile_db(db_path: Path, profile: str) -> None:
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
        raise AssertionError(f"Unknown profile: {profile}")
    conn.commit()
    conn.close()


def test_runtime_pilot_helper_supports_schema_profiles(tmp_path: Path) -> None:
    wallet_address = "0x89b5cdaaa4866c1e738406712012a630b4078beb"
    profiles = ["minimal_market_id", "symbol_only", "extended_with_stats"]

    for profile in profiles:
        db_path = tmp_path / f"{profile}_research.db"
        source_db_path = tmp_path / f"{profile}_source.db"
        _create_polymarket_research_db(db_path)
        _create_source_profile_db(source_db_path, profile)

        result = ensure_runtime_priority_wallet(
            db_path=str(db_path),
            source_db_path=str(source_db_path),
            display_name="ohanism",
            profile_ref="https://polymarket.com/tr/@ohanism",
            priority_rank=1,
            priority_mode="fast_track_shadow",
            target_specialization="CRYPTO",
            wallet_address=wallet_address,
            link_notes="verified manually",
            approval_notes="schema profile approval",
            seed_runtime_source_trade=True,
        )
        assert result["operator_approved_pilot"] is True

        # Idempotency
        ensure_runtime_priority_wallet(
            db_path=str(db_path),
            source_db_path=str(source_db_path),
            display_name="ohanism",
            profile_ref="https://polymarket.com/tr/@ohanism",
            priority_rank=1,
            priority_mode="fast_track_shadow",
            target_specialization="CRYPTO",
            wallet_address=wallet_address,
            link_notes="verified manually",
            approval_notes="schema profile approval",
            seed_runtime_source_trade=True,
        )

        with sqlite3.connect(source_db_path) as conn:
            closed_count = conn.execute(
                "SELECT COUNT(*) FROM trades WHERE source_signal = 'manual_source_replay' AND status = 'CLOSED'"
            ).fetchone()[0]
            open_count = conn.execute(
                "SELECT COUNT(*) FROM trades WHERE source_signal = 'runtime_pilot_seed' AND status = 'OPEN'"
            ).fetchone()[0]
        assert closed_count == 5
        assert open_count == 1

        repository = PolymarketResearchRepository(str(db_path), str(source_db_path))
        repository.ensure_tables()
        summary = PolymarketResearchService(
            PolymarketResearchSettings(
                db_path=str(db_path),
                source_db_path=str(source_db_path),
                discovery_pool_size=50,
                shadow_pool_size=20,
                copy_ready_size=5,
                shadow_window_days=14,
            ),
            repository,
        ).build_summary()
        ohanism_row = next(row for row in summary["priority_watchlist_rows"] if row["display_name"] == "ohanism")
        assert ohanism_row["historical_trade_evidence_status"] == "detailed_trade_history"
        assert ohanism_row["pilot_copy_gate_reason"] in {"eligible", "shadow_proven"}
        assert ohanism_row["pilot_copy_gate_status"] in {"promoted", "shadow_proven"}

        copy_summary = PolymarketCopyRuntime(
            PolymarketCopySettings(
                db_path=str(db_path),
                source_db_path=str(source_db_path),
                lookback_days=14,
                follower_delay_seconds=0,
                min_trade_size_usd=25.0,
                max_trade_size_usd=50.0,
                wallet_risk_limit_usd=150.0,
                market_risk_limit_usd=150.0,
                copy_ready_limit=5,
            )
        ).run_once()

        runtime_acceptance = copy_summary["copy_runtime_acceptance_summary"]
        assert copy_summary["copy_execution_summary"]["eligible_copy_wallets_total"] >= 1
        assert runtime_acceptance["runtime_open_action_observed"] is True
        assert runtime_acceptance["runtime_open_position_observed"] is True
