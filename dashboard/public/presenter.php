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
         FROM " . dashboard_decision_audit_window_sql('decision_audit') . "
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

function dashboard_build_hot_window_summary(PDO $pdo, array $runtimeSummary): array
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

    $summary = $defaults;
    foreach (['hot_window_markets', 'hot_window_expiries', 'hot_window_hits', 'hot_window_promotions', 'active_window_misses'] as $key) {
        if (array_key_exists($key, $runtimeSummary)) {
            $summary[$key] = (int) $runtimeSummary[$key];
        }
    }
    if (array_key_exists('active_window_miss_rate', $runtimeSummary)) {
        $summary['active_window_miss_rate'] = (float) $runtimeSummary['active_window_miss_rate'];
    }
    if (array_key_exists('resolver_hit_rate', $runtimeSummary)) {
        $summary['resolver_hit_rate'] = (float) $runtimeSummary['resolver_hit_rate'];
    }
    if (($runtimeSummary['live_metrics_available'] ?? false) === true) {
        return $summary;
    }

    $decisionStats = dashboard_fetch_one(
        $pdo,
        "
        WITH " . dashboard_decision_audit_window_cte('recent_decisions') . "
        SELECT
            COALESCE(SUM(CASE WHEN mapping_stage = 'hot_window' AND reason NOT LIKE 'market_not_mapped%' THEN 1 ELSE 0 END), 0) AS hot_window_hits,
            COALESCE(SUM(CASE WHEN reason = 'market_not_mapped_active_window' THEN 1 ELSE 0 END), 0) AS active_window_misses,
            COALESCE(SUM(CASE WHEN hot_window_promoted = 1 THEN 1 ELSE 0 END), 0) AS hot_window_promotions,
            COALESCE(SUM(CASE WHEN signal_family IN ('activity_orderflow', 'whale') THEN 1 ELSE 0 END), 0) AS total_orderflow,
            COALESCE(SUM(CASE WHEN signal_family IN ('activity_orderflow', 'whale') AND reason LIKE 'market_not_mapped%' THEN 1 ELSE 0 END), 0) AS unmapped_orderflow,
            COALESCE(SUM(CASE WHEN signal_family IN ('activity_orderflow', 'whale') AND mapping_stage = 'alias_cache' AND reason NOT LIKE 'market_not_mapped%' THEN 1 ELSE 0 END), 0) AS alias_cache_hits,
            COALESCE(SUM(CASE WHEN signal_family IN ('activity_orderflow', 'whale') AND lazy_lookup_hit = 1 THEN 1 ELSE 0 END), 0) AS lazy_lookup_hits
        FROM recent_decisions
        "
    ) ?: [];

    $totalOrderflow = (int) ($decisionStats['total_orderflow'] ?? 0);
    $mappedOrderflow = max($totalOrderflow - (int) ($decisionStats['unmapped_orderflow'] ?? 0), 0);
    $resolverHits = (int) ($decisionStats['alias_cache_hits'] ?? 0) + (int) ($decisionStats['lazy_lookup_hits'] ?? 0);

    $summary['hot_window_hits'] = (int) ($decisionStats['hot_window_hits'] ?? 0);
    $summary['hot_window_promotions'] = (int) ($decisionStats['hot_window_promotions'] ?? 0);
    $summary['active_window_misses'] = (int) ($decisionStats['active_window_misses'] ?? 0);
    $summary['active_window_miss_rate'] = $totalOrderflow > 0
        ? round(((int) $summary['active_window_misses'] / $totalOrderflow) * 100.0, 1)
        : 0.0;
    $summary['resolver_hit_rate'] = $mappedOrderflow > 0
        ? round(($resolverHits / $mappedOrderflow) * 100.0, 1)
        : 0.0;

    return $summary;
}

function dashboard_build_sampling_reject_breakdown(PDO $pdo): array
{
    $rows = dashboard_fetch_all(
        $pdo,
        "
        SELECT reason
        FROM " . dashboard_decision_audit_window_sql('decision_audit') . "
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
    $hydratedLookupMarkets = (int) ($runtimeSummary['hydrated_lookup_markets'] ?? 0);
    $hydratedLookupAliases = (int) ($runtimeSummary['hydrated_lookup_aliases'] ?? 0);
    $aliasPersistenceGap = max(
        (int) ($runtimeSummary['alias_persistence_gap'] ?? max($lookupUniverseAliases - $persistedRows, 0)),
        0
    );

    $warning = $runtimeSummary['lookup_hydration_warning'] ?? null;
    if ($warning === 'persisted_alias_rows_present_but_lookup_hydration_zero') {
        $warning = 'Kalici alias cache dolu ama canli lookup hydration sifir gorunuyor.';
    } elseif ($lookupUniverseAliases > 0 && $aliasPersistenceGap > 0) {
        $warning = 'Lookup evreni dolu ama kalıcı alias cache geriden geliyor.';
    } elseif ($persistedRows === 0) {
        $warning = 'Kalıcı alias cache henüz ısınmadı.';
    }

    return [
        'lookup_universe_markets' => $lookupUniverseMarkets,
        'lookup_universe_aliases' => $lookupUniverseAliases,
        'persisted_market_alias_rows' => $persistedRows,
        'persisted_market_alias_markets' => $persistedMarkets,
        'hydrated_lookup_markets' => $hydratedLookupMarkets,
        'hydrated_lookup_aliases' => $hydratedLookupAliases,
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
        WITH " . dashboard_decision_audit_window_cte('recent_decisions') . "
        SELECT
            CASE
                WHEN raw_source_signal = 'discovery' AND reason = 'route_whale_orderflow_only' THEN 'discovery-route-only'
                WHEN signal_family IN ('activity_orderflow', 'whale') AND strategy_profile = 'sampling_relaxed' THEN 'sampling-orderflow'
                WHEN signal_family IN ('activity_orderflow', 'whale') THEN 'baseline-orderflow'
                ELSE 'other'
            END AS flow_classification,
            COUNT(*) AS count
        FROM recent_decisions
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
        WITH " . dashboard_decision_audit_window_cte('recent_decisions') . "
        SELECT action, COUNT(*) AS count
        FROM recent_decisions
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
        WITH " . dashboard_decision_audit_window_cte('recent_decisions') . "
        SELECT reason, COUNT(*) AS count
        FROM recent_decisions
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
        WITH " . dashboard_decision_audit_window_cte('recent_decisions') . "
        SELECT
            COALESCE(SUM(CASE WHEN signal_family IN ('activity_orderflow', 'whale') THEN 1 ELSE 0 END), 0) AS total_orderflow,
            COALESCE(SUM(CASE WHEN signal_family IN ('activity_orderflow', 'whale') AND reason NOT LIKE 'market_not_mapped%' THEN 1 ELSE 0 END), 0) AS orderflow_after_mapping,
            COALESCE(SUM(CASE WHEN raw_source_signal = 'discovery' AND reason = 'route_whale_orderflow_only' THEN 1 ELSE 0 END), 0) AS discovery_route_only,
            COALESCE(SUM(CASE WHEN signal_family IN ('activity_orderflow', 'whale') AND strategy_profile = 'sampling_relaxed' THEN 1 ELSE 0 END), 0) AS sampling_orderflow,
            COALESCE(SUM(CASE WHEN signal_family IN ('activity_orderflow', 'whale') AND strategy_profile != 'sampling_relaxed' THEN 1 ELSE 0 END), 0) AS baseline_orderflow,
            COALESCE(SUM(CASE WHEN reason IN ('unsupported_side_filtered', 'sell_side_not_supported') THEN 1 ELSE 0 END), 0) AS unsupported_side_filtered
        FROM recent_decisions
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
        WITH " . dashboard_decision_audit_window_cte('recent_decisions') . "
        SELECT reason, COUNT(*) AS count
        FROM recent_decisions
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

function dashboard_fetch_whale_wallet_source_counts(PDO $pdo): array
{
    if (dashboard_table_has_column($pdo, 'whale_wallet_sources', 'source_type')) {
        return dashboard_fetch_all(
            $pdo,
            "
            SELECT whale_wallet_sources.source_type, COUNT(DISTINCT LOWER(whale_wallet_sources.address)) AS count
            FROM whale_wallet_sources
            INNER JOIN whale_wallets ON LOWER(whale_wallets.address) = LOWER(whale_wallet_sources.address)
            WHERE whale_wallets.enabled = 1
            GROUP BY whale_wallet_sources.source_type
            ORDER BY whale_wallet_sources.source_type
            "
        );
    }

    return dashboard_fetch_all(
        $pdo,
        'SELECT source_type, COUNT(*) AS count FROM whale_wallets WHERE enabled = 1 GROUP BY source_type ORDER BY source_type'
    );
}

function dashboard_build_whale_universe_summary(PDO $pdo, array $runtimeSummary): array
{
    $counts = dashboard_fetch_whale_wallet_source_counts($pdo);
    $summary = [
        'tracked_whales' => 0,
        'leaderboard_wallets' => 0,
        'activity_discovered_wallets' => 0,
        'graph_discovered_wallets' => 0,
        'trusted_whales' => 0,
    ];

    foreach ($counts as $row) {
        $sourceType = (string) ($row['source_type'] ?? '');
        $count = (int) ($row['count'] ?? 0);
        if ($sourceType === 'leaderboard') {
            $summary['leaderboard_wallets'] += $count;
        } elseif ($sourceType === 'activity_discovery') {
            $summary['activity_discovered_wallets'] += $count;
        } elseif ($sourceType === 'graph_discovery') {
            $summary['graph_discovered_wallets'] += $count;
        }
    }

    $trackedRow = dashboard_fetch_one($pdo, 'SELECT COUNT(*) AS count FROM whale_wallets WHERE enabled = 1');
    $summary['tracked_whales'] = (int) (($trackedRow['count'] ?? 0));

    $trustedRow = dashboard_fetch_one(
        $pdo,
        "
        SELECT COUNT(*) AS count
        FROM whale_stats
        WHERE total_trades > 0
        "
    );
    $summary['trusted_whales'] = (int) (($trustedRow['count'] ?? 0));

    return $summary;
}

function dashboard_build_trusted_whale_summary(PDO $pdo): array
{
    return dashboard_fetch_all(
        $pdo,
        "
        SELECT
            whale_stats.address,
            COALESCE(whale_wallets.source_type, 'trusted_only') AS source_type,
            COALESCE(whale_wallets.discovery_score, 0) AS discovery_score,
            whale_stats.trust_score,
            whale_stats.total_trades,
            whale_stats.wins,
            whale_stats.total_pnl,
            CASE
                WHEN whale_stats.total_trades > 0
                    THEN ROUND((CAST(whale_stats.wins AS REAL) / whale_stats.total_trades) * 100.0, 1)
                ELSE NULL
            END AS win_rate
        FROM whale_stats
        LEFT JOIN whale_wallets ON whale_wallets.address = whale_stats.address
        WHERE whale_stats.total_trades > 0
        ORDER BY whale_stats.trust_score DESC, whale_stats.total_trades DESC, whale_stats.total_pnl DESC
        LIMIT 10
        "
    );
}

function dashboard_build_whale_copy_summary(PDO $pdo): array
{
    $row = dashboard_fetch_one(
        $pdo,
        "
        WITH " . dashboard_decision_audit_window_cte('recent_decisions') . "
        SELECT
            COALESCE(SUM(CASE WHEN signal_family IN ('activity_orderflow', 'whale') THEN 1 ELSE 0 END), 0) AS total_whale_events,
            COALESCE(SUM(CASE WHEN signal_family IN ('activity_orderflow', 'whale')
                                  AND COALESCE(reason, '') NOT LIKE 'market_not_mapped%'
                                  AND COALESCE(reason, '') NOT LIKE '%unsupported_side_filtered%'
                                  AND COALESCE(reason, '') NOT LIKE '%sell_side_not_supported%'
                             THEN 1 ELSE 0 END), 0) AS resolved_whale_events,
            COALESCE(SUM(CASE WHEN signal_family IN ('activity_orderflow', 'whale')
                                  AND COALESCE(reason, '') NOT LIKE 'market_not_mapped%'
                                  AND COALESCE(reason, '') NOT LIKE '%unsupported_side_filtered%'
                                  AND COALESCE(reason, '') NOT LIKE '%sell_side_not_supported%'
                                  AND COALESCE(reason, '') NOT LIKE '%route_whale_orderflow_only%'
                             THEN 1 ELSE 0 END), 0) AS whale_copy_candidates,
            COALESCE(SUM(CASE WHEN signal_family IN ('activity_orderflow', 'whale')
                                  AND action = 'reject'
                                  AND COALESCE(reason, '') NOT LIKE 'market_not_mapped%'
                                  AND COALESCE(reason, '') NOT LIKE '%unsupported_side_filtered%'
                                  AND COALESCE(reason, '') NOT LIKE '%sell_side_not_supported%'
                                  AND COALESCE(reason, '') NOT LIKE '%route_whale_orderflow_only%'
                             THEN 1 ELSE 0 END), 0) AS gated_rejects,
            COALESCE(SUM(CASE WHEN signal_family IN ('activity_orderflow', 'whale')
                                  AND action = 'decision'
                             THEN 1 ELSE 0 END), 0) AS gated_decisions
        FROM recent_decisions
        "
    ) ?: [];

    $executeQuery = "
        SELECT COUNT(*) AS count
        FROM trades
        WHERE venue = 'polymarket'
          AND strategy_profile IN ('baseline', 'sampling_relaxed')
          AND source_signal IN ('activity', 'whale_tracker')
    ";
    $executeRow = dashboard_fetch_one($pdo, $executeQuery) ?: ['count' => 0];

    return [
        'total_whale_events' => (int) ($row['total_whale_events'] ?? 0),
        'resolved_whale_events' => (int) ($row['resolved_whale_events'] ?? 0),
        'whale_copy_candidates' => (int) ($row['whale_copy_candidates'] ?? 0),
        'gated_rejects' => (int) ($row['gated_rejects'] ?? 0),
        'gated_decisions' => (int) ($row['gated_decisions'] ?? 0),
        'gated_executes' => (int) ($executeRow['count'] ?? 0),
    ];
}

function dashboard_build_gated_reject_breakdown(PDO $pdo): array
{
    $rows = dashboard_fetch_all(
        $pdo,
        "
        SELECT reason
        FROM " . dashboard_decision_audit_window_sql('decision_audit') . "
        WHERE signal_family IN ('activity_orderflow', 'whale')
          AND action = 'reject'
          AND COALESCE(reason, '') NOT LIKE 'market_not_mapped%'
          AND COALESCE(reason, '') NOT LIKE '%unsupported_side_filtered%'
          AND COALESCE(reason, '') NOT LIKE '%sell_side_not_supported%'
          AND COALESCE(reason, '') NOT LIKE '%route_whale_orderflow_only%'
        ORDER BY id DESC
        LIMIT 300
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
    foreach (array_slice($counts, 0, 10, true) as $reason => $count) {
        $items[] = [
            'reason' => $reason,
            'count' => $count,
        ];
    }

    return $items;
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
    if ($pdo !== null) {
        $payload = array_merge($payload, dashboard_build_unresolved_alias_summary($pdo));
        $payload['runtime_summary'] = array_merge(
            $payload['runtime_summary'] ?? [],
            dashboard_build_hot_window_summary($pdo, $payload['runtime_summary'] ?? [])
        );
        $payload['whale_wallet_counts'] = dashboard_fetch_whale_wallet_source_counts($pdo);
        $payload['routing_breakdown'] = dashboard_build_routing_breakdown($pdo);
        $payload['sampling_decision_summary'] = dashboard_build_sampling_decision_summary($pdo);
        $payload['mapping_miss_breakdown'] = dashboard_build_mapping_miss_breakdown($pdo);
        $payload['sampling_reject_breakdown'] = dashboard_build_sampling_reject_breakdown($pdo);
        $payload['alias_persistence_summary'] = dashboard_build_alias_persistence_summary($pdo, $payload['runtime_summary'] ?? []);
        $payload['source_quality_summary'] = dashboard_build_source_quality_summary($pdo);
        $payload['unsupported_side_summary'] = dashboard_build_unsupported_side_summary($pdo);
        $payload['whale_universe_summary'] = dashboard_build_whale_universe_summary($pdo, $payload['runtime_summary'] ?? []);
        $payload['trusted_whale_summary'] = dashboard_build_trusted_whale_summary($pdo);
        $payload['whale_copy_summary'] = dashboard_build_whale_copy_summary($pdo);
        $payload['gated_reject_breakdown'] = dashboard_build_gated_reject_breakdown($pdo);
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
        $payload['whale_universe_summary'] = [];
        $payload['trusted_whale_summary'] = [];
        $payload['whale_copy_summary'] = [];
        $payload['gated_reject_breakdown'] = [];
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
