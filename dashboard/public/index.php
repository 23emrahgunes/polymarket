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
        .shell { max-width: 1680px; margin: 0 auto; padding: 28px 18px 36px; }
        .hero-card, .stat-card, .panel, .tabs-shell {
            border: 1px solid var(--line);
            box-shadow: var(--shadow);
            backdrop-filter: blur(10px);
        }
        .tabs-shell {
            background: rgba(7, 18, 28, 0.98);
            border-radius: 18px;
            padding: 14px;
            margin-bottom: 18px;
        }
        .tabs-bar {
            display: flex;
            gap: 10px;
            flex-wrap: nowrap;
            overflow-x: auto;
            padding-bottom: 6px;
        }
        .tab-button {
            appearance: none;
            border: 1px solid var(--line);
            background: rgba(255, 255, 255, 0.02);
            color: var(--muted);
            border-radius: 999px;
            padding: 10px 14px;
            font: inherit;
            font-size: 0.88rem;
            cursor: pointer;
            white-space: nowrap;
            transition: background 120ms ease, color 120ms ease, border-color 120ms ease;
        }
        .tab-button.active {
            color: var(--text);
            background: rgba(77, 199, 176, 0.16);
            border-color: rgba(77, 199, 176, 0.32);
        }
        .tab-help-card {
            margin-top: 12px;
            border: 1px solid rgba(132, 181, 205, 0.12);
            background: rgba(13, 29, 41, 0.9);
            border-radius: 14px;
            padding: 14px;
        }
        .tab-help-copy {
            margin: 0 0 10px;
            color: var(--muted);
            line-height: 1.55;
            font-size: 0.92rem;
        }
        .glossary-list {
            display: grid;
            gap: 8px;
        }
        .glossary-item {
            border-top: 1px solid var(--line);
            padding-top: 8px;
        }
        .glossary-item:first-child {
            border-top: 0;
            padding-top: 0;
        }
        .glossary-term {
            display: block;
            font-weight: 700;
            margin-bottom: 3px;
        }
        .glossary-meaning {
            color: var(--muted);
            line-height: 1.5;
            font-size: 0.9rem;
        }
        .is-hidden {
            display: none !important;
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
        .section-marker {
            grid-column: span 12;
            display: flex;
            align-items: center;
            gap: 12px;
            margin-top: 6px;
        }
        .section-marker::after {
            content: "";
            flex: 1;
            height: 1px;
            background: linear-gradient(90deg, rgba(77, 199, 176, 0.28), rgba(132, 181, 205, 0.06));
        }
        .section-label {
            color: var(--muted);
            font-size: 0.82rem;
            letter-spacing: 0.12em;
            text-transform: uppercase;
            white-space: nowrap;
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
        .panel-tertiary { grid-column: span 3; }
        .panel-primary { grid-column: span 5; }
        .panel-secondary { grid-column: span 7; }
        .panel-header { display: flex; justify-content: space-between; gap: 12px; align-items: center; margin-bottom: 14px; }
        .panel h2 { margin: 0; font-size: 1rem; letter-spacing: 0.03em; text-transform: uppercase; }
        .panel-copy { margin: 0 0 14px; color: var(--muted); line-height: 1.5; font-size: 0.94rem; }
        .panel-subgrid {
            display: grid;
            gap: 12px;
            align-content: start;
        }
        .panel-inline-grid {
            display: grid;
            grid-template-columns: 1fr;
            gap: 12px;
            align-items: start;
        }
        .subcard {
            border: 1px solid rgba(132, 181, 205, 0.12);
            background: rgba(13, 29, 41, 0.9);
            border-radius: 14px;
            padding: 14px;
            min-height: 0;
            overflow: hidden;
        }
        .fold-card {
            border: 1px solid rgba(132, 181, 205, 0.12);
            background: rgba(13, 29, 41, 0.9);
            border-radius: 14px;
            overflow: hidden;
        }
        .fold-summary {
            list-style: none;
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 12px;
            padding: 14px;
            cursor: pointer;
        }
        .fold-summary::-webkit-details-marker { display: none; }
        .fold-summary::after {
            content: "Ac";
            color: var(--muted);
            font-size: 0.78rem;
            letter-spacing: 0.08em;
            text-transform: uppercase;
        }
        .fold-card[open] .fold-summary::after { content: "Kapat"; }
        .fold-body {
            border-top: 1px solid rgba(132, 181, 205, 0.1);
            padding: 0 14px 14px;
        }
        .fold-body .subcard {
            border: 0;
            background: transparent;
            padding: 14px 0 0;
            box-shadow: none;
        }
        .subcard-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 10px;
            margin-bottom: 10px;
        }
        .subcard-title {
            margin: 0;
            color: var(--muted);
            font-size: 0.8rem;
            letter-spacing: 0.08em;
            text-transform: uppercase;
        }
        .subcard-copy {
            margin: 0 0 10px;
            color: var(--muted);
            line-height: 1.45;
            font-size: 0.9rem;
        }
        .subcard-scroll {
            min-height: 0;
            overflow: auto;
        }
        .subcard .subsection-title {
            margin-top: 0;
        }
        .subcard .panel-copy {
            margin-bottom: 10px;
            font-size: 0.9rem;
        }
        .subsection-title {
            margin: 16px 0 8px;
            color: var(--muted);
            font-size: 0.8rem;
            letter-spacing: 0.08em;
            text-transform: uppercase;
        }
        #market-alias-counts,
        #top-market-aliases,
        #top-unresolved-aliases,
        #recent-unresolved-aliases,
        #routing-breakdown,
        #sampling-decision-summary,
        #mapping-miss-breakdown,
        #source-quality-summary,
        #unsupported-side-summary,
        #alias-persistence-summary,
        #whale-universe-summary,
        #whale-wallet-counts,
        #trusted-whale-summary,
        #top-whales,
        #whale-copy-summary,
        #whale-copy-recovery-summary,
        #binance-technical-summary,
        #binance-technical-fresh-summary,
        #binance-technical-gate-funnel,
        #binance-technical-fresh-gate-funnel,
        #binance-technical-recovery-summary,
        #binance-technical-fresh-recovery-summary,
        #binance-technical-score-component-summary,
        #binance-technical-score-gap-summary,
        #binance-technical-fresh-score-gap-summary,
        #binance-technical-stale-eligibility-summary,
        #binance-technical-position-pressure-summary,
        #graph-discovery-summary,
        #whale-candidate-aggregation-summary,
        #recent-gate-ready-candidates,
        #gated-reject-breakdown,
        #relaxed-gate-reject-breakdown,
        #binance-technical-reject-breakdown,
        #binance-technical-fresh-reject-breakdown,
        #performance-snapshot,
        #sampling-reject-breakdown {
            border: 1px solid rgba(132, 181, 205, 0.12);
            background: rgba(13, 29, 41, 0.9);
            border-radius: 14px;
            padding: 12px;
        }
        #top-whales,
        #trusted-whale-summary,
        #gated-reject-breakdown,
        #relaxed-gate-reject-breakdown,
        #sampling-reject-breakdown {
            margin-top: 12px;
        }
        .subcard #market-alias-counts,
        .subcard #top-market-aliases,
        .subcard #top-unresolved-aliases,
        .subcard #recent-unresolved-aliases,
        .subcard #routing-breakdown,
        .subcard #sampling-decision-summary,
        .subcard #mapping-miss-breakdown,
        .subcard #source-quality-summary,
        .subcard #unsupported-side-summary,
        .subcard #alias-persistence-summary,
        .subcard #whale-universe-summary,
        .subcard #whale-wallet-counts,
        .subcard #trusted-whale-summary,
        .subcard #top-whales,
        .subcard #whale-copy-summary,
        .subcard #whale-copy-recovery-summary,
        .subcard #binance-technical-summary,
        .subcard #binance-technical-fresh-summary,
        .subcard #binance-technical-gate-funnel,
        .subcard #binance-technical-fresh-gate-funnel,
        .subcard #binance-technical-recovery-summary,
        .subcard #binance-technical-fresh-recovery-summary,
        .subcard #binance-technical-score-component-summary,
        .subcard #binance-technical-score-gap-summary,
        .subcard #binance-technical-fresh-score-gap-summary,
        .subcard #binance-technical-position-pressure-summary,
        .subcard #graph-discovery-summary,
        .subcard #whale-candidate-aggregation-summary,
        .subcard #recent-gate-ready-candidates,
        .subcard #gated-reject-breakdown,
        .subcard #relaxed-gate-reject-breakdown,
        .subcard #binance-technical-reject-breakdown,
        .subcard #binance-technical-fresh-reject-breakdown,
        .subcard #performance-snapshot,
        .subcard #sampling-reject-breakdown {
            border: 0;
            background: transparent;
            border-radius: 0;
            padding: 0;
            margin-top: 0;
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
        #market-alias-counts, #whale-wallet-counts, #unsupported-side-summary { max-height: 180px; overflow: auto; }
        #top-market-aliases, #top-whales, #trusted-whale-summary { max-height: 320px; overflow: auto; }
        #top-unresolved-aliases, #recent-unresolved-aliases { max-height: 220px; overflow: auto; }
        #performance-snapshot, #source-quality-summary, #alias-persistence-summary, #whale-universe-summary, #whale-copy-summary, #whale-copy-recovery-summary, #binance-technical-summary, #binance-technical-fresh-summary, #binance-technical-gate-funnel, #binance-technical-fresh-gate-funnel, #binance-technical-recovery-summary, #binance-technical-fresh-recovery-summary, #binance-technical-score-component-summary, #binance-technical-score-gap-summary, #binance-technical-fresh-score-gap-summary, #binance-technical-stale-eligibility-summary, #binance-technical-legacy-position-shape-summary, #binance-technical-position-pressure-summary, #graph-discovery-summary, #whale-candidate-aggregation-summary { max-height: 420px; overflow: auto; }
        #recent-gate-ready-candidates { max-height: 260px; overflow: auto; }
        #sampling-reject-breakdown, #gated-reject-breakdown, #relaxed-gate-reject-breakdown, #binance-technical-reject-breakdown, #binance-technical-fresh-reject-breakdown, #binance-technical-score-blocker-breakdown { max-height: 220px; overflow: auto; }
        #service-log { max-height: 280px; }
        #recent-decisions table { min-width: 1040px; table-layout: auto; }
        #top-whales table, #trusted-whale-summary table { min-width: 900px; table-layout: auto; }
        #top-market-aliases table,
        #recent-unresolved-aliases table,
        #sampling-reject-breakdown table,
        #gated-reject-breakdown table,
        #relaxed-gate-reject-breakdown table { min-width: 560px; table-layout: auto; }
        @media (max-width: 1100px) {
            .panel-half, .panel-third, .panel-tertiary, .panel-primary, .panel-secondary { grid-column: span 12; }
            .panel-inline-grid { grid-template-columns: 1fr; }
        }
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

    <div class="section-marker" data-tab="genel-bakis"><span class="section-label">Özet</span></div>
    <section class="stats">
        <article class="stat-card"><div class="stat-label">Servis</div><div class="stat-value" id="service-status">...</div><div class="stat-note" id="service-name">ghost-trader</div></article>
        <article class="stat-card"><div class="stat-label">Toplam İşlem</div><div class="stat-value" id="total-trades">0</div><div class="stat-note" id="open-state-note">0 açık pozisyon / 0 açık emir</div></article>
        <article class="stat-card"><div class="stat-label">İzlenen Balinalar</div><div class="stat-value" id="tracked-whales">0</div><div class="stat-note">Hibrit cache kapsamı</div></article>
        <article class="stat-card"><div class="stat-label">Eşlenen Orderflow</div><div class="stat-value" id="mapped-orderflow">0 / 0</div><div class="stat-note" id="mapping-note">Alias cache 0, lazy hit 0</div></article>
        <article class="stat-card"><div class="stat-label">Market Eşleşmedi</div><div class="stat-value" id="market-not-mapped-rate">0%</div><div class="stat-note">Daha düşük daha iyi</div></article>
        <article class="stat-card"><div class="stat-label">Nihai Karar</div><div class="stat-value" id="final-verdict">...</div><div class="stat-note" id="verdict-reason">Rapor bekleniyor</div></article>
    </section>

    <section class="tabs-shell" id="dashboard-tabs">
        <div class="tabs-bar" role="tablist" aria-label="Dashboard sekmeleri">
            <button class="tab-button" type="button" data-tab-trigger="genel-bakis">Genel Bakis</button>
            <button class="tab-button" type="button" data-tab-trigger="polymarket">Polymarket</button>
            <button class="tab-button" type="button" data-tab-trigger="binance-teknik">Binance Teknik</button>
            <button class="tab-button" type="button" data-tab-trigger="pozisyonlar-risk">Pozisyonlar ve Risk</button>
            <button class="tab-button" type="button" data-tab-trigger="teshis-log">Teshis ve Log</button>
        </div>
        <div class="tab-help-card">
            <div class="subcard-header">
                <h3 class="subcard-title">Bu sekme neyi gosteriyor?</h3>
                <span class="badge info" id="tab-help-badge">Genel Bakis</span>
            </div>
            <p class="tab-help-copy" id="tab-help-copy">Bu sekme botun canli durumunu, son kararlari ve hizli genel resmi gosterir.</p>
            <div class="glossary-list" id="tab-glossary">
                <div class="glossary-item"><strong>Sure baskisiyla cikis</strong><span>Teknik destek zayiflarsa uzun sure acik kalan paper pozisyon kapatilir.</span></div>
                <div class="glossary-item"><strong>Uzun sure acik kaldigi icin cikis</strong><span>Hard-timeout sinirina takilan ve guncel destek bulamayan pozisyon kapatilir.</span></div>
            </div>
        </div>
    </section>

    <section class="grid">
        <div class="section-marker" data-tabs="genel-bakis,pozisyonlar-risk"><span class="section-label">İşlem ve Karar Akışı</span></div>
        <article class="panel panel-wide" data-tab="genel-bakis"><div class="panel-header"><h2>Son Kararlar</h2><span class="badge warn">Denetim Akışı</span></div><p class="panel-copy">Kaynak, neden, skor, mapping aşaması ve işlem boyutuyla birlikte son red, karar, işlem ve çıkış olayları.</p><div id="recent-decisions"></div></article>
        <article class="panel panel-half" data-tab="pozisyonlar-risk"><div class="panel-header"><h2>Açık Pozisyonlar</h2><span class="badge info">Venue Maruziyeti</span></div><div id="open-positions"></div></article>
        <article class="panel panel-half" data-tab="pozisyonlar-risk"><div class="panel-header"><h2>Açık Emirler</h2><span class="badge info">Koruma Katmanı</span></div><div id="open-orders"></div></article>
        <article class="panel panel-half" data-tab="pozisyonlar-risk"><div class="panel-header"><h2>Son İşlemler</h2><span class="badge info">SQLite Çalışma Geçmişi</span></div><div id="recent-trades"></div></article>
        <article class="panel panel-half" data-tab="pozisyonlar-risk"><div class="panel-header"><h2>Venue Hesapları</h2><span class="badge info">Paper Bakiyeleri</span></div><div id="venue-accounts"></div></article>
        <div class="section-marker" data-tabs="polymarket,binance-teknik,pozisyonlar-risk,teshis-log"><span class="section-label">Teşhis ve Kanıt</span></div>
        <article class="panel panel-wide" data-tab="polymarket">
            <div class="panel-header"><h2>Mapping Sağlığı</h2><span class="badge info">Kapsam</span></div>
            <div class="panel-subgrid">
                <div class="subcard">
                    <div class="subcard-header"><h3 class="subcard-title">Ana Metrikler</h3><span class="badge info">Resolver</span></div>
                    <div class="metric-list" id="mapping-health"></div>
                </div>
                <div class="panel-inline-grid">
                    <div class="subcard">
                        <div class="subcard-header"><h3 class="subcard-title">Akış Ayrımı</h3><span class="badge info">Yol</span></div>
                        <div id="routing-breakdown"></div>
                    </div>
                    <div class="subcard">
                        <div class="subcard-header"><h3 class="subcard-title">Sampling Karar Özeti</h3><span class="badge warn">Paper</span></div>
                        <div id="sampling-decision-summary"></div>
                    </div>
                </div>
                <div class="subcard">
                    <div class="subcard-header"><h3 class="subcard-title">Kaynak Kalitesi Özeti</h3><span class="badge info">Orderflow</span></div>
                    <div class="metric-list" id="source-quality-summary"></div>
                </div>
                <div class="subcard">
                    <div class="subcard-header"><h3 class="subcard-title">Mapping Miss Nedenleri</h3><span class="badge warn">Tanı</span></div>
                    <div id="mapping-miss-breakdown"></div>
                </div>
                <div class="subcard">
                    <div class="subcard-header"><h3 class="subcard-title">Desteklenmeyen Yön Filtresi</h3><span class="badge warn">Side</span></div>
                    <div id="unsupported-side-summary"></div>
                </div>
                <div class="subcard">
                    <div class="subcard-header"><h3 class="subcard-title">Alias Cache Bütünlüğü</h3><span class="badge info">Write-through</span></div>
                    <div class="metric-list" id="alias-persistence-summary"></div>
                </div>
                <div class="panel-inline-grid">
                    <div class="subcard">
                        <div class="subcard-header"><h3 class="subcard-title">Alias Kaynakları</h3><span class="badge info">Cache</span></div>
                        <div id="market-alias-counts"></div>
                    </div>
                    <div class="subcard">
                        <div class="subcard-header"><h3 class="subcard-title">En Güçlü Alias Cache</h3><span class="badge info">Top</span></div>
                        <div class="subcard-scroll"><div id="top-market-aliases"></div></div>
                    </div>
                </div>
                <div class="panel-inline-grid">
                    <div class="subcard">
                        <div class="subcard-header"><h3 class="subcard-title">En Sık Çözülemeyen Alias’lar</h3><span class="badge warn">Miss</span></div>
                        <div class="subcard-scroll"><div id="top-unresolved-aliases"></div></div>
                    </div>
                    <div class="subcard">
                        <div class="subcard-header"><h3 class="subcard-title">Son Çözülemeyen Alias Olayları</h3><span class="badge warn">Son Olaylar</span></div>
                        <div class="subcard-scroll"><div id="recent-unresolved-aliases"></div></div>
                    </div>
                </div>
            </div>
        </article>
        <article class="panel panel-wide" data-tab="polymarket">
            <div class="panel-header"><h2>Balina Kaynağı</h2><span class="badge info">Hibrit Cache</span></div>
            <p class="panel-copy">Keşif skoru, whale adresini sıralamak için kullanılır; başarı oranı değildir. Güven skoru ve kazanma oranı yalnızca kapanmış canlı işlemlerden öğrenilir.</p>
            <div class="panel-subgrid">
                <div class="subcard">
                    <div class="subcard-header"><h3 class="subcard-title">Kaynak Sayıları</h3><span class="badge info">Havuz</span></div>
                    <p class="subcard-copy">Balina Evreni Ozeti: leaderboard, activity, graph-discovery ve kanitli balina ayrimi.</p>
                    <div class="metric-list" id="whale-universe-summary"></div>
                    <div id="whale-wallet-counts"></div>
                </div>
                <div class="subcard">
                    <div class="subcard-header"><h3 class="subcard-title">Kanitli Balinalar</h3><span class="badge info">Trust</span></div>
                    <div class="subcard-scroll"><div id="trusted-whale-summary"></div></div>
                </div>
                <div class="subcard">
                    <div class="subcard-header"><h3 class="subcard-title">Balina Tablosu</h3><span class="badge info">Skor ve Güven</span></div>
                    <div class="subcard-scroll"><div id="top-whales"></div></div>
                </div>
            </div>
        </article>
        <article class="panel panel-wide" data-tabs="genel-bakis,polymarket,binance-teknik,pozisyonlar-risk,teshis-log">
            <div class="panel-header"><h2>Performans Özeti</h2><span class="badge warn">Kanıt</span></div>
            <div class="panel-subgrid">
                <div class="subcard" data-tabs="genel-bakis,binance-teknik,pozisyonlar-risk">
                    <div class="subcard-header"><h3 class="subcard-title">Strateji ve Sampling</h3><span class="badge warn">Özet</span></div>
                    <p class="subcard-copy">Bu alan yalnizca copy_policy=gated_whale_copy ile isaretlenmis eventleri sayar; generic whale/activity redlerini icermez.</p>
                    <div class="metric-list" id="whale-copy-summary"></div>
                    <div class="subsection-title">Whale-copy recovery özeti</div>
                    <div class="metric-list" id="whale-copy-recovery-summary"></div>
                    <div class="subsection-title">Binance teknik sampling</div>
                    <div class="metric-list" id="binance-technical-summary"></div>
                    <div class="subsection-title">Taze teknik ozet (son 60 dk)</div>
                    <div class="metric-list" id="binance-technical-fresh-summary"></div>
                    <div class="subsection-title">Binance teknik gate funnel</div>
                    <div class="metric-list" id="binance-technical-gate-funnel"></div>
                    <div class="subsection-title">Taze teknik gate funnel</div>
                    <div class="metric-list" id="binance-technical-fresh-gate-funnel"></div>
                    <div class="subsection-title">Binance teknik recovery ozeti</div>
                    <div class="metric-list" id="binance-technical-recovery-summary"></div>
                    <div class="subsection-title">Taze teknik recovery ozeti</div>
                    <div class="metric-list" id="binance-technical-fresh-recovery-summary"></div>
                    <div class="subsection-title">Futures snapshot ozeti</div>
                    <div class="metric-list" id="binance-futures-snapshot-summary"></div>
                    <div class="subsection-title">Skor bilesen ozeti</div>
                    <div class="metric-list" id="binance-technical-score-component-summary"></div>
                    <div class="subsection-title">Skor gap ozeti</div>
                    <div class="metric-list" id="binance-technical-score-gap-summary"></div>
                    <div class="subsection-title">Taze teknik skor gap</div>
                    <div class="metric-list" id="binance-technical-fresh-score-gap-summary"></div>
                    <div class="subsection-title">Stale eligibility ozeti</div>
                    <div class="metric-list" id="binance-technical-stale-eligibility-summary"></div>
                    <div class="subsection-title">Legacy teknik shape ozeti</div>
                    <div class="metric-list" id="binance-technical-legacy-position-shape-summary"></div>
                    <div class="subsection-title">Legacy acik pozisyonlar</div>
                    <div id="binance-technical-legacy-open-positions"></div>
                    <div class="subsection-title">Pozisyon Baskisi ve Exit Akisi</div>
                    <div class="metric-list" id="binance-technical-position-pressure-summary"></div>
                    <div class="metric-list" id="performance-snapshot"></div>
                </div>
                <div class="subcard" data-tab="polymarket">
                    <div class="subcard-header"><h3 class="subcard-title">Graph Discovery Ozeti</h3><span class="badge info">Graph</span></div>
                    <div class="metric-list" id="graph-discovery-summary"></div>
                </div>
                <div class="subcard" data-tab="polymarket">
                    <div class="subcard-header"><h3 class="subcard-title">Whale Side Ozeti</h3><span class="badge info">Side</span></div>
                    <p class="subcard-copy">SELL yonlu eventler mapping ve alias cache icin islenir; trade adayina cevrilmez.</p>
                    <div class="metric-list" id="whale-side-summary"></div>
                </div>
                <div class="subcard" data-tab="polymarket">
                    <div class="subcard-header"><h3 class="subcard-title">Whale-Copy Gate Funnel</h3><span class="badge info">Funnel</span></div>
                    <div class="metric-list" id="whale-copy-gate-funnel"></div>
                </div>
                <div class="subcard" data-tab="polymarket">
                    <div class="subcard-header"><h3 class="subcard-title">Whale Candidate Birikimi</h3><span class="badge info">Agg</span></div>
                    <div class="metric-list" id="whale-candidate-aggregation-summary"></div>
                </div>
                <div class="subcard" data-tab="polymarket">
                    <div class="subcard-header"><h3 class="subcard-title">Gate-ready Whale Adaylari</h3><span class="badge info">Ready</span></div>
                    <div id="recent-gate-ready-candidates"></div>
                </div>
                <div class="subcard" data-tabs="polymarket,teshis-log">
                    <div class="subcard-header"><h3 class="subcard-title">Gated Whale-Copy Red Nedenleri</h3><span class="badge warn">Gate</span></div>
                    <div id="gated-reject-breakdown"></div>
                </div>
                <div class="subcard" data-tabs="polymarket,teshis-log">
                    <div class="subcard-header"><h3 class="subcard-title">Relaxed Gate Sonrasi Kalan Red Nedenleri</h3><span class="badge warn">Recovery</span></div>
                    <div id="relaxed-gate-reject-breakdown"></div>
                </div>
                <div class="subcard" data-tabs="polymarket,teshis-log">
                    <div class="subcard-header"><h3 class="subcard-title">Sampling Red Nedenleri</h3><span class="badge warn">Blocker</span></div>
                    <div id="sampling-reject-breakdown"></div>
                </div>
                <div class="subcard" data-tabs="binance-teknik,teshis-log">
                    <div class="subcard-header"><h3 class="subcard-title">Taze Teknik Red Nedenleri</h3><span class="badge warn">Fresh</span></div>
                    <div id="binance-technical-fresh-reject-breakdown"></div>
                </div>
                <div class="subcard" data-tabs="binance-teknik,teshis-log">
                    <div class="subcard-header"><h3 class="subcard-title">Binance Teknik Red Nedenleri</h3><span class="badge warn">Momentum</span></div>
                    <div id="binance-technical-reject-breakdown"></div>
                </div>
                <div class="subcard" data-tabs="binance-teknik,teshis-log">
                    <div class="subcard-header"><h3 class="subcard-title">Teknik Skor Blocker Dagilimi</h3><span class="badge warn">Skor</span></div>
                    <div id="binance-technical-score-blocker-breakdown"></div>
                </div>
            </div>
        </article>
        <article class="panel panel-wide" data-tab="teshis-log"><div class="panel-header"><h2>Servis Log Özeti</h2><span class="badge info">Best Effort</span></div><pre id="service-log">Yükleniyor...</pre></article>
    </section>
</div>
<script>
const refreshSeconds = <?= json_encode($refreshSeconds, JSON_UNESCAPED_SLASHES) ?>;
const DASHBOARD_TABS = ['genel-bakis', 'polymarket', 'binance-teknik', 'pozisyonlar-risk', 'teshis-log'];
let activeTab = 'genel-bakis';
let latestPayload = null;

function parseTabList(value) {
    return String(value ?? '')
        .split(',')
        .map((item) => item.trim())
        .filter(Boolean);
}

function renderTabHelp(payload) {
    const help = payload?.dashboard_tab_help || {};
    const glossary = payload?.dashboard_glossary || {};
    const badge = document.getElementById('tab-help-badge');
    const copy = document.getElementById('tab-help-copy');
    const list = document.getElementById('tab-glossary');
    if (!badge || !copy || !list) { return; }

    const labels = {
        'genel-bakis': 'Genel Bakis',
        'polymarket': 'Polymarket',
        'binance-teknik': 'Binance Teknik',
        'pozisyonlar-risk': 'Pozisyonlar ve Risk',
        'teshis-log': 'Teshis ve Log'
    };

    badge.textContent = labels[activeTab] || 'Genel Bakis';
    copy.textContent = help[activeTab] || 'Bu sekme secili alanin ne ise yaradigini hizli ozetler.';

    const rows = Array.isArray(glossary[activeTab]) ? glossary[activeTab] : [];
    list.innerHTML = rows.map((item) => `
        <div class="glossary-item">
            <div class="glossary-term">${escapeHtml(item.term || 'Terim')}</div>
            <div class="glossary-meaning">${escapeHtml(item.meaning || '')}</div>
        </div>
    `).join('');
}

function applyActiveTab() {
    document.querySelectorAll('[data-tab], [data-tabs]').forEach((element) => {
        const single = element.getAttribute('data-tab');
        const multi = parseTabList(element.getAttribute('data-tabs'));
        const visible = single ? single === activeTab : multi.includes(activeTab);
        element.classList.toggle('is-hidden', !visible);
    });

    document.querySelectorAll('[data-tab-trigger]').forEach((button) => {
        const isActive = button.getAttribute('data-tab-trigger') === activeTab;
        button.classList.toggle('active', isActive);
        button.setAttribute('aria-selected', isActive ? 'true' : 'false');
    });

    if (latestPayload) {
        renderTabHelp(latestPayload);
    }
}

function setActiveTab(tabId, syncHash = true) {
    activeTab = DASHBOARD_TABS.includes(tabId) ? tabId : 'genel-bakis';
    if (syncHash && window.history?.replaceState) {
        window.history.replaceState(null, '', `#${activeTab}`);
    }
    applyActiveTab();
}

function setupTabs() {
    document.querySelectorAll('[data-tab-trigger]').forEach((button) => {
        button.addEventListener('click', () => setActiveTab(button.getAttribute('data-tab-trigger') || 'genel-bakis'));
    });

    window.addEventListener('hashchange', () => {
        const hashTab = window.location.hash.replace(/^#/, '');
        setActiveTab(hashTab || 'genel-bakis', false);
    });

    const initialTab = window.location.hash.replace(/^#/, '');
    setActiveTab(initialTab || 'genel-bakis', !initialTab);
}

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
        sampling_relaxed: 'sampling_relaxed',
        binance_technical_sampling: 'binance_technical_sampling'
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
        lookup_universe: 'lookup evreni',
        lazy_lookup: 'lazy lookup',
        unresolved_retry: 'yeniden deneme',
        hot_window: 'sıcak pencere',
        active_window: 'aktif pencere',
        unknown_token: 'bilinmeyen token'
    };
    return map[text] || String(value ?? 'yok');
}

function translateFlowClassification(value) {
    const text = String(value ?? '').toLowerCase();
    const map = {
        'discovery-route-only': 'discovery route-only',
        'sampling-orderflow': 'sampling orderflow',
        'baseline-orderflow': 'baseline orderflow',
        other: 'diğer'
    };
    return map[text] || String(value ?? 'diğer');
}

function translateReason(value) {
    const text = String(value ?? '');
    if (text.includes(',')) {
        return text
            .split(',')
            .map((part) => translateReason(part.trim()))
            .filter(Boolean)
            .join(', ');
    }
    const map = {
        route_whale_orderflow_only: 'yalnızca whale/orderflow rotası',
        market_not_mapped_active_window: 'aktif pencere dışında kaldı',
        market_not_mapped_lazy_lookup_failed: 'lazy lookup eşleme bulamadı',
        market_not_mapped_unknown_token: 'token eşleşmesi bulunamadı',
        unsupported_side_filtered: 'desteklenmeyen yön erken filtrelendi',
        sell_side_not_supported: 'desteklenmeyen yön filtrelendi',
        score_below_threshold: 'skor eşik altında',
        liquidity_guard_rejection: 'likidite koruması reddetti',
        slippage_guard_rejection: 'slippage koruması reddetti',
        missing_polymarket_token_price: 'Polymarket token fiyatı bulunamadı',
        token_recovery_failed: 'token recovery başarısız',
        cluster_threshold_not_reached: 'cluster eşiği tutmadı',
        sampling_target_reached: 'sampling hedefi dolduğu için durduruldu',
        futures_spread_wide: 'futures spread genis',
        futures_bid_ask_missing: 'futures bid ask eksik',
        futures_snapshot_untrusted: 'futures snapshot guvensiz',
        technical_alignment_weak: 'teknik hizalanma zayif',
        max_order_usd_exceeded: 'tek islem limiti asildi',
        max_open_positions_exceeded: 'acik pozisyon limiti doldu',
        max_total_position_usd_exceeded: 'toplam pozisyon limiti doldu',
        max_position_exceeded: 'legacy pozisyon limiti',
        stale_time_exit: 'sure baskisiyla cikis',
        stale_hard_timeout_exit: 'uzun sure acik kaldigi icin cikis',
        microstructure_drag: 'mikro yapi skoru dusuk',
        momentum_drag: 'momentum skoru dusuk',
        macd_drag: 'MACD skoru dusuk',
        volume_drag: 'hacim skoru dusuk',
        rsi_drag: 'RSI skoru dusuk',
        multi_factor_drag: 'coklu faktor skoru dusuk',
        macd_not_aligned: 'MACD hizalanmadi',
        missing_price_history: 'fiyat gecmisi eksik',
        missing_futures_volume: 'futures hacmi eksik',
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

function ensureSamplingRejectPanel() {
    const metrics = document.getElementById('performance-snapshot');
    if (!metrics) { return null; }
    const panel = metrics.closest('.panel');
    if (!panel) { return null; }

    let title = document.getElementById('sampling-reject-breakdown-title');
    if (!title) {
        title = document.createElement('div');
        title.id = 'sampling-reject-breakdown-title';
        title.className = 'subsection-title';
        title.textContent = 'Sampling Red Nedenleri';
        panel.appendChild(title);
    }

    let breakdown = document.getElementById('sampling-reject-breakdown');
    if (!breakdown) {
        breakdown = document.createElement('div');
        breakdown.id = 'sampling-reject-breakdown';
        panel.appendChild(breakdown);
    }

    return breakdown;
}

function collapseSectionById(targetId, label, badgeTone = 'info') {
    const target = document.getElementById(targetId);
    if (!target) { return; }
    const card = target.closest('.subcard');
    if (!card || card.dataset.folded === '1') { return; }

    const details = document.createElement('details');
    details.className = 'fold-card';

    const summary = document.createElement('summary');
    summary.className = 'fold-summary';

    const title = document.createElement('span');
    title.className = 'subcard-title';
    title.textContent = label;

    const badge = document.createElement('span');
    badge.className = `badge ${badgeTone}`;
    badge.textContent = 'Detay';

    summary.append(title, badge);

    const body = document.createElement('div');
    body.className = 'fold-body';

    const parent = card.parentNode;
    if (!parent) { return; }
    parent.replaceChild(details, card);
    body.appendChild(card);
    details.append(summary, body);
    card.dataset.folded = '1';
}

function setupDiagnosticFolds() {
    collapseSectionById('market-alias-counts', 'Alias Kaynakları', 'info');
    collapseSectionById('top-market-aliases', 'En Güçlü Alias Cache', 'info');
    collapseSectionById('top-unresolved-aliases', 'En Sık Çözülemeyen Alias’lar', 'warn');
    collapseSectionById('recent-unresolved-aliases', 'Son Çözülemeyen Alias Olayları', 'warn');
    collapseSectionById('trusted-whale-summary', 'Kanıtlı Balinalar', 'info');
    collapseSectionById('top-whales', 'Balina Tablosu', 'info');
    collapseSectionById('gated-reject-breakdown', 'Gated Whale-Copy Red Nedenleri', 'warn');
    collapseSectionById('relaxed-gate-reject-breakdown', 'Relaxed Gate Sonrasi Kalan Red Nedenleri', 'warn');
    collapseSectionById('sampling-reject-breakdown', 'Sampling Red Nedenleri', 'warn');
    collapseSectionById('binance-technical-fresh-reject-breakdown', 'Taze Teknik Red Nedenleri', 'warn');
    collapseSectionById('binance-technical-score-blocker-breakdown', 'Teknik Skor Blocker Dagilimi', 'warn');
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
    const marketRateNote = document.getElementById('market-not-mapped-note') || document.getElementById('market-not-mapped-rate')?.nextElementSibling;
    if (marketRateNote) {
        marketRateNote.id = 'market-not-mapped-note';
        marketRateNote.textContent = runtime.live_metrics_available
            ? 'Canli oran gosteriliyor, 60 dk ve tarihsel detay asagida'
            : 'Canli status yok, son 60 dk orani gosteriliyor';
    }
    document.getElementById('final-verdict').textContent = translateVerdict(verdictBlock.verdict || 'yok');
    document.getElementById('verdict-reason').textContent = verdictBlock.reason || 'Henüz SWOT kararı yok.';
    document.getElementById('open-state-note').textContent = `${formatNumber(runtime.open_positions_count, 0)} açık pozisyon / ${formatNumber(runtime.open_orders_count, 0)} açık emir`;
    document.getElementById('last-refresh-badge').textContent = `Son yenileme ${new Date(payload.generated_at).toLocaleTimeString('tr-TR')}`;
}

function updatePanels(payload) {
    renderTable('recent-decisions', [
        { key: 'occurred_at', label: 'Zaman', mono: true, render: (row) => escapeHtml(row.occurred_at || '') },
        { key: 'source', label: 'Kaynak', render: (row) => `<span class="badge ${badgeClass(row.action || row.raw_source_signal)}" title="${escapeHtml(row.raw_source_signal || row.signal_family || 'unknown')}">${escapeHtml(row.raw_source_signal || row.signal_family || 'unknown')}</span>` },
        { key: 'flow_classification', label: 'Akış', render: (row) => escapeHtml(translateFlowClassification(row.flow_classification || 'other')) },
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

    const whaleUniverse = payload.whale_universe_summary || {};
    renderMetrics('whale-universe-summary', [
        { label: 'Izlenen toplam balina', value: formatNumber(whaleUniverse.tracked_whales, 0) },
        { label: 'Leaderboard kaynakli', value: formatNumber(whaleUniverse.leaderboard_wallets, 0) },
        { label: 'Activity kaynakli', value: formatNumber(whaleUniverse.activity_discovered_wallets, 0) },
        { label: 'Graph-discovery kaynakli', value: formatNumber(whaleUniverse.graph_discovered_wallets, 0) },
        { label: 'Kanitli balina', value: formatNumber(whaleUniverse.trusted_whales, 0) }
    ]);

    renderTable('trusted-whale-summary', [
        { key: 'address', label: 'Adres', mono: true, render: (row) => truncateHtml(row.address || '', 22) },
        { key: 'source_type', label: 'Kaynak', render: (row) => escapeHtml(row.source_type || '') },
        { key: 'trust_score', label: 'Guven', render: (row) => escapeHtml(formatNumber(row.trust_score, 3)) },
        { key: 'total_trades', label: 'Islem', render: (row) => escapeHtml(formatNumber(row.total_trades, 0)) },
        { key: 'win_rate', label: 'Kazanma', render: (row) => row.win_rate === null || row.win_rate === undefined ? '&mdash;' : escapeHtml(`%${formatNumber(row.win_rate, 1)}`) },
        { key: 'total_pnl', label: 'Toplam PnL', render: (row) => escapeHtml(formatNumber(row.total_pnl, 2)) }
    ], payload.trusted_whale_summary, 'Henuz kapanmis performans kaniti yok.');

    renderTable('top-whales', [
        { key: 'address', label: 'Adres', mono: true, render: (row) => truncateHtml(row.address || '', 22) },
        { key: 'source_type', label: 'Kaynak', render: (row) => escapeHtml(row.source_type || '') },
        { key: 'discovery_score', label: 'Keşif Skoru', render: (row) => escapeHtml(formatNumber(row.discovery_score, 3)) },
        {
            key: 'trust_score',
            label: 'Güven Skoru',
            render: (row) => {
                const title = Number(row.total_trades || 0) > 0
                    ? 'Kapanmış whale geçmişinden öğrenilen güven skoru.'
                    : 'Henüz kapanmış whale geçmişi yok; nötr güven.';
                return `<span title="${escapeHtml(title)}">${escapeHtml(formatNumber(row.trust_score, 3))}</span>`;
            }
        },
        { key: 'event_count_24h', label: '24s Event', render: (row) => escapeHtml(formatNumber(row.event_count_24h, 0)) },
        { key: 'total_trades', label: 'Kapanmış İşlem', render: (row) => escapeHtml(formatNumber(row.total_trades, 0)) },
        {
            key: 'win_rate',
            label: 'Kazanma Oranı',
            render: (row) => Number(row.total_trades || 0) > 0
                ? escapeHtml(`%${formatNumber(row.win_rate, 1)}`)
                : '&mdash;'
        },
        {
            key: 'total_pnl',
            label: 'Toplam PnL',
            render: (row) => Number(row.total_trades || 0) > 0
                ? escapeHtml(formatNumber(row.total_pnl, 2))
                : '&mdash;'
        }
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

    renderTable('routing-breakdown', [
        { key: 'flow_classification', label: 'Akış', render: (row) => escapeHtml(translateFlowClassification(row.flow_classification || 'other')) },
        { key: 'count', label: 'Adet', render: (row) => escapeHtml(formatNumber(row.count, 0)) }
    ], payload.routing_breakdown, 'Henüz akış ayrımı birikmedi.');

    renderTable('sampling-decision-summary', [
        { key: 'action', label: 'Aksiyon', render: (row) => `<span class="badge ${badgeClass(row.action)}">${escapeHtml(translateAction(row.action || 'n/a'))}</span>` },
        { key: 'count', label: 'Adet', render: (row) => escapeHtml(formatNumber(row.count, 0)) }
    ], payload.sampling_decision_summary, 'Henüz sampling karar özeti yok.');

    renderTable('mapping-miss-breakdown', [
        { key: 'reason', label: 'Neden', render: (row) => escapeHtml(translateReason(row.reason || 'yok')) },
        { key: 'count', label: 'Adet', render: (row) => escapeHtml(formatNumber(row.count, 0)) }
    ], payload.mapping_miss_breakdown, 'Henüz mapping miss nedeni yok.');

    renderTable('unsupported-side-summary', [
        { key: 'reason', label: 'Neden', render: (row) => escapeHtml(translateReason(row.reason || 'yok')) },
        { key: 'count', label: 'Adet', render: (row) => escapeHtml(formatNumber(row.count, 0)) }
    ], payload.unsupported_side_summary, 'Henüz desteklenmeyen yön filtresi görünmüyor.');

    const runtime = payload.runtime_summary || {};
    renderMetrics('mapping-health', [
        { label: 'Eslenen orderflow event', value: formatNumber(runtime.mapped_orderflow_events, 0) },
        { label: 'Eslenemeyen orderflow event', value: formatNumber(runtime.unmapped_orderflow_events, 0) },
        { label: 'Alias cache hit', value: formatNumber(runtime.alias_cache_hits, 0) },
        { label: 'Lazy lookup hit', value: formatNumber(runtime.lazy_lookup_hits, 0) },
        { label: 'Sicak pencere marketleri', value: formatNumber(runtime.hot_window_markets, 0) },
        { label: 'Sicak pencere hit', value: formatNumber(runtime.hot_window_hits, 0) },
        { label: 'Hot-window promotion', value: formatNumber(runtime.hot_window_promotions, 0) },
        { label: 'Hot-window expiry', value: formatNumber(runtime.hot_window_expiries, 0) },
        { label: 'Active-window miss', value: formatNumber(runtime.active_window_misses, 0) },
        { label: 'Active-window miss orani', value: `${formatNumber(runtime.active_window_miss_rate, 1)}%` },
        { label: 'Resolver hit orani', value: `${formatNumber(runtime.resolver_hit_rate, 1)}%` },
        { label: 'Canli market eslesmedi orani', value: runtime.live_metrics_available ? `${formatNumber(runtime.market_not_mapped_rate, 1)}%` : 'yok' },
        { label: 'Son 60 dk market eslesmedi orani', value: `${formatNumber(runtime.recent_market_not_mapped_rate, 1)}%` },
        { label: 'Tarihsel market eslesmedi orani', value: `${formatNumber(runtime.historical_market_not_mapped_rate, 1)}%` }
    ]);

    const sourceQuality = payload.source_quality_summary || {};
    renderMetrics('source-quality-summary', [
        { label: 'Toplam orderflow', value: formatNumber(sourceQuality.total_orderflow, 0) },
        { label: 'Mapping sonrası kalan', value: formatNumber(sourceQuality.orderflow_after_mapping, 0) },
        { label: 'Discovery route-only', value: formatNumber(sourceQuality.discovery_route_only, 0) },
        { label: 'Baseline orderflow', value: formatNumber(sourceQuality.baseline_orderflow, 0) },
        { label: 'Sampling orderflow', value: formatNumber(sourceQuality.sampling_orderflow, 0) },
        { label: 'Unsupported side filtre', value: formatNumber(sourceQuality.unsupported_side_filtered, 0) }
    ]);

    const aliasPersistence = payload.alias_persistence_summary || {};
    renderMetrics('alias-persistence-summary', [
        { label: 'Lookup evreni market', value: formatNumber(aliasPersistence.lookup_universe_markets, 0) },
        { label: 'Lookup evreni alias', value: formatNumber(aliasPersistence.lookup_universe_aliases, 0) },
        { label: 'Kalıcı alias satırı', value: formatNumber(aliasPersistence.persisted_market_alias_rows, 0) },
        { label: 'Kalıcı market satırı', value: formatNumber(aliasPersistence.persisted_market_alias_markets, 0) },
        { label: 'Yazma farkı', value: formatNumber(aliasPersistence.alias_persistence_gap, 0) },
        { label: 'Hydrate edilen market', value: formatNumber(aliasPersistence.hydrated_lookup_markets, 0) },
        { label: 'Hydrate edilen alias', value: formatNumber(aliasPersistence.hydrated_lookup_aliases, 0) },
        { label: 'Durum', value: aliasPersistence.warning || 'uyumlu', long: true }
    ]);

    const whaleCopy = payload.whale_copy_summary || {};
    renderMetrics('whale-copy-summary', [
        { label: 'Toplam whale/orderflow event', value: formatNumber(whaleCopy.total_whale_events, 0) },
        { label: 'Resolved whale event', value: formatNumber(whaleCopy.resolved_whale_events, 0) },
        { label: 'Whale-copy adayi', value: formatNumber(whaleCopy.whale_copy_candidates, 0) },
        { label: 'Gated red', value: formatNumber(whaleCopy.gated_rejects, 0) },
        { label: 'Karar', value: formatNumber(whaleCopy.gated_decisions, 0) },
        { label: 'Execute', value: formatNumber(whaleCopy.gated_executes, 0) }
    ]);

    const whaleCopyRecovery = payload.whale_copy_recovery_summary || {};
    renderMetrics('whale-copy-recovery-summary', [
        { label: 'Relaxed gate denemesi', value: formatNumber(whaleCopyRecovery.relaxed_gate_attempts, 0) },
        { label: 'Relaxed gate red', value: formatNumber(whaleCopyRecovery.relaxed_gate_rejects, 0) },
        { label: 'Relaxed gate karar', value: formatNumber(whaleCopyRecovery.relaxed_gate_decisions, 0) },
        { label: 'Relaxed gate execute', value: formatNumber(whaleCopyRecovery.relaxed_gate_executes, 0) },
        { label: 'Token recovery denemesi', value: formatNumber(whaleCopyRecovery.token_recovery_attempts, 0) },
        { label: 'Token recovery hit', value: formatNumber(whaleCopyRecovery.token_recovery_hits, 0) },
        { label: 'Token recovery failed', value: formatNumber(whaleCopyRecovery.token_recovery_failed, 0) },
        { label: 'Eksik token fiyatı red', value: formatNumber(whaleCopyRecovery.missing_token_rejects, 0) }
    ]);

    const technicalSummary = payload.binance_technical_summary || {};
    renderMetrics('binance-technical-summary', [
        { label: 'Karar', value: formatNumber(technicalSummary.decisions, 0) },
        { label: 'Red', value: formatNumber(technicalSummary.rejects, 0) },
        { label: 'Execute', value: formatNumber(technicalSummary.executes, 0) },
        { label: 'LONG sinyal', value: formatNumber(technicalSummary.long_signals, 0) },
        { label: 'SHORT sinyal', value: formatNumber(technicalSummary.short_signals, 0) },
        { label: 'Force sample', value: formatNumber(technicalSummary.forced_samples, 0) }
    ]);

    const technicalFreshSummary = payload.binance_technical_fresh_summary || {};
    renderMetrics('binance-technical-fresh-summary', [
        { label: 'Karar', value: formatNumber(technicalFreshSummary.decisions, 0) },
        { label: 'Red', value: formatNumber(technicalFreshSummary.rejects, 0) },
        { label: 'Execute', value: formatNumber(technicalFreshSummary.executes, 0) },
        { label: 'LONG sinyal', value: formatNumber(technicalFreshSummary.long_signals, 0) },
        { label: 'SHORT sinyal', value: formatNumber(technicalFreshSummary.short_signals, 0) },
        { label: 'Force sample', value: formatNumber(technicalFreshSummary.forced_samples, 0) }
    ]);

    const technicalGateFunnel = payload.binance_technical_gate_funnel || {};
    renderMetrics('binance-technical-gate-funnel', [
        { label: 'Taranan sembol', value: formatNumber(technicalGateFunnel.scanned_symbols, 0) },
        { label: 'Yonlu sinyal', value: formatNumber(technicalGateFunnel.directional_signals, 0) },
        { label: 'Recovery hizalanma', value: formatNumber(technicalGateFunnel.recovered_alignment_signals, 0) },
        { label: 'Spread red', value: formatNumber(technicalGateFunnel.spread_rejects, 0) },
        { label: 'Skor red', value: formatNumber(technicalGateFunnel.score_rejects, 0) },
        { label: 'Karar', value: formatNumber(technicalGateFunnel.decisions, 0) },
        { label: 'Execute', value: formatNumber(technicalGateFunnel.executes, 0) }
    ]);

    const technicalFreshGateFunnel = payload.binance_technical_fresh_gate_funnel || {};
    renderMetrics('binance-technical-fresh-gate-funnel', [
        { label: 'Taranan sembol', value: formatNumber(technicalFreshGateFunnel.scanned_symbols, 0) },
        { label: 'Yonlu sinyal', value: formatNumber(technicalFreshGateFunnel.directional_signals, 0) },
        { label: 'Recovery hizalanma', value: formatNumber(technicalFreshGateFunnel.recovered_alignment_signals, 0) },
        { label: 'Spread red', value: formatNumber(technicalFreshGateFunnel.spread_rejects, 0) },
        { label: 'Skor red', value: formatNumber(technicalFreshGateFunnel.score_rejects, 0) },
        { label: 'Karar', value: formatNumber(technicalFreshGateFunnel.decisions, 0) },
        { label: 'Execute', value: formatNumber(technicalFreshGateFunnel.executes, 0) }
    ]);

    const technicalRecovery = payload.binance_technical_recovery_summary || {};
    renderMetrics('binance-technical-recovery-summary', [
        { label: 'Recovery uygulandi', value: formatNumber(technicalRecovery.recovery_applied_count, 0) },
        { label: 'Alignment recovery hit', value: formatNumber(technicalRecovery.alignment_recovery_hits, 0) },
        { label: 'Mikro yapi recovery hit', value: formatNumber(technicalRecovery.microstructure_recovery_hits, 0) },
        { label: 'Spread recovery hit', value: formatNumber(technicalRecovery.spread_recovery_hits, 0) },
        { label: 'Mikro yapi v2 hit', value: formatNumber(technicalRecovery.microstructure_recovery_v2_hits, 0) },
        { label: 'Spread v2 hit', value: formatNumber(technicalRecovery.spread_recovery_v2_hits, 0) },
        { label: 'Final skor recovery hit', value: formatNumber(technicalRecovery.final_score_recovery_hits, 0) },
        { label: 'Yakin esik adayi', value: formatNumber(technicalRecovery.near_threshold_candidates, 0) },
        { label: 'Skor recovery adayi', value: formatNumber(technicalRecovery.score_recovery_candidates, 0) },
        { label: 'Skor recovery gecis', value: formatNumber(technicalRecovery.score_recovery_passes, 0) },
        { label: 'Force recovery adayi', value: formatNumber(technicalRecovery.force_recovery_candidates, 0) },
        { label: 'Candidate floor hit', value: formatNumber(technicalRecovery.microstructure_candidate_floor_hits, 0) },
        { label: 'Force sample hit', value: formatNumber(technicalRecovery.force_sample_hits, 0) },
        { label: 'Aktif sembol', value: formatNumber(technicalRecovery.active_symbol_count, 0) }
    ]);

    const technicalFreshRecovery = payload.binance_technical_fresh_recovery_summary || {};
    renderMetrics('binance-technical-fresh-recovery-summary', [
        { label: 'Recovery uygulandi', value: formatNumber(technicalFreshRecovery.recovery_applied_count, 0) },
        { label: 'Alignment recovery hit', value: formatNumber(technicalFreshRecovery.alignment_recovery_hits, 0) },
        { label: 'Mikro yapi recovery hit', value: formatNumber(technicalFreshRecovery.microstructure_recovery_hits, 0) },
        { label: 'Spread recovery hit', value: formatNumber(technicalFreshRecovery.spread_recovery_hits, 0) },
        { label: 'Mikro yapi v2 hit', value: formatNumber(technicalFreshRecovery.microstructure_recovery_v2_hits, 0) },
        { label: 'Spread v2 hit', value: formatNumber(technicalFreshRecovery.spread_recovery_v2_hits, 0) },
        { label: 'Final skor recovery hit', value: formatNumber(technicalFreshRecovery.final_score_recovery_hits, 0) },
        { label: 'Yakin esik adayi', value: formatNumber(technicalFreshRecovery.near_threshold_candidates, 0) },
        { label: 'Skor recovery adayi', value: formatNumber(technicalFreshRecovery.score_recovery_candidates, 0) },
        { label: 'Skor recovery gecis', value: formatNumber(technicalFreshRecovery.score_recovery_passes, 0) },
        { label: 'Force recovery adayi', value: formatNumber(technicalFreshRecovery.force_recovery_candidates, 0) },
        { label: 'Candidate floor hit', value: formatNumber(technicalFreshRecovery.microstructure_candidate_floor_hits, 0) },
        { label: 'Force sample hit', value: formatNumber(technicalFreshRecovery.force_sample_hits, 0) },
        { label: 'Aktif sembol', value: formatNumber(technicalFreshRecovery.active_symbol_count, 0) }
    ]);

    const technicalSnapshot = payload.binance_futures_snapshot_summary || {};
    renderMetrics('binance-futures-snapshot-summary', [
        { label: 'Ticker book', value: formatNumber(technicalSnapshot.trusted_ticker_book_hits, 0) },
        { label: 'Info book', value: formatNumber(technicalSnapshot.trusted_info_book_hits, 0) },
        { label: 'Orderbook book', value: formatNumber(technicalSnapshot.trusted_orderbook_book_hits, 0) },
        { label: 'Orderbook fallback', value: formatNumber(technicalSnapshot.orderbook_fallback_hits, 0) },
        { label: 'Orderbook repriced', value: formatNumber(technicalSnapshot.orderbook_reprice_hits, 0) },
        { label: 'Bid ask eksik red', value: formatNumber(technicalSnapshot.missing_bid_ask_rejects, 0) },
        { label: 'Snapshot guvensiz red', value: formatNumber(technicalSnapshot.snapshot_untrusted_rejects, 0) }
    ]);

    const technicalScoreComponents = payload.binance_technical_score_component_summary || {};
    renderMetrics('binance-technical-score-component-summary', [
        { label: 'Ornek sayisi', value: formatNumber(technicalScoreComponents.sample_count, 0) },
        { label: 'Ortalama RSI', value: formatNumber(technicalScoreComponents.avg_rsi_component, 3) },
        { label: 'Ortalama MACD', value: formatNumber(technicalScoreComponents.avg_macd_component, 3) },
        { label: 'Ortalama momentum', value: formatNumber(technicalScoreComponents.avg_momentum_component, 3) },
        { label: 'Ortalama hacim', value: formatNumber(technicalScoreComponents.avg_volume_component, 3) },
        { label: 'Ortalama mikro yapi', value: formatNumber(technicalScoreComponents.avg_microstructure_component, 3) },
        { label: 'Ortalama MACD normalizer', value: formatNumber(technicalScoreComponents.avg_macd_normalizer, 4) },
        { label: 'Ortalama momentum normalizer', value: formatNumber(technicalScoreComponents.avg_momentum_normalizer, 4) },
        { label: 'Ortalama hacim normalizer', value: formatNumber(technicalScoreComponents.avg_volume_ratio_normalizer, 4) },
        { label: 'Ortalama esik', value: formatNumber(technicalScoreComponents.avg_effective_min_score, 3) },
        { label: 'Ortalama final skor', value: formatNumber(technicalScoreComponents.avg_final_score, 3) }
    ]);

    const technicalScoreGap = payload.binance_technical_score_gap_summary || {};
    renderMetrics('binance-technical-score-gap-summary', [
        { label: 'Ortalama skor farki', value: formatNumber(technicalScoreGap.avg_score_gap_to_threshold, 3) },
        { label: 'Esik alti adet', value: formatNumber(technicalScoreGap.below_threshold_count, 0) },
        { label: 'Yakin esik adayi', value: formatNumber(technicalScoreGap.near_threshold_count, 0) },
        { label: 'Derin esik alti', value: formatNumber(technicalScoreGap.deep_below_threshold_count, 0) }
    ]);

    const technicalFreshScoreGap = payload.binance_technical_fresh_score_gap_summary || {};
    renderMetrics('binance-technical-fresh-score-gap-summary', [
        { label: 'Ortalama skor farki', value: formatNumber(technicalFreshScoreGap.avg_score_gap_to_threshold, 3) },
        { label: 'Esik alti adet', value: formatNumber(technicalFreshScoreGap.below_threshold_count, 0) },
        { label: 'Yakin esik adayi', value: formatNumber(technicalFreshScoreGap.near_threshold_count, 0) },
        { label: 'Derin esik alti', value: formatNumber(technicalFreshScoreGap.deep_below_threshold_count, 0) }
    ]);

    const technicalStaleEligibility = payload.binance_technical_stale_eligibility_summary || {};
    renderMetrics('binance-technical-stale-eligibility-summary', [
        { label: 'Teknik acik pozisyon', value: formatNumber(technicalStaleEligibility.technical_open_positions_total, 0) },
        { label: 'Strict teknik pozisyon', value: formatNumber(technicalStaleEligibility.technical_open_positions_strict, 0) },
        { label: 'Eski teknik pozisyon', value: formatNumber(technicalStaleEligibility.technical_open_positions_legacy, 0) },
        { label: 'Backfill edilen pozisyon', value: formatNumber(technicalStaleEligibility.technical_open_positions_backfilled, 0) },
        { label: 'Kapsam disi pozisyon', value: formatNumber(technicalStaleEligibility.technical_open_positions_ineligible, 0) }
    ]);

    const technicalLegacyShape = payload.binance_technical_legacy_position_shape_summary || {};
    renderMetrics('binance-technical-legacy-position-shape-summary', [
        { label: 'Acik Binance paper pozisyon', value: formatNumber(technicalLegacyShape.open_binance_paper_positions, 0) },
        { label: 'Strict teknik', value: formatNumber(technicalLegacyShape.strict_technical_positions, 0) },
        { label: 'Legacy teknik', value: formatNumber(technicalLegacyShape.legacy_technical_positions, 0) },
        { label: 'Price-structure kaynakli', value: formatNumber(technicalLegacyShape.price_structure_source_positions, 0) },
        { label: 'Momentum kaynakli', value: formatNumber(technicalLegacyShape.technical_momentum_source_positions, 0) },
        { label: 'Koruma emri bagli', value: formatNumber(technicalLegacyShape.protection_linked_positions, 0) },
        { label: 'Backfill edilen', value: formatNumber(technicalLegacyShape.backfilled_positions, 0) },
        { label: 'Kapsam disi', value: formatNumber(technicalLegacyShape.ineligible_positions, 0) }
    ]);

    renderTable('binance-technical-legacy-open-positions', [
        { key: 'classification', label: 'Sinif', render: (row) => escapeHtml(row.classification || 'ineligible') },
        { key: 'venue', label: 'Venue', mono: true, render: (row) => escapeHtml(row.venue || '') },
        { key: 'symbol_or_market_id', label: 'Sembol / Market', mono: true, render: (row) => truncateHtml(row.symbol_or_market_id || '', 20) },
        { key: 'side', label: 'Yon', render: (row) => escapeHtml(row.side || '') },
        { key: 'source_signal', label: 'Kaynak', render: (row) => escapeHtml(row.source_signal || 'yok') },
        { key: 'strategy_profile', label: 'Strateji', render: (row) => escapeHtml(row.strategy_profile || 'yok') },
        { key: 'sample_kind', label: 'Ornek', render: (row) => escapeHtml(row.sample_kind || 'yok') },
        { key: 'position_age_minutes', label: 'Yas (dk)', render: (row) => escapeHtml(formatNumber(row.position_age_minutes, 0)) },
        { key: 'marker_labels', label: 'Shape Isaretleri', render: (row) => escapeHtml(row.marker_labels || 'yok') }
    ], payload.binance_technical_legacy_open_positions || [], 'Legacy teknik acik pozisyon bulunmuyor.');

    const technicalPositionPressure = payload.binance_technical_position_pressure_summary || {};
    renderMetrics('binance-technical-position-pressure-summary', [
        { label: 'Acik pozisyon', value: formatNumber(technicalPositionPressure.open_positions, 0) },
        { label: 'Acik notional', value: formatNumber(technicalPositionPressure.open_notional_usd, 2) },
        { label: 'Kalan kapasite', value: formatNumber(technicalPositionPressure.remaining_capacity_usd, 2) },
        { label: 'Son 60 dk cikis', value: formatNumber(technicalPositionPressure.recent_exits_60m, 0) },
        { label: 'Stop-loss cikis', value: formatNumber(technicalPositionPressure.stop_loss_exits_60m, 0) },
        { label: 'Take-profit cikis', value: formatNumber(technicalPositionPressure.take_profit_exits_60m, 0) },
        { label: 'En eski acik pozisyon (dk)', value: formatNumber(technicalPositionPressure.oldest_open_position_minutes, 0) },
        { label: '30 dk ustu pozisyon', value: formatNumber(technicalPositionPressure.positions_over_30m, 0) },
        { label: '60 dk ustu pozisyon', value: formatNumber(technicalPositionPressure.positions_over_60m, 0) },
        { label: '120 dk ustu pozisyon', value: formatNumber(technicalPositionPressure.positions_over_120m, 0) },
        { label: '240 dk ustu pozisyon', value: formatNumber(technicalPositionPressure.positions_over_240m, 0) },
        { label: 'Max open positions red', value: formatNumber(technicalPositionPressure.max_open_positions_rejects, 0) },
        { label: 'Max total position red', value: formatNumber(technicalPositionPressure.max_total_position_usd_rejects, 0) },
        { label: 'Max order red', value: formatNumber(technicalPositionPressure.max_order_usd_rejects, 0) },
        { label: 'Legacy max position red', value: formatNumber(technicalPositionPressure.legacy_max_position_exceeded_rejects, 0) },
        { label: 'Boyutu kucultulen giris', value: formatNumber(technicalPositionPressure.sized_down_entries, 0) },
        { label: 'Stale review 90 dk', value: formatNumber(technicalPositionPressure.stale_review_candidates_90m, 0) },
        { label: 'Stale exit 120 dk', value: formatNumber(technicalPositionPressure.stale_exit_candidates_120m, 0) },
        { label: '240 dk hard-timeout aday', value: formatNumber(technicalPositionPressure.stale_hard_timeout_candidates_240m, 0) },
        { label: 'Sure baskisiyla cikis', value: formatNumber(technicalPositionPressure.stale_exit_executed, 0) },
        { label: 'Hard-timeout cikis', value: formatNumber(technicalPositionPressure.stale_hard_timeout_executed, 0) },
        { label: 'Stale skip teknik destek', value: formatNumber(technicalPositionPressure.stale_exit_skipped_alignment_support, 0) },
        { label: 'Stale skip son destek', value: formatNumber(technicalPositionPressure.stale_exit_skipped_recent_support, 0) },
        { label: 'Stale skip kar korumasi', value: formatNumber(technicalPositionPressure.stale_exit_skipped_profit_protection, 0) },
        { label: 'Kapasiteye geri acilan USD', value: formatNumber(technicalPositionPressure.capacity_released_usd_60m, 2) }
    ]);

    const whaleSide = payload.whale_side_summary || {};
    renderMetrics('whale-side-summary', [
        { label: 'BUY side event', value: formatNumber(whaleSide.buy_side_events, 0) },
        { label: 'SELL side event', value: formatNumber(whaleSide.sell_side_events, 0) },
        { label: 'Desteklenmeyen yon filtre', value: formatNumber(whaleSide.unsupported_side_filtered, 0) }
    ]);

    const whaleCopyGateFunnel = payload.whale_copy_gate_funnel || {};
    renderMetrics('whale-copy-gate-funnel', [
        { label: 'Resolved whale event', value: formatNumber(whaleCopyGateFunnel.resolved_whale_events, 0) },
        { label: 'Gate-ready aday', value: formatNumber(whaleCopyGateFunnel.gate_ready_candidates, 0) },
        { label: 'Relaxed gate denemesi', value: formatNumber(whaleCopyGateFunnel.relaxed_gate_attempts, 0) },
        { label: 'Gated red', value: formatNumber(whaleCopyGateFunnel.gated_rejects, 0) },
        { label: 'Gated karar', value: formatNumber(whaleCopyGateFunnel.gated_decisions, 0) },
        { label: 'Gated execute', value: formatNumber(whaleCopyGateFunnel.gated_executes, 0) }
    ]);

    const graphDiscovery = payload.graph_discovery_summary || {};
    renderMetrics('graph-discovery-summary', [
        { label: 'Graph balina', value: formatNumber(graphDiscovery.graph_discovered_wallets, 0) },
        { label: 'Promote edilen cluster', value: formatNumber(graphDiscovery.graph_clusters_promoted, 0) },
        { label: 'Market ref eksik', value: formatNumber(graphDiscovery.graph_skipped_missing_market_ref, 0) },
        { label: 'Tek wallet kalan', value: formatNumber(graphDiscovery.graph_skipped_single_wallet, 0) },
        { label: 'Notional alti kalan', value: formatNumber(graphDiscovery.graph_skipped_low_notional, 0) }
    ]);

    const whaleCandidateAggregation = payload.whale_candidate_aggregation_summary || {};
    renderMetrics('whale-candidate-aggregation-summary', [
        { label: 'Accumulator bucket', value: formatNumber(whaleCandidateAggregation.accumulator_buckets, 0) },
        { label: 'Gate-ready aday', value: formatNumber(whaleCandidateAggregation.gate_ready_candidates, 0) },
        { label: 'Retry aday', value: formatNumber(whaleCandidateAggregation.retry_candidates, 0) },
        { label: 'Biriken BUY event', value: formatNumber(whaleCandidateAggregation.accumulated_buy_events, 0) },
        { label: 'Biriken notional', value: formatNumber(whaleCandidateAggregation.accumulated_total_notional, 2) }
    ]);

    renderTable('recent-gate-ready-candidates', [
        { key: 'market_id', label: 'Market', mono: true, render: (row) => truncateHtml(row.market_id || '', 32) },
        { key: 'total_amount', label: 'Toplam Notional', render: (row) => escapeHtml(formatNumber(row.total_amount, 2)) },
        { key: 'unique_wallets', label: 'Benzersiz Wallet', render: (row) => escapeHtml(formatNumber(row.unique_wallets, 0)) },
        { key: 'event_count', label: 'Event', render: (row) => escapeHtml(formatNumber(row.event_count, 0)) },
        { key: 'max_trust', label: 'Max Trust', render: (row) => escapeHtml(formatNumber(row.max_trust, 3)) },
        { key: 'gate_ready_reason', label: 'Gate Nedeni', render: (row) => escapeHtml(row.gate_ready_reason || 'yok') },
        { key: 'last_reject_reason', label: 'Son Red', render: (row) => escapeHtml(translateReason(row.last_reject_reason || 'yok')) }
    ], payload.recent_gate_ready_candidates, 'HenÃ¼z gate-ready whale adayi yok.');

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

    renderTable('sampling-reject-breakdown', [
        { key: 'reason', label: 'Neden', render: (row) => escapeHtml(translateReason(row.reason || 'yok')) },
        { key: 'count', label: 'Adet', render: (row) => escapeHtml(formatNumber(row.count, 0)) }
    ], payload.sampling_reject_breakdown, 'Henüz sampling red nedeni birikmedi.');

    renderTable('gated-reject-breakdown', [
        { key: 'reason', label: 'Neden', render: (row) => escapeHtml(translateReason(row.reason || 'yok')) },
        { key: 'count', label: 'Adet', render: (row) => escapeHtml(formatNumber(row.count, 0)) }
    ], payload.gated_reject_breakdown, 'Henüz gated whale-copy red nedeni birikmedi.');

    renderTable('relaxed-gate-reject-breakdown', [
        { key: 'reason', label: 'Neden', render: (row) => escapeHtml(translateReason(row.reason || 'yok')) },
        { key: 'count', label: 'Adet', render: (row) => escapeHtml(formatNumber(row.count, 0)) }
    ], payload.relaxed_gate_reject_breakdown, 'Henuz relaxed gate red nedeni birikmedi.');

    renderTable('binance-technical-reject-breakdown', [
        { key: 'reason', label: 'Neden', render: (row) => escapeHtml(translateReason(row.reason || 'yok')) },
        { key: 'count', label: 'Adet', render: (row) => escapeHtml(formatNumber(row.count, 0)) }
    ], payload.binance_technical_reject_breakdown, 'Henuz binance teknik red nedeni birikmedi.');

    renderTable('binance-technical-fresh-reject-breakdown', [
        { key: 'reason', label: 'Neden', render: (row) => escapeHtml(translateReason(row.reason || 'yok')) },
        { key: 'count', label: 'Adet', render: (row) => escapeHtml(formatNumber(row.count, 0)) }
    ], payload.binance_technical_fresh_reject_breakdown, 'Henuz taze binance teknik red nedeni birikmedi.');

    renderTable('binance-technical-score-blocker-breakdown', [
        { key: 'reason', label: 'Blocker', render: (row) => escapeHtml(translateReason(row.reason || 'yok')) },
        { key: 'count', label: 'Adet', render: (row) => escapeHtml(formatNumber(row.count, 0)) }
    ], payload.binance_technical_score_blocker_breakdown, 'Henuz teknik skor blocker verisi birikmedi.');

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
        latestPayload = payload;
        renderWarnings(payload.warnings || []);
        renderStatusBanner(payload.service || {});
        updateHeader(payload);
        updatePanels(payload);
        renderTabHelp(payload);
        applyActiveTab();
    } catch (error) {
        renderWarnings([`dashboard yenilemesi başarısız oldu: ${error.message}`]);
    }
}

setupDiagnosticFolds();
setupTabs();
refreshDashboard();
setInterval(refreshDashboard, refreshSeconds * 1000);
</script>
</body>
</html>
