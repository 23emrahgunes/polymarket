<?php
declare(strict_types=1);

require_once dirname(__DIR__) . '/lib/bootstrap.php';
require_once dirname(__DIR__) . '/lib/auth.php';

dashboard_require_auth();

$refreshSeconds = max(dashboard_int_env('DASHBOARD_REFRESH_SECONDS', 5), 2);
?>
<!DOCTYPE html>
<html lang="tr">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Ghost Trader Operasyon Paneli</title>
    <style>
        :root {
            --bg: #08121a;
            --bg-elevated: rgba(14, 27, 39, 0.82);
            --panel: rgba(10, 24, 35, 0.92);
            --panel-soft: rgba(15, 33, 47, 0.88);
            --line: rgba(132, 181, 205, 0.16);
            --text: #e8f3f8;
            --muted: #95afbc;
            --accent: #4dc7b0;
            --warn: #f0b35a;
            --danger: #ef6b6b;
            --ok: #7dd68b;
            --shadow: 0 24px 48px rgba(0, 0, 0, 0.28);
        }
        * { box-sizing: border-box; }
        body {
            margin: 0;
            min-height: 100vh;
            font-family: "IBM Plex Sans", "Segoe UI", "Trebuchet MS", sans-serif;
            background:
                radial-gradient(circle at top right, rgba(77, 199, 176, 0.14), transparent 32%),
                radial-gradient(circle at top left, rgba(240, 179, 90, 0.12), transparent 28%),
                linear-gradient(180deg, #08121a 0%, #091018 100%);
            color: var(--text);
        }
        .shell { max-width: 1500px; margin: 0 auto; padding: 28px 18px 36px; }
        .hero-card, .stat-card, .panel {
            border: 1px solid var(--line);
            box-shadow: var(--shadow);
            backdrop-filter: blur(10px);
        }
        .hero-card {
            background: linear-gradient(180deg, rgba(13, 27, 40, 0.95), rgba(9, 20, 30, 0.9));
            border-radius: 20px;
            padding: 22px;
            margin-bottom: 18px;
        }
        .hero-top { display: flex; justify-content: space-between; gap: 14px; flex-wrap: wrap; align-items: center; }
        .eyebrow { font-size: 0.82rem; letter-spacing: 0.16em; text-transform: uppercase; color: var(--muted); }
        h1 { margin: 6px 0 0; font-size: clamp(1.7rem, 2vw, 2.4rem); line-height: 1.05; }
        .hero-sub { margin: 12px 0 0; max-width: 920px; color: var(--muted); line-height: 1.6; }
        .status-banner, .warnings {
            display: none;
            margin-bottom: 18px;
            border-radius: 18px;
            padding: 16px 18px;
        }
        .status-banner { border: 1px solid rgba(239, 107, 107, 0.35); background: rgba(99, 27, 27, 0.32); color: #ffd4d4; }
        .warnings { border: 1px solid rgba(240, 179, 90, 0.28); background: rgba(89, 58, 14, 0.32); }
        .warnings ul { margin: 10px 0 0; padding-left: 18px; color: #ffdca4; }
        .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(175px, 1fr)); gap: 14px; margin-bottom: 18px; }
        .stat-card {
            background: var(--bg-elevated);
            border-radius: 18px;
            padding: 16px;
            min-height: 120px;
            overflow: hidden;
        }
        .stat-label { color: var(--muted); font-size: 0.84rem; letter-spacing: 0.08em; text-transform: uppercase; }
        .stat-value { margin-top: 14px; font-size: 1.8rem; font-weight: 700; line-height: 1; }
        .stat-note { margin-top: 10px; color: var(--muted); font-size: 0.92rem; }
        #verdict-reason {
            display: block;
            max-height: 6.4em;
            overflow: auto;
            line-height: 1.45;
            padding-right: 4px;
        }
        .grid {
            display: grid;
            grid-template-columns: repeat(12, minmax(0, 1fr));
            gap: 16px;
            align-items: start;
        }
        .panel {
            background: rgba(7, 18, 28, 0.98);
            border-radius: 18px;
            padding: 18px;
            overflow: hidden;
            display: flex;
            flex-direction: column;
            min-height: 0;
            isolation: isolate;
        }
        .panel-wide { grid-column: span 12; }
        .panel-half { grid-column: span 6; }
        .panel-third { grid-column: span 4; }
        .panel-header { display: flex; justify-content: space-between; gap: 12px; align-items: center; margin-bottom: 14px; }
        .panel h2 { margin: 0; font-size: 1rem; letter-spacing: 0.03em; text-transform: uppercase; }
        .panel-copy { margin: 0 0 14px; color: var(--muted); line-height: 1.5; font-size: 0.94rem; }
        .subsection-title {
            margin: 16px 0 8px;
            color: var(--muted);
            font-size: 0.8rem;
            letter-spacing: 0.08em;
            text-transform: uppercase;
        }
        .badge {
            display: inline-flex; align-items: center; gap: 8px; padding: 7px 12px; border-radius: 999px; font-size: 0.78rem;
            letter-spacing: 0.08em; text-transform: uppercase; border: 1px solid var(--line); background: rgba(255, 255, 255, 0.02); color: var(--text);
        }
        .badge.ok { color: var(--ok); border-color: rgba(125, 214, 139, 0.3); background: rgba(51, 94, 54, 0.24); }
        .badge.warn { color: var(--warn); border-color: rgba(240, 179, 90, 0.3); background: rgba(109, 76, 24, 0.24); }
        .badge.danger { color: #ffb0b0; border-color: rgba(239, 107, 107, 0.28); background: rgba(112, 36, 36, 0.28); }
        .badge.info { color: var(--accent); border-color: rgba(77, 199, 176, 0.28); background: rgba(14, 83, 74, 0.28); }
        table { width: 100%; border-collapse: collapse; font-size: 0.92rem; table-layout: fixed; }
        th, td { padding: 10px 8px; text-align: left; border-bottom: 1px solid var(--line); vertical-align: top; }
        th { color: var(--muted); font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.08em; }
        .mono { font-family: "IBM Plex Mono", "Cascadia Mono", "Consolas", monospace; font-size: 0.88rem; }
        .empty { padding: 18px 0 8px; color: var(--muted); }
        pre {
            margin: 0; padding: 14px; border-radius: 14px; border: 1px solid var(--line); background: var(--panel-soft); overflow: auto;
            max-height: 360px; font-family: "IBM Plex Mono", "Cascadia Mono", "Consolas", monospace; font-size: 0.85rem; line-height: 1.55;
        }
        .metric-list { display: grid; gap: 10px; }
        .metric-item { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; padding: 10px 0; border-bottom: 1px solid var(--line); }
        .metric-item:last-child { border-bottom: none; }
        .metric-key { color: var(--muted); }
        .metric-value { font-weight: 700; max-width: 52%; text-align: right; }
        .metric-item.stacked { flex-direction: column; }
        .metric-item.stacked .metric-value { max-width: 100%; text-align: left; line-height: 1.45; font-weight: 600; }
        .truncate-text {
            display: block;
            max-width: 100%;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }
        #recent-decisions { max-height: 420px; overflow: auto; }
        #market-alias-counts, #whale-wallet-counts { max-height: 160px; overflow: auto; }
        #top-market-aliases, #top-whales { max-height: 320px; overflow: auto; }
        #top-unresolved-aliases, #recent-unresolved-aliases { max-height: 220px; overflow: auto; }
        #performance-snapshot { max-height: 420px; overflow: auto; }
        #service-log { max-height: 280px; }
        @media (max-width: 1100px) { .panel-half, .panel-third { grid-column: span 12; } }
    </style>
</head>
<body>
<div class="shell">
    <section class="hero-card">
        <div class="hero-top">
            <div>
                <div class="eyebrow">Ghost Trader Operasyon</div>
                <h1>Canlı çalışma zamanı, mapping sağlığı ve strateji kanıtı tek ekranda.</h1>
            </div>
            <div class="badge info" id="last-refresh-badge">Her <?= dashboard_html((string) $refreshSeconds) ?> saniyede yenileniyor</div>
        </div>
        <p class="hero-sub">Çalışma zamanı sağlığı, çoklu venue durumu, karar denetim akışı, market eşleme kapsamı ve temkinli performans verdict’i için salt-okunur operasyon paneli.</p>
    </section>

    <div class="status-banner" id="status-banner"></div>
    <div class="warnings" id="warnings-panel"><strong>Uyarılar</strong><ul id="warnings-list"></ul></div>

    <section class="stats">
        <article class="stat-card"><div class="stat-label">Servis</div><div class="stat-value" id="service-status">...</div><div class="stat-note" id="service-name">ghost-trader</div></article>
        <article class="stat-card"><div class="stat-label">Toplam İşlem</div><div class="stat-value" id="total-trades">0</div><div class="stat-note" id="open-state-note">0 açık pozisyon / 0 açık emir</div></article>
        <article class="stat-card"><div class="stat-label">İzlenen Balinalar</div><div class="stat-value" id="tracked-whales">0</div><div class="stat-note">Hibrit cache kapsamı</div></article>
        <article class="stat-card"><div class="stat-label">Eşlenen Orderflow</div><div class="stat-value" id="mapped-orderflow">0 / 0</div><div class="stat-note" id="mapping-note">Alias cache 0, lazy hit 0</div></article>
        <article class="stat-card"><div class="stat-label">Market Eşleşmedi</div><div class="stat-value" id="market-not-mapped-rate">0%</div><div class="stat-note">Daha düşük daha iyi</div></article>
        <article class="stat-card"><div class="stat-label">Nihai Karar</div><div class="stat-value" id="final-verdict">...</div><div class="stat-note" id="verdict-reason">Rapor bekleniyor</div></article>
    </section>

    <section class="grid">
        <article class="panel panel-wide"><div class="panel-header"><h2>Son Kararlar</h2><span class="badge warn">Denetim Akışı</span></div><p class="panel-copy">Kaynak, neden, skor, mapping aşaması ve işlem boyutuyla birlikte son red, karar, işlem ve çıkış olayları.</p><div id="recent-decisions"></div></article>
        <article class="panel panel-half"><div class="panel-header"><h2>Açık Pozisyonlar</h2><span class="badge info">Venue Maruziyeti</span></div><div id="open-positions"></div></article>
        <article class="panel panel-half"><div class="panel-header"><h2>Açık Emirler</h2><span class="badge info">Koruma Katmanı</span></div><div id="open-orders"></div></article>
        <article class="panel panel-half"><div class="panel-header"><h2>Son İşlemler</h2><span class="badge info">SQLite Çalışma Geçmişi</span></div><div id="recent-trades"></div></article>
        <article class="panel panel-half"><div class="panel-header"><h2>Venue Hesapları</h2><span class="badge info">Paper Bakiyeleri</span></div><div id="venue-accounts"></div></article>
        <article class="panel panel-third">
            <div class="panel-header"><h2>Mapping Sağlığı</h2><span class="badge info">Kapsam</span></div>
            <div class="metric-list" id="mapping-health"></div>
            <div class="subsection-title">Alias Kaynakları</div>
            <div id="market-alias-counts"></div>
            <div class="subsection-title">En Güçlü Alias Cache</div>
            <div id="top-market-aliases"></div>
            <div class="subsection-title">En Sık Çözülemeyen Alias’lar</div>
            <div id="top-unresolved-aliases"></div>
            <div class="subsection-title">Son Çözülemeyen Alias Olayları</div>
            <div id="recent-unresolved-aliases"></div>
        </article>
        <article class="panel panel-third"><div class="panel-header"><h2>Balina Kaynağı</h2><span class="badge info">Hibrit Cache</span></div><div id="whale-wallet-counts"></div><div id="top-whales" style="margin-top:14px;"></div></article>
        <article class="panel panel-third"><div class="panel-header"><h2>Performans Özeti</h2><span class="badge warn">Kanıt</span></div><div class="metric-list" id="performance-snapshot"></div></article>
        <article class="panel panel-wide"><div class="panel-header"><h2>Servis Log Özeti</h2><span class="badge info">Best Effort</span></div><pre id="service-log">Yükleniyor...</pre></article>
    </section>
</div>
<script>
const refreshSeconds = <?= json_encode($refreshSeconds, JSON_UNESCAPED_SLASHES) ?>;

function escapeHtml(value) {
    return String(value ?? '').replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll("'", '&#39;');
}

function formatNumber(value, digits = 1) {
    if (value === null || value === undefined || value === '' || Number.isNaN(Number(value))) {
        return 'yok';
    }
    return Number(value).toLocaleString('tr-TR', { maximumFractionDigits: digits, minimumFractionDigits: 0 });
}

function translateCategory(value) {
    const text = String(value ?? '').toUpperCase();
    const map = { CRYPTO: 'KRİPTO', SPORTS: 'SPOR', POLITICS: 'POLİTİKA', OTHER: 'DİĞER', UNKNOWN: 'BİLİNMİYOR' };
    return map[text] || String(value ?? 'yok');
}

function translateVerdict(value) {
    const text = String(value ?? '').toUpperCase();
    const map = {
        'IMPROVE FIRST': 'ÖNCE İYİLEŞTİR',
        'NO-GO': 'UYGUN DEĞİL',
        'GO': 'DEVAM'
    };
    return map[text] || String(value ?? 'yok');
}

function translateStrategyProfile(value) {
    const text = String(value ?? '').toLowerCase();
    const map = {
        baseline: 'baseline',
        sampling_relaxed: 'sampling_relaxed'
    };
    return map[text] || String(value ?? 'yok');
}

function translateSamplingMode(value) {
    const text = String(value ?? '').toLowerCase();
    const map = {
        enabled: 'Açık',
        disabled: 'Kapalı',
        target_reached: 'Hedefe ulaştı'
    };
    return map[text] || String(value ?? 'yok');
}

function translateAction(value) {
    const text = String(value ?? '').toUpperCase();
    const map = {
        RUNNING: 'ÇALIŞIYOR',
        DEGRADED: 'DEGRADE',
        REJECT: 'RED',
        DECISION: 'KARAR',
        EXECUTE: 'İŞLENDİ',
        EXECUTED: 'İŞLENDİ',
        EXIT: 'ÇIKIŞ',
        OPEN: 'AÇIK',
        CLOSED_WIN: 'KAPANDI_KAZANÇ',
        CLOSED_LOSS: 'KAPANDI_ZARAR',
        CLOSED_FLAT: 'KAPANDI_NÖTR'
    };
    return map[text] || String(value ?? 'yok');
}

function translateMappingStage(value) {
    const text = String(value ?? '').toLowerCase();
    const map = {
        active_context: 'aktif bağlam',
        alias_cache: 'alias cache',
        lazy_lookup: 'lazy lookup',
        hot_window: 'sıcak pencere',
        active_window: 'aktif pencere',
        unknown_token: 'bilinmeyen token'
    };
    return map[text] || String(value ?? 'yok');
}

function translateReason(value) {
    const text = String(value ?? '');
    const map = {
        route_whale_orderflow_only: 'yalnızca whale/orderflow rotası',
        market_not_mapped_active_window: 'aktif pencere dışında kaldı',
        market_not_mapped_lazy_lookup_failed: 'lazy lookup eşleme bulamadı',
        market_not_mapped_unknown_token: 'token eşleşmesi bulunamadı',
        score_below_threshold: 'skor eşik altında',
        liquidity_guard_rejection: 'likidite koruması reddetti',
        slippage_guard_rejection: 'slippage koruması reddetti',
        cluster_threshold_not_reached: 'cluster eşiği tutmadı',
        sampling_target_reached: 'sampling hedefi dolduğu için durduruldu',
        none: 'yok'
    };
    return map[text] || String(value ?? 'yok');
}

function badgeClass(label) {
    const text = String(label ?? '').toUpperCase();
    if (text.includes('RUN') || text.includes('ACTIVE') || text === 'GO') { return 'ok'; }
    if (text.includes('REJECT') || text.includes('IMPROVE') || text.includes('WARN')) { return 'warn'; }
    if (text.includes('FAIL') || text.includes('ERROR') || text.includes('NO-GO')) { return 'danger'; }
    return 'info';
}

function truncateHtml(value, max = 44) {
    const text = String(value ?? '');
    if (!text) {
        return '';
    }
    const shortened = text.length > max ? `${text.slice(0, Math.max(max - 1, 1))}…` : text;
    return `<span class="truncate-text" title="${escapeHtml(text)}">${escapeHtml(shortened)}</span>`;
}

function renderTable(targetId, columns, rows, emptyMessage) {
    const target = document.getElementById(targetId);
    if (!target) { return; }
    if (!Array.isArray(rows) || rows.length === 0) {
        target.innerHTML = `<div class="empty">${escapeHtml(emptyMessage)}</div>`;
        return;
    }
    const thead = columns.map((column) => `<th>${escapeHtml(column.label)}</th>`).join('');
    const tbody = rows.map((row) => {
        const cells = columns.map((column) => {
            const raw = typeof column.render === 'function' ? column.render(row) : row[column.key];
            return `<td class="${column.mono ? 'mono' : ''}">${raw ?? ''}</td>`;
        }).join('');
        return `<tr>${cells}</tr>`;
    }).join('');
    target.innerHTML = `<table><thead><tr>${thead}</tr></thead><tbody>${tbody}</tbody></table>`;
}

function renderMetrics(targetId, items) {
    const target = document.getElementById(targetId);
    if (!target) { return; }
    if (!Array.isArray(items) || items.length === 0) {
        target.innerHTML = '<div class="empty">Gösterilecek metrik yok.</div>';
        return;
    }
    target.innerHTML = items.map((item) => {
        const itemClass = item.long ? 'metric-item stacked' : 'metric-item';
        const valueClass = item.long ? 'metric-value long' : 'metric-value';
        return `<div class="${itemClass}"><div class="metric-key">${escapeHtml(item.label)}</div><div class="${valueClass}" title="${escapeHtml(item.value)}">${escapeHtml(item.value)}</div></div>`;
    }).join('');
}

function renderWarnings(warnings) {
    const panel = document.getElementById('warnings-panel');
    const list = document.getElementById('warnings-list');
    if (!panel || !list) { return; }
    if (!Array.isArray(warnings) || warnings.length === 0) {
        panel.style.display = 'none';
        list.innerHTML = '';
        return;
    }
    panel.style.display = 'block';
    list.innerHTML = warnings.map((warning) => `<li>${escapeHtml(warning)}</li>`).join('');
}

function renderStatusBanner(service) {
    const banner = document.getElementById('status-banner');
    if (!banner) { return; }
    if (service && service.active === false) {
        banner.style.display = 'block';
        banner.textContent = 'Ghost Trader bot servisi aktif değil. Dashboard salt-okunur çalışmaya devam eder ama runtime sağlığı şu an degrade.';
        return;
    }
    banner.style.display = 'none';
    banner.textContent = '';
}

function updateHeader(payload) {
    const runtime = payload.runtime_summary || {};
    const service = payload.service || {};
    const verdictBlock = payload.swot_verdict?.final_verdict || {};

    document.getElementById('service-status').textContent = service.active ? 'ÇALIŞIYOR' : 'DEGRADE';
    document.getElementById('service-name').textContent = service.name || 'ghost-trader';
    document.getElementById('total-trades').textContent = formatNumber(runtime.total_trades, 0);
    document.getElementById('tracked-whales').textContent = formatNumber(runtime.tracked_whales, 0);
    document.getElementById('mapped-orderflow').textContent = `${formatNumber(runtime.mapped_orderflow_events, 0)} / ${formatNumber(runtime.unmapped_orderflow_events, 0)}`;
    document.getElementById('mapping-note').textContent = `Alias cache ${formatNumber(runtime.alias_cache_hits, 0)}, lazy ${formatNumber(runtime.lazy_lookup_hits, 0)}, sıcak pencere ${formatNumber(runtime.hot_window_hits, 0)}`;
    document.getElementById('market-not-mapped-rate').textContent = `${formatNumber(runtime.market_not_mapped_rate, 1)}%`;
    document.getElementById('final-verdict').textContent = translateVerdict(verdictBlock.verdict || 'yok');
    document.getElementById('verdict-reason').textContent = verdictBlock.reason || 'Henüz SWOT kararı yok.';
    document.getElementById('open-state-note').textContent = `${formatNumber(runtime.open_positions_count, 0)} açık pozisyon / ${formatNumber(runtime.open_orders_count, 0)} açık emir`;
    document.getElementById('last-refresh-badge').textContent = `Son yenileme ${new Date(payload.generated_at).toLocaleTimeString('tr-TR')}`;
}

function updatePanels(payload) {
    renderTable('recent-decisions', [
        { key: 'occurred_at', label: 'Zaman', mono: true, render: (row) => escapeHtml(row.occurred_at || '') },
        { key: 'source', label: 'Kaynak', render: (row) => `<span class="badge ${badgeClass(row.action || row.raw_source_signal)}" title="${escapeHtml(row.raw_source_signal || row.signal_family || 'unknown')}">${escapeHtml(row.raw_source_signal || row.signal_family || 'unknown')}</span>` },
        { key: 'strategy_profile', label: 'Profil', render: (row) => escapeHtml(translateStrategyProfile(row.strategy_profile || 'baseline')) },
        { key: 'category', label: 'Kategori', render: (row) => escapeHtml(translateCategory(row.category)) },
        { key: 'action', label: 'Aksiyon', render: (row) => `<span class="badge ${badgeClass(row.action)}">${escapeHtml(translateAction(row.action || 'n/a'))}</span>` },
        { key: 'mapping_stage', label: 'Aşama', render: (row) => escapeHtml(translateMappingStage(row.mapping_stage || 'yok')) },
        { key: 'reason', label: 'Neden', render: (row) => escapeHtml(translateReason(row.reason || row.mapping_stage || 'yok')) },
        { key: 'decision_score', label: 'Skor', render: (row) => escapeHtml(formatNumber(row.decision_score, 2)) },
        { key: 'trade_size', label: 'İşlem Boyutu', render: (row) => escapeHtml(formatNumber(row.trade_size, 2)) },
        { key: 'hot_window_promoted', label: 'Sıcak Pencere', render: (row) => escapeHtml(row.hot_window_promoted ? 'terfi etti' : 'yok') }
    ], payload.recent_decisions, 'Henüz karar denetim kaydı yok.');

    renderTable('open-positions', [
        { key: 'venue', label: 'Venue', render: (row) => escapeHtml(row.venue || 'yok') },
        { key: 'symbol_or_market_id', label: 'Sembol / Market', mono: true, render: (row) => truncateHtml(row.symbol_or_market_id || '', 30) },
        { key: 'side', label: 'Yön', render: (row) => escapeHtml(row.side || '') },
        { key: 'notional_usd', label: 'Notional', render: (row) => escapeHtml(formatNumber(row.notional_usd, 2)) },
        { key: 'unrealized_pnl', label: 'Gerç. Olmayan PnL', render: (row) => escapeHtml(formatNumber(row.unrealized_pnl, 2)) },
        { key: 'opened_at', label: 'Açılış', mono: true, render: (row) => escapeHtml(row.opened_at || '') }
    ], payload.open_positions, 'Açık pozisyon yok.');

    renderTable('open-orders', [
        { key: 'venue', label: 'Venue', render: (row) => escapeHtml(row.venue || '') },
        { key: 'symbol_or_market_id', label: 'Sembol / Market', mono: true, render: (row) => truncateHtml(row.symbol_or_market_id || '', 30) },
        { key: 'order_type', label: 'Tür', render: (row) => escapeHtml(row.order_type || '') },
        { key: 'side', label: 'Yön', render: (row) => escapeHtml(row.side || '') },
        { key: 'stop_price', label: 'Stop', render: (row) => escapeHtml(formatNumber(row.stop_price, 2)) },
        { key: 'qty', label: 'Miktar', render: (row) => escapeHtml(formatNumber(row.qty, 6)) }
    ], payload.open_orders, 'Açık emir yok.');

    renderTable('recent-trades', [
        { key: 'timestamp', label: 'Zaman', mono: true, render: (row) => escapeHtml(row.timestamp || '') },
        { key: 'venue', label: 'Venue', render: (row) => escapeHtml(row.venue || '') },
        { key: 'market_id', label: 'Market', mono: true, render: (row) => truncateHtml(row.market_id || '', 30) },
        { key: 'strategy_profile', label: 'Profil', render: (row) => escapeHtml(translateStrategyProfile(row.strategy_profile || 'baseline')) },
        { key: 'side', label: 'Yön', render: (row) => escapeHtml(row.side || '') },
        { key: 'size', label: 'Boyut', render: (row) => escapeHtml(formatNumber(row.size, 2)) },
        { key: 'status', label: 'Durum', render: (row) => `<span class="badge ${badgeClass(row.status)}">${escapeHtml(translateAction(row.status || ''))}</span>` }
    ], payload.recent_trades, 'Henüz kaydedilmiş işlem yok.');

    renderTable('venue-accounts', [
        { key: 'venue', label: 'Venue', render: (row) => escapeHtml(row.venue || '') },
        { key: 'execution_mode', label: 'Mod', render: (row) => escapeHtml(row.execution_mode || '') },
        { key: 'cash_balance', label: 'Nakit', render: (row) => escapeHtml(formatNumber(row.cash_balance, 2)) },
        { key: 'equity', label: 'Özsermaye', render: (row) => escapeHtml(formatNumber(row.equity, 2)) },
        { key: 'available_balance', label: 'Kullanılabilir', render: (row) => escapeHtml(formatNumber(row.available_balance, 2)) }
    ], payload.venue_accounts, 'Venue hesabı bulunamadı.');

    renderTable('whale-wallet-counts', [
        { key: 'source_type', label: 'Kaynak', render: (row) => escapeHtml(row.source_type || '') },
        { key: 'count', label: 'Adet', render: (row) => escapeHtml(formatNumber(row.count, 0)) }
    ], payload.whale_wallet_counts, 'Balina kaynak sayısı yok.');

    renderTable('top-whales', [
        { key: 'address', label: 'Adres', mono: true, render: (row) => truncateHtml(row.address || '', 28) },
        { key: 'source_type', label: 'Kaynak', render: (row) => escapeHtml(row.source_type || '') },
        { key: 'discovery_score', label: 'Skor', render: (row) => escapeHtml(formatNumber(row.discovery_score, 3)) },
        { key: 'event_count_24h', label: '24s Event', render: (row) => escapeHtml(formatNumber(row.event_count_24h, 0)) }
    ], payload.top_whales, 'Henüz sıralı balina verisi yok.');

    renderTable('market-alias-counts', [
        { key: 'source', label: 'Kaynak', render: (row) => escapeHtml(row.source || '') },
        { key: 'count', label: 'Adet', render: (row) => escapeHtml(formatNumber(row.count, 0)) }
    ], payload.market_alias_counts, 'Alias kaynak sayısı yok.');

    renderTable('top-market-aliases', [
        { key: 'alias', label: 'Alias', mono: true, render: (row) => truncateHtml(row.alias || '', 46) },
        { key: 'alias_type', label: 'Tür', render: (row) => escapeHtml(row.alias_type || '') },
        { key: 'category', label: 'Kategori', render: (row) => escapeHtml(translateCategory(row.category)) },
        { key: 'source', label: 'Kaynak', render: (row) => escapeHtml(row.source || '') }
    ], payload.top_market_aliases, 'Henüz cache’lenen market alias yok.');

    renderTable('top-unresolved-aliases', [
        { key: 'alias', label: 'Alias', mono: true, render: (row) => truncateHtml(row.alias || '', 42) },
        { key: 'count', label: 'Adet', render: (row) => escapeHtml(formatNumber(row.count, 0)) },
        { key: 'reason', label: 'Son Neden', render: (row) => escapeHtml(translateReason(row.reason || 'yok')) }
    ], payload.top_unresolved_aliases, 'Henüz çözülemeyen alias özeti yok.');

    renderTable('recent-unresolved-aliases', [
        { key: 'occurred_at', label: 'Zaman', mono: true, render: (row) => escapeHtml(row.occurred_at || '') },
        { key: 'mapping_stage', label: 'Aşama', render: (row) => escapeHtml(translateMappingStage(row.mapping_stage || 'yok')) },
        { key: 'reason', label: 'Neden', render: (row) => escapeHtml(translateReason(row.reason || 'yok')) },
        { key: 'aliases', label: 'Alias Adayları', mono: true, render: (row) => truncateHtml(Array.isArray(row.aliases) ? row.aliases.join(', ') : '', 56) }
    ], payload.recent_unresolved_aliases, 'Son çözülemeyen alias olayı yok.');

    const runtime = payload.runtime_summary || {};
    renderMetrics('mapping-health', [
        { label: 'Eşlenen orderflow event', value: formatNumber(runtime.mapped_orderflow_events, 0) },
        { label: 'Eşlenemeyen orderflow event', value: formatNumber(runtime.unmapped_orderflow_events, 0) },
        { label: 'Alias cache hit', value: formatNumber(runtime.alias_cache_hits, 0) },
        { label: 'Lazy lookup hit', value: formatNumber(runtime.lazy_lookup_hits, 0) },
        { label: 'Sıcak pencere marketleri', value: formatNumber(runtime.hot_window_markets, 0) },
        { label: 'Sıcak pencere hit', value: formatNumber(runtime.hot_window_hits, 0) },
        { label: 'Hot-window promotion', value: formatNumber(runtime.hot_window_promotions, 0) },
        { label: 'Hot-window expiry', value: formatNumber(runtime.hot_window_expiries, 0) },
        { label: 'Active-window miss', value: formatNumber(runtime.active_window_misses, 0) },
        { label: 'Active-window miss oranı', value: `${formatNumber(runtime.active_window_miss_rate, 1)}%` },
        { label: 'Resolver hit oranı', value: `${formatNumber(runtime.resolver_hit_rate, 1)}%` },
        { label: 'Market eşleşmedi oranı', value: `${formatNumber(runtime.market_not_mapped_rate, 1)}%` }
    ]);

    const performance = payload.performance_summary || {};
    const evidence = performance.evidence || {};
    const core = performance.core || {};
    const sampling = payload.sampling_summary || {};
    const verdict = payload.swot_verdict?.final_verdict || {};
    renderMetrics('performance-snapshot', [
        { label: 'Kapanmış live paper', value: formatNumber(evidence.live_paper_closed, 0) },
        { label: 'Sentetik örnek', value: formatNumber(evidence.synthetic_total, 0) },
        { label: 'Ana expectancy', value: core.expectancy === null || core.expectancy === undefined ? 'yok' : formatNumber(core.expectancy, 4) },
        { label: 'Sampling modu', value: translateSamplingMode(runtime.sampling_mode || 'disabled') },
        { label: 'Sampling profili', value: translateStrategyProfile(sampling.strategy_profile || 'sampling_relaxed') },
        { label: 'Sampling ilerlemesi', value: `${formatNumber(runtime.sampling_closed_trades, 0)} / ${formatNumber(runtime.sampling_target_closed_trades, 0)} kapanmış trade` },
        { label: 'Sampling win rate', value: sampling.win_rate === null || sampling.win_rate === undefined ? 'yok' : `${formatNumber(sampling.win_rate, 1)}%` },
        { label: 'Sampling expectancy', value: sampling.expectancy === null || sampling.expectancy === undefined ? 'yok' : formatNumber(sampling.expectancy, 4) },
        { label: 'Sampling toplam PnL', value: sampling.total_pnl === null || sampling.total_pnl === undefined ? 'yok' : formatNumber(sampling.total_pnl, 2) },
        { label: 'Sampling durma nedeni', value: translateReason(runtime.sampling_stop_reason || 'none') },
        { label: 'Nihai karar', value: translateVerdict(verdict.verdict || 'yok') },
        { label: 'Karar nedeni', value: verdict.reason || 'Henüz karar yok', long: true },
        { label: 'Sampling notu', value: 'Sampling sonuçları ana alpha kanıtı değildir; ayrı deney profili olarak izlenir.', long: true }
    ]);

    const logLines = Array.isArray(payload.service_log_excerpt) ? payload.service_log_excerpt : [];
    document.getElementById('service-log').textContent = logLines.length > 0 ? logLines.join('\n') : 'Servis logu alınamadı.';
}

async function refreshDashboard() {
    try {
        const response = await fetch(`api.php?view=full&_=${Date.now()}`, {
            headers: { 'Accept': 'application/json' },
            credentials: 'same-origin',
            cache: 'no-store'
        });
        if (!response.ok) {
            throw new Error(`API ${response.status} döndü`);
        }
        const payload = await response.json();
        renderWarnings(payload.warnings || []);
        renderStatusBanner(payload.service || {});
        updateHeader(payload);
        updatePanels(payload);
    } catch (error) {
        renderWarnings([`dashboard yenilemesi başarısız oldu: ${error.message}`]);
    }
}

refreshDashboard();
setInterval(refreshDashboard, refreshSeconds * 1000);
</script>
</body>
</html>
