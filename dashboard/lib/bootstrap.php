<?php
declare(strict_types=1);

function dashboard_repo_root(): string
{
    static $root = null;
    if ($root === null) {
        $configured = getenv('GHOST_TRADER_REPO_ROOT') ?: '';
        $candidate = $configured !== '' ? $configured : dirname(__DIR__, 2);
        $resolved = realpath($candidate);
        $root = $resolved !== false ? $resolved : $candidate;
    }

    return $root;
}

function dashboard_load_env_file(string $envFile): array
{
    $values = [];
    if (!is_file($envFile)) {
        return $values;
    }

    $lines = file($envFile, FILE_IGNORE_NEW_LINES | FILE_SKIP_EMPTY_LINES);
    if ($lines === false) {
        return $values;
    }

    foreach ($lines as $line) {
        $trimmed = trim($line);
        if ($trimmed === '' || str_starts_with($trimmed, '#') || !str_contains($trimmed, '=')) {
            continue;
        }

        [$key, $value] = explode('=', $trimmed, 2);
        $key = trim($key);
        $value = trim($value);
        if ($key === '') {
            continue;
        }

        $length = strlen($value);
        if ($length >= 2) {
            $first = $value[0];
            $last = $value[$length - 1];
            if (($first === '"' && $last === '"') || ($first === "'" && $last === "'")) {
                $value = substr($value, 1, -1);
            }
        }

        $values[$key] = $value;
    }

    return $values;
}

function dashboard_config(): array
{
    static $config = null;
    if ($config !== null) {
        return $config;
    }

    $defaults = [
        'GHOST_TRADER_DB_PATH' => 'data/ghost_trader.db',
        'DASHBOARD_ENABLED' => 'true',
        'DASHBOARD_HOST' => '0.0.0.0',
        'DASHBOARD_PORT' => '8081',
        'DASHBOARD_USER' => 'admin',
        'DASHBOARD_PASSWORD_HASH' => '',
        'DASHBOARD_REFRESH_SECONDS' => '5',
        'DASHBOARD_API_CACHE_SECONDS' => '8',
        'DASHBOARD_DECISION_SCAN_LIMIT' => '50000',
        'DASHBOARD_LOG_LINES' => '40',
        'DASHBOARD_SUMMARY_PATH' => 'reports/performance/summary.json',
        'DASHBOARD_SWOT_PATH' => 'reports/performance/swot_report.json',
        'DASHBOARD_TARGET_SERVICE' => 'ghost-trader',
        'DASHBOARD_SERVICE_NAME' => 'ghost-trader-dashboard',
        'DASHBOARD_LANE_MODE' => 'split',
    ];

    $config = $defaults;
    $envFile = dashboard_repo_root() . DIRECTORY_SEPARATOR . '.env';
    foreach (dashboard_load_env_file($envFile) as $key => $value) {
        $config[$key] = $value;
    }

    foreach (array_keys($config) as $key) {
        $envValue = getenv($key);
        if ($envValue !== false && $envValue !== '') {
            $config[$key] = $envValue;
        }
    }

    return $config;
}

function dashboard_env(string $key, ?string $default = null): ?string
{
    $config = dashboard_config();
    return array_key_exists($key, $config) ? (string) $config[$key] : $default;
}

function dashboard_bool_env(string $key, bool $default = false): bool
{
    $value = dashboard_env($key);
    if ($value === null) {
        return $default;
    }

    return in_array(strtolower(trim($value)), ['1', 'true', 'yes', 'on'], true);
}

function dashboard_int_env(string $key, int $default = 0): int
{
    $value = dashboard_env($key);
    return $value !== null && is_numeric($value) ? (int) $value : $default;
}

function dashboard_path(string $path): string
{
    if ($path === '') {
        return dashboard_repo_root();
    }

    if (preg_match('/^[A-Za-z]:[\\\\\\/]/', $path) === 1 || str_starts_with($path, '/') || str_starts_with($path, '\\')) {
        return $path;
    }

    return dashboard_repo_root() . DIRECTORY_SEPARATOR . str_replace(['/', '\\'], DIRECTORY_SEPARATOR, $path);
}

function dashboard_service_name(string $raw): string
{
    $sanitized = preg_replace('/[^A-Za-z0-9_.@-]/', '', $raw) ?? '';
    return $sanitized !== '' ? $sanitized : 'ghost-trader';
}

function dashboard_lane_mode(): string
{
    $mode = strtolower(trim((string) dashboard_env('DASHBOARD_LANE_MODE', 'split')));
    return in_array($mode, ['split', 'polymarket_research', 'binance_technical'], true) ? $mode : 'split';
}

function dashboard_json_response(array $payload, int $statusCode = 200): never
{
    http_response_code($statusCode);
    header('Content-Type: application/json; charset=utf-8');
    echo json_encode($payload, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES);
    exit;
}

function dashboard_html(string $value): string
{
    return htmlspecialchars($value, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
}
