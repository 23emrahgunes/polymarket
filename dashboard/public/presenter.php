<?php
declare(strict_types=1);

require_once dirname(__DIR__) . '/lib/data.php';

const DASHBOARD_TECHNICAL_NEAR_THRESHOLD_GAP = 0.10;

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

function dashboard_table_exists(PDO $pdo, string $table): bool
{
    $safeTable = preg_replace('/[^A-Za-z0-9_]/', '', $table);
    $statement = $pdo->prepare("SELECT name FROM sqlite_master WHERE type = 'table' AND name = :table LIMIT 1");
    $statement->execute([':table' => $safeTable]);
    $row = $statement->fetch(PDO::FETCH_ASSOC);
    return is_array($row);
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

function dashboard_decode_inputs_json(?string $json): array
{
    if ($json === null || trim($json) === '') {
        return [];
    }

    try {
        $decoded = json_decode($json, true, 512, JSON_THROW_ON_ERROR);
    } catch (Throwable $exception) {
        return [];
    }

    return is_array($decoded) ? $decoded : [];
}

function dashboard_reason_parts(?string $reasonText): array
{
    $reasons = [];
    foreach (explode(',', trim((string) $reasonText)) as $reason) {
        $reason = trim($reason);
        if ($reason === '' || in_array($reason, $reasons, true)) {
            continue;
        }
        $reasons[] = $reason;
    }
    return $reasons;
}

function dashboard_float_input(array $inputs, string $key): ?float
{
    if (!array_key_exists($key, $inputs) || $inputs[$key] === null || $inputs[$key] === '') {
        return null;
    }
    if (!is_numeric($inputs[$key])) {
        return null;
    }
    return (float) $inputs[$key];
}

function dashboard_average(array $values): float
{
    return $values === [] ? 0.0 : round(array_sum($values) / count($values), 4);
}

function dashboard_env_float(string $name, float $default): float
{
    $raw = getenv($name);
    if ($raw === false || trim((string) $raw) === '') {
        return $default;
    }
    return is_numeric($raw) ? (float) $raw : $default;
}

function dashboard_env_int(string $name, int $default): int
{
    $raw = getenv($name);
    if ($raw === false || trim((string) $raw) === '') {
        return $default;
    }
    return is_numeric($raw) ? (int) $raw : $default;
}

function dashboard_technical_final_score(array $inputs): ?float
{
    foreach (['post_final_score_recovery_score', 'post_spread_recovery_score', 'post_microstructure_score', 'pre_microstructure_score'] as $key) {
        $value = dashboard_float_input($inputs, $key);
        if ($value !== null) {
            return $value;
        }
    }
    return null;
}

function dashboard_inputs_is_gated_whale_copy(array $inputs): bool
{
    return strtolower((string) ($inputs['copy_policy'] ?? '')) === 'gated_whale_copy';
}

function dashboard_inputs_is_relaxed_gate(array $inputs): bool
{
    return !empty($inputs['whale_copy_relaxed_gate']);
}

function dashboard_inputs_original_side(array $inputs): string
{
    return strtoupper(trim((string) ($inputs['original_side'] ?? '')));
}

function dashboard_reason_is_excluded_whale_copy(?string $reasonText): bool
{
    $reasonText = (string) ($reasonText ?? '');
    return str_contains($reasonText, 'market_not_mapped')
        || str_contains($reasonText, 'unsupported_side_filtered')
        || str_contains($reasonText, 'sell_side_not_supported')
        || str_contains($reasonText, 'route_whale_orderflow_only');
}

function dashboard_fetch_recent_whale_copy_audit_rows(PDO $pdo): array
{
    return dashboard_fetch_all(
        $pdo,
        "
        SELECT occurred_at, market_id, action, reason, inputs_json
        FROM " . dashboard_decision_audit_window_sql('decision_audit') . "
        WHERE signal_family IN ('activity_orderflow', 'whale')
          AND action IN ('reject', 'decision', 'execute')
        ORDER BY id DESC
        LIMIT 500
        "
    );
}

function dashboard_fetch_recent_binance_technical_rows(PDO $pdo): array
{
    return dashboard_fetch_all(
        $pdo,
        "
        SELECT occurred_at, venue, market_id, action, reason, inputs_json
        FROM " . dashboard_decision_audit_window_sql('decision_audit') . "
        WHERE strategy_profile = 'binance_technical_sampling'
          AND action IN ('reject', 'decision', 'execute')
        ORDER BY id DESC
        LIMIT 300
        "
    );
}

function dashboard_parse_utc_datetime(?string $value): ?DateTimeImmutable
{
    $text = trim((string) $value);
    if ($text === '') {
        return null;
    }
    try {
        $parsed = new DateTimeImmutable($text, new DateTimeZone('UTC'));
    } catch (Throwable $exception) {
        return null;
    }
    return $parsed->setTimezone(new DateTimeZone('UTC'));
}

function dashboard_filter_rows_within_minutes(array $rows, int $minutes): array
{
    $cutoff = new DateTimeImmutable('now', new DateTimeZone('UTC'));
    $cutoff = $cutoff->sub(new DateInterval(sprintf('PT%dM', max(1, $minutes))));
    $filtered = [];
    foreach ($rows as $row) {
        $occurredAt = dashboard_parse_utc_datetime($row['occurred_at'] ?? null);
        if ($occurredAt === null || $occurredAt < $cutoff) {
            continue;
        }
        $filtered[] = $row;
    }
    return $filtered;
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
        WHERE action IN ('reject', 'decision')
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

function dashboard_build_binance_technical_summary_from_rows(array $rows): array
{
    $summary = [
        'decisions' => 0,
        'rejects' => 0,
        'executes' => 0,
        'long_signals' => 0,
        'short_signals' => 0,
        'forced_samples' => 0,
    ];

    foreach ($rows as $row) {
        $action = strtolower((string) ($row['action'] ?? ''));
        $inputs = dashboard_decode_inputs_json($row['inputs_json'] ?? null);
        $direction = strtoupper((string) ($inputs['signal_direction'] ?? $inputs['direction'] ?? ''));
        if ($direction === 'LONG') {
            $summary['long_signals']++;
        } elseif ($direction === 'SHORT') {
            $summary['short_signals']++;
        }
        if (!empty($inputs['force_sample'])) {
            $summary['forced_samples']++;
        }
        if ($action === 'decision') {
            $summary['decisions']++;
        } elseif ($action === 'reject') {
            $summary['rejects']++;
        } elseif ($action === 'execute') {
            $summary['executes']++;
        }
    }

    return $summary;
}

function dashboard_build_binance_technical_summary(PDO $pdo): array
{
    return dashboard_build_binance_technical_summary_from_rows(dashboard_fetch_recent_binance_technical_rows($pdo));
}

function dashboard_build_binance_technical_reject_breakdown_from_rows(array $rows): array
{
    $counts = [];
    foreach ($rows as $row) {
        if (strtolower((string) ($row['action'] ?? '')) !== 'reject') {
            continue;
        }
        foreach (dashboard_reason_parts($row['reason'] ?? null) as $reason) {
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

function dashboard_build_binance_technical_reject_breakdown(PDO $pdo): array
{
    return dashboard_build_binance_technical_reject_breakdown_from_rows(dashboard_fetch_recent_binance_technical_rows($pdo));
}

function dashboard_binance_technical_active_symbol_count(array $rows, array $runtimeSummary = []): int
{
    $snapshotCount = (int) ($runtimeSummary['binance_technical_active_symbol_count'] ?? 0);
    if ($snapshotCount > 0) {
        return $snapshotCount;
    }

    $symbols = [];
    foreach ($rows as $row) {
        $inputs = dashboard_decode_inputs_json($row['inputs_json'] ?? null);
        $symbol = trim((string) ($inputs['symbol'] ?? ''));
        if ($symbol !== '') {
            $symbols[$symbol] = true;
        }
    }
    return count($symbols);
}

function dashboard_build_binance_technical_gate_funnel_from_rows(array $rows): array
{
    $summary = [
        'scanned_symbols' => 0,
        'directional_signals' => 0,
        'recovered_alignment_signals' => 0,
        'spread_rejects' => 0,
        'score_rejects' => 0,
        'decisions' => 0,
        'executes' => 0,
    ];
    $symbols = [];

    foreach ($rows as $row) {
        $action = strtolower((string) ($row['action'] ?? ''));
        $inputs = dashboard_decode_inputs_json($row['inputs_json'] ?? null);
        $symbol = trim((string) ($inputs['symbol'] ?? ''));
        if ($symbol !== '') {
            $symbols[$symbol] = true;
        }
        $direction = strtoupper((string) ($inputs['signal_direction'] ?? $inputs['direction'] ?? ''));
        if ($direction === 'LONG' || $direction === 'SHORT') {
            $summary['directional_signals']++;
        }
        if (!empty($inputs['alignment_recovery_applied']) || !empty($inputs['technical_alignment_recovered'])) {
            $summary['recovered_alignment_signals']++;
        }
        foreach (dashboard_reason_parts($row['reason'] ?? null) as $reason) {
            if ($reason === 'futures_spread_wide') {
                $summary['spread_rejects']++;
            } elseif ($reason === 'score_below_threshold') {
                $summary['score_rejects']++;
            }
        }
        if ($action === 'decision') {
            $summary['decisions']++;
        } elseif ($action === 'execute') {
            $summary['executes']++;
        }
    }

    $summary['scanned_symbols'] = count($symbols);
    return $summary;
}

function dashboard_build_binance_technical_gate_funnel(PDO $pdo): array
{
    return dashboard_build_binance_technical_gate_funnel_from_rows(dashboard_fetch_recent_binance_technical_rows($pdo));
}

function dashboard_build_binance_technical_recovery_summary_from_rows(array $rows, array $runtimeSummary = []): array
{
    $summary = [
        'recovery_applied_count' => 0,
        'alignment_recovery_hits' => 0,
        'microstructure_recovery_hits' => 0,
        'spread_recovery_hits' => 0,
        'microstructure_recovery_v2_hits' => 0,
        'spread_recovery_v2_hits' => 0,
        'force_recovery_candidates' => 0,
        'microstructure_candidate_floor_hits' => 0,
        'force_sample_hits' => 0,
        'final_score_recovery_hits' => 0,
        'near_threshold_candidates' => 0,
        'score_recovery_candidates' => 0,
        'score_recovery_passes' => 0,
        'active_symbol_count' => dashboard_binance_technical_active_symbol_count($rows, $runtimeSummary),
    ];

    foreach ($rows as $row) {
        $inputs = dashboard_decode_inputs_json($row['inputs_json'] ?? null);
        if (!empty($inputs['technical_recovery_applied'])) {
            $summary['recovery_applied_count']++;
        }
        if (!empty($inputs['alignment_recovery_applied']) || !empty($inputs['technical_alignment_recovered'])) {
            $summary['alignment_recovery_hits']++;
        }
        if (!empty($inputs['microstructure_recovery_applied'])) {
            $summary['microstructure_recovery_hits']++;
        }
        if (!empty($inputs['spread_recovery_applied'])) {
            $summary['spread_recovery_hits']++;
        }
        if (!empty($inputs['microstructure_recovery_v2_applied'])) {
            $summary['microstructure_recovery_v2_hits']++;
        }
        if (!empty($inputs['spread_recovery_v2_applied'])) {
            $summary['spread_recovery_v2_hits']++;
        }
        if (!empty($inputs['force_recovery_candidate'])) {
            $summary['force_recovery_candidates']++;
            if (array_key_exists('pre_microstructure_score', $inputs)) {
                $preScore = (float) ($inputs['pre_microstructure_score'] ?? 0.0);
                $forceFloor = (float) ($inputs['force_min_score'] ?? 0.5);
                if ($preScore < $forceFloor) {
                    $summary['microstructure_candidate_floor_hits']++;
                }
            }
        }
        if (!empty($inputs['force_sample']) || !empty($inputs['force_sample_ready'])) {
            $summary['force_sample_hits']++;
        }
        if (!empty($inputs['final_score_recovery_applied'])) {
            $summary['final_score_recovery_hits']++;
        }
        if (!empty($inputs['near_threshold_candidate'])) {
            $summary['near_threshold_candidates']++;
        }
        if (!empty($inputs['score_recovery_candidate'])) {
            $summary['score_recovery_candidates']++;
        }
        if (!empty($inputs['score_recovery_passed'])) {
            $summary['score_recovery_passes']++;
        }
    }

    return $summary;
}

function dashboard_build_binance_technical_recovery_summary(PDO $pdo, array $runtimeSummary = []): array
{
    return dashboard_build_binance_technical_recovery_summary_from_rows(dashboard_fetch_recent_binance_technical_rows($pdo), $runtimeSummary);
}

function dashboard_build_binance_futures_snapshot_summary_from_rows(array $rows): array
{
    $summary = [
        'trusted_ticker_book_hits' => 0,
        'trusted_info_book_hits' => 0,
        'trusted_orderbook_book_hits' => 0,
        'orderbook_fallback_hits' => 0,
        'orderbook_reprice_hits' => 0,
        'missing_bid_ask_rejects' => 0,
        'snapshot_untrusted_rejects' => 0,
    ];

    foreach ($rows as $row) {
        $inputs = dashboard_decode_inputs_json($row['inputs_json'] ?? null);
        $snapshotQuality = strtolower((string) ($inputs['snapshot_quality'] ?? ''));
        if ($snapshotQuality === 'trusted_ticker_book') {
            $summary['trusted_ticker_book_hits']++;
        } elseif ($snapshotQuality === 'trusted_info_book') {
            $summary['trusted_info_book_hits']++;
        } elseif ($snapshotQuality === 'trusted_orderbook_book') {
            $summary['trusted_orderbook_book_hits']++;
        }
        if (!empty($inputs['orderbook_fallback_used'])) {
            $summary['orderbook_fallback_hits']++;
        }
        if (!empty($inputs['orderbook_repriced'])) {
            $summary['orderbook_reprice_hits']++;
        }
        foreach (dashboard_reason_parts($row['reason'] ?? null) as $reason) {
            if ($reason === 'futures_bid_ask_missing') {
                $summary['missing_bid_ask_rejects']++;
            } elseif ($reason === 'futures_snapshot_untrusted') {
                $summary['snapshot_untrusted_rejects']++;
            }
        }
    }

    return $summary;
}

function dashboard_build_binance_futures_snapshot_summary(PDO $pdo): array
{
    return dashboard_build_binance_futures_snapshot_summary_from_rows(dashboard_fetch_recent_binance_technical_rows($pdo));
}

function dashboard_build_binance_technical_score_component_summary_from_rows(array $rows): array
{
    $values = [
        'rsi_component' => [],
        'macd_component' => [],
        'momentum_component' => [],
        'volume_component' => [],
        'microstructure_component' => [],
        'macd_normalizer' => [],
        'momentum_normalizer' => [],
        'volume_ratio_normalizer' => [],
        'effective_min_score' => [],
        'final_score' => [],
    ];
    $sampleCount = 0;

    foreach ($rows as $row) {
        $inputs = dashboard_decode_inputs_json($row['inputs_json'] ?? null);
        $hasComponent = false;
        foreach (['rsi_component', 'macd_component', 'momentum_component', 'volume_component', 'microstructure_component', 'macd_normalizer', 'momentum_normalizer', 'volume_ratio_normalizer', 'effective_min_score'] as $key) {
            $value = dashboard_float_input($inputs, $key);
            if ($value !== null) {
                $values[$key][] = $value;
                $hasComponent = true;
            }
        }
        $finalScore = dashboard_technical_final_score($inputs);
        if ($finalScore !== null) {
            $values['final_score'][] = $finalScore;
            $hasComponent = true;
        }
        if ($hasComponent) {
            $sampleCount++;
        }
    }

    return [
        'sample_count' => $sampleCount,
        'avg_rsi_component' => dashboard_average($values['rsi_component']),
        'avg_macd_component' => dashboard_average($values['macd_component']),
        'avg_momentum_component' => dashboard_average($values['momentum_component']),
        'avg_volume_component' => dashboard_average($values['volume_component']),
        'avg_microstructure_component' => dashboard_average($values['microstructure_component']),
        'avg_macd_normalizer' => dashboard_average($values['macd_normalizer']),
        'avg_momentum_normalizer' => dashboard_average($values['momentum_normalizer']),
        'avg_volume_ratio_normalizer' => dashboard_average($values['volume_ratio_normalizer']),
        'avg_effective_min_score' => dashboard_average($values['effective_min_score']),
        'avg_final_score' => dashboard_average($values['final_score']),
    ];
}

function dashboard_build_binance_technical_score_component_summary(PDO $pdo): array
{
    return dashboard_build_binance_technical_score_component_summary_from_rows(dashboard_fetch_recent_binance_technical_rows($pdo));
}

function dashboard_build_binance_technical_score_gap_summary_from_rows(array $rows): array
{
    $gaps = [];
    $belowThresholdCount = 0;
    $nearThresholdCount = 0;
    $deepBelowThresholdCount = 0;

    foreach ($rows as $row) {
        $inputs = dashboard_decode_inputs_json($row['inputs_json'] ?? null);
        $gap = dashboard_float_input($inputs, 'score_gap_to_threshold');
        if ($gap === null) {
            $threshold = dashboard_float_input($inputs, 'effective_min_score');
            $finalScore = dashboard_technical_final_score($inputs);
            $gap = ($threshold !== null && $finalScore !== null) ? max($threshold - $finalScore, 0.0) : null;
        }
        if ($gap === null) {
            continue;
        }
        $gaps[] = $gap;
        if ($gap > 0.0 || in_array('score_below_threshold', dashboard_reason_parts($row['reason'] ?? null), true)) {
            $belowThresholdCount++;
        }
        if ($gap > 0.0 && $gap <= DASHBOARD_TECHNICAL_NEAR_THRESHOLD_GAP) {
            $nearThresholdCount++;
        } elseif ($gap > DASHBOARD_TECHNICAL_NEAR_THRESHOLD_GAP) {
            $deepBelowThresholdCount++;
        }
    }

    return [
        'avg_score_gap_to_threshold' => dashboard_average($gaps),
        'below_threshold_count' => $belowThresholdCount,
        'near_threshold_count' => $nearThresholdCount,
        'deep_below_threshold_count' => $deepBelowThresholdCount,
    ];
}

function dashboard_build_binance_technical_score_gap_summary(PDO $pdo): array
{
    return dashboard_build_binance_technical_score_gap_summary_from_rows(dashboard_fetch_recent_binance_technical_rows($pdo));
}

function dashboard_build_binance_technical_position_pressure_summary(PDO $pdo, array $runtimeSummary = [], ?array $rows = null): array
{
    $rows = $rows ?? dashboard_fetch_recent_binance_technical_rows($pdo);
    $futuresOpen = dashboard_fetch_one(
        $pdo,
        "SELECT COUNT(*) AS open_count, COALESCE(SUM(notional_usd), 0) AS open_notional FROM venue_positions WHERE status = 'OPEN' AND venue = 'binance_futures'"
    ) ?? ['open_count' => 0, 'open_notional' => 0.0];
    $spotOpen = dashboard_fetch_one(
        $pdo,
        "SELECT COUNT(*) AS open_count, COALESCE(SUM(notional_usd), 0) AS open_notional FROM venue_positions WHERE status = 'OPEN' AND venue = 'binance_spot'"
    ) ?? ['open_count' => 0, 'open_notional' => 0.0];

    $futuresOpenCount = (int) ($futuresOpen['open_count'] ?? 0);
    $spotOpenCount = (int) ($spotOpen['open_count'] ?? 0);
    $futuresOpenNotional = (float) ($futuresOpen['open_notional'] ?? 0.0);
    $spotOpenNotional = (float) ($spotOpen['open_notional'] ?? 0.0);
    $openPositions = $futuresOpenCount + $spotOpenCount;
    $openNotionalUsd = $futuresOpenNotional + $spotOpenNotional;
    $remainingCapacityUsd = max(0.0, dashboard_env_float('BINANCE_FUTURES_MAX_POSITION_USD', 250.0) - $futuresOpenNotional)
        + max(0.0, dashboard_env_float('BINANCE_SPOT_MAX_POSITION_USD', 200.0) - $spotOpenNotional);

    $anchorRow = dashboard_fetch_one(
        $pdo,
        "SELECT MAX(occurred_at) AS max_occurred_at FROM decision_audit WHERE strategy_profile = 'binance_technical_sampling' AND venue IN ('binance_futures', 'binance_spot')"
    );
    $recentExits = 0;
    $stopLossExits = 0;
    $takeProfitExits = 0;
    $capacityReleasedUsd60m = 0.0;
    $oldestOpenPositionMinutes = 0;
    $positionsOver30m = 0;
    $positionsOver60m = 0;
    $positionsOver120m = 0;
    $positionsOver240m = 0;
    if (!empty($anchorRow['max_occurred_at'])) {
        $exitWindow = dashboard_fetch_one(
            $pdo,
            "
            SELECT
                COUNT(*) AS recent_exits,
                COALESCE(SUM(CASE WHEN reason = 'STOP_LOSS' THEN 1 ELSE 0 END), 0) AS stop_loss_exits,
                COALESCE(SUM(CASE WHEN reason = 'TAKE_PROFIT' THEN 1 ELSE 0 END), 0) AS take_profit_exits,
                COALESCE(SUM(COALESCE(trade_size, 0)), 0) AS released_usd
            FROM decision_audit
            WHERE strategy_profile = 'binance_technical_sampling'
              AND venue IN ('binance_futures', 'binance_spot')
              AND action = 'exit'
              AND occurred_at >= datetime(?, '-60 minutes')
            ",
            [$anchorRow['max_occurred_at']]
        ) ?? ['recent_exits' => 0, 'stop_loss_exits' => 0, 'take_profit_exits' => 0];
        $recentExits = (int) ($exitWindow['recent_exits'] ?? 0);
        $stopLossExits = (int) ($exitWindow['stop_loss_exits'] ?? 0);
        $takeProfitExits = (int) ($exitWindow['take_profit_exits'] ?? 0);
        $capacityReleasedUsd60m = (float) ($exitWindow['released_usd'] ?? 0.0);

        if (dashboard_table_has_column($pdo, 'venue_positions', 'opened_at')) {
            $ageWindow = dashboard_fetch_one(
                $pdo,
                "
                SELECT
                    COALESCE(MAX(MAX((julianday(?) - julianday(opened_at)) * 24.0 * 60.0, 0)), 0) AS oldest_open_position_minutes,
                    COALESCE(SUM(CASE WHEN MAX((julianday(?) - julianday(opened_at)) * 24.0 * 60.0, 0) >= 30 THEN 1 ELSE 0 END), 0) AS positions_over_30m,
                    COALESCE(SUM(CASE WHEN MAX((julianday(?) - julianday(opened_at)) * 24.0 * 60.0, 0) >= 60 THEN 1 ELSE 0 END), 0) AS positions_over_60m,
                    COALESCE(SUM(CASE WHEN MAX((julianday(?) - julianday(opened_at)) * 24.0 * 60.0, 0) >= 120 THEN 1 ELSE 0 END), 0) AS positions_over_120m,
                    COALESCE(SUM(CASE WHEN MAX((julianday(?) - julianday(opened_at)) * 24.0 * 60.0, 0) >= 240 THEN 1 ELSE 0 END), 0) AS positions_over_240m
                FROM venue_positions
                WHERE status = 'OPEN'
                  AND venue IN ('binance_futures', 'binance_spot')
                  AND opened_at IS NOT NULL
                ",
                [
                    $anchorRow['max_occurred_at'],
                    $anchorRow['max_occurred_at'],
                    $anchorRow['max_occurred_at'],
                    $anchorRow['max_occurred_at'],
                    $anchorRow['max_occurred_at'],
                ]
            ) ?? [
                'oldest_open_position_minutes' => 0,
                'positions_over_30m' => 0,
                'positions_over_60m' => 0,
                'positions_over_120m' => 0,
                'positions_over_240m' => 0,
            ];
            $oldestOpenPositionMinutes = max(0, (int) round((float) ($ageWindow['oldest_open_position_minutes'] ?? 0.0)));
            $positionsOver30m = max(0, (int) ($ageWindow['positions_over_30m'] ?? 0));
            $positionsOver60m = max(0, (int) ($ageWindow['positions_over_60m'] ?? 0));
            $positionsOver120m = max(0, (int) ($ageWindow['positions_over_120m'] ?? 0));
            $positionsOver240m = max(0, (int) ($ageWindow['positions_over_240m'] ?? 0));
        }
    }

    $maxOpenPositionsRejects = 0;
    $maxTotalPositionUsdRejects = 0;
    $maxOrderUsdRejects = 0;
    $legacyMaxPositionExceededRejects = 0;
    $sizedDownEntries = 0;
    foreach ($rows as $row) {
        $inputs = dashboard_decode_inputs_json($row['inputs_json'] ?? null);
        if (($row['action'] ?? '') === 'reject') {
            foreach (dashboard_reason_parts($row['reason'] ?? null) as $reason) {
                if ($reason === 'max_open_positions_exceeded') {
                    $maxOpenPositionsRejects++;
                } elseif ($reason === 'max_total_position_usd_exceeded') {
                    $maxTotalPositionUsdRejects++;
                } elseif ($reason === 'max_order_usd_exceeded') {
                    $maxOrderUsdRejects++;
                } elseif ($reason === 'max_position_exceeded') {
                    $legacyMaxPositionExceededRejects++;
                }
            }
        }
        if (in_array(strtolower((string) ($row['action'] ?? '')), ['decision', 'execute'], true) && !empty($inputs['position_capacity_sized_down'])) {
            $sizedDownEntries++;
        }
    }

    return [
        'open_positions' => $openPositions,
        'open_notional_usd' => round($openNotionalUsd, 4),
        'remaining_capacity_usd' => round($remainingCapacityUsd, 4),
        'recent_exits_60m' => $recentExits,
        'stop_loss_exits_60m' => $stopLossExits,
        'take_profit_exits_60m' => $takeProfitExits,
        'oldest_open_position_minutes' => $oldestOpenPositionMinutes,
        'positions_over_30m' => $positionsOver30m,
        'positions_over_60m' => $positionsOver60m,
        'positions_over_120m' => $positionsOver120m,
        'positions_over_240m' => $positionsOver240m,
        'max_open_positions_rejects' => $maxOpenPositionsRejects,
        'max_total_position_usd_rejects' => $maxTotalPositionUsdRejects,
        'max_order_usd_rejects' => $maxOrderUsdRejects,
        'legacy_max_position_exceeded_rejects' => $legacyMaxPositionExceededRejects,
        'sized_down_entries' => $sizedDownEntries,
        'stale_review_candidates_90m' => (int) ($runtimeSummary['technical_stale_review_candidates_90m'] ?? 0),
        'stale_exit_candidates_120m' => (int) ($runtimeSummary['technical_stale_exit_candidates_120m'] ?? 0),
        'stale_hard_timeout_candidates_240m' => (int) ($runtimeSummary['technical_stale_hard_timeout_candidates_240m'] ?? 0),
        'stale_exit_executed' => (int) ($runtimeSummary['technical_stale_exit_executed'] ?? 0),
        'stale_hard_timeout_executed' => (int) ($runtimeSummary['technical_stale_hard_timeout_executed'] ?? 0),
        'stale_exit_skipped_alignment_support' => (int) ($runtimeSummary['technical_stale_exit_skipped_alignment_support'] ?? 0),
        'stale_exit_skipped_recent_support' => (int) ($runtimeSummary['technical_stale_exit_skipped_recent_support'] ?? 0),
        'stale_exit_skipped_profit_protection' => (int) ($runtimeSummary['technical_stale_exit_skipped_profit_protection'] ?? 0),
        'capacity_released_usd_60m' => round($capacityReleasedUsd60m, 4),
    ];
}

function dashboard_build_binance_technical_stale_eligibility_summary(array $runtimeSummary = []): array
{
    return [
        'stale_review_runs' => (int) ($runtimeSummary['technical_stale_review_runs'] ?? 0),
        'open_positions_seen_by_stale_review' => (int) ($runtimeSummary['technical_open_positions_seen_by_stale_review'] ?? 0),
        'technical_open_positions_total' => (int) ($runtimeSummary['technical_open_positions_total'] ?? 0),
        'technical_open_positions_strict' => (int) ($runtimeSummary['technical_open_positions_strict'] ?? 0),
        'technical_open_positions_legacy' => (int) ($runtimeSummary['technical_open_positions_legacy'] ?? 0),
        'technical_open_positions_backfilled' => (int) ($runtimeSummary['technical_open_positions_backfilled'] ?? 0),
        'technical_open_positions_ineligible' => (int) ($runtimeSummary['technical_open_positions_ineligible'] ?? 0),
        'technical_open_positions_rescue' => (int) ($runtimeSummary['technical_open_positions_rescue'] ?? 0),
        'stale_review_skipped' => trim((string) ($runtimeSummary['technical_stale_review_skipped_reason'] ?? 'none')),
    ];
}

function dashboard_build_binance_technical_legacy_position_shape_summary(array $runtimeSummary = []): array
{
    $rawSummary = $runtimeSummary['technical_legacy_position_shape_summary'] ?? [];
    if (!is_array($rawSummary)) {
        $rawSummary = [];
    }

    return [
        'open_binance_paper_positions' => (int) ($rawSummary['open_binance_paper_positions'] ?? ($runtimeSummary['technical_open_positions_total'] ?? 0)),
        'strict_technical_positions' => (int) ($rawSummary['strict_technical_positions'] ?? ($runtimeSummary['technical_open_positions_strict'] ?? 0)),
        'legacy_technical_positions' => (int) ($rawSummary['legacy_technical_positions'] ?? ($runtimeSummary['technical_open_positions_legacy'] ?? 0)),
        'ineligible_positions' => (int) ($rawSummary['ineligible_positions'] ?? ($runtimeSummary['technical_open_positions_ineligible'] ?? 0)),
        'price_structure_source_positions' => (int) ($rawSummary['price_structure_source_positions'] ?? ($runtimeSummary['technical_open_positions_price_structure'] ?? 0)),
        'technical_momentum_source_positions' => (int) ($rawSummary['technical_momentum_source_positions'] ?? ($runtimeSummary['technical_open_positions_momentum_source'] ?? 0)),
        'protection_linked_positions' => (int) ($rawSummary['protection_linked_positions'] ?? ($runtimeSummary['technical_open_positions_protection_linked'] ?? 0)),
        'rescue_age_positions' => (int) ($rawSummary['rescue_age_positions'] ?? ($runtimeSummary['technical_open_positions_rescue'] ?? 0)),
        'backfilled_positions' => (int) ($rawSummary['backfilled_positions'] ?? ($runtimeSummary['technical_open_positions_backfilled'] ?? 0)),
    ];
}

function dashboard_build_binance_technical_legacy_open_positions(array $runtimeSummary = []): array
{
    $rawPositions = $runtimeSummary['technical_legacy_open_positions'] ?? [];
    if (!is_array($rawPositions)) {
        return [];
    }

    $items = [];
    foreach (array_slice($rawPositions, 0, 10) as $row) {
        if (!is_array($row)) {
            continue;
        }
        $markerLabels = $row['marker_labels'] ?? [];
        if (is_array($markerLabels)) {
            $markerLabels = implode(', ', array_map(static fn($value): string => trim((string) $value), $markerLabels));
        }
        $items[] = [
            'position_id' => (int) ($row['position_id'] ?? 0),
            'classification' => trim((string) ($row['classification'] ?? 'ineligible')),
            'venue' => trim((string) ($row['venue'] ?? '')),
            'symbol_or_market_id' => trim((string) ($row['symbol_or_market_id'] ?? '')),
            'side' => trim((string) ($row['side'] ?? '')),
            'source_signal' => trim((string) ($row['source_signal'] ?? '')),
            'signal_family' => trim((string) ($row['signal_family'] ?? '')),
            'strategy_profile' => trim((string) ($row['strategy_profile'] ?? '')),
            'sample_kind' => trim((string) ($row['sample_kind'] ?? '')),
            'opened_at' => trim((string) ($row['opened_at'] ?? '')),
            'position_age_minutes' => round((float) ($row['position_age_minutes'] ?? 0.0), 2),
            'has_stop_loss_order' => !empty($row['has_stop_loss_order']),
            'has_take_profit_order' => !empty($row['has_take_profit_order']),
            'marker_labels' => trim((string) $markerLabels),
        ];
    }

    return $items;
}

function dashboard_build_binance_technical_score_blocker_breakdown_from_rows(array $rows): array
{
    $counts = [];
    foreach ($rows as $row) {
        if (!in_array('score_below_threshold', dashboard_reason_parts($row['reason'] ?? null), true)) {
            continue;
        }
        $inputs = dashboard_decode_inputs_json($row['inputs_json'] ?? null);
        $labels = $inputs['score_blocker_labels'] ?? [];
        if (!is_array($labels)) {
            $labels = [];
        }
        if ($labels === []) {
            $componentMap = [
                'microstructure_drag' => dashboard_float_input($inputs, 'microstructure_component'),
                'momentum_drag' => dashboard_float_input($inputs, 'momentum_component'),
                'macd_drag' => dashboard_float_input($inputs, 'macd_component'),
                'volume_drag' => dashboard_float_input($inputs, 'volume_component'),
                'rsi_drag' => dashboard_float_input($inputs, 'rsi_component'),
            ];
            foreach ($componentMap as $label => $value) {
                if ($value !== null && $value < 0.35) {
                    $labels[] = $label;
                }
            }
            if (count($labels) >= 2) {
                $labels[] = 'multi_factor_drag';
            }
        }
        foreach ($labels as $label) {
            $label = trim((string) $label);
            if ($label === '') {
                continue;
            }
            $counts[$label] = ($counts[$label] ?? 0) + 1;
        }
    }

    arsort($counts);
    $rows = [];
    foreach (array_slice($counts, 0, 10, true) as $reason => $count) {
        $rows[] = ['reason' => $reason, 'count' => $count];
    }
    return $rows;
}

function dashboard_build_binance_technical_score_blocker_breakdown(PDO $pdo): array
{
    return dashboard_build_binance_technical_score_blocker_breakdown_from_rows(dashboard_fetch_recent_binance_technical_rows($pdo));
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
        WHERE action IN ('reject', 'decision')
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
          AND action IN ('reject', 'decision')
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
        WHERE action IN ('reject', 'decision')
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
    $summary = [
        'total_whale_events' => 0,
        'resolved_whale_events' => 0,
        'whale_copy_candidates' => 0,
        'gated_rejects' => 0,
        'gated_decisions' => 0,
        'gated_executes' => 0,
    ];

    foreach (dashboard_fetch_recent_whale_copy_audit_rows($pdo) as $row) {
        $action = strtolower((string) ($row['action'] ?? ''));
        $reason = (string) ($row['reason'] ?? '');
        $inputs = dashboard_decode_inputs_json($row['inputs_json'] ?? null);
        $isGatedCopy = dashboard_inputs_is_gated_whale_copy($inputs);

        if (in_array($action, ['reject', 'decision'], true)) {
            $summary['total_whale_events']++;
            if (!dashboard_reason_is_excluded_whale_copy($reason)) {
                $summary['resolved_whale_events']++;
            }
            if ($isGatedCopy) {
                $summary['whale_copy_candidates']++;
                if ($action === 'reject') {
                    $summary['gated_rejects']++;
                } elseif ($action === 'decision') {
                    $summary['gated_decisions']++;
                }
            }
            continue;
        }

        if ($action === 'execute' && $isGatedCopy) {
            $summary['gated_executes']++;
        }
    }

    return $summary;
}

function dashboard_build_gated_reject_breakdown(PDO $pdo): array
{
    $counts = [];
    foreach (dashboard_fetch_recent_whale_copy_audit_rows($pdo) as $row) {
        if (strtolower((string) ($row['action'] ?? '')) !== 'reject') {
            continue;
        }
        $reasonText = trim((string) ($row['reason'] ?? ''));
        if ($reasonText === '' || dashboard_reason_is_excluded_whale_copy($reasonText)) {
            continue;
        }
        $inputs = dashboard_decode_inputs_json($row['inputs_json'] ?? null);
        if (!dashboard_inputs_is_gated_whale_copy($inputs)) {
            continue;
        }
        foreach (dashboard_reason_parts($reasonText) as $reason) {
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

function dashboard_build_relaxed_gate_reject_breakdown(PDO $pdo): array
{
    $counts = [];
    foreach (dashboard_fetch_recent_whale_copy_audit_rows($pdo) as $row) {
        if (strtolower((string) ($row['action'] ?? '')) !== 'reject') {
            continue;
        }
        $reasonText = trim((string) ($row['reason'] ?? ''));
        if ($reasonText === '' || dashboard_reason_is_excluded_whale_copy($reasonText)) {
            continue;
        }
        $inputs = dashboard_decode_inputs_json($row['inputs_json'] ?? null);
        if (!dashboard_inputs_is_relaxed_gate($inputs)) {
            continue;
        }
        foreach (dashboard_reason_parts($reasonText) as $reason) {
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

function dashboard_build_whale_copy_recovery_summary(PDO $pdo): array
{
    $summary = [
        'relaxed_gate_attempts' => 0,
        'relaxed_gate_rejects' => 0,
        'relaxed_gate_decisions' => 0,
        'relaxed_gate_executes' => 0,
        'token_recovery_attempts' => 0,
        'token_recovery_hits' => 0,
        'token_recovery_failed' => 0,
        'missing_token_rejects' => 0,
    ];
    if (!dashboard_table_has_column($pdo, 'decision_audit', 'inputs_json')) {
        return $summary;
    }

    $rows = dashboard_fetch_all(
        $pdo,
        "
        SELECT action, reason, inputs_json
        FROM " . dashboard_decision_audit_window_sql('decision_audit') . "
        WHERE signal_family IN ('activity_orderflow', 'whale')
          AND inputs_json IS NOT NULL
        ORDER BY id DESC
        LIMIT 500
        "
    );

    foreach ($rows as $row) {
        $inputs = dashboard_decode_inputs_json($row['inputs_json'] ?? null);
        $reason = (string) ($row['reason'] ?? '');
        $copyPolicy = strtolower((string) ($inputs['copy_policy'] ?? ''));
        $isGatedCopy = $copyPolicy === 'gated_whale_copy' || !empty($inputs['whale_copy_relaxed_gate']);
        if (!$isGatedCopy && !str_contains($reason, 'token_recovery') && !str_contains($reason, 'missing_polymarket_token_price')) {
            continue;
        }

        $action = strtolower((string) ($row['action'] ?? ''));
        if (!empty($inputs['whale_copy_relaxed_gate'])) {
            if (in_array($action, ['reject', 'decision'], true)) {
                $summary['relaxed_gate_attempts']++;
            }
            if ($action === 'reject') {
                $summary['relaxed_gate_rejects']++;
            } elseif ($action === 'decision') {
                $summary['relaxed_gate_decisions']++;
            } elseif ($action === 'execute') {
                $summary['relaxed_gate_executes']++;
            }
        }
        if (in_array($action, ['reject', 'decision'], true) && !empty($inputs['token_recovery_attempted'])) {
            $summary['token_recovery_attempts']++;
        }
        if (in_array($action, ['reject', 'decision'], true) && !empty($inputs['token_recovery_hit'])) {
            $summary['token_recovery_hits']++;
        }
        if (in_array($action, ['reject', 'decision'], true) && (!empty($inputs['token_recovery_failed']) || str_contains($reason, 'token_recovery_failed'))) {
            $summary['token_recovery_failed']++;
        }
        if (in_array($action, ['reject', 'decision'], true) && str_contains($reason, 'missing_polymarket_token_price')) {
            $summary['missing_token_rejects']++;
        }
    }

    return $summary;
}

function dashboard_build_whale_side_summary(PDO $pdo): array
{
    $summary = [
        'buy_side_events' => 0,
        'sell_side_events' => 0,
        'unsupported_side_filtered' => 0,
    ];

    foreach (dashboard_fetch_recent_whale_copy_audit_rows($pdo) as $row) {
        $action = strtolower((string) ($row['action'] ?? ''));
        if (!in_array($action, ['reject', 'decision'], true)) {
            continue;
        }

        $reason = (string) ($row['reason'] ?? '');
        $inputs = dashboard_decode_inputs_json($row['inputs_json'] ?? null);
        $originalSide = dashboard_inputs_original_side($inputs);
        if ($originalSide === 'BUY') {
            $summary['buy_side_events']++;
        } elseif ($originalSide === 'SELL') {
            $summary['sell_side_events']++;
        }

        if (str_contains($reason, 'unsupported_side_filtered') || str_contains($reason, 'sell_side_not_supported')) {
            $summary['unsupported_side_filtered']++;
        }
    }

    return $summary;
}

function dashboard_build_whale_copy_gate_funnel(PDO $pdo): array
{
    $summary = [
        'resolved_whale_events' => 0,
        'gate_ready_candidates' => 0,
        'relaxed_gate_attempts' => 0,
        'gated_rejects' => 0,
        'gated_decisions' => 0,
        'gated_executes' => 0,
    ];

    foreach (dashboard_fetch_recent_whale_copy_audit_rows($pdo) as $row) {
        $action = strtolower((string) ($row['action'] ?? ''));
        $reason = (string) ($row['reason'] ?? '');
        $inputs = dashboard_decode_inputs_json($row['inputs_json'] ?? null);
        $isGatedCopy = dashboard_inputs_is_gated_whale_copy($inputs);

        if (in_array($action, ['reject', 'decision'], true)) {
            if (!dashboard_reason_is_excluded_whale_copy($reason)) {
                $summary['resolved_whale_events']++;
            }
            if ($isGatedCopy && !empty($inputs['whale_copy_gate_ready'])) {
                $summary['gate_ready_candidates']++;
            }
            if (!empty($inputs['whale_copy_relaxed_gate'])) {
                $summary['relaxed_gate_attempts']++;
            }
            if ($isGatedCopy && $action === 'reject') {
                $summary['gated_rejects']++;
            } elseif ($isGatedCopy && $action === 'decision') {
                $summary['gated_decisions']++;
            }
            continue;
        }

        if ($action === 'execute' && $isGatedCopy) {
            $summary['gated_executes']++;
        }
    }

    return $summary;
}

function dashboard_build_graph_discovery_summary(array $runtimeSummary, array $whaleUniverseSummary): array
{
    return [
        'graph_discovered_wallets' => (int) ($whaleUniverseSummary['graph_discovered_wallets'] ?? $runtimeSummary['graph_discovered_wallets'] ?? 0),
        'graph_clusters_promoted' => (int) ($runtimeSummary['graph_clusters_promoted'] ?? 0),
        'graph_skipped_missing_market_ref' => (int) ($runtimeSummary['graph_skipped_missing_market_ref'] ?? 0),
        'graph_skipped_single_wallet' => (int) ($runtimeSummary['graph_skipped_single_wallet'] ?? 0),
        'graph_skipped_low_notional' => (int) ($runtimeSummary['graph_skipped_low_notional'] ?? 0),
    ];
}

function dashboard_build_whale_candidate_aggregation_summary(array $runtimeSummary): array
{
    return [
        'accumulator_buckets' => (int) ($runtimeSummary['whale_copy_accumulator_buckets'] ?? 0),
        'gate_ready_candidates' => (int) ($runtimeSummary['whale_copy_gate_ready_candidates'] ?? 0),
        'retry_candidates' => (int) ($runtimeSummary['whale_copy_retry_candidates'] ?? 0),
        'accumulated_buy_events' => (int) ($runtimeSummary['whale_copy_accumulated_buy_events'] ?? 0),
        'accumulated_total_notional' => (float) ($runtimeSummary['whale_copy_accumulated_total_notional'] ?? 0.0),
    ];
}

function dashboard_build_recent_gate_ready_candidates(array $runtimeSummary): array
{
    $rows = $runtimeSummary['recent_gate_ready_candidates'] ?? [];
    if (!is_array($rows)) {
        return [];
    }

    $items = [];
    foreach ($rows as $row) {
        if (!is_array($row)) {
            continue;
        }
        $items[] = [
            'market_id' => (string) ($row['market_id'] ?? ''),
            'total_amount' => (float) ($row['total_amount'] ?? 0.0),
            'unique_wallets' => (int) ($row['unique_wallets'] ?? 0),
            'event_count' => (int) ($row['event_count'] ?? 0),
            'max_trust' => (float) ($row['max_trust'] ?? 0.0),
            'gate_ready_reason' => (string) ($row['gate_ready_reason'] ?? ''),
            'retry_cooldown_applied' => !empty($row['retry_cooldown_applied']),
            'last_reject_reason' => (string) ($row['last_reject_reason'] ?? ''),
        ];
    }
    return $items;
}

function dashboard_build_polymarket_research_summary(PDO $pdo): array
{
    $emptyDiscoverySourceSummary = [
        'rows' => [],
        'selected_wallets' => 0,
        'min_viable_pool' => 30,
        'static_seed_used' => 0,
    ];
    $emptyShadowPromotionSummary = [
        'eligible_wallets' => 0,
        'promoted_wallets' => 0,
        'blocked_wallets' => 0,
        'blocker_counts' => [],
    ];
    $emptyWalletProvenanceSummary = [
        'multi_source_wallets' => 0,
        'single_source_wallets' => 0,
        'seed_only_wallets' => 0,
    ];
    $emptyLinkedWalletEvidenceSummary = [
        'linked_wallets_total' => 0,
        'linked_with_trade_history' => 0,
        'linked_stats_only' => 0,
        'linked_without_trade_history' => 0,
        'linked_promoted_to_shadow' => 0,
    ];
    $emptyShadowEvidenceBackfillSummary = [
        'replay_rows_created' => 0,
        'wallets_with_replay_history' => 0,
        'wallets_without_replay_history' => 0,
        'net_replay_shadow_pnl' => 0.0,
        'net_replay_shadow_edge' => 0.0,
    ];
    $emptyLongHorizonWatchlistSummary = [
        'priority_watch' => 0,
        'linked' => 0,
        'observing' => 0,
        'shadow_tracking' => 0,
        'pilot_copy_ready' => 0,
        'copy_ready' => 0,
    ];
    $emptySpecialistWalletScoreSummary = [
        'sample_count' => 0,
        'avg_long_horizon_score' => 0.0,
        'avg_pnl_smoothness_score' => 0.0,
        'avg_one_off_gain_penalty' => 0.0,
        'crypto_specialists' => 0,
    ];
    $emptyObservationProgressSummary = [
        'watch_wallets' => 0,
        'observing_wallets' => 0,
        'total_observed_actions' => 0,
        'wallets_with_observed_actions' => 0,
        'avg_observation_days' => 0.0,
    ];
    $emptyPilotCopyAdmissionSummary = [
        'pilot_copy_wallets' => 0,
        'operator_approved_wallets' => 0,
        'blocked_wallets' => 0,
        'blocker_counts' => [],
    ];

    if (dashboard_table_exists($pdo, 'polymarket_research_wallets')) {
        $persistedCount = (int) ((dashboard_fetch_one($pdo, 'SELECT COUNT(*) AS count FROM polymarket_research_wallets')['count'] ?? 0));
        if ($persistedCount > 0) {
            $historicalEvidenceExpr = dashboard_table_has_column($pdo, 'polymarket_research_wallets', 'historical_trade_evidence_status')
                ? "historical_trade_evidence_status"
                : "'no_historical_evidence' AS historical_trade_evidence_status";
            $historicalRowsExpr = dashboard_table_has_column($pdo, 'polymarket_research_wallets', 'historical_trade_rows')
                ? "historical_trade_rows"
                : "0 AS historical_trade_rows";
            $evidenceLastTradeExpr = dashboard_table_has_column($pdo, 'polymarket_research_wallets', 'evidence_last_trade_at')
                ? "evidence_last_trade_at"
                : "'' AS evidence_last_trade_at";
            $shadowSeededExpr = dashboard_table_has_column($pdo, 'polymarket_research_wallets', 'shadow_seeded')
                ? "shadow_seeded"
                : "0 AS shadow_seeded";
            $shadowBlockerExpr = dashboard_table_has_column($pdo, 'polymarket_research_wallets', 'shadow_blocker_reason')
                ? "shadow_blocker_reason"
                : "shadow_gate_reason AS shadow_blocker_reason";
            $longHorizonStatusExpr = dashboard_table_has_column($pdo, 'polymarket_research_wallets', 'long_horizon_status')
                ? "long_horizon_status"
                : "'untracked' AS long_horizon_status";
            $longHorizonScoreExpr = dashboard_table_has_column($pdo, 'polymarket_research_wallets', 'long_horizon_score')
                ? "long_horizon_score"
                : "0 AS long_horizon_score";
            $observationDaysExpr = dashboard_table_has_column($pdo, 'polymarket_research_wallets', 'observation_days')
                ? "observation_days"
                : "0 AS observation_days";
            $observedActionCountExpr = dashboard_table_has_column($pdo, 'polymarket_research_wallets', 'observed_action_count')
                ? "observed_action_count"
                : "0 AS observed_action_count";
            $pnlSmoothnessExpr = dashboard_table_has_column($pdo, 'polymarket_research_wallets', 'pnl_smoothness_score')
                ? "pnl_smoothness_score"
                : "0 AS pnl_smoothness_score";
            $oneOffPenaltyExpr = dashboard_table_has_column($pdo, 'polymarket_research_wallets', 'one_off_gain_penalty')
                ? "one_off_gain_penalty"
                : "0 AS one_off_gain_penalty";
            $pilotGateStatusExpr = dashboard_table_has_column($pdo, 'polymarket_research_wallets', 'pilot_copy_gate_status')
                ? "pilot_copy_gate_status"
                : "'blocked' AS pilot_copy_gate_status";
            $pilotGateReasonExpr = dashboard_table_has_column($pdo, 'polymarket_research_wallets', 'pilot_copy_gate_reason')
                ? "pilot_copy_gate_reason"
                : "'operator_approval_required' AS pilot_copy_gate_reason";
            $operatorApprovedPilotExpr = dashboard_table_has_column($pdo, 'polymarket_research_wallets', 'operator_approved_pilot')
                ? "operator_approved_pilot"
                : "0 AS operator_approved_pilot";
            $walletRows = dashboard_fetch_all(
                $pdo,
                "
                SELECT
                    address,
                    source_type,
                    primary_source,
                    source_labels,
                    source_count,
                    discovery_bucket,
                    cohort,
                    discovery_rank,
                    shadow_rank,
                    copy_ready_rank,
                    discovery_score,
                    trust_score,
                    consistency_score,
                    profit_consistency_score,
                    recency_score,
                    frequency_score,
                    drawdown_estimate_pct,
                    active_days,
                    closed_trade_count,
                    realized_pnl,
                    crypto_participation_ratio,
                    specialization,
                    event_count_24h,
                    last_event_amount,
                    last_seen_at,
                    closed_shadow_trades,
                    shadow_pnl,
                    shadow_edge,
                    worst_drawdown_pct,
                    shadow_gate_status,
                    shadow_gate_reason,
                    copy_ready_gate_status,
                    copy_ready_gate_reason,
                    shadow_eligible,
                    copy_ready_eligible,
                    watchlist_priority_rank,
                    watchlist_status,
                    watchlist_mode,
                    identity_resolution_status,
                    priority_pinned,
                    {$historicalEvidenceExpr},
                    {$historicalRowsExpr},
                    {$evidenceLastTradeExpr},
                    {$shadowSeededExpr},
                    {$shadowBlockerExpr},
                    {$longHorizonStatusExpr},
                    {$longHorizonScoreExpr},
                    {$observationDaysExpr},
                    {$observedActionCountExpr},
                    {$pnlSmoothnessExpr},
                    {$oneOffPenaltyExpr},
                    {$pilotGateStatusExpr},
                    {$pilotGateReasonExpr},
                    {$operatorApprovedPilotExpr},
                    refreshed_at
                FROM polymarket_research_wallets
                ORDER BY discovery_rank ASC, consistency_score DESC, trust_score DESC
                LIMIT 50
                "
            );

            $candidates = array_map(
                static fn (array $row): array => [
                    'address' => (string) ($row['address'] ?? ''),
                    'source_type' => (string) ($row['source_type'] ?? 'unknown'),
                    'primary_source' => (string) ($row['primary_source'] ?? $row['source_type'] ?? 'unknown'),
                    'source_labels' => array_values(
                        array_filter(
                            (array) json_decode((string) ($row['source_labels'] ?? '[]'), true),
                            static fn ($value): bool => is_string($value) && trim($value) !== ''
                        )
                    ),
                    'source_count' => (int) ($row['source_count'] ?? 0),
                    'discovery_bucket' => (string) ($row['discovery_bucket'] ?? 'unassigned'),
                    'cohort' => (string) ($row['cohort'] ?? 'discovery'),
                    'discovery_rank' => (int) ($row['discovery_rank'] ?? 0),
                    'shadow_rank' => (int) ($row['shadow_rank'] ?? 0),
                    'copy_ready_rank' => (int) ($row['copy_ready_rank'] ?? 0),
                    'discovery_score' => round((float) ($row['discovery_score'] ?? 0.0), 4),
                    'trust_score' => round((float) ($row['trust_score'] ?? 0.5), 4),
                    'consistency_score' => round((float) ($row['consistency_score'] ?? 0.0), 4),
                    'profit_consistency_score' => round((float) ($row['profit_consistency_score'] ?? 0.0), 4),
                    'recency_score' => round((float) ($row['recency_score'] ?? 0.0), 4),
                    'frequency_score' => round((float) ($row['frequency_score'] ?? 0.0), 4),
                    'drawdown_estimate_pct' => round((float) ($row['drawdown_estimate_pct'] ?? 0.0), 4),
                    'active_days' => (int) ($row['active_days'] ?? 0),
                    'closed_trade_count' => (int) ($row['closed_trade_count'] ?? 0),
                    'realized_pnl' => round((float) ($row['realized_pnl'] ?? 0.0), 4),
                    'crypto_participation_ratio' => round((float) ($row['crypto_participation_ratio'] ?? 0.0), 4),
                    'specialization' => strtoupper((string) ($row['specialization'] ?? 'UNKNOWN')),
                    'event_count_24h' => (int) ($row['event_count_24h'] ?? 0),
                    'last_event_amount' => round((float) ($row['last_event_amount'] ?? 0.0), 4),
                    'last_seen_at' => (string) ($row['last_seen_at'] ?? ''),
                    'closed_shadow_trades' => (int) ($row['closed_shadow_trades'] ?? 0),
                    'shadow_pnl' => round((float) ($row['shadow_pnl'] ?? 0.0), 4),
                    'shadow_edge' => round((float) ($row['shadow_edge'] ?? 0.0), 4),
                    'worst_drawdown_pct' => round((float) ($row['worst_drawdown_pct'] ?? 0.0), 4),
                    'shadow_gate_status' => (string) ($row['shadow_gate_status'] ?? 'blocked'),
                    'shadow_gate_reason' => (string) ($row['shadow_gate_reason'] ?? 'low_consistency'),
                    'copy_ready_gate_status' => (string) ($row['copy_ready_gate_status'] ?? 'blocked'),
                    'copy_ready_gate_reason' => (string) ($row['copy_ready_gate_reason'] ?? 'needs_shadow_history'),
                    'shadow_eligible' => !empty($row['shadow_eligible']),
                    'copy_ready_eligible' => !empty($row['copy_ready_eligible']),
                    'watchlist_priority_rank' => (int) ($row['watchlist_priority_rank'] ?? 0),
                    'watchlist_status' => (string) ($row['watchlist_status'] ?? ''),
                    'watchlist_mode' => (string) ($row['watchlist_mode'] ?? ''),
                    'identity_resolution_status' => (string) ($row['identity_resolution_status'] ?? 'untracked'),
                    'priority_pinned' => !empty($row['priority_pinned']),
                    'historical_trade_evidence_status' => (string) ($row['historical_trade_evidence_status'] ?? 'no_historical_evidence'),
                    'historical_trade_rows' => (int) ($row['historical_trade_rows'] ?? 0),
                    'evidence_last_trade_at' => (string) ($row['evidence_last_trade_at'] ?? ''),
                    'shadow_seeded' => !empty($row['shadow_seeded']),
                    'shadow_blocker_reason' => (string) ($row['shadow_blocker_reason'] ?? $row['shadow_gate_reason'] ?? ''),
                    'long_horizon_status' => (string) ($row['long_horizon_status'] ?? 'untracked'),
                    'long_horizon_score' => round((float) ($row['long_horizon_score'] ?? 0.0), 4),
                    'observation_days' => (int) ($row['observation_days'] ?? 0),
                    'observed_action_count' => (int) ($row['observed_action_count'] ?? 0),
                    'pnl_smoothness_score' => round((float) ($row['pnl_smoothness_score'] ?? 0.0), 4),
                    'one_off_gain_penalty' => round((float) ($row['one_off_gain_penalty'] ?? 0.0), 4),
                    'pilot_copy_gate_status' => (string) ($row['pilot_copy_gate_status'] ?? 'blocked'),
                    'pilot_copy_gate_reason' => (string) ($row['pilot_copy_gate_reason'] ?? 'operator_approval_required'),
                    'operator_approved_pilot' => !empty($row['operator_approved_pilot']),
                ],
                $walletRows
            );

            $shadowWalletTable = array_values(
                array_filter(
                    $candidates,
                    static fn (array $row): bool => !empty($row['shadow_eligible']) || in_array($row['cohort'], ['shadow', 'copy_ready'], true)
                )
            );
            usort(
                $shadowWalletTable,
                static fn (array $left, array $right): int => [$left['shadow_rank'] ?: 9999, $left['discovery_rank']]
                    <=> [$right['shadow_rank'] ?: 9999, $right['discovery_rank']]
            );
            $shadowWalletTable = array_slice($shadowWalletTable, 0, 20);

            $copyReadyWallets = array_values(
                array_filter(
                    $candidates,
                    static fn (array $row): bool => !empty($row['copy_ready_eligible']) || ($row['cohort'] ?? '') === 'copy_ready'
                )
            );
            usort(
                $copyReadyWallets,
                static fn (array $left, array $right): int => [$left['copy_ready_rank'] ?: 9999, $left['discovery_rank']]
                    <=> [$right['copy_ready_rank'] ?: 9999, $right['discovery_rank']]
            );
            $copyReadyWallets = array_slice($copyReadyWallets, 0, 5);

            $bucketTargets = [
                'leaderboard' => 15,
                'activity_discovery' => 15,
                'graph_discovery' => 10,
                'manual_persisted' => 10,
            ];
            $bucketCounts = array_fill_keys(array_keys($bucketTargets), 0);
            $staticSeedUsed = 0;
            $shadowEligibleCount = 0;
            $shadowBlockedCount = 0;
            $shadowBlockers = [];
            $multiSourceWallets = 0;
            $singleSourceWallets = 0;
            $seedOnlyWallets = 0;

            foreach ($candidates as $candidate) {
                $bucket = (string) ($candidate['discovery_bucket'] ?? 'unassigned');
                if (array_key_exists($bucket, $bucketCounts)) {
                    $bucketCounts[$bucket] += 1;
                } elseif ($bucket === 'static_seed') {
                    $staticSeedUsed += 1;
                }

                $sourceLabels = array_values(
                    array_map(
                        static fn ($label): string => strtolower(trim((string) $label)),
                        (array) ($candidate['source_labels'] ?? [])
                    )
                );
                $sourceLabels = array_values(array_filter($sourceLabels, static fn (string $label): bool => $label !== ''));
                $sourceLabels = array_values(array_unique($sourceLabels));
                if ((int) ($candidate['source_count'] ?? 0) > 1) {
                    $multiSourceWallets += 1;
                } elseif ((int) ($candidate['source_count'] ?? 0) === 1) {
                    $singleSourceWallets += 1;
                }
                if ($sourceLabels === ['static_seed']) {
                    $seedOnlyWallets += 1;
                }

                if ((string) ($candidate['shadow_gate_reason'] ?? '') === 'eligible') {
                    $shadowEligibleCount += 1;
                }
                if ((string) ($candidate['shadow_gate_status'] ?? '') === 'blocked') {
                    $shadowBlockedCount += 1;
                    $reason = (string) ($candidate['shadow_gate_reason'] ?? 'unknown');
                    $shadowBlockers[$reason] = ($shadowBlockers[$reason] ?? 0) + 1;
                }
            }

            $discoverySourceSummary = [
                'rows' => array_values(
                    array_map(
                        static fn (string $bucket, int $target): array => [
                            'bucket' => $bucket,
                            'target' => $target,
                            'actual' => $bucketCounts[$bucket] ?? 0,
                        ],
                        array_keys($bucketTargets),
                        array_values($bucketTargets)
                    )
                ),
                'selected_wallets' => count($candidates),
                'min_viable_pool' => 30,
                'static_seed_used' => $staticSeedUsed,
            ];

            $shadowPromotionSummary = [
                'eligible_wallets' => $shadowEligibleCount,
                'promoted_wallets' => count($shadowWalletTable),
                'blocked_wallets' => $shadowBlockedCount,
                'blocker_counts' => array_map(
                    static fn (string $reason, int $count): array => ['reason' => $reason, 'count' => $count],
                    array_keys($shadowBlockers),
                    array_values($shadowBlockers)
                ),
            ];

            $walletProvenanceSummary = [
                'multi_source_wallets' => $multiSourceWallets,
                'single_source_wallets' => $singleSourceWallets,
                'seed_only_wallets' => $seedOnlyWallets,
            ];

            $recentShadowActions = [];
            $shadowReplayRows = [];
            $shadowReplaySummary = [
                'replayed_actions_created' => 0,
                'wallets_with_replay_history' => 0,
                'net_shadow_pnl' => 0.0,
                'net_shadow_edge' => 0.0,
                'eligible_without_trade_history' => 0,
            ];
            if (dashboard_table_exists($pdo, 'polymarket_shadow_actions')) {
                $recentShadowRows = dashboard_fetch_all(
                    $pdo,
                    "
                    SELECT
                        wallet_address,
                        market_id,
                        category,
                        source_type,
                        action_type,
                        shadow_pnl,
                        shadow_edge,
                        drawdown_pct,
                        opened_at,
                        closed_at,
                        status
                    FROM polymarket_shadow_actions
                    ORDER BY COALESCE(closed_at, opened_at) DESC, id DESC
                    LIMIT 12
                    "
                );
                foreach ($recentShadowRows as $row) {
                    $recentShadowActions[] = [
                        'wallet_address' => (string) ($row['wallet_address'] ?? ''),
                        'market_id' => (string) ($row['market_id'] ?? ''),
                        'category' => (string) ($row['category'] ?? 'UNKNOWN'),
                        'source_type' => (string) ($row['source_type'] ?? 'unknown'),
                        'action_type' => (string) ($row['action_type'] ?? 'shadow_trade'),
                        'shadow_pnl' => round((float) ($row['shadow_pnl'] ?? 0.0), 4),
                        'shadow_edge' => round((float) ($row['shadow_edge'] ?? 0.0), 4),
                        'drawdown_pct' => round((float) ($row['drawdown_pct'] ?? 0.0), 4),
                        'opened_at' => (string) ($row['opened_at'] ?? ''),
                        'closed_at' => (string) ($row['closed_at'] ?? ''),
                        'status' => (string) ($row['status'] ?? 'OPEN'),
                    ];
                }

                $shadowReplayRows = dashboard_fetch_all(
                    $pdo,
                    "
                    SELECT
                        wallet_address,
                        shadow_pnl,
                        shadow_edge
                    FROM polymarket_shadow_actions
                    WHERE action_type = 'shadow_replay'
                    "
                );
                $walletReplayHistory = [];
                $netReplayPnl = 0.0;
                $netReplayEdge = 0.0;
                foreach ($shadowReplayRows as $row) {
                    $walletAddress = strtolower(trim((string) ($row['wallet_address'] ?? '')));
                    if ($walletAddress !== '') {
                        $walletReplayHistory[$walletAddress] = true;
                    }
                    $netReplayPnl += (float) ($row['shadow_pnl'] ?? 0.0);
                    $netReplayEdge += (float) ($row['shadow_edge'] ?? 0.0);
                }
                $eligibleShadowAddresses = [];
                foreach ($shadowWalletTable as $row) {
                    $address = strtolower(trim((string) ($row['address'] ?? '')));
                    if ($address !== '') {
                        $eligibleShadowAddresses[$address] = true;
                    }
                }
                $eligibleWithoutReplay = 0;
                foreach (array_keys($eligibleShadowAddresses) as $address) {
                    if (!isset($walletReplayHistory[$address])) {
                        $eligibleWithoutReplay += 1;
                    }
                }
                $shadowReplaySummary = [
                    'replayed_actions_created' => count($shadowReplayRows),
                    'wallets_with_replay_history' => count($walletReplayHistory),
                    'net_shadow_pnl' => round($netReplayPnl, 4),
                    'net_shadow_edge' => round($netReplayEdge, 4),
                    'eligible_without_trade_history' => $eligibleWithoutReplay,
                ];
            }

            $priorityWatchlistRows = [];
            $priorityWatchlistSummary = [
                'total_watchlist_rows' => 0,
                'linked_rows' => 0,
                'pending_resolution_rows' => 0,
                'promoted_priority_wallets' => 0,
            ];
            $identityResolutionSummary = [
                'pending_handle_only_entries' => 0,
                'linked_entries' => 0,
                'unresolved_but_ranked_entries' => 0,
            ];
            $linkedWalletEvidenceSummary = $emptyLinkedWalletEvidenceSummary;
            $shadowEvidenceBackfillSummary = $emptyShadowEvidenceBackfillSummary;
            $longHorizonWatchlistSummary = $emptyLongHorizonWatchlistSummary;
            $specialistWalletScoreSummary = $emptySpecialistWalletScoreSummary;
            $observationProgressSummary = $emptyObservationProgressSummary;
            $pilotCopyAdmissionSummary = $emptyPilotCopyAdmissionSummary;
            if (dashboard_table_exists($pdo, 'polymarket_research_watchlist')) {
                $watchlistRows = dashboard_fetch_all(
                    $pdo,
                    "
                    SELECT
                        display_name,
                        profile_ref,
                        wallet_address,
                        priority_rank,
                        priority_mode,
                        target_specialization,
                        status
                    FROM polymarket_research_watchlist
                    ORDER BY priority_rank ASC, id ASC
                    "
                );
                $linkedRows = 0;
                $pendingRows = 0;
                $unresolvedRankedRows = 0;
                $promotedPriorityWallets = 0;
                $linkedWithTradeHistory = 0;
                $linkedStatsOnly = 0;
                $linkedWithoutTradeHistory = 0;
                $linkedPromotedToShadow = 0;
                $linkedWalletAddresses = [];
                foreach ($watchlistRows as $row) {
                    $walletAddress = strtolower(trim((string) ($row['wallet_address'] ?? '')));
                    $linked = $walletAddress !== '';
                    if ($linked) {
                        $linkedRows += 1;
                        $linkedWalletAddresses[$walletAddress] = true;
                    } else {
                        $pendingRows += 1;
                        if ((int) ($row['priority_rank'] ?? 0) > 0) {
                            $unresolvedRankedRows += 1;
                        }
                    }
                    $linkedCandidate = null;
                    if ($linked) {
                        foreach ($candidates as $candidate) {
                            if (strtolower((string) ($candidate['address'] ?? '')) === $walletAddress) {
                                $linkedCandidate = $candidate;
                                break;
                            }
                        }
                    }
                    $promotedToShadow = $linkedCandidate !== null
                        && (string) ($linkedCandidate['shadow_gate_status'] ?? '') === 'promoted';
                    if ($promotedToShadow) {
                        $promotedPriorityWallets += 1;
                        $linkedPromotedToShadow += 1;
                    }
                    $evidenceStatus = $linkedCandidate !== null
                        ? (string) ($linkedCandidate['historical_trade_evidence_status'] ?? 'no_historical_evidence')
                        : ($linked ? 'no_historical_evidence' : '');
                    if ($linked) {
                        if ($evidenceStatus === 'detailed_trade_history') {
                            $linkedWithTradeHistory += 1;
                        } elseif ($evidenceStatus === 'stats_only') {
                            $linkedStatsOnly += 1;
                        } else {
                            $linkedWithoutTradeHistory += 1;
                        }
                    }
                    $priorityWatchlistRows[] = [
                        'display_name' => (string) ($row['display_name'] ?? ''),
                        'profile_ref' => (string) ($row['profile_ref'] ?? ''),
                        'wallet_address' => $walletAddress,
                        'priority_rank' => (int) ($row['priority_rank'] ?? 0),
                        'priority_mode' => (string) ($row['priority_mode'] ?? 'normal'),
                        'target_specialization' => strtoupper((string) ($row['target_specialization'] ?? 'UNKNOWN')),
                        'status' => (string) ($row['status'] ?? 'pending_resolution'),
                        'identity_resolution_status' => $linked ? 'linked' : 'pending_resolution',
                        'promoted_to_shadow' => $promotedToShadow,
                        'historical_trade_evidence_status' => $evidenceStatus,
                        'historical_trade_rows' => $linkedCandidate !== null ? (int) ($linkedCandidate['historical_trade_rows'] ?? 0) : 0,
                        'evidence_last_trade_at' => $linkedCandidate !== null ? (string) ($linkedCandidate['evidence_last_trade_at'] ?? '') : '',
                        'shadow_seeded' => $linkedCandidate !== null && !empty($linkedCandidate['shadow_seeded']),
                        'shadow_blocker_reason' => $linkedCandidate !== null ? (string) ($linkedCandidate['shadow_blocker_reason'] ?? $linkedCandidate['shadow_gate_reason'] ?? '') : '',
                        'long_horizon_status' => $linkedCandidate !== null ? (string) ($linkedCandidate['long_horizon_status'] ?? ($linked ? 'linked' : 'priority_watch')) : ($linked ? 'linked' : 'priority_watch'),
                        'long_horizon_score' => $linkedCandidate !== null ? round((float) ($linkedCandidate['long_horizon_score'] ?? 0.0), 4) : 0.0,
                        'observation_days' => $linkedCandidate !== null ? (int) ($linkedCandidate['observation_days'] ?? 0) : 0,
                        'observed_action_count' => $linkedCandidate !== null ? (int) ($linkedCandidate['observed_action_count'] ?? 0) : 0,
                        'pilot_copy_gate_status' => $linkedCandidate !== null ? (string) ($linkedCandidate['pilot_copy_gate_status'] ?? 'blocked') : 'blocked',
                        'pilot_copy_gate_reason' => $linkedCandidate !== null ? (string) ($linkedCandidate['pilot_copy_gate_reason'] ?? 'operator_approval_required') : 'operator_approval_required',
                        'operator_approved_pilot' => $linkedCandidate !== null && !empty($linkedCandidate['operator_approved_pilot']),
                    ];
                }
                $priorityWatchlistSummary = [
                    'total_watchlist_rows' => count($watchlistRows),
                    'linked_rows' => $linkedRows,
                    'pending_resolution_rows' => $pendingRows,
                    'promoted_priority_wallets' => $promotedPriorityWallets,
                ];
                $identityResolutionSummary = [
                    'pending_handle_only_entries' => $pendingRows,
                    'linked_entries' => $linkedRows,
                    'unresolved_but_ranked_entries' => $unresolvedRankedRows,
                ];
                $linkedWalletEvidenceSummary = [
                    'linked_wallets_total' => $linkedRows,
                    'linked_with_trade_history' => $linkedWithTradeHistory,
                    'linked_stats_only' => $linkedStatsOnly,
                    'linked_without_trade_history' => $linkedWithoutTradeHistory,
                    'linked_promoted_to_shadow' => $linkedPromotedToShadow,
                ];

                $linkedReplayRows = [];
                $linkedReplayWallets = [];
                foreach ($shadowReplayRows as $row) {
                    $walletAddress = strtolower(trim((string) ($row['wallet_address'] ?? '')));
                    if ($walletAddress !== '' && isset($linkedWalletAddresses[$walletAddress])) {
                        $linkedReplayRows[] = $row;
                        $linkedReplayWallets[$walletAddress] = true;
                    }
                }
                $shadowEvidenceBackfillSummary = [
                    'replay_rows_created' => count($linkedReplayRows),
                    'wallets_with_replay_history' => count($linkedReplayWallets),
                    'wallets_without_replay_history' => max($linkedRows - count($linkedReplayWallets), 0),
                    'net_replay_shadow_pnl' => round(array_sum(array_map(static fn (array $row): float => (float) ($row['shadow_pnl'] ?? 0.0), $linkedReplayRows)), 4),
                    'net_replay_shadow_edge' => round(array_sum(array_map(static fn (array $row): float => (float) ($row['shadow_edge'] ?? 0.0), $linkedReplayRows)), 4),
                ];
            }
            $statusCounts = array_count_values(array_map(static fn (array $row): string => (string) ($row['long_horizon_status'] ?? 'untracked'), $candidates));
            $longHorizonWatchlistSummary = [
                'priority_watch' => (int) ($statusCounts['priority_watch'] ?? 0),
                'linked' => (int) ($statusCounts['linked'] ?? 0),
                'observing' => (int) ($statusCounts['observing'] ?? 0),
                'shadow_tracking' => (int) ($statusCounts['shadow_tracking'] ?? 0),
                'pilot_copy_ready' => (int) ($statusCounts['pilot_copy_ready'] ?? 0),
                'copy_ready' => (int) ($statusCounts['copy_ready'] ?? 0),
            ];
            $sampleCount = count($candidates);
            $watchWallets = array_values(array_filter(
                $candidates,
                static fn (array $row): bool => (int) ($row['watchlist_priority_rank'] ?? 0) > 0 || (string) ($row['identity_resolution_status'] ?? '') === 'linked'
            ));
            $pilotBlockers = [];
            foreach ($watchWallets as $row) {
                $reason = (string) ($row['pilot_copy_gate_reason'] ?? 'operator_approval_required');
                if ($reason !== 'eligible') {
                    $pilotBlockers[$reason] = ($pilotBlockers[$reason] ?? 0) + 1;
                }
            }
            $specialistWalletScoreSummary = [
                'sample_count' => $sampleCount,
                'avg_long_horizon_score' => $sampleCount > 0 ? round(array_sum(array_map(static fn (array $row): float => (float) ($row['long_horizon_score'] ?? 0.0), $candidates)) / $sampleCount, 4) : 0.0,
                'avg_pnl_smoothness_score' => $sampleCount > 0 ? round(array_sum(array_map(static fn (array $row): float => (float) ($row['pnl_smoothness_score'] ?? 0.0), $candidates)) / $sampleCount, 4) : 0.0,
                'avg_one_off_gain_penalty' => $sampleCount > 0 ? round(array_sum(array_map(static fn (array $row): float => (float) ($row['one_off_gain_penalty'] ?? 0.0), $candidates)) / $sampleCount, 4) : 0.0,
                'crypto_specialists' => count(array_filter($candidates, static fn (array $row): bool => (string) ($row['specialization'] ?? '') === 'CRYPTO')),
            ];
            $observationProgressSummary = [
                'watch_wallets' => count($watchWallets),
                'observing_wallets' => count(array_filter($watchWallets, static fn (array $row): bool => in_array((string) ($row['long_horizon_status'] ?? ''), ['observing', 'shadow_tracking', 'pilot_copy_ready', 'copy_ready'], true))),
                'total_observed_actions' => array_sum(array_map(static fn (array $row): int => (int) ($row['observed_action_count'] ?? 0), $watchWallets)),
                'wallets_with_observed_actions' => count(array_filter($watchWallets, static fn (array $row): bool => (int) ($row['observed_action_count'] ?? 0) > 0)),
                'avg_observation_days' => count($watchWallets) > 0 ? round(array_sum(array_map(static fn (array $row): int => (int) ($row['observation_days'] ?? 0), $watchWallets)) / count($watchWallets), 2) : 0.0,
            ];
            $pilotCopyAdmissionSummary = [
                'pilot_copy_wallets' => count(array_filter($candidates, static fn (array $row): bool => (string) ($row['pilot_copy_gate_status'] ?? '') === 'promoted')),
                'operator_approved_wallets' => count(array_filter($candidates, static fn (array $row): bool => !empty($row['operator_approved_pilot']))),
                'blocked_wallets' => array_sum($pilotBlockers),
                'blocker_counts' => $pilotBlockers,
            ];

            return [
                'discovery_wallet_summary' => [
                    'tracked_wallets' => count($candidates),
                    'discovery_pool_target' => 50,
                    'shadow_pool_target' => 20,
                    'copy_ready_target' => 5,
                    'crypto_specialists' => count(array_filter($candidates, static fn (array $row): bool => ($row['specialization'] ?? '') === 'CRYPTO')),
                    'promoted_to_shadow' => count($shadowWalletTable),
                    'persisted_wallet_snapshots' => count($candidates),
                ],
                'discovery_source_summary' => $discoverySourceSummary,
                'shadow_wallet_summary' => [
                    'shadow_wallets' => count($shadowWalletTable),
                    'wallets_with_shadow_actions' => count(array_filter($shadowWalletTable, static fn (array $row): bool => (int) ($row['closed_shadow_trades'] ?? 0) > 0)),
                    'closed_shadow_trades' => array_sum(array_map(static fn (array $row): int => (int) ($row['closed_shadow_trades'] ?? 0), $shadowWalletTable)),
                    'positive_shadow_wallets' => count(array_filter($shadowWalletTable, static fn (array $row): bool => (float) ($row['shadow_edge'] ?? 0.0) > 0.0)),
                ],
                'shadow_promotion_summary' => $shadowPromotionSummary,
                'copy_ready_wallet_summary' => [
                    'copy_ready_wallets' => count($copyReadyWallets),
                    'copy_ready_target' => 5,
                    'minimum_shadow_trades' => 3,
                    'positive_shadow_edge_wallets' => count(array_filter($shadowWalletTable, static fn (array $row): bool => (float) ($row['shadow_edge'] ?? 0.0) > 0.0)),
                ],
                'wallet_provenance_summary' => $walletProvenanceSummary,
                'shadow_edge_summary' => [
                    'evaluation_window_days' => 14,
                    'net_shadow_edge' => round(array_sum(array_map(static fn (array $row): float => (float) ($row['shadow_edge'] ?? 0.0), $shadowWalletTable)), 4),
                    'net_shadow_pnl' => round(array_sum(array_map(static fn (array $row): float => (float) ($row['shadow_pnl'] ?? 0.0), $shadowWalletTable)), 4),
                    'worst_drawdown_pct' => round(min(array_map(static fn (array $row): float => (float) ($row['worst_drawdown_pct'] ?? 0.0), $shadowWalletTable) ?: [0.0]), 4),
                    'shadow_ready' => count($copyReadyWallets) > 0,
                ],
                'priority_watchlist_summary' => $priorityWatchlistSummary,
                'identity_resolution_summary' => $identityResolutionSummary,
                'shadow_replay_summary' => $shadowReplaySummary,
                'linked_wallet_evidence_summary' => $linkedWalletEvidenceSummary,
                'shadow_evidence_backfill_summary' => $shadowEvidenceBackfillSummary,
                'long_horizon_watchlist_summary' => $longHorizonWatchlistSummary,
                'specialist_wallet_score_summary' => $specialistWalletScoreSummary,
                'observation_progress_summary' => $observationProgressSummary,
                'pilot_copy_admission_summary' => $pilotCopyAdmissionSummary,
                'priority_watchlist_rows' => $priorityWatchlistRows,
                'recent_shadow_actions' => $recentShadowActions,
                'wallet_consistency_table' => array_slice($candidates, 0, 12),
                'shadow_wallet_table' => $shadowWalletTable,
                'copy_ready_wallets' => $copyReadyWallets,
            ];
        }
    }

    if (!dashboard_table_has_column($pdo, 'whale_wallets', 'address')) {
        return [
            'discovery_wallet_summary' => [],
            'discovery_source_summary' => $emptyDiscoverySourceSummary,
            'shadow_wallet_summary' => [],
            'shadow_promotion_summary' => $emptyShadowPromotionSummary,
            'copy_ready_wallet_summary' => [],
            'wallet_provenance_summary' => $emptyWalletProvenanceSummary,
            'shadow_edge_summary' => [],
            'priority_watchlist_summary' => [],
            'identity_resolution_summary' => [],
            'shadow_replay_summary' => [],
            'linked_wallet_evidence_summary' => $emptyLinkedWalletEvidenceSummary,
            'shadow_evidence_backfill_summary' => $emptyShadowEvidenceBackfillSummary,
            'long_horizon_watchlist_summary' => $emptyLongHorizonWatchlistSummary,
            'specialist_wallet_score_summary' => $emptySpecialistWalletScoreSummary,
            'observation_progress_summary' => $emptyObservationProgressSummary,
            'pilot_copy_admission_summary' => $emptyPilotCopyAdmissionSummary,
            'priority_watchlist_rows' => [],
            'recent_shadow_actions' => [],
            'wallet_consistency_table' => [],
            'shadow_wallet_table' => [],
            'copy_ready_wallets' => [],
        ];
    }

    $tradeClosedAtExpr = dashboard_table_has_column($pdo, 'trades', 'closed_at')
        ? "closed_at"
        : "timestamp";

    $candidateRows = dashboard_fetch_all(
        $pdo,
        "
        WITH trade_stats AS (
            SELECT
                LOWER(COALESCE(whale_address, '')) AS address,
                COUNT(CASE WHEN status LIKE 'CLOSED%' THEN 1 END) AS closed_trades,
                ROUND(COALESCE(SUM(COALESCE(pnl, 0)), 0), 4) AS realized_pnl,
                COUNT(DISTINCT substr(COALESCE({$tradeClosedAtExpr}, timestamp), 1, 10)) AS active_days,
                COUNT(*) AS total_trade_rows,
                SUM(CASE WHEN UPPER(COALESCE(category, '')) = 'CRYPTO' THEN 1 ELSE 0 END) AS crypto_trade_rows,
                MAX(COALESCE(category, '')) AS specialization_hint
            FROM trades
            WHERE TRIM(COALESCE(whale_address, '')) != ''
            GROUP BY LOWER(COALESCE(whale_address, ''))
        )
        SELECT
            whale_wallets.address,
            whale_wallets.source_type,
            whale_wallets.discovery_score,
            whale_wallets.event_count_24h,
            whale_wallets.last_event_amount,
            whale_wallets.last_event_category,
            COALESCE(whale_stats.trust_score, 0.5) AS trust_score,
            COALESCE(whale_stats.total_trades, 0) AS closed_trade_count,
            ROUND(COALESCE(whale_stats.total_pnl, 0), 4) AS realized_pnl,
            COALESCE(trade_stats.active_days, 0) AS active_days,
            COALESCE(trade_stats.total_trade_rows, 0) AS total_trade_rows,
            COALESCE(trade_stats.crypto_trade_rows, 0) AS crypto_trade_rows,
            COALESCE(trade_stats.specialization_hint, whale_wallets.last_event_category, 'UNKNOWN') AS specialization_hint
        FROM whale_wallets
        LEFT JOIN whale_stats ON LOWER(whale_stats.address) = LOWER(whale_wallets.address)
        LEFT JOIN trade_stats ON trade_stats.address = LOWER(whale_wallets.address)
        WHERE whale_wallets.enabled = 1
        ORDER BY whale_wallets.discovery_score DESC, whale_wallets.last_event_amount DESC, whale_wallets.last_seen_at DESC
        LIMIT 50
        "
    );

    $candidates = [];
    foreach ($candidateRows as $row) {
        $totalTradeRows = max((int) ($row['total_trade_rows'] ?? 0), 0);
        $cryptoTradeRows = max((int) ($row['crypto_trade_rows'] ?? 0), 0);
        $cryptoRatio = $totalTradeRows > 0 ? $cryptoTradeRows / $totalTradeRows : 0.0;
        $trustScore = (float) ($row['trust_score'] ?? 0.5);
        $discoveryScore = (float) ($row['discovery_score'] ?? 0.0);
        $activeDays = (int) ($row['active_days'] ?? 0);
        $closedTrades = (int) ($row['closed_trade_count'] ?? 0);
        $realizedPnl = (float) ($row['realized_pnl'] ?? 0.0);
        $realizedComponent = min(max($realizedPnl / 5000.0, 0.0), 1.0);
        $activeComponent = min(max($activeDays / 14.0, 0.0), 1.0);
        $closedComponent = min(max($closedTrades / 25.0, 0.0), 1.0);
        $consistencyScore = round(
            ($trustScore * 0.30)
            + ($discoveryScore * 0.25)
            + ($closedComponent * 0.20)
            + ($activeComponent * 0.15)
            + ($realizedComponent * 0.10),
            4
        );
        $specialization = strtoupper((string) ($row['specialization_hint'] ?? $row['last_event_category'] ?? 'UNKNOWN'));
        if ($specialization === 'UNKNOWN' && $cryptoRatio >= 0.6) {
            $specialization = 'CRYPTO';
        }
        $candidates[] = [
            'address' => (string) ($row['address'] ?? ''),
            'source_type' => (string) ($row['source_type'] ?? ''),
            'discovery_score' => round($discoveryScore, 4),
            'trust_score' => round($trustScore, 4),
            'active_days' => $activeDays,
            'closed_trade_count' => $closedTrades,
            'realized_pnl' => round($realizedPnl, 4),
            'crypto_participation_ratio' => round($cryptoRatio, 4),
            'specialization' => $specialization,
            'event_count_24h' => (int) ($row['event_count_24h'] ?? 0),
            'last_event_amount' => round((float) ($row['last_event_amount'] ?? 0.0), 4),
            'consistency_score' => $consistencyScore,
        ];
    }

    usort(
        $candidates,
        static fn (array $left, array $right): int => [$right['consistency_score'], $right['trust_score'], $right['realized_pnl']]
            <=> [$left['consistency_score'], $left['trust_score'], $left['realized_pnl']]
    );

    $shadowAggregates = [];
    if (dashboard_table_exists($pdo, 'polymarket_shadow_actions')) {
        $shadowRows = dashboard_fetch_all(
            $pdo,
            "
            SELECT
                wallet_address,
                COUNT(CASE WHEN status != 'OPEN' THEN 1 END) AS closed_shadow_trades,
                ROUND(COALESCE(SUM(COALESCE(shadow_pnl, 0)), 0), 4) AS shadow_pnl,
                ROUND(COALESCE(SUM(COALESCE(shadow_edge, 0)), 0), 4) AS shadow_edge,
                ROUND(MIN(COALESCE(drawdown_pct, 0)), 4) AS worst_drawdown_pct
            FROM polymarket_shadow_actions
            WHERE opened_at >= datetime('now', '-14 days')
            GROUP BY wallet_address
            "
        );
        foreach ($shadowRows as $row) {
            $shadowAggregates[strtolower((string) ($row['wallet_address'] ?? ''))] = [
                'closed_shadow_trades' => (int) ($row['closed_shadow_trades'] ?? 0),
                'shadow_pnl' => round((float) ($row['shadow_pnl'] ?? 0.0), 4),
                'shadow_edge' => round((float) ($row['shadow_edge'] ?? 0.0), 4),
                'worst_drawdown_pct' => round((float) ($row['worst_drawdown_pct'] ?? 0.0), 4),
            ];
        }
    }

    $shadowWalletTable = [];
    foreach (array_slice($candidates, 0, 20) as $candidate) {
        $shadowStats = $shadowAggregates[strtolower($candidate['address'])] ?? [
            'closed_shadow_trades' => 0,
            'shadow_pnl' => 0.0,
            'shadow_edge' => 0.0,
            'worst_drawdown_pct' => 0.0,
        ];
        $shadowWalletTable[] = $candidate + $shadowStats;
    }

    $copyReadyWallets = array_values(
        array_slice(
            array_filter(
                $shadowWalletTable,
                static fn (array $row): bool =>
                    (int) ($row['closed_shadow_trades'] ?? 0) >= 3
                    && (float) ($row['shadow_edge'] ?? 0.0) > 0.0
                    && (float) ($row['worst_drawdown_pct'] ?? 0.0) >= -15.0
            ),
            0,
            5
        )
    );

    $recentShadowActions = [];
    if (dashboard_table_exists($pdo, 'polymarket_shadow_actions')) {
        $recentShadowRows = dashboard_fetch_all(
            $pdo,
            "
            SELECT
                wallet_address,
                market_id,
                category,
                source_type,
                action_type,
                shadow_pnl,
                shadow_edge,
                drawdown_pct,
                opened_at,
                closed_at,
                status
            FROM polymarket_shadow_actions
            ORDER BY COALESCE(closed_at, opened_at) DESC, id DESC
            LIMIT 12
            "
        );
        foreach ($recentShadowRows as $row) {
            $recentShadowActions[] = [
                'wallet_address' => (string) ($row['wallet_address'] ?? ''),
                'market_id' => (string) ($row['market_id'] ?? ''),
                'category' => (string) ($row['category'] ?? 'UNKNOWN'),
                'source_type' => (string) ($row['source_type'] ?? 'unknown'),
                'action_type' => (string) ($row['action_type'] ?? 'shadow_trade'),
                'shadow_pnl' => round((float) ($row['shadow_pnl'] ?? 0.0), 4),
                'shadow_edge' => round((float) ($row['shadow_edge'] ?? 0.0), 4),
                'drawdown_pct' => round((float) ($row['drawdown_pct'] ?? 0.0), 4),
                'opened_at' => (string) ($row['opened_at'] ?? ''),
                'closed_at' => (string) ($row['closed_at'] ?? ''),
                'status' => (string) ($row['status'] ?? 'OPEN'),
            ];
        }
    }

    return [
        'discovery_wallet_summary' => [
            'tracked_wallets' => count($candidates),
            'discovery_pool_target' => 50,
            'shadow_pool_target' => 20,
            'copy_ready_target' => 5,
            'crypto_specialists' => count(array_filter($candidates, static fn (array $row): bool => $row['specialization'] === 'CRYPTO')),
            'promoted_to_shadow' => min(count($candidates), 20),
        ],
        'discovery_source_summary' => $emptyDiscoverySourceSummary,
        'shadow_wallet_summary' => [
            'shadow_wallets' => count($shadowWalletTable),
            'wallets_with_shadow_actions' => count(array_filter($shadowWalletTable, static fn (array $row): bool => (int) ($row['closed_shadow_trades'] ?? 0) > 0)),
            'closed_shadow_trades' => array_sum(array_map(static fn (array $row): int => (int) ($row['closed_shadow_trades'] ?? 0), $shadowWalletTable)),
            'positive_shadow_wallets' => count(array_filter($shadowWalletTable, static fn (array $row): bool => (float) ($row['shadow_edge'] ?? 0.0) > 0.0)),
        ],
        'shadow_promotion_summary' => $emptyShadowPromotionSummary,
        'copy_ready_wallet_summary' => [
            'copy_ready_wallets' => count($copyReadyWallets),
            'copy_ready_target' => 5,
            'minimum_shadow_trades' => 3,
            'positive_shadow_edge_wallets' => count(array_filter($shadowWalletTable, static fn (array $row): bool => (float) ($row['shadow_edge'] ?? 0.0) > 0.0)),
        ],
        'wallet_provenance_summary' => $emptyWalletProvenanceSummary,
        'shadow_edge_summary' => [
            'evaluation_window_days' => 14,
            'net_shadow_edge' => round(array_sum(array_map(static fn (array $row): float => (float) ($row['shadow_edge'] ?? 0.0), $shadowWalletTable)), 4),
            'net_shadow_pnl' => round(array_sum(array_map(static fn (array $row): float => (float) ($row['shadow_pnl'] ?? 0.0), $shadowWalletTable)), 4),
            'worst_drawdown_pct' => round(min(array_map(static fn (array $row): float => (float) ($row['worst_drawdown_pct'] ?? 0.0), $shadowWalletTable) ?: [0.0]), 4),
            'shadow_ready' => count($copyReadyWallets) > 0,
        ],
        'priority_watchlist_summary' => [],
        'identity_resolution_summary' => [],
        'shadow_replay_summary' => [],
        'linked_wallet_evidence_summary' => $emptyLinkedWalletEvidenceSummary,
            'shadow_evidence_backfill_summary' => $emptyShadowEvidenceBackfillSummary,
            'long_horizon_watchlist_summary' => $emptyLongHorizonWatchlistSummary,
            'specialist_wallet_score_summary' => $emptySpecialistWalletScoreSummary,
            'observation_progress_summary' => $emptyObservationProgressSummary,
            'pilot_copy_admission_summary' => $emptyPilotCopyAdmissionSummary,
            'priority_watchlist_rows' => [],
        'recent_shadow_actions' => $recentShadowActions,
        'wallet_consistency_table' => array_slice($shadowWalletTable, 0, 12),
        'shadow_wallet_table' => $shadowWalletTable,
        'copy_ready_wallets' => $copyReadyWallets,
    ];
}

function dashboard_empty_polymarket_copy_summary(): array
{
    return [
        'copy_execution_summary' => [
            'copy_ready_wallets' => 0,
            'shadow_proven_wallets' => 0,
            'manual_fast_track_wallets' => 0,
            'pilot_copy_wallets' => 0,
            'watch_only_wallets' => 0,
            'copy_blocker_reason' => 'no_eligible_copy_wallets',
            'eligible_copy_wallets_total' => 0,
            'copy_window_days' => 14,
            'open_actions' => 0,
            'close_actions' => 0,
            'replay_closed_actions' => 0,
            'reject_actions' => 0,
            'active_copy_positions' => 0,
            'active_shadow_proven_positions' => 0,
            'active_manual_fast_track_positions' => 0,
            'active_pilot_copy_positions' => 0,
            'wallets_with_realized_pnl' => 0,
        ],
        'copy_acceptance_summary' => [
            'last_acceptance_at' => '',
            'all_checks_passed' => false,
            'copy_open_action_observed' => false,
            'copy_open_action_id' => 0,
            'copy_open_position_observed' => false,
            'copy_open_position_id' => 0,
            'acceptance_actions' => 0,
            'acceptance_open_positions' => 0,
            'reason' => 'acceptance_copy_entry_missing',
        ],
        'copy_reject_breakdown' => [],
        'active_copy_positions' => [],
        'wallet_follower_pnl_summary' => [],
        'shadow_vs_copy_drift_summary' => [
            'copy_ready_wallets' => 0,
            'shadow_proven_wallets' => 0,
            'manual_fast_track_wallets' => 0,
            'pilot_copy_wallets' => 0,
            'watch_only_wallets' => 0,
            'copy_blocker_reason' => 'no_eligible_copy_wallets',
            'eligible_copy_wallets_total' => 0,
            'shadow_closed_trades' => 0,
            'shadow_net_edge' => 0.0,
            'source_realized_pnl' => 0.0,
            'copy_realized_pnl' => 0.0,
            'copy_vs_source_pnl_gap' => 0.0,
        ],
        'recent_copy_actions' => [],
    ];
}

function dashboard_is_polymarket_copy_acceptance_row(array $row): bool
{
    $sourceTradeKey = strtolower((string) ($row['source_trade_key'] ?? ''));
    if (str_starts_with($sourceTradeKey, 'acceptance:')) {
        return true;
    }
    $notes = dashboard_decode_inputs_json((string) ($row['notes_json'] ?? '{}'));
    return ($notes['acceptance_fixture'] ?? false) === true
        || (string) ($notes['cohort_source'] ?? '') === 'acceptance_fixture';
}

function dashboard_build_copy_acceptance_summary_from_rows(array $actions, array $positions): array
{
    $openActions = array_values(array_filter(
        $actions,
        static fn (array $row): bool => (string) ($row['action_type'] ?? '') === 'open'
    ));
    $openPositions = array_values(array_filter(
        $positions,
        static fn (array $row): bool => strtoupper((string) ($row['status'] ?? '')) === 'OPEN'
    ));
    $latestTs = '';
    foreach (array_merge($openActions, $openPositions) as $row) {
        $candidate = (string) ($row['executed_at'] ?? $row['opened_at'] ?? '');
        if ($candidate !== '' && $candidate > $latestTs) {
            $latestTs = $candidate;
        }
    }
    $firstAction = $openActions[0] ?? [];
    $firstPosition = $openPositions[0] ?? [];
    $actionSeen = !empty($openActions);
    $positionSeen = !empty($openPositions);
    return [
        'last_acceptance_at' => $latestTs,
        'all_checks_passed' => $actionSeen && $positionSeen,
        'copy_open_action_observed' => $actionSeen,
        'copy_open_action_id' => (int) ($firstAction['id'] ?? 0),
        'copy_open_position_observed' => $positionSeen,
        'copy_open_position_id' => (int) ($firstPosition['id'] ?? 0),
        'acceptance_actions' => count($actions),
        'acceptance_open_positions' => count($openPositions),
        'reason' => $actionSeen && $positionSeen ? 'acceptance_copy_entry' : 'acceptance_copy_entry_missing',
    ];
}

function dashboard_build_copy_runtime_acceptance_summary_from_rows(array $actions, array $positions, array $copyExecutionSummary): array
{
    $openActions = array_values(array_filter(
        $actions,
        static fn (array $row): bool => (string) ($row['action_type'] ?? '') === 'open'
    ));
    $closeActions = array_values(array_filter(
        $actions,
        static fn (array $row): bool => (string) ($row['action_type'] ?? '') === 'close'
    ));
    $replayClosedActions = array_values(array_filter(
        $actions,
        static fn (array $row): bool => (string) ($row['action_type'] ?? '') === 'replay_closed'
    ));
    $openPositions = array_values(array_filter(
        $positions,
        static fn (array $row): bool => strtoupper((string) ($row['status'] ?? '')) === 'OPEN'
    ));
    $latestTs = '';
    foreach (array_merge($actions, $positions) as $row) {
        $candidate = (string) ($row['executed_at'] ?? $row['opened_at'] ?? '');
        if ($candidate !== '' && $candidate > $latestTs) {
            $latestTs = $candidate;
        }
    }

    $eligibleWallets = (int) ($copyExecutionSummary['eligible_copy_wallets_total'] ?? 0);
    if ($eligibleWallets <= 0) {
        $reason = 'no_eligible_copy_wallets';
    } elseif (empty($openActions) && empty($openPositions) && empty($closeActions) && empty($replayClosedActions)) {
        $reason = 'eligible_wallets_no_runtime_actions';
    } else {
        $reason = 'runtime_copy_active';
    }

    return [
        'last_runtime_action_at' => $latestTs,
        'all_checks_passed' => $eligibleWallets > 0 && !empty($openActions) && !empty($openPositions),
        'runtime_open_action_observed' => !empty($openActions),
        'runtime_open_position_observed' => !empty($openPositions),
        'runtime_close_action_observed' => !empty($closeActions),
        'runtime_replay_closed_observed' => !empty($replayClosedActions),
        'runtime_action_rows' => count($actions),
        'runtime_open_positions' => count($openPositions),
        'eligible_copy_wallets_total' => $eligibleWallets,
        'shadow_proven_wallets' => (int) ($copyExecutionSummary['shadow_proven_wallets'] ?? 0),
        'manual_fast_track_wallets' => (int) ($copyExecutionSummary['manual_fast_track_wallets'] ?? 0),
        'runtime_blocker_reason' => $reason,
        'runtime_schema_guard_status' => 'ok',
        'reason' => $reason,
    ];
}

function dashboard_build_polymarket_copy_summary(PDO $pdo): array
{
    $summary = dashboard_empty_polymarket_copy_summary();
    $eligibleWalletMap = [];

    if (dashboard_table_exists($pdo, 'polymarket_research_wallets')) {
        $copyReadyExpr = dashboard_table_has_column($pdo, 'polymarket_research_wallets', 'copy_ready_gate_status')
            ? 'copy_ready_gate_status'
            : "'blocked'";
        $pilotCopyExpr = dashboard_table_has_column($pdo, 'polymarket_research_wallets', 'pilot_copy_gate_status')
            ? 'pilot_copy_gate_status'
            : "'blocked'";
        $pilotReasonExpr = dashboard_table_has_column($pdo, 'polymarket_research_wallets', 'pilot_copy_gate_reason')
            ? 'pilot_copy_gate_reason'
            : "'operator_approval_required'";
        $longHorizonExpr = dashboard_table_has_column($pdo, 'polymarket_research_wallets', 'long_horizon_status')
            ? 'long_horizon_status'
            : "'untracked'";
        $identityStatusExpr = dashboard_table_has_column($pdo, 'polymarket_research_wallets', 'identity_resolution_status')
            ? 'identity_resolution_status'
            : "''";
        $evidenceExpr = dashboard_table_has_column($pdo, 'polymarket_research_wallets', 'historical_trade_evidence_status')
            ? 'historical_trade_evidence_status'
            : "'no_historical_evidence'";
        $specializationExpr = dashboard_table_has_column($pdo, 'polymarket_research_wallets', 'specialization')
            ? 'specialization'
            : "''";
        $targetSpecializationExpr = dashboard_table_has_column($pdo, 'polymarket_research_wallets', 'target_specialization')
            ? 'target_specialization'
            : "''";
        $trustScoreExpr = dashboard_table_has_column($pdo, 'polymarket_research_wallets', 'trust_score')
            ? 'trust_score'
            : '0';
        $shadowEdgeExpr = dashboard_table_has_column($pdo, 'polymarket_research_wallets', 'shadow_edge')
            ? 'shadow_edge'
            : '0';
        $closedShadowExpr = dashboard_table_has_column($pdo, 'polymarket_research_wallets', 'closed_shadow_trades')
            ? 'closed_shadow_trades'
            : '0';
        $copyReadyRankExpr = dashboard_table_has_column($pdo, 'polymarket_research_wallets', 'copy_ready_rank')
            ? 'copy_ready_rank'
            : '999999';
        $watchlistRankExpr = dashboard_table_has_column($pdo, 'polymarket_research_wallets', 'watchlist_priority_rank')
            ? 'watchlist_priority_rank'
            : '999999';
        $pilotCopyCondition = "COALESCE({$pilotCopyExpr}, 'blocked') = 'promoted'";
        $eligibleWalletRows = dashboard_fetch_all(
            $pdo,
            "
            SELECT
                address,
                COALESCE({$specializationExpr}, '') AS specialization,
                COALESCE({$trustScoreExpr}, 0) AS trust_score,
                COALESCE({$shadowEdgeExpr}, 0) AS shadow_edge,
                COALESCE({$closedShadowExpr}, 0) AS closed_shadow_trades,
                COALESCE({$copyReadyRankExpr}, 999999) AS copy_ready_rank,
                COALESCE({$watchlistRankExpr}, 999999) AS watchlist_priority_rank,
                COALESCE({$identityStatusExpr}, '') AS identity_resolution_status,
                COALESCE({$evidenceExpr}, 'no_historical_evidence') AS historical_trade_evidence_status,
                COALESCE({$pilotCopyExpr}, 'blocked') AS pilot_copy_gate_status,
                COALESCE({$pilotReasonExpr}, 'operator_approval_required') AS pilot_copy_gate_reason,
                COALESCE({$longHorizonExpr}, 'untracked') AS long_horizon_status,
                CASE
                    WHEN COALESCE({$copyReadyExpr}, 'blocked') = 'promoted' THEN 'shadow_proven'
                    WHEN ({$pilotCopyCondition}) THEN 'pilot_copy_ready'
                    ELSE ''
                END AS cohort_source
            FROM polymarket_research_wallets
            WHERE COALESCE({$copyReadyExpr}, 'blocked') = 'promoted'
               OR ({$pilotCopyCondition})
            ORDER BY
                CASE
                    WHEN COALESCE({$copyReadyExpr}, 'blocked') = 'promoted' THEN 0
                    ELSE 1
                END ASC,
                CASE
                    WHEN COALESCE({$copyReadyExpr}, 'blocked') = 'promoted' THEN COALESCE({$copyReadyRankExpr}, 999999)
                    ELSE COALESCE({$watchlistRankExpr}, 999999)
                END ASC,
                COALESCE({$shadowEdgeExpr}, 0) DESC,
                COALESCE({$trustScoreExpr}, 0) DESC,
                address ASC
            "
        );
        foreach ($eligibleWalletRows as $row) {
            $eligibleWalletMap[strtolower((string) ($row['address'] ?? ''))] = $row;
        }
        $shadowProvenWallets = count(array_filter($eligibleWalletRows, static fn (array $row): bool => (string) ($row['cohort_source'] ?? '') === 'shadow_proven'));
        $pilotCopyWallets = count(array_filter($eligibleWalletRows, static fn (array $row): bool => (string) ($row['cohort_source'] ?? '') === 'pilot_copy_ready'));
        $manualFastTrackWallets = $pilotCopyWallets;
        $watchOnlyRows = dashboard_fetch_one(
            $pdo,
            "
            SELECT COUNT(*) AS count
            FROM polymarket_research_wallets
            WHERE COALESCE({$longHorizonExpr}, 'untracked') IN ('priority_watch', 'linked', 'observing', 'shadow_tracking')
              AND COALESCE({$copyReadyExpr}, 'blocked') <> 'promoted'
              AND COALESCE({$pilotCopyExpr}, 'blocked') <> 'promoted'
            "
        );
        $watchOnlyWallets = (int) ($watchOnlyRows['count'] ?? 0);
        $copyBlockerReason = count($eligibleWalletRows) > 0
            ? 'eligible_copy_wallets_available'
            : ($watchOnlyWallets > 0 ? 'watch_only_needs_shadow_or_pilot_proof' : 'no_eligible_copy_wallets');
        $shadowClosedTrades = array_sum(array_map(static fn (array $row): int => (int) ($row['closed_shadow_trades'] ?? 0), $eligibleWalletRows));
        $shadowNetEdge = round(array_sum(array_map(static fn (array $row): float => (float) ($row['shadow_edge'] ?? 0.0), $eligibleWalletRows)), 4);

        $summary['copy_execution_summary']['copy_ready_wallets'] = $shadowProvenWallets;
        $summary['copy_execution_summary']['shadow_proven_wallets'] = $shadowProvenWallets;
        $summary['copy_execution_summary']['manual_fast_track_wallets'] = $manualFastTrackWallets;
        $summary['copy_execution_summary']['pilot_copy_wallets'] = $pilotCopyWallets;
        $summary['copy_execution_summary']['watch_only_wallets'] = $watchOnlyWallets;
        $summary['copy_execution_summary']['copy_blocker_reason'] = $copyBlockerReason;
        $summary['copy_execution_summary']['eligible_copy_wallets_total'] = count($eligibleWalletRows);
        $summary['shadow_vs_copy_drift_summary']['copy_ready_wallets'] = $shadowProvenWallets;
        $summary['shadow_vs_copy_drift_summary']['shadow_proven_wallets'] = $shadowProvenWallets;
        $summary['shadow_vs_copy_drift_summary']['manual_fast_track_wallets'] = $manualFastTrackWallets;
        $summary['shadow_vs_copy_drift_summary']['pilot_copy_wallets'] = $pilotCopyWallets;
        $summary['shadow_vs_copy_drift_summary']['watch_only_wallets'] = $watchOnlyWallets;
        $summary['shadow_vs_copy_drift_summary']['copy_blocker_reason'] = $copyBlockerReason;
        $summary['shadow_vs_copy_drift_summary']['eligible_copy_wallets_total'] = count($eligibleWalletRows);
        $summary['shadow_vs_copy_drift_summary']['shadow_closed_trades'] = $shadowClosedTrades;
        $summary['shadow_vs_copy_drift_summary']['shadow_net_edge'] = $shadowNetEdge;
    }

    if (!dashboard_table_exists($pdo, 'polymarket_copy_actions')) {
        return $summary;
    }

    $actionCounts = dashboard_fetch_all(
        $pdo,
        "SELECT action_type, COUNT(*) AS count FROM polymarket_copy_actions GROUP BY action_type"
    );
    $acceptanceActionRows = dashboard_fetch_all(
        $pdo,
        "
        SELECT id, executed_at, action_type, source_trade_key, notes_json
        FROM polymarket_copy_actions
        WHERE source_trade_key LIKE 'acceptance:%'
           OR COALESCE(notes_json, '') LIKE '%acceptance_fixture%'
        ORDER BY executed_at DESC, id DESC
        "
    );
    $acceptanceActionIds = [];
    foreach ($acceptanceActionRows as $row) {
        $acceptanceActionIds[(int) ($row['id'] ?? 0)] = true;
    }
    foreach ($actionCounts as $row) {
        $actionType = (string) ($row['action_type'] ?? '');
        $count = (int) ($row['count'] ?? 0);
        if ($actionType === 'open') {
            $summary['copy_execution_summary']['open_actions'] = $count;
        } elseif ($actionType === 'close') {
            $summary['copy_execution_summary']['close_actions'] = $count;
        } elseif ($actionType === 'replay_closed') {
            $summary['copy_execution_summary']['replay_closed_actions'] = $count;
        } elseif ($actionType === 'reject') {
            $summary['copy_execution_summary']['reject_actions'] = $count;
        }
    }

    $rejectRows = dashboard_fetch_all(
        $pdo,
        "
        SELECT reason, COUNT(*) AS count
        FROM polymarket_copy_actions
        WHERE action_type = 'reject'
          AND source_trade_key NOT LIKE 'acceptance:%'
          AND COALESCE(notes_json, '') NOT LIKE '%acceptance_fixture%'
        GROUP BY reason
        ORDER BY count DESC, reason ASC
        "
    );
    $summary['copy_reject_breakdown'] = array_map(
        static fn (array $row): array => [
            'reason' => (string) ($row['reason'] ?? 'unknown'),
            'count' => (int) ($row['count'] ?? 0),
        ],
        $rejectRows
    );

    $pnlRows = dashboard_fetch_all(
        $pdo,
        "
        SELECT
            wallet_address,
            COUNT(CASE WHEN action_type IN ('close', 'replay_closed') THEN 1 END) AS closed_actions,
            ROUND(COALESCE(SUM(CASE WHEN action_type IN ('close', 'replay_closed') THEN COALESCE(follower_pnl, 0) ELSE 0 END), 0), 4) AS follower_realized_pnl,
            ROUND(COALESCE(SUM(CASE WHEN action_type IN ('close', 'replay_closed') THEN COALESCE(source_pnl, 0) ELSE 0 END), 0), 4) AS source_realized_pnl,
            ROUND(COALESCE(SUM(CASE WHEN action_type = 'open' THEN COALESCE(follower_notional_usd, 0) ELSE 0 END), 0), 4) AS opened_notional_usd,
            MAX(executed_at) AS last_action_at
        FROM polymarket_copy_actions
        WHERE source_trade_key NOT LIKE 'acceptance:%'
          AND COALESCE(notes_json, '') NOT LIKE '%acceptance_fixture%'
        GROUP BY wallet_address
        HAVING COUNT(CASE WHEN action_type IN ('close', 'replay_closed') THEN 1 END) > 0
            OR COALESCE(SUM(CASE WHEN action_type = 'open' THEN COALESCE(follower_notional_usd, 0) ELSE 0 END), 0) > 0
        ORDER BY follower_realized_pnl DESC, last_action_at DESC
        LIMIT 20
        "
    );
    $summary['wallet_follower_pnl_summary'] = array_map(
        static function (array $row) use ($eligibleWalletMap): array {
            $followerPnl = (float) ($row['follower_realized_pnl'] ?? 0.0);
            $sourcePnl = (float) ($row['source_realized_pnl'] ?? 0.0);
            $walletAddress = strtolower((string) ($row['wallet_address'] ?? ''));
            $cohortSource = (string) (($eligibleWalletMap[$walletAddress]['cohort_source'] ?? '') ?: 'shadow_proven');
            return [
                'wallet_address' => (string) ($row['wallet_address'] ?? ''),
                'cohort_source' => $cohortSource,
                'closed_actions' => (int) ($row['closed_actions'] ?? 0),
                'follower_realized_pnl' => round($followerPnl, 4),
                'source_realized_pnl' => round($sourcePnl, 4),
                'pnl_drift' => round($followerPnl - $sourcePnl, 4),
                'opened_notional_usd' => round((float) ($row['opened_notional_usd'] ?? 0.0), 4),
                'last_action_at' => (string) ($row['last_action_at'] ?? ''),
            ];
        },
        $pnlRows
    );
    $summary['copy_execution_summary']['wallets_with_realized_pnl'] = count(
        array_filter(
            $summary['wallet_follower_pnl_summary'],
            static fn (array $row): bool => (int) ($row['closed_actions'] ?? 0) > 0
        )
    );

    $sourcePnl = array_sum(array_map(static fn (array $row): float => (float) ($row['source_realized_pnl'] ?? 0.0), $summary['wallet_follower_pnl_summary']));
    $copyPnl = array_sum(array_map(static fn (array $row): float => (float) ($row['follower_realized_pnl'] ?? 0.0), $summary['wallet_follower_pnl_summary']));
    $summary['shadow_vs_copy_drift_summary']['source_realized_pnl'] = round($sourcePnl, 4);
    $summary['shadow_vs_copy_drift_summary']['copy_realized_pnl'] = round($copyPnl, 4);
    $summary['shadow_vs_copy_drift_summary']['copy_vs_source_pnl_gap'] = round($copyPnl - $sourcePnl, 4);

    $recentRows = dashboard_fetch_all(
        $pdo,
        "
        SELECT
            executed_at,
            wallet_address,
            market_id,
            category,
            action_type,
            reason,
            side,
            source_status,
            source_notional_usd,
            follower_notional_usd,
            source_pnl,
            follower_pnl,
            delayed_seconds,
            source_opened_at,
            source_closed_at,
            notes_json
        FROM polymarket_copy_actions
        WHERE source_trade_key NOT LIKE 'acceptance:%'
          AND COALESCE(notes_json, '') NOT LIKE '%acceptance_fixture%'
        ORDER BY executed_at DESC, id DESC
        LIMIT 12
        "
    );
    $summary['recent_copy_actions'] = array_map(
        static function (array $row) use ($eligibleWalletMap): array {
            $notes = dashboard_decode_inputs_json((string) ($row['notes_json'] ?? '{}'));
            $walletAddress = strtolower((string) ($row['wallet_address'] ?? ''));
            $cohortSource = (string) (($notes['cohort_source'] ?? '') ?: ($eligibleWalletMap[$walletAddress]['cohort_source'] ?? 'shadow_proven'));
            return [
                'executed_at' => (string) ($row['executed_at'] ?? ''),
                'wallet_address' => (string) ($row['wallet_address'] ?? ''),
                'cohort_source' => $cohortSource,
                'market_id' => (string) ($row['market_id'] ?? ''),
                'category' => (string) ($row['category'] ?? 'UNKNOWN'),
                'action_type' => (string) ($row['action_type'] ?? ''),
                'reason' => (string) ($row['reason'] ?? ''),
                'side' => (string) ($row['side'] ?? ''),
                'source_status' => (string) ($row['source_status'] ?? ''),
                'source_notional_usd' => round((float) ($row['source_notional_usd'] ?? 0.0), 4),
                'follower_notional_usd' => round((float) ($row['follower_notional_usd'] ?? 0.0), 4),
                'source_pnl' => round((float) ($row['source_pnl'] ?? 0.0), 4),
                'follower_pnl' => round((float) ($row['follower_pnl'] ?? 0.0), 4),
                'delayed_seconds' => (int) ($row['delayed_seconds'] ?? 0),
                'source_opened_at' => (string) ($row['source_opened_at'] ?? ''),
                'source_closed_at' => (string) ($row['source_closed_at'] ?? ''),
                'notes' => $notes,
            ];
        },
        $recentRows
    );

    if (dashboard_table_exists($pdo, 'polymarket_copy_positions')) {
        $activeRows = dashboard_fetch_all(
            $pdo,
            "
            SELECT
                wallet_address,
                market_id,
                category,
                side,
                source_notional_usd,
                follower_notional_usd,
                opened_at,
                source_opened_at,
                status,
                notes_json
            FROM polymarket_copy_positions
            WHERE status = 'OPEN'
              AND source_trade_key NOT LIKE 'acceptance:%'
              AND COALESCE(notes_json, '') NOT LIKE '%acceptance_fixture%'
            ORDER BY opened_at DESC, id DESC
            LIMIT 20
            "
        );
        $acceptancePositionRows = dashboard_fetch_all(
            $pdo,
            "
            SELECT id, opened_at, status, source_trade_key, notes_json
            FROM polymarket_copy_positions
            WHERE source_trade_key LIKE 'acceptance:%'
               OR COALESCE(notes_json, '') LIKE '%acceptance_fixture%'
            ORDER BY opened_at DESC, id DESC
            "
        );
        $summary['active_copy_positions'] = array_map(
            static function (array $row) use ($eligibleWalletMap): array {
                $notes = dashboard_decode_inputs_json((string) ($row['notes_json'] ?? '{}'));
                $walletAddress = strtolower((string) ($row['wallet_address'] ?? ''));
                $cohortSource = (string) (($notes['cohort_source'] ?? '') ?: ($eligibleWalletMap[$walletAddress]['cohort_source'] ?? 'shadow_proven'));
                return [
                    'wallet_address' => (string) ($row['wallet_address'] ?? ''),
                    'cohort_source' => $cohortSource,
                    'market_id' => (string) ($row['market_id'] ?? ''),
                    'category' => (string) ($row['category'] ?? 'UNKNOWN'),
                    'side' => (string) ($row['side'] ?? ''),
                    'source_notional_usd' => round((float) ($row['source_notional_usd'] ?? 0.0), 4),
                    'follower_notional_usd' => round((float) ($row['follower_notional_usd'] ?? 0.0), 4),
                    'opened_at' => (string) ($row['opened_at'] ?? ''),
                    'source_opened_at' => (string) ($row['source_opened_at'] ?? ''),
                    'status' => (string) ($row['status'] ?? 'OPEN'),
                ];
            },
            $activeRows
        );
        $summary['copy_execution_summary']['active_copy_positions'] = count($summary['active_copy_positions']);
        $summary['copy_execution_summary']['active_shadow_proven_positions'] = count(
            array_filter(
                $summary['active_copy_positions'],
                static fn (array $row): bool => (string) ($row['cohort_source'] ?? '') === 'shadow_proven'
            )
        );
        $summary['copy_execution_summary']['active_manual_fast_track_positions'] = count(
            array_filter(
                $summary['active_copy_positions'],
                static fn (array $row): bool => in_array((string) ($row['cohort_source'] ?? ''), ['manual_fast_track', 'pilot_copy_ready'], true)
            )
        );
        $summary['copy_execution_summary']['active_pilot_copy_positions'] = count(
            array_filter(
                $summary['active_copy_positions'],
                static fn (array $row): bool => (string) ($row['cohort_source'] ?? '') === 'pilot_copy_ready'
            )
        );
        $summary['copy_acceptance_summary'] = dashboard_build_copy_acceptance_summary_from_rows(
            array_values(array_filter($acceptanceActionRows, 'dashboard_is_polymarket_copy_acceptance_row')),
            array_values(array_filter($acceptancePositionRows, 'dashboard_is_polymarket_copy_acceptance_row'))
        );
        $summary['copy_runtime_acceptance_summary'] = dashboard_build_copy_runtime_acceptance_summary_from_rows(
            $summary['recent_copy_actions'],
            $summary['active_copy_positions'],
            $summary['copy_execution_summary']
        );
    } else {
        $summary['copy_acceptance_summary'] = dashboard_build_copy_acceptance_summary_from_rows(
            array_values(array_filter($acceptanceActionRows, 'dashboard_is_polymarket_copy_acceptance_row')),
            []
        );
        $summary['copy_runtime_acceptance_summary'] = dashboard_build_copy_runtime_acceptance_summary_from_rows(
            $summary['recent_copy_actions'],
            [],
            $summary['copy_execution_summary']
        );
    }

    return $summary;
}

function dashboard_is_binance_acceptance_row(array $row): bool
{
    $inputs = dashboard_decode_inputs_json((string) ($row['inputs_json'] ?? '{}'));
    return ($inputs['acceptance_fixture'] ?? false) === true
        || (string) ($inputs['sample_kind'] ?? '') === 'acceptance_fixture';
}

function dashboard_is_binance_acceptance_position(array $row): bool
{
    return (string) ($row['sample_kind'] ?? '') === 'acceptance_fixture'
        || (string) ($row['source_signal'] ?? '') === 'binance_acceptance_fixture';
}

function dashboard_build_binance_technical_acceptance_summary(array $decisionRows, array $positionRows): array
{
    $find = static function (string $venue, string $action, ?string $direction = null, ?string $reason = null) use ($decisionRows): ?array {
        foreach ($decisionRows as $row) {
            $inputs = dashboard_decode_inputs_json((string) ($row['inputs_json'] ?? '{}'));
            if ((string) ($row['venue'] ?? '') !== $venue) {
                continue;
            }
            if (strtolower((string) ($row['action'] ?? '')) !== strtolower($action)) {
                continue;
            }
            if ($direction !== null && strtoupper((string) ($inputs['signal_direction'] ?? '')) !== $direction) {
                continue;
            }
            if ($reason !== null && !str_contains((string) ($row['reason'] ?? ''), $reason)) {
                continue;
            }
            return $row;
        }
        return null;
    };

    $futuresLong = $find('binance_futures', 'execute', 'LONG');
    $futuresShort = $find('binance_futures', 'execute', 'SHORT');
    $spotLong = $find('binance_spot', 'execute', 'LONG');
    $spotShortReject = $find('binance_spot', 'reject', 'SHORT', 'spot_short_not_supported');
    $latestTs = '';
    foreach ($decisionRows as $row) {
        $candidate = (string) ($row['occurred_at'] ?? '');
        if ($candidate !== '' && $candidate > $latestTs) {
            $latestTs = $candidate;
        }
    }

    return [
        'last_acceptance_at' => $latestTs,
        'all_checks_passed' => !empty($futuresLong) && !empty($futuresShort) && !empty($spotLong) && !empty($spotShortReject),
        'futures_long_execute' => !empty($futuresLong),
        'futures_long_execute_id' => (int) ($futuresLong['id'] ?? 0),
        'futures_short_execute' => !empty($futuresShort),
        'futures_short_execute_id' => (int) ($futuresShort['id'] ?? 0),
        'spot_long_execute' => !empty($spotLong),
        'spot_long_execute_id' => (int) ($spotLong['id'] ?? 0),
        'spot_short_reject' => !empty($spotShortReject),
        'spot_short_reject_id' => (int) ($spotShortReject['id'] ?? 0),
        'acceptance_decision_rows' => count($decisionRows),
        'acceptance_open_positions' => count($positionRows),
    ];
}

function dashboard_build_binance_technical_runtime_acceptance_summary(array $decisionRows, array $positionRows): array
{
    $find = static function (string $venue, string $action, ?string $direction = null, ?string $reason = null) use ($decisionRows): ?array {
        foreach ($decisionRows as $row) {
            $inputs = dashboard_decode_inputs_json((string) ($row['inputs_json'] ?? '{}'));
            if ((string) ($row['venue'] ?? '') !== $venue) {
                continue;
            }
            if (strtolower((string) ($row['action'] ?? '')) !== strtolower($action)) {
                continue;
            }
            if ($direction !== null && strtoupper((string) ($inputs['signal_direction'] ?? '')) !== $direction) {
                continue;
            }
            if ($reason !== null && !str_contains((string) ($row['reason'] ?? ''), $reason)) {
                continue;
            }
            return $row;
        }
        return null;
    };

    $futuresLong = $find('binance_futures', 'execute', 'LONG');
    $futuresShort = $find('binance_futures', 'execute', 'SHORT');
    $spotLong = $find('binance_spot', 'execute', 'LONG');
    $spotShortReject = $find('binance_spot', 'reject', 'SHORT', 'spot_short_not_supported');
    $latestTs = '';
    foreach ($decisionRows as $row) {
        $candidate = (string) ($row['occurred_at'] ?? '');
        if ($candidate !== '' && $candidate > $latestTs) {
            $latestTs = $candidate;
        }
    }

    $allChecksPassed = !empty($futuresLong) && !empty($futuresShort) && !empty($spotLong) && !empty($spotShortReject);
    if (empty($decisionRows)) {
        $reason = 'no_runtime_decisions';
    } elseif ($allChecksPassed) {
        $reason = 'runtime_all_paths_observed';
    } else {
        $reason = 'runtime_paths_incomplete';
    }

    return [
        'last_runtime_at' => $latestTs,
        'all_checks_passed' => $allChecksPassed,
        'runtime_futures_long_execute' => !empty($futuresLong),
        'runtime_futures_long_execute_id' => (int) ($futuresLong['id'] ?? 0),
        'runtime_futures_short_execute' => !empty($futuresShort),
        'runtime_futures_short_execute_id' => (int) ($futuresShort['id'] ?? 0),
        'runtime_spot_long_execute' => !empty($spotLong),
        'runtime_spot_long_execute_id' => (int) ($spotLong['id'] ?? 0),
        'runtime_spot_short_reject' => !empty($spotShortReject),
        'runtime_spot_short_reject_id' => (int) ($spotShortReject['id'] ?? 0),
        'runtime_decision_rows' => count($decisionRows),
        'runtime_open_positions' => count($positionRows),
        'reason' => $reason,
    ];
}

function dashboard_operator_short_address(string $address): string
{
    $address = trim($address);
    if ($address === '' || strlen($address) <= 14) {
        return $address;
    }
    return substr($address, 0, 6) . '...' . substr($address, -4);
}

function dashboard_operator_service_status(array $payload): array
{
    $service = is_array($payload['service'] ?? null) ? $payload['service'] : [];
    $warnings = is_array($payload['warnings'] ?? null) ? $payload['warnings'] : [];
    $active = !empty($service['active']);
    $warningCount = count($warnings);

    if (!$active) {
        $state = 'STOPPED';
        $tone = 'danger';
    } elseif ($warningCount > 0) {
        $state = 'DEGRADED';
        $tone = 'warn';
    } else {
        $state = 'RUNNING';
        $tone = 'ok';
    }

    return [
        'label' => $state,
        'tone' => $tone,
        'service_name' => (string) ($service['name'] ?? 'ghost-trader'),
        'status_text' => (string) ($service['status_text'] ?? ''),
        'warning_count' => $warningCount,
        'last_sync_at' => (string) ($payload['generated_at'] ?? ''),
    ];
}

function dashboard_operator_find_venue_account(array $payload, string $venue): array
{
    $rows = is_array($payload['venue_accounts'] ?? null) ? $payload['venue_accounts'] : [];
    foreach ($rows as $row) {
        if ((string) ($row['venue'] ?? '') !== $venue) {
            continue;
        }
        return [
            'venue' => $venue,
            'cash_balance' => round((float) ($row['cash_balance'] ?? 0.0), 4),
            'equity' => round((float) ($row['equity'] ?? 0.0), 4),
            'available_balance' => round((float) ($row['available_balance'] ?? 0.0), 4),
            'updated_at' => (string) ($row['updated_at'] ?? ''),
        ];
    }

    return [
        'venue' => $venue,
        'cash_balance' => 0.0,
        'equity' => 0.0,
        'available_balance' => 0.0,
        'updated_at' => '',
    ];
}

function dashboard_operator_sum_recent_rows(array $rows, string $timestampKey, string $valueKey, int $hours): float
{
    $cutoff = (new DateTimeImmutable('now', new DateTimeZone('UTC')))->sub(new DateInterval(sprintf('PT%dH', max(1, $hours))));
    $total = 0.0;
    foreach ($rows as $row) {
        $occurredAt = dashboard_parse_utc_datetime((string) ($row[$timestampKey] ?? ''));
        if ($occurredAt === null || $occurredAt < $cutoff) {
            continue;
        }
        $total += (float) ($row[$valueKey] ?? 0.0);
    }
    return round($total, 4);
}

function dashboard_operator_timeseries(array $rows, string $timestampKey, string $valueKey, int $days = 7): array
{
    $days = max(1, $days);
    $buckets = [];
    $now = new DateTimeImmutable('now', new DateTimeZone('UTC'));
    for ($offset = $days - 1; $offset >= 0; $offset--) {
        $date = $now->sub(new DateInterval(sprintf('P%dD', $offset)));
        $key = $date->format('Y-m-d');
        $buckets[$key] = [
            'label' => $date->format('d M'),
            'value' => 0.0,
        ];
    }

    foreach ($rows as $row) {
        $occurredAt = dashboard_parse_utc_datetime((string) ($row[$timestampKey] ?? ''));
        if ($occurredAt === null) {
            continue;
        }
        $key = $occurredAt->format('Y-m-d');
        if (!array_key_exists($key, $buckets)) {
            continue;
        }
        if ($valueKey === '__count') {
            $buckets[$key]['value'] += 1.0;
            continue;
        }
        $buckets[$key]['value'] += (float) ($row[$valueKey] ?? 0.0);
    }

    return array_values(
        array_map(
            static fn (array $bucket): array => [
                'label' => $bucket['label'],
                'value' => round((float) ($bucket['value'] ?? 0.0), 4),
            ],
            $buckets
        )
    );
}

function dashboard_operator_wallet_tier(array $row): string
{
    $score = max(
        (float) ($row['long_horizon_score'] ?? 0.0),
        (float) ($row['consistency_score'] ?? 0.0),
        (float) ($row['discovery_score'] ?? 0.0)
    );
    if ($score >= 0.8) {
        return 'A';
    }
    if ($score >= 0.6) {
        return 'B';
    }
    return 'C';
}

function dashboard_operator_copy_status(array $row): array
{
    $shadowStatus = (string) ($row['shadow_gate_status'] ?? '');
    $pilotStatus = (string) ($row['pilot_copy_gate_status'] ?? '');
    $copyReady = !empty($row['copy_ready_eligible']) || (string) ($row['cohort'] ?? '') === 'copy_ready';
    $linked = strtolower((string) ($row['identity_resolution_status'] ?? $row['watchlist_status'] ?? '')) === 'linked';

    if ($copyReady || $pilotStatus === 'promoted' || $pilotStatus === 'shadow_proven' || $shadowStatus === 'promoted') {
        return ['label' => 'takipte', 'tone' => 'ok'];
    }
    if ($linked || (int) ($row['watchlist_priority_rank'] ?? 0) > 0) {
        return ['label' => 'probation', 'tone' => 'warn'];
    }
    return ['label' => 'pasif', 'tone' => 'info'];
}

function dashboard_operator_human_reason(string $reason): string
{
    $reason = trim($reason);
    if ($reason === '') {
        return 'Net bir engel yok';
    }

    $map = [
        'duplicate_market_exposure' => 'Ayni markette acik pozisyon zaten var',
        'watch_only_needs_shadow_or_pilot_proof' => 'Cuzdan izleniyor ama henuz copy proof yeterli degil',
        'no_eligible_copy_wallets' => 'Copy icin uygun cüzdan havuzu henuz bos',
        'runtime_copy_active' => 'Gercek paper copy akisinda islem gozleniyor',
        'spot_short_not_supported' => 'Spot short desteklenmiyor',
        'technical_alignment_weak' => 'Teknik hizalanma zayif',
        'score_below_threshold' => 'Skor esigin altinda kaldi',
        'max_open_positions_exceeded' => 'Ayni anda acik pozisyon limiti dolu',
        'duplicate_open_trade' => 'Ayni sembolde acik islem oldugu icin engellendi',
        'shadow_replay_seed' => 'Shadow replay seed ile takip kaniti olustu',
        'copy_entry' => 'Kaynak islem copy kapisini gecti',
    ];

    return $map[$reason] ?? str_replace('_', ' ', $reason);
}

function dashboard_build_binance_fresh_pnl_summary(PDO $pdo): array
{
    $tradeOpenedAtExpr = dashboard_table_has_column($pdo, 'trades', 'opened_at')
        ? "opened_at"
        : "timestamp";

    $rows = dashboard_fetch_all(
        $pdo,
        "
        SELECT venue, status, pnl
        FROM trades
        WHERE strategy_profile = 'binance_technical_sampling'
          AND sample_kind = 'live_paper'
          AND venue IN ('binance_futures', 'binance_spot')
          AND COALESCE({$tradeOpenedAtExpr}, timestamp) >= datetime('now', '-7 days')
        "
    );

    $closedRows = array_values(array_filter($rows, static fn (array $row): bool => str_starts_with(strtoupper((string) ($row['status'] ?? '')), 'CLOSED')));
    $wins = count(array_filter($closedRows, static fn (array $row): bool => (float) ($row['pnl'] ?? 0.0) > 0.0));
    $netPnl = round(array_sum(array_map(static fn (array $row): float => (float) ($row['pnl'] ?? 0.0), $closedRows)), 4);
    $executeCountByVenue = [
        'binance_futures' => 0,
        'binance_spot' => 0,
    ];

    if (dashboard_table_exists($pdo, 'decision_audit')) {
        $executeRows = dashboard_fetch_all(
            $pdo,
            "
            SELECT venue, COUNT(*) AS count
            FROM decision_audit
            WHERE strategy_profile = 'binance_technical_sampling'
              AND action = 'execute'
              AND COALESCE(inputs_json, '') NOT LIKE '%acceptance_fixture%'
              AND venue IN ('binance_futures', 'binance_spot')
              AND occurred_at >= datetime('now', '-7 days')
            GROUP BY venue
            "
        );
        foreach ($executeRows as $row) {
            $venue = (string) ($row['venue'] ?? '');
            if (array_key_exists($venue, $executeCountByVenue)) {
                $executeCountByVenue[$venue] = (int) ($row['count'] ?? 0);
            }
        }
    }

    $venueSummaries = [];
    foreach (['binance_futures', 'binance_spot'] as $venue) {
        $venueRows = array_values(array_filter($rows, static fn (array $row): bool => (string) ($row['venue'] ?? '') === $venue));
        $venueClosedRows = array_values(array_filter($closedRows, static fn (array $row): bool => (string) ($row['venue'] ?? '') === $venue));
        $venueWins = count(array_filter($venueClosedRows, static fn (array $row): bool => (float) ($row['pnl'] ?? 0.0) > 0.0));
        $venueSummaries[$venue] = [
            'fresh_trade_count' => count($venueRows),
            'fresh_closed_trades' => count($venueClosedRows),
            'net_pnl' => round(array_sum(array_map(static fn (array $row): float => (float) ($row['pnl'] ?? 0.0), $venueClosedRows)), 4),
            'gross_wins' => round(array_sum(array_map(static fn (array $row): float => max((float) ($row['pnl'] ?? 0.0), 0.0), $venueClosedRows)), 4),
            'gross_losses' => round(array_sum(array_map(static fn (array $row): float => min((float) ($row['pnl'] ?? 0.0), 0.0), $venueClosedRows)), 4),
            'win_rate' => count($venueClosedRows) > 0 ? round(($venueWins / count($venueClosedRows)) * 100.0, 1) : null,
            'execute_count' => $executeCountByVenue[$venue],
        ];
    }

    return [
        'fresh_window_days' => 7,
        'fresh_trade_count' => count($rows),
        'fresh_closed_trades' => count($closedRows),
        'net_pnl' => $netPnl,
        'gross_wins' => round(array_sum(array_map(static fn (array $row): float => max((float) ($row['pnl'] ?? 0.0), 0.0), $closedRows)), 4),
        'gross_losses' => round(array_sum(array_map(static fn (array $row): float => min((float) ($row['pnl'] ?? 0.0), 0.0), $closedRows)), 4),
        'win_rate' => count($closedRows) > 0 ? round(($wins / count($closedRows)) * 100.0, 1) : null,
        'venues' => $venueSummaries,
    ];
}

function dashboard_build_polymarket_operator_summary(array $payload): array
{
    $copyExecution = is_array($payload['copy_execution_summary'] ?? null) ? $payload['copy_execution_summary'] : [];
    $copyDrift = is_array($payload['shadow_vs_copy_drift_summary'] ?? null) ? $payload['shadow_vs_copy_drift_summary'] : [];
    $copyRuntime = is_array($payload['copy_runtime_acceptance_summary'] ?? null) ? $payload['copy_runtime_acceptance_summary'] : [];
    $copyAcceptance = is_array($payload['copy_acceptance_summary'] ?? null) ? $payload['copy_acceptance_summary'] : [];
    $priorityRows = is_array($payload['priority_watchlist_rows'] ?? null) ? $payload['priority_watchlist_rows'] : [];
    $activePositions = is_array($payload['active_copy_positions'] ?? null) ? $payload['active_copy_positions'] : [];
    $recentActions = is_array($payload['recent_copy_actions'] ?? null) ? $payload['recent_copy_actions'] : [];
    $walletPnlRows = is_array($payload['wallet_follower_pnl_summary'] ?? null) ? $payload['wallet_follower_pnl_summary'] : [];
    $walletRows = is_array($payload['wallet_consistency_table'] ?? null) ? $payload['wallet_consistency_table'] : [];
    $recentShadowActions = is_array($payload['recent_shadow_actions'] ?? null) ? $payload['recent_shadow_actions'] : [];
    $trustedWhales = is_array($payload['trusted_whale_summary'] ?? null) ? $payload['trusted_whale_summary'] : [];
    $longHorizon = is_array($payload['long_horizon_watchlist_summary'] ?? null) ? $payload['long_horizon_watchlist_summary'] : [];
    $serviceStatus = dashboard_operator_service_status($payload);
    $polymarketAccount = dashboard_operator_find_venue_account($payload, 'polymarket');

    $mainWallet = [];
    foreach ($priorityRows as $row) {
        if ((int) ($row['priority_rank'] ?? 0) === 1) {
            $mainWallet = $row;
            break;
        }
    }
    if (empty($mainWallet) && !empty($priorityRows)) {
        $mainWallet = $priorityRows[0];
    }

    $closedActions = array_values(array_filter(
        $recentActions,
        static fn (array $row): bool => in_array((string) ($row['action_type'] ?? ''), ['close', 'replay_closed'], true)
    ));
    $wins = count(array_filter($closedActions, static fn (array $row): bool => (float) ($row['follower_pnl'] ?? 0.0) > 0.0));
    $winRate = count($closedActions) > 0 ? round(($wins / count($closedActions)) * 100.0, 1) : null;
    $followerPnl = array_sum(array_map(static fn (array $row): float => (float) ($row['follower_realized_pnl'] ?? 0.0), $walletPnlRows));
    $todayFollowerPnl = dashboard_operator_sum_recent_rows($closedActions, 'executed_at', 'follower_pnl', 24);
    $sevenDayFollowerPnl = dashboard_operator_sum_recent_rows($closedActions, 'executed_at', 'follower_pnl', 24 * 7);
    $sevenDayOpenedCapital = array_sum(array_map(static fn (array $row): float => (float) ($row['opened_notional_usd'] ?? 0.0), $walletPnlRows));
    $sevenDayRoi = $sevenDayOpenedCapital > 0.0 ? round(($sevenDayFollowerPnl / $sevenDayOpenedCapital) * 100.0, 2) : null;

    $trustedWhaleByAddress = [];
    foreach ($trustedWhales as $row) {
        $trustedWhaleByAddress[strtolower((string) ($row['address'] ?? ''))] = $row;
    }

    $recentShadowByAddress = [];
    foreach ($recentShadowActions as $row) {
        $address = strtolower((string) ($row['wallet_address'] ?? ''));
        if ($address === '' || isset($recentShadowByAddress[$address])) {
            continue;
        }
        $recentShadowByAddress[$address] = $row;
    }

    $bestClosedAction = $closedActions === []
        ? null
        : array_reduce(
            $closedActions,
            static function (?array $carry, array $row): array {
                if ($carry === null || (float) ($row['follower_pnl'] ?? 0.0) > (float) ($carry['follower_pnl'] ?? 0.0)) {
                    return $row;
                }
                return $carry;
            }
        );
    $worstClosedAction = $closedActions === []
        ? null
        : array_reduce(
            $closedActions,
            static function (?array $carry, array $row): array {
                if ($carry === null || (float) ($row['follower_pnl'] ?? 0.0) < (float) ($carry['follower_pnl'] ?? 0.0)) {
                    return $row;
                }
                return $carry;
            }
        );

    $trackedWallets = [];
    foreach (array_slice($walletRows, 0, 8) as $row) {
        $address = strtolower((string) ($row['address'] ?? $row['wallet_address'] ?? ''));
        $shortAddress = dashboard_operator_short_address((string) ($row['address'] ?? $row['wallet_address'] ?? ''));
        $tier = dashboard_operator_wallet_tier($row);
        $finalScore = round(max(
            (float) ($row['long_horizon_score'] ?? 0.0),
            (float) ($row['consistency_score'] ?? 0.0),
            (float) ($row['discovery_score'] ?? 0.0)
        ) * 100.0, 1);
        $followabilityScore = round(min(1.0, max(0.0,
            ((float) ($row['trust_score'] ?? 0.5) * 0.55)
            + ((float) ($row['consistency_score'] ?? 0.0) * 0.25)
            + ((float) ($row['crypto_participation_ratio'] ?? 0.0) * 0.20)
        )) * 100.0, 1);
        $winRateValue = $trustedWhaleByAddress[$address]['win_rate'] ?? null;
        $copyStatus = dashboard_operator_copy_status($row);
        $lastShadow = $recentShadowByAddress[$address] ?? [];
        $recentWalletClosed = array_values(array_filter(
            $closedActions,
            static fn (array $action): bool => strtolower((string) ($action['wallet_address'] ?? '')) === $address
        ));

        $reliableReasons = [];
        if ((int) ($row['historical_trade_rows'] ?? 0) > 0) {
            $reliableReasons[] = 'Gecmis islem kaniti mevcut';
        }
        if ((float) ($row['crypto_participation_ratio'] ?? 0.0) >= 0.60) {
            $reliableReasons[] = 'Crypto market uzmanligi yuksek';
        }
        if ((float) ($row['long_horizon_score'] ?? 0.0) >= 0.70) {
            $reliableReasons[] = 'Uzun vade tutarlilik skoru guclu';
        }

        $riskReasons = [];
        if ((string) ($row['historical_trade_evidence_status'] ?? '') === 'no_historical_evidence') {
            $riskReasons[] = 'Gecmis kanit henuz zayif';
        }
        if ((float) ($row['drawdown_estimate_pct'] ?? 0.0) < -10.0 || (float) ($row['worst_drawdown_pct'] ?? 0.0) < -10.0) {
            $riskReasons[] = 'Drawdown dikkati gerekiyor';
        }
        if ((string) ($row['pilot_copy_gate_reason'] ?? '') !== '' && (string) ($row['pilot_copy_gate_status'] ?? '') !== 'promoted') {
            $riskReasons[] = dashboard_operator_human_reason((string) ($row['pilot_copy_gate_reason'] ?? ''));
        }

        $trackedWallets[] = [
            'address' => (string) ($row['address'] ?? $row['wallet_address'] ?? ''),
            'address_short' => $shortAddress,
            'wallet_label' => (string) ($row['display_name'] ?? $shortAddress),
            'tier' => $tier,
            'final_score' => $finalScore,
            'followability_score' => $followabilityScore,
            'pnl_7d' => round((float) ($row['shadow_pnl'] ?? 0.0), 4),
            'pnl_30d' => round((float) ($row['realized_pnl'] ?? 0.0), 4),
            'win_rate' => $winRateValue === null ? null : round((float) $winRateValue, 1),
            'last_trade_at' => (string) ($row['evidence_last_trade_at'] ?? $row['last_seen_at'] ?? ''),
            'last_action' => (string) ($lastShadow['action_type'] ?? $row['long_horizon_status'] ?? 'watch_only'),
            'copy_status' => $copyStatus['label'],
            'copy_status_tone' => $copyStatus['tone'],
            'detail' => [
                'score_breakdown' => [
                    'long_horizon_score' => round((float) ($row['long_horizon_score'] ?? 0.0), 4),
                    'consistency_score' => round((float) ($row['consistency_score'] ?? 0.0), 4),
                    'trust_score' => round((float) ($row['trust_score'] ?? 0.0), 4),
                    'profit_consistency_score' => round((float) ($row['profit_consistency_score'] ?? 0.0), 4),
                ],
                'specialization' => (string) ($row['specialization'] ?? 'UNKNOWN'),
                'crypto_participation_ratio' => round((float) ($row['crypto_participation_ratio'] ?? 0.0) * 100.0, 1),
                'stability' => [
                    'active_days' => (int) ($row['active_days'] ?? 0),
                    'closed_trade_count' => (int) ($row['closed_trade_count'] ?? 0),
                    'observation_days' => (int) ($row['observation_days'] ?? 0),
                    'observed_action_count' => (int) ($row['observed_action_count'] ?? 0),
                    'worst_drawdown_pct' => round((float) ($row['worst_drawdown_pct'] ?? 0.0), 4),
                ],
                'gate_state' => [
                    'shadow_gate_status' => (string) ($row['shadow_gate_status'] ?? 'blocked'),
                    'shadow_gate_reason' => (string) ($row['shadow_gate_reason'] ?? ''),
                    'pilot_copy_gate_status' => (string) ($row['pilot_copy_gate_status'] ?? 'blocked'),
                    'pilot_copy_gate_reason' => (string) ($row['pilot_copy_gate_reason'] ?? ''),
                    'copy_ready_gate_status' => (string) ($row['copy_ready_gate_status'] ?? 'blocked'),
                    'copy_ready_gate_reason' => (string) ($row['copy_ready_gate_reason'] ?? ''),
                    'historical_trade_evidence_status' => (string) ($row['historical_trade_evidence_status'] ?? 'no_historical_evidence'),
                ],
                'recent_closed_positions' => array_slice($recentWalletClosed, 0, 3),
                'reliable_reasons' => $reliableReasons,
                'risk_reasons' => $riskReasons,
            ],
        ];
    }

    $recentWhaleActions = array_map(
        static function (array $row): array {
            $actionType = (string) ($row['action_type'] ?? '');
            return [
                'timestamp' => (string) ($row['executed_at'] ?? ''),
                'wallet_address' => (string) ($row['wallet_address'] ?? ''),
                'wallet_short' => dashboard_operator_short_address((string) ($row['wallet_address'] ?? '')),
                'market' => (string) ($row['market_id'] ?? ''),
                'action' => strtoupper((string) ($row['side'] ?? $actionType)),
                'entry_price' => null,
                'size' => round((float) ($row['source_notional_usd'] ?? 0.0), 4),
                'confidence' => null,
                'bot_decision' => $actionType === 'reject' ? 'rejected' : 'copied',
                'reject_reason' => $actionType === 'reject' ? dashboard_operator_human_reason((string) ($row['reason'] ?? '')) : '',
                'reason' => dashboard_operator_human_reason((string) ($row['reason'] ?? '')),
            ];
        },
        array_slice($recentActions, 0, 8)
    );

    $openCopyPositions = array_map(
        static function (array $row): array {
            return [
                'market' => (string) ($row['market_id'] ?? ''),
                'side' => (string) ($row['side'] ?? ''),
                'source_wallet' => dashboard_operator_short_address((string) ($row['wallet_address'] ?? '')),
                'entry' => (string) ($row['source_opened_at'] ?? ''),
                'current' => (string) ($row['status'] ?? 'OPEN'),
                'size' => round((float) ($row['follower_notional_usd'] ?? 0.0), 4),
                'notional' => round((float) ($row['source_notional_usd'] ?? 0.0), 4),
                'pnl' => 0.0,
                'opened_at' => (string) ($row['opened_at'] ?? ''),
                'age' => (string) ($row['opened_at'] ?? ''),
                'status' => (string) ($row['status'] ?? 'OPEN'),
                'cohort_source' => (string) ($row['cohort_source'] ?? 'shadow_proven'),
            ];
        },
        array_slice($activePositions, 0, 6)
    );

    $recentClosedCopyPositions = array_map(
        static function (array $row): array {
            return [
                'market' => (string) ($row['market_id'] ?? ''),
                'source_wallet' => dashboard_operator_short_address((string) ($row['wallet_address'] ?? '')),
                'status' => (string) ($row['source_status'] ?? ''),
                'follower_pnl' => round((float) ($row['follower_pnl'] ?? 0.0), 4),
                'source_pnl' => round((float) ($row['source_pnl'] ?? 0.0), 4),
                'action_type' => (string) ($row['action_type'] ?? ''),
                'closed_at' => (string) ($row['executed_at'] ?? ''),
            ];
        },
        array_slice($closedActions, 0, 6)
    );

    $decisionRows = array_map(
        static function (array $row) use ($trackedWallets): array {
            $wallet = strtolower((string) ($row['wallet_address'] ?? ''));
            $matched = null;
            foreach ($trackedWallets as $walletRow) {
                if (strtolower((string) ($walletRow['address'] ?? '')) === $wallet) {
                    $matched = $walletRow;
                    break;
                }
            }
            $reason = (string) ($row['reason'] ?? '');
            $actionType = (string) ($row['action_type'] ?? '');
            return [
                'timestamp' => (string) ($row['executed_at'] ?? ''),
                'source_wallet' => dashboard_operator_short_address((string) ($row['wallet_address'] ?? '')),
                'score' => (float) ($matched['final_score'] ?? 0.0),
                'threshold' => 75.0,
                'liquidity_check' => !str_contains($reason, 'liquidity') ? 'geçti' : 'takildi',
                'stale_check' => !str_contains($reason, 'stale') ? 'geçti' : 'takildi',
                'concentration_check' => !str_contains($reason, 'duplicate') ? 'geçti' : 'takildi',
                'final_verdict' => $actionType === 'reject' ? 'rejected' : ($actionType === 'open' ? 'copied' : 'closed'),
                'reason_summary' => dashboard_operator_human_reason($reason),
            ];
        },
        array_slice($recentActions, 0, 8)
    );

    $watchlistSegments = [
        ['key' => 'core', 'label' => 'Core wallets', 'count' => (int) (($longHorizon['copy_ready'] ?? 0) + ($longHorizon['shadow_tracking'] ?? 0)), 'tone' => 'ok'],
        ['key' => 'emerging', 'label' => 'Emerging wallets', 'count' => (int) ($longHorizon['observing'] ?? 0), 'tone' => 'info'],
        ['key' => 'probation', 'label' => 'Probation wallets', 'count' => (int) (($longHorizon['linked'] ?? 0) + ($longHorizon['priority_watch'] ?? 0)), 'tone' => 'warn'],
        ['key' => 'rising', 'label' => 'Rising wallets', 'count' => (int) ($longHorizon['pilot_copy_ready'] ?? 0), 'tone' => 'ok'],
        ['key' => 'dropped', 'label' => 'Dropped wallets', 'count' => count(array_filter($walletRows, static fn (array $row): bool => (string) ($row['shadow_gate_status'] ?? '') === 'blocked' && (int) ($row['watchlist_priority_rank'] ?? 0) > 0)), 'tone' => 'danger'],
    ];

    $topKpis = [
        'service_status' => $serviceStatus,
        'tracked_whales' => count($trackedWallets),
        'active_copy_positions' => count($activePositions),
        'portfolio_value' => round((float) ($polymarketAccount['equity'] ?? 0.0), 4),
        'daily_pnl' => $todayFollowerPnl,
        'seven_day_pnl' => $sevenDayFollowerPnl,
        'seven_day_roi' => $sevenDayRoi,
        'copy_success_rate' => $winRate,
        'last_sync_at' => (string) ($serviceStatus['last_sync_at'] ?? ''),
    ];

    $copyPortfolioSummary = [
        'allocated_capital' => round((float) ($polymarketAccount['equity'] ?? 0.0), 4),
        'available_balance' => round((float) ($polymarketAccount['available_balance'] ?? 0.0), 4),
        'realized_pnl' => round($followerPnl, 4),
        'unrealized_pnl' => 0.0,
        'opened_today' => count(array_filter($recentActions, static fn (array $row): bool => (string) ($row['action_type'] ?? '') === 'open')),
        'closed_today' => count($closedActions),
        'best_position' => $bestClosedAction,
        'worst_position' => $worstClosedAction,
    ];

    $performanceChartSeries = [
        'equity_trend' => dashboard_operator_timeseries($walletPnlRows, 'last_action_at', 'follower_realized_pnl', 7),
        'daily_pnl_trend' => dashboard_operator_timeseries($closedActions, 'executed_at', 'follower_pnl', 7),
        'copied_trade_count_trend' => dashboard_operator_timeseries($recentActions, 'executed_at', '__count', 7),
    ];

    return [
        'service_status' => $serviceStatus,
        'top_kpis' => $topKpis,
        'tracked_wallets' => $trackedWallets,
        'recent_whale_actions' => $recentWhaleActions,
        'copy_portfolio_summary' => $copyPortfolioSummary,
        'open_copy_positions' => $openCopyPositions,
        'recent_closed_copy_positions' => $recentClosedCopyPositions,
        'decision_explainability_rows' => $decisionRows,
        'watchlist_segments' => $watchlistSegments,
        'performance_chart_series' => $performanceChartSeries,
        'wallet_copy_status' => [
            'watched_wallets' => count($priorityRows),
            'linked_wallets' => count(array_filter($priorityRows, static fn (array $row): bool => strtolower((string) ($row['identity_resolution_status'] ?? $row['status'] ?? '')) === 'linked')),
            'copy_ready_wallets' => (int) ($copyExecution['copy_ready_wallets'] ?? 0),
            'eligible_copy_wallets_total' => (int) ($copyExecution['eligible_copy_wallets_total'] ?? 0),
            'main_wallet_name' => (string) ($mainWallet['display_name'] ?? 'ohanism'),
            'main_wallet_address' => (string) ($mainWallet['wallet_address'] ?? ''),
            'main_wallet_status' => (string) ($mainWallet['long_horizon_status'] ?? $mainWallet['status'] ?? 'priority_watch'),
            'shadow_edge' => round((float) ($copyDrift['shadow_net_edge'] ?? ($payload['shadow_edge_summary']['net_shadow_edge'] ?? 0.0)), 4),
            'copy_blocker_reason' => (string) ($copyExecution['copy_blocker_reason'] ?? 'no_eligible_copy_wallets'),
        ],
        'paper_copy_performance' => [
            'follower_realized_pnl' => round($followerPnl, 4),
            'source_realized_pnl' => round((float) ($copyDrift['source_realized_pnl'] ?? 0.0), 4),
            'open_copy_positions' => count($activePositions),
            'closed_or_replay_actions' => (int) ($copyExecution['replay_closed_actions'] ?? 0) + (int) ($copyExecution['close_actions'] ?? 0),
            'win_rate' => $winRate,
            'win_rate_label' => $winRate === null ? 'veri bekleniyor' : ((string) $winRate . '%'),
            'runtime_copy_active' => (bool) ($copyRuntime['all_checks_passed'] ?? false),
            'test_proof_active' => (bool) ($copyAcceptance['all_checks_passed'] ?? false),
        ],
        'open_paper_trades' => array_slice($activePositions, 0, 6),
        'recent_closed_trades' => array_slice($closedActions, 0, 6),
        'work_proof' => [
            'test_proof_passed' => (bool) ($copyAcceptance['all_checks_passed'] ?? false),
            'runtime_proof_passed' => (bool) ($copyRuntime['all_checks_passed'] ?? false),
            'runtime_open_action_observed' => (bool) ($copyRuntime['runtime_open_action_observed'] ?? false),
            'runtime_open_position_observed' => (bool) ($copyRuntime['runtime_open_position_observed'] ?? false),
            'reason' => (string) ($copyRuntime['reason'] ?? $copyRuntime['runtime_blocker_reason'] ?? 'runtime_copy_active'),
            'fixture_note' => 'Test kaniti ana performansa dahil edilmez.',
        ],
    ];
}

function dashboard_build_binance_operator_summary(array $payload): array
{
    $freshPnl = is_array($payload['fresh_pnl_summary_7d'] ?? null) ? $payload['fresh_pnl_summary_7d'] : [];
    $venues = is_array($freshPnl['venues'] ?? null) ? $freshPnl['venues'] : [];
    $futuresPnl = is_array($venues['binance_futures'] ?? null) ? $venues['binance_futures'] : [];
    $spotPnl = is_array($venues['binance_spot'] ?? null) ? $venues['binance_spot'] : [];
    $positionPressure = is_array($payload['position_pressure_summary'] ?? null) ? $payload['position_pressure_summary'] : [];
    $acceptance = is_array($payload['technical_acceptance_summary'] ?? null) ? $payload['technical_acceptance_summary'] : [];
    $runtime = is_array($payload['technical_runtime_acceptance_summary'] ?? null) ? $payload['technical_runtime_acceptance_summary'] : [];
    $openPositions = is_array($payload['open_positions'] ?? null) ? $payload['open_positions'] : [];
    $openOrders = is_array($payload['open_orders'] ?? null) ? $payload['open_orders'] : [];
    $recentTrades = is_array($payload['recent_trades'] ?? null) ? $payload['recent_trades'] : [];
    $recentDecisions = is_array($payload['recent_decisions'] ?? null) ? $payload['recent_decisions'] : [];
    $serviceStatus = dashboard_operator_service_status($payload);
    $futuresAccount = dashboard_operator_find_venue_account($payload, 'binance_futures');
    $spotAccount = dashboard_operator_find_venue_account($payload, 'binance_spot');
    $scoreComponentSummary = is_array($payload['binance_technical_score_component_summary'] ?? null) ? $payload['binance_technical_score_component_summary'] : [];
    $gapSummary = is_array($payload['binance_technical_fresh_score_gap_summary'] ?? null) ? $payload['binance_technical_fresh_score_gap_summary'] : [];
    $blockerBreakdown = is_array($payload['binance_technical_score_blocker_breakdown'] ?? null) ? $payload['binance_technical_score_blocker_breakdown'] : [];
    $rejectBreakdown = is_array($payload['binance_technical_fresh_reject_breakdown'] ?? null) ? $payload['binance_technical_fresh_reject_breakdown'] : [];

    $openTechnical = array_values(array_filter(
        $openPositions,
        static fn (array $row): bool => in_array((string) ($row['venue'] ?? ''), ['binance_futures', 'binance_spot'], true)
    ));
    $openTechnicalOrders = array_values(array_filter(
        $openOrders,
        static fn (array $row): bool => in_array((string) ($row['venue'] ?? ''), ['binance_futures', 'binance_spot'], true)
            && strtoupper((string) ($row['status'] ?? '')) === 'OPEN'
    ));
    $closedTechnical = array_values(array_filter(
        $recentTrades,
        static fn (array $row): bool => in_array((string) ($row['venue'] ?? ''), ['binance_futures', 'binance_spot'], true)
            && str_starts_with(strtoupper((string) ($row['status'] ?? '')), 'CLOSED')
            && (string) ($row['sample_kind'] ?? '') !== 'acceptance_fixture'
    ));
    $runtimeDecisionFeed = array_values(array_filter(
        $recentDecisions,
        static fn (array $row): bool => in_array((string) ($row['venue'] ?? ''), ['binance_futures', 'binance_spot'], true)
    ));
    $wins = count(array_filter($closedTechnical, static fn (array $row): bool => (float) ($row['pnl'] ?? 0.0) > 0.0));
    $winRate = count($closedTechnical) > 0 ? round(($wins / count($closedTechnical)) * 100.0, 1) : null;
    $dailyPnl = dashboard_operator_sum_recent_rows($closedTechnical, 'timestamp', 'pnl', 24);
    $totalEquity = round((float) ($futuresAccount['equity'] ?? 0.0) + (float) ($spotAccount['equity'] ?? 0.0), 4);
    $currentExposurePct = $totalEquity > 0.0
        ? round((((float) ($positionPressure['open_notional_usd'] ?? 0.0)) / $totalEquity) * 100.0, 2)
        : 0.0;
    $positionBySymbol = [];
    foreach ($openTechnical as $row) {
        $symbol = (string) ($row['symbol_or_market_id'] ?? $row['market_id'] ?? '');
        $positionBySymbol[$symbol] = ($positionBySymbol[$symbol] ?? 0.0) + abs((float) ($row['notional_usd'] ?? 0.0));
    }
    arsort($positionBySymbol);
    $topSymbol = array_key_first($positionBySymbol);
    $topSymbolNotional = $topSymbol !== null ? (float) ($positionBySymbol[$topSymbol] ?? 0.0) : 0.0;
    $symbolConcentrationPct = ((float) ($positionPressure['open_notional_usd'] ?? 0.0)) > 0.0
        ? round(($topSymbolNotional / (float) ($positionPressure['open_notional_usd'] ?? 1.0)) * 100.0, 2)
        : 0.0;
    $lossCount = count($closedTechnical) - $wins;
    $avgWinner = $wins > 0 ? round(array_sum(array_map(static fn (array $row): float => max((float) ($row['pnl'] ?? 0.0), 0.0), $closedTechnical)) / $wins, 4) : null;
    $avgLoser = $lossCount > 0 ? round(array_sum(array_map(static fn (array $row): float => min((float) ($row['pnl'] ?? 0.0), 0.0), $closedTechnical)) / $lossCount, 4) : null;

    $topKpis = [
        'service_status' => $serviceStatus,
        'total_account_value' => $totalEquity,
        'spot_equity' => round((float) ($spotAccount['equity'] ?? 0.0), 4),
        'futures_equity' => round((float) ($futuresAccount['equity'] ?? 0.0), 4),
        'daily_total_pnl' => $dailyPnl,
        'pnl_7d' => round((float) ($freshPnl['net_pnl'] ?? 0.0), 4),
        'open_positions' => count($openTechnical),
        'open_orders' => count($openTechnicalOrders),
        'total_risk_exposure' => $currentExposurePct,
        'last_runtime_at' => (string) ($runtime['last_runtime_at'] ?? $serviceStatus['last_sync_at'] ?? ''),
    ];

    $filterOptions = [
        'symbols' => array_values(array_unique(array_filter(array_map(static fn (array $row): string => (string) ($row['symbol_or_market_id'] ?? $row['market_id'] ?? ''), array_merge($openTechnical, $openTechnicalOrders, $closedTechnical))))),
        'venues' => ['all', 'binance_futures', 'binance_spot'],
        'sides' => ['all', 'long', 'short'],
        'statuses' => ['all', 'open', 'closed', 'rejected'],
    ];

    $strategyStatusSummary = [
        'futures_long' => ['label' => 'Futures LONG', 'allowed' => true],
        'futures_short' => ['label' => 'Futures SHORT', 'allowed' => true],
        'spot_long' => ['label' => 'Spot LONG', 'allowed' => true],
        'spot_short' => ['label' => 'Spot SHORT', 'allowed' => false],
        'score_threshold' => round((float) ($scoreComponentSummary['avg_effective_min_score'] ?? 0.54), 4),
        'acceptance_gate' => !empty($runtime['all_checks_passed']) || !empty($acceptance['all_checks_passed']),
        'runtime_decision_rows' => (int) ($runtime['runtime_decision_rows'] ?? 0),
    ];

    $riskSummary = [
        'max_trade_risk_pct' => 1.5,
        'current_exposure_pct' => $currentExposurePct,
        'symbol_concentration' => [
            'symbol' => $topSymbol ?? '',
            'pct' => $symbolConcentrationPct,
        ],
        'daily_drawdown_pct' => $totalEquity > 0.0 && $dailyPnl < 0.0 ? round((abs($dailyPnl) / $totalEquity) * 100.0, 2) : 0.0,
        'kill_switch_status' => 'KAPALI',
        'blocked_reasons' => array_slice($rejectBreakdown, 0, 3),
        'rejected_trades_today' => count(array_filter(
            $runtimeDecisionFeed,
            static fn (array $row): bool => (string) ($row['action'] ?? '') === 'reject'
        )),
    ];

    $decisionFeed = array_map(
        static function (array $row): array {
            return [
                'timestamp' => (string) ($row['occurred_at'] ?? ''),
                'symbol' => (string) ($row['market_id'] ?? ''),
                'venue' => (string) ($row['venue'] ?? ''),
                'score' => round((float) ($row['decision_score'] ?? 0.0), 4),
                'threshold' => round((float) ($row['threshold'] ?? 0.0), 4),
                'action' => (string) ($row['action'] ?? ''),
                'reason' => dashboard_operator_human_reason((string) ($row['reason'] ?? '')),
                'final_verdict' => (string) ($row['action'] ?? ''),
            ];
        },
        array_slice($runtimeDecisionFeed, 0, 10)
    );

    $freshPnlSummary = [
        'today' => $dailyPnl,
        'seven_days' => round((float) ($freshPnl['net_pnl'] ?? 0.0), 4),
        'futures_pnl' => round((float) ($futuresPnl['net_pnl'] ?? 0.0), 4),
        'spot_pnl' => round((float) ($spotPnl['net_pnl'] ?? 0.0), 4),
        'win_rate' => $winRate,
        'closed_trade_count' => count($closedTechnical),
        'average_winner' => $avgWinner,
        'average_loser' => $avgLoser,
    ];

    $technicalQualitySummary = [
        'avg_score' => round((float) ($scoreComponentSummary['avg_final_score'] ?? 0.0), 4),
        'avg_threshold' => round((float) ($scoreComponentSummary['avg_effective_min_score'] ?? 0.54), 4),
        'avg_rsi' => round((float) ($scoreComponentSummary['avg_rsi_component'] ?? 0.0), 4),
        'avg_macd' => round((float) ($scoreComponentSummary['avg_macd_component'] ?? 0.0), 4),
        'avg_momentum' => round((float) ($scoreComponentSummary['avg_momentum_component'] ?? 0.0), 4),
        'avg_spread' => 0.0,
        'near_threshold_count' => (int) ($gapSummary['near_threshold_count'] ?? 0),
        'blocker_distribution' => $blockerBreakdown,
    ];

    $performanceChartSeries = [
        'pnl_trend' => dashboard_operator_timeseries($closedTechnical, 'timestamp', 'pnl', 7),
        'trade_count_trend' => dashboard_operator_timeseries($closedTechnical, 'timestamp', '__count', 7),
        'equity_breakdown' => [
            ['label' => 'Spot', 'value' => round((float) ($spotAccount['equity'] ?? 0.0), 4)],
            ['label' => 'Futures', 'value' => round((float) ($futuresAccount['equity'] ?? 0.0), 4)],
            ['label' => 'Risk', 'value' => round((float) ($positionPressure['open_notional_usd'] ?? 0.0), 4)],
        ],
    ];

    return [
        'service_status' => $serviceStatus,
        'top_kpis' => $topKpis,
        'filter_options' => $filterOptions,
        'strategy_status_summary' => $strategyStatusSummary,
        'open_positions' => array_slice($openTechnical, 0, 8),
        'open_orders' => array_slice($openTechnicalOrders, 0, 8),
        'fresh_pnl_summary' => $freshPnlSummary,
        'runtime_decision_feed' => $decisionFeed,
        'risk_summary' => $riskSummary,
        'recent_closed_trades' => array_slice($closedTechnical, 0, 8),
        'technical_quality_summary' => $technicalQualitySummary,
        'performance_chart_series' => $performanceChartSeries,
        'paper_balance_pnl' => [
            'fresh_window_days' => (int) ($freshPnl['fresh_window_days'] ?? 7),
            'fresh_7d_pnl' => round((float) ($freshPnl['net_pnl'] ?? 0.0), 4),
            'futures_pnl' => round((float) ($futuresPnl['net_pnl'] ?? 0.0), 4),
            'spot_pnl' => round((float) ($spotPnl['net_pnl'] ?? 0.0), 4),
            'open_notional_usd' => round((float) ($positionPressure['open_notional_usd'] ?? 0.0), 4),
            'futures_execute_count' => (int) ($futuresPnl['execute_count'] ?? 0),
            'spot_execute_count' => (int) ($spotPnl['execute_count'] ?? 0),
        ],
        'open_trades' => array_slice($openTechnical, 0, 6),
        'closed_trades' => array_slice($closedTechnical, 0, 6),
        'closed_trade_summary' => [
            'closed_trades' => count($closedTechnical),
            'wins' => $wins,
            'losses' => count($closedTechnical) - $wins,
            'realized_pnl' => round(array_sum(array_map(static fn (array $row): float => (float) ($row['pnl'] ?? 0.0), $closedTechnical)), 4),
            'win_rate' => $winRate,
            'win_rate_label' => $winRate === null ? 'veri bekleniyor' : ((string) $winRate . '%'),
        ],
        'work_proof' => [
            'test_proof_passed' => (bool) ($acceptance['all_checks_passed'] ?? false),
            'runtime_proof_passed' => (bool) ($runtime['all_checks_passed'] ?? false),
            'futures_long_execute' => (bool) (($runtime['runtime_futures_long_execute'] ?? false) || ($acceptance['futures_long_execute'] ?? false)),
            'futures_short_execute' => (bool) (($runtime['runtime_futures_short_execute'] ?? false) || ($acceptance['futures_short_execute'] ?? false)),
            'spot_long_execute' => (bool) (($runtime['runtime_spot_long_execute'] ?? false) || ($acceptance['spot_long_execute'] ?? false)),
            'spot_short_reject' => (bool) (($runtime['runtime_spot_short_reject'] ?? false) || ($acceptance['spot_short_reject'] ?? false)),
            'reason' => (string) ($runtime['reason'] ?? 'runtime_paths_incomplete'),
            'fixture_note' => 'Test kaniti ana fresh PnL hesabina dahil edilmez.',
        ],
    ];
}

function dashboard_build_operator_landing_summary(array $payload): array
{
    $serviceStatus = dashboard_operator_service_status($payload);
    $polymarket = is_array($payload['polymarket_operator_summary'] ?? null) ? $payload['polymarket_operator_summary'] : [];
    $binance = is_array($payload['binance_operator_summary'] ?? null) ? $payload['binance_operator_summary'] : [];

    return [
        'service_status' => $serviceStatus,
        'all_running' => (string) ($serviceStatus['label'] ?? '') === 'RUNNING',
        'last_sync_at' => (string) ($serviceStatus['last_sync_at'] ?? ''),
        'total_alerts' => (int) ($serviceStatus['warning_count'] ?? 0),
        'lanes' => [
            'polymarket' => [
                'title' => 'Polymarket Copy Trade',
                'description' => 'Takip edilen cüzdanlar, whale akis ve paper copy operasyonu',
                'status' => $polymarket['service_status'] ?? $serviceStatus,
                'today_pnl' => (float) ($polymarket['top_kpis']['daily_pnl'] ?? 0.0),
                'active_positions' => (int) ($polymarket['top_kpis']['active_copy_positions'] ?? 0),
                'tracked_wallets' => (int) ($polymarket['top_kpis']['tracked_whales'] ?? 0),
            ],
            'binance' => [
                'title' => 'Binance Trading Operations',
                'description' => 'Spot + futures paper trading operasyon paneli',
                'status' => $binance['service_status'] ?? $serviceStatus,
                'today_pnl' => (float) ($binance['top_kpis']['daily_total_pnl'] ?? 0.0),
                'active_positions' => (int) ($binance['top_kpis']['open_positions'] ?? 0),
                'open_orders' => (int) ($binance['top_kpis']['open_orders'] ?? 0),
            ],
        ],
    ];
}

function dashboard_build_binance_lane_summary(array $payload): array
{
    $freshSummary = $payload['binance_technical_fresh_summary'] ?? [];
    if (is_array($freshSummary)) {
        $freshSummary['fresh_window_days'] = (int) ($freshSummary['fresh_window_days'] ?? 7);
    } else {
        $freshSummary = [];
    }

    return [
        'fresh_technical_summary' => $freshSummary,
        'fresh_pnl_summary_7d' => $payload['fresh_pnl_summary_7d'] ?? [],
        'technical_acceptance_summary' => $payload['technical_acceptance_summary'] ?? [],
        'technical_runtime_acceptance_summary' => $payload['technical_runtime_acceptance_summary'] ?? [],
        'technical_score_summary' => [
            'component_summary' => $payload['binance_technical_score_component_summary'] ?? [],
            'gap_summary' => $payload['binance_technical_fresh_score_gap_summary'] ?? [],
            'blocker_breakdown' => $payload['binance_technical_score_blocker_breakdown'] ?? [],
        ],
        'technical_reject_breakdown' => $payload['binance_technical_fresh_reject_breakdown'] ?? [],
        'position_pressure_summary' => $payload['binance_technical_position_pressure_summary'] ?? [],
        'legacy_position_summary' => [
            'stale_eligibility' => $payload['binance_technical_stale_eligibility_summary'] ?? [],
            'legacy_shape' => $payload['binance_technical_legacy_position_shape_summary'] ?? [],
            'legacy_open_positions' => $payload['binance_technical_legacy_open_positions'] ?? [],
        ],
    ];
}

function dashboard_build_dashboard_tab_help(): array
{
    return [
        'polymarket-research' => 'Bu sekme istikrarli Polymarket cüzdanlarini once kesfeder, sonra shadow cohort ve copy-ready kisitli listeyi gosterir.',
        'polymarket-copy' => 'Bu sekme sadece copy-ready cohorttan gelen paper copy aksiyonlarini, aktif follower pozisyonlarini ve red nedenlerini gosterir.',
        'binance-technical' => 'Bu sekme bagimsiz Binance teknik paper lane performansini, taze 7 gun PnL ozetini ve kapasite baskisini gosterir.',
        'sozluk-aciklamalar' => 'Bu sekme metriklerin ne anlama geldigini sade Turkce ile aciklar ve servis logunu tek yerde toplar.',
    ];
}

function dashboard_build_dashboard_glossary(): array
{
    return [
        'polymarket-research' => [
            ['term' => 'Discovery pool', 'meaning' => 'Izlenen aday cüzdan havuzu. Burada amac hemen trade acmak degil, once iyi aday toplamak.'],
            ['term' => 'Shadow cohort', 'meaning' => 'Gercek takip yerine gecikmeli taklit simülasyonu yapilan ikinci kademe cüzdan grubu.'],
            ['term' => 'Copy-ready', 'meaning' => 'Shadow takipte artida kalan ve drawdowni kabul edilebilir olan kisa liste.'],
            ['term' => 'Shadow edge', 'meaning' => 'Cüzdanin ham karindan degil, bizim gecikmeli takip simülasyonumuzdan kalan net avantaj.'],
            ['term' => 'Tutarlilik skoru', 'meaning' => 'Aktif gun, kapanmis islem, guven skoru ve realized PnL ile olusan bileşik kalite puani.'],
            ['term' => 'Primary source', 'meaning' => 'Cuzdanin en baskin geldigi kaynak.'],
            ['term' => 'Source labels', 'meaning' => 'Bu cüzdanin hangi listelerde gorundugu.'],
            ['term' => 'Shadow eligible', 'meaning' => 'Gecikmeli takibe alinmaya uygun.'],
            ['term' => 'Seed-only excluded', 'meaning' => 'Sadece tohum listede var, veri kaniti yetmiyor.'],
            ['term' => 'Priority watchlist', 'meaning' => 'Elle oncelik verdigimiz uzman cüzdan listesi.'],
            ['term' => 'Pending resolution', 'meaning' => 'Handle var, adres henuz baglanmadi.'],
            ['term' => 'Manual link', 'meaning' => 'Dogruladigimiz cuzdan adresini watchlist kaydina elle baglama.'],
            ['term' => 'Fast-track shadow', 'meaning' => 'Normal sirayi beklemeden shadow takibe alinacak.'],
            ['term' => 'Shadow replay', 'meaning' => 'Gecmis kapanmis islemlerden gecikmeli takip simulasyonu.'],
            ['term' => 'Historical trade evidence', 'meaning' => 'Bu cuzdan icin gecmis kapanmis islem kaniti var mi.'],
            ['term' => 'Stats only', 'meaning' => 'Detay islem listesi yok; sadece toplu performans ozeti var.'],
            ['term' => 'Shadow seeded', 'meaning' => 'Bu cuzdan icin shadow simulasyon gecmisi olusturuldu.'],
            ['term' => 'Linked but unproven', 'meaning' => 'Cuzdan baglandi ama henuz yeterli gecmis kanit yok.'],
        ],
        'binance-technical' => [
            ['term' => 'Fresh 7g paper PnL', 'meaning' => 'Yeni Binance lane tarafindan son 7 günde acilan paper islemlerden gelen net sonuc.'],
            ['term' => 'Score quality', 'meaning' => 'RSI, MACD, momentum, hacim ve mikro yapi bilesenlerinin skora katkisi.'],
            ['term' => 'Spread reject', 'meaning' => 'Piyasa yapisi guvenli degilse sinyal olsa bile giris acilmaz.'],
            ['term' => 'Position pressure', 'meaning' => 'Acilan eski ve yeni pozisyonlarin yeni sinyal acma kapasitesini ne kadar kistigi.'],
            ['term' => 'Legacy position', 'meaning' => 'Yeni lane disinda kalmis veya eski metadata ile tasinan acik Binance paper pozisyonu.'],
        ],
        'polymarket-copy' => [
            ['term' => 'Paper copy', 'meaning' => 'Gercek para kullanmadan copy-ready cuzdan aksiyonunu takip eden deneme islemi.'],
            ['term' => 'Follower position', 'meaning' => 'Balinanin pozisyonuna gecikmeli ve limitli sekilde eslik eden bizim paper pozisyonumuz.'],
            ['term' => 'Copy reject', 'meaning' => 'Copy sinyali geldi ama risk, tekrar pozisyon veya islem boyutu kurali nedeniyle acilmadi.'],
            ['term' => 'Shadow vs copy drift', 'meaning' => 'Shadow simulasyon sonucu ile gercek paper copy sonucunun arasindaki fark.'],
        ],
        'sozluk-aciklamalar' => [
            ['term' => 'Trade acmak degil, once kanit', 'meaning' => 'Polymarket lane once istikrari ispatlar; shadow edge pozitif olmadan copy acmaz.'],
            ['term' => 'Tek basari metriği', 'meaning' => 'Her lane sadece bir ana metrikle yonetilir; metrik iyilesmiyorsa patch durur.'],
            ['term' => 'Servis log ozeti', 'meaning' => 'Son calisma satirlarini kisa bir blokta gosterir; hata ayiklamayi hizlandirir.'],
        ],
    ];
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
        $allTechnicalRows = dashboard_fetch_recent_binance_technical_rows($pdo);
        $acceptanceTechnicalRows = array_values(array_filter($allTechnicalRows, 'dashboard_is_binance_acceptance_row'));
        $technicalRows = array_values(array_filter($allTechnicalRows, static fn (array $row): bool => !dashboard_is_binance_acceptance_row($row)));
        $freshTechnicalRows = dashboard_filter_rows_within_minutes($technicalRows, 60);
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
        $payload['binance_technical_summary'] = dashboard_build_binance_technical_summary_from_rows($technicalRows);
        $payload['binance_technical_fresh_summary'] = dashboard_build_binance_technical_summary_from_rows($freshTechnicalRows);
        $payload['fresh_pnl_summary_7d'] = dashboard_build_binance_fresh_pnl_summary($pdo);
        $acceptancePositionRows = dashboard_table_exists($pdo, 'venue_positions')
            ? array_values(array_filter(dashboard_fetch_all($pdo, "SELECT id, sample_kind, source_signal FROM venue_positions WHERE execution_mode = 'paper'"), 'dashboard_is_binance_acceptance_position'))
            : [];
        $runtimePositionRows = dashboard_table_exists($pdo, 'venue_positions')
            ? array_values(array_filter(
                dashboard_fetch_all($pdo, "SELECT id, sample_kind, source_signal, opened_at, status FROM venue_positions WHERE execution_mode = 'paper'"),
                static fn (array $row): bool => !dashboard_is_binance_acceptance_position($row)
            ))
            : [];
        $payload['technical_acceptance_summary'] = dashboard_build_binance_technical_acceptance_summary($acceptanceTechnicalRows, $acceptancePositionRows);
        $payload['technical_runtime_acceptance_summary'] = dashboard_build_binance_technical_runtime_acceptance_summary($technicalRows, $runtimePositionRows);
        $payload['binance_technical_gate_funnel'] = dashboard_build_binance_technical_gate_funnel_from_rows($technicalRows);
        $payload['binance_technical_fresh_gate_funnel'] = dashboard_build_binance_technical_gate_funnel_from_rows($freshTechnicalRows);
        $payload['binance_technical_recovery_summary'] = dashboard_build_binance_technical_recovery_summary_from_rows($technicalRows, $payload['runtime_summary'] ?? []);
        $payload['binance_technical_fresh_recovery_summary'] = dashboard_build_binance_technical_recovery_summary_from_rows($freshTechnicalRows, $payload['runtime_summary'] ?? []);
        $payload['binance_futures_snapshot_summary'] = dashboard_build_binance_futures_snapshot_summary_from_rows($technicalRows);
        $payload['binance_technical_score_component_summary'] = dashboard_build_binance_technical_score_component_summary_from_rows($technicalRows);
        $payload['binance_technical_score_gap_summary'] = dashboard_build_binance_technical_score_gap_summary_from_rows($technicalRows);
        $payload['binance_technical_fresh_score_gap_summary'] = dashboard_build_binance_technical_score_gap_summary_from_rows($freshTechnicalRows);
        $payload['binance_technical_score_blocker_breakdown'] = dashboard_build_binance_technical_score_blocker_breakdown_from_rows($technicalRows);
        $payload['binance_technical_stale_eligibility_summary'] = dashboard_build_binance_technical_stale_eligibility_summary($payload['runtime_summary'] ?? []);
        $payload['binance_technical_legacy_position_shape_summary'] = dashboard_build_binance_technical_legacy_position_shape_summary($payload['runtime_summary'] ?? []);
        $payload['binance_technical_legacy_open_positions'] = dashboard_build_binance_technical_legacy_open_positions($payload['runtime_summary'] ?? []);
        $payload['binance_technical_position_pressure_summary'] = dashboard_build_binance_technical_position_pressure_summary($pdo, $payload['runtime_summary'] ?? [], $technicalRows);
        $payload['binance_technical_reject_breakdown'] = dashboard_build_binance_technical_reject_breakdown_from_rows($technicalRows);
        $payload['binance_technical_fresh_reject_breakdown'] = dashboard_build_binance_technical_reject_breakdown_from_rows($freshTechnicalRows);
        $payload['alias_persistence_summary'] = dashboard_build_alias_persistence_summary($pdo, $payload['runtime_summary'] ?? []);
        $payload['source_quality_summary'] = dashboard_build_source_quality_summary($pdo);
        $payload['unsupported_side_summary'] = dashboard_build_unsupported_side_summary($pdo);
        $payload['whale_universe_summary'] = dashboard_build_whale_universe_summary($pdo, $payload['runtime_summary'] ?? []);
        $payload['trusted_whale_summary'] = dashboard_build_trusted_whale_summary($pdo);
        $payload['whale_copy_summary'] = dashboard_build_whale_copy_summary($pdo);
        $payload['whale_side_summary'] = dashboard_build_whale_side_summary($pdo);
        $payload['whale_copy_gate_funnel'] = dashboard_build_whale_copy_gate_funnel($pdo);
        $payload['graph_discovery_summary'] = dashboard_build_graph_discovery_summary($payload['runtime_summary'] ?? [], $payload['whale_universe_summary'] ?? []);
        $payload['whale_candidate_aggregation_summary'] = dashboard_build_whale_candidate_aggregation_summary($payload['runtime_summary'] ?? []);
        $payload['recent_gate_ready_candidates'] = dashboard_build_recent_gate_ready_candidates($payload['runtime_summary'] ?? []);
        $payload['gated_reject_breakdown'] = dashboard_build_gated_reject_breakdown($pdo);
        $payload['relaxed_gate_reject_breakdown'] = dashboard_build_relaxed_gate_reject_breakdown($pdo);
        $payload['whale_copy_recovery_summary'] = dashboard_build_whale_copy_recovery_summary($pdo);
        $payload = array_merge($payload, dashboard_build_polymarket_research_summary($pdo));
        $payload = array_merge($payload, dashboard_build_polymarket_copy_summary($pdo));
        $payload = array_merge($payload, dashboard_build_binance_lane_summary($payload));
        $payload['polymarket_operator_summary'] = dashboard_build_polymarket_operator_summary($payload);
        $payload['binance_operator_summary'] = dashboard_build_binance_operator_summary($payload);
        $payload['operator_landing_summary'] = dashboard_build_operator_landing_summary($payload);
        $payload['dashboard_tab_help'] = dashboard_build_dashboard_tab_help();
        $payload['dashboard_glossary'] = dashboard_build_dashboard_glossary();
        $payload = dashboard_augment_recent_decisions($pdo, $payload);
    } else {
        $payload['top_unresolved_aliases'] = [];
        $payload['recent_unresolved_aliases'] = [];
        $payload['routing_breakdown'] = [];
        $payload['sampling_decision_summary'] = [];
        $payload['mapping_miss_breakdown'] = [];
        $payload['sampling_reject_breakdown'] = [];
        $payload['binance_technical_summary'] = [];
        $payload['binance_technical_fresh_summary'] = [];
        $payload['fresh_pnl_summary_7d'] = [];
        $payload['technical_acceptance_summary'] = [];
        $payload['technical_runtime_acceptance_summary'] = [];
        $payload['binance_technical_gate_funnel'] = [];
        $payload['binance_technical_fresh_gate_funnel'] = [];
        $payload['binance_technical_recovery_summary'] = [];
        $payload['binance_technical_fresh_recovery_summary'] = [];
        $payload['binance_futures_snapshot_summary'] = [];
        $payload['binance_technical_score_component_summary'] = [];
        $payload['binance_technical_score_gap_summary'] = [];
        $payload['binance_technical_fresh_score_gap_summary'] = [];
        $payload['binance_technical_score_blocker_breakdown'] = [];
        $payload['binance_technical_stale_eligibility_summary'] = [];
        $payload['binance_technical_legacy_position_shape_summary'] = [];
        $payload['binance_technical_legacy_open_positions'] = [];
        $payload['binance_technical_position_pressure_summary'] = [];
        $payload['binance_technical_reject_breakdown'] = [];
        $payload['binance_technical_fresh_reject_breakdown'] = [];
        $payload['alias_persistence_summary'] = [];
        $payload['source_quality_summary'] = [];
        $payload['unsupported_side_summary'] = [];
        $payload['whale_universe_summary'] = [];
        $payload['trusted_whale_summary'] = [];
        $payload['whale_copy_summary'] = [];
        $payload['whale_side_summary'] = [];
        $payload['whale_copy_gate_funnel'] = [];
        $payload['graph_discovery_summary'] = [];
        $payload['whale_candidate_aggregation_summary'] = [];
        $payload['recent_gate_ready_candidates'] = [];
        $payload['gated_reject_breakdown'] = [];
        $payload['relaxed_gate_reject_breakdown'] = [];
        $payload['whale_copy_recovery_summary'] = [];
        $payload = array_merge($payload, dashboard_build_polymarket_research_summary(new PDO('sqlite::memory:')));
        $payload = array_merge($payload, dashboard_build_polymarket_copy_summary(new PDO('sqlite::memory:')));
        $payload = array_merge($payload, dashboard_build_binance_lane_summary($payload));
        $payload['polymarket_operator_summary'] = dashboard_build_polymarket_operator_summary($payload);
        $payload['binance_operator_summary'] = dashboard_build_binance_operator_summary($payload);
        $payload['operator_landing_summary'] = dashboard_build_operator_landing_summary($payload);
        $payload['dashboard_tab_help'] = dashboard_build_dashboard_tab_help();
        $payload['dashboard_glossary'] = dashboard_build_dashboard_glossary();
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
