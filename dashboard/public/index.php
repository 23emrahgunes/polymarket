<?php
declare(strict_types=1);

require_once dirname(__DIR__) . '/lib/bootstrap.php';
require_once dirname(__DIR__) . '/lib/auth.php';

dashboard_require_auth();

$laneMode = dashboard_lane_mode();
$refreshSeconds = max(dashboard_int_env('DASHBOARD_REFRESH_SECONDS', 5), 2);

$requestHost = (string) ($_SERVER['HTTP_HOST'] ?? '127.0.0.1');
$hostName = preg_replace('/:\d+$/', '', $requestHost) ?: '127.0.0.1';
$scheme = (!empty($_SERVER['HTTPS']) && $_SERVER['HTTPS'] !== 'off') ? 'https' : 'http';
$currentUrl = $scheme . '://' . $requestHost . rtrim((string) ($_SERVER['PHP_SELF'] ?? '/index.php'), '/');

$pageMap = [
    'split' => [
        'body_class' => 'ws-lane-split lane-split',
        'title' => 'WhaleSignal Operations',
        'subtitle' => 'Polymarket whale tracking ve Binance paper trading lane durumunu tek bakista yonetin.',
        'eyebrow' => 'WhaleSignal Operator',
    ],
    'polymarket_research' => [
        'body_class' => 'ws-lane-polymarket lane-polymarket-research',
        'title' => 'Polymarket Copy Trade',
        'subtitle' => 'Takip edilen istikrarli cüzdanlar, kopyalanan islemler ve portföy performansi.',
        'eyebrow' => 'WhaleSignal / Polymarket',
    ],
    'binance_technical' => [
        'body_class' => 'ws-lane-binance lane-binance-technical',
        'title' => 'Binance Trading Operations',
        'subtitle' => 'Spot ve futures islemleri, pozisyonlar, emirler ve risk görünümü.',
        'eyebrow' => 'WhaleSignal / Binance',
    ],
];

$page = $pageMap[$laneMode] ?? $pageMap['split'];

$laneLinks = [
    'landing' => $laneMode === 'split' ? $currentUrl : $scheme . '://' . $hostName . ':8081/index.php',
    'polymarket' => $scheme . '://' . $hostName . ':8082/index.php',
    'binance' => $scheme . '://' . $hostName . ':8083/index.php',
];

$navMap = [
    'split' => [
        ['label' => 'Genel Bakis', 'href' => '#landing-overview', 'active' => true],
        ['label' => 'Polymarket', 'href' => $laneLinks['polymarket'], 'active' => false, 'external' => true],
        ['label' => 'Binance', 'href' => $laneLinks['binance'], 'active' => false, 'external' => true],
        ['label' => 'Notlar', 'href' => '#landing-notes', 'active' => false],
    ],
    'polymarket_research' => [
        ['label' => 'Genel Bakis', 'href' => '#overview', 'active' => true],
        ['label' => 'Balinalar', 'href' => '#tracked-wallets', 'active' => false],
        ['label' => 'Canli Akis', 'href' => '#whale-feed', 'active' => false],
        ['label' => 'Pozisyonlar', 'href' => '#copy-positions', 'active' => false],
        ['label' => 'Kararlar', 'href' => '#decision-engine', 'active' => false],
        ['label' => 'Segmentler', 'href' => '#watch-segments', 'active' => false],
        ['label' => 'Detay', 'href' => '#detail-tabs', 'active' => false],
    ],
    'binance_technical' => [
        ['label' => 'Genel Bakis', 'href' => '#overview', 'active' => true],
        ['label' => 'Filtreler', 'href' => '#filters', 'active' => false],
        ['label' => 'Pozisyonlar', 'href' => '#open-positions', 'active' => false],
        ['label' => 'Emirler', 'href' => '#open-orders', 'active' => false],
        ['label' => 'Runtime', 'href' => '#runtime-feed', 'active' => false],
        ['label' => 'Risk', 'href' => '#risk-panel', 'active' => false],
        ['label' => 'Gecmis', 'href' => '#trade-history', 'active' => false],
        ['label' => 'Detay', 'href' => '#detail-tabs', 'active' => false],
    ],
];

$shellConfig = [
    'brand' => 'WhaleSignal',
    'laneMode' => $laneMode,
    'refreshSeconds' => $refreshSeconds,
    'apiUrl' => 'api.php',
    'page' => $page,
    'laneLinks' => $laneLinks,
];

$assetVersion = (string) (@filemtime(__DIR__ . '/assets/operator.css') ?: time());
?>
<!DOCTYPE html>
<html lang="tr">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title><?= dashboard_html($page['title']); ?></title>
    <link rel="stylesheet" href="assets/operator.css?v=<?= dashboard_html($assetVersion); ?>">
</head>
<body class="<?= dashboard_html($page['body_class']); ?>">
    <div class="ws-shell">
        <aside class="ws-sidebar" aria-label="WhaleSignal navigasyon">
            <div class="ws-brand">
                <span class="ws-brand-mark">WS</span>
                <div>
                    <div>WhaleSignal</div>
                    <div class="ws-muted">Operator Panel</div>
                </div>
            </div>

            <div class="ws-sidebar-group">
                <div class="ws-sidebar-label">Navigasyon</div>
                <nav class="ws-nav">
                    <?php foreach ($navMap[$laneMode] ?? [] as $item): ?>
                        <a
                            class="ws-nav-link<?= !empty($item['active']) ? ' is-active' : ''; ?>"
                            href="<?= dashboard_html($item['href']); ?>"
                            <?= !empty($item['external']) ? 'target="_self" rel="noreferrer"' : ''; ?>
                        >
                            <span><?= dashboard_html($item['label']); ?></span>
                        </a>
                    <?php endforeach; ?>
                </nav>
            </div>

            <div class="ws-sidebar-group">
                <div class="ws-sidebar-label">Lane Gecis</div>
                <nav class="ws-nav">
                    <a class="ws-nav-link<?= $laneMode === 'split' ? ' is-active' : ''; ?>" href="<?= dashboard_html($laneLinks['landing']); ?>">Kontrol Merkezi</a>
                    <a class="ws-nav-link<?= $laneMode === 'polymarket_research' ? ' is-active' : ''; ?>" href="<?= dashboard_html($laneLinks['polymarket']); ?>">Polymarket</a>
                    <a class="ws-nav-link<?= $laneMode === 'binance_technical' ? ' is-active' : ''; ?>" href="<?= dashboard_html($laneLinks['binance']); ?>">Binance</a>
                </nav>
            </div>

            <div class="ws-sidebar-footer">
                <strong>Operator hedefi</strong>
                <div style="margin-top:8px;">Ilk bakista servis durumu, PnL, acik islemler, izlenen kaynaklar ve risk gorünümü.</div>
            </div>
        </aside>

        <main class="ws-main-wrap">
            <div class="ws-topbar">
                <div>
                    <button class="ws-mobile-toggle" type="button" aria-label="Menüyü ac" data-mobile-toggle>Menu</button>
                    <div class="ws-heading-eyebrow"><?= dashboard_html($page['eyebrow']); ?></div>
                    <h1 class="ws-page-title"><?= dashboard_html($page['title']); ?></h1>
                    <p class="ws-page-subtitle"><?= dashboard_html($page['subtitle']); ?></p>
                </div>
                <div class="ws-topbar-meta">
                    <span id="ws-status-pill" class="ws-status-pill is-neutral">Yukleniyor</span>
                    <span class="ws-last-sync" id="ws-last-sync">Son guncelleme bekleniyor</span>
                </div>
            </div>

            <div id="ws-alerts" class="ws-alerts is-hidden"></div>

            <div id="ws-root" class="ws-root">
                <div class="ws-loading-card">
                    Operator dashboard verisi yukleniyor...
                </div>
            </div>
        </main>
    </div>

    <dialog id="ws-detail-modal" class="ws-modal">
        <div class="ws-modal-inner" id="ws-detail-modal-content"></div>
    </dialog>

    <script>
        window.WHALESIGNAL_CONFIG = <?= json_encode($shellConfig, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE); ?>;
    </script>
    <script src="assets/operator.js?v=<?= dashboard_html((string) (@filemtime(__DIR__ . '/assets/operator.js') ?: time())); ?>"></script>
</body>
</html>
