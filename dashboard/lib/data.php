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

function dashboard_runtime_summary_from_db(PDO $pdo): array
{
    $wallet = dashboard_fetch_one($pdo, 'SELECT balance FROM wallet WHERE id = 1');
    $tradeCount = dashboard_fetch_one($pdo, 'SELECT COUNT(*) AS count FROM trades');
    $openPositions = dashboard_fetch_one($pdo, "SELECT COUNT(*) AS count FROM venue_positions WHERE status = 'OPEN'");
    $openOrders = dashboard_fetch_one($pdo, "SELECT COUNT(*) AS count FROM venue_orders WHERE status = 'OPEN'");
    $whales = dashboard_fetch_all(
        $pdo,
        "SELECT source_type, COUNT(*) AS count FROM whale_wallets WHERE enabled = 1 GROUP BY source_type ORDER BY source_type"
    );

    $decisionStats = dashboard_fetch_one(
        $pdo,
        "
        SELECT
            COALESCE(SUM(CASE WHEN signal_family IN ('activity_orderflow', 'whale') THEN 1 ELSE 0 END), 0) AS total_orderflow,
            COALESCE(SUM(CASE WHEN signal_family IN ('activity_orderflow', 'whale') AND reason LIKE 'market_not_mapped%' THEN 1 ELSE 0 END), 0) AS unmapped_orderflow,
            COALESCE(SUM(CASE WHEN signal_family IN ('activity_orderflow', 'whale') AND mapping_stage = 'alias_cache' AND reason NOT LIKE 'market_not_mapped%' THEN 1 ELSE 0 END), 0) AS alias_cache_hits,
            COALESCE(SUM(CASE WHEN signal_family IN ('activity_orderflow', 'whale') AND lazy_lookup_hit = 1 THEN 1 ELSE 0 END), 0) AS lazy_lookup_hits
        FROM decision_audit
        "
    ) ?? ['total_orderflow' => 0, 'unmapped_orderflow' => 0, 'alias_cache_hits' => 0, 'lazy_lookup_hits' => 0];

    $trackedWhales = 0;
    foreach ($whales as $row) {
        $trackedWhales += (int) ($row['count'] ?? 0);
    }

    $totalOrderflow = (int) ($decisionStats['total_orderflow'] ?? 0);
    $unmappedOrderflow = (int) ($decisionStats['unmapped_orderflow'] ?? 0);
    $mappedOrderflow = max($totalOrderflow - $unmappedOrderflow, 0);
    $marketNotMappedRate = $totalOrderflow > 0 ? ($unmappedOrderflow / $totalOrderflow) * 100.0 : 0.0;

    return [
        'wallet_balance' => isset($wallet['balance']) ? (float) $wallet['balance'] : null,
        'tracked_whales' => $trackedWhales,
        'mapped_orderflow_events' => $mappedOrderflow,
        'unmapped_orderflow_events' => $unmappedOrderflow,
        'alias_cache_hits' => (int) ($decisionStats['alias_cache_hits'] ?? 0),
        'lazy_lookup_hits' => (int) ($decisionStats['lazy_lookup_hits'] ?? 0),
        'market_not_mapped_rate' => round($marketNotMappedRate, 1),
        'total_trades' => isset($tradeCount['count']) ? (int) $tradeCount['count'] : 0,
        'open_positions_count' => isset($openPositions['count']) ? (int) $openPositions['count'] : 0,
        'open_orders_count' => isset($openOrders['count']) ? (int) $openOrders['count'] : 0,
    ];
}

function dashboard_merge_runtime_summary(array $dbSummary, array $statusMetrics): array
{
    $summary = $dbSummary;
    $keys = [
        'wallet_balance',
        'tracked_whales',
        'mapped_orderflow_events',
        'unmapped_orderflow_events',
        'alias_cache_hits',
        'lazy_lookup_hits',
        'market_not_mapped_rate',
        'total_trades',
    ];

    foreach ($keys as $key) {
        if (array_key_exists($key, $statusMetrics)) {
            $summary[$key] = $statusMetrics[$key];
        }
    }

    if (isset($statusMetrics['futures_open_positions']) || isset($statusMetrics['spot_open_positions'])) {
        $summary['open_positions_count'] = (int) ($statusMetrics['futures_open_positions'] ?? 0) + (int) ($statusMetrics['spot_open_positions'] ?? 0);
    }

    return $summary;
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
            'SELECT id, venue, instrument_type, market_id, side, size, price, confidence, source_signal, category, status, pnl, whale_address, timestamp FROM trades ORDER BY id DESC LIMIT 12'
        ),
        'open_positions' => dashboard_fetch_all(
            $pdo,
            "SELECT id, venue, instrument_type, symbol_or_market_id, side, entry_price, mark_price, notional_usd, unrealized_pnl, realized_pnl, leverage, status, opened_at FROM venue_positions WHERE status = 'OPEN' ORDER BY id DESC LIMIT 12"
        ),
        'open_orders' => dashboard_fetch_all(
            $pdo,
            "SELECT id, venue, symbol_or_market_id, order_type, side, qty, price, stop_price, reduce_only, status, created_at FROM venue_orders WHERE status = 'OPEN' ORDER BY id DESC LIMIT 20"
        ),
        'recent_decisions' => dashboard_fetch_all(
            $pdo,
            'SELECT occurred_at, venue, market_id, category, signal_family, raw_source_signal, action, reason, decision_score, threshold, trade_size, confidence, mapping_stage, lazy_lookup_attempted, lazy_lookup_hit FROM decision_audit ORDER BY id DESC LIMIT 20'
        ),
        'whale_wallet_counts' => dashboard_fetch_all(
            $pdo,
            'SELECT source_type, COUNT(*) AS count FROM whale_wallets WHERE enabled = 1 GROUP BY source_type ORDER BY source_type'
        ),
        'top_whales' => dashboard_fetch_all(
            $pdo,
            'SELECT address, source_type, discovery_score, last_event_amount, event_count_24h, failure_streak, last_seen_at FROM whale_wallets WHERE enabled = 1 ORDER BY discovery_score DESC, last_event_amount DESC LIMIT 12'
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
        'mapped_orderflow_events' => 0,
        'unmapped_orderflow_events' => 0,
        'alias_cache_hits' => 0,
        'lazy_lookup_hits' => 0,
        'market_not_mapped_rate' => 0.0,
        'total_trades' => 0,
        'open_positions_count' => 0,
        'open_orders_count' => 0,
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
        $runtimeSummary = dashboard_merge_runtime_summary(dashboard_runtime_summary_from_db($pdo), $statusMetrics);
        $collections = dashboard_fetch_runtime_collections($pdo);
    }

    $summaryReport = dashboard_load_report('DASHBOARD_SUMMARY_PATH', 'reports/performance/summary.json', 'performance report', $warnings);
    $swotReport = dashboard_load_report('DASHBOARD_SWOT_PATH', 'reports/performance/swot_report.json', 'SWOT report', $warnings);

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
        'swot_verdict' => $swotReport,
        'service_log_excerpt' => $service['log_lines'],
        'warnings' => $warnings,
    ];
}
