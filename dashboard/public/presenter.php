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

function dashboard_augment_payload(array $payload): array
{
    $warnings = [];
    $pdo = dashboard_open_db($warnings);
    if ($pdo !== null) {
        $payload = array_merge($payload, dashboard_build_unresolved_alias_summary($pdo));
    } else {
        $payload['top_unresolved_aliases'] = [];
        $payload['recent_unresolved_aliases'] = [];
    }

    $translatedWarnings = array_map('dashboard_translate_warning', array_merge($payload['warnings'] ?? [], $warnings));
    $payload['warnings'] = array_values(array_unique($translatedWarnings));
    return $payload;
}
