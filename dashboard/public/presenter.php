<?php
declare(strict_types=1);

require_once dirname(__DIR__) . '/lib/data.php';

function dashboard_table_has_column(PDO $pdo, string $table, string $column): bool
{
    static $cache = [];
    $safeTable = preg_replace('/[^A-Za-z0-9_]/', '', $table);
    $cacheKey = $safeTable . ':' . $column;
    if (array_key_exists($cacheKey, $cache)) {
        return $cache[$cacheKey];
    }

    $statement = $pdo->query("PRAGMA table_info({$safeTable})");
    $rows = $statement ? $statement->fetchAll(PDO::FETCH_ASSOC) : [];
    foreach ($rows as $row) {
        if (($row['name'] ?? null) === $column) {
            $cache[$cacheKey] = true;
            return true;
        }
    }

    $cache[$cacheKey] = false;
    return false;
}

function dashboard_decode_alias_candidates(?string $json): array
{
    if ($json === null || trim($json) === '') {
        return [];
    }

    try {
        $decoded = json_decode($json, true, 512, JSON_THROW_ON_ERROR);
    } catch (Throwable $exception) {
        return [];
    }

    if (!is_array($decoded)) {
        return [];
    }

    $aliases = [];
    foreach ($decoded as $candidate) {
        if (!is_string($candidate)) {
            continue;
        }
        $candidate = trim($candidate);
        if ($candidate === '' || in_array($candidate, $aliases, true)) {
            continue;
        }
        $aliases[] = $candidate;
    }

    return $aliases;
}

function dashboard_translate_warning(string $warning): string
{
    return str_replace(
        [
            'runtime data unavailable:',
            'performance report unavailable:',
            'SWOT report unavailable:',
            'service status unavailable:',
            'service status detail unavailable:',
            'service log unavailable:',
            'shell_exec unavailable',
            'command execution failed',
            'SQLite database missing at ',
            'file missing at ',
            'could not read ',
        ],
        [
            'çalışma zamanı verisi kullanılamıyor:',
            'performans raporu kullanılamıyor:',
            'SWOT raporu kullanılamıyor:',
            'servis durumu alınamadı:',
            'servis detay durumu alınamadı:',
            'servis logu alınamadı:',
            'shell_exec kullanılamıyor',
            'komut çalıştırılamadı',
            'SQLite veritabanı bulunamadı -> ',
            'dosya bulunamadı -> ',
            'dosya okunamadı -> ',
        ],
        $warning
    );
}

function dashboard_build_unresolved_alias_summary(PDO $pdo): array
{
    if (!dashboard_table_has_column($pdo, 'decision_audit', 'alias_candidates_json')) {
        return [
            'top_unresolved_aliases' => [],
            'recent_unresolved_aliases' => [],
        ];
    }

    $rows = dashboard_fetch_all(
        $pdo,
        "SELECT occurred_at, reason, mapping_stage, alias_candidates_json
         FROM decision_audit
         WHERE reason LIKE 'market_not_mapped%'
         ORDER BY id DESC
         LIMIT 250"
    );

    $recent = [];
    $top = [];
    foreach ($rows as $row) {
        $aliases = dashboard_decode_alias_candidates($row['alias_candidates_json'] ?? null);
        if ($aliases === []) {
            continue;
        }

        if (count($recent) < 10) {
            $recent[] = [
                'occurred_at' => $row['occurred_at'] ?? null,
                'reason' => $row['reason'] ?? null,
                'mapping_stage' => $row['mapping_stage'] ?? null,
                'aliases' => array_slice($aliases, 0, 3),
            ];
        }

        foreach (array_slice($aliases, 0, 3) as $alias) {
            if (!isset($top[$alias])) {
                $top[$alias] = [
                    'alias' => $alias,
                    'count' => 0,
                    'last_seen_at' => $row['occurred_at'] ?? null,
                    'reason' => $row['reason'] ?? null,
                ];
            }
            $top[$alias]['count'] += 1;
            $top[$alias]['last_seen_at'] = $row['occurred_at'] ?? $top[$alias]['last_seen_at'];
            $top[$alias]['reason'] = $row['reason'] ?? $top[$alias]['reason'];
        }
    }

    uasort(
        $top,
        static function (array $left, array $right): int {
            $countCompare = ($right['count'] ?? 0) <=> ($left['count'] ?? 0);
            if ($countCompare !== 0) {
                return $countCompare;
            }
            return strcmp((string) ($right['last_seen_at'] ?? ''), (string) ($left['last_seen_at'] ?? ''));
        }
    );

    return [
        'top_unresolved_aliases' => array_values(array_slice($top, 0, 10)),
        'recent_unresolved_aliases' => $recent,
    ];
}

function dashboard_build_hot_window_summary(PDO $pdo, array $statusMetrics): array
{
    $defaults = [
        'hot_window_markets' => 0,
        'hot_window_hits' => 0,
        'hot_window_promotions' => 0,
        'hot_window_expiries' => 0,
        'active_window_misses' => 0,
        'active_window_miss_rate' => 0.0,
        'resolver_hit_rate' => 0.0,
    ];

    $decisionStats = dashboard_fetch_one(
        $pdo,
        "
        SELECT
            COALESCE(SUM(CASE WHEN mapping_stage = 'hot_window' AND reason NOT LIKE 'market_not_mapped%' THEN 1 ELSE 0 END), 0) AS hot_window_hits,
            COALESCE(SUM(CASE WHEN reason = 'market_not_mapped_active_window' THEN 1 ELSE 0 END), 0) AS active_window_misses,
            COALESCE(SUM(CASE WHEN hot_window_promoted = 1 THEN 1 ELSE 0 END), 0) AS hot_window_promotions,
            COALESCE(SUM(CASE WHEN signal_family IN ('activity_orderflow', 'whale') THEN 1 ELSE 0 END), 0) AS total_orderflow,
            COALESCE(SUM(CASE WHEN signal_family IN ('activity_orderflow', 'whale') AND reason LIKE 'market_not_mapped%' THEN 1 ELSE 0 END), 0) AS unmapped_orderflow,
            COALESCE(SUM(CASE WHEN signal_family IN ('activity_orderflow', 'whale') AND mapping_stage = 'alias_cache' AND reason NOT LIKE 'market_not_mapped%' THEN 1 ELSE 0 END), 0) AS alias_cache_hits,
            COALESCE(SUM(CASE WHEN signal_family IN ('activity_orderflow', 'whale') AND lazy_lookup_hit = 1 THEN 1 ELSE 0 END), 0) AS lazy_lookup_hits
        FROM decision_audit
        "
    ) ?: [];

    $totalOrderflow = (int) ($decisionStats['total_orderflow'] ?? 0);
    $mappedOrderflow = max($totalOrderflow - (int) ($decisionStats['unmapped_orderflow'] ?? 0), 0);
    $resolverHits = (int) ($decisionStats['alias_cache_hits'] ?? 0) + (int) ($decisionStats['lazy_lookup_hits'] ?? 0);

    $summary = $defaults;
    $summary['hot_window_hits'] = (int) ($decisionStats['hot_window_hits'] ?? 0);
    $summary['hot_window_promotions'] = (int) ($decisionStats['hot_window_promotions'] ?? 0);
    $summary['active_window_misses'] = (int) ($decisionStats['active_window_misses'] ?? 0);
    $summary['active_window_miss_rate'] = $totalOrderflow > 0
        ? round(((int) $summary['active_window_misses'] / $totalOrderflow) * 100.0, 1)
        : 0.0;
    $summary['resolver_hit_rate'] = $mappedOrderflow > 0
        ? round(($resolverHits / $mappedOrderflow) * 100.0, 1)
        : 0.0;

    foreach (['hot_window_markets', 'hot_window_expiries'] as $key) {
        if (array_key_exists($key, $statusMetrics)) {
            $summary[$key] = (int) $statusMetrics[$key];
        }
    }

    return $summary;
}

function dashboard_build_sampling_reject_breakdown(PDO $pdo): array
{
    $rows = dashboard_fetch_all(
        $pdo,
        "
        SELECT reason
        FROM decision_audit
        WHERE strategy_profile = 'sampling_relaxed' AND action = 'reject'
        ORDER BY id DESC
        LIMIT 250
        "
    );

    $counts = [];
    foreach ($rows as $row) {
        $reasonText = trim((string) ($row['reason'] ?? ''));
        if ($reasonText === '') {
            continue;
        }
        foreach (explode(',', $reasonText) as $reason) {
            $reason = trim($reason);
            if ($reason === '') {
                continue;
            }
            $counts[$reason] = ($counts[$reason] ?? 0) + 1;
        }
    }

    arsort($counts);
    $items = [];
    foreach (array_slice($counts, 0, 8, true) as $reason => $count) {
        $items[] = [
            'reason' => $reason,
            'count' => $count,
        ];
    }
    return $items;
}

function dashboard_build_alias_persistence_summary(PDO $pdo, array $runtimeSummary): array
{
    $integrity = dashboard_fetch_one(
        $pdo,
        'SELECT COUNT(*) AS alias_rows, COUNT(DISTINCT market_id) AS market_rows FROM market_aliases'
    ) ?? ['alias_rows' => 0, 'market_rows' => 0];

    $lookupUniverseMarkets = (int) ($runtimeSummary['lookup_universe_markets'] ?? 0);
    $lookupUniverseAliases = (int) ($runtimeSummary['lookup_universe_aliases'] ?? 0);
    $persistedRows = (int) (($runtimeSummary['persisted_market_alias_rows'] ?? 0) ?: ($integrity['alias_rows'] ?? 0));
    $persistedMarkets = (int) (($runtimeSummary['persisted_market_alias_markets'] ?? 0) ?: ($integrity['market_rows'] ?? 0));
    $aliasPersistenceGap = max(
        (int) ($runtimeSummary['alias_persistence_gap'] ?? max($lookupUniverseAliases - $persistedRows, 0)),
        0
    );

    $warning = null;
    if ($lookupUniverseAliases > 0 && $aliasPersistenceGap > 0) {
        $warning = 'Lookup evreni dolu ama kalıcı alias cache geriden geliyor.';
    } elseif ($persistedRows === 0) {
        $warning = 'Kalıcı alias cache henüz ısınmadı.';
    }

    return [
        'lookup_universe_markets' => $lookupUniverseMarkets,
        'lookup_universe_aliases' => $lookupUniverseAliases,
        'persisted_market_alias_rows' => $persistedRows,
        'persisted_market_alias_markets' => $persistedMarkets,
        'alias_persistence_gap' => $aliasPersistenceGap,
        'warning' => $warning,
    ];
}

function dashboard_classify_decision_flow(array $row): string
{
    $signalFamily = strtolower((string) ($row['signal_family'] ?? ''));
    $rawSource = strtolower((string) ($row['raw_source_signal'] ?? ''));
    $reason = strtolower((string) ($row['reason'] ?? ''));
    $strategyProfile = strtolower((string) ($row['strategy_profile'] ?? 'baseline'));

    if ($rawSource === 'discovery' && str_contains($reason, 'route_whale_orderflow_only')) {
        return 'discovery-route-only';
    }
    if (in_array($signalFamily, ['activity_orderflow', 'whale'], true)) {
        return $strategyProfile === 'sampling_relaxed' ? 'sampling-orderflow' : 'baseline-orderflow';
    }
    return 'other';
}

function dashboard_build_routing_breakdown(PDO $pdo): array
{
    $rows = dashboard_fetch_all(
        $pdo,
        "
        SELECT
            CASE
                WHEN raw_source_signal = 'discovery' AND reason = 'route_whale_orderflow_only' THEN 'discovery-route-only'
                WHEN signal_family IN ('activity_orderflow', 'whale') AND strategy_profile = 'sampling_relaxed' THEN 'sampling-orderflow'
                WHEN signal_family IN ('activity_orderflow', 'whale') THEN 'baseline-orderflow'
                ELSE 'other'
            END AS flow_classification,
            COUNT(*) AS count
        FROM decision_audit
        GROUP BY flow_classification
        HAVING flow_classification != 'other'
        ORDER BY count DESC, flow_classification ASC
        "
    );

    return array_map(
        static fn (array $row): array => [
            'flow_classification' => $row['flow_classification'] ?? 'other',
            'count' => (int) ($row['count'] ?? 0),
        ],
        $rows
    );
}

function dashboard_build_sampling_decision_summary(PDO $pdo): array
{
    $rows = dashboard_fetch_all(
        $pdo,
        "
        SELECT action, COUNT(*) AS count
        FROM decision_audit
        WHERE strategy_profile = 'sampling_relaxed'
          AND signal_family IN ('activity_orderflow', 'whale')
        GROUP BY action
        ORDER BY count DESC, action ASC
        "
    );

    $items = array_map(
        static fn (array $row): array => [
            'action' => strtolower((string) ($row['action'] ?? 'unknown')),
            'count' => (int) ($row['count'] ?? 0),
        ],
        $rows
    );

    $executeQuery = "
        SELECT COUNT(*) AS count
        FROM trades
        WHERE strategy_profile = 'sampling_relaxed'
          AND venue = 'polymarket'
    ";
    if (dashboard_table_has_column($pdo, 'trades', 'signal_family')) {
        $executeQuery .= " AND signal_family IN ('activity_orderflow', 'whale')";
    }
    $executeRow = dashboard_fetch_one($pdo, $executeQuery);
    $items[] = [
        'action' => 'execute',
        'count' => (int) (($executeRow['count'] ?? 0)),
    ];

    usort(
        $items,
        static function (array $left, array $right): int {
            $countCompare = ($right['count'] ?? 0) <=> ($left['count'] ?? 0);
            if ($countCompare !== 0) {
                return $countCompare;
            }
            return strcmp((string) ($left['action'] ?? ''), (string) ($right['action'] ?? ''));
        }
    );

    return $items;
}

function dashboard_build_mapping_miss_breakdown(PDO $pdo): array
{
    $rows = dashboard_fetch_all(
        $pdo,
        "
        SELECT reason, COUNT(*) AS count
        FROM decision_audit
        WHERE reason LIKE 'market_not_mapped%'
           OR reason = 'unsupported_side_filtered'
           OR reason = 'sell_side_not_supported'
        GROUP BY reason
        ORDER BY count DESC, reason ASC
        "
    );

    return array_map(
        static fn (array $row): array => [
            'reason' => $row['reason'] ?? 'market_not_mapped_unknown_token',
            'count' => (int) ($row['count'] ?? 0),
        ],
        $rows
    );
}

function dashboard_build_source_quality_summary(PDO $pdo): array
{
    $row = dashboard_fetch_one(
        $pdo,
        "
        SELECT
            COALESCE(SUM(CASE WHEN signal_family IN ('activity_orderflow', 'whale') THEN 1 ELSE 0 END), 0) AS total_orderflow,
            COALESCE(SUM(CASE WHEN signal_family IN ('activity_orderflow', 'whale') AND reason NOT LIKE 'market_not_mapped%' THEN 1 ELSE 0 END), 0) AS orderflow_after_mapping,
            COALESCE(SUM(CASE WHEN raw_source_signal = 'discovery' AND reason = 'route_whale_orderflow_only' THEN 1 ELSE 0 END), 0) AS discovery_route_only,
            COALESCE(SUM(CASE WHEN signal_family IN ('activity_orderflow', 'whale') AND strategy_profile = 'sampling_relaxed' THEN 1 ELSE 0 END), 0) AS sampling_orderflow,
            COALESCE(SUM(CASE WHEN signal_family IN ('activity_orderflow', 'whale') AND strategy_profile != 'sampling_relaxed' THEN 1 ELSE 0 END), 0) AS baseline_orderflow,
            COALESCE(SUM(CASE WHEN reason IN ('unsupported_side_filtered', 'sell_side_not_supported') THEN 1 ELSE 0 END), 0) AS unsupported_side_filtered
        FROM decision_audit
        "
    ) ?? [];

    return [
        'total_orderflow' => (int) ($row['total_orderflow'] ?? 0),
        'orderflow_after_mapping' => (int) ($row['orderflow_after_mapping'] ?? 0),
        'discovery_route_only' => (int) ($row['discovery_route_only'] ?? 0),
        'sampling_orderflow' => (int) ($row['sampling_orderflow'] ?? 0),
        'baseline_orderflow' => (int) ($row['baseline_orderflow'] ?? 0),
        'unsupported_side_filtered' => (int) ($row['unsupported_side_filtered'] ?? 0),
    ];
}

function dashboard_build_unsupported_side_summary(PDO $pdo): array
{
    $rows = dashboard_fetch_all(
        $pdo,
        "
        SELECT reason, COUNT(*) AS count
        FROM decision_audit
        WHERE reason IN ('unsupported_side_filtered', 'sell_side_not_supported')
        GROUP BY reason
        ORDER BY count DESC, reason ASC
        "
    );

    return array_map(
        static fn (array $row): array => [
            'reason' => $row['reason'] ?? 'unsupported_side_filtered',
            'count' => (int) ($row['count'] ?? 0),
        ],
        $rows
    );
}

function dashboard_augment_recent_decisions(PDO $pdo, array $payload): array
{
    if (!dashboard_table_has_column($pdo, 'decision_audit', 'hot_window_promoted')) {
        return $payload;
    }

    $payload['recent_decisions'] = dashboard_fetch_all(
        $pdo,
        'SELECT occurred_at, venue, market_id, category, signal_family, strategy_profile, raw_source_signal, action, reason, decision_score, threshold, trade_size, confidence, mapping_stage, lazy_lookup_attempted, lazy_lookup_hit, hot_window_promoted FROM decision_audit ORDER BY id DESC LIMIT 20'
    );
    $payload['recent_decisions'] = array_map(
        static function (array $row): array {
            $row['flow_classification'] = dashboard_classify_decision_flow($row);
            return $row;
        },
        $payload['recent_decisions']
    );
    return $payload;
}

function dashboard_augment_payload(array $payload): array
{
    $warnings = [];
    $pdo = dashboard_open_db($warnings);
    $statusMetrics = dashboard_parse_status_metrics($payload['service_log_excerpt'] ?? []);
    if ($pdo !== null) {
        $payload = array_merge($payload, dashboard_build_unresolved_alias_summary($pdo));
        $payload['runtime_summary'] = array_merge(
            $payload['runtime_summary'] ?? [],
            dashboard_build_hot_window_summary($pdo, $statusMetrics)
        );
        $payload['routing_breakdown'] = dashboard_build_routing_breakdown($pdo);
        $payload['sampling_decision_summary'] = dashboard_build_sampling_decision_summary($pdo);
        $payload['mapping_miss_breakdown'] = dashboard_build_mapping_miss_breakdown($pdo);
        $payload['sampling_reject_breakdown'] = dashboard_build_sampling_reject_breakdown($pdo);
        $payload['alias_persistence_summary'] = dashboard_build_alias_persistence_summary($pdo, $payload['runtime_summary'] ?? []);
        $payload['source_quality_summary'] = dashboard_build_source_quality_summary($pdo);
        $payload['unsupported_side_summary'] = dashboard_build_unsupported_side_summary($pdo);
        $payload = dashboard_augment_recent_decisions($pdo, $payload);
    } else {
        $payload['top_unresolved_aliases'] = [];
        $payload['recent_unresolved_aliases'] = [];
        $payload['routing_breakdown'] = [];
        $payload['sampling_decision_summary'] = [];
        $payload['mapping_miss_breakdown'] = [];
        $payload['sampling_reject_breakdown'] = [];
        $payload['alias_persistence_summary'] = [];
        $payload['source_quality_summary'] = [];
        $payload['unsupported_side_summary'] = [];
        $payload['runtime_summary'] = array_merge(
            $payload['runtime_summary'] ?? [],
            [
                'hot_window_markets' => 0,
                'hot_window_hits' => 0,
                'hot_window_promotions' => 0,
                'hot_window_expiries' => 0,
                'active_window_misses' => 0,
                'active_window_miss_rate' => 0.0,
                'resolver_hit_rate' => 0.0,
            ]
        );
    }

    $translatedWarnings = array_map('dashboard_translate_warning', array_merge($payload['warnings'] ?? [], $warnings));
    $payload['warnings'] = array_values(array_unique($translatedWarnings));
    return $payload;
}
