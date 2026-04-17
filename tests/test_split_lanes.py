from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from apps.binance_technical.config import BinanceTechnicalSettings
from apps.binance_technical.repository import BinanceTechnicalRepository
from apps.binance_technical.service import BinanceTechnicalService
from apps.polymarket_research.cli import main as polymarket_research_cli_main
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
        INSERT INTO trades (
            whale_address, venue, market_id, status, pnl, size, closed_at, timestamp, category, source_signal, side
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            ("0xaaa", "polymarket", "Bitcoin Up or Down - Apr 16", "CLOSED", 1200.0, 500.0, "2026-04-16 12:00:00", "2026-04-16 12:00:00", "CRYPTO", "manual_replay", "BUY"),
            ("0xaaa", "polymarket", "Ethereum Up or Down - Apr 15", "CLOSED", 900.0, 350.0, "2026-04-15 12:00:00", "2026-04-15 12:00:00", "CRYPTO", "manual_replay", "BUY"),
            ("0xaaa", "polymarket", "Bitcoin Up or Down - Apr 14", "CLOSED", 800.0, 320.0, "2026-04-14 12:00:00", "2026-04-14 12:00:00", "CRYPTO", "manual_replay", "BUY"),
            ("0xbbb", "polymarket", "Bitcoin Up or Down - Apr 16", "CLOSED", 150.0, 120.0, "2026-04-16 10:00:00", "2026-04-16 10:00:00", "CRYPTO", "manual_replay", "BUY"),
            ("0xbbb", "polymarket", "Ethereum Up or Down - Apr 13", "CLOSED", 50.0, 80.0, "2026-04-13 09:00:00", "2026-04-13 09:00:00", "CRYPTO", "manual_replay", "BUY"),
            ("0xbbb", "polymarket", "Bitcoin Up or Down - Apr 11", "CLOSED", 25.0, 60.0, "2026-04-11 08:00:00", "2026-04-11 08:00:00", "CRYPTO", "manual_replay", "BUY"),
            ("0xccc", "polymarket", "Sports Market - Apr 16", "CLOSED", 80.0, 75.0, "2026-04-16 08:00:00", "2026-04-16 08:00:00", "SPORTS", "manual_replay", "BUY"),
            ("0xccc", "polymarket", "Sports Market - Apr 14", "CLOSED", 40.0, 55.0, "2026-04-14 08:00:00", "2026-04-14 08:00:00", "SPORTS", "manual_replay", "BUY"),
            ("0xccc", "polymarket", "Sports Market - Apr 12", "CLOSED", 20.0, 45.0, "2026-04-12 08:00:00", "2026-04-12 08:00:00", "SPORTS", "manual_replay", "BUY"),
            ("0xeee", "polymarket", "Bitcoin Up or Down - Apr 16", "CLOSED", 110.0, 95.0, "2026-04-16 06:00:00", "2026-04-16 06:00:00", "CRYPTO", "manual_replay", "BUY"),
            ("0xeee", "polymarket", "Ethereum Up or Down - Apr 13", "CLOSED", 90.0, 85.0, "2026-04-13 06:00:00", "2026-04-13 06:00:00", "CRYPTO", "manual_replay", "BUY"),
            ("0xeee", "polymarket", "Bitcoin Up or Down - Apr 11", "CLOSED", 60.0, 65.0, "2026-04-11 06:00:00", "2026-04-11 06:00:00", "CRYPTO", "manual_replay", "BUY"),
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
    with repository.connect() as connection:
        connection.execute(
            """
            INSERT INTO polymarket_research_watchlist (
                display_name, profile_ref, wallet_address, priority_rank, priority_mode,
                target_specialization, status, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "crypto-pro",
                "https://polymarket.com/tr/@crypto-pro",
                "0xaaa",
                2,
                "fast_track_shadow",
                "CRYPTO",
                "linked",
                "Linked priority specialist",
            ),
        )
        connection.commit()
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

    assert summary["priority_watchlist_summary"]["total_watchlist_rows"] == 2
    assert summary["priority_watchlist_summary"]["linked_rows"] == 1
    assert summary["priority_watchlist_summary"]["pending_resolution_rows"] == 1
    assert summary["priority_watchlist_summary"]["promoted_priority_wallets"] == 1
    assert summary["identity_resolution_summary"]["pending_handle_only_entries"] == 1
    assert summary["identity_resolution_summary"]["linked_entries"] == 1
    assert summary["identity_resolution_summary"]["unresolved_but_ranked_entries"] == 1
    assert summary["discovery_wallet_summary"]["tracked_wallets"] == 5
    assert summary["discovery_wallet_summary"]["crypto_specialists"] == 4
    assert summary["discovery_wallet_summary"]["persisted_wallet_snapshots"] == 5
    assert summary["discovery_wallet_summary"]["promoted_to_shadow"] == 3
    assert summary["discovery_source_summary"]["selected_wallets"] == 5
    assert summary["discovery_source_summary"]["static_seed_used"] == 1
    assert discovery_sources["leaderboard"] == 0
    assert discovery_sources["activity_discovery"] == 1
    assert discovery_sources["graph_discovery"] == 1
    assert discovery_sources["manual_persisted"] == 2
    assert summary["shadow_promotion_summary"]["eligible_wallets"] == 3
    assert summary["shadow_promotion_summary"]["promoted_wallets"] == 3
    assert summary["shadow_promotion_summary"]["blocked_wallets"] == 2
    assert shadow_blockers["non_crypto_specialist"] == 1
    assert shadow_blockers["seed_only_excluded"] == 1
    assert summary["wallet_provenance_summary"]["multi_source_wallets"] == 1
    assert summary["wallet_provenance_summary"]["single_source_wallets"] == 4
    assert summary["wallet_provenance_summary"]["seed_only_wallets"] == 1
    assert summary["shadow_wallet_summary"]["wallets_with_shadow_actions"] == 3
    assert summary["copy_ready_wallet_summary"]["copy_ready_wallets"] == 2
    assert summary["copy_ready_wallets"][0]["address"] == "0xaaa"
    assert summary["shadow_edge_summary"]["shadow_ready"] is True
    assert summary["shadow_replay_summary"]["replayed_actions_created"] == 9
    assert summary["shadow_replay_summary"]["wallets_with_replay_history"] == 3
    assert summary["shadow_replay_summary"]["net_shadow_pnl"] == 3381.65
    assert summary["shadow_replay_summary"]["net_shadow_edge"] == 12.8849
    assert summary["shadow_replay_summary"]["eligible_without_trade_history"] == 0
    assert summary["priority_watchlist_rows"][0]["display_name"] == "ohanism"
    assert summary["priority_watchlist_rows"][0]["identity_resolution_status"] == "pending_resolution"
    assert summary["priority_watchlist_rows"][1]["wallet_address"] == "0xaaa"
    assert summary["priority_watchlist_rows"][1]["promoted_to_shadow"] is True
    assert summary["recent_shadow_actions"][0]["wallet_address"] in {"0xaaa", "0xbbb", "0xeee"}
    assert summary["wallet_consistency_table"][0]["address"] == "0xaaa"
    assert summary["wallet_consistency_table"][0]["primary_source"] == "manual_persisted"
    assert "manual_confirmed" in summary["wallet_consistency_table"][0]["source_labels"]
    assert summary["wallet_consistency_table"][0]["watchlist_priority_rank"] == 2
    assert summary["wallet_consistency_table"][0]["watchlist_status"] == "linked"
    assert summary["wallet_consistency_table"][0]["identity_resolution_status"] == "linked"
    assert len(persisted_rows) == 5
    assert persisted_by_address["0xaaa"]["cohort"] == "copy_ready"
    assert persisted_by_address["0xaaa"]["copy_ready_eligible"] == 1
    assert persisted_by_address["0xaaa"]["shadow_eligible"] == 1
    assert persisted_by_address["0xaaa"]["copy_ready_rank"] == 1
    assert persisted_by_address["0xaaa"]["profit_consistency_score"] > 0
    assert persisted_by_address["0xaaa"]["recency_score"] > 0
    assert persisted_by_address["0xaaa"]["frequency_score"] > 0
    assert persisted_by_address["0xaaa"]["watchlist_priority_rank"] == 2
    assert persisted_by_address["0xaaa"]["watchlist_mode"] == "fast_track_shadow"
    assert persisted_by_address["0xaaa"]["identity_resolution_status"] == "linked"
    assert persisted_by_address["0xaaa"]["priority_pinned"] == 1
    assert persisted_by_address["0xbbb"]["cohort"] == "shadow"
    assert persisted_by_address["0xbbb"]["shadow_gate_reason"] == "eligible"
    assert persisted_by_address["0xccc"]["cohort"] == "discovery"
    assert persisted_by_address["0xccc"]["shadow_gate_reason"] == "non_crypto_specialist"
    assert persisted_by_address["0xddd"]["cohort"] == "discovery"
    assert persisted_by_address["0xddd"]["shadow_gate_reason"] == "seed_only_excluded"
    assert persisted_by_address["0xeee"]["cohort"] == "copy_ready"


def test_polymarket_watchlist_manual_linking_guards(tmp_path: Path) -> None:
    db_path = tmp_path / "polymarket_research.db"
    _create_polymarket_research_db(db_path)
    repository = PolymarketResearchRepository(str(db_path))
    repository.ensure_tables()

    seeded_row = repository.fetch_watchlist_row(1)
    assert seeded_row is not None
    assert seeded_row["display_name"] == "ohanism"
    assert seeded_row["status"] == "pending_resolution"
    assert seeded_row["wallet_address"] is None

    linked_address = "0x1111111111111111111111111111111111111111"
    linked_row = repository.link_watchlist_wallet(
        row_id=1,
        wallet_address=linked_address.upper().replace("X", "x"),
        notes="verified manually",
    )
    assert linked_row["wallet_address"] == linked_address
    assert linked_row["status"] == "linked"
    assert linked_row["notes"] == "verified manually"

    relinked_row = repository.link_watchlist_wallet(row_id=1, wallet_address=linked_address)
    assert relinked_row["wallet_address"] == linked_address

    added_row = repository.add_watchlist_row(
        display_name="second-specialist",
        profile_ref="https://polymarket.com/tr/@second-specialist",
        priority_rank=2,
        priority_mode="fast_track_shadow",
        notes="operator candidate",
    )
    assert added_row["status"] == "pending_resolution"
    assert added_row["wallet_address"] is None

    with pytest.raises(ValueError, match="0x-prefixed"):
        repository.link_watchlist_wallet(row_id=added_row["id"], wallet_address="not-a-wallet")

    with pytest.raises(ValueError, match="already linked"):
        repository.link_watchlist_wallet(row_id=added_row["id"], wallet_address=linked_address)


def test_polymarket_research_cli_watchlist_commands(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_path = tmp_path / "polymarket_research.db"
    _create_polymarket_research_db(db_path)

    assert polymarket_research_cli_main(["watchlist-list", "--db-path", str(db_path)]) == 0
    list_output = capsys.readouterr().out
    assert "WATCHLIST_ROWS" in list_output
    assert "ohanism" in list_output
    assert "pending_resolution" in list_output

    linked_address = "0x2222222222222222222222222222222222222222"
    assert (
        polymarket_research_cli_main(
            [
                "watchlist-link",
                "--db-path",
                str(db_path),
                "--id",
                "1",
                "--wallet-address",
                linked_address,
                "--notes",
                "verified manually",
            ]
        )
        == 0
    )
    link_output = capsys.readouterr().out
    assert "WATCHLIST_ROW_LINKED" in link_output
    assert linked_address in link_output

    repository = PolymarketResearchRepository(str(db_path))
    linked_row = repository.fetch_watchlist_row(1)
    assert linked_row is not None
    assert linked_row["wallet_address"] == linked_address
    assert linked_row["status"] == "linked"

    assert (
        polymarket_research_cli_main(
            [
                "watchlist-add",
                "--db-path",
                str(db_path),
                "--display-name",
                "new-specialist",
                "--profile-ref",
                "https://polymarket.com/tr/@new-specialist",
                "--priority-rank",
                "3",
            ]
        )
        == 0
    )
    add_output = capsys.readouterr().out
    assert "WATCHLIST_ROW_ADDED" in add_output
    assert "new-specialist" in add_output

    assert (
        polymarket_research_cli_main(
            [
                "watchlist-link",
                "--db-path",
                str(db_path),
                "--id",
                "1",
                "--wallet-address",
                "bad-address",
            ]
        )
        == 2
    )
    invalid_result = capsys.readouterr()
    assert "0x-prefixed" in invalid_result.err


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
