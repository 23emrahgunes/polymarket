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
        'technical_open_positions_total' => (int) ($runtimeSummary['technical_open_positions_total'] ?? 0),
        'technical_open_positions_strict' => (int) ($runtimeSummary['technical_open_positions_strict'] ?? 0),
        'technical_open_positions_legacy' => (int) ($runtimeSummary['technical_open_positions_legacy'] ?? 0),
        'technical_open_positions_backfilled' => (int) ($runtimeSummary['technical_open_positions_backfilled'] ?? 0),
        'technical_open_positions_ineligible' => (int) ($runtimeSummary['technical_open_positions_ineligible'] ?? 0),
    ];
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

function dashboard_build_dashboard_tab_help(): array
{
    return [
        'genel-bakis' => 'Bu sekme botun canli durumunu, son kararlari ve hizli genel resmi gosterir.',
        'polymarket' => 'Bu sekme market esleme, alias cache, whale evreni ve Polymarket kaynak kalitesini gosterir.',
        'binance-teknik' => 'Bu sekme Binance teknik paper lane performansini, taze 60 dakika ozetini ve recovery metriklerini gosterir.',
        'pozisyonlar-risk' => 'Bu sekme acik pozisyonlari, kalan kapasiteyi ve stale pozisyon baskisini gosterir.',
        'teshis-log' => 'Bu sekme blocker dagilimlarini, detayli red nedenlerini ve servis loglarini gosterir.',
    ];
}

function dashboard_build_dashboard_glossary(): array
{
    return [
        'genel-bakis' => [
            ['term' => 'Nihai karar', 'meaning' => 'Panelin mevcut veriye gore verdigi ust seviye operasyon yorumu.'],
            ['term' => 'Eslenen orderflow', 'meaning' => 'Gelen akisin market ile bag kurulabilen kismi.'],
        ],
        'polymarket' => [
            ['term' => 'Market eslesmedi', 'meaning' => 'Akis var ama dogru markete baglanamadi.'],
            ['term' => 'Alias cache', 'meaning' => 'Market kimliklerini hizli bulmak icin tutulan esleme cache katmani.'],
            ['term' => 'Whale', 'meaning' => 'Yuksek hacimli veya tekrar eden profesyonel cuzdan davranisi.'],
        ],
        'binance-teknik' => [
            ['term' => 'Skor esik alti', 'meaning' => 'Sinyal var ama islem acacak kadar guclu degil.'],
            ['term' => 'Teknik uyum zayif', 'meaning' => 'EMA, MACD ve momentum ayni yone yeterince destek vermiyor.'],
            ['term' => 'Taze ozet', 'meaning' => 'Yalnizca son 60 dakikadaki teknik davranisi gosterir.'],
            ['term' => 'Eski teknik pozisyon', 'meaning' => 'Eski surumden kalan, teknik lane metadata bilgisi eksik acik pozisyon.'],
        ],
        'pozisyonlar-risk' => [
            ['term' => 'Kalan kapasite', 'meaning' => 'Yeni pozisyon acmak icin elde kalan risk butcesi.'],
            ['term' => 'Stale pozisyon', 'meaning' => 'Uzun suredir acik kalan ve yeniden gozden gecirilen pozisyon.'],
            ['term' => 'Sure baskisiyla cikis', 'meaning' => 'Teknik destek zayifladigi icin uzun sure acik kalan pozisyonun kapatilmasi.'],
            ['term' => 'Uzun sure acik kaldigi icin cikis', 'meaning' => 'Hard-timeout sinirina takilan ve guncel destek bulamayan pozisyonun kapatilmasi.'],
        ],
        'teshis-log' => [
            ['term' => 'Blocker', 'meaning' => 'Kararin execute olmasini engelleyen baskin neden.'],
            ['term' => 'Servis log ozeti', 'meaning' => 'Botun son calisma satirlarini hizli okumak icin log kesiti.'],
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
        $technicalRows = dashboard_fetch_recent_binance_technical_rows($pdo);
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
