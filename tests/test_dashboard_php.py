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
        'CREATE TABLE decision_audit (id INTEGER PRIMARY KEY, occurred_at TEXT, venue TEXT, market_id TEXT, category TEXT, signal_family TEXT, strategy_profile TEXT, raw_source_signal TEXT, action TEXT, reason TEXT, decision_score REAL, threshold REAL, trade_size REAL, confidence REAL, mapping_stage TEXT, lazy_lookup_attempted INTEGER, lazy_lookup_hit INTEGER, alias_candidates_json TEXT, hot_window_promoted INTEGER DEFAULT 0)'
    )
    cur.executemany(
        'INSERT INTO decision_audit VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        [
            (1, '2026-04-09 10:00:00', 'polymarket', 'market-1', 'SPORTS', 'activity_orderflow', 'baseline', 'activity', 'reject', 'liquidity_guard', 0.67, 0.72, 40.0, 0.67, 'alias_cache', 0, 0, '["token-1","condition-1"]', 0),
            (2, '2026-04-09 10:01:00', 'polymarket', 'market-2', 'OTHER', 'whale', 'baseline', 'whale_tracker', 'reject', 'market_not_mapped_active_window', 0.0, 0.78, 25.0, 0.0, 'active_window', 0, 0, '["mystery-token","mystery-market"]', 0),
            (3, '2026-04-09 10:02:00', 'polymarket', 'market-3', 'SPORTS', 'whale', 'sampling_relaxed', 'whale_tracker', 'decision', 'score_below_threshold', 0.71, 0.72, 40.0, 0.71, 'hot_window', 1, 1, '["hot-token","hot-market"]', 1),
            (4, '2026-04-09 10:03:00', 'polymarket', 'market-4', 'POLITICS', 'whale', 'sampling_relaxed', 'activity', 'reject', 'slippage_guard_rejection,score_below_threshold', 0.48, 0.58, 35.0, 0.48, 'lazy_lookup', 1, 0, '["sampling-token","sampling-market"]', 0),
            (5, '2026-04-09 10:04:00', 'polymarket', 'market-5', 'SPORTS', 'discovery', 'baseline', 'discovery', 'reject', 'route_whale_orderflow_only', 0.0, 0.72, 40.0, 0.0, 'active_context', 0, 0, '["route-only-market"]', 0),
            (6, '2026-04-09 10:05:00', 'polymarket', 'market-6', 'OTHER', 'whale', 'baseline', 'whale_tracker', 'reject', 'unsupported_side_filtered', 0.0, 0.78, 25.0, 0.0, 'alias_cache', 0, 0, '["unsupported-market"]', 0),
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
    def __init__(self, process: subprocess.Popen[bytes], base_url: str):
        self.process = process
        self.base_url = base_url

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

    server = DashboardServer(process, base_url)
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
    assert payload['runtime_summary']['tracked_whales'] == 2
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
    assert payload['sampling_summary']['strategy_profile'] == 'sampling_relaxed'
    assert payload['sampling_summary']['closed_trades'] == 1
    assert payload['performance_summary']['evidence']['live_paper_closed'] == 3
    assert payload['swot_verdict']['final_verdict']['verdict'] == 'IMPROVE FIRST'
    assert isinstance(payload['warnings'], list)


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
    assert 'Henüz kapanmış whale geçmişi yok; nötr güven.' in html
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
