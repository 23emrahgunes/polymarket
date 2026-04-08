<?php
declare(strict_types=1);

require_once dirname(__DIR__) . '/lib/bootstrap.php';
require_once dirname(__DIR__) . '/lib/auth.php';

dashboard_require_auth();

$refreshSeconds = max(dashboard_int_env('DASHBOARD_REFRESH_SECONDS', 5), 2);
?>
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Ghost Trader Ops Dashboard</title>
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
        }
        .stat-label { color: var(--muted); font-size: 0.84rem; letter-spacing: 0.08em; text-transform: uppercase; }
        .stat-value { margin-top: 14px; font-size: 1.8rem; font-weight: 700; line-height: 1; }
        .stat-note { margin-top: 10px; color: var(--muted); font-size: 0.92rem; }
        .grid { display: grid; grid-template-columns: repeat(12, minmax(0, 1fr)); gap: 16px; }
        .panel { background: var(--panel); border-radius: 18px; padding: 18px; }
        .panel-wide { grid-column: span 12; }
        .panel-half { grid-column: span 6; }
        .panel-third { grid-column: span 4; }
        .panel-header { display: flex; justify-content: space-between; gap: 12px; align-items: center; margin-bottom: 14px; }
        .panel h2 { margin: 0; font-size: 1rem; letter-spacing: 0.03em; text-transform: uppercase; }
        .panel-copy { margin: 0 0 14px; color: var(--muted); line-height: 1.5; font-size: 0.94rem; }
        .badge {
            display: inline-flex; align-items: center; gap: 8px; padding: 7px 12px; border-radius: 999px; font-size: 0.78rem;
            letter-spacing: 0.08em; text-transform: uppercase; border: 1px solid var(--line); background: rgba(255, 255, 255, 0.02); color: var(--text);
        }
        .badge.ok { color: var(--ok); border-color: rgba(125, 214, 139, 0.3); background: rgba(51, 94, 54, 0.24); }
        .badge.warn { color: var(--warn); border-color: rgba(240, 179, 90, 0.3); background: rgba(109, 76, 24, 0.24); }
        .badge.danger { color: #ffb0b0; border-color: rgba(239, 107, 107, 0.28); background: rgba(112, 36, 36, 0.28); }
        .badge.info { color: var(--accent); border-color: rgba(77, 199, 176, 0.28); background: rgba(14, 83, 74, 0.28); }
        table { width: 100%; border-collapse: collapse; font-size: 0.92rem; }
        th, td { padding: 10px 8px; text-align: left; border-bottom: 1px solid var(--line); vertical-align: top; }
        th { color: var(--muted); font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.08em; }
        .mono { font-family: "IBM Plex Mono", "Cascadia Mono", "Consolas", monospace; font-size: 0.88rem; }
        .empty { padding: 18px 0 8px; color: var(--muted); }
        pre {
            margin: 0; padding: 14px; border-radius: 14px; border: 1px solid var(--line); background: var(--panel-soft); overflow: auto;
            max-height: 360px; font-family: "IBM Plex Mono", "Cascadia Mono", "Consolas", monospace; font-size: 0.85rem; line-height: 1.55;
        }
        .metric-list { display: grid; gap: 10px; }
        .metric-item { display: flex; justify-content: space-between; gap: 12px; padding: 10px 0; border-bottom: 1px solid var(--line); }
        .metric-item:last-child { border-bottom: none; }
        .metric-key { color: var(--muted); }
        .metric-value { font-weight: 700; }
        @media (max-width: 1100px) { .panel-half, .panel-third { grid-column: span 12; } }
    </style>
</head>
<body>
<div class="shell">
    <section class="hero-card">
        <div class="hero-top">
            <div>
                <div class="eyebrow">Ghost Trader Ops</div>
                <h1>Live runtime, mapping health, and strategy evidence in one screen.</h1>
            </div>
            <div class="badge info" id="last-refresh-badge">Refreshing every <?= dashboard_html((string) $refreshSeconds) ?>s</div>
        </div>
        <p class="hero-sub">Read-only dashboard for runtime health, multi-venue state, decision audit flow, market mapping coverage, and conservative performance verdicts.</p>
    </section>

    <div class="status-banner" id="status-banner"></div>
    <div class="warnings" id="warnings-panel"><strong>Warnings</strong><ul id="warnings-list"></ul></div>

    <section class="stats">
        <article class="stat-card"><div class="stat-label">Service</div><div class="stat-value" id="service-status">...</div><div class="stat-note" id="service-name">ghost-trader</div></article>
        <article class="stat-card"><div class="stat-label">Total Trades</div><div class="stat-value" id="total-trades">0</div><div class="stat-note" id="open-state-note">0 open positions / 0 open orders</div></article>
        <article class="stat-card"><div class="stat-label">Tracked Whales</div><div class="stat-value" id="tracked-whales">0</div><div class="stat-note">Hybrid cache coverage</div></article>
        <article class="stat-card"><div class="stat-label">Mapped Orderflow</div><div class="stat-value" id="mapped-orderflow">0 / 0</div><div class="stat-note" id="mapping-note">Alias cache 0, lazy hits 0</div></article>
        <article class="stat-card"><div class="stat-label">Market Not Mapped</div><div class="stat-value" id="market-not-mapped-rate">0%</div><div class="stat-note">Lower is better</div></article>
        <article class="stat-card"><div class="stat-label">Final Verdict</div><div class="stat-value" id="final-verdict">...</div><div class="stat-note" id="verdict-reason">Awaiting report</div></article>
    </section>

    <section class="grid">
        <article class="panel panel-wide"><div class="panel-header"><h2>Recent Decisions</h2><span class="badge warn">Audit stream</span></div><p class="panel-copy">Latest reject, decision, execute, and exit events with source, reason, score, mapping stage, and trade size.</p><div id="recent-decisions"></div></article>
        <article class="panel panel-half"><div class="panel-header"><h2>Open Positions</h2><span class="badge info">Venue exposure</span></div><div id="open-positions"></div></article>
        <article class="panel panel-half"><div class="panel-header"><h2>Open Orders</h2><span class="badge info">Protection layer</span></div><div id="open-orders"></div></article>
        <article class="panel panel-half"><div class="panel-header"><h2>Recent Trades</h2><span class="badge info">SQLite runtime history</span></div><div id="recent-trades"></div></article>
        <article class="panel panel-half"><div class="panel-header"><h2>Venue Accounts</h2><span class="badge info">Paper balances</span></div><div id="venue-accounts"></div></article>
        <article class="panel panel-third"><div class="panel-header"><h2>Mapping Health</h2><span class="badge info">Coverage</span></div><div class="metric-list" id="mapping-health"></div><div id="top-market-aliases" style="margin-top:14px;"></div></article>
        <article class="panel panel-third"><div class="panel-header"><h2>Whale Source</h2><span class="badge info">Hybrid cache</span></div><div id="whale-wallet-counts"></div><div id="top-whales" style="margin-top:14px;"></div></article>
        <article class="panel panel-third"><div class="panel-header"><h2>Performance Snapshot</h2><span class="badge warn">Evidence</span></div><div class="metric-list" id="performance-snapshot"></div></article>
        <article class="panel panel-wide"><div class="panel-header"><h2>Service Log Excerpt</h2><span class="badge info">Best effort</span></div><pre id="service-log">Loading...</pre></article>
    </section>
</div>
<script>
const refreshSeconds = <?= json_encode($refreshSeconds, JSON_UNESCAPED_SLASHES) ?>;
function escapeHtml(value) { return String(value ?? '').replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll("'", '&#39;'); }
function formatNumber(value, digits = 1) { if (value === null || value === undefined || value === '' || Number.isNaN(Number(value))) { return 'n/a'; } return Number(value).toLocaleString(undefined, { maximumFractionDigits: digits, minimumFractionDigits: 0 }); }
function badgeClass(label) { const text = String(label ?? '').toUpperCase(); if (text.includes('RUN') || text.includes('ACTIVE') || text.includes('GO')) { return 'ok'; } if (text.includes('REJECT') || text.includes('IMPROVE') || text.includes('WARN')) { return 'warn'; } if (text.includes('FAIL') || text.includes('ERROR') || text.includes('NO-GO')) { return 'danger'; } return 'info'; }
function renderTable(targetId, columns, rows, emptyMessage) { const target = document.getElementById(targetId); if (!target) { return; } if (!Array.isArray(rows) || rows.length === 0) { target.innerHTML = `<div class="empty">${escapeHtml(emptyMessage)}</div>`; return; } const thead = columns.map((column) => `<th>${escapeHtml(column.label)}</th>`).join(''); const tbody = rows.map((row) => { const cells = columns.map((column) => { const raw = typeof column.render === 'function' ? column.render(row) : row[column.key]; return `<td class="${column.mono ? 'mono' : ''}">${raw ?? ''}</td>`; }).join(''); return `<tr>${cells}</tr>`; }).join(''); target.innerHTML = `<table><thead><tr>${thead}</tr></thead><tbody>${tbody}</tbody></table>`; }
function renderMetrics(targetId, items) { const target = document.getElementById(targetId); if (!target) { return; } if (!Array.isArray(items) || items.length === 0) { target.innerHTML = '<div class="empty">No metrics available.</div>'; return; } target.innerHTML = items.map((item) => `<div class="metric-item"><div class="metric-key">${escapeHtml(item.label)}</div><div class="metric-value">${escapeHtml(item.value)}</div></div>`).join(''); }
function renderWarnings(warnings) { const panel = document.getElementById('warnings-panel'); const list = document.getElementById('warnings-list'); if (!panel || !list) { return; } if (!Array.isArray(warnings) || warnings.length === 0) { panel.style.display = 'none'; list.innerHTML = ''; return; } panel.style.display = 'block'; list.innerHTML = warnings.map((warning) => `<li>${escapeHtml(warning)}</li>`).join(''); }
function renderStatusBanner(service) { const banner = document.getElementById('status-banner'); if (!banner) { return; } if (service && service.active === false) { banner.style.display = 'block'; banner.textContent = 'Ghost Trader bot service is not active. Dashboard stays read-only, but runtime health is degraded.'; return; } banner.style.display = 'none'; banner.textContent = ''; }
function updateHeader(payload) { const runtime = payload.runtime_summary || {}; const service = payload.service || {}; const verdictBlock = payload.swot_verdict?.final_verdict || {}; document.getElementById('service-status').textContent = service.active ? 'RUNNING' : 'DEGRADED'; document.getElementById('service-name').textContent = service.name || 'ghost-trader'; document.getElementById('total-trades').textContent = formatNumber(runtime.total_trades, 0); document.getElementById('tracked-whales').textContent = formatNumber(runtime.tracked_whales, 0); document.getElementById('mapped-orderflow').textContent = `${formatNumber(runtime.mapped_orderflow_events, 0)} / ${formatNumber(runtime.unmapped_orderflow_events, 0)}`; document.getElementById('mapping-note').textContent = `Alias cache ${formatNumber(runtime.alias_cache_hits, 0)}, lazy hits ${formatNumber(runtime.lazy_lookup_hits, 0)}`; document.getElementById('market-not-mapped-rate').textContent = `${formatNumber(runtime.market_not_mapped_rate, 1)}%`; document.getElementById('final-verdict').textContent = verdictBlock.verdict || 'N/A'; document.getElementById('verdict-reason').textContent = verdictBlock.reason || 'No SWOT verdict yet.'; document.getElementById('open-state-note').textContent = `${formatNumber(runtime.open_positions_count, 0)} open positions / ${formatNumber(runtime.open_orders_count, 0)} open orders`; document.getElementById('last-refresh-badge').textContent = `Last refresh ${new Date(payload.generated_at).toLocaleTimeString()}`; }
function updatePanels(payload) {
renderTable('recent-decisions', [{ key: 'occurred_at', label: 'Time', mono: true, render: (row) => escapeHtml(row.occurred_at || '') }, { key: 'source', label: 'Source', render: (row) => `<span class="badge ${badgeClass(row.action || row.raw_source_signal)}">${escapeHtml(row.raw_source_signal || row.signal_family || 'unknown')}</span>` }, { key: 'category', label: 'Category', render: (row) => escapeHtml(row.category || 'n/a') }, { key: 'action', label: 'Action', render: (row) => `<span class="badge ${badgeClass(row.action)}">${escapeHtml(row.action || 'n/a')}</span>` }, { key: 'reason', label: 'Reason', render: (row) => escapeHtml(row.reason || row.mapping_stage || 'n/a') }, { key: 'decision_score', label: 'Score', render: (row) => escapeHtml(formatNumber(row.decision_score, 2)) }, { key: 'trade_size', label: 'Trade Size', render: (row) => escapeHtml(formatNumber(row.trade_size, 2)) }], payload.recent_decisions, 'No decision audit rows yet.');
renderTable('open-positions', [{ key: 'venue', label: 'Venue', render: (row) => escapeHtml(row.venue || 'n/a') }, { key: 'symbol_or_market_id', label: 'Symbol / Market', mono: true, render: (row) => escapeHtml(row.symbol_or_market_id || '') }, { key: 'side', label: 'Side', render: (row) => escapeHtml(row.side || '') }, { key: 'notional_usd', label: 'Notional', render: (row) => escapeHtml(formatNumber(row.notional_usd, 2)) }, { key: 'unrealized_pnl', label: 'U-PnL', render: (row) => escapeHtml(formatNumber(row.unrealized_pnl, 2)) }, { key: 'opened_at', label: 'Opened', mono: true, render: (row) => escapeHtml(row.opened_at || '') }], payload.open_positions, 'No open positions.');
renderTable('open-orders', [{ key: 'venue', label: 'Venue', render: (row) => escapeHtml(row.venue || '') }, { key: 'symbol_or_market_id', label: 'Symbol / Market', mono: true, render: (row) => escapeHtml(row.symbol_or_market_id || '') }, { key: 'order_type', label: 'Type', render: (row) => escapeHtml(row.order_type || '') }, { key: 'side', label: 'Side', render: (row) => escapeHtml(row.side || '') }, { key: 'stop_price', label: 'Stop', render: (row) => escapeHtml(formatNumber(row.stop_price, 2)) }, { key: 'qty', label: 'Qty', render: (row) => escapeHtml(formatNumber(row.qty, 6)) }], payload.open_orders, 'No open orders.');
renderTable('recent-trades', [{ key: 'timestamp', label: 'Time', mono: true, render: (row) => escapeHtml(row.timestamp || '') }, { key: 'venue', label: 'Venue', render: (row) => escapeHtml(row.venue || '') }, { key: 'market_id', label: 'Market', mono: true, render: (row) => escapeHtml(row.market_id || '') }, { key: 'side', label: 'Side', render: (row) => escapeHtml(row.side || '') }, { key: 'size', label: 'Size', render: (row) => escapeHtml(formatNumber(row.size, 2)) }, { key: 'status', label: 'Status', render: (row) => `<span class="badge ${badgeClass(row.status)}">${escapeHtml(row.status || '')}</span>` }], payload.recent_trades, 'No trades recorded yet.');
renderTable('venue-accounts', [{ key: 'venue', label: 'Venue', render: (row) => escapeHtml(row.venue || '') }, { key: 'execution_mode', label: 'Mode', render: (row) => escapeHtml(row.execution_mode || '') }, { key: 'cash_balance', label: 'Cash', render: (row) => escapeHtml(formatNumber(row.cash_balance, 2)) }, { key: 'equity', label: 'Equity', render: (row) => escapeHtml(formatNumber(row.equity, 2)) }, { key: 'available_balance', label: 'Available', render: (row) => escapeHtml(formatNumber(row.available_balance, 2)) }], payload.venue_accounts, 'No venue accounts found.');
renderTable('whale-wallet-counts', [{ key: 'source_type', label: 'Source', render: (row) => escapeHtml(row.source_type || '') }, { key: 'count', label: 'Count', render: (row) => escapeHtml(formatNumber(row.count, 0)) }], payload.whale_wallet_counts, 'No whale wallet counts available.');
renderTable('top-whales', [{ key: 'address', label: 'Address', mono: true, render: (row) => escapeHtml(row.address || '') }, { key: 'source_type', label: 'Source', render: (row) => escapeHtml(row.source_type || '') }, { key: 'discovery_score', label: 'Score', render: (row) => escapeHtml(formatNumber(row.discovery_score, 3)) }, { key: 'event_count_24h', label: '24h Events', render: (row) => escapeHtml(formatNumber(row.event_count_24h, 0)) }], payload.top_whales, 'No whales ranked yet.');
renderTable('top-market-aliases', [{ key: 'alias', label: 'Alias', mono: true, render: (row) => escapeHtml(row.alias || '') }, { key: 'alias_type', label: 'Type', render: (row) => escapeHtml(row.alias_type || '') }, { key: 'category', label: 'Category', render: (row) => escapeHtml(row.category || '') }, { key: 'source', label: 'Source', render: (row) => escapeHtml(row.source || '') }], payload.top_market_aliases, 'No market aliases cached yet.');
const runtime = payload.runtime_summary || {}; renderMetrics('mapping-health', [{ label: 'Mapped orderflow events', value: formatNumber(runtime.mapped_orderflow_events, 0) }, { label: 'Unmapped orderflow events', value: formatNumber(runtime.unmapped_orderflow_events, 0) }, { label: 'Alias cache hits', value: formatNumber(runtime.alias_cache_hits, 0) }, { label: 'Lazy lookup hits', value: formatNumber(runtime.lazy_lookup_hits, 0) }, { label: 'Market not mapped rate', value: `${formatNumber(runtime.market_not_mapped_rate, 1)}%` }]);
const performance = payload.performance_summary || {}; const evidence = performance.evidence || {}; const core = performance.core || {}; const verdict = payload.swot_verdict?.final_verdict || {}; renderMetrics('performance-snapshot', [{ label: 'Live paper closed', value: formatNumber(evidence.live_paper_closed, 0) }, { label: 'Synthetic samples', value: formatNumber(evidence.synthetic_total, 0) }, { label: 'Core expectancy', value: core.expectancy === null || core.expectancy === undefined ? 'n/a' : formatNumber(core.expectancy, 4) }, { label: 'Final verdict', value: verdict.verdict || 'n/a' }, { label: 'Verdict reason', value: verdict.reason || 'No verdict yet' }]);
const logLines = Array.isArray(payload.service_log_excerpt) ? payload.service_log_excerpt : []; document.getElementById('service-log').textContent = logLines.length > 0 ? logLines.join('\n') : 'Service log unavailable.'; }
async function refreshDashboard() { try { const response = await fetch(`api.php?view=full&_=${Date.now()}`, { headers: { 'Accept': 'application/json' }, credentials: 'same-origin', cache: 'no-store' }); if (!response.ok) { throw new Error(`API returned ${response.status}`); } const payload = await response.json(); renderWarnings(payload.warnings || []); renderStatusBanner(payload.service || {}); updateHeader(payload); updatePanels(payload); } catch (error) { renderWarnings([`dashboard refresh failed: ${error.message}`]); } }
refreshDashboard(); setInterval(refreshDashboard, refreshSeconds * 1000);
</script>
</body>
</html>
