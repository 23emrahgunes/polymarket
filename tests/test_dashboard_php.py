from __future__ import annotations

import base64
import json
import os
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
        'CREATE TABLE whale_wallets (address TEXT, source_type TEXT, enabled INTEGER, discovery_score REAL, last_event_amount REAL, event_count_24h INTEGER, failure_streak INTEGER, last_seen_at TEXT)'
    )
    cur.executemany(
        'INSERT INTO whale_wallets VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
        [
            ('0xaaa', 'leaderboard', 1, 0.91, 12000.0, 5, 0, '2026-04-09 10:00:00'),
            ('0xbbb', 'activity_discovery', 1, 0.72, 8000.0, 3, 1, '2026-04-09 10:00:00'),
            ('0xccc', 'graph_discovery', 1, 0.68, 5500.0, 2, 0, '2026-04-09 10:00:00'),
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


def test_dashboard_api_returns_binance_technical_sections(dashboard_server: DashboardServer, tmp_path: Path):
    conn = sqlite3.connect(dashboard_server.db_path)
    cur = conn.cursor()
    cur.executemany(
        'INSERT INTO decision_audit VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        [
            (
                101,
                '2026-04-09 12:10:00',
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
                '{"symbol":"BTC/USDT:USDT","signal_direction":"LONG","force_sample":false,"technical_recovery_applied":true,"microstructure_recovery_applied":true,"spread_recovery_applied":true,"microstructure_recovery_v2_applied":true,"spread_recovery_v2_applied":true,"force_recovery_candidate":true,"effective_spread_cap_stage":"microstructure_recovery_v2","effective_spread_normalizer":0.018,"pre_microstructure_score":0.48,"post_microstructure_score":0.56,"pre_spread_recovery_score":0.48,"post_spread_recovery_score":0.56,"pre_final_score_recovery_score":0.52,"post_final_score_recovery_score":0.52,"score_gap_to_threshold":0.10,"near_threshold_candidate":true,"score_recovery_candidate":false,"score_recovery_passed":false,"final_score_recovery_applied":false,"score_recovery_quality_gate_passed":false,"score_recovery_macd_floor":0.18,"score_recovery_momentum_floor":0.18,"score_recovery_volume_floor":0.22,"final_score_recovery_bonus":0.0,"final_score_recovery_reason":"not_applicable","score_blocker_labels":["macd_drag","momentum_drag","multi_factor_drag"],"rsi_component":0.6,"macd_component":0.2,"momentum_component":0.1,"volume_component":0.5,"microstructure_component":0.3,"effective_min_score":0.62,"microstructure_candidate_floor":0.42,"force_min_score":0.5,"macd_normalizer":0.0022,"momentum_normalizer":0.008,"volume_ratio_normalizer":1.2,"score_normalization_stage":"paper_technical_v7","base_trade_size":100.0,"effective_trade_size":100.0,"remaining_position_capacity_usd":400.0,"position_capacity_sized_down":false,"open_position_count_at_decision":1,"snapshot_quality":"trusted_orderbook_book","spread_source":"orderbook","bid_source":"orderbook.bid","ask_source":"orderbook.ask","orderbook_fallback_used":true,"orderbook_repriced":true}',
            ),
            (
                102,
                '2026-04-09 12:11:00',
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
                '{"symbol":"ETH/USDT:USDT","signal_direction":"SHORT","force_sample":true,"technical_recovery_applied":true,"alignment_recovery_applied":true,"technical_alignment_recovered":true,"force_recovery_candidate":true,"pre_microstructure_score":0.50,"post_microstructure_score":0.50,"pre_spread_recovery_score":0.50,"post_spread_recovery_score":0.50,"pre_final_score_recovery_score":0.50,"post_final_score_recovery_score":0.56,"score_gap_to_threshold":0.0,"near_threshold_candidate":true,"score_recovery_candidate":true,"score_recovery_passed":true,"final_score_recovery_applied":true,"score_recovery_quality_gate_passed":true,"score_recovery_macd_floor":0.18,"score_recovery_momentum_floor":0.18,"score_recovery_volume_floor":0.22,"final_score_recovery_bonus":0.06,"final_score_recovery_reason":"near_threshold_components_ok","score_blocker_labels":[],"rsi_component":0.8,"macd_component":0.7,"momentum_component":0.6,"volume_component":0.5,"microstructure_component":0.9,"effective_min_score":0.54,"microstructure_candidate_floor":0.42,"macd_normalizer":0.0022,"momentum_normalizer":0.008,"volume_ratio_normalizer":1.2,"score_normalization_stage":"paper_technical_v7","base_trade_size":100.0,"effective_trade_size":50.0,"remaining_position_capacity_usd":50.0,"position_capacity_sized_down":true,"open_position_count_at_decision":1,"snapshot_quality":"trusted_info_book","spread_source":"info_book","bid_source":"info.bidPrice","ask_source":"info.askPrice","orderbook_fallback_used":false,"orderbook_repriced":false}',
            ),
            (
                103,
                '2026-04-09 12:12:00',
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
                '{"symbol":"SOL/USDT:USDT","signal_direction":"LONG","force_sample":false,"technical_recovery_applied":true,"pre_final_score_recovery_score":0.71,"post_final_score_recovery_score":0.71,"score_gap_to_threshold":0.0,"near_threshold_candidate":false,"score_recovery_candidate":false,"score_recovery_passed":false,"final_score_recovery_applied":false,"score_recovery_quality_gate_passed":true,"score_recovery_macd_floor":0.18,"score_recovery_momentum_floor":0.18,"score_recovery_volume_floor":0.22,"score_blocker_labels":[],"rsi_component":0.9,"macd_component":0.8,"momentum_component":0.7,"volume_component":0.6,"microstructure_component":0.8,"effective_min_score":0.54,"macd_normalizer":0.0022,"momentum_normalizer":0.008,"volume_ratio_normalizer":1.2,"score_normalization_stage":"paper_technical_v7","base_trade_size":100.0,"effective_trade_size":40.0,"remaining_position_capacity_usd":40.0,"position_capacity_sized_down":true,"open_position_count_at_decision":1,"snapshot_quality":"trusted_ticker_book","spread_source":"ticker_book","bid_source":"ticker.bid","ask_source":"ticker.ask","orderbook_fallback_used":false,"orderbook_repriced":false}',
            ),
            (
                104,
                '2026-04-09 12:13:00',
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
                105,
                '2026-04-09 12:20:00',
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
                "echo json_encode([",
                "    'summary' => dashboard_build_binance_technical_summary($pdo),",
                "    'gate_funnel' => dashboard_build_binance_technical_gate_funnel($pdo),",
                "    'recovery_summary' => dashboard_build_binance_technical_recovery_summary($pdo),",
                "    'snapshot_summary' => dashboard_build_binance_futures_snapshot_summary($pdo),",
                "    'component_summary' => dashboard_build_binance_technical_score_component_summary($pdo),",
                "    'gap_summary' => dashboard_build_binance_technical_score_gap_summary($pdo),",
                "    'position_pressure_summary' => dashboard_build_binance_technical_position_pressure_summary($pdo),",
                "    'score_blocker_breakdown' => dashboard_build_binance_technical_score_blocker_breakdown($pdo),",
                "    'reject_breakdown' => dashboard_build_binance_technical_reject_breakdown($pdo),",
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
        'rejects': 2,
        'executes': 1,
        'long_signals': 2,
        'short_signals': 1,
        'forced_samples': 1,
    }
    technical_breakdown = {row['reason']: row['count'] for row in payload['reject_breakdown']}
    assert technical_breakdown['score_below_threshold'] == 1
    assert technical_breakdown['futures_spread_wide'] == 1
    assert technical_breakdown['technical_alignment_weak'] == 1
    assert technical_breakdown['futures_bid_ask_missing'] == 1
    assert payload['gate_funnel'] == {
        'scanned_symbols': 3,
        'directional_signals': 3,
        'recovered_alignment_signals': 1,
        'spread_rejects': 1,
        'score_rejects': 1,
        'decisions': 1,
        'executes': 1,
    }
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
        'avg_macd_normalizer': pytest.approx(0.0022),
        'avg_momentum_normalizer': pytest.approx(0.0080),
        'avg_volume_ratio_normalizer': pytest.approx(1.2),
        'avg_effective_min_score': pytest.approx(0.5667),
        'avg_final_score': pytest.approx(0.5967),
    }
    assert payload['gap_summary'] == {
        'avg_score_gap_to_threshold': pytest.approx(0.0333),
        'below_threshold_count': 1,
        'near_threshold_count': 1,
        'deep_below_threshold_count': 0,
    }
    assert payload['position_pressure_summary'] == {
        'open_positions': 1,
        'open_notional_usd': pytest.approx(100.0),
        'remaining_capacity_usd': pytest.approx(350.0),
        'recent_exits_60m': 1,
        'stop_loss_exits_60m': 0,
        'take_profit_exits_60m': 1,
        'max_open_positions_rejects': 0,
        'max_total_position_usd_rejects': 0,
        'max_order_usd_rejects': 0,
        'sized_down_entries': 2,
    }
    score_blockers = {row['reason']: row['count'] for row in payload['score_blocker_breakdown']}
    assert score_blockers['macd_drag'] == 1
    assert score_blockers['momentum_drag'] == 1
    assert score_blockers['multi_factor_drag'] == 1


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
    assert 'Ghost Trader Operasyon Paneli' in html
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
    assert 'Whale Side Ozeti' in html
    assert 'Whale-Copy Gate Funnel' in html
    assert 'Graph Discovery Ozeti' in html
    assert 'Whale Candidate Birikimi' in html
    assert 'Gate-ready Whale Adaylari' in html
    assert 'Whale-copy recovery' in html
    assert 'Gated Whale-Copy Red Nedenleri' in html
    assert 'Relaxed Gate Sonrasi Kalan Red Nedenleri' in html
    assert 'Henüz kapanmış whale geçmişi yok; nötr güven.' in html
    assert 'Binance teknik sampling' in html
    assert 'Binance teknik gate funnel' in html
    assert 'Binance teknik recovery ozeti' in html
    assert 'Futures snapshot ozeti' in html
    assert 'Mikro yapi recovery hit' in html
    assert 'Mikro yapi v2 hit' in html
    assert 'Spread v2 hit' in html
    assert 'Final skor recovery hit' in html
    assert 'Skor bilesen ozeti' in html
    assert 'Skor gap ozeti' in html
    assert 'Pozisyon Baskisi ve Exit Akisi' in html
    assert 'Ortalama MACD normalizer' in html
    assert 'Ortalama momentum normalizer' in html
    assert 'Ortalama hacim normalizer' in html
    assert 'Teknik Skor Blocker Dagilimi' in html
    assert 'Candidate floor hit' in html
    assert 'Orderbook repriced' in html
    assert 'Binance Teknik Red Nedenleri' in html
    assert '&mdash;' in html


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
