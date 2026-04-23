/* global window, document, fetch, setInterval */
(function () {
    'use strict';

    /** @typedef {{label:string,tone?:string,detail?:string,last_sync_at?:string,warning_count?:number}} ServiceStatus */
    /** @typedef {{service_status:ServiceStatus, lanes?:Object<string, any>, all_running?:boolean, total_alerts?:number, last_sync_at?:string}} LandingSummary */
    /** @typedef {{service_status:ServiceStatus, top_kpis?:Record<string, any>, tracked_wallets?:Array<any>, recent_whale_actions?:Array<any>, copy_portfolio_summary?:Record<string, any>, open_copy_positions?:Array<any>, recent_closed_copy_positions?:Array<any>, decision_explainability_rows?:Array<any>, watchlist_segments?:Array<any>, performance_chart_series?:Record<string, any>, work_proof?:Record<string, any>}} PolymarketOperatorSummary */
    /** @typedef {{service_status:ServiceStatus, top_kpis?:Record<string, any>, filter_options?:Record<string, any>, strategy_status_summary?:Record<string, any>, open_positions?:Array<any>, open_orders?:Array<any>, fresh_pnl_summary?:Record<string, any>, runtime_decision_feed?:Array<any>, risk_summary?:Record<string, any>, recent_closed_trades?:Array<any>, technical_quality_summary?:Record<string, any>, performance_chart_series?:Record<string, any>, work_proof?:Record<string, any>}} BinanceOperatorSummary */

    const CONFIG = window.WHALESIGNAL_CONFIG || {};
    const state = {
        payload: null,
        pollHandle: null,
        polymarketDetailTab: 'analysis',
        binanceDetailTab: 'signals',
        binanceFilters: {
            symbol: 'all',
            venue: 'all',
            side: 'all',
            status: 'all',
        },
        walletDetails: new Map(),
    };

    const rootEl = document.getElementById('ws-root');
    const alertsEl = document.getElementById('ws-alerts');
    const statusEl = document.getElementById('ws-status-pill');
    const lastSyncEl = document.getElementById('ws-last-sync');
    const modalEl = document.getElementById('ws-detail-modal');
    const modalContentEl = document.getElementById('ws-detail-modal-content');

    document.addEventListener('DOMContentLoaded', init);

    function init() {
        bindShellEvents();
        loadAndRender();
        const refreshSeconds = Number(CONFIG.refreshSeconds || 5);
        if (Number.isFinite(refreshSeconds) && refreshSeconds > 1) {
            state.pollHandle = window.setInterval(loadAndRender, refreshSeconds * 1000);
        }
    }

    function bindShellEvents() {
        document.addEventListener('click', onDocumentClick);
        document.addEventListener('change', onDocumentChange);
        if (modalEl) {
            modalEl.addEventListener('click', (event) => {
                const rect = modalEl.getBoundingClientRect();
                const inside =
                    event.clientX >= rect.left &&
                    event.clientX <= rect.right &&
                    event.clientY >= rect.top &&
                    event.clientY <= rect.bottom;
                if (!inside && typeof modalEl.close === 'function') {
                    modalEl.close();
                }
            });
        }
    }

    async function loadAndRender() {
        try {
            const response = await fetch(String(CONFIG.apiUrl || 'api.php'), {
                credentials: 'same-origin',
                headers: {
                    Accept: 'application/json',
                },
            });
            if (!response.ok) {
                throw new Error(`Dashboard API ${response.status}`);
            }
            state.payload = await response.json();
            syncTopbar(state.payload);
            renderAlerts(state.payload.warnings || []);
            renderRoot(state.payload);
        } catch (error) {
            syncTopbar({
                service: { active: false, status_text: 'API error' },
                warnings: [String(error.message || error)],
            });
            renderAlerts([`Dashboard verisi alinamadi: ${String(error.message || error)}`]);
            rootEl.innerHTML = renderEmptyState(
                'Veri baglantisi kurulamadi',
                'API yaniti gelmedi. Auth, DB yolu veya lane servisi tarafini kontrol edin.'
            );
        }
    }

    function syncTopbar(payload) {
        const laneMode = String(CONFIG.laneMode || 'split');
        const summary = laneMode === 'split'
            ? payload.operator_landing_summary || {}
            : laneMode === 'polymarket_research'
                ? payload.polymarket_operator_summary || {}
                : payload.binance_operator_summary || {};
        const serviceStatus = summary.service_status || payload.service_status || buildFallbackServiceStatus(payload.service || {});
        statusEl.className = `ws-status-pill ${toneClass(serviceStatus.tone)}`;
        statusEl.textContent = serviceStatus.label || 'Bilinmiyor';
        const syncValue = serviceStatus.last_sync_at || summary.last_sync_at || payload.generated_at || '';
        lastSyncEl.textContent = syncValue ? `Son senkron: ${formatDateTime(syncValue)}` : 'Son senkron bekleniyor';
    }

    function renderAlerts(warnings) {
        const normalized = Array.isArray(warnings) ? warnings.filter(Boolean) : [];
        if (normalized.length === 0) {
            alertsEl.classList.add('is-hidden');
            alertsEl.innerHTML = '';
            return;
        }
        alertsEl.classList.remove('is-hidden');
        alertsEl.innerHTML = normalized
            .slice(0, 4)
            .map((warning) => `<div class="ws-alert is-warn">${escapeHtml(String(warning))}</div>`)
            .join('');
    }

    function renderRoot(payload) {
        const laneMode = String(CONFIG.laneMode || 'split');
        if (laneMode === 'polymarket_research') {
            rootEl.innerHTML = renderPolymarketPage(
                /** @type {PolymarketOperatorSummary} */ (payload.polymarket_operator_summary || {}),
                payload
            );
            return;
        }
        if (laneMode === 'binance_technical') {
            rootEl.innerHTML = renderBinancePage(
                /** @type {BinanceOperatorSummary} */ (payload.binance_operator_summary || {}),
                payload
            );
            return;
        }
        rootEl.innerHTML = renderLandingPage(
            /** @type {LandingSummary} */ (payload.operator_landing_summary || {}),
            payload
        );
    }

    function renderLandingPage(summary, payload) {
        const serviceStatus = summary.service_status || buildFallbackServiceStatus(payload.service || {});
        const lanes = summary.lanes || {};
        const polymarket = lanes.polymarket || {};
        const binance = lanes.binance || {};
        const cards = [
            renderStatCard('Servis durumu', serviceStatus.label || 'Bilinmiyor', serviceStatus.detail || 'Iki lane servis durumunun ortak ozeti', statusAccent(serviceStatus.label || '', serviceStatus.tone || 'neutral')),
            renderStatCard('Toplam uyari', formatInteger(summary.total_alerts || 0), 'Operator dikkat listesi', ''),
            renderStatCard('Son senkron', summary.last_sync_at ? formatDateTime(summary.last_sync_at) : 'Bekleniyor', 'Tum lane ozetleri', ''),
            renderStatCard('Tum lane aktif mi?', summary.all_running ? 'Evet' : 'Hayir', 'Landing health check', summary.all_running ? spanWithTone('Hazir', 'ok') : spanWithTone('Kontrol et', 'warn')),
        ].join('');

        return `
            <section id="landing-overview" class="ws-root-section">
                <div class="ws-kpi-grid">${cards}</div>
            </section>
            <section class="ws-layout-row is-2-equal">
                ${renderLaneCard(
                    polymarket.title || 'Polymarket Copy Trade',
                    polymarket.description || 'Whale tracking ve paper copy operasyon paneli',
                    polymarket.status || serviceStatus,
                    [
                        ['Bugun follower PnL', formatCurrency(polymarket.today_pnl || 0)],
                        ['Aktif copy pozisyonu', formatInteger(polymarket.active_positions || 0)],
                        ['Takip edilen wallet', formatInteger(polymarket.tracked_wallets || 0)],
                    ],
                    CONFIG.laneLinks && CONFIG.laneLinks.polymarket ? CONFIG.laneLinks.polymarket : '#'
                )}
                ${renderLaneCard(
                    binance.title || 'Binance Trading Operations',
                    binance.description || 'Spot + futures paper trading operasyon paneli',
                    binance.status || serviceStatus,
                    [
                        ['Gunluk PnL', formatCurrency(binance.today_pnl || 0)],
                        ['Acik pozisyon', formatInteger(binance.active_positions || 0)],
                        ['Acik emir', formatInteger(binance.open_orders || 0)],
                    ],
                    CONFIG.laneLinks && CONFIG.laneLinks.binance ? CONFIG.laneLinks.binance : '#'
                )}
            </section>
            <section id="landing-notes" class="ws-section-card">
                <div class="ws-section-head">
                    <div>
                        <div class="ws-section-kicker">Kontrol merkezi</div>
                        <h2 class="ws-section-title">Ilk bakista cevaplanan sorular</h2>
                        <p class="ws-section-note">Landing sayfasi teknik detay gostermez. Sadece hangi lane'in nasil durumda oldugunu ve hangi panele girmeniz gerektigini soyler.</p>
                    </div>
                </div>
                <div class="ws-card-grid is-3">
                    ${renderMiniNoteCard('Sistem calisiyor mu?', 'Her lane kartinda anlik servis durumu ve son senkron bilgisi var.')}
                    ${renderMiniNoteCard('Bugun ne oldu?', 'Her lane kartinda bugunluk PnL ve acik pozisyon sayisi gosteriliyor.')}
                    ${renderMiniNoteCard('Nereye bakmaliyim?', 'Detay gerekiyorsa ilgili lane paneline gecip ana tablo ve feed alanina odaklanin.')}
                </div>
            </section>
        `;
    }

    function renderPolymarketPage(summary, payload) {
        const top = summary.top_kpis || {};
        const workProof = summary.work_proof || {};
        const copyPortfolio = summary.copy_portfolio_summary || {};
        const segments = Array.isArray(summary.watchlist_segments) ? summary.watchlist_segments : [];
        const charts = summary.performance_chart_series || {};
        const trackedWallets = Array.isArray(summary.tracked_wallets) ? summary.tracked_wallets : [];
        const liveFeed = Array.isArray(summary.recent_whale_actions) ? summary.recent_whale_actions : [];
        const openCopyPositions = Array.isArray(summary.open_copy_positions) ? summary.open_copy_positions : [];
        const recentClosed = Array.isArray(summary.recent_closed_copy_positions) ? summary.recent_closed_copy_positions : [];
        const decisions = Array.isArray(summary.decision_explainability_rows) ? summary.decision_explainability_rows : [];

        state.walletDetails = new Map(trackedWallets.map((row) => [String(row.address || row.address_short || row.wallet_label), row]));

        const kpiCards = [
            renderStatCard('Servis durumu', top.service_status?.label || 'Bilinmiyor', 'Copy runtime ve research health', statusAccent(top.service_status?.label || '', top.service_status?.tone || 'neutral')),
            renderStatCard('Takip edilen balina', formatInteger(top.tracked_whales || 0), 'Aktif whale izleme havuzu'),
            renderStatCard('Aktif copy pozisyonu', formatInteger(top.active_copy_positions || 0), 'Su an acik follower trade'),
            renderStatCard('Portfoy degeri', formatCurrency(top.portfolio_value || 0), 'Paper copy sermayesi'),
            renderStatCard('Gunluk PnL', formatCurrency(top.daily_pnl || 0), 'Bugun follower sonuc', statusAccentNumber(top.daily_pnl || 0)),
            renderStatCard('7 gun / ROI', formatCurrency(top.seven_day_pnl || 0), 'ROI: ' + formatPercent(top.seven_day_roi, { fallback: 'veri bekleniyor' }), statusAccentNumber(top.seven_day_pnl || 0)),
            renderStatCard('Copy success', formatPercent(top.copy_success_rate, { fallback: 'veri bekleniyor' }), 'Kapanan follower islemleri'),
            renderStatCard('Son senkron', top.last_sync_at ? formatDateTime(top.last_sync_at) : 'Bekleniyor', 'Research + copy lane'),
        ].join('');

        const trackedWalletTable = renderTable(
            [
                { label: 'Wallet', render: (row) => `${escapeHtml(row.wallet_label || row.address_short || '--')}<div class="ws-muted">${escapeHtml(row.address_short || '--')}</div>` },
                { label: 'Tier', className: 'is-tight', render: (row) => renderWalletScoreBadge(row.tier || 'C', row.final_score || 0) },
                { label: 'Final score', className: 'is-tight', render: (row) => formatScore(row.final_score) },
                { label: 'Followability', className: 'is-tight', render: (row) => formatScore(row.followability_score) },
                { label: '7g PnL', className: 'is-tight', render: (row) => renderPnlBadge(row.pnl_7d) },
                { label: '30g PnL', className: 'is-tight', render: (row) => renderPnlBadge(row.pnl_30d) },
                { label: 'Win rate', className: 'is-tight', render: (row) => formatPercent(row.win_rate, { fallback: 'veri bekleniyor' }) },
                { label: 'Son islem', className: 'is-tight', render: (row) => formatShortDate(row.last_trade_at) },
                { label: 'Son aksiyon', className: 'is-tight', render: (row) => renderActionBadge(row.last_action) },
                { label: 'Durum', className: 'is-tight', render: (row) => renderStatusBadge(row.copy_status, row.copy_status_tone) },
                { label: 'Detay', className: 'is-tight', render: (row) => `<button type="button" class="ws-action-button" data-wallet-detail-open="${escapeAttribute(row.address || row.address_short || '')}">Detay</button>` },
            ],
            trackedWallets,
            {
                emptyTitle: 'Henuz izlenen balina yok',
                emptyCopy: 'Watchlist dolduruldugunda burada operatorin bakacagi ana tablo gosterilir.',
            }
        );

        const liveFeedTable = renderTable(
            [
                { label: 'Zaman', className: 'is-tight', render: (row) => formatShortDate(row.timestamp) },
                { label: 'Wallet', className: 'is-tight', render: (row) => escapeHtml(row.wallet_short || '--') },
                { label: 'Market', render: (row) => escapeHtml(row.market || '--') },
                { label: 'Aksiyon', className: 'is-tight', render: (row) => renderActionBadge(row.action) },
                { label: 'Boyut', className: 'is-tight', render: (row) => formatCurrency(row.size || 0) },
                { label: 'Bot karari', className: 'is-tight', render: (row) => renderStatusBadge(row.bot_decision || 'ignored', row.bot_decision === 'copied' ? 'ok' : row.bot_decision === 'rejected' ? 'danger' : 'info') },
                { label: 'Neden', render: (row) => escapeHtml(row.reason || row.reject_reason || '--') },
            ],
            liveFeed,
            {
                emptyTitle: 'Canli balina akisi bos',
                emptyCopy: 'Copy lane yeni aksiyon bekliyor.',
            }
        );

        const portfolioMetrics = renderCompactMetricGrid([
            ['Ayrilan sermaye', formatCurrency(copyPortfolio.allocated_capital || 0)],
            ['Kullanilabilir bakiye', formatCurrency(copyPortfolio.available_balance || 0)],
            ['Gerceklesmis PnL', formatCurrency(copyPortfolio.realized_pnl || 0)],
            ['Gerceklesmemis PnL', formatCurrency(copyPortfolio.unrealized_pnl || 0)],
            ['Bugun acilan', formatInteger(copyPortfolio.opened_today || 0)],
            ['Bugun kapanan', formatInteger(copyPortfolio.closed_today || 0)],
            ['En iyi pozisyon', copyPortfolio.best_position ? renderPnlBadge(copyPortfolio.best_position.follower_pnl || 0) : 'veri bekleniyor'],
            ['En kotu pozisyon', copyPortfolio.worst_position ? renderPnlBadge(copyPortfolio.worst_position.follower_pnl || 0) : 'veri bekleniyor'],
        ]);

        const openPositionsTable = renderTable(
            [
                { label: 'Market', render: (row) => escapeHtml(row.market || '--') },
                { label: 'Yon', className: 'is-tight', render: (row) => renderActionBadge(row.side) },
                { label: 'Kaynak', className: 'is-tight', render: (row) => escapeHtml(row.source_wallet || '--') },
                { label: 'Boyut', className: 'is-tight', render: (row) => formatCurrency(row.size || 0) },
                { label: 'Notional', className: 'is-tight', render: (row) => formatCurrency(row.notional || 0) },
                { label: 'Durum', className: 'is-tight', render: (row) => renderStatusBadge(row.status || 'OPEN', row.status === 'OPEN' ? 'ok' : 'info') },
                { label: 'Acilis', className: 'is-tight', render: (row) => formatShortDate(row.opened_at) },
            ],
            openCopyPositions,
            {
                emptyTitle: 'Acik copy pozisyonu yok',
                emptyCopy: 'Copy-ready veya pilot cohort yeni islem actiginda burada gorunur.',
            }
        );

        const closedPositionsTable = renderTable(
            [
                { label: 'Market', render: (row) => escapeHtml(row.market || '--') },
                { label: 'Kaynak', className: 'is-tight', render: (row) => escapeHtml(row.source_wallet || '--') },
                { label: 'Biz takip etseydik kar/zarar', className: 'is-tight', render: (row) => renderPnlBadge(row.follower_pnl || 0) },
                { label: 'Kaynak wallet PnL', className: 'is-tight', render: (row) => renderPnlBadge(row.source_pnl || 0) },
                { label: 'Durum', className: 'is-tight', render: (row) => renderStatusBadge(row.status || '--', toneFromPnl(row.follower_pnl || 0)) },
                { label: 'Kapanis', className: 'is-tight', render: (row) => formatShortDate(row.closed_at) },
            ],
            recentClosed,
            {
                emptyTitle: 'Henuz kapanan follower islem yok',
                emptyCopy: 'Replay veya gercek paper close geldikce bu liste dolar.',
            }
        );

        const decisionTimeline = renderDecisionTimeline(decisions, {
            emptyTitle: 'Karar motoru sessiz',
            emptyCopy: 'Copy engine yeni karar satiri yazdiginda burada neden kopyalandigi gorunur.',
        });

        const segmentCards = segments.length > 0
            ? `<div class="ws-segment-grid">${segments.map((segment) => `
                    <div class="ws-segment-card">
                        ${renderStatusBadge(segment.label, segment.tone || 'info')}
                        <div class="ws-segment-value">${escapeHtml(formatInteger(segment.count || 0))}</div>
                        <div class="ws-muted">Watchlist segment durumu</div>
                    </div>
                `).join('')}</div>`
            : renderEmptyState('Segment verisi yok', 'Wallet cohort dagilimi geldiginde burada gruplar gorunur.');

        const workProofCard = renderChecklistCard('Sistem calisma kaniti', [
            ['Test kaniti', !!workProof.test_proof_passed],
            ['Gercek paper akisi', !!workProof.runtime_proof_passed],
            ['Runtime open action', !!workProof.runtime_open_action_observed],
            ['Runtime open position', !!workProof.runtime_open_position_observed],
        ], workProof.fixture_note || 'Test kaniti ana performansa dahil edilmez.');

        const detailTabs = renderDetailTabs(
            'polymarket',
            state.polymarketDetailTab,
            [
                { key: 'analysis', label: 'Detayli Cuzdan Analizi' },
                { key: 'shadow', label: 'Shadow Kaniti' },
                { key: 'logs', label: 'Teknik Log' },
            ],
            {
                analysis: `
                    ${renderMetricGrid([
                        ['Copy-ready wallet', formatInteger(payload.copy_ready_wallet_summary?.copy_ready_wallets || 0)],
                        ['Shadow-ready', payload.shadow_edge_summary?.shadow_ready ? 'evet' : 'hayir'],
                        ['Linked wallet', formatInteger(payload.linked_wallet_evidence_summary?.linked_wallets_total || 0)],
                        ['Ana wallet', escapeHtml(summary.wallet_copy_status?.main_wallet_name || 'ohenism')],
                    ])}
                    ${renderTable(
                        [
                            { label: 'Wallet', render: (row) => escapeHtml(row.display_name || row.address || '--') },
                            { label: 'Primary source', className: 'is-tight', render: (row) => escapeHtml(row.primary_source || '--') },
                            { label: 'Specialization', className: 'is-tight', render: (row) => escapeHtml(row.specialization || '--') },
                            { label: 'Long-horizon', className: 'is-tight', render: (row) => formatScore(row.long_horizon_score) },
                            { label: 'Pilot gate', className: 'is-tight', render: (row) => renderStatusBadge(row.pilot_copy_gate_status || 'blocked', gateTone(row.pilot_copy_gate_status || 'blocked')) },
                            { label: 'Neden', render: (row) => escapeHtml(row.pilot_copy_gate_reason || row.shadow_gate_reason || '--') },
                        ],
                        Array.isArray(payload.wallet_consistency_table) ? payload.wallet_consistency_table.slice(0, 8) : [],
                        { emptyTitle: 'Cuzdan analiz verisi yok', emptyCopy: 'Research table olustugunda ayrintilar burada gorunur.' }
                    )}
                `,
                shadow: `
                    ${renderMetricGrid([
                        ['Gecikmeli takip avantaji', formatCurrency(payload.shadow_edge_summary?.net_shadow_edge || 0)],
                        ['Net shadow PnL', formatCurrency(payload.shadow_edge_summary?.net_shadow_pnl || 0)],
                        ['Replay rows', formatInteger(payload.shadow_evidence_backfill_summary?.replay_rows_created || 0)],
                        ['Evidence status', escapeHtml(payload.linked_wallet_evidence_summary?.linked_with_trade_history ? 'trade history var' : 'veri bekleniyor')],
                    ])}
                    ${renderTable(
                        [
                            { label: 'Wallet', className: 'is-tight', render: (row) => escapeHtml(shortAddress(row.wallet_address || '--')) },
                            { label: 'Market', render: (row) => escapeHtml(row.market_id || '--') },
                            { label: 'Aksiyon', className: 'is-tight', render: (row) => renderActionBadge(row.action_type) },
                            { label: 'Shadow PnL', className: 'is-tight', render: (row) => renderPnlBadge(row.shadow_pnl || 0) },
                            { label: 'Shadow edge', className: 'is-tight', render: (row) => renderPnlBadge(row.shadow_edge || 0) },
                        ],
                        Array.isArray(payload.recent_shadow_actions) ? payload.recent_shadow_actions.slice(0, 6) : [],
                        { emptyTitle: 'Shadow akis bos', emptyCopy: 'Replay ve delayed takip satirlari burada gorunur.' }
                    )}
                `,
                logs: `
                    ${renderMetricGrid([
                        ['Eligible copy wallets', formatInteger(summary.wallet_copy_status?.eligible_copy_wallets_total || 0)],
                        ['Copy blocker', escapeHtml(summary.wallet_copy_status?.copy_blocker_reason || '--')],
                        ['Reject sayisi', formatInteger((payload.copy_reject_breakdown || []).length)],
                        ['Warning sayisi', formatInteger((payload.warnings || []).length)],
                    ])}
                    ${renderTable(
                        [
                            { label: 'Reason', render: (row) => escapeHtml(row.reason || '--') },
                            { label: 'Count', className: 'is-tight', render: (row) => formatInteger(row.count || 0) },
                        ],
                        Array.isArray(payload.copy_reject_breakdown) ? payload.copy_reject_breakdown : [],
                        { emptyTitle: 'Copy reject verisi yok', emptyCopy: 'Engine red nedenleri geldikce bu tabloda toplanir.' }
                    )}
                    ${renderGlossaryBlock(payload.dashboard_glossary?.['polymarket-copy'] || payload.dashboard_glossary?.['polymarket-research'] || [])}
                `,
            }
        );

        return `
            <section id="overview" class="ws-root-section">
                <div class="ws-kpi-grid is-compact">${kpiCards}</div>
            </section>
            <div class="ws-hero-grid is-polymarket">
                <section id="tracked-wallets" class="ws-section-card ws-priority-card">
                    <div class="ws-section-head">
                        <div>
                            <div class="ws-section-kicker">Ana tablo</div>
                            <h2 class="ws-section-title">Takipteki Balinalar</h2>
                            <p class="ws-section-note">Hangi wallet daha guvenilir, en son ne yapti ve copy durumu ne hemen gorunur.</p>
                        </div>
                        <div class="ws-section-actions">
                            ${renderStatusBadge('Aktif uzman: ' + (summary.wallet_copy_status?.main_wallet_name || 'Ana wallet yok'), 'info')}
                        </div>
                    </div>
                    ${trackedWalletTable}
                </section>
                <aside class="ws-side-stack">
                    <section class="ws-section-card ws-accent-card">
                        <div class="ws-section-head">
                            <div>
                                <div class="ws-section-kicker">Portfoy ozeti</div>
                                <h2 class="ws-section-title">Bizim Copy Trade Ozeti</h2>
                                <p class="ws-section-note">Sermaye, follower sonuc ve gunluk hareketler tek kartta.</p>
                            </div>
                        </div>
                        ${portfolioMetrics}
                    </section>
                    ${workProofCard}
                </aside>
            </div>
            <section class="ws-section-card ws-performance-band" aria-label="Polymarket kazanc grafikleri">
                <div class="ws-section-head">
                    <div>
                        <div class="ws-section-kicker">Performans</div>
                        <h2 class="ws-section-title">Portfolio Equity ve PnL Grafikleri</h2>
                        <p class="ws-section-note">Kazanc trendi, gunluk PnL ve copy hizi ilk ekranda net gorunsun.</p>
                    </div>
                </div>
                <div class="ws-chart-grid is-featured">
                    ${renderChartCard('Portfolio Equity', charts.equity_trend || [], 'line', 'Toplam follower realize etkisi', { variant: 'hero', tone: 'ok' })}
                    ${renderChartCard('Daily PnL', charts.daily_pnl_trend || [], 'bar', 'Son 7 gun follower sonuc', { variant: 'hero' })}
                    ${renderChartCard('Copy Trade Count', charts.copied_trade_count_trend || [], 'bar', 'Gunluk aksiyon adedi', { variant: 'mini', tone: 'info', valueMode: 'integer' })}
                </div>
            </section>
            <div class="ws-layout-row is-2">
                <section id="whale-feed" class="ws-section-card">
                    <div class="ws-section-head">
                        <div>
                            <div class="ws-section-kicker">Canli akis</div>
                            <h2 class="ws-section-title">Canli Balina Islem Akisi</h2>
                            <p class="ws-section-note">Su an hangi kaynak wallet ne yapti ve bot nasil karar verdi sorusunun cevabi.</p>
                        </div>
                    </div>
                    ${liveFeedTable}
                </section>
                <section id="copy-positions" class="ws-section-card">
                    <div class="ws-section-head">
                        <div>
                            <div class="ws-section-kicker">Acik copy islemleri</div>
                            <h2 class="ws-section-title">Acik Kopya Pozisyonlar</h2>
                        </div>
                    </div>
                    ${openPositionsTable}
                </section>
            </div>
            <div class="ws-layout-row is-2-equal">
                <section class="ws-section-card">
                    <div class="ws-section-head">
                        <div>
                            <div class="ws-section-kicker">Son kapananlar</div>
                            <h2 class="ws-section-title">Son Kapanan Islemler</h2>
                        </div>
                    </div>
                    ${closedPositionsTable}
                </section>
            </div>
            <div class="ws-layout-row is-2">
                <section id="decision-engine" class="ws-section-card">
                    <div class="ws-section-head">
                        <div>
                            <div class="ws-section-kicker">Explainability</div>
                            <h2 class="ws-section-title">Karar Motoru / Neden Kopyalandi?</h2>
                            <p class="ws-section-note">Teknik duvara donmeden son kararlarin nedenlerini operator diliyle anlatir.</p>
                        </div>
                    </div>
                    ${decisionTimeline}
                </section>
                <section id="watch-segments" class="ws-section-card">
                    <div class="ws-section-head">
                        <div>
                            <div class="ws-section-kicker">Wallet segmentleri</div>
                            <h2 class="ws-section-title">Watchlist Segmentleri</h2>
                        </div>
                    </div>
                    ${segmentCards}
                </section>
            </div>
            <section id="detail-tabs" class="ws-section-card">
                <div class="ws-section-head">
                    <div>
                        <div class="ws-section-kicker">Ikincil detaylar</div>
                        <h2 class="ws-section-title">Detay / Teknik Kanit</h2>
                        <p class="ws-section-note">Ana ekran kalabaliklasmasin diye daha teknik ve kanit odakli bloklar burada tutulur.</p>
                    </div>
                </div>
                ${detailTabs}
            </section>
        `;
    }

    function renderBinancePage(summary, payload) {
        const top = summary.top_kpis || {};
        const filters = summary.filter_options || {};
        const strategy = summary.strategy_status_summary || {};
        const workProof = summary.work_proof || {};
        const risk = summary.risk_summary || {};
        const fresh = summary.fresh_pnl_summary || {};
        const technical = summary.technical_quality_summary || {};
        const charts = summary.performance_chart_series || {};

        const openPositions = applyBinanceFilters(Array.isArray(summary.open_positions) ? summary.open_positions : [], state.binanceFilters);
        const openOrders = applyBinanceFilters(Array.isArray(summary.open_orders) ? summary.open_orders : [], state.binanceFilters);
        const decisionFeed = applyBinanceFilters(Array.isArray(summary.runtime_decision_feed) ? summary.runtime_decision_feed : [], state.binanceFilters, true);
        const recentClosed = applyBinanceFilters(Array.isArray(summary.recent_closed_trades) ? summary.recent_closed_trades : [], state.binanceFilters);

        const kpiCards = [
            renderStatCard('Servis durumu', top.service_status?.label || 'Bilinmiyor', 'Paper runtime health', statusAccent(top.service_status?.label || '', top.service_status?.tone || 'neutral')),
            renderStatCard('Toplam hesap degeri', formatCurrency(top.total_account_value || 0), 'Spot + futures'),
            renderStatCard('Spot equity', formatCurrency(top.spot_equity || 0), 'Spot bakiye'),
            renderStatCard('Futures equity', formatCurrency(top.futures_equity || 0), 'Futures bakiye'),
            renderStatCard('Gunluk toplam PnL', formatCurrency(top.daily_total_pnl || 0), 'Bugun kapanan fresh paper islemler', statusAccentNumber(top.daily_total_pnl || 0)),
            renderStatCard('7 gun PnL', formatCurrency(top.pnl_7d || 0), 'Fresh paper KPI', statusAccentNumber(top.pnl_7d || 0)),
            renderStatCard('Acik pozisyon', formatInteger(top.open_positions || 0), 'Spot + futures'),
            renderStatCard('Acik emir', formatInteger(top.open_orders || 0), 'Koruma ve tetik emirleri'),
            renderStatCard('Toplam risk exposure', formatPercent(top.total_risk_exposure, { fallback: '0%' }), 'Acilan notional / equity'),
            renderStatCard('Son runtime', top.last_runtime_at ? formatDateTime(top.last_runtime_at) : 'Bekleniyor', 'Karar motoru'),
        ].join('');

        const filterBar = `
            <div class="ws-filter-bar">
                ${renderFilterField('Symbol', 'symbol', filters.symbols || [], state.binanceFilters.symbol)}
                ${renderFilterField('Pazar', 'venue', (filters.venues || []).filter(Boolean), state.binanceFilters.venue)}
                ${renderFilterField('Taraf', 'side', (filters.sides || []).filter(Boolean), state.binanceFilters.side)}
                ${renderFilterField('Durum', 'status', (filters.statuses || []).filter(Boolean), state.binanceFilters.status)}
                <div class="ws-filter-field">
                    <label>Yenile</label>
                    <button type="button" class="ws-action-button" data-refresh>Veriyi yenile</button>
                </div>
            </div>
        `;

        const strategyMetrics = renderMetricGrid([
            ['Futures LONG', strategy.futures_long?.allowed ? renderStatusBadge('izin veriliyor', 'ok') : renderStatusBadge('kapali', 'danger')],
            ['Futures SHORT', strategy.futures_short?.allowed ? renderStatusBadge('izin veriliyor', 'ok') : renderStatusBadge('kapali', 'danger')],
            ['Spot LONG', strategy.spot_long?.allowed ? renderStatusBadge('izin veriliyor', 'ok') : renderStatusBadge('kapali', 'danger')],
            ['Spot SHORT', strategy.spot_short?.allowed ? renderStatusBadge('izin veriliyor', 'ok') : renderStatusBadge('reddediliyor', 'warn')],
            ['Score threshold', formatScore(strategy.score_threshold)],
            ['Acceptance gate', strategy.acceptance_gate ? renderStatusBadge('acik', 'ok') : renderStatusBadge('kapali', 'danger')],
            ['Runtime karar sayisi', formatInteger(strategy.runtime_decision_rows || 0)],
        ]);

        const riskMetrics = renderMetricGrid([
            ['Max trade risk', formatPercent(risk.max_trade_risk_pct, { fallback: '0%' })],
            ['Current exposure', renderRiskBadge(risk.current_exposure_pct || 0)],
            ['Symbol concentration', risk.symbol_concentration?.symbol ? `${escapeHtml(risk.symbol_concentration.symbol)} / ${formatPercent(risk.symbol_concentration.pct, { fallback: '0%' })}` : 'veri bekleniyor'],
            ['Daily drawdown', renderRiskBadge(risk.daily_drawdown_pct || 0, true)],
            ['Kill switch', renderStatusBadge(risk.kill_switch_status || 'KAPALI', risk.kill_switch_status === 'KAPALI' ? 'ok' : 'danger')],
            ['Rejected trades bugun', formatInteger(risk.rejected_trades_today || 0)],
        ]);

        const openPositionsTable = renderTable(
            [
                { label: 'Venue', className: 'is-tight', render: (row) => renderVenueBadge(row.venue) },
                { label: 'Symbol', render: (row) => escapeHtml(row.symbol_or_market_id || row.market_id || '--') },
                { label: 'Taraf', className: 'is-tight', render: (row) => renderActionBadge(row.side) },
                { label: 'Leverage', className: 'is-tight', render: (row) => row.venue === 'binance_futures' ? `${escapeHtml(String(row.leverage || '--'))}x` : '--' },
                { label: 'Qty', className: 'is-tight', render: (row) => formatNumber(row.qty || row.size || 0) },
                { label: 'Notional', className: 'is-tight', render: (row) => formatCurrency(row.notional_usd || 0) },
                { label: 'Entry', className: 'is-tight', render: (row) => formatNumber(row.entry_price || 0) },
                { label: 'Mark', className: 'is-tight', render: (row) => formatNumber(row.mark_price || 0) },
                { label: 'SL', className: 'is-tight', render: (row) => formatNumber(row.stop_loss || row.stop_price || 0, '--') },
                { label: 'TP', className: 'is-tight', render: (row) => formatNumber(row.take_profit || 0, '--') },
                { label: 'PnL', className: 'is-tight', render: (row) => renderPnlBadge(row.unrealized_pnl || row.pnl || 0) },
                { label: 'Acilis', className: 'is-tight', render: (row) => formatShortDate(row.opened_at) },
            ],
            openPositions,
            {
                emptyTitle: 'Filtreye uyan acik pozisyon yok',
                emptyCopy: 'Fresh paper veya koruma pozisyonlari burada listelenir.',
            }
        );

        const openOrdersTable = renderTable(
            [
                { label: 'Venue', className: 'is-tight', render: (row) => renderVenueBadge(row.venue) },
                { label: 'Symbol', render: (row) => escapeHtml(row.symbol_or_market_id || row.market_id || '--') },
                { label: 'Tip', className: 'is-tight', render: (row) => escapeHtml(row.order_type || '--') },
                { label: 'Taraf', className: 'is-tight', render: (row) => renderActionBadge(row.side) },
                { label: 'Trigger / stop', className: 'is-tight', render: (row) => formatNumber(row.stop_price || row.price || 0) },
                { label: 'Qty', className: 'is-tight', render: (row) => formatNumber(row.qty || 0) },
                { label: 'Reduce only', className: 'is-tight', render: (row) => row.reduce_only ? renderStatusBadge('evet', 'warn') : 'hayir' },
                { label: 'Olusturuldu', className: 'is-tight', render: (row) => formatShortDate(row.created_at) },
                { label: 'Durum', className: 'is-tight', render: (row) => renderStatusBadge(row.status || '--', row.status === 'OPEN' ? 'ok' : 'info') },
            ],
            openOrders,
            {
                emptyTitle: 'Acik emir yok',
                emptyCopy: 'Stop loss, take profit ve trigger emirleri burada gorunur.',
            }
        );

        const freshSummary = renderCompactMetricGrid([
            ['Bugun', renderPnlBadge(fresh.today || 0)],
            ['7 gun', renderPnlBadge(fresh.seven_days || 0)],
            ['Futures PnL', renderPnlBadge(fresh.futures_pnl || 0)],
            ['Spot PnL', renderPnlBadge(fresh.spot_pnl || 0)],
            ['Win rate', formatPercent(fresh.win_rate, { fallback: 'veri bekleniyor' })],
            ['Kapanan trade', formatInteger(fresh.closed_trade_count || 0)],
            ['Average winner', fresh.average_winner == null ? 'veri bekleniyor' : renderPnlBadge(fresh.average_winner)],
            ['Average loser', fresh.average_loser == null ? 'veri bekleniyor' : renderPnlBadge(fresh.average_loser)],
        ]);

        const runtimeTimeline = renderDecisionTimeline(
            decisionFeed.map((row) => ({
                timestamp: row.timestamp,
                title: `${row.symbol || '--'} / ${row.venue || '--'}`,
                verdict: row.final_verdict,
                summary: row.reason,
                metrics: [
                    ['Score', formatScore(row.score)],
                    ['Threshold', formatScore(row.threshold)],
                    ['Aksiyon', row.action || '--'],
                ],
            })),
            {
                emptyTitle: 'Runtime karar akisi bos',
                emptyCopy: 'Decision audit satirlari geldikce burada son kararlar gorunur.',
            }
        );

        const workProofCard = renderChecklistCard('Sistem calisma kaniti', [
            ['Futures LONG', !!workProof.futures_long_execute],
            ['Futures SHORT', !!workProof.futures_short_execute],
            ['Spot LONG', !!workProof.spot_long_execute],
            ['Spot SHORT reject', !!workProof.spot_short_reject],
            ['Gercek paper akisi', !!workProof.runtime_proof_passed],
        ], workProof.fixture_note || 'Test kaniti fresh PnL hesabina dahil edilmez.');

        const tradeHistoryTable = renderTable(
            [
                { label: 'Symbol', render: (row) => escapeHtml(row.symbol_or_market_id || row.market_id || '--') },
                { label: 'Venue', className: 'is-tight', render: (row) => renderVenueBadge(row.venue) },
                { label: 'Taraf', className: 'is-tight', render: (row) => renderActionBadge(row.side) },
                { label: 'Entry', className: 'is-tight', render: (row) => formatNumber(row.price || row.entry_price || 0, '--') },
                { label: 'PnL', className: 'is-tight', render: (row) => renderPnlBadge(row.pnl || 0) },
                { label: 'Result', className: 'is-tight', render: (row) => renderStatusBadge((row.pnl || 0) >= 0 ? 'WIN' : 'LOSS', (row.pnl || 0) >= 0 ? 'ok' : 'danger') },
                { label: 'Kapanis', className: 'is-tight', render: (row) => formatShortDate(row.timestamp || row.closed_at) },
            ],
            recentClosed,
            {
                emptyTitle: 'Henuz kapanan paper trade yok',
                emptyCopy: 'Fresh paper close geldikce basari yuzdesi ve trade history burada dolacak.',
            }
        );

        const detailTabs = renderDetailTabs(
            'binance',
            state.binanceDetailTab,
            [
                { key: 'signals', label: 'Sinyal Detayi' },
                { key: 'rejects', label: 'Red Nedenleri' },
                { key: 'pressure', label: 'Pozisyon Baskisi' },
                { key: 'logs', label: 'Teknik Log' },
            ],
            {
                signals: `
                    ${renderMetricGrid([
                        ['Ortalama score', formatScore(technical.avg_score)],
                        ['Ort. threshold', formatScore(technical.avg_threshold)],
                        ['Ort. RSI', formatScore(technical.avg_rsi)],
                        ['Ort. MACD', formatScore(technical.avg_macd)],
                        ['Ort. momentum', formatScore(technical.avg_momentum)],
                        ['Near-threshold', formatInteger(technical.near_threshold_count || 0)],
                    ])}
                    ${renderChartCard('Pnl trend', charts.pnl_trend || [], 'line', 'Kapanan fresh paper islemler')}
                `,
                rejects: `
                    ${renderTable(
                        [
                            { label: 'Reason', render: (row) => escapeHtml(row.reason || '--') },
                            { label: 'Count', className: 'is-tight', render: (row) => formatInteger(row.count || 0) },
                        ],
                        Array.isArray(payload.technical_reject_breakdown) ? payload.technical_reject_breakdown : [],
                        { emptyTitle: 'Red verisi yok', emptyCopy: 'Runtime reject nedenleri geldikce burada toplanir.' }
                    )}
                `,
                pressure: `
                    ${renderMetricGrid([
                        ['Acik pozisyon', formatInteger(payload.position_pressure_summary?.open_positions || 0)],
                        ['Fresh acik', formatInteger(payload.position_pressure_summary?.fresh_open_positions || 0)],
                        ['Legacy acik', formatInteger(payload.position_pressure_summary?.legacy_open_positions || 0)],
                        ['Acik notional', formatCurrency(payload.position_pressure_summary?.open_notional_usd || 0)],
                        ['En eski acik', formatInteger(payload.position_pressure_summary?.oldest_open_position_minutes || 0) + ' dk'],
                        ['Acilik', renderRiskBadge(risk.current_exposure_pct || 0)],
                    ])}
                `,
                logs: `
                    ${workProofCard}
                    ${renderGlossaryBlock(payload.dashboard_glossary?.['binance-technical'] || [])}
                `,
            }
        );

        return `
            <section id="overview" class="ws-root-section">
                <div class="ws-kpi-grid is-compact">${kpiCards}</div>
            </section>
            <section id="filters" class="ws-section-card">
                <div class="ws-section-head">
                    <div>
                        <div class="ws-section-kicker">Filtreler</div>
                        <h2 class="ws-section-title">Sembol / Pazar Filtre Alani</h2>
                        <p class="ws-section-note">Acik pozisyon, emir, runtime feed ve trade history tablolarini ayni anda filtreler.</p>
                    </div>
                </div>
                ${filterBar}
            </section>
            <div class="ws-layout-row is-2-equal">
                <section class="ws-section-card">
                    <div class="ws-section-head">
                        <div>
                            <div class="ws-section-kicker">Strateji durumu</div>
                            <h2 class="ws-section-title">Bu sistem neye izin veriyor?</h2>
                        </div>
                    </div>
                    ${strategyMetrics}
                </section>
                <section id="risk-panel" class="ws-section-card">
                    <div class="ws-section-head">
                        <div>
                            <div class="ws-section-kicker">Risk paneli</div>
                            <h2 class="ws-section-title">Risk Durumu</h2>
                        </div>
                    </div>
                    ${riskMetrics}
                </section>
            </div>
            <div class="ws-layout-row is-2-equal">
                <section id="open-positions" class="ws-section-card">
                    <div class="ws-section-head">
                        <div>
                            <div class="ws-section-kicker">Ana tablo</div>
                            <h2 class="ws-section-title">Acik Pozisyonlar</h2>
                        </div>
                    </div>
                    ${openPositionsTable}
                </section>
                <section id="open-orders" class="ws-section-card">
                    <div class="ws-section-head">
                        <div>
                            <div class="ws-section-kicker">Koruma katmani</div>
                            <h2 class="ws-section-title">Acik Emirler</h2>
                        </div>
                    </div>
                    ${openOrdersTable}
                </section>
            </div>
            <div class="ws-layout-row is-2">
                <section class="ws-section-card ws-performance-band">
                    <div class="ws-section-head">
                        <div>
                            <div class="ws-section-kicker">Fresh PnL</div>
                            <h2 class="ws-section-title">Fresh PnL Ozeti</h2>
                            <p class="ws-section-note">Kazanc grafigi, kapanan trade hizi ve spot/futures dagilimi ilk operasyon alaninda gorunur.</p>
                        </div>
                    </div>
                    ${freshSummary}
                    <div class="ws-chart-grid is-featured is-binance">
                        ${renderChartCard('7g PnL Trend', charts.pnl_trend || [], 'line', 'Kapanan fresh paper islemler', { variant: 'hero' })}
                        ${renderChartCard('Trade Count', charts.trade_count_trend || [], 'bar', 'Gunluk kapanan trade adedi', { variant: 'mini', tone: 'info', valueMode: 'integer' })}
                        ${renderBreakdownChartCard('Spot / Futures / Risk', charts.equity_breakdown || [])}
                    </div>
                    <div class="ws-proof-inline">${workProofCard}</div>
                </section>
                <section id="runtime-feed" class="ws-section-card">
                    <div class="ws-section-head">
                        <div>
                            <div class="ws-section-kicker">Runtime feed</div>
                            <h2 class="ws-section-title">Son Kararlar / Runtime Feed</h2>
                        </div>
                    </div>
                    ${runtimeTimeline}
                </section>
            </div>
            <section id="trade-history" class="ws-section-card">
                <div class="ws-section-head">
                    <div>
                        <div class="ws-section-kicker">Trade history</div>
                        <h2 class="ws-section-title">Trade Gecmisi</h2>
                    </div>
                </div>
                ${tradeHistoryTable}
            </section>
            <section id="detail-tabs" class="ws-section-card">
                <div class="ws-section-head">
                    <div>
                        <div class="ws-section-kicker">Teknik detaylar</div>
                        <h2 class="ws-section-title">Detay / Teknik Kanit</h2>
                        <p class="ws-section-note">Ana ekran kalabaliklasmasin diye reject, teknik kalite ve log bloklari burada tutulur.</p>
                    </div>
                </div>
                ${detailTabs}
            </section>
        `;
    }

    function onDocumentClick(event) {
        const mobileToggle = event.target.closest('[data-mobile-toggle]');
        if (mobileToggle) {
            document.body.classList.toggle('ws-sidebar-open');
            return;
        }
        const refreshTrigger = event.target.closest('[data-refresh]');
        if (refreshTrigger) {
            loadAndRender();
            return;
        }
        const walletDetailTrigger = event.target.closest('[data-wallet-detail-open]');
        if (walletDetailTrigger) {
            openWalletDetail(walletDetailTrigger.getAttribute('data-wallet-detail-open') || '');
            return;
        }
        const modalCloseTrigger = event.target.closest('[data-modal-close]');
        if (modalCloseTrigger && modalEl && typeof modalEl.close === 'function') {
            modalEl.close();
            return;
        }
        const detailTab = event.target.closest('[data-detail-tab]');
        if (detailTab) {
            const lane = detailTab.getAttribute('data-detail-lane') || '';
            const key = detailTab.getAttribute('data-detail-tab') || '';
            if (lane === 'polymarket') {
                state.polymarketDetailTab = key;
            } else if (lane === 'binance') {
                state.binanceDetailTab = key;
            }
            if (state.payload) {
                renderRoot(state.payload);
            }
        }
    }

    function onDocumentChange(event) {
        const filterSelect = event.target.closest('[data-binance-filter]');
        if (!filterSelect) {
            return;
        }
        const filterKey = filterSelect.getAttribute('data-binance-filter');
        if (!filterKey) {
            return;
        }
        state.binanceFilters[filterKey] = String(filterSelect.value || 'all');
        if (state.payload) {
            renderRoot(state.payload);
        }
    }

    function openWalletDetail(key) {
        const wallet = state.walletDetails.get(key);
        if (!wallet || !modalEl || !modalContentEl) {
            return;
        }
        const detail = wallet.detail || {};
        const scoreBreakdown = detail.score_breakdown || {};
        const stability = detail.stability || {};
        const gateState = detail.gate_state || {};
        const recentClosed = Array.isArray(detail.recent_closed_positions) ? detail.recent_closed_positions : [];
        const reliableReasons = Array.isArray(detail.reliable_reasons) ? detail.reliable_reasons : [];
        const riskReasons = Array.isArray(detail.risk_reasons) ? detail.risk_reasons : [];

        modalContentEl.innerHTML = `
            <div class="ws-modal-header">
                <div>
                    <div class="ws-section-kicker">Wallet detay</div>
                    <h2 class="ws-section-title">${escapeHtml(wallet.wallet_label || wallet.address_short || '--')}</h2>
                    <p class="ws-section-note">${escapeHtml(wallet.address || wallet.address_short || '--')}</p>
                </div>
                <button type="button" class="ws-modal-close" data-modal-close>Kapat</button>
            </div>
            <div class="ws-layout-row is-2-equal">
                <div class="ws-section-card">
                    <div class="ws-section-kicker">Skor kirilimi</div>
                    ${renderKeyValueRows([
                        ['Long-horizon', formatScore(scoreBreakdown.long_horizon_score)],
                        ['Consistency', formatScore(scoreBreakdown.consistency_score)],
                        ['Trust', formatScore(scoreBreakdown.trust_score)],
                        ['Profit consistency', formatScore(scoreBreakdown.profit_consistency_score)],
                    ])}
                </div>
                <div class="ws-section-card">
                    <div class="ws-section-kicker">Stability / consistency</div>
                    ${renderKeyValueRows([
                        ['Specialization', escapeHtml(detail.specialization || '--')],
                        ['Crypto ratio', formatPercent(detail.crypto_participation_ratio, { fallback: '0%' })],
                        ['Active days', formatInteger(stability.active_days || 0)],
                        ['Closed trades', formatInteger(stability.closed_trade_count || 0)],
                        ['Observed actions', formatInteger(stability.observed_action_count || 0)],
                        ['Worst drawdown', formatPercent(stability.worst_drawdown_pct, { fallback: '0%' })],
                    ])}
                </div>
            </div>
            <div class="ws-layout-row is-2-equal">
                <div class="ws-section-card">
                    <div class="ws-section-kicker">Gate durumu</div>
                    ${renderKeyValueRows([
                        ['Shadow gate', `${escapeHtml(gateState.shadow_gate_status || '--')} / ${escapeHtml(gateState.shadow_gate_reason || '--')}`],
                        ['Pilot gate', `${escapeHtml(gateState.pilot_copy_gate_status || '--')} / ${escapeHtml(gateState.pilot_copy_gate_reason || '--')}`],
                        ['Copy-ready gate', `${escapeHtml(gateState.copy_ready_gate_status || '--')} / ${escapeHtml(gateState.copy_ready_gate_reason || '--')}`],
                        ['Evidence', escapeHtml(gateState.historical_trade_evidence_status || '--')],
                    ])}
                </div>
                <div class="ws-section-card">
                    <div class="ws-section-kicker">Neden guvenilir / riskli</div>
                    <div class="ws-detail-list">
                        <div>${renderReasonList('Guvenilir bulunan taraflar', reliableReasons, 'ok')}</div>
                        <div style="margin-top:12px;">${renderReasonList('Risk / dikkat notlari', riskReasons, 'warn')}</div>
                    </div>
                </div>
            </div>
            <div class="ws-section-card">
                <div class="ws-section-kicker">Son kapali pozisyonlar</div>
                ${renderTable(
                    [
                        { label: 'Market', render: (row) => escapeHtml(row.market_id || '--') },
                        { label: 'Action', className: 'is-tight', render: (row) => renderActionBadge(row.action_type) },
                        { label: 'Shadow PnL', className: 'is-tight', render: (row) => renderPnlBadge(row.shadow_pnl || 0) },
                        { label: 'Shadow edge', className: 'is-tight', render: (row) => renderPnlBadge(row.shadow_edge || 0) },
                        { label: 'Durum', className: 'is-tight', render: (row) => escapeHtml(row.status || '--') },
                    ],
                    recentClosed,
                    { emptyTitle: 'Son kapali pozisyon yok', emptyCopy: 'Bu wallet icin henuz kapanmis shadow kaydi yok.' }
                )}
            </div>
        `;
        if (typeof modalEl.showModal === 'function') {
            modalEl.showModal();
        }
    }

    function buildFallbackServiceStatus(service) {
        const active = !!service.active;
        return {
            label: active ? 'RUNNING' : 'STOPPED',
            tone: active ? 'ok' : 'danger',
            detail: service.status_text || '',
            last_sync_at: '',
            warning_count: 0,
        };
    }

    function renderLaneCard(title, description, status, metrics, href) {
        return `
            <section class="ws-lane-card">
                <div class="ws-section-head">
                    <div>
                        <div class="ws-section-kicker">Lane</div>
                        <h2 class="ws-section-title">${escapeHtml(title)}</h2>
                        <p class="ws-section-note">${escapeHtml(description)}</p>
                    </div>
                    <div class="ws-section-actions">${renderStatusBadge(status.label || 'Bilinmiyor', status.tone || 'neutral')}</div>
                </div>
                <div class="ws-metric-grid">
                    ${metrics.map(([label, value]) => `
                        <div class="ws-stat-card">
                            <div class="ws-stat-label">${escapeHtml(label)}</div>
                            <div class="ws-stat-value">${typeof value === 'string' ? value : escapeHtml(String(value))}</div>
                        </div>
                    `).join('')}
                </div>
                <div style="margin-top:16px;">
                    <a class="ws-action-link" href="${escapeAttribute(href)}">Panele git</a>
                </div>
            </section>
        `;
    }

    function renderMiniNoteCard(title, copy) {
        return `
            <div class="ws-section-card">
                <h3 class="ws-section-title">${escapeHtml(title)}</h3>
                <p class="ws-section-note">${escapeHtml(copy)}</p>
            </div>
        `;
    }

    function renderStatCard(label, value, note, accent) {
        return `
            <div class="ws-stat-card">
                <div class="ws-stat-label">${escapeHtml(label)}</div>
                <div class="ws-stat-value">${typeof value === 'string' ? value : escapeHtml(String(value))}</div>
                ${note ? `<div class="ws-stat-note">${escapeHtml(note)}</div>` : ''}
                ${accent ? `<div class="ws-stat-accent">${accent}</div>` : ''}
            </div>
        `;
    }

    function renderMetricGrid(items) {
        return `
            <div class="ws-metric-grid">
                ${items.map(([label, value]) => `
                    <div class="ws-stat-card">
                        <div class="ws-stat-label">${escapeHtml(label)}</div>
                        <div class="ws-stat-value">${typeof value === 'string' ? value : escapeHtml(String(value))}</div>
                    </div>
                `).join('')}
            </div>
        `;
    }

    function renderChecklistCard(title, items, note) {
        return `
            <div class="ws-section-card ws-checklist-card">
                <div class="ws-section-kicker">Sistem calisma kaniti</div>
                <h3 class="ws-section-title">${escapeHtml(title)}</h3>
                <div class="ws-checklist">
                    ${items.map(([label, passed]) => `
                        <div class="ws-checklist-row">
                            <span>${escapeHtml(label)}</span>
                            ${renderStatusBadge(passed ? 'hazir' : 'bekliyor', passed ? 'ok' : 'warn')}
                        </div>
                    `).join('')}
                </div>
                ${note ? `<p class="ws-section-note">${escapeHtml(note)}</p>` : ''}
            </div>
        `;
    }

    function renderTable(columns, rows, options) {
        const safeRows = Array.isArray(rows) ? rows : [];
        if (safeRows.length === 0) {
            return renderEmptyState(options.emptyTitle, options.emptyCopy);
        }
        return `
            <div class="ws-table-wrap">
                <table class="ws-table">
                    <thead>
                        <tr>
                            ${columns.map((column) => `<th class="${escapeAttribute(column.className || '')}">${escapeHtml(column.label)}</th>`).join('')}
                        </tr>
                    </thead>
                    <tbody>
                        ${safeRows.map((row) => `
                            <tr>
                                ${columns.map((column) => `
                                    <td class="${escapeAttribute(column.className || '')}">
                                        ${column.render ? column.render(row) : escapeHtml(String(row[column.key] || ''))}
                                    </td>
                                `).join('')}
                            </tr>
                        `).join('')}
                    </tbody>
                </table>
            </div>
        `;
    }

    function renderDecisionTimeline(rows, options) {
        if (!Array.isArray(rows) || rows.length === 0) {
            return renderEmptyState(options.emptyTitle, options.emptyCopy);
        }
        return `
            <div class="ws-timeline">
                ${rows.map((row) => {
                    const title = row.title || row.source_wallet || '--';
                    const summary = row.summary || row.reason_summary || '--';
                    const verdict = row.final_verdict || row.verdict || '--';
                    const metrics = row.metrics || [
                        ['Score', formatScore(row.score)],
                        ['Threshold', formatScore(row.threshold)],
                        ['Likidite', row.liquidity_check || '--'],
                        ['Stale', row.stale_check || '--'],
                        ['Konsantrasyon', row.concentration_check || '--'],
                    ];
                    return `
                        <div class="ws-timeline-item">
                            <div class="ws-timeline-meta">
                                <span>${escapeHtml(formatShortDate(row.timestamp))}</span>
                                ${renderStatusBadge(verdict, verdictTone(verdict))}
                            </div>
                            <div class="ws-timeline-title">${escapeHtml(title)}</div>
                            <div class="ws-muted">${escapeHtml(summary)}</div>
                            <div class="ws-inline-metrics">
                                ${metrics.map(([label, value]) => `<span><strong>${escapeHtml(label)}:</strong> ${typeof value === 'string' ? value : escapeHtml(String(value))}</span>`).join('')}
                            </div>
                        </div>
                    `;
                }).join('')}
            </div>
        `;
    }

    function renderDetailTabs(lane, activeKey, tabs, panes) {
        return `
            <div class="ws-detail-tabs">
                ${tabs.map((tab) => `
                    <button
                        type="button"
                        class="ws-detail-tab ${tab.key === activeKey ? 'is-active' : ''}"
                        data-detail-lane="${escapeAttribute(lane)}"
                        data-detail-tab="${escapeAttribute(tab.key)}"
                    >
                        ${escapeHtml(tab.label)}
                    </button>
                `).join('')}
            </div>
            ${tabs.map((tab) => `
                <div class="ws-detail-pane ${tab.key === activeKey ? 'is-active' : ''}">
                    ${panes[tab.key] || ''}
                </div>
            `).join('')}
        `;
    }

    function renderChartCard(title, series, type, note, options = {}) {
        const normalized = Array.isArray(series) ? series : [];
        const lastValue = normalized.length > 0 ? Number(normalized[normalized.length - 1].value || 0) : 0;
        const tone = options.tone || toneFromPnl(lastValue);
        const valueBadge = options.valueMode === 'integer'
            ? renderStatusBadge(formatInteger(lastValue), tone || 'info')
            : renderPnlBadge(lastValue);
        const chart = normalized.length > 0
            ? renderSvgChart(normalized, type, { tone })
            : `<div class="ws-empty-state is-chart-empty"><h3>Grafik verisi bekleniyor</h3><p>Henuz grafik icin yeterli kapanmis islem yok.</p></div>`;
        const variantClass = options.variant ? ` is-${escapeAttribute(options.variant)}` : '';
        return `
            <div class="ws-chart-card${variantClass}">
                <div class="ws-chart-meta">
                    <h3>${escapeHtml(title)}</h3>
                    ${valueBadge}
                </div>
                ${chart}
                <div class="ws-chart-footer">
                    <span>${escapeHtml(note || '')}</span>
                    <span>${escapeHtml(String(normalized.length))} nokta</span>
                </div>
            </div>
        `;
    }

    function renderBreakdownChartCard(title, items) {
        const safeItems = Array.isArray(items) ? items : [];
        return `
            <div class="ws-chart-card">
                <div class="ws-chart-meta">
                    <h3>${escapeHtml(title)}</h3>
                    ${renderStatusBadge('Dagilim', 'info')}
                </div>
                <div class="ws-key-value">
                    ${safeItems.map((item) => `
                        <div class="ws-key-value-row">
                            <span class="ws-key">${escapeHtml(item.label || '--')}</span>
                            <span class="ws-value">${formatCurrency(item.value || 0)}</span>
                        </div>
                    `).join('')}
                </div>
            </div>
        `;
    }

    function renderFilterField(label, key, options, selectedValue) {
        const normalized = ['all'].concat(Array.isArray(options) ? options.filter((value) => value !== 'all') : []);
        return `
            <div class="ws-filter-field">
                <label>${escapeHtml(label)}</label>
                <select data-binance-filter="${escapeAttribute(key)}">
                    ${normalized.map((value) => `
                        <option value="${escapeAttribute(String(value))}" ${String(value) === String(selectedValue || 'all') ? 'selected' : ''}>
                            ${escapeHtml(filterLabel(value))}
                        </option>
                    `).join('')}
                </select>
            </div>
        `;
    }

    function renderStatusBadge(label, tone) {
        return `<span class="ws-badge ${toneClass(tone)}">${escapeHtml(String(label || '--'))}</span>`;
    }

    function renderWalletScoreBadge(tier, score) {
        const tone = tier === 'A' ? 'ok' : tier === 'B' ? 'info' : 'warn';
        return `<span class="ws-badge ${toneClass(tone)}">${escapeHtml(String(tier))} / ${escapeHtml(formatScore(score))}</span>`;
    }

    function renderPnlBadge(value) {
        return `<span class="${pnlClass(value)}">${escapeHtml(formatCurrency(value || 0))}</span>`;
    }

    function renderRiskBadge(value, inverse) {
        const numeric = Number(value || 0);
        let tone = 'ok';
        if (inverse) {
            tone = numeric >= 3 ? 'danger' : numeric >= 1 ? 'warn' : 'ok';
        } else {
            tone = numeric >= 75 ? 'danger' : numeric >= 45 ? 'warn' : 'ok';
        }
        return renderStatusBadge(formatPercent(numeric, { fallback: '0%' }), tone);
    }

    function renderActionBadge(action) {
        const normalized = String(action || '--').toUpperCase();
        let tone = 'info';
        if (normalized.includes('BUY') || normalized.includes('YES') || normalized.includes('LONG') || normalized.includes('OPEN') || normalized === 'COPIED') {
            tone = 'ok';
        } else if (normalized.includes('NO') || normalized.includes('SHORT') || normalized.includes('REJECT') || normalized.includes('EXIT')) {
            tone = normalized.includes('REJECT') ? 'danger' : 'warn';
        }
        return renderStatusBadge(normalized, tone);
    }

    function renderVenueBadge(venue) {
        const normalized = String(venue || '--');
        const tone = normalized.includes('futures') ? 'info' : normalized.includes('spot') ? 'ok' : 'neutral';
        return renderStatusBadge(normalized.replace('binance_', ''), tone);
    }

    function renderEmptyState(title, copy) {
        return `
            <div class="ws-empty-state">
                <h3>${escapeHtml(title)}</h3>
                <p>${escapeHtml(copy)}</p>
            </div>
        `;
    }

    function renderGlossaryBlock(items) {
        const safeItems = Array.isArray(items) ? items : [];
        if (safeItems.length === 0) {
            return renderEmptyState('Sozluk verisi yok', 'Bu lane icin aciklayici metin henuz gelmedi.');
        }
        return `
            <div class="ws-key-value">
                ${safeItems.map((item) => `
                    <div class="ws-key-value-row">
                        <span class="ws-key">${escapeHtml(item.term || '--')}</span>
                        <span class="ws-value">${escapeHtml(item.meaning || '--')}</span>
                    </div>
                `).join('')}
            </div>
        `;
    }

    function renderReasonList(title, reasons, tone) {
        return `
            <div>
                <div class="ws-section-kicker">${escapeHtml(title)}</div>
                ${Array.isArray(reasons) && reasons.length > 0
                    ? `<div class="ws-inline-metrics">${reasons.map((reason) => renderStatusBadge(reason, tone)).join('')}</div>`
                    : '<div class="ws-muted">Ozel not yok</div>'}
            </div>
        `;
    }

    function renderKeyValueRows(items) {
        return `
            <div class="ws-key-value">
                ${items.map(([key, value]) => `
                    <div class="ws-key-value-row">
                        <span class="ws-key">${escapeHtml(key)}</span>
                        <span class="ws-value">${typeof value === 'string' ? value : escapeHtml(String(value))}</span>
                    </div>
                `).join('')}
            </div>
        `;
    }

    function renderCompactMetricGrid(items) {
        return `
            <div class="ws-compact-metric-grid">
                ${items.map(([key, value]) => `
                    <div class="ws-compact-metric">
                        <span class="ws-key">${escapeHtml(key)}</span>
                        <strong>${typeof value === 'string' ? value : escapeHtml(String(value))}</strong>
                    </div>
                `).join('')}
            </div>
        `;
    }

    function renderSvgChart(series, type, options = {}) {
        const values = series.map((point) => Number(point.value || 0));
        const labels = series.map((point) => String(point.label || ''));
        const min = Math.min(...values, 0);
        const max = Math.max(...values, 1);
        const width = 320;
        const height = 120;
        const step = values.length > 1 ? width / (values.length - 1) : width;
        const tone = options.tone || toneFromPnl(values[values.length - 1] || 0);
        const stroke = tone === 'danger' ? '#ff6d6d' : tone === 'warn' ? '#f4be62' : tone === 'info' ? '#4dc7df' : '#37d48c';
        const soft = tone === 'danger' ? 'rgba(255, 109, 109, 0.14)' : tone === 'warn' ? 'rgba(244, 190, 98, 0.14)' : tone === 'info' ? 'rgba(77, 199, 223, 0.16)' : 'rgba(55, 212, 140, 0.16)';
        const gradientId = `ws-chart-gradient-${Math.abs(series.map((point) => String(point.label || '') + String(point.value || '')).join('').split('').reduce((hash, char) => ((hash << 5) - hash + char.charCodeAt(0)) | 0, 0))}`;
        const points = values.map((value, index) => {
            const normalized = max === min ? 0.5 : (value - min) / (max - min);
            const x = index * step;
            const y = height - normalized * (height - 12) - 6;
            return { x, y, value, label: labels[index] };
        });

        if (type === 'bar') {
            const barWidth = Math.max(8, Math.floor(width / Math.max(values.length, 1)) - 6);
            return `
                <svg class="ws-chart-svg" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" role="img" aria-label="chart">
                    <line x1="0" y1="${(height - 18).toFixed(2)}" x2="${width}" y2="${(height - 18).toFixed(2)}" stroke="rgba(143, 168, 184, 0.18)" stroke-width="1"></line>
                    ${points.map((point) => {
                        const barHeight = height - point.y - 10;
                        const x = Math.max(0, point.x - barWidth / 2);
                        const fill = point.value < 0 ? '#ff6d6d' : stroke;
                        return `<rect x="${x.toFixed(2)}" y="${point.y.toFixed(2)}" width="${barWidth}" height="${Math.max(barHeight, 4).toFixed(2)}" rx="6" fill="${fill}" opacity="0.78"></rect>`;
                    }).join('')}
                </svg>
            `;
        }

        const path = points.map((point, index) => `${index === 0 ? 'M' : 'L'} ${point.x.toFixed(2)} ${point.y.toFixed(2)}`).join(' ');
        const areaPath = `${path} L ${width.toFixed(2)} ${height.toFixed(2)} L 0 ${height.toFixed(2)} Z`;
        const lastPoint = points[points.length - 1];
        return `
            <svg class="ws-chart-svg" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" role="img" aria-label="chart">
                <defs>
                    <linearGradient id="${gradientId}" x1="0" x2="0" y1="0" y2="1">
                        <stop offset="0%" stop-color="${soft}"></stop>
                        <stop offset="100%" stop-color="rgba(7, 17, 26, 0)"></stop>
                    </linearGradient>
                </defs>
                <line x1="0" y1="22" x2="${width}" y2="22" stroke="rgba(143, 168, 184, 0.12)" stroke-width="1"></line>
                <line x1="0" y1="${(height - 18).toFixed(2)}" x2="${width}" y2="${(height - 18).toFixed(2)}" stroke="rgba(143, 168, 184, 0.18)" stroke-width="1"></line>
                <path d="${areaPath}" fill="url(#${gradientId})"></path>
                <path d="${path}" fill="none" stroke="${stroke}" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round"></path>
                <circle cx="${lastPoint.x.toFixed(2)}" cy="${lastPoint.y.toFixed(2)}" r="4" fill="${stroke}" stroke="#07111a" stroke-width="2"></circle>
            </svg>
        `;
    }

    function applyBinanceFilters(rows, filters, decisionMode) {
        const safeRows = Array.isArray(rows) ? rows : [];
        return safeRows.filter((row) => {
            const symbol = String(row.symbol_or_market_id || row.market_id || row.symbol || '');
            const venue = String(row.venue || '');
            const side = normalizeSide(row);
            const status = normalizeStatus(row, decisionMode);

            if (filters.symbol && filters.symbol !== 'all' && symbol !== filters.symbol) {
                return false;
            }
            if (filters.venue && filters.venue !== 'all' && venue !== filters.venue) {
                return false;
            }
            if (filters.side && filters.side !== 'all' && side !== filters.side) {
                return false;
            }
            if (filters.status && filters.status !== 'all' && status !== filters.status) {
                return false;
            }
            return true;
        });
    }

    function normalizeSide(row) {
        const raw = String(row.side || row.action || '').toUpperCase();
        if (raw.includes('SHORT') || raw === 'SELL' || raw === 'NO') {
            return 'short';
        }
        return 'long';
    }

    function normalizeStatus(row, decisionMode) {
        if (decisionMode) {
            return String(row.action || row.final_verdict || '').toLowerCase() === 'reject' ? 'rejected' : 'open';
        }
        const raw = String(row.status || '').toUpperCase();
        if (raw.startsWith('CLOSED')) {
            return 'closed';
        }
        if (raw === 'OPEN') {
            return 'open';
        }
        return raw ? raw.toLowerCase() : 'open';
    }

    function filterLabel(value) {
        const normalized = String(value || 'all');
        const map = {
            all: 'Tumu',
            binance_futures: 'Futures',
            binance_spot: 'Spot',
            long: 'Long',
            short: 'Short',
            open: 'Open',
            closed: 'Closed',
            rejected: 'Rejected',
        };
        return map[normalized] || normalized;
    }

    function shortAddress(address) {
        const raw = String(address || '');
        if (raw.length <= 12) {
            return raw || '--';
        }
        return `${raw.slice(0, 6)}...${raw.slice(-4)}`;
    }

    function formatCurrency(value) {
        const numeric = Number(value || 0);
        return new Intl.NumberFormat('tr-TR', {
            style: 'currency',
            currency: 'USD',
            maximumFractionDigits: 0,
        }).format(numeric);
    }

    function formatNumber(value, fallback) {
        if (value == null || value === '' || Number.isNaN(Number(value))) {
            return fallback || '0';
        }
        return new Intl.NumberFormat('tr-TR', {
            maximumFractionDigits: 2,
        }).format(Number(value));
    }

    function formatInteger(value) {
        return new Intl.NumberFormat('tr-TR', {
            maximumFractionDigits: 0,
        }).format(Number(value || 0));
    }

    function formatScore(value) {
        if (value == null || value === '' || Number.isNaN(Number(value))) {
            return '0';
        }
        return Number(value).toFixed(2);
    }

    function formatPercent(value, options) {
        const fallback = options && options.fallback ? options.fallback : '0%';
        if (value == null || value === '' || Number.isNaN(Number(value))) {
            return fallback;
        }
        return `${Number(value).toFixed(1)}%`;
    }

    function formatDateTime(value) {
        const date = safeDate(value);
        if (!date) {
            return 'Bekleniyor';
        }
        return new Intl.DateTimeFormat('tr-TR', {
            dateStyle: 'medium',
            timeStyle: 'short',
        }).format(date);
    }

    function formatShortDate(value) {
        const date = safeDate(value);
        if (!date) {
            return 'Bekleniyor';
        }
        return new Intl.DateTimeFormat('tr-TR', {
            month: '2-digit',
            day: '2-digit',
            hour: '2-digit',
            minute: '2-digit',
        }).format(date);
    }

    function safeDate(value) {
        if (!value) {
            return null;
        }
        const normalized = String(value).replace(' ', 'T');
        const date = new Date(normalized);
        return Number.isNaN(date.getTime()) ? null : date;
    }

    function statusAccent(label, tone) {
        return `<span class="${toneClass(tone)}">${escapeHtml(String(label || ''))}</span>`;
    }

    function spanWithTone(label, tone) {
        return `<span class="ws-badge ${toneClass(tone)}">${escapeHtml(String(label || ''))}</span>`;
    }

    function statusAccentNumber(value) {
        return `<span class="${pnlClass(value)}">${escapeHtml(formatCurrency(value || 0))}</span>`;
    }

    function toneClass(tone) {
        const normalized = String(tone || 'neutral').toLowerCase();
        const map = {
            ok: 'is-ok',
            success: 'is-ok',
            danger: 'is-danger',
            error: 'is-danger',
            warn: 'is-warn',
            warning: 'is-warn',
            info: 'is-info',
            neutral: 'is-neutral',
        };
        return map[normalized] || 'is-neutral';
    }

    function pnlClass(value) {
        const numeric = Number(value || 0);
        if (numeric > 0) {
            return 'ws-pnl-positive';
        }
        if (numeric < 0) {
            return 'ws-pnl-negative';
        }
        return 'ws-pnl-neutral';
    }

    function verdictTone(verdict) {
        const normalized = String(verdict || '').toLowerCase();
        if (normalized.includes('copy') || normalized.includes('open') || normalized.includes('execute') || normalized.includes('win')) {
            return 'ok';
        }
        if (normalized.includes('reject') || normalized.includes('loss')) {
            return 'danger';
        }
        if (normalized.includes('close')) {
            return 'warn';
        }
        return 'info';
    }

    function toneFromPnl(value) {
        const numeric = Number(value || 0);
        return numeric > 0 ? 'ok' : numeric < 0 ? 'danger' : 'info';
    }

    function gateTone(status) {
        const normalized = String(status || '').toLowerCase();
        if (normalized === 'promoted' || normalized === 'shadow_proven') {
            return 'ok';
        }
        if (normalized === 'blocked') {
            return 'warn';
        }
        return 'info';
    }

    function escapeHtml(value) {
        return String(value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

    function escapeAttribute(value) {
        return escapeHtml(value);
    }
})();
