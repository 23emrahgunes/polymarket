from __future__ import annotations

import sqlite3
from pathlib import Path

from apps.binance_technical.config import BinanceTechnicalSettings
from apps.binance_technical.repository import BinanceTechnicalRepository
from apps.binance_technical.service import BinanceTechnicalService
from apps.polymarket_research.config import PolymarketResearchSettings
from apps.polymarket_research.repository import PolymarketResearchRepository
from apps.polymarket_research.service import PolymarketResearchService


def _create_polymarket_research_db(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
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
    cur.execute(
        """
        CREATE TABLE whale_wallet_sources (
            address TEXT NOT NULL,
            source_type TEXT NOT NULL,
            last_seen_at TEXT,
            PRIMARY KEY (address, source_type)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            whale_address TEXT,
            status TEXT,
            pnl REAL,
            closed_at TEXT,
            timestamp TEXT,
            category TEXT
        )
        """
    )
    cur.executemany(
        """
        INSERT INTO whale_wallets (
            address, source_type, discovery_score, event_count_24h,
            last_event_amount, last_event_category, last_seen_at, enabled
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            ("0xaaa", "leaderboard", 0.92, 9, 1500.0, "CRYPTO", "2026-04-17 09:00:00", 1),
            ("0xbbb", "activity_discovery", 0.71, 4, 900.0, "CRYPTO", "2026-04-17 08:00:00", 1),
            ("0xccc", "persisted_wallets", 0.66, 5, 420.0, "SPORTS", "2026-04-17 07:00:00", 1),
            ("0xddd", "static_seed", 0.88, 7, 1100.0, "CRYPTO", "2026-04-17 06:00:00", 1),
            ("0xeee", "graph_discovery", 0.69, 6, 980.0, "CRYPTO", "2026-04-17 05:00:00", 1),
        ],
    )
    cur.executemany(
        "INSERT INTO whale_stats (address, trust_score, total_trades, total_pnl) VALUES (?, ?, ?, ?)",
        [
            ("0xaaa", 0.84, 12, 3250.0),
            ("0xbbb", 0.61, 6, 420.0),
            ("0xccc", 0.52, 5, 140.0),
            ("0xddd", 0.79, 11, 1200.0),
            ("0xeee", 0.58, 4, 260.0),
        ],
    )
    cur.executemany(
        "INSERT INTO whale_wallet_sources (address, source_type, last_seen_at) VALUES (?, ?, ?)",
        [
            ("0xaaa", "leaderboard", "2026-04-17 09:00:00"),
            ("0xaaa", "manual_confirmed", "2026-04-17 09:00:00"),
            ("0xbbb", "activity_discovery", "2026-04-17 08:00:00"),
            ("0xccc", "persisted_wallets", "2026-04-17 07:00:00"),
            ("0xddd", "static_seed", "2026-04-17 06:00:00"),
            ("0xeee", "graph_discovery", "2026-04-17 05:00:00"),
        ],
    )
    cur.executemany(
        """
        INSERT INTO trades (whale_address, status, pnl, closed_at, timestamp, category)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            ("0xaaa", "CLOSED", 1200.0, "2026-04-16 12:00:00", "2026-04-16 12:00:00", "CRYPTO"),
            ("0xaaa", "CLOSED", 900.0, "2026-04-15 12:00:00", "2026-04-15 12:00:00", "CRYPTO"),
            ("0xaaa", "CLOSED", 800.0, "2026-04-14 12:00:00", "2026-04-14 12:00:00", "CRYPTO"),
            ("0xbbb", "CLOSED", 150.0, "2026-04-16 10:00:00", "2026-04-16 10:00:00", "CRYPTO"),
            ("0xbbb", "CLOSED", 50.0, "2026-04-13 09:00:00", "2026-04-13 09:00:00", "CRYPTO"),
            ("0xbbb", "CLOSED", 25.0, "2026-04-11 08:00:00", "2026-04-11 08:00:00", "CRYPTO"),
            ("0xccc", "CLOSED", 80.0, "2026-04-16 08:00:00", "2026-04-16 08:00:00", "SPORTS"),
            ("0xccc", "CLOSED", 40.0, "2026-04-14 08:00:00", "2026-04-14 08:00:00", "SPORTS"),
            ("0xccc", "CLOSED", 20.0, "2026-04-12 08:00:00", "2026-04-12 08:00:00", "SPORTS"),
            ("0xeee", "CLOSED", 110.0, "2026-04-16 06:00:00", "2026-04-16 06:00:00", "CRYPTO"),
            ("0xeee", "CLOSED", 90.0, "2026-04-13 06:00:00", "2026-04-13 06:00:00", "CRYPTO"),
            ("0xeee", "CLOSED", 60.0, "2026-04-11 06:00:00", "2026-04-11 06:00:00", "CRYPTO"),
        ],
    )
    conn.commit()
    conn.close()


def _create_binance_lane_db(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            venue TEXT,
            instrument_type TEXT,
            market_id TEXT,
            status TEXT,
            pnl REAL,
            timestamp TEXT,
            opened_at TEXT,
            closed_at TEXT,
            strategy_profile TEXT,
            sample_kind TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE decision_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            occurred_at TEXT,
            venue TEXT,
            market_id TEXT,
            action TEXT,
            reason TEXT,
            decision_score REAL,
            threshold REAL,
            trade_size REAL,
            inputs_json TEXT,
            strategy_profile TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE venue_positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            venue TEXT,
            execution_mode TEXT,
            symbol_or_market_id TEXT,
            strategy_profile TEXT,
            sample_kind TEXT,
            status TEXT,
            source_signal TEXT,
            signal_family TEXT,
            notional_usd REAL,
            unrealized_pnl REAL,
            opened_at TEXT
        )
        """
    )
    cur.executemany(
        """
        INSERT INTO trades (
            venue, instrument_type, market_id, status, pnl, timestamp, opened_at, closed_at,
            strategy_profile, sample_kind
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                "binance_futures",
                "futures",
                "BTC/USDT:USDT",
                "CLOSED_WIN",
                45.5,
                "2026-04-16 12:00:00",
                "2026-04-16 10:00:00",
                "2026-04-16 12:00:00",
                "binance_technical_sampling",
                "live_paper",
            ),
            (
                "binance_spot",
                "spot",
                "ETH/USDT",
                "CLOSED_LOSS",
                -10.0,
                "2026-04-15 12:00:00",
                "2026-04-15 09:00:00",
                "2026-04-15 12:00:00",
                "binance_technical_sampling",
                "live_paper",
            ),
            (
                "binance_futures",
                "futures",
                "SOL/USDT:USDT",
                "CLOSED_WIN",
                99.0,
                "2026-04-01 12:00:00",
                "2026-04-01 09:00:00",
                "2026-04-01 12:00:00",
                "binance_technical_sampling",
                "live_paper",
            ),
        ],
    )
    cur.executemany(
        """
        INSERT INTO decision_audit (
            occurred_at, venue, market_id, action, reason, decision_score, threshold, trade_size, inputs_json, strategy_profile
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                "2026-04-16 12:00:00",
                "binance_futures",
                "BTC/USDT:USDT",
                "execute",
                "",
                0.72,
                0.54,
                100.0,
                "{}",
                "binance_technical_sampling",
            ),
            (
                "2026-04-16 11:00:00",
                "binance_spot",
                "ETH/USDT",
                "reject",
                "score_below_threshold,technical_alignment_weak",
                0.41,
                0.54,
                100.0,
                "{}",
                "binance_technical_sampling",
            ),
        ],
    )
    cur.executemany(
        """
        INSERT INTO venue_positions (
            venue, execution_mode, symbol_or_market_id, strategy_profile, sample_kind, status,
            source_signal, signal_family, notional_usd, unrealized_pnl, opened_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                "binance_futures",
                "paper",
                "BTC/USDT:USDT",
                "binance_technical_sampling",
                "live_paper",
                "OPEN",
                "binance_technical_momentum",
                "binance_technical_momentum",
                100.0,
                12.5,
                "2026-04-16 09:00:00",
            ),
            (
                "binance_spot",
                "paper",
                "ETH/USDT",
                "baseline",
                "",
                "OPEN",
                "legacy_signal",
                "legacy_signal",
                80.0,
                -1.0,
                "2026-04-15 09:00:00",
            ),
        ],
    )
    conn.commit()
    conn.close()


def test_polymarket_research_service_builds_shadow_funnel(tmp_path: Path) -> None:
    db_path = tmp_path / "polymarket_research.db"
    _create_polymarket_research_db(db_path)

    repository = PolymarketResearchRepository(str(db_path))
    repository.ensure_tables()
    repository.seed_shadow_action(
        wallet_address="0xaaa",
        market_id="market-1",
        category="CRYPTO",
        source_type="leaderboard",
        shadow_pnl=120.0,
        shadow_edge=90.0,
        drawdown_pct=-4.0,
    )
    repository.seed_shadow_action(
        wallet_address="0xaaa",
        market_id="market-2",
        category="CRYPTO",
        source_type="leaderboard",
        shadow_pnl=60.0,
        shadow_edge=40.0,
        drawdown_pct=-3.0,
    )
    repository.seed_shadow_action(
        wallet_address="0xaaa",
        market_id="market-3",
        category="CRYPTO",
        source_type="leaderboard",
        shadow_pnl=30.0,
        shadow_edge=15.0,
        drawdown_pct=-2.0,
    )
    repository.seed_shadow_action(
        wallet_address="0xbbb",
        market_id="market-4",
        category="CRYPTO",
        source_type="activity_discovery",
        shadow_pnl=-15.0,
        shadow_edge=-10.0,
        drawdown_pct=-8.0,
    )
    repository.seed_shadow_action(
        wallet_address="0xeee",
        market_id="market-5",
        category="CRYPTO",
        source_type="graph_discovery",
        shadow_pnl=10.0,
        shadow_edge=4.0,
        drawdown_pct=-5.0,
    )

    service = PolymarketResearchService(
        PolymarketResearchSettings(
            db_path=str(db_path),
            discovery_pool_size=50,
            shadow_pool_size=20,
            copy_ready_size=5,
            shadow_window_days=14,
        ),
        repository,
    )

    summary = service.build_summary()
    persisted_rows = repository.fetch_persisted_wallet_snapshots()
    persisted_by_address = {str(row["address"]): row for row in persisted_rows}
    discovery_sources = {
        row["bucket"]: row["actual"] for row in summary["discovery_source_summary"]["rows"]
    }
    shadow_blockers = {
        row["reason"]: row["count"] for row in summary["shadow_promotion_summary"]["blocker_counts"]
    }

    assert summary["discovery_wallet_summary"]["tracked_wallets"] == 5
    assert summary["discovery_wallet_summary"]["crypto_specialists"] == 4
    assert summary["discovery_wallet_summary"]["persisted_wallet_snapshots"] == 5
    assert summary["discovery_wallet_summary"]["promoted_to_shadow"] == 3
    assert summary["discovery_source_summary"]["selected_wallets"] == 5
    assert summary["discovery_source_summary"]["static_seed_used"] == 1
    assert discovery_sources["leaderboard"] == 1
    assert discovery_sources["activity_discovery"] == 1
    assert discovery_sources["graph_discovery"] == 1
    assert discovery_sources["manual_persisted"] == 1
    assert summary["shadow_promotion_summary"]["eligible_wallets"] == 3
    assert summary["shadow_promotion_summary"]["promoted_wallets"] == 3
    assert summary["shadow_promotion_summary"]["blocked_wallets"] == 2
    assert shadow_blockers["non_crypto_specialist"] == 1
    assert shadow_blockers["seed_only_excluded"] == 1
    assert summary["wallet_provenance_summary"]["multi_source_wallets"] == 1
    assert summary["wallet_provenance_summary"]["single_source_wallets"] == 4
    assert summary["wallet_provenance_summary"]["seed_only_wallets"] == 1
    assert summary["shadow_wallet_summary"]["wallets_with_shadow_actions"] == 3
    assert summary["copy_ready_wallet_summary"]["copy_ready_wallets"] == 1
    assert summary["copy_ready_wallets"][0]["address"] == "0xaaa"
    assert summary["shadow_edge_summary"]["shadow_ready"] is True
    assert summary["recent_shadow_actions"][0]["wallet_address"] in {"0xaaa", "0xbbb", "0xeee"}
    assert summary["wallet_consistency_table"][0]["address"] == "0xaaa"
    assert summary["wallet_consistency_table"][0]["primary_source"] == "leaderboard"
    assert "manual_confirmed" in summary["wallet_consistency_table"][0]["source_labels"]
    assert len(persisted_rows) == 5
    assert persisted_by_address["0xaaa"]["cohort"] == "copy_ready"
    assert persisted_by_address["0xaaa"]["copy_ready_eligible"] == 1
    assert persisted_by_address["0xaaa"]["shadow_eligible"] == 1
    assert persisted_by_address["0xaaa"]["copy_ready_rank"] == 1
    assert persisted_by_address["0xaaa"]["profit_consistency_score"] > 0
    assert persisted_by_address["0xaaa"]["recency_score"] > 0
    assert persisted_by_address["0xaaa"]["frequency_score"] > 0
    assert persisted_by_address["0xbbb"]["cohort"] == "shadow"
    assert persisted_by_address["0xbbb"]["shadow_gate_reason"] == "eligible"
    assert persisted_by_address["0xccc"]["cohort"] == "discovery"
    assert persisted_by_address["0xccc"]["shadow_gate_reason"] == "non_crypto_specialist"
    assert persisted_by_address["0xddd"]["cohort"] == "discovery"
    assert persisted_by_address["0xddd"]["shadow_gate_reason"] == "seed_only_excluded"
    assert persisted_by_address["0xeee"]["cohort"] == "shadow"


def test_binance_technical_service_builds_fresh_and_legacy_summaries(tmp_path: Path) -> None:
    db_path = tmp_path / "binance_lane.db"
    _create_binance_lane_db(db_path)

    repository = BinanceTechnicalRepository(str(db_path))
    service = BinanceTechnicalService(
        BinanceTechnicalSettings(db_path=str(db_path), fresh_window_days=7, symbols=["BTC", "ETH", "SOL"]),
        repository,
    )

    summary = service.build_summary()

    assert summary["fresh_technical_summary"]["fresh_window_days"] == 7
    assert summary["fresh_technical_summary"]["fresh_trade_count"] == 2
    assert summary["fresh_technical_summary"]["fresh_closed_trades"] == 2
    assert summary["fresh_technical_summary"]["fresh_execute_count"] == 1
    assert summary["fresh_pnl_summary_7d"]["fresh_window_days"] == 7
    assert summary["fresh_pnl_summary_7d"]["net_pnl"] == 35.5
    assert summary["fresh_pnl_summary_7d"]["win_rate"] == 50.0
    assert summary["technical_score_summary"]["decision_rows"] == 2
    assert summary["technical_reject_breakdown"][0]["reason"] == "score_below_threshold"
    assert summary["position_pressure_summary"]["open_positions"] == 2
    assert summary["position_pressure_summary"]["fresh_open_positions"] == 1
    assert summary["position_pressure_summary"]["legacy_open_positions"] == 1
    assert summary["legacy_position_summary"]["legacy_open_positions"] == 1
    assert summary["legacy_position_summary"]["strict_fresh_positions"] == 1
    assert "ETH/USDT" in summary["legacy_position_summary"]["legacy_symbols"]
