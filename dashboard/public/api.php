<?php
declare(strict_types=1);

require_once dirname(__DIR__) . '/lib/bootstrap.php';
require_once dirname(__DIR__) . '/lib/auth.php';
require_once dirname(__DIR__) . '/lib/data.php';
require_once __DIR__ . '/presenter.php';

dashboard_require_auth();

$view = isset($_GET['view']) ? (string) $_GET['view'] : 'full';
$cacheSeconds = max(dashboard_int_env('DASHBOARD_API_CACHE_SECONDS', 8), 0);
$cachePath = null;
if ($cacheSeconds > 0 && !isset($_GET['nocache'])) {
    $dbPath = dashboard_path(dashboard_env('GHOST_TRADER_DB_PATH', 'data/ghost_trader.db') ?? 'data/ghost_trader.db');
    $snapshotVersion = '';
    if (is_file($dbPath)) {
        try {
            $cachePdo = new PDO('sqlite:' . $dbPath);
            $cachePdo->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);
            $snapshotRow = $cachePdo->query('SELECT updated_at FROM runtime_status_snapshot WHERE id = 1');
            $snapshotVersion = $snapshotRow ? (string) ($snapshotRow->fetchColumn() ?: '') : '';
        } catch (Throwable $exception) {
            $snapshotVersion = (string) (@filemtime($dbPath) ?: '');
        }
    }
    $cacheKey = hash('sha1', $view . '|' . $dbPath . '|' . $snapshotVersion);
    $cachePath = dashboard_path('data/dashboard_api_cache_' . $cacheKey . '.json');
    if (is_file($cachePath) && (time() - (int) filemtime($cachePath)) <= $cacheSeconds) {
        $cached = file_get_contents($cachePath);
        if ($cached !== false && trim($cached) !== '') {
            header('Content-Type: application/json; charset=utf-8');
            header('X-Dashboard-Cache: HIT');
            echo $cached;
            exit;
        }
    }
}

$payload = dashboard_augment_payload(dashboard_build_payload($view));
$json = json_encode($payload, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES);
if ($json === false) {
    dashboard_json_response($payload);
}

if ($cachePath !== null) {
    $cacheDir = dirname($cachePath);
    if (is_dir($cacheDir) || @mkdir($cacheDir, 0775, true)) {
        $tmpPath = $cachePath . '.' . getmypid() . '.tmp';
        if (@file_put_contents($tmpPath, $json, LOCK_EX) !== false) {
            @rename($tmpPath, $cachePath);
        }
    }
}

header('Content-Type: application/json; charset=utf-8');
header('X-Dashboard-Cache: MISS');
echo $json;
exit;
