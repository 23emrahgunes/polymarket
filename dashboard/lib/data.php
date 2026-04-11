<?php
declare(strict_types=1);

require_once __DIR__ . '/bootstrap.php';

function dashboard_shell_exec(array $parts, array &$warnings, string $warningLabel): ?string
{
    if (!function_exists('shell_exec')) {
        $warnings[] = $warningLabel . ': shell_exec unavailable';
        return null;
    }

    $escaped = array_map(static fn (string $part): string => escapeshellarg($part), $parts);
    $command = implode(' ', $escaped) . ' 2>&1';
    $output = shell_exec($command);
    if ($output === null) {
        $warnings[] = $warningLabel . ': command execution failed';
        return null;
    }

    return trim($output);
}

function dashboard_open_db(array &$warnings): ?PDO
{
    $dbPath = dashboard_path(dashboard_env('GHOST_TRADER_DB_PATH', 'data/ghost_trader.db') ?? 'data/ghost_trader.db');
    if (!is_file($dbPath)) {
        $warnings[] = 'runtime data unavailable: SQLite database missing at ' . $dbPath;
        return null;
    }

    try {
        $pdo = new PDO('sqlite:' . $dbPath);
        $pdo->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);
        $pdo->setAttribute(PDO::ATTR_DEFAULT_FETCH_MODE, PDO::FETCH_ASSOC);
        return $pdo;
    } catch (Throwable $exception) {
        $warnings[] = 'runtime data unavailable: ' . $exception->getMessage();
        return null;
    }
}

function dashboard_fetch_all(PDO $pdo, string $sql, array $params = []): array
{
    $statement = $pdo->prepare($sql);
    $statement->execute($params);
    $rows = $statement->fetchAll();
    return is_array($rows) ? $rows : [];
}

function dashboard_fetch_one(PDO $pdo, string $sql, array $params = []): ?array
{
    $statement = $pdo->prepare($sql);
    $statement->execute($params);
    $row = $statement->fetch();
    return is_array($row) ? $row : null;
}

function dashboard_decision_scan_limit(): int
{
    $configured = dashboard_int_env('DASHBOARD_DECISION_SCAN_LIMIT', 50000);
    return max(min($configured, 250000), 1000);
}

function dashboard_decision_audit_window_columns(): string
{
    return 'id, occurred_at, venue, market_id, category, signal_family, strategy_profile, raw_source_signal, action, reason, decision_score, threshold, trade_size, confidence, inputs_json, mapping_stage, lazy_lookup_attempted, lazy_lookup_hit, alias_candidates_json, hot_window_promoted';
}

function dashboard_decision_audit_window_sql(string $alias = 'decision_audit'): string
{
    $safeAlias = preg_replace('/[^A-Za-z0-9_]/', '', $alias) ?: 'decision_audit';
    $limit = dashboard_decision_scan_limit();
    return '(SELECT ' . dashboard_decision_audit_window_columns() . ' FROM decision_audit ORDER BY id DESC LIMIT ' . $limit . ') AS ' . $safeAlias;
}

function dashboard_decision_audit_window_cte(string $name = 'recent_decisions'): string
{
    $safeName = preg_replace('/[^A-Za-z0-9_]/', '', $name) ?: 'recent_decisions';
    $limit = dashboard_decision_scan_limit();
    return $safeName . ' AS (SELECT ' . dashboard_decision_audit_window_columns() . ' FROM decision_audit ORDER BY id DESC LIMIT ' . $limit . ')';
}

function dashboard_load_report(string $pathEnvKey, string $fallbackPath, string $label, array &$warnings): ?array
{
    $path = dashboard_path(dashboard_env($pathEnvKey, $fallbackPath) ?? $fallbackPath);
    if (!is_file($path)) {
        $warnings[] = $label . ' unavailable: file missing at ' . $path;
        return null;
    }

    $content = file_get_contents($path);
    if ($content === false) {
        $warnings[] = $label . ' unavailable: could not read ' . $path;
        return null;
    }

    try {
        $decoded = json_decode($content, true, 512, JSON_THROW_ON_ERROR);
        return is_array($decoded) ? $decoded : null;
    } catch (Throwable $exception) {
        $warnings[] = $label . ' unavailable: ' . $exception->getMessage();
        return null;
    }
}

function dashboard_tail_lines(?string $content, int $maxLines): array
{
    if ($content === null || trim($content) === '') {
        return [];
    }

    $lines = preg_split('/\R/', trim($content)) ?: [];
    return array_slice($lines, -1 * max($maxLines, 1));
}

function dashboard_parse_status_metrics(array $logLines): array
{
    $latestStatus = null;
    foreach ($logLines as $line) {
        if (str_contains($line, '[STATUS]')) {
            $latestStatus = $line;
        }
    }

    if ($latestStatus === null) {
        return [];
    }

    preg_match_all('/([A-Za-z_]+)=([^\s]+)/', $latestStatus, $matches, PREG_SET_ORDER);
    $metrics = [];
    foreach ($matches as $match) {
        $key = $match[1];
        $value = $match[2];
        if (is_numeric($value)) {
            $metrics[$key] = str_contains($value, '.') ? (float) $value : (int) $value;
        } else {
            $metrics[$key] = $value;
        }
    }

    return $metrics;
}

function dashboard_read_runtime_status_snapshot(PDO $pdo, array &$warnings): array
{
    try {
        $statement = $pdo->query('SELECT updated_at, metrics_json FROM runtime_status_snapshot WHERE id = 1');
        $row = $statement ? $statement->fetch(PDO::FETCH_ASSOC) : false;
    } catch (Throwable $exception) {
        return [];
    }

    if (!is_array($row) || trim((string) ($row['metrics_json'] ?? '')) === '') {
        return [];
    }

    try {
        $decoded = json_decode((string) $row['metrics_json'], true, 512, JSON_THROW_ON_ERROR);
    } catch (Throwable $exception) {
        $warnings[] = 'runtime status snapshot decode failed';
        return [];
    }

    if (!is_array($decoded)) {
        return [];
    }

    $decoded['updated_at'] = $row['updated_at'] ?? null;
    if (($decoded['lookup_hydration_warning'] ?? 'none') === 'none') {
        $decoded['lookup_hydration_warning'] = null;
    }
    return $decoded;
}

function dashboard_get_service_data(array &$warnings): array
{
    $serviceName = dashboard_service_name(dashboard_env('DASHBOARD_TARGET_SERVICE', 'ghost-trader') ?? 'ghost-trader');
    $statusText = dashboard_shell_exec(['systemctl', 'is-active', $serviceName], $warnings, 'service status unavailable');
    $serviceStatusOutput = dashboard_shell_exec(['systemctl', 'status', $serviceName, '--no-pager'], $warnings, 'service status detail unavailable');
    $logLineCount = max(dashboard_int_env('DASHBOARD_LOG_LINES', 40) * 3, 120);
    $logOutput = dashboard_shell_exec(['journalctl', '-u', $serviceName, '-n', (string) $logLineCount, '--no-pager'], $warnings, 'service log unavailable');

    $active = trim((string) $statusText) === 'active';
    $lastError = null;
    if ($serviceStatusOutput !== null && preg_match('/status=([^ )]+)/i', $serviceStatusOutput, $match) === 1) {
        $lastError = $match[1];
    }

    return [
        'name' => $serviceName,
        'active' => $active,
        'status_text' => $serviceStatusOutput !== null ? trim($serviceStatusOutput) : trim((string) $statusText),
        'last_error' => $lastError,
        'log_lines' => dashboard_tail_lines($logOutput, dashboard_int_env('DASHBOARD_LOG_LINES', 40)),
        'status_metrics' => dashboard_parse_status_metrics(dashboard_tail_lines($logOutput, $logLineCount)),
    ];
}

function dashboard_fetch_orderflow_mapping_stats(PDO $pdo, bool $recentWindow = false): array
{
    $windowCte = dashboard_decision_audit_window_cte('recent_decisions');
    if ($recentWindow) {
        return dashboard_fetch_one(
            $pdo,
            "
            WITH {$windowCte},
            anchor AS (
                SELECT MAX(occurred_at) AS max_occurred_at
                FROM recent_decisions
                WHERE signal_family IN ('activity_orderflow', 'whale')
                  AND action IN ('reject', 'decision')
            )
            SELECT
                COALESCE(SUM(CASE WHEN decision_audit.signal_family IN ('activity_orderflow', 'whale') THEN 1 ELSE 0 END), 0) AS total_orderflow,
                COALESCE(SUM(CASE WHEN decision_audit.signal_family IN ('activity_orderflow', 'whale') AND decision_audit.reason LIKE 'market_not_mapped%' THEN 1 ELSE 0 END), 0) AS unmapped_orderflow,
                COALESCE(SUM(CASE WHEN decision_audit.signal_family IN ('activity_orderflow', 'whale') AND decision_audit.mapping_stage = 'alias_cache' AND decision_audit.reason NOT LIKE 'market_not_mapped%' THEN 1 ELSE 0 END), 0) AS alias_cache_hits,
                COALESCE(SUM(CASE WHEN decision_audit.signal_family IN ('activity_orderflow', 'whale') AND decision_audit.lazy_lookup_hit = 1 THEN 1 ELSE 0 END), 0) AS lazy_lookup_hits
            FROM recent_decisions AS decision_audit
            CROSS JOIN anchor
            WHERE anchor.max_occurred_at IS NOT NULL
              AND decision_audit.action IN ('reject', 'decision')
              AND decision_audit.occurred_at >= datetime(anchor.max_occurred_at, '-60 minutes')
            "
        ) ?? ['total_orderflow' => 0, 'unmapped_orderflow' => 0, 'alias_cache_hits' => 0, 'lazy_lookup_hits' => 0];
    }

    return dashboard_fetch_one(
        $pdo,
        "
        WITH {$windowCte}
        SELECT
            COALESCE(SUM(CASE WHEN signal_family IN ('activity_orderflow', 'whale') THEN 1 ELSE 0 END), 0) AS total_orderflow,
            COALESCE(SUM(CASE WHEN signal_family IN ('activity_orderflow', 'whale') AND reason LIKE 'market_not_mapped%' THEN 1 ELSE 0 END), 0) AS unmapped_orderflow,
            COALESCE(SUM(CASE WHEN signal_family IN ('activity_orderflow', 'whale') AND mapping_stage = 'alias_cache' AND reason NOT LIKE 'market_not_mapped%' THEN 1 ELSE 0 END), 0) AS alias_cache_hits,
            COALESCE(SUM(CASE WHEN signal_family IN ('activity_orderflow', 'whale') AND lazy_lookup_hit = 1 THEN 1 ELSE 0 END), 0) AS lazy_lookup_hits
        FROM recent_decisions
        WHERE action IN ('reject', 'decision')
        "
    ) ?? ['total_orderflow' => 0, 'unmapped_orderflow' => 0, 'alias_cache_hits' => 0, 'lazy_lookup_hits' => 0];
}

function dashboard_runtime_summary_from_db(PDO $pdo): array
{
    $wallet = dashboard_fetch_one($pdo, 'SELECT balance FROM wallet WHERE id = 1');
    $tradeCount = dashboard_fetch_one($pdo, 'SELECT COUNT(*) AS count FROM trades');
    $openPositions = dashboard_fetch_one($pdo, "SELECT COUNT(*) AS count FROM venue_positions WHERE status = 'OPEN'");
    $openOrders = dashboard_fetch_one($pdo, "SELECT COUNT(*) AS count FROM venue_orders WHERE status = 'OPEN'");
    $aliasIntegrity = dashboard_fetch_one(
        $pdo,
        'SELECT COUNT(*) AS alias_rows, COUNT(DISTINCT market_id) AS market_rows FROM market_aliases'
    ) ?? ['alias_rows' => 0, 'market_rows' => 0];
    $samplingClosed = dashboard_fetch_one(
        $pdo,
        "SELECT COUNT(*) AS count FROM trades WHERE venue = 'polymarket' AND sample_kind = 'live_paper' AND is_synthetic = 0 AND strategy_profile = 'sampling_relaxed' AND status != 'OPEN'"
    );
    $whales = dashboard_fetch_all(
        $pdo,
        "SELECT source_type, COUNT(*) AS count FROM whale_wallets WHERE enabled = 1 GROUP BY source_type ORDER BY source_type"
    );

    $historicalDecisionStats = dashboard_fetch_orderflow_mapping_stats($pdo, false);
    $recentDecisionStats = dashboard_fetch_orderflow_mapping_stats($pdo, true);

    $trackedWhales = 0;
    foreach ($whales as $row) {
        $trackedWhales += (int) ($row['count'] ?? 0);
    }

    $historicalTotalOrderflow = (int) ($historicalDecisionStats['total_orderflow'] ?? 0);
    $historicalUnmappedOrderflow = (int) ($historicalDecisionStats['unmapped_orderflow'] ?? 0);
    $historicalMappedOrderflow = max($historicalTotalOrderflow - $historicalUnmappedOrderflow, 0);
    $historicalMarketNotMappedRate = $historicalTotalOrderflow > 0
        ? ($historicalUnmappedOrderflow / $historicalTotalOrderflow) * 100.0
        : 0.0;
    $recentTotalOrderflow = (int) ($recentDecisionStats['total_orderflow'] ?? 0);
    $recentUnmappedOrderflow = (int) ($recentDecisionStats['unmapped_orderflow'] ?? 0);
    $recentMappedOrderflow = max($recentTotalOrderflow - $recentUnmappedOrderflow, 0);
    $recentMarketNotMappedRate = $recentTotalOrderflow > 0
        ? ($recentUnmappedOrderflow / $recentTotalOrderflow) * 100.0
        : 0.0;
    $samplingTarget = max(dashboard_int_env('PAPER_SAMPLING_TARGET_CLOSED_TRADES', 20), 1);
    $samplingMode = 'disabled';
    $samplingStopReason = 'none';
    if (dashboard_bool_env('PAPER_SAMPLING_MODE', false)) {
        $samplingMode = ((int) ($samplingClosed['count'] ?? 0) >= $samplingTarget) ? 'target_reached' : 'enabled';
        $samplingStopReason = $samplingMode === 'target_reached' ? 'target_reached' : 'none';
    }

    return [
        'wallet_balance' => isset($wallet['balance']) ? (float) $wallet['balance'] : null,
        'tracked_whales' => $trackedWhales,
        'mapped_orderflow_events' => $recentMappedOrderflow,
        'unmapped_orderflow_events' => $recentUnmappedOrderflow,
        'alias_cache_hits' => (int) ($recentDecisionStats['alias_cache_hits'] ?? 0),
        'lazy_lookup_hits' => (int) ($recentDecisionStats['lazy_lookup_hits'] ?? 0),
        'market_not_mapped_rate' => round($recentMarketNotMappedRate, 1),
        'recent_mapped_orderflow_events' => $recentMappedOrderflow,
        'recent_unmapped_orderflow_events' => $recentUnmappedOrderflow,
        'recent_market_not_mapped_rate' => round($recentMarketNotMappedRate, 1),
        'historical_mapped_orderflow_events' => $historicalMappedOrderflow,
        'historical_unmapped_orderflow_events' => $historicalUnmappedOrderflow,
        'historical_market_not_mapped_rate' => round($historicalMarketNotMappedRate, 1),
        'live_metrics_available' => false,
        'total_trades' => isset($tradeCount['count']) ? (int) $tradeCount['count'] : 0,
        'open_positions_count' => isset($openPositions['count']) ? (int) $openPositions['count'] : 0,
        'open_orders_count' => isset($openOrders['count']) ? (int) $openOrders['count'] : 0,
        'sampling_mode' => $samplingMode,
        'sampling_closed_trades' => isset($samplingClosed['count']) ? (int) $samplingClosed['count'] : 0,
        'sampling_target_closed_trades' => $samplingTarget,
        'sampling_stop_reason' => $samplingStopReason,
        'lookup_universe_markets' => 0,
        'lookup_universe_aliases' => 0,
        'persisted_market_alias_rows' => (int) ($aliasIntegrity['alias_rows'] ?? 0),
        'persisted_market_alias_markets' => (int) ($aliasIntegrity['market_rows'] ?? 0),
        'hydrated_lookup_markets' => 0,
        'hydrated_lookup_aliases' => 0,
        'lookup_hydration_warning' => null,
        'alias_persistence_gap' => 0,
    ];
}

function dashboard_merge_runtime_summary(array $dbSummary, array $statusMetrics): array
{
    $summary = $dbSummary;
    $keys = [
        'wallet_balance',
        'tracked_whales',
        'leaderboard_wallets',
        'activity_discovered_wallets',
        'graph_discovered_wallets',
        'trusted_whales',
        'persisted_wallets',
        'mapped_orderflow_events',
        'unmapped_orderflow_events',
        'alias_cache_hits',
        'lazy_lookup_hits',
        'market_not_mapped_rate',
        'total_trades',
        'sampling_mode',
        'sampling_closed_trades',
        'sampling_target_closed_trades',
        'sampling_stop_reason',
        'lookup_universe_markets',
        'lookup_universe_aliases',
        'persisted_market_alias_rows',
        'persisted_market_alias_markets',
        'hydrated_lookup_markets',
        'hydrated_lookup_aliases',
        'lookup_hydration_warning',
        'alias_persistence_gap',
    ];

    foreach ($keys as $key) {
        if (array_key_exists($key, $statusMetrics)) {
            $summary[$key] = $statusMetrics[$key];
        }
    }

    if (isset($statusMetrics['futures_open_positions']) || isset($statusMetrics['spot_open_positions'])) {
        $summary['open_positions_count'] = (int) ($statusMetrics['futures_open_positions'] ?? 0) + (int) ($statusMetrics['spot_open_positions'] ?? 0);
    }

    if (
        array_key_exists('mapped_orderflow_events', $statusMetrics)
        || array_key_exists('unmapped_orderflow_events', $statusMetrics)
        || array_key_exists('market_not_mapped_rate', $statusMetrics)
    ) {
        $summary['live_metrics_available'] = true;
    }

    return $summary;
}

function dashboard_sampling_summary_from_db(PDO $pdo): array
{
    $row = dashboard_fetch_one(
        $pdo,
        "
        SELECT
            COUNT(*) AS total_trades,
            COALESCE(SUM(CASE WHEN status != 'OPEN' THEN 1 ELSE 0 END), 0) AS closed_trades,
            COALESCE(SUM(CASE WHEN status = 'OPEN' THEN 1 ELSE 0 END), 0) AS open_trades,
            COALESCE(SUM(CASE WHEN status != 'OPEN' AND pnl > 0 THEN 1 ELSE 0 END), 0) AS winning_trades,
            COALESCE(SUM(CASE WHEN status != 'OPEN' THEN pnl ELSE 0 END), 0) AS realized_pnl
        FROM trades
        WHERE venue = 'polymarket' AND sample_kind = 'live_paper' AND is_synthetic = 0 AND strategy_profile = 'sampling_relaxed'
        "
    ) ?: [];

    $totalTrades = (int) ($row['total_trades'] ?? 0);
    $closedTrades = (int) ($row['closed_trades'] ?? 0);
    $openTrades = (int) ($row['open_trades'] ?? 0);
    $winningTrades = (int) ($row['winning_trades'] ?? 0);
    $realizedPnl = (float) ($row['realized_pnl'] ?? 0.0);

    return [
        'strategy_profile' => 'sampling_relaxed',
        'total_trades' => $totalTrades,
        'closed_trades' => $closedTrades,
        'open_trades' => $openTrades,
        'win_rate' => $closedTrades > 0 ? round(($winningTrades / $closedTrades) * 100.0, 1) : null,
        'expectancy' => $closedTrades > 0 ? round($realizedPnl / $closedTrades, 4) : null,
        'total_pnl' => round($realizedPnl, 2),
    ];
}

function dashboard_fetch_runtime_collections(PDO $pdo): array
{
    return [
        'venue_accounts' => dashboard_fetch_all(
            $pdo,
            'SELECT venue, execution_mode, cash_balance, equity, available_balance, updated_at FROM venue_accounts ORDER BY venue, execution_mode'
        ),
        'recent_trades' => dashboard_fetch_all(
            $pdo,
            'SELECT id, venue, instrument_type, market_id, side, size, price, confidence, source_signal, category, strategy_profile, status, pnl, whale_address, timestamp FROM trades ORDER BY id DESC LIMIT 12'
        ),
        'open_positions' => dashboard_fetch_all(
            $pdo,
            "SELECT id, venue, instrument_type, symbol_or_market_id, side, entry_price, mark_price, notional_usd, unrealized_pnl, realized_pnl, leverage, strategy_profile, status, opened_at FROM venue_positions WHERE status = 'OPEN' ORDER BY id DESC LIMIT 12"
        ),
        'open_orders' => dashboard_fetch_all(
            $pdo,
            "SELECT id, venue, symbol_or_market_id, order_type, side, qty, price, stop_price, reduce_only, status, created_at FROM venue_orders WHERE status = 'OPEN' ORDER BY id DESC LIMIT 20"
        ),
        'recent_decisions' => dashboard_fetch_all(
            $pdo,
            'SELECT occurred_at, venue, market_id, category, signal_family, strategy_profile, raw_source_signal, action, reason, decision_score, threshold, trade_size, confidence, mapping_stage, lazy_lookup_attempted, lazy_lookup_hit, hot_window_promoted FROM decision_audit ORDER BY id DESC LIMIT 20'
        ),
        'whale_wallet_counts' => dashboard_fetch_all(
            $pdo,
            'SELECT source_type, COUNT(*) AS count FROM whale_wallets WHERE enabled = 1 GROUP BY source_type ORDER BY source_type'
        ),
        'top_whales' => dashboard_fetch_all(
            $pdo,
            "
            SELECT
                whale_wallets.address,
                whale_wallets.source_type,
                whale_wallets.discovery_score,
                whale_wallets.last_event_amount,
                whale_wallets.event_count_24h,
                whale_wallets.failure_streak,
                whale_wallets.last_seen_at,
                COALESCE(whale_stats.trust_score, 0.5) AS trust_score,
                COALESCE(whale_stats.total_trades, 0) AS total_trades,
                COALESCE(whale_stats.wins, 0) AS wins,
                COALESCE(whale_stats.total_pnl, 0) AS total_pnl,
                CASE
                    WHEN COALESCE(whale_stats.total_trades, 0) > 0 THEN ROUND((CAST(whale_stats.wins AS REAL) / whale_stats.total_trades) * 100.0, 1)
                    ELSE NULL
                END AS win_rate
            FROM whale_wallets
            LEFT JOIN whale_stats ON whale_stats.address = whale_wallets.address
            WHERE whale_wallets.enabled = 1
            ORDER BY whale_wallets.discovery_score DESC, whale_wallets.last_event_amount DESC
            LIMIT 12
            "
        ),
        'market_alias_counts' => dashboard_fetch_all(
            $pdo,
            'SELECT source, COUNT(*) AS count FROM market_aliases GROUP BY source ORDER BY source'
        ),
        'top_market_aliases' => dashboard_fetch_all(
            $pdo,
            'SELECT alias, alias_type, market_id, question, category, volume_24h, active, source, last_seen_at FROM market_aliases ORDER BY volume_24h DESC, last_seen_at DESC LIMIT 12'
        ),
    ];
}

function dashboard_build_payload(string $view = 'full'): array
{
    $warnings = [];
    $service = dashboard_get_service_data($warnings);
    $statusMetrics = $service['status_metrics'];

    $pdo = dashboard_open_db($warnings);
    $runtimeSummary = [
        'wallet_balance' => null,
        'tracked_whales' => 0,
        'leaderboard_wallets' => 0,
        'activity_discovered_wallets' => 0,
        'graph_discovered_wallets' => 0,
        'trusted_whales' => 0,
        'persisted_wallets' => 0,
        'mapped_orderflow_events' => 0,
        'unmapped_orderflow_events' => 0,
        'alias_cache_hits' => 0,
        'lazy_lookup_hits' => 0,
        'market_not_mapped_rate' => 0.0,
        'recent_mapped_orderflow_events' => 0,
        'recent_unmapped_orderflow_events' => 0,
        'recent_market_not_mapped_rate' => 0.0,
        'historical_mapped_orderflow_events' => 0,
        'historical_unmapped_orderflow_events' => 0,
        'historical_market_not_mapped_rate' => 0.0,
        'live_metrics_available' => false,
        'total_trades' => 0,
        'open_positions_count' => 0,
        'open_orders_count' => 0,
        'sampling_mode' => dashboard_bool_env('PAPER_SAMPLING_MODE', false) ? 'enabled' : 'disabled',
        'sampling_closed_trades' => 0,
        'sampling_target_closed_trades' => max(dashboard_int_env('PAPER_SAMPLING_TARGET_CLOSED_TRADES', 20), 1),
        'sampling_stop_reason' => 'none',
        'lookup_universe_markets' => 0,
        'lookup_universe_aliases' => 0,
        'persisted_market_alias_rows' => 0,
        'persisted_market_alias_markets' => 0,
        'hydrated_lookup_markets' => 0,
        'hydrated_lookup_aliases' => 0,
        'lookup_hydration_warning' => null,
        'alias_persistence_gap' => 0,
    ];

    $collections = [
        'venue_accounts' => [],
        'recent_trades' => [],
        'open_positions' => [],
        'open_orders' => [],
        'recent_decisions' => [],
        'whale_wallet_counts' => [],
        'top_whales' => [],
        'market_alias_counts' => [],
        'top_market_aliases' => [],
    ];

    if ($pdo !== null) {
        $snapshotMetrics = dashboard_read_runtime_status_snapshot($pdo, $warnings);
        $liveMetrics = $snapshotMetrics !== [] ? $snapshotMetrics : $statusMetrics;
        $runtimeSummary = dashboard_merge_runtime_summary(dashboard_runtime_summary_from_db($pdo), $liveMetrics);
        if (!empty($snapshotMetrics['lookup_hydration_warning'])) {
            $warnings[] = 'canli lookup hydration sifir gorunuyor; kalici alias cache runtime a tam yuklenmemis olabilir.';
        }
        $collections = dashboard_fetch_runtime_collections($pdo);
    }

    $summaryReport = dashboard_load_report('DASHBOARD_SUMMARY_PATH', 'reports/performance/summary.json', 'performance report', $warnings);
    $swotReport = dashboard_load_report('DASHBOARD_SWOT_PATH', 'reports/performance/swot_report.json', 'SWOT report', $warnings);
    $samplingSummary = $pdo !== null
        ? dashboard_sampling_summary_from_db($pdo)
        : [
            'strategy_profile' => 'sampling_relaxed',
            'total_trades' => 0,
            'closed_trades' => 0,
            'open_trades' => 0,
            'win_rate' => null,
            'expectancy' => null,
            'total_pnl' => 0.0,
        ];

    return [
        'generated_at' => gmdate('c'),
        'service' => [
            'name' => $service['name'],
            'active' => $service['active'],
            'status_text' => $service['status_text'],
            'last_error' => $service['last_error'],
        ],
        'runtime_summary' => $runtimeSummary,
        'venue_accounts' => $collections['venue_accounts'],
        'recent_trades' => $collections['recent_trades'],
        'open_positions' => $collections['open_positions'],
        'open_orders' => $collections['open_orders'],
        'recent_decisions' => $collections['recent_decisions'],
        'whale_wallet_counts' => $collections['whale_wallet_counts'],
        'top_whales' => $collections['top_whales'],
        'market_alias_counts' => $collections['market_alias_counts'],
        'top_market_aliases' => $collections['top_market_aliases'],
        'performance_summary' => $summaryReport,
        'sampling_summary' => $samplingSummary,
        'swot_verdict' => $swotReport,
        'service_log_excerpt' => $service['log_lines'],
        'warnings' => $warnings,
    ];
}
