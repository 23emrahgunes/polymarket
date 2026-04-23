from __future__ import annotations

import base64
import json
import os
from datetime import datetime, timedelta, timezone
import shutil
import signal
import socket
import sqlite3
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from apps.binance_technical.config import BinanceTechnicalSettings
from apps.binance_technical.runtime import BinanceTechnicalRuntime
from apps.polymarket_copy.config import PolymarketCopySettings
from apps.polymarket_copy.runtime import PolymarketCopyRuntime

REPO_ROOT = Path(__file__).resolve().parents[1]
PHP_BIN = shutil.which('php')
DASHBOARD_HASH = '$2y$10$ycVVdHE7aM4FCpXKhwIg2.lP64iQndfqYEI2uvcj7FQ.gXo9umPzy'
DASHBOARD_PASSWORD = 'change-me-now'
DASHBOARD_USER = 'tester'


def _create_dashboard_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    cur = conn.cursor()
    cur.execute('CREATE TABLE wallet (id INTEGER PRIMARY KEY, balance REAL NOT NULL)')
    cur.execute('INSERT INTO wallet (id, balance) VALUES (1, 987.5)')
    cur.execute(
        'CREATE TABLE venue_accounts (venue TEXT, execution_mode TEXT, cash_balance REAL, equity REAL, available_balance REAL, updated_at TEXT)'
    )
    cur.executemany(
        'INSERT INTO venue_accounts VALUES (?, ?, ?, ?, ?, ?)',
        [
            ('polymarket', 'paper', 960.0, 960.0, 960.0, '2026-04-09 10:00:00'),
            ('binance_futures', 'paper', 950.0, 955.0, 950.0, '2026-04-09 10:00:00'),
        ],
    )
    cur.execute(
        'CREATE TABLE trades (id INTEGER PRIMARY KEY, venue TEXT, instrument_type TEXT, market_id TEXT, side TEXT, size REAL, price REAL, confidence REAL, source_signal TEXT, category TEXT, strategy_profile TEXT, sample_kind TEXT, is_synthetic INTEGER, status TEXT, pnl REAL, whale_address TEXT, timestamp TEXT)'
    )
    cur.execute(
        'INSERT INTO trades VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        ('polymarket', 'prediction', 'market-1', 'YES', 40.0, 0.58, 0.73, 'activity', 'SPORTS', 'baseline', 'live_paper', 0, 'OPEN', 0.0, '0xabc', '2026-04-09 10:00:00'),
    )
    cur.execute(
        'INSERT INTO trades VALUES (2, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        ('polymarket', 'prediction', 'market-2', 'YES', 35.0, 0.54, 0.69, 'whale_tracker', 'POLITICS', 'sampling_relaxed', 'live_paper', 0, 'CLOSED_WIN', 9.5, '0xdef', '2026-04-09 11:00:00'),
    )
    cur.execute(
        'CREATE TABLE venue_positions (id INTEGER PRIMARY KEY, venue TEXT, instrument_type TEXT, symbol_or_market_id TEXT, side TEXT, entry_price REAL, mark_price REAL, notional_usd REAL, unrealized_pnl REAL, realized_pnl REAL, leverage INTEGER, strategy_profile TEXT, status TEXT, opened_at TEXT)'
    )
    cur.execute(
        'INSERT INTO venue_positions VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        ('binance_futures', 'futures', 'BTC/USDT:USDT', 'LONG', 100000.0, 100500.0, 100.0, 5.0, 0.0, 2, 'baseline', 'OPEN', '2026-04-09 10:00:00'),
    )
    cur.execute(
        'CREATE TABLE venue_orders (id INTEGER PRIMARY KEY, venue TEXT, symbol_or_market_id TEXT, order_type TEXT, side TEXT, qty REAL, price REAL, stop_price REAL, reduce_only INTEGER, status TEXT, created_at TEXT)'
    )
    cur.execute(
        'INSERT INTO venue_orders VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        ('binance_futures', 'BTC/USDT:USDT', 'STOP_LOSS', 'SELL', 0.001, 100000.0, 97000.0, 1, 'OPEN', '2026-04-09 10:00:00'),
    )
    cur.execute(
        'CREATE TABLE whale_wallets (address TEXT, source_type TEXT, enabled INTEGER, discovery_score REAL, last_event_amount REAL, event_count_24h INTEGER, failure_streak INTEGER, last_seen_at TEXT, last_event_category TEXT)'
    )
    cur.executemany(
        'INSERT INTO whale_wallets VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
        [
            ('0xaaa', 'leaderboard', 1, 0.91, 12000.0, 5, 0, '2026-04-09 10:00:00', 'CRYPTO'),
            ('0xbbb', 'activity_discovery', 1, 0.72, 8000.0, 3, 1, '2026-04-09 10:00:00', 'POLITICS'),
            ('0xccc', 'graph_discovery', 1, 0.68, 5500.0, 2, 0, '2026-04-09 10:00:00', 'CRYPTO'),
        ],
    )
    cur.execute(
        'CREATE TABLE whale_wallet_sources (address TEXT NOT NULL, source_type TEXT NOT NULL, last_seen_at TEXT, PRIMARY KEY (address, source_type))'
    )
    cur.executemany(
        'INSERT INTO whale_wallet_sources VALUES (?, ?, ?)',
        [
            ('0xaaa', 'leaderboard', '2026-04-09 10:00:00'),
            ('0xbbb', 'activity_discovery', '2026-04-09 10:00:00'),
            ('0xccc', 'graph_discovery', '2026-04-09 10:00:00'),
        ],
    )
    cur.execute(
        'CREATE TABLE whale_stats (address TEXT PRIMARY KEY, total_trades INTEGER DEFAULT 0, wins INTEGER DEFAULT 0, total_pnl REAL DEFAULT 0, trust_score REAL DEFAULT 0.5, last_active TEXT)'
    )
    cur.execute(
        'INSERT INTO whale_stats VALUES (?, ?, ?, ?, ?, ?)',
        ('0xaaa', 4, 3, 12.5, 0.75, '2026-04-09 11:00:00'),
    )
    cur.execute(
        'CREATE TABLE decision_audit (id INTEGER PRIMARY KEY, occurred_at TEXT, venue TEXT, market_id TEXT, category TEXT, signal_family TEXT, strategy_profile TEXT, raw_source_signal TEXT, action TEXT, reason TEXT, decision_score REAL, threshold REAL, trade_size REAL, confidence REAL, mapping_stage TEXT, lazy_lookup_attempted INTEGER, lazy_lookup_hit INTEGER, alias_candidates_json TEXT, hot_window_promoted INTEGER DEFAULT 0, inputs_json TEXT)'
    )
    cur.executemany(
        'INSERT INTO decision_audit VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        [
            (1, '2026-04-09 10:00:00', 'polymarket', 'market-1', 'SPORTS', 'activity_orderflow', 'baseline', 'activity', 'reject', 'liquidity_guard', 0.67, 0.72, 40.0, 0.67, 'alias_cache', 0, 0, '["token-1","condition-1"]', 0, '{"original_side":"BUY","copy_eligible":true}'),
            (2, '2026-04-09 10:01:00', 'polymarket', 'market-2', 'OTHER', 'whale', 'baseline', 'whale_tracker', 'reject', 'market_not_mapped_active_window', 0.0, 0.78, 25.0, 0.0, 'active_window', 0, 0, '["mystery-token","mystery-market"]', 0, '{"original_side":"BUY","copy_eligible":true}'),
            (3, '2026-04-09 10:02:00', 'polymarket', 'market-3', 'SPORTS', 'whale', 'sampling_relaxed', 'whale_tracker', 'decision', 'score_below_threshold', 0.71, 0.72, 40.0, 0.71, 'hot_window', 1, 1, '["hot-token","hot-market"]', 1, '{"copy_policy":"gated_whale_copy","whale_copy_relaxed_gate":true,"whale_copy_gate_ready":true,"token_recovery_attempted":true,"token_recovery_hit":true,"original_side":"BUY","copy_eligible":true}'),
            (4, '2026-04-09 10:03:00', 'polymarket', 'market-4', 'POLITICS', 'whale', 'sampling_relaxed', 'activity', 'reject', 'slippage_guard_rejection,score_below_threshold,missing_polymarket_token_price,token_recovery_failed', 0.48, 0.58, 35.0, 0.48, 'lazy_lookup', 1, 0, '["sampling-token","sampling-market"]', 0, '{"copy_policy":"gated_whale_copy","whale_copy_relaxed_gate":true,"whale_copy_gate_ready":true,"token_recovery_attempted":true,"token_recovery_failed":true,"original_side":"BUY","copy_eligible":true}'),
            (5, '2026-04-09 10:04:00', 'polymarket', 'market-5', 'SPORTS', 'discovery', 'baseline', 'discovery', 'reject', 'route_whale_orderflow_only', 0.0, 0.72, 40.0, 0.0, 'active_context', 0, 0, '["route-only-market"]', 0, '{}'),
            (6, '2026-04-09 10:05:00', 'polymarket', 'market-6', 'OTHER', 'whale', 'baseline', 'whale_tracker', 'reject', 'unsupported_side_filtered', 0.0, 0.78, 25.0, 0.0, 'alias_cache', 0, 0, '["unsupported-market"]', 0, '{"original_side":"SELL","copy_eligible":false,"side_filter_stage":"post_mapping_pre_orderbook"}'),
            (7, '2026-04-09 10:06:00', 'polymarket', 'market-3', 'SPORTS', 'whale', 'sampling_relaxed', 'whale_tracker', 'execute', None, 0.71, None, 40.0, 0.71, 'hot_window', 1, 1, '["hot-token","hot-market"]', 1, '{"copy_policy":"gated_whale_copy","whale_copy_relaxed_gate":true,"whale_copy_gate_ready":true,"token_recovery_attempted":true,"token_recovery_hit":true,"gated_whale_event_count":2,"gated_total_notional":900.0,"gated_unique_wallets":2,"gated_source_count":2,"gated_max_trust":0.66,"original_side":"BUY","copy_eligible":true}'),
        ],
    )
    cur.execute(
        'CREATE TABLE market_aliases (alias TEXT, alias_type TEXT, market_id TEXT, question TEXT, category TEXT, volume_24h REAL, active INTEGER, source TEXT, last_seen_at TEXT)'
    )
    cur.executemany(
        'INSERT INTO market_aliases VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
        [
            ('token-1', 'token_id', 'market-1', 'Test market', 'SPORTS', 75000.0, 1, 'explorer', '2026-04-09 10:00:00'),
            ('condition-1', 'market_id', 'market-1', 'Test market', 'SPORTS', 75000.0, 1, 'lazy_lookup', '2026-04-09 10:00:00'),
        ],
    )
    cur.execute(
        'CREATE TABLE runtime_status_snapshot (id INTEGER PRIMARY KEY, updated_at TEXT DEFAULT CURRENT_TIMESTAMP, metrics_json TEXT NOT NULL)'
    )
    cur.execute(
        'CREATE TABLE polymarket_shadow_actions (id INTEGER PRIMARY KEY, wallet_address TEXT, market_id TEXT, category TEXT, source_type TEXT, action_type TEXT, replay_key TEXT, raw_notional_usd REAL, shadow_pnl REAL, shadow_edge REAL, drawdown_pct REAL, opened_at TEXT, closed_at TEXT, status TEXT)'
    )
    cur.executemany(
        'INSERT INTO polymarket_shadow_actions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        [
            (1, '0xaaa', 'market-2', 'CRYPTO', 'leaderboard', 'shadow_trade', None, 40.0, 6.5, 3.2, -2.1, '2026-04-08 10:00:00', '2026-04-08 12:00:00', 'CLOSED_WIN'),
            (2, '0xaaa', 'market-3', 'CRYPTO', 'leaderboard', 'shadow_trade', None, 35.0, 4.0, 1.6, -1.0, '2026-04-09 09:00:00', '2026-04-09 10:30:00', 'CLOSED_WIN'),
            (3, '0xaaa', 'market-4', 'CRYPTO', 'leaderboard', 'shadow_replay', '0xaaa:trade-1', 50.0, 3.0, 1.1, -0.8, '2026-04-09 11:00:00', '2026-04-09 12:00:00', 'CLOSED_WIN'),
            (4, '0xbbb', 'market-5', 'POLITICS', 'activity_discovery', 'shadow_trade', None, 25.0, -1.2, -0.6, -6.0, '2026-04-09 07:00:00', '2026-04-09 08:00:00', 'CLOSED_LOSS'),
        ],
    )
    cur.execute(
        """
        CREATE TABLE polymarket_copy_actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_trade_key TEXT NOT NULL,
            wallet_address TEXT NOT NULL,
            market_id TEXT NOT NULL,
            category TEXT NOT NULL,
            action_type TEXT NOT NULL,
            reason TEXT NOT NULL DEFAULT '',
            source_status TEXT NOT NULL DEFAULT '',
            side TEXT NOT NULL DEFAULT '',
            source_notional_usd REAL NOT NULL DEFAULT 0,
            follower_notional_usd REAL NOT NULL DEFAULT 0,
            source_pnl REAL NOT NULL DEFAULT 0,
            follower_pnl REAL NOT NULL DEFAULT 0,
            delayed_seconds INTEGER NOT NULL DEFAULT 0,
            source_opened_at TEXT NOT NULL DEFAULT '',
            source_closed_at TEXT NOT NULL DEFAULT '',
            executed_at TEXT NOT NULL,
            notes_json TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    cur.executemany(
        """
        INSERT INTO polymarket_copy_actions (
            source_trade_key, wallet_address, market_id, category, action_type, reason,
            source_status, side, source_notional_usd, follower_notional_usd, source_pnl,
            follower_pnl, delayed_seconds, source_opened_at, source_closed_at, executed_at, notes_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                '0xaaa:copy-open', '0xaaa', 'market-copy-1', 'CRYPTO', 'open',
                'copy_entry', 'OPEN', 'BUY', 120.0, 50.0, 0.0, 0.0, 90,
                '2026-04-09 09:00:00', '', '2026-04-09 09:02:00', '{}',
            ),
            (
                '0xaaa:copy-closed', '0xaaa', 'market-copy-2', 'CRYPTO', 'replay_closed',
                'shadow_replay_seed', 'CLOSED_WIN', 'BUY', 80.0, 50.0, 12.0, 7.5, 90,
                '2026-04-08 09:00:00', '2026-04-08 11:00:00', '2026-04-09 09:03:00', '{}',
            ),
            (
                '0xaaa:copy-reject', '0xaaa', 'market-copy-1', 'CRYPTO', 'reject',
                'duplicate_market_exposure', 'OPEN', 'BUY', 30.0, 0.0, 0.0, 0.0, 90,
                '2026-04-09 09:05:00', '', '2026-04-09 09:06:00', '{}',
            ),
        ],
    )
    cur.execute(
        """
        CREATE TABLE polymarket_copy_positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_trade_key TEXT UNIQUE NOT NULL,
            wallet_address TEXT NOT NULL,
            market_id TEXT NOT NULL,
            category TEXT NOT NULL,
            side TEXT NOT NULL,
            source_notional_usd REAL NOT NULL DEFAULT 0,
            follower_notional_usd REAL NOT NULL DEFAULT 0,
            source_pnl REAL NOT NULL DEFAULT 0,
            follower_pnl REAL NOT NULL DEFAULT 0,
            source_status TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'OPEN',
            source_opened_at TEXT NOT NULL DEFAULT '',
            source_closed_at TEXT NOT NULL DEFAULT '',
            opened_at TEXT NOT NULL,
            closed_at TEXT,
            notes_json TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    cur.execute(
        """
        INSERT INTO polymarket_copy_positions (
            source_trade_key, wallet_address, market_id, category, side, source_notional_usd,
            follower_notional_usd, source_status, status, source_opened_at, opened_at, notes_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            '0xaaa:copy-open', '0xaaa', 'market-copy-1', 'CRYPTO', 'BUY', 120.0,
            50.0, 'OPEN', 'OPEN', '2026-04-09 09:00:00', '2026-04-09 09:02:00', '{}',
        ),
    )
    cur.execute(
        """
        CREATE TABLE polymarket_research_wallets (
            address TEXT PRIMARY KEY,
            source_type TEXT NOT NULL DEFAULT 'unknown',
            primary_source TEXT NOT NULL DEFAULT 'unknown',
            source_labels TEXT NOT NULL DEFAULT '[]',
            source_count INTEGER NOT NULL DEFAULT 0,
            discovery_bucket TEXT NOT NULL DEFAULT 'unassigned',
            cohort TEXT NOT NULL DEFAULT 'discovery',
            discovery_rank INTEGER NOT NULL DEFAULT 0,
            shadow_rank INTEGER NOT NULL DEFAULT 0,
            copy_ready_rank INTEGER NOT NULL DEFAULT 0,
            discovery_score REAL NOT NULL DEFAULT 0,
            trust_score REAL NOT NULL DEFAULT 0.5,
            consistency_score REAL NOT NULL DEFAULT 0,
            profit_consistency_score REAL NOT NULL DEFAULT 0,
            recency_score REAL NOT NULL DEFAULT 0,
            frequency_score REAL NOT NULL DEFAULT 0,
            drawdown_estimate_pct REAL NOT NULL DEFAULT 0,
            active_days INTEGER NOT NULL DEFAULT 0,
            closed_trade_count INTEGER NOT NULL DEFAULT 0,
            realized_pnl REAL NOT NULL DEFAULT 0,
            crypto_participation_ratio REAL NOT NULL DEFAULT 0,
            specialization TEXT NOT NULL DEFAULT 'UNKNOWN',
            event_count_24h INTEGER NOT NULL DEFAULT 0,
            last_event_amount REAL NOT NULL DEFAULT 0,
            last_seen_at TEXT,
            closed_shadow_trades INTEGER NOT NULL DEFAULT 0,
            shadow_pnl REAL NOT NULL DEFAULT 0,
            shadow_edge REAL NOT NULL DEFAULT 0,
            worst_drawdown_pct REAL NOT NULL DEFAULT 0,
            shadow_gate_status TEXT NOT NULL DEFAULT 'blocked',
            shadow_gate_reason TEXT NOT NULL DEFAULT 'low_consistency',
            copy_ready_gate_status TEXT NOT NULL DEFAULT 'blocked',
            copy_ready_gate_reason TEXT NOT NULL DEFAULT 'needs_shadow_history',
            shadow_eligible INTEGER NOT NULL DEFAULT 0,
            copy_ready_eligible INTEGER NOT NULL DEFAULT 0,
            watchlist_priority_rank INTEGER NOT NULL DEFAULT 0,
            watchlist_status TEXT NOT NULL DEFAULT '',
            watchlist_mode TEXT NOT NULL DEFAULT '',
            identity_resolution_status TEXT NOT NULL DEFAULT 'untracked',
            priority_pinned INTEGER NOT NULL DEFAULT 0,
            historical_trade_evidence_status TEXT NOT NULL DEFAULT 'no_historical_evidence',
            historical_trade_rows INTEGER NOT NULL DEFAULT 0,
            evidence_last_trade_at TEXT,
            shadow_seeded INTEGER NOT NULL DEFAULT 0,
            shadow_blocker_reason TEXT NOT NULL DEFAULT '',
            refreshed_at TEXT
        )
        """
    )
    cur.executemany(
        """
        INSERT INTO polymarket_research_wallets (
            address, source_type, primary_source, source_labels, source_count, discovery_bucket, cohort,
            discovery_rank, shadow_rank, copy_ready_rank, discovery_score, trust_score, consistency_score,
            profit_consistency_score, recency_score, frequency_score, drawdown_estimate_pct, active_days,
            closed_trade_count, realized_pnl, crypto_participation_ratio, specialization, event_count_24h,
            last_event_amount, last_seen_at, closed_shadow_trades, shadow_pnl, shadow_edge, worst_drawdown_pct,
            shadow_gate_status, shadow_gate_reason, copy_ready_gate_status, copy_ready_gate_reason,
            shadow_eligible, copy_ready_eligible, watchlist_priority_rank, watchlist_status, watchlist_mode,
            identity_resolution_status, priority_pinned, historical_trade_evidence_status, historical_trade_rows,
            evidence_last_trade_at, shadow_seeded, shadow_blocker_reason, refreshed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                '0xaaa', 'leaderboard', 'leaderboard', json.dumps(['leaderboard', 'manual_confirmed']), 2,
                'leaderboard', 'copy_ready', 1, 1, 1, 0.91, 0.75, 0.88, 0.91, 0.92, 0.84, 2.1, 6, 12, 3250.0,
                1.0, 'CRYPTO', 5, 12000.0, '2026-04-09 10:00:00', 3, 13.5, 5.9, -2.1,
                'promoted', 'eligible', 'promoted', 'eligible', 1, 1, 2, 'linked', 'fast_track_shadow', 'linked', 1,
                'detailed_trade_history', 3, '2026-04-09 12:00:00', 1, 'eligible', '2026-04-09 10:00:00'
            ),
            (
                '0xbbb', 'activity_discovery', 'activity_discovery', json.dumps(['activity_discovery']), 1,
                'activity_discovery', 'shadow', 2, 2, 0, 0.72, 0.50, 0.62, 0.51, 0.66, 0.44, 6.0, 4, 4, 420.0,
                0.75, 'CRYPTO', 3, 8000.0, '2026-04-09 10:00:00', 1, -1.2, -0.6, -6.0,
                'promoted', 'eligible', 'blocked', 'needs_shadow_history', 1, 0, 0, '', '', 'untracked', 0,
                'no_historical_evidence', 0, '', 0, 'eligible', '2026-04-09 10:00:00'
            ),
            (
                '0xccc', 'graph_discovery', 'graph_discovery', json.dumps(['graph_discovery']), 1,
                'graph_discovery', 'discovery', 3, 0, 0, 0.68, 0.50, 0.41, 0.38, 0.52, 0.31, 4.4, 2, 2, 120.0,
                1.0, 'CRYPTO', 2, 5500.0, '2026-04-09 10:00:00', 0, 0.0, 0.0, 0.0,
                'blocked', 'low_activity', 'blocked', 'not_shadow_wallet', 0, 0, 0, '', '', 'untracked', 0,
                'no_historical_evidence', 0, '', 0, 'low_activity', '2026-04-09 10:00:00'
            ),
            (
                '0xddd', 'static_seed', 'static_seed', json.dumps(['static_seed']), 1,
                'static_seed', 'discovery', 4, 0, 0, 0.83, 0.50, 0.79, 0.82, 0.71, 0.67, 3.1, 7, 7, 900.0,
                1.0, 'CRYPTO', 2, 4000.0, '2026-04-09 10:00:00', 0, 0.0, 0.0, 0.0,
                'blocked', 'seed_only_excluded', 'blocked', 'not_shadow_wallet', 0, 0, 0, '', '', 'untracked', 0,
                'no_historical_evidence', 0, '', 0, 'seed_only_excluded', '2026-04-09 10:00:00'
            ),
        ],
    )
    cur.execute(
        """
        CREATE TABLE polymarket_research_watchlist (
            id INTEGER PRIMARY KEY,
            display_name TEXT NOT NULL,
            profile_ref TEXT NOT NULL,
            wallet_address TEXT,
            priority_rank INTEGER NOT NULL DEFAULT 0,
            priority_mode TEXT NOT NULL DEFAULT 'normal',
            target_specialization TEXT NOT NULL DEFAULT 'UNKNOWN',
            status TEXT NOT NULL DEFAULT 'pending_resolution',
            notes TEXT,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    cur.executemany(
        'INSERT INTO polymarket_research_watchlist VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        [
            (1, 'ohanism', 'https://polymarket.com/tr/@ohanism', None, 1, 'fast_track_shadow', 'CRYPTO', 'pending_resolution', 'Seeded priority specialist candidate', '2026-04-09 10:00:00', '2026-04-09 10:00:00'),
            (2, 'crypto-pro', 'https://polymarket.com/tr/@crypto-pro', '0xaaa', 2, 'fast_track_shadow', 'CRYPTO', 'linked', 'Linked test priority wallet', '2026-04-09 10:00:00', '2026-04-09 10:00:00'),
        ],
    )
    conn.commit()
    conn.close()


def _write_reports(summary_path: Path, swot_path: Path) -> None:
    summary_path.write_text(
        json.dumps(
            {
                'evidence': {'live_paper_closed': 3, 'synthetic_total': 6},
                'core': {'expectancy': 0.0312},
                'sampling_summary': {
                    'strategy_profile': 'sampling_relaxed',
                    'core_metrics': {
                        'closed_trades': 1,
                        'win_rate': 100.0,
                        'expectancy': 9.5,
                        'total_pnl': 9.5,
                    },
                },
            }
        ),
        encoding='utf-8',
    )
    swot_path.write_text(
        json.dumps(
            {
                'final_verdict': {
                    'verdict': 'IMPROVE FIRST',
                    'reason': 'Still collecting non-synthetic live paper evidence.',
                }
            }
        ),
        encoding='utf-8',
    )


def _find_free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _request(url: str, auth: tuple[str, str] | None = None):
    request = urllib.request.Request(url)
    if auth is not None:
        token = base64.b64encode(f'{auth[0]}:{auth[1]}'.encode('utf-8')).decode('ascii')
        request.add_header('Authorization', f'Basic {token}')
    return urllib.request.urlopen(request, timeout=5)


class DashboardServer:
    def __init__(self, process: subprocess.Popen[bytes], base_url: str, db_path: Path):
        self.process = process
        self.base_url = base_url
        self.db_path = db_path

    def close(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)


@pytest.fixture()
def dashboard_server(tmp_path: Path):
    if PHP_BIN is None:
        pytest.skip('php is not available in PATH')

    db_path = tmp_path / 'dashboard.db'
    summary_path = tmp_path / 'summary.json'
    swot_path = tmp_path / 'swot_report.json'
    _create_dashboard_db(db_path)
    _write_reports(summary_path, swot_path)

    port = _find_free_port()
    env = os.environ.copy()
    env.update(
        {
            'GHOST_TRADER_REPO_ROOT': str(REPO_ROOT),
            'GHOST_TRADER_DB_PATH': str(db_path),
            'DASHBOARD_SUMMARY_PATH': str(summary_path),
            'DASHBOARD_SWOT_PATH': str(swot_path),
            'DASHBOARD_USER': DASHBOARD_USER,
            'DASHBOARD_PASSWORD_HASH': DASHBOARD_HASH,
            'DASHBOARD_REFRESH_SECONDS': '1',
            'DASHBOARD_LOG_LINES': '5',
        }
    )

    process = subprocess.Popen(
        [PHP_BIN, '-S', f'127.0.0.1:{port}', '-t', str(REPO_ROOT / 'dashboard' / 'public')],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base_url = f'http://127.0.0.1:{port}'

    started = False
    for _ in range(40):
        try:
            _request(base_url + '/api.php', auth=(DASHBOARD_USER, DASHBOARD_PASSWORD)).close()
            started = True
            break
        except Exception:
            time.sleep(0.1)

    if not started:
        process.terminate()
        process.wait(timeout=5)
        pytest.fail('dashboard php server did not start in time')

    server = DashboardServer(process, base_url, db_path)
    try:
        yield server
    finally:
        server.close()


def test_dashboard_requires_basic_auth(dashboard_server: DashboardServer):
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        _request(dashboard_server.base_url + '/api.php').close()
    assert exc_info.value.code == 401


def test_dashboard_rejects_wrong_credentials(dashboard_server: DashboardServer):
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        _request(dashboard_server.base_url + '/api.php', auth=(DASHBOARD_USER, 'wrong-password')).close()
    assert exc_info.value.code == 401


def test_dashboard_api_returns_runtime_payload(dashboard_server: DashboardServer):
    response = _request(dashboard_server.base_url + '/api.php', auth=(DASHBOARD_USER, DASHBOARD_PASSWORD))
    payload = json.loads(response.read().decode('utf-8'))

    assert 'dashboard_tab_help' in payload
    assert 'dashboard_glossary' in payload
    assert 'discovery_wallet_summary' in payload
    assert 'discovery_source_summary' in payload
    assert 'shadow_wallet_summary' in payload
    assert 'shadow_promotion_summary' in payload
    assert 'copy_ready_wallet_summary' in payload
    assert 'wallet_provenance_summary' in payload
    assert 'linked_wallet_evidence_summary' in payload
    assert 'shadow_evidence_backfill_summary' in payload
    assert 'shadow_edge_summary' in payload
    assert 'recent_shadow_actions' in payload
    assert 'wallet_consistency_table' in payload
    assert 'fresh_technical_summary' in payload
    assert 'fresh_pnl_summary_7d' in payload
    assert 'technical_score_summary' in payload
    assert 'technical_reject_breakdown' in payload
    assert 'position_pressure_summary' in payload
    assert 'legacy_position_summary' in payload
    assert 'binance_technical_fresh_summary' in payload
    assert 'binance_technical_stale_eligibility_summary' in payload
    assert 'binance_technical_fresh_gate_funnel' in payload
    assert 'binance_technical_fresh_recovery_summary' in payload
    assert 'binance_technical_fresh_score_gap_summary' in payload
    assert 'binance_technical_fresh_reject_breakdown' in payload
    assert 'copy_execution_summary' in payload
    assert 'copy_reject_breakdown' in payload
    assert 'active_copy_positions' in payload
    assert 'wallet_follower_pnl_summary' in payload
    assert 'shadow_vs_copy_drift_summary' in payload
    assert 'recent_copy_actions' in payload
    assert 'polymarket_operator_summary' in payload
    assert 'binance_operator_summary' in payload
    assert 'operator_landing_summary' in payload
    assert payload['service']['name'] == 'ghost-trader'
    assert payload['runtime_summary']['tracked_whales'] == 3
    assert payload['runtime_summary']['total_trades'] == 2
    assert payload['runtime_summary']['unmapped_orderflow_events'] == 1
    assert payload['runtime_summary']['alias_cache_hits'] == 2
    assert payload['runtime_summary']['lazy_lookup_hits'] == 1
    assert payload['runtime_summary']['market_not_mapped_rate'] == 20.0
    assert payload['runtime_summary']['recent_market_not_mapped_rate'] == 20.0
    assert payload['runtime_summary']['historical_market_not_mapped_rate'] == 20.0
    assert payload['runtime_summary']['recent_mapped_orderflow_events'] == 4
    assert payload['runtime_summary']['historical_mapped_orderflow_events'] == 4
    assert payload['runtime_summary']['live_metrics_available'] is False
    assert payload['runtime_summary']['hot_window_hits'] == 1
    assert payload['runtime_summary']['hot_window_promotions'] == 1
    assert payload['runtime_summary']['active_window_misses'] == 1
    assert payload['runtime_summary']['sampling_mode'] == 'disabled'
    assert payload['runtime_summary']['sampling_target_closed_trades'] == 20
    assert payload['recent_trades'][0]['market_id'] == 'market-2'
    assert any(row['strategy_profile'] == 'sampling_relaxed' for row in payload['recent_decisions'])
    assert payload['open_positions'][0]['symbol_or_market_id'] == 'BTC/USDT:USDT'
    assert payload['top_whales'][0]['address'] == '0xaaa'
    assert payload['top_whales'][0]['trust_score'] == 0.75
    assert payload['top_whales'][0]['total_trades'] == 4
    assert payload['top_whales'][0]['wins'] == 3
    assert payload['top_whales'][0]['total_pnl'] == 12.5
    assert payload['top_whales'][0]['win_rate'] == 75.0
    assert payload['top_whales'][1]['address'] == '0xbbb'
    assert payload['top_whales'][1]['trust_score'] == 0.5
    assert payload['top_whales'][1]['total_trades'] == 0
    assert payload['top_whales'][1]['win_rate'] is None
    assert payload['discovery_wallet_summary']['tracked_wallets'] == 4
    assert payload['discovery_wallet_summary']['promoted_to_shadow'] == 2
    assert payload['shadow_wallet_summary']['wallets_with_shadow_actions'] == 2
    assert payload['copy_ready_wallet_summary']['copy_ready_wallets'] == 1
    assert payload['shadow_edge_summary']['shadow_ready'] is True
    assert payload['discovery_source_summary']['selected_wallets'] == 4
    assert payload['discovery_source_summary']['static_seed_used'] == 1
    discovery_source_counts = {row['bucket']: row['actual'] for row in payload['discovery_source_summary']['rows']}
    assert discovery_source_counts['leaderboard'] == 1
    assert discovery_source_counts['activity_discovery'] == 1
    assert discovery_source_counts['graph_discovery'] == 1
    assert discovery_source_counts['manual_persisted'] == 0
    assert payload['shadow_promotion_summary']['eligible_wallets'] == 2
    assert payload['shadow_promotion_summary']['promoted_wallets'] == 2
    blocker_counts = {row['reason']: row['count'] for row in payload['shadow_promotion_summary']['blocker_counts']}
    assert blocker_counts['low_activity'] == 1
    assert blocker_counts['seed_only_excluded'] == 1
    assert payload['wallet_provenance_summary']['multi_source_wallets'] == 1
    assert payload['wallet_provenance_summary']['single_source_wallets'] == 3
    assert payload['wallet_provenance_summary']['seed_only_wallets'] == 1
    assert payload['priority_watchlist_summary']['total_watchlist_rows'] == 2
    assert payload['priority_watchlist_summary']['linked_rows'] == 1
    assert payload['priority_watchlist_summary']['pending_resolution_rows'] == 1
    assert payload['priority_watchlist_summary']['promoted_priority_wallets'] == 1
    assert payload['identity_resolution_summary']['pending_handle_only_entries'] == 1
    assert payload['identity_resolution_summary']['linked_entries'] == 1
    assert payload['identity_resolution_summary']['unresolved_but_ranked_entries'] == 1
    assert payload['linked_wallet_evidence_summary']['linked_wallets_total'] == 1
    assert payload['linked_wallet_evidence_summary']['linked_with_trade_history'] == 1
    assert payload['linked_wallet_evidence_summary']['linked_stats_only'] == 0
    assert payload['linked_wallet_evidence_summary']['linked_without_trade_history'] == 0
    assert payload['linked_wallet_evidence_summary']['linked_promoted_to_shadow'] == 1
    assert payload['shadow_replay_summary']['replayed_actions_created'] == 1
    assert payload['shadow_replay_summary']['wallets_with_replay_history'] == 1
    assert payload['shadow_replay_summary']['net_shadow_pnl'] == 3.0
    assert payload['shadow_replay_summary']['net_shadow_edge'] == 1.1
    assert payload['shadow_replay_summary']['eligible_without_trade_history'] == 1
    assert payload['shadow_evidence_backfill_summary']['replay_rows_created'] == 1
    assert payload['shadow_evidence_backfill_summary']['wallets_with_replay_history'] == 1
    assert payload['shadow_evidence_backfill_summary']['wallets_without_replay_history'] == 0
    assert payload['shadow_evidence_backfill_summary']['net_replay_shadow_pnl'] == 3.0
    assert payload['shadow_evidence_backfill_summary']['net_replay_shadow_edge'] == 1.1
    assert payload['recent_shadow_actions'][0]['wallet_address'] == '0xaaa'
    assert payload['wallet_consistency_table'][0]['address'] == '0xaaa'
    assert payload['wallet_consistency_table'][0]['primary_source'] == 'leaderboard'
    assert 'manual_confirmed' in payload['wallet_consistency_table'][0]['source_labels']
    assert payload['wallet_consistency_table'][0]['shadow_gate_status'] == 'promoted'
    assert payload['wallet_consistency_table'][0]['shadow_gate_reason'] == 'eligible'
    assert payload['wallet_consistency_table'][0]['watchlist_priority_rank'] == 2
    assert payload['wallet_consistency_table'][0]['watchlist_status'] == 'linked'
    assert payload['wallet_consistency_table'][0]['watchlist_mode'] == 'fast_track_shadow'
    assert payload['wallet_consistency_table'][0]['identity_resolution_status'] == 'linked'
    assert payload['wallet_consistency_table'][0]['historical_trade_evidence_status'] == 'detailed_trade_history'
    assert payload['wallet_consistency_table'][0]['historical_trade_rows'] == 3
    assert payload['wallet_consistency_table'][0]['shadow_seeded'] is True
    assert payload['wallet_consistency_table'][0]['shadow_blocker_reason'] == 'eligible'
    assert payload['copy_ready_wallets'][0]['address'] == '0xaaa'
    assert payload['copy_execution_summary']['copy_ready_wallets'] == 1
    assert payload['copy_execution_summary']['shadow_proven_wallets'] == 1
    assert payload['copy_execution_summary']['manual_fast_track_wallets'] == 0
    assert payload['copy_execution_summary']['eligible_copy_wallets_total'] == 1
    assert payload['copy_execution_summary']['open_actions'] == 1
    assert payload['copy_execution_summary']['replay_closed_actions'] == 1
    assert payload['copy_execution_summary']['reject_actions'] == 1
    assert payload['copy_execution_summary']['active_copy_positions'] == 1
    assert payload['copy_execution_summary']['active_shadow_proven_positions'] == 1
    assert payload['copy_execution_summary']['active_manual_fast_track_positions'] == 0
    assert payload['copy_execution_summary']['wallets_with_realized_pnl'] == 1
    assert payload['copy_reject_breakdown'][0]['reason'] == 'duplicate_market_exposure'
    assert payload['active_copy_positions'][0]['market_id'] == 'market-copy-1'
    assert payload['active_copy_positions'][0]['cohort_source'] == 'shadow_proven'
    assert payload['wallet_follower_pnl_summary'][0]['wallet_address'] == '0xaaa'
    assert payload['wallet_follower_pnl_summary'][0]['cohort_source'] == 'shadow_proven'
    assert payload['wallet_follower_pnl_summary'][0]['follower_realized_pnl'] == 7.5
    assert payload['shadow_vs_copy_drift_summary']['copy_ready_wallets'] == 1
    assert payload['shadow_vs_copy_drift_summary']['shadow_proven_wallets'] == 1
    assert payload['shadow_vs_copy_drift_summary']['manual_fast_track_wallets'] == 0
    assert payload['shadow_vs_copy_drift_summary']['eligible_copy_wallets_total'] == 1
    assert payload['shadow_vs_copy_drift_summary']['copy_realized_pnl'] == 7.5
    assert payload['recent_copy_actions'][0]['reason'] == 'duplicate_market_exposure'
    assert payload['recent_copy_actions'][0]['cohort_source'] == 'shadow_proven'
    assert payload['polymarket_operator_summary']['wallet_copy_status']['main_wallet_name'] == 'ohanism'
    assert payload['polymarket_operator_summary']['wallet_copy_status']['copy_ready_wallets'] == 1
    assert payload['polymarket_operator_summary']['paper_copy_performance']['open_copy_positions'] == 1
    assert payload['polymarket_operator_summary']['work_proof']['test_proof_passed'] is False
    assert payload['polymarket_operator_summary']['open_paper_trades'][0]['market_id'] == 'market-copy-1'
    assert payload['polymarket_operator_summary']['recent_closed_trades'][0]['action_type'] == 'replay_closed'
    assert payload['polymarket_operator_summary']['top_kpis']['tracked_whales'] == 4
    assert payload['polymarket_operator_summary']['top_kpis']['active_copy_positions'] == 1
    assert payload['polymarket_operator_summary']['top_kpis']['portfolio_value'] == 960.0
    assert payload['polymarket_operator_summary']['copy_portfolio_summary']['available_balance'] == 960.0
    assert payload['polymarket_operator_summary']['tracked_wallets'][0]['tier'] == 'A'
    assert payload['polymarket_operator_summary']['tracked_wallets'][0]['copy_status'] == 'takipte'
    assert payload['polymarket_operator_summary']['tracked_wallets'][0]['detail']['gate_state']['historical_trade_evidence_status'] == 'detailed_trade_history'
    assert len(payload['polymarket_operator_summary']['watchlist_segments']) == 5
    assert payload['polymarket_operator_summary']['performance_chart_series']['equity_trend']
    assert payload['priority_watchlist_rows'][0]['display_name'] == 'ohanism'
    assert payload['priority_watchlist_rows'][0]['profile_ref'] == 'https://polymarket.com/tr/@ohanism'
    assert payload['priority_watchlist_rows'][0]['identity_resolution_status'] == 'pending_resolution'
    assert payload['priority_watchlist_rows'][1]['wallet_address'] == '0xaaa'
    assert payload['priority_watchlist_rows'][1]['promoted_to_shadow'] is True
    assert payload['priority_watchlist_rows'][1]['historical_trade_evidence_status'] == 'detailed_trade_history'
    assert payload['priority_watchlist_rows'][1]['historical_trade_rows'] == 3
    assert payload['priority_watchlist_rows'][1]['shadow_seeded'] is True
    assert payload['priority_watchlist_rows'][1]['shadow_blocker_reason'] == 'eligible'
    assert payload['fresh_technical_summary']['fresh_window_days'] == 7
    assert payload['fresh_pnl_summary_7d']['fresh_window_days'] == 7
    assert payload['fresh_pnl_summary_7d']['fresh_trade_count'] == 0
    assert payload['fresh_pnl_summary_7d']['fresh_closed_trades'] == 0
    assert payload['fresh_pnl_summary_7d']['net_pnl'] == 0.0
    assert payload['fresh_pnl_summary_7d']['venues']['binance_futures']['execute_count'] == 0
    assert payload['fresh_pnl_summary_7d']['venues']['binance_spot']['execute_count'] == 0
    assert payload['binance_operator_summary']['paper_balance_pnl']['fresh_7d_pnl'] == 0.0
    assert payload['binance_operator_summary']['paper_balance_pnl']['open_notional_usd'] == 100.0
    assert payload['binance_operator_summary']['open_trades'][0]['symbol_or_market_id'] == 'BTC/USDT:USDT'
    assert payload['binance_operator_summary']['closed_trade_summary']['win_rate_label'] == 'veri bekleniyor'
    assert payload['binance_operator_summary']['work_proof']['spot_short_reject'] is False
    assert payload['binance_operator_summary']['top_kpis']['open_positions'] == 1
    assert payload['binance_operator_summary']['top_kpis']['open_orders'] == 1
    assert payload['binance_operator_summary']['strategy_status_summary']['spot_short']['allowed'] is False
    assert 'binance_futures' in payload['binance_operator_summary']['filter_options']['venues']
    assert payload['binance_operator_summary']['risk_summary']['symbol_concentration']['symbol'] == 'BTC/USDT:USDT'
    assert payload['binance_operator_summary']['fresh_pnl_summary']['closed_trade_count'] == 0
    assert payload['binance_operator_summary']['performance_chart_series']['equity_breakdown'][0]['label'] == 'Spot'
    assert payload['operator_landing_summary']['lanes']['polymarket']['tracked_wallets'] == 4
    assert payload['operator_landing_summary']['lanes']['binance']['open_orders'] == 1
    assert payload['position_pressure_summary']['open_positions'] == 1
    assert 'open_binance_paper_positions' in payload['legacy_position_summary']['legacy_shape']
    assert payload['legacy_position_summary']['legacy_open_positions'] == []
    whale_counts = {row['source_type']: row['count'] for row in payload['whale_wallet_counts']}
    assert whale_counts['leaderboard'] == 1
    assert whale_counts['activity_discovery'] == 1
    assert whale_counts['graph_discovery'] == 1
    whale_universe = payload['whale_universe_summary']
    assert whale_universe['tracked_whales'] == 3
    assert whale_universe['leaderboard_wallets'] == 1
    assert whale_universe['activity_discovered_wallets'] == 1
    assert whale_universe['graph_discovered_wallets'] == 1
    assert whale_universe['trusted_whales'] == 1
    assert payload['trusted_whale_summary'][0]['address'] == '0xaaa'
    assert payload['trusted_whale_summary'][0]['win_rate'] == 75.0
    assert payload['top_unresolved_aliases'][0]['alias'] == 'mystery-token'
    assert payload['recent_unresolved_aliases'][0]['aliases'][0] == 'mystery-token'
    assert any(row['hot_window_promoted'] == 1 for row in payload['recent_decisions'])
    assert any(row['flow_classification'] == 'discovery-route-only' for row in payload['recent_decisions'])
    sampling_breakdown = {row['reason']: row['count'] for row in payload['sampling_reject_breakdown']}
    assert sampling_breakdown['score_below_threshold'] == 1
    assert sampling_breakdown['slippage_guard_rejection'] == 1
    routing_breakdown = {row['flow_classification']: row['count'] for row in payload['routing_breakdown']}
    assert routing_breakdown['discovery-route-only'] == 1
    assert routing_breakdown['sampling-orderflow'] == 2
    assert routing_breakdown['baseline-orderflow'] == 3
    sampling_decision_summary = {row['action']: row['count'] for row in payload['sampling_decision_summary']}
    assert sampling_decision_summary['decision'] == 1
    assert sampling_decision_summary['reject'] == 1
    assert sampling_decision_summary['execute'] == 1
    mapping_miss_breakdown = {row['reason']: row['count'] for row in payload['mapping_miss_breakdown']}
    assert mapping_miss_breakdown['market_not_mapped_active_window'] == 1
    assert mapping_miss_breakdown['unsupported_side_filtered'] == 1
    alias_persistence_summary = payload['alias_persistence_summary']
    assert alias_persistence_summary['persisted_market_alias_rows'] == 2
    assert alias_persistence_summary['persisted_market_alias_markets'] == 1
    source_quality_summary = payload['source_quality_summary']
    assert source_quality_summary['total_orderflow'] == 5
    assert source_quality_summary['orderflow_after_mapping'] == 4
    assert source_quality_summary['discovery_route_only'] == 1
    assert source_quality_summary['sampling_orderflow'] == 2
    assert source_quality_summary['baseline_orderflow'] == 3
    assert source_quality_summary['unsupported_side_filtered'] == 1
    unsupported_side_summary = {row['reason']: row['count'] for row in payload['unsupported_side_summary']}
    assert unsupported_side_summary['unsupported_side_filtered'] == 1
    whale_copy_summary = payload['whale_copy_summary']
    assert whale_copy_summary['total_whale_events'] == 5
    assert whale_copy_summary['resolved_whale_events'] == 3
    assert whale_copy_summary['whale_copy_candidates'] == 2
    assert whale_copy_summary['gated_rejects'] == 1
    assert whale_copy_summary['gated_decisions'] == 1
    assert whale_copy_summary['gated_executes'] == 1
    whale_side_summary = payload['whale_side_summary']
    assert whale_side_summary['buy_side_events'] == 4
    assert whale_side_summary['sell_side_events'] == 1
    assert whale_side_summary['unsupported_side_filtered'] == 1
    graph_discovery_summary = payload['graph_discovery_summary']
    assert graph_discovery_summary['graph_discovered_wallets'] == 1
    assert graph_discovery_summary['graph_clusters_promoted'] == 0
    assert graph_discovery_summary['graph_skipped_missing_market_ref'] == 0
    whale_candidate_aggregation = payload['whale_candidate_aggregation_summary']
    assert whale_candidate_aggregation['accumulator_buckets'] == 0
    assert whale_candidate_aggregation['gate_ready_candidates'] == 0
    assert whale_candidate_aggregation['retry_candidates'] == 0
    assert whale_candidate_aggregation['accumulated_buy_events'] == 0
    assert whale_candidate_aggregation['accumulated_total_notional'] == 0.0
    assert payload['recent_gate_ready_candidates'] == []
    whale_copy_gate_funnel = payload['whale_copy_gate_funnel']
    assert whale_copy_gate_funnel['resolved_whale_events'] == 3
    assert whale_copy_gate_funnel['gate_ready_candidates'] == 2
    assert whale_copy_gate_funnel['relaxed_gate_attempts'] == 2
    assert whale_copy_gate_funnel['gated_rejects'] == 1
    assert whale_copy_gate_funnel['gated_decisions'] == 1
    assert whale_copy_gate_funnel['gated_executes'] == 1
    whale_copy_recovery = payload['whale_copy_recovery_summary']
    assert whale_copy_recovery['relaxed_gate_attempts'] == 2
    assert whale_copy_recovery['relaxed_gate_rejects'] == 1
    assert whale_copy_recovery['relaxed_gate_decisions'] == 1
    assert whale_copy_recovery['relaxed_gate_executes'] == 1
    assert whale_copy_recovery['token_recovery_attempts'] == 2
    assert whale_copy_recovery['token_recovery_hits'] == 1
    assert whale_copy_recovery['token_recovery_failed'] == 1
    assert whale_copy_recovery['missing_token_rejects'] == 1
    gated_breakdown = {row['reason']: row['count'] for row in payload['gated_reject_breakdown']}
    assert gated_breakdown['score_below_threshold'] == 1
    assert gated_breakdown['slippage_guard_rejection'] == 1
    assert gated_breakdown['missing_polymarket_token_price'] == 1
    assert gated_breakdown['token_recovery_failed'] == 1
    relaxed_breakdown = {row['reason']: row['count'] for row in payload['relaxed_gate_reject_breakdown']}
    assert relaxed_breakdown['score_below_threshold'] == 1
    assert relaxed_breakdown['slippage_guard_rejection'] == 1
    assert relaxed_breakdown['missing_polymarket_token_price'] == 1
    assert relaxed_breakdown['token_recovery_failed'] == 1
    assert payload['sampling_summary']['strategy_profile'] == 'sampling_relaxed'
    assert payload['sampling_summary']['closed_trades'] == 1
    assert payload['performance_summary']['evidence']['live_paper_closed'] == 3
    assert payload['swot_verdict']['final_verdict']['verdict'] == 'IMPROVE FIRST'
    assert isinstance(payload['warnings'], list)


def test_dashboard_api_exposes_acceptance_summaries(dashboard_server: DashboardServer) -> None:
    polymarket_runtime = PolymarketCopyRuntime(
        PolymarketCopySettings(
            db_path=str(dashboard_server.db_path),
            source_db_path=str(dashboard_server.db_path),
        )
    )
    polymarket_runtime.repository.ensure_tables()
    acceptance_notes = {
        "acceptance_fixture": True,
        "cohort_source": "acceptance_fixture",
        "reason": "acceptance_copy_entry",
    }
    polymarket_runtime.repository.insert_copy_action(
        source_trade_key="acceptance:polymarket_copy:open:v1",
        wallet_address="0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        market_id="acceptance-market",
        category="CRYPTO",
        action_type="open",
        reason="acceptance_copy_entry",
        source_status="OPEN",
        side="YES",
        source_notional_usd=50.0,
        follower_notional_usd=25.0,
        source_pnl=0.0,
        follower_pnl=0.0,
        delayed_seconds=0,
        source_opened_at="2026-04-21 10:00:00",
        source_closed_at="",
        executed_at="2026-04-21 10:00:01",
        notes=acceptance_notes,
    )
    polymarket_runtime.repository.create_or_replace_position(
        source_trade_key="acceptance:polymarket_copy:open:v1",
        wallet_address="0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        market_id="acceptance-market",
        category="CRYPTO",
        side="YES",
        source_notional_usd=50.0,
        follower_notional_usd=25.0,
        source_pnl=0.0,
        follower_pnl=0.0,
        source_status="OPEN",
        status="OPEN",
        source_opened_at="2026-04-21 10:00:00",
        source_closed_at="",
        opened_at="2026-04-21 10:00:01",
        closed_at=None,
        notes=acceptance_notes,
    )

    acceptance_db = dashboard_server.db_path.parent / 'binance_acceptance_dashboard.db'
    if acceptance_db.exists():
        acceptance_db.unlink()
    binance_runtime = BinanceTechnicalRuntime(
        BinanceTechnicalSettings(
            db_path=str(acceptance_db),
            symbols=["BTC", "ETH", "SOL"],
            futures_enabled=True,
            spot_enabled=True,
        )
    )
    binance_runtime.run_acceptance_fixture()

    source_conn = sqlite3.connect(acceptance_db)
    source_conn.row_factory = sqlite3.Row
    dashboard_conn = sqlite3.connect(dashboard_server.db_path)
    dashboard_conn.row_factory = sqlite3.Row
    try:
        decision_rows = source_conn.execute(
            """
            SELECT *
            FROM decision_audit
            WHERE strategy_profile = 'binance_technical_sampling'
              AND json_extract(inputs_json, '$.acceptance_fixture') = 1
            ORDER BY id ASC
            """
        ).fetchall()
        if decision_rows:
            target_cols = [row['name'] for row in dashboard_conn.execute("PRAGMA table_info(decision_audit)").fetchall()]
            insert_cols = [col for col in target_cols if col in decision_rows[0].keys() and col != 'id']
            placeholders = ",".join("?" for _ in insert_cols)
            dashboard_conn.executemany(
                f"INSERT INTO decision_audit ({','.join(insert_cols)}) VALUES ({placeholders})",
                [tuple(row[col] for col in insert_cols) for row in decision_rows],
            )

        position_rows = source_conn.execute(
            """
            SELECT *
            FROM venue_positions
            WHERE sample_kind = 'acceptance_fixture'
               OR source_signal = 'binance_acceptance_fixture'
            ORDER BY id ASC
            """
        ).fetchall()
        if position_rows:
            target_cols = [row['name'] for row in dashboard_conn.execute("PRAGMA table_info(venue_positions)").fetchall()]
            insert_cols = [col for col in target_cols if col in position_rows[0].keys() and col != 'id']
            placeholders = ",".join("?" for _ in insert_cols)
            dashboard_conn.executemany(
                f"INSERT INTO venue_positions ({','.join(insert_cols)}) VALUES ({placeholders})",
                [tuple(row[col] for col in insert_cols) for row in position_rows],
            )
        dashboard_conn.commit()
    finally:
        source_conn.close()
        dashboard_conn.close()

    response = _request(dashboard_server.base_url + '/api.php', auth=(DASHBOARD_USER, DASHBOARD_PASSWORD))
    payload = json.loads(response.read().decode('utf-8'))

    assert 'copy_acceptance_summary' in payload
    assert 'all_checks_passed' in payload['copy_acceptance_summary']
    assert 'copy_open_action_observed' in payload['copy_acceptance_summary']
    assert 'copy_open_position_observed' in payload['copy_acceptance_summary']
    assert 'technical_acceptance_summary' in payload
    assert 'all_checks_passed' in payload['technical_acceptance_summary']
    assert 'futures_long_execute' in payload['technical_acceptance_summary']
    assert 'futures_short_execute' in payload['technical_acceptance_summary']
    assert 'spot_long_execute' in payload['technical_acceptance_summary']
    assert 'spot_short_reject' in payload['technical_acceptance_summary']


def test_dashboard_api_returns_binance_technical_sections(dashboard_server: DashboardServer, tmp_path: Path):
    conn = sqlite3.connect(dashboard_server.db_path)
    cur = conn.cursor()
    now_utc = datetime.now(timezone.utc).replace(microsecond=0)
    t101 = (now_utc - timedelta(minutes=25)).strftime('%Y-%m-%d %H:%M:%S')
    t102 = (now_utc - timedelta(minutes=24)).strftime('%Y-%m-%d %H:%M:%S')
    t103 = (now_utc - timedelta(minutes=23)).strftime('%Y-%m-%d %H:%M:%S')
    t104 = (now_utc - timedelta(minutes=22)).strftime('%Y-%m-%d %H:%M:%S')
    t106 = (now_utc - timedelta(minutes=21)).strftime('%Y-%m-%d %H:%M:%S')
    t105 = (now_utc - timedelta(minutes=10)).strftime('%Y-%m-%d %H:%M:%S')
    opened_at = (now_utc - timedelta(minutes=140)).strftime('%Y-%m-%d %H:%M:%S')
    cur.execute('UPDATE venue_positions SET opened_at = ? WHERE id = 1', (opened_at,))
    cur.executemany(
        'INSERT INTO decision_audit VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        [
            (
                101,
                t101,
                'binance_futures',
                'BTC/USDT:USDT',
                'CRYPTO',
                'binance_technical_momentum',
                'binance_technical_sampling',
                'binance_technical_momentum',
                'reject',
                'score_below_threshold,futures_spread_wide,technical_alignment_weak',
                0.54,
                0.62,
                50.0,
                0.54,
                'technical',
                0,
                0,
                '[]',
                0,
                '{"symbol":"BTC/USDT:USDT","signal_direction":"LONG","force_sample":false,"technical_recovery_applied":true,"microstructure_recovery_applied":true,"spread_recovery_applied":true,"microstructure_recovery_v2_applied":true,"spread_recovery_v2_applied":true,"force_recovery_candidate":true,"effective_spread_cap_stage":"microstructure_recovery_v2","effective_spread_normalizer":0.018,"pre_microstructure_score":0.48,"post_microstructure_score":0.56,"pre_spread_recovery_score":0.48,"post_spread_recovery_score":0.56,"pre_final_score_recovery_score":0.52,"post_final_score_recovery_score":0.52,"score_gap_to_threshold":0.10,"near_threshold_candidate":true,"score_recovery_candidate":false,"score_recovery_passed":false,"final_score_recovery_applied":false,"score_recovery_quality_gate_passed":false,"score_recovery_macd_floor":0.18,"score_recovery_momentum_floor":0.15,"score_recovery_volume_floor":0.18,"final_score_recovery_bonus":0.0,"final_score_recovery_reason":"not_applicable","score_blocker_labels":["macd_drag","momentum_drag","multi_factor_drag"],"rsi_component":0.6,"macd_component":0.2,"momentum_component":0.1,"volume_component":0.5,"microstructure_component":0.3,"effective_min_score":0.62,"microstructure_candidate_floor":0.42,"force_min_score":0.5,"macd_normalizer":0.002,"momentum_normalizer":0.0065,"volume_ratio_normalizer":1.05,"score_normalization_stage":"paper_technical_v8","base_trade_size":100.0,"effective_trade_size":100.0,"remaining_position_capacity_usd":400.0,"position_capacity_sized_down":false,"open_position_count_at_decision":1,"snapshot_quality":"trusted_orderbook_book","spread_source":"orderbook","bid_source":"orderbook.bid","ask_source":"orderbook.ask","orderbook_fallback_used":true,"orderbook_repriced":true}',
            ),
            (
                102,
                t102,
                'binance_futures',
                'ETH/USDT:USDT',
                'CRYPTO',
                'binance_technical_momentum',
                'binance_technical_sampling',
                'binance_technical_momentum',
                'decision',
                None,
                0.56,
                0.62,
                60.0,
                0.56,
                'technical',
                0,
                0,
                '[]',
                0,
                '{"symbol":"ETH/USDT:USDT","signal_direction":"SHORT","force_sample":true,"technical_recovery_applied":true,"alignment_recovery_applied":true,"technical_alignment_recovered":true,"force_recovery_candidate":true,"pre_microstructure_score":0.50,"post_microstructure_score":0.50,"pre_spread_recovery_score":0.50,"post_spread_recovery_score":0.50,"pre_final_score_recovery_score":0.50,"post_final_score_recovery_score":0.56,"score_gap_to_threshold":0.0,"near_threshold_candidate":true,"score_recovery_candidate":true,"score_recovery_passed":true,"final_score_recovery_applied":true,"score_recovery_quality_gate_passed":true,"score_recovery_macd_floor":0.18,"score_recovery_momentum_floor":0.15,"score_recovery_volume_floor":0.18,"final_score_recovery_bonus":0.06,"final_score_recovery_reason":"near_threshold_components_ok","score_blocker_labels":[],"rsi_component":0.8,"macd_component":0.7,"momentum_component":0.6,"volume_component":0.5,"microstructure_component":0.9,"effective_min_score":0.54,"microstructure_candidate_floor":0.42,"macd_normalizer":0.002,"momentum_normalizer":0.0065,"volume_ratio_normalizer":1.05,"score_normalization_stage":"paper_technical_v8","base_trade_size":100.0,"effective_trade_size":50.0,"remaining_position_capacity_usd":50.0,"position_capacity_sized_down":true,"open_position_count_at_decision":1,"snapshot_quality":"trusted_info_book","spread_source":"info_book","bid_source":"info.bidPrice","ask_source":"info.askPrice","orderbook_fallback_used":false,"orderbook_repriced":false}',
            ),
            (
                103,
                t103,
                'binance_futures',
                'SOL/USDT:USDT',
                'CRYPTO',
                'binance_technical_momentum',
                'binance_technical_sampling',
                'binance_technical_momentum',
                'execute',
                None,
                0.71,
                0.62,
                40.0,
                0.71,
                'technical',
                0,
                0,
                '[]',
                0,
                '{"symbol":"SOL/USDT:USDT","signal_direction":"LONG","force_sample":false,"technical_recovery_applied":true,"pre_final_score_recovery_score":0.71,"post_final_score_recovery_score":0.71,"score_gap_to_threshold":0.0,"near_threshold_candidate":false,"score_recovery_candidate":false,"score_recovery_passed":false,"final_score_recovery_applied":false,"score_recovery_quality_gate_passed":true,"score_recovery_macd_floor":0.18,"score_recovery_momentum_floor":0.15,"score_recovery_volume_floor":0.18,"score_blocker_labels":[],"rsi_component":0.9,"macd_component":0.8,"momentum_component":0.7,"volume_component":0.6,"microstructure_component":0.8,"effective_min_score":0.54,"macd_normalizer":0.002,"momentum_normalizer":0.0065,"volume_ratio_normalizer":1.05,"score_normalization_stage":"paper_technical_v8","base_trade_size":100.0,"effective_trade_size":40.0,"remaining_position_capacity_usd":40.0,"position_capacity_sized_down":true,"open_position_count_at_decision":1,"snapshot_quality":"trusted_ticker_book","spread_source":"ticker_book","bid_source":"ticker.bid","ask_source":"ticker.ask","orderbook_fallback_used":false,"orderbook_repriced":false}',
            ),
            (
                104,
                t104,
                'binance_futures',
                'BTC/USDT:USDT',
                'CRYPTO',
                'binance_technical_momentum',
                'binance_technical_sampling',
                'binance_technical_momentum',
                'reject',
                'futures_bid_ask_missing',
                0.0,
                0.62,
                50.0,
                0.0,
                'technical',
                0,
                0,
                '[]',
                0,
                '{"symbol":"BTC/USDT:USDT","snapshot_quality":"invalid_missing_bid_ask","orderbook_fallback_used":true,"orderbook_repriced":false}',
            ),
            (
                106,
                t106,
                'binance_futures',
                'ETH/USDT:USDT',
                'CRYPTO',
                'binance_technical_momentum',
                'binance_technical_sampling',
                'binance_technical_momentum',
                'reject',
                'max_position_exceeded',
                0.0,
                0.62,
                50.0,
                0.0,
                'technical',
                0,
                0,
                '[]',
                0,
                '{"symbol":"ETH/USDT:USDT"}',
            ),
            (
                105,
                t105,
                'binance_futures',
                'SOL/USDT:USDT',
                'CRYPTO',
                'binance_technical_momentum',
                'binance_technical_sampling',
                'binance_technical_momentum',
                'exit',
                'TAKE_PROFIT',
                0.0,
                None,
                40.0,
                0.0,
                'technical',
                0,
                0,
                '[]',
                0,
                '{"symbol":"SOL/USDT:USDT","reason":"TAKE_PROFIT"}',
            ),
        ],
    )
    cur.executemany(
        'INSERT INTO trades VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        [
            (
                301,
                'binance_futures',
                'futures',
                'SOL/USDT:USDT',
                'LONG',
                40.0,
                100.0,
                0.71,
                'binance_technical_momentum',
                'CRYPTO',
                'binance_technical_sampling',
                'live_paper',
                0,
                'CLOSED_WIN',
                45.5,
                None,
                t103,
            ),
            (
                302,
                'binance_spot',
                'spot',
                'BTC/USDT',
                'LONG',
                25.0,
                100.0,
                0.62,
                'binance_technical_momentum',
                'CRYPTO',
                'binance_technical_sampling',
                'live_paper',
                0,
                'CLOSED_LOSS',
                -10.0,
                None,
                t102,
            ),
        ],
    )
    conn.commit()
    conn.close()

    script_path = tmp_path / 'technical_probe.php'
    script_path.write_text(
        "\n".join(
            [
                "<?php",
                "declare(strict_types=1);",
                f"putenv('GHOST_TRADER_REPO_ROOT={REPO_ROOT.as_posix()}');",
                f"putenv('GHOST_TRADER_DB_PATH={dashboard_server.db_path.as_posix()}');",
                "require 'dashboard/public/presenter.php';",
                "$warnings = [];",
                "$pdo = dashboard_open_db($warnings);",
                "$rows = dashboard_fetch_recent_binance_technical_rows($pdo);",
                "$freshRows = dashboard_filter_rows_within_minutes($rows, 60);",
                "$runtimeSummary = [",
                "    'technical_open_positions_total' => 2,",
                "    'technical_open_positions_strict' => 1,",
                "    'technical_open_positions_legacy' => 1,",
                "    'technical_open_positions_backfilled' => 1,",
                "    'technical_open_positions_ineligible' => 0,",
                "    'technical_open_positions_rescue' => 1,",
                "    'technical_stale_review_candidates_90m' => 1,",
                "    'technical_stale_exit_candidates_120m' => 1,",
                "    'technical_stale_hard_timeout_candidates_240m' => 1,",
                "    'technical_stale_exit_executed' => 1,",
                "    'technical_stale_hard_timeout_executed' => 1,",
                "    'technical_stale_exit_skipped_alignment_support' => 1,",
                "    'technical_stale_exit_skipped_recent_support' => 1,",
                "    'technical_stale_exit_skipped_profit_protection' => 1,",
                "];",
                "echo json_encode([",
                "    'summary' => dashboard_build_binance_technical_summary_from_rows($rows),",
                "    'fresh_summary' => dashboard_build_binance_technical_summary_from_rows($freshRows),",
                "    'gate_funnel' => dashboard_build_binance_technical_gate_funnel_from_rows($rows),",
                "    'fresh_gate_funnel' => dashboard_build_binance_technical_gate_funnel_from_rows($freshRows),",
                "    'recovery_summary' => dashboard_build_binance_technical_recovery_summary_from_rows($rows),",
                "    'fresh_recovery_summary' => dashboard_build_binance_technical_recovery_summary_from_rows($freshRows),",
                "    'snapshot_summary' => dashboard_build_binance_futures_snapshot_summary_from_rows($rows),",
                "    'component_summary' => dashboard_build_binance_technical_score_component_summary_from_rows($rows),",
                "    'gap_summary' => dashboard_build_binance_technical_score_gap_summary_from_rows($rows),",
                "    'fresh_gap_summary' => dashboard_build_binance_technical_score_gap_summary_from_rows($freshRows),",
                "    'fresh_pnl' => dashboard_build_binance_fresh_pnl_summary($pdo),",
                "    'stale_eligibility_summary' => dashboard_build_binance_technical_stale_eligibility_summary($runtimeSummary),",
                "    'position_pressure_summary' => dashboard_build_binance_technical_position_pressure_summary($pdo, $runtimeSummary, $rows),",
                "    'score_blocker_breakdown' => dashboard_build_binance_technical_score_blocker_breakdown_from_rows($rows),",
                "    'reject_breakdown' => dashboard_build_binance_technical_reject_breakdown_from_rows($rows),",
                "    'fresh_reject_breakdown' => dashboard_build_binance_technical_reject_breakdown_from_rows($freshRows),",
                "], JSON_THROW_ON_ERROR);",
            ]
        ),
        encoding='utf-8',
    )

    completed = subprocess.run(
        [PHP_BIN, str(script_path)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=True,
    )
    payload = json.loads(completed.stdout)

    assert payload['summary'] == {
        'decisions': 1,
        'rejects': 3,
        'executes': 1,
        'long_signals': 2,
        'short_signals': 1,
        'forced_samples': 1,
    }
    assert payload['fresh_summary'] == payload['summary']
    technical_breakdown = {row['reason']: row['count'] for row in payload['reject_breakdown']}
    assert technical_breakdown['score_below_threshold'] == 1
    assert technical_breakdown['futures_spread_wide'] == 1
    assert technical_breakdown['technical_alignment_weak'] == 1
    assert technical_breakdown['futures_bid_ask_missing'] == 1
    assert technical_breakdown['max_position_exceeded'] == 1
    assert payload['gate_funnel'] == {
        'scanned_symbols': 3,
        'directional_signals': 3,
        'recovered_alignment_signals': 1,
        'spread_rejects': 1,
        'score_rejects': 1,
        'decisions': 1,
        'executes': 1,
    }
    assert payload['fresh_gate_funnel'] == payload['gate_funnel']
    assert payload['recovery_summary'] == {
        'recovery_applied_count': 3,
        'alignment_recovery_hits': 1,
        'microstructure_recovery_hits': 1,
        'spread_recovery_hits': 1,
        'microstructure_recovery_v2_hits': 1,
        'spread_recovery_v2_hits': 1,
        'force_recovery_candidates': 2,
        'microstructure_candidate_floor_hits': 1,
        'force_sample_hits': 1,
        'final_score_recovery_hits': 1,
        'near_threshold_candidates': 2,
        'score_recovery_candidates': 1,
        'score_recovery_passes': 1,
        'active_symbol_count': 3,
    }
    assert payload['fresh_recovery_summary'] == payload['recovery_summary']
    assert payload['snapshot_summary'] == {
        'trusted_ticker_book_hits': 1,
        'trusted_info_book_hits': 1,
        'trusted_orderbook_book_hits': 1,
        'orderbook_fallback_hits': 2,
        'orderbook_reprice_hits': 1,
        'missing_bid_ask_rejects': 1,
        'snapshot_untrusted_rejects': 0,
    }
    assert payload['component_summary'] == {
        'sample_count': 3,
        'avg_rsi_component': pytest.approx(0.7667),
        'avg_macd_component': pytest.approx(0.5667),
        'avg_momentum_component': pytest.approx(0.4667),
        'avg_volume_component': pytest.approx(0.5333),
        'avg_microstructure_component': pytest.approx(0.6667),
        'avg_macd_normalizer': pytest.approx(0.0020),
        'avg_momentum_normalizer': pytest.approx(0.0065),
        'avg_volume_ratio_normalizer': pytest.approx(1.05),
        'avg_effective_min_score': pytest.approx(0.5667),
        'avg_final_score': pytest.approx(0.5967),
    }
    assert payload['gap_summary'] == {
        'avg_score_gap_to_threshold': pytest.approx(0.0333),
        'below_threshold_count': 1,
        'near_threshold_count': 1,
        'deep_below_threshold_count': 0,
    }
    assert payload['fresh_gap_summary'] == payload['gap_summary']
    assert payload['fresh_pnl']['fresh_trade_count'] == 2
    assert payload['fresh_pnl']['fresh_closed_trades'] == 2
    assert payload['fresh_pnl']['net_pnl'] == pytest.approx(35.5)
    assert payload['fresh_pnl']['venues']['binance_futures']['net_pnl'] == pytest.approx(45.5)
    assert payload['fresh_pnl']['venues']['binance_futures']['execute_count'] == 1
    assert payload['fresh_pnl']['venues']['binance_spot']['net_pnl'] == pytest.approx(-10.0)
    assert payload['fresh_pnl']['venues']['binance_spot']['execute_count'] == 0
    assert payload['stale_eligibility_summary'] == {
        'stale_review_runs': 0,
        'open_positions_seen_by_stale_review': 0,
        'technical_open_positions_total': 2,
        'technical_open_positions_strict': 1,
        'technical_open_positions_legacy': 1,
        'technical_open_positions_backfilled': 1,
        'technical_open_positions_ineligible': 0,
        'technical_open_positions_rescue': 1,
        'stale_review_skipped': 'none',
    }
    assert payload['position_pressure_summary'] == {
        'open_positions': 1,
        'open_notional_usd': pytest.approx(100.0),
        'remaining_capacity_usd': pytest.approx(350.0),
        'recent_exits_60m': 1,
        'stop_loss_exits_60m': 0,
        'take_profit_exits_60m': 1,
        'oldest_open_position_minutes': pytest.approx(130.0, abs=1.0),
        'positions_over_30m': 1,
        'positions_over_60m': 1,
        'positions_over_120m': 1,
        'positions_over_240m': 0,
        'max_open_positions_rejects': 0,
        'max_total_position_usd_rejects': 0,
        'max_order_usd_rejects': 0,
        'legacy_max_position_exceeded_rejects': 1,
        'sized_down_entries': 2,
        'stale_review_candidates_90m': 1,
        'stale_exit_candidates_120m': 1,
        'stale_hard_timeout_candidates_240m': 1,
        'stale_exit_executed': 1,
        'stale_hard_timeout_executed': 1,
        'stale_exit_skipped_alignment_support': 1,
        'stale_exit_skipped_recent_support': 1,
        'stale_exit_skipped_profit_protection': 1,
        'capacity_released_usd_60m': pytest.approx(40.0),
    }
    score_blockers = {row['reason']: row['count'] for row in payload['score_blocker_breakdown']}
    assert score_blockers['macd_drag'] == 1
    assert score_blockers['momentum_drag'] == 1
    assert score_blockers['multi_factor_drag'] == 1
    assert payload['fresh_reject_breakdown'] == payload['reject_breakdown']


def test_dashboard_prefers_runtime_status_snapshot_when_present(dashboard_server: DashboardServer):
    metrics = {
        'mapped_orderflow_events': 7,
        'unmapped_orderflow_events': 1,
        'market_not_mapped_rate': 12.5,
        'alias_cache_hits': 4,
        'lazy_lookup_hits': 2,
        'hot_window_markets': 3,
        'hot_window_hits': 2,
        'hot_window_promotions': 5,
        'hot_window_expiries': 1,
        'active_window_misses': 1,
        'active_window_miss_rate': 12.5,
        'resolver_hit_rate': 85.7,
        'lookup_universe_markets': 25,
        'lookup_universe_aliases': 140,
        'persisted_market_alias_rows': 2,
        'persisted_market_alias_markets': 1,
        'hydrated_lookup_markets': 21,
        'hydrated_lookup_aliases': 120,
        'lookup_hydration_warning': 'persisted_alias_rows_present_but_lookup_hydration_zero',
        'alias_persistence_gap': 138,
        'graph_clusters_promoted': 2,
        'graph_skipped_missing_market_ref': 1,
        'graph_skipped_single_wallet': 3,
        'graph_skipped_low_notional': 4,
        'whale_copy_accumulator_buckets': 2,
        'whale_copy_gate_ready_candidates': 1,
        'whale_copy_retry_candidates': 1,
        'whale_copy_accumulated_buy_events': 3,
        'whale_copy_accumulated_total_notional': 425.5,
        'recent_gate_ready_candidates': [
            {
                'market_id': 'market-snapshot',
                'total_amount': 425.5,
                'unique_wallets': 2,
                'event_count': 3,
                'max_trust': 0.82,
                'gate_ready_reason': 'event_count',
                'retry_cooldown_applied': False,
                'last_reject_reason': 'slippage_guard_rejection',
            }
        ],
        'sampling_mode': 'enabled',
        'sampling_closed_trades': 1,
        'sampling_target_closed_trades': 20,
        'sampling_stop_reason': 'none',
    }
    conn = sqlite3.connect(dashboard_server.db_path)
    cur = conn.cursor()
    cur.execute(
        'INSERT OR REPLACE INTO runtime_status_snapshot (id, updated_at, metrics_json) VALUES (1, ?, ?)',
        ('2026-04-09 12:00:00', json.dumps(metrics)),
    )
    conn.commit()
    conn.close()

    response = _request(dashboard_server.base_url + '/api.php', auth=(DASHBOARD_USER, DASHBOARD_PASSWORD))
    payload = json.loads(response.read().decode('utf-8'))

    assert payload['runtime_summary']['live_metrics_available'] is True
    assert payload['runtime_summary']['market_not_mapped_rate'] == 12.5
    assert payload['runtime_summary']['recent_market_not_mapped_rate'] == 20.0
    assert payload['runtime_summary']['historical_market_not_mapped_rate'] == 20.0
    assert payload['runtime_summary']['hydrated_lookup_markets'] == 21
    assert payload['runtime_summary']['hydrated_lookup_aliases'] == 120
    assert payload['runtime_summary']['lookup_hydration_warning'] == 'persisted_alias_rows_present_but_lookup_hydration_zero'
    assert payload['alias_persistence_summary']['hydrated_lookup_markets'] == 21
    assert payload['alias_persistence_summary']['warning'] == 'Kalici alias cache dolu ama canli lookup hydration sifir gorunuyor.'
    assert payload['graph_discovery_summary']['graph_clusters_promoted'] == 2
    assert payload['graph_discovery_summary']['graph_skipped_missing_market_ref'] == 1
    assert payload['whale_candidate_aggregation_summary']['accumulator_buckets'] == 2
    assert payload['whale_candidate_aggregation_summary']['gate_ready_candidates'] == 1
    assert payload['whale_candidate_aggregation_summary']['retry_candidates'] == 1
    assert payload['whale_candidate_aggregation_summary']['accumulated_buy_events'] == 3
    assert payload['whale_candidate_aggregation_summary']['accumulated_total_notional'] == 425.5
    assert payload['recent_gate_ready_candidates'][0]['market_id'] == 'market-snapshot'
    assert payload['recent_gate_ready_candidates'][0]['last_reject_reason'] == 'slippage_guard_rejection'
    assert any('canli lookup hydration sifir gorunuyor' in warning.lower() for warning in payload['warnings'])


def test_dashboard_index_renders_with_auth(dashboard_server: DashboardServer):
    response = _request(dashboard_server.base_url + '/index.php', auth=(DASHBOARD_USER, DASHBOARD_PASSWORD))
    html = response.read().decode('utf-8')
    assert 'WhaleSignal' in html
    assert 'WhaleSignal Operations' in html
    assert 'Kontrol Merkezi' in html
    assert 'Polymarket' in html
    assert 'Binance' in html
    assert 'assets/operator.css' in html
    assert 'assets/operator.js' in html
    assert '"laneMode":"split"' in html
    assert 'Operator hedefi' in html
    return
    assert 'Ana Panel' in html
    assert 'Detay / Teknik Kanit' in html
    assert ('Sozluk / Aciklamalar' in html) or ('Sözlük / Açıklamalar' in html) or ('SÃ¶zlÃ¼k / AÃ§Ä±klamalar' in html)
    assert 'Bu sekme neyi gosteriyor?' in html
    assert 'Polymarket Paper Trader Paneli' in html
    assert 'Binance Paper Trader Paneli' in html
    assert 'Cuzdan / Copy Durumu' in html
    assert 'Paper Copy Performansi' in html
    assert 'Paper Bakiye / PnL' in html
    assert 'Sistem Calisma Kaniti' in html
    assert 'Calisma Kaniti' in html
    assert 'Discovery Hunisi' in html
    assert 'Discovery Wallet Ozeti' in html
    assert 'Shadow Cohort Ozeti' in html
    assert 'Copy-ready Ozeti' in html
    assert 'Shadow Edge Ozeti' in html
    assert 'Kaynak Dagilimi' in html
    assert 'Shadow Terfi Ozeti' in html
    assert 'Cuzdan Provenance' in html
    assert 'Oncelikli Izleme Listesi' in html
    assert 'Manual link' in html
    assert 'Kimlik Cozumleme Durumu' in html
    assert 'Shadow Replay Ozeti' in html
    assert 'Cuzdan Tutarlilik Tablosu' in html
    assert 'Copy-ready Kisa Liste' in html
    assert 'Son Shadow Aksiyonlari' in html
    assert 'Paper Copy Ozeti' in html
    assert 'Acceptance Kaniti' in html
    assert 'Aktif Paper Copy Pozisyonlari' in html
    assert 'Copy Red Nedenleri' in html
    assert 'Wallet Follower PnL' in html
    assert 'Son Copy Aksiyonlari' in html
    assert 'Ana Kaynak' in html
    assert 'Kaynak Etiketleri' in html
    assert 'Shadow Durumu' in html
    assert 'Shadow Nedeni' in html
    assert 'Oncelik' in html
    assert 'Profil' in html
    assert 'Watchlist Durumu' in html
    assert 'Kimlik Durumu' in html
    assert 'Fresh 7g Paper PnL' in html
    assert 'Futures LONG execute' in html
    assert 'Spot SHORT reject' in html
    assert 'Fresh PnL Ozeti' in html
    assert 'Fresh Teknik Ozet' in html
    assert 'Fresh Teknik Red Nedenleri' in html
    assert 'Skor Kalite Ozeti' in html
    assert 'Score Component Ozeti' in html
    assert 'Score Gap Ozeti' in html
    assert 'Score Blocker Dagilimi' in html
    assert 'Pozisyon Baskisi ve Legacy Durum' in html
    assert 'Position Pressure' in html
    assert 'Legacy Position Ozeti' in html
    assert 'Acik Pozisyonlar' in html
    assert 'Acik Emirler' in html
    assert 'Sozluk ve Log' in html
    assert 'Terimler ve Aciklamalar' in html
    assert 'Plain Turkish' in html
    assert 'Servis Log Ozeti' in html
    assert 'Shadow edge' in html
    return
    assert 'Son Kararlar' in html
    assert 'Sampling modu' in html
    assert 'İşlem ve Karar Akışı' in html
    assert 'Teşhis ve Kanıt' in html
    assert 'Keşif skoru, whale adresini sıralamak için kullanılır; başarı oranı değildir.' in html
    assert 'Keşif Skoru' in html
    assert 'Güven Skoru' in html
    assert 'Kazanma Oranı' in html
    assert 'Toplam PnL' in html
    assert 'Sampling Red Nedenleri' in html
    assert 'Mapping Miss Nedenleri' in html
    assert 'Sampling Karar' in html
    assert 'Kaynak Kalitesi' in html
    assert 'Alias Cache' in html
    assert 'Desteklenmeyen' in html
    assert 'Balina Evreni' in html
    assert 'Kanitli Balinalar' in html
    assert 'Whale-Copy' in html


def test_dashboard_research_lane_mode_hides_binance_tab_and_skips_report_warnings(tmp_path: Path):
    if PHP_BIN is None:
        pytest.skip('php is not available in PATH')

    db_path = tmp_path / 'dashboard.db'
    _create_dashboard_db(db_path)
    port = _find_free_port()
    env = os.environ.copy()
    env.update(
        {
            'GHOST_TRADER_REPO_ROOT': str(REPO_ROOT),
            'GHOST_TRADER_DB_PATH': str(db_path),
            'DASHBOARD_USER': DASHBOARD_USER,
            'DASHBOARD_PASSWORD_HASH': DASHBOARD_HASH,
            'DASHBOARD_REFRESH_SECONDS': '1',
            'DASHBOARD_LOG_LINES': '5',
            'DASHBOARD_LANE_MODE': 'polymarket_research',
        }
    )

    process = subprocess.Popen(
        [PHP_BIN, '-S', f'127.0.0.1:{port}', '-t', str(REPO_ROOT / 'dashboard' / 'public')],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base_url = f'http://127.0.0.1:{port}'
    try:
        started = False
        for _ in range(40):
            try:
                _request(base_url + '/api.php', auth=(DASHBOARD_USER, DASHBOARD_PASSWORD)).close()
                started = True
                break
            except Exception:
                time.sleep(0.1)
        assert started

        response = _request(base_url + '/api.php', auth=(DASHBOARD_USER, DASHBOARD_PASSWORD))
        payload = json.loads(response.read().decode('utf-8'))
        assert not any('performance report unavailable' in warning.lower() for warning in payload['warnings'])
        assert not any('swot report unavailable' in warning.lower() for warning in payload['warnings'])

        response = _request(base_url + '/', auth=(DASHBOARD_USER, DASHBOARD_PASSWORD))
        html = response.read().decode('utf-8')
        assert 'lane-polymarket-research' in html
        assert 'WhaleSignal / Polymarket' in html
        assert 'Polymarket Copy Trade' in html
        assert '"laneMode":"polymarket_research"' in html
        assert '#tracked-wallets' in html
        assert '#whale-feed' in html
        return
        assert 'DASHBOARD_TABS = ["polymarket-research","polymarket-copy","sozluk-aciklamalar"]' in html
        assert "const DEFAULT_TAB = DASHBOARD_TABS[0] || 'polymarket-research';" in html
        assert 'Detay / Teknik Kanit' in html
        assert 'Polymarket Research paneli: aday cüzdan, shadow takip ve copy-ready kanıtı.' in html
        assert 'Binance Paper Trader Paneli' not in html
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def test_dashboard_binance_lane_mode_uses_binance_default_tab(tmp_path: Path):
    if PHP_BIN is None:
        pytest.skip('php is not available in PATH')

    db_path = tmp_path / 'dashboard.db'
    _create_dashboard_db(db_path)
    port = _find_free_port()
    env = os.environ.copy()
    env.update(
        {
            'GHOST_TRADER_REPO_ROOT': str(REPO_ROOT),
            'GHOST_TRADER_DB_PATH': str(db_path),
            'DASHBOARD_USER': DASHBOARD_USER,
            'DASHBOARD_PASSWORD_HASH': DASHBOARD_HASH,
            'DASHBOARD_REFRESH_SECONDS': '1',
            'DASHBOARD_LOG_LINES': '5',
            'DASHBOARD_LANE_MODE': 'binance_technical',
        }
    )

    process = subprocess.Popen(
        [PHP_BIN, '-S', f'127.0.0.1:{port}', '-t', str(REPO_ROOT / 'dashboard' / 'public')],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base_url = f'http://127.0.0.1:{port}'
    try:
        started = False
        for _ in range(40):
            try:
                _request(base_url + '/api.php', auth=(DASHBOARD_USER, DASHBOARD_PASSWORD)).close()
                started = True
                break
            except Exception:
                time.sleep(0.1)
        assert started

        response = _request(base_url + '/', auth=(DASHBOARD_USER, DASHBOARD_PASSWORD))
        html = response.read().decode('utf-8')
        assert 'lane-binance-technical' in html
        assert 'WhaleSignal / Binance' in html
        assert 'Binance Trading Operations' in html
        assert '"laneMode":"binance_technical"' in html
        assert '#open-positions' in html
        assert '#runtime-feed' in html
        return
        assert 'DASHBOARD_TABS = ["binance-technical","sozluk-aciklamalar"]' in html
        assert "const DEFAULT_TAB = DASHBOARD_TABS[0] || 'polymarket-research';" in html
        assert 'Binance Technical paneli: fresh paper PnL, skor kalitesi ve pozisyon baskisi.' in html
        assert 'Binance Paper Trader Paneli' in html
        assert 'Polymarket Paper Trader Paneli' not in html
        assert 'Detay / Teknik Kanit' in html
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def test_dashboard_api_degrades_without_runtime_files(tmp_path: Path):
    if PHP_BIN is None:
        pytest.skip('php is not available in PATH')

    port = _find_free_port()
    env = os.environ.copy()
    env.update(
        {
            'GHOST_TRADER_REPO_ROOT': str(REPO_ROOT),
            'GHOST_TRADER_DB_PATH': str(tmp_path / 'missing.db'),
            'DASHBOARD_SUMMARY_PATH': str(tmp_path / 'missing-summary.json'),
            'DASHBOARD_SWOT_PATH': str(tmp_path / 'missing-swot.json'),
            'DASHBOARD_USER': DASHBOARD_USER,
            'DASHBOARD_PASSWORD_HASH': DASHBOARD_HASH,
            'DASHBOARD_REFRESH_SECONDS': '1',
        }
    )

    process = subprocess.Popen(
        [PHP_BIN, '-S', f'127.0.0.1:{port}', '-t', str(REPO_ROOT / 'dashboard' / 'public')],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base_url = f'http://127.0.0.1:{port}'
    try:
        for _ in range(40):
            try:
                response = _request(base_url + '/api.php', auth=(DASHBOARD_USER, DASHBOARD_PASSWORD))
                payload = json.loads(response.read().decode('utf-8'))
                break
            except Exception:
                time.sleep(0.1)
        else:
            pytest.fail('dashboard php server did not start in time for degrade test')

        assert payload['runtime_summary']['total_trades'] == 0
        assert payload['warnings']
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def test_vps_scripts_reference_dashboard_flow():
    refresh_script = (REPO_ROOT / 'scripts' / 'vps_refresh_and_evaluate.sh').read_text(encoding='utf-8')
    bootstrap_script = (REPO_ROOT / 'scripts' / 'bootstrap_vps.sh').read_text(encoding='utf-8')
    assert 'ghost-trader-dashboard' in refresh_script
    assert 'start_dashboard.sh' in bootstrap_script
