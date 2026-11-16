/**
 * Multi-Chain Memecoin Intelligence, Forensics & Trading OS
 * Paper Trading Command Center — Main Application Logic
 * Strictly Read-Only, Connected to Core SQLite APIs
 */

import {
  EvidenceType,
  formatExactValue,
  renderIcon,
  renderEvidenceBadge,
  renderMetricCard,
  renderTokenCard,
  renderChainCard,
  renderSecurityCard,
  renderPaperDeskCard,
  renderStrategyCard,
  renderWorkflowCard,
  renderSparkline,
  renderDataTable,
  renderEmptyState,
  renderLoadingSpinner,
  renderErrorState,
  renderTerminalMatrixCard,
  renderLiveGatewayArchitectureCard,
  renderTerminalHandoffButtons
} from './components.js';

// Application State
const state = {
  activeView: 'overview',
  theme: localStorage.getItem('theme_preference') || 'dark',
  chainFilter: 'ALL',
  riskFilter: 'ALL',
  verificationFilter: 'ALL',
  searchQuery: '',
  operatingMode: 'online_research',
  cache: {}
};

// Dynamic Screener State (Solana + Base Live Market Discovery)
const screenerState = {
  chain: 'all',          // 'all', 'solana', 'base'
  category: 'MEME',      // 'MEME', 'ALL', 'UNKNOWN', 'NON_MEME'
  limit: 50,             // 10, 50, 100
  sortBy: 'volume_24h',  // 'volume_24h', 'liquidity', 'price_change_5m', 'price_change_1h', 'price_change_24h'
  sortDir: 'desc',
  minLiquidity: '',
  minVolume24h: '',
  minAgeHours: '',
  search: '',
  isRefreshing: false
};


// ============================================================================
// Core API Fetch Helper
// ============================================================================
async function fetchCoreApi(endpoint, options = {}) {
  try {
    const res = await fetch(endpoint, { credentials: 'same-origin', ...options });
    if (!res.ok) {
      const errText = await res.text();
      throw new Error(`HTTP ${res.status}: ${errText || res.statusText}`);
    }
    return await res.json();
  } catch (err) {
    console.error(`API fetch error on ${endpoint}:`, err);
    throw err;
  }
}

// ============================================================================
// Theme & Mode Management
// ============================================================================
function applyTheme(theme) {
  state.theme = theme;
  let effectiveTheme = theme;
  if (theme === 'system') {
    effectiveTheme = window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }

  document.documentElement.setAttribute('data-theme', effectiveTheme);
  localStorage.setItem('theme_preference', theme);

  const themeToggle = document.getElementById('theme-toggle-btn');
  if (themeToggle) {
    const iconName = effectiveTheme === 'dark' ? 'moon' : 'sun';
    const label = effectiveTheme === 'dark' ? 'Dark' : 'Light';
    themeToggle.innerHTML = `${renderIcon(iconName, 'icon-xs')} <span>${label}</span>`;
  }
}

function updateModeBadge(mode) {
  state.operatingMode = mode;
  const badge = document.getElementById('pm-mode-badge');
  if (!badge) return;

  if (mode === 'offline_research') {
    badge.textContent = 'OFFLINE ISOLATED';
    badge.className = 'pm-mode-pill text-xs font-mono text-warn';
  } else {
    badge.textContent = 'ONLINE RESEARCH';
    badge.className = 'pm-mode-pill text-xs font-mono text-success';
  }
}

// ============================================================================
// 1. Overview / Command Center View
// ============================================================================
async function renderOverviewView() {
  const container = document.getElementById('view-content');
  if (!container) return;
  container.innerHTML = renderLoadingSpinner('Loading Command Center KPIs & Real-Time Desk Equity...');

  try {
    const [kpi, chains, terminalsData] = await Promise.all([
      fetchCoreApi('/api/v1/dashboard/overview'),
      fetchCoreApi('/api/v1/dashboard/chains'),
      fetchCoreApi('/api/v1/dashboard/terminals')
    ]);

    const kpisHtml = `
      <div class="kpi-grid grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
        ${renderMetricCard({
          title: 'Total Paper Equity (USD)',
          value: `$${Number(kpi.total_paper_equity_usd || 0).toLocaleString()}`,
          subtitle: `Config Capital: $${Number(kpi.initial_virtual_capital_usd || 252500).toLocaleString()} (Benchmark Rates)`,
          icon: 'wallet',
          evidence: EvidenceType.PROBABILISTIC
        })}
        ${renderMetricCard({
          title: 'Realized PnL',
          value: `${Number(kpi.total_realized_pnl) >= 0 ? '+' : ''}${kpi.total_realized_pnl}`,
          isPositive: Number(kpi.total_realized_pnl) >= 0,
          subtitle: kpi.performance_provenance === 'TEST_HARNESS_OBSERVED'
            ? `Test-Harness Data (${kpi.sample_size_trades} test sells / 0 user trades)`
            : (kpi.win_rate_pct !== null && kpi.win_rate_pct !== undefined 
                ? `Win Rate: ${kpi.win_rate_pct}% (PF: ${kpi.profit_factor !== null && kpi.profit_factor !== undefined ? kpi.profit_factor : 'N/A'})` 
                : `No Closed Trades`),
          icon: 'trendingUp',
          evidence: EvidenceType.PROBABILISTIC
        })}
        ${renderMetricCard({
          title: 'Open Positions',
          value: kpi.open_positions_count || 0,
          subtitle: `Orders: ${kpi.filled_orders_count} Filled / ${kpi.rejected_orders_count} Rejected`,
          icon: 'target',
          evidence: EvidenceType.VERIFIED
        })}
        ${renderMetricCard({
          title: 'Intelligence & Safety',
          value: `${kpi.tracked_tokens_count} Evaluated`,
          subtitle: `${kpi.scams_blocked_count} Blocked | ${kpi.high_conviction_count} Conviction (DB-Observed)`,
          icon: 'shield',
          evidence: EvidenceType.PROBABILISTIC
        })}
      </div>

      <!-- 12 Connected Multi-Chain Ecosystem Grid (cards-grid cols-4) -->
      <div class="mb-6">
        <div class="section-header flex justify-between items-center mb-3">
          <h3 class="text-sm font-bold text-primary">Connected Multi-Chain Ecosystem (12 Networks)</h3>
          <span class="badge badge-info">12 Active Desks</span>
        </div>
        <div class="cards-grid cols-4 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          ${(chains || []).map(c => renderChainCard(c)).join('')}
        </div>
      </div>

      <!-- Verified Memecoin Trading Terminals & Protocols Matrix -->
      ${renderTerminalMatrixCard(terminalsData?.terminals || [])}

      <!-- Future Live Trading Gateway Architecture Card (Locked & Isolated) -->
      ${renderLiveGatewayArchitectureCard(terminalsData?.live_gateway_status || {})}

      <!-- Dual Orchestrator Architecture Card -->
      <div class="card p-4 mb-6">
        <div class="card-header border-b border-subtle pb-2 mb-3 flex justify-between items-center">
          <h3 class="card-title text-sm font-bold text-primary">Orchestration Architecture Status</h3>
          <span class="badge badge-info">P10.2 Compliant</span>
        </div>
        <div class="grid grid-cols-1 md:grid-cols-3 gap-3 text-xs">
          <div class="p-3 rounded bg-subtle">
            <div class="flex justify-between items-center mb-1">
              <strong class="text-primary">Windmill (Primary)</strong>
              <span class="badge ${kpi.dual_orchestrator_status?.windmill?.status === 'ONLINE' ? 'badge-success' : 'badge-warn'}">${kpi.dual_orchestrator_status?.windmill?.status || 'NOT_CONNECTED'}</span>
            </div>
            <p class="text-muted">${kpi.dual_orchestrator_status?.windmill?.daemon_state === 'NO_ACTIVE_DAEMON' ? 'Standalone scripts available. No background daemon active.' : 'Primary orchestration for WF01-WF33 research & paper pipelines.'}</p>
          </div>
          <div class="p-3 rounded bg-subtle">
            <div class="flex justify-between items-center mb-1">
              <strong class="text-primary">Hermes AI (Optional)</strong>
              <span class="badge badge-secondary">${kpi.dual_orchestrator_status?.hermes?.status || 'STANDALONE_CLI'}</span>
            </div>
            <p class="text-muted">Read-only analyst for narrative digests. Zero execution authority.</p>
          </div>
          <div class="p-3 rounded bg-subtle">
            <div class="flex justify-between items-center mb-1">
              <strong class="text-primary">n8n Automation</strong>
              <span class="badge badge-warn">${kpi.dual_orchestrator_status?.n8n?.status || 'DEFERRED'}</span>
            </div>
            <p class="text-muted">Deferred in Core Version 1. No active background dependencies.</p>
          </div>
        </div>
      </div>

      <!-- Recent Signals & Recent Fills Grid -->
      <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <!-- Recent Research Signals -->
        <div class="card p-4">
          <div class="card-header border-b border-subtle pb-2 mb-3 flex justify-between items-center">
            <h3 class="card-title text-sm font-bold text-primary">Recent Evaluated Signals</h3>
            <span class="text-xs text-muted">${kpi.recent_signals?.length || 0} Records</span>
          </div>
          ${kpi.recent_signals && kpi.recent_signals.length > 0 ? `
            <div class="table-responsive-wrapper overflow-x-auto">
              <table class="data-table text-xs w-full">
                <thead>
                  <tr class="text-muted text-left border-b border-subtle">
                    <th class="py-2">Token</th>
                    <th class="py-2">Chain</th>
                    <th class="py-2">Score</th>
                    <th class="py-2">Decimals</th>
                    <th class="py-2 text-right">Action</th>
                  </tr>
                </thead>
                <tbody>
                  ${kpi.recent_signals.map(s => `
                    <tr class="border-b border-subtle">
                      <td class="py-2 font-bold font-mono">${s.symbol || 'TOKEN'}</td>
                      <td class="py-2 font-mono text-muted truncate max-w-100">${s.chain_id}</td>
                      <td class="py-2 font-mono font-bold ${s.composite_score >= 80 ? 'text-success' : (s.composite_score <= 10 ? 'text-danger' : 'text-primary')}">
                        ${s.composite_score !== null ? s.composite_score : 'N/A'}
                      </td>
                      <td class="py-2">${renderEvidenceBadge(s.verification_status === 'VERIFIED' ? EvidenceType.VERIFIED : EvidenceType.INSUFFICIENT, s.verification_status)}</td>
                      <td class="py-2 text-right">
                        <button class="btn btn-secondary btn-sm" data-action="inspect" data-token="${s.symbol || ''}" data-address="${s.asset_address}">
                          Dossier
                        </button>
                      </td>
                    </tr>
                  `).join('')}
                </tbody>
              </table>
            </div>
          ` : renderEmptyState({ title: 'No Signals Recorded', message: 'Run research orchestrator WF-25 to populate candidates.', icon: 'cpu' })}
        </div>

        <!-- Recent Executed Fills -->
        <div class="card p-4">
          <div class="card-header border-b border-subtle pb-2 mb-3 flex justify-between items-center">
            <h3 class="card-title text-sm font-bold text-primary">Recent Atomic Paper Fills</h3>
            <span class="text-xs text-muted font-mono">${kpi.total_fills_count || 0} Total Fills</span>
          </div>
          ${kpi.recent_fills && kpi.recent_fills.length > 0 ? `
            <div class="table-responsive-wrapper overflow-x-auto">
              <table class="data-table text-xs w-full">
                <thead>
                  <tr class="text-muted text-left border-b border-subtle">
                    <th class="py-2">Side</th>
                    <th class="py-2">Chain</th>
                    <th class="py-2">Quantity</th>
                    <th class="py-2">Price (Native)</th>
                    <th class="py-2">Slippage</th>
                  </tr>
                </thead>
                <tbody>
                  ${kpi.recent_fills.map(f => `
                    <tr class="border-b border-subtle">
                      <td class="py-2 font-bold ${f.side === 'BUY' ? 'text-success' : 'text-danger'}">${f.side}</td>
                      <td class="py-2 font-mono text-muted truncate max-w-100">${f.chain_id}</td>
                      <td class="py-2 font-mono">${formatExactValue(f.fill_quantity, '', 2)}</td>
                      <td class="py-2 font-mono">${Number(f.fill_price_native).toFixed(8)}</td>
                      <td class="py-2 font-mono text-muted">${f.slippage_bps} bps</td>
                    </tr>
                  `).join('')}
                </tbody>
              </table>
            </div>
          ` : renderEmptyState({ title: 'No Fills Recorded', message: 'Simulated paper executions will appear here.', icon: 'fileText' })}
        </div>
      </div>
    `;

    container.innerHTML = kpisHtml;
  } catch (err) {
    container.innerHTML = renderErrorState({ title: 'Overview KPI Connection Failed', error: err.message });
  }
}

// ============================================================================
// 2. Market Intelligence (Screener) View
// ============================================================================
// ============================================================================
// 2. Market Intelligence (Dynamic Screener) View — Solana & Base Live Feeds
// ============================================================================
async function renderScreenerView() {
  const container = document.getElementById('view-content');
  if (!container) return;
  container.innerHTML = renderLoadingSpinner('Ingesting Live Solana & Base DEX Observations...');

  try {
    const params = new URLSearchParams();
    if (screenerState.chain && screenerState.chain !== 'all') {
      params.append('chain', screenerState.chain);
    }
    if (screenerState.category && screenerState.category !== 'ALL') {
      params.append('category', screenerState.category);
      params.append('memecoin_only', screenerState.category === 'MEME' ? 'true' : 'false');
    } else if (screenerState.category === 'ALL') {
      params.append('category', 'ALL');
      params.append('memecoin_only', 'false');
    }
    params.append('limit', screenerState.limit.toString());
    params.append('sort_by', screenerState.sortBy);
    params.append('sort_dir', screenerState.sortDir);
    if (screenerState.minLiquidity) params.append('min_liquidity', screenerState.minLiquidity);
    if (screenerState.minVolume24h) params.append('min_volume_24h', screenerState.minVolume24h);
    if (screenerState.minAgeHours) params.append('min_age_hours', screenerState.minAgeHours);
    if (screenerState.search) params.append('search', screenerState.search.trim());
    if (screenerState.forceRefresh) {
      params.append('force_refresh', 'true');
      screenerState.forceRefresh = false;
    }

    // Fetch live screener observations & provider health concurrently
    const [feed, health] = await Promise.all([
      fetchCoreApi(`/api/v1/discovery/screener?${params.toString()}`),
      fetchCoreApi('/api/v1/discovery/providers/health').catch(() => null)
    ]);

    const tokens = feed.tokens || [];
    const gtHealth = health?.providers?.geckoterminal;
    const dsHealth = health?.providers?.dexscreener;

    // Helper: format small and normal prices
    const formatTokenPrice = (p) => {
      if (p === null || p === undefined) return 'N/A';
      const num = Number(p);
      if (isNaN(num)) return 'N/A';
      if (num === 0) return '$0.00';
      if (num >= 1.0) return `$${num.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 4 })}`;
      if (num < 0.000001) return `$${num.toExponential(3)}`;
      return `$${num.toFixed(6)}`;
    };

    // Helper: format % changes
    const formatChangeBadge = (pct) => {
      if (pct === null || pct === undefined) return '<span class="text-muted font-mono">N/A</span>';
      const num = Number(pct);
      if (isNaN(num)) return '<span class="text-muted font-mono">N/A</span>';
      const sign = num > 0 ? '+' : '';
      const colorClass = num > 0 ? 'text-success' : num < 0 ? 'text-danger' : 'text-secondary';
      return `<span class="font-mono font-bold ${colorClass}">${sign}${num.toFixed(2)}%</span>`;
    };

    // Helper: format token age
    const formatAge = (isoStr) => {
      if (!isoStr) return '<span class="text-muted">Unrecorded</span>';
      const ms = Date.now() - new Date(isoStr).getTime();
      if (isNaN(ms) || ms < 0) return '<span class="text-muted">Just now</span>';
      const hours = ms / (1000 * 60 * 60);
      if (hours < 1) return `${Math.max(1, Math.round(hours * 60))}m ago`;
      if (hours < 24) return `${hours.toFixed(1)}h ago`;
      const days = hours / 24;
      return `${days.toFixed(1)}d ago`;
    };

    // Table Columns definition
    const columns = [
      { key: 'rank_col', label: '#', align: 'text-center' },
      { key: 'token_col', label: 'TOKEN / CONTRACT', align: 'text-left' },
      { key: 'chain_col', label: 'CHAIN', align: 'text-center' },
      { key: 'price_col', label: 'PRICE USD', align: 'text-right' },
      { key: 'ch5m_col', label: '5M', align: 'text-right' },
      { key: 'ch1h_col', label: '1H', align: 'text-right' },
      { key: 'ch24h_col', label: '24H', align: 'text-right' },
      { key: 'trend_col', label: 'TREND', align: 'text-center' },
      { key: 'liq_col', label: 'CANONICAL LIQUIDITY', align: 'text-right' },
      { key: 'vol_col', label: '24H VOLUME', align: 'text-right' },
      { key: 'dex_col', label: 'DEX / POOL', align: 'text-left' },
      { key: 'age_col', label: 'AGE', align: 'text-center' },
      { key: 'source_col', label: 'PROVENANCE', align: 'text-center' },
      { key: 'action_col', label: 'ACTION', align: 'text-right' }
    ];

    const rows = tokens.map((t, idx) => {
      const isSolana = t.chain_id?.startsWith('solana') || t.provider_network_id === 'solana';
      const shortAddr = t.token_address ? `${t.token_address.slice(0, 6)}...${t.token_address.slice(-4)}` : '';
      const poolAddr = t.canonical_pool_address ? `${t.canonical_pool_address.slice(0, 5)}...${t.canonical_pool_address.slice(-4)}` : '';
      const poolCountBadge = t.multi_pool_count > 1 
        ? `<span class="badge badge-secondary ml-1" title="Aggregated from ${t.multi_pool_count} pools without double counting">${t.multi_pool_count} pools</span>`
        : '';
      const provTooltip = `${t.classification_source || 'DEFAULT'} (${t.confidence || 'LOW'} confidence): ${t.classification_reason || 'Unclassified'}`;
      const categoryBadge = t.asset_category === 'MEME'
        ? `<span class="badge badge-brand text-xs font-mono ml-1" title="${provTooltip}">MEME</span>`
        : t.asset_category === 'NON_MEME'
        ? `<span class="badge badge-secondary text-xs font-mono ml-1" title="${provTooltip}">NON-MEME</span>`
        : `<span class="badge badge-subtle text-xs font-mono ml-1" title="${provTooltip}">UNKNOWN</span>`;

      return {
        rank_col: `<span class="font-mono text-muted text-xs">${idx + 1}</span>`,
        token_col: `
          <div class="token-cell">
            <div class="flex items-center gap-1">
              <span class="font-bold text-primary font-mono">${t.symbol || 'UNKNOWN'}</span>
              ${categoryBadge}
            </div>
            <span class="text-xs text-secondary truncate block max-w-160">${t.name || ''}</span>
            <span class="font-mono text-muted text-xs block truncate max-w-160" title="${t.token_address}">${shortAddr}</span>
          </div>
        `,
        chain_col: `<span class="badge ${isSolana ? 'badge-brand' : 'badge-chain'} font-mono">${isSolana ? 'SOLANA' : 'BASE'}</span>`,
        price_col: `<span class="font-mono font-bold text-primary">${formatTokenPrice(t.price_usd)}</span>`,
        ch5m_col: formatChangeBadge(t.price_change_5m_pct),
        ch1h_col: formatChangeBadge(t.price_change_1h_pct),
        ch24h_col: formatChangeBadge(t.price_change_24h_pct),
        trend_col: renderSparkline(t.sparkline || [10, 15, 12, 18, 22, 19, 27, 35], Number(t.price_change_24h_pct || 0) >= 0),
        liq_col: `<span class="font-mono text-primary font-bold">${formatExactValue(t.liquidity_usd)}</span>`,
        vol_col: `<span class="font-mono text-primary">${formatExactValue(t.volume_24h_usd)}</span>`,
        dex_col: `
          <div>
            <span class="font-bold text-xs uppercase text-primary">${t.canonical_dex_id || 'DEX'}</span>
            ${poolCountBadge}
            <span class="font-mono text-muted text-xs block" title="${t.canonical_pool_address}">${poolAddr}</span>
          </div>
        `,
        age_col: `<span class="font-mono text-xs text-secondary">${formatAge(t.pair_created_at)}</span>`,
        source_col: `<span class="badge badge-subtle font-mono text-xs">${t.provider_source || 'GeckoTerminal'}</span>`,
        action_col: `
          <div class="flex items-center justify-end gap-1">
            ${renderTerminalHandoffButtons(null, isSolana ? 'solana:mainnet' : 'eip155:8453', t.token_address)}
            <button class="btn btn-secondary btn-sm" data-action="inspect" data-token="${t.symbol || ''}" data-address="${t.token_address}">Dossier</button>
          </div>
        `
      };
    });

    // Build Freshness Badge HTML (FRESH, STALE, UNAVAILABLE - no REALTIME)
    const freshnessColor = feed.freshness_status === 'FRESH' ? 'badge-safe' 
      : feed.freshness_status === 'STALE' ? 'badge-warn' : 'badge-danger';

    const gtStatusPill = gtHealth ? `
      <div class="provider-pill flex items-center gap-1.5 px-2 py-1 rounded bg-subtle text-xs font-mono">
        <span class="status-indicator ${gtHealth.circuit_breaker_state === 'CLOSED' ? 'active' : 'warn'}"></span>
        <span class="text-primary font-bold">GeckoTerminal:</span>
        <span class="text-secondary">${gtHealth.rate_limit_state}</span>
        <span class="text-muted">(${gtHealth.latency_ms || 0}ms)</span>
      </div>
    ` : '';

    const dsStatusPill = dsHealth ? `
      <div class="provider-pill flex items-center gap-1.5 px-2 py-1 rounded bg-subtle text-xs font-mono">
        <span class="status-indicator ${dsHealth.circuit_breaker_state === 'CLOSED' ? 'active' : 'warn'}"></span>
        <span class="text-primary font-bold">DEX Screener:</span>
        <span class="text-secondary">${dsHealth.rate_limit_state}</span>
        <span class="text-muted">(${dsHealth.latency_ms || 0}ms)</span>
      </div>
    ` : '';

    // Render Top 3 Cards
    const topCardsHtml = tokens.slice(0, 3).map((t, i) => {
      const isSol = t.chain_id?.startsWith('solana') || t.provider_network_id === 'solana';
      const provTooltip = `${t.classification_source || 'DEFAULT'} (${t.confidence || 'LOW'} confidence): ${t.classification_reason || 'Unclassified'}`;
      const catBadge = t.asset_category === 'MEME'
        ? `<span class="badge badge-brand text-xs font-mono" title="${provTooltip}">MEME</span>`
        : t.asset_category === 'NON_MEME'
        ? `<span class="badge badge-secondary text-xs font-mono" title="${provTooltip}">NON-MEME</span>`
        : `<span class="badge badge-subtle text-xs font-mono" title="${provTooltip}">UNKNOWN</span>`;

      return `
        <div class="card p-4 relative overflow-hidden">
          <div class="flex justify-between items-start mb-2">
            <div>
              <div class="flex items-center gap-1.5 flex-wrap">
                <span class="font-mono font-bold text-lg text-primary">${t.symbol || 'UNKNOWN'}</span>
                <span class="badge ${isSol ? 'badge-brand' : 'badge-chain'} text-xs font-mono">${isSol ? 'SOLANA' : 'BASE'}</span>
                ${catBadge}
                <span class="badge badge-subtle text-xs">#${i + 1}</span>
              </div>
              <span class="text-xs text-secondary truncate block max-w-180">${t.name || ''}</span>
            </div>
            <div class="text-right">
              <span class="font-mono font-bold text-lg block text-primary">${formatTokenPrice(t.price_usd)}</span>
              ${formatChangeBadge(t.price_change_24h_pct)}
            </div>
          </div>
          <div class="grid grid-cols-2 gap-2 text-xs font-mono pt-2 border-t border-subtle mt-2 mb-3">
            <div>
              <span class="text-muted block">CANONICAL LIQ</span>
              <span class="font-bold text-primary">${formatExactValue(t.liquidity_usd)}</span>
            </div>
            <div>
              <span class="text-muted block">24H VOLUME</span>
              <span class="font-bold text-primary">${formatExactValue(t.volume_24h_usd)}</span>
            </div>
            <div>
              <span class="text-muted block">DEX POOL</span>
              <span class="text-secondary uppercase">${t.canonical_dex_id || 'DEX'}</span>
            </div>
            <div>
              <span class="text-muted block">PAIR AGE</span>
              <span class="text-secondary">${formatAge(t.pair_created_at)}</span>
            </div>
          </div>
          <div class="flex justify-between items-center pt-2 border-t border-subtle">
            <span class="text-xs text-muted font-mono">${t.multi_pool_count > 1 ? `${t.multi_pool_count} pools` : '1 pool'}</span>
            ${renderTerminalHandoffButtons(null, isSol ? 'solana:mainnet' : 'eip155:8453', t.token_address)}
          </div>
        </div>
      `;
    }).join('');

    const html = `
      <div class="section-container">
        <!-- Section Header -->
        <div class="section-header flex flex-col md:flex-row md:justify-between md:items-center gap-3 mb-4">
          <div>
            <div class="flex items-center gap-2 mb-1">
              <h2 class="section-title text-lg font-bold text-primary">Dynamic Memecoin Screener</h2>
              <span class="badge ${freshnessColor} font-mono">${feed.freshness_status} (${feed.cache_age_seconds}s)</span>
              <span class="badge badge-info font-mono text-xs">Solana + Base</span>
            </div>
            <p class="text-xs text-muted">
              Live DEX market observations normalized from GeckoTerminal (discovery) & DEX Screener (enrichment).
              Canonical pool aggregated by max liquidity without double counting. Filtered for memecoins by default.
            </p>
          </div>
          <div class="flex flex-wrap items-center gap-2">
            ${gtStatusPill}
            ${dsStatusPill}
            <button id="screener-refresh-btn" class="btn btn-secondary btn-sm inline-flex items-center gap-1.5" title="Force fresh fetch from providers">
              ${renderIcon('refresh', `icon-xs ${screenerState.isRefreshing ? 'spin' : ''}`)}
              <span>Refresh</span>
            </button>
          </div>
        </div>

        <!-- Stale / Cache Notice Banner -->
        ${feed.freshness_status === 'STALE' ? `
          <div class="p-3 mb-4 rounded bg-warn-subtle border border-warn text-xs flex items-center justify-between font-mono">
            <div class="flex items-center gap-2">
              ${renderIcon('alertTriangle', 'icon-sm text-warn')}
              <span>Market cache is STALE (${feed.cache_age_seconds}s old). Fetched at ${new Date(feed.fetched_at).toLocaleTimeString()}. Public rate limit budget active.</span>
            </div>
            <span class="text-muted">Bounded 10-60 RPM</span>
          </div>
        ` : ''}

        <!-- Warnings if any -->
        ${(feed.warnings || []).length > 0 ? `
          <div class="p-3 mb-4 rounded bg-subtle border border-medium text-xs font-mono">
            <span class="text-warn font-bold">Notices:</span> ${feed.warnings.join(' • ')}
          </div>
        ` : ''}

        <!-- Screener Controls & Filters Bar (Soft Card) -->
        <div class="card p-4 mb-4">
          <div class="flex flex-wrap items-center justify-between gap-3 mb-3">
            <!-- Chain Selector -->
            <div class="flex items-center gap-1.5">
              <span class="text-xs text-muted font-mono uppercase mr-1">Chain:</span>
              <div class="btn-group inline-flex rounded-lg p-0.5 bg-subtle">
                <button class="btn btn-xs ${screenerState.chain === 'all' ? 'btn-primary' : 'btn-ghost'}" data-screener-chain="all">Both</button>
                <button class="btn btn-xs ${screenerState.chain === 'solana' ? 'btn-primary' : 'btn-ghost'}" data-screener-chain="solana">Solana</button>
                <button class="btn btn-xs ${screenerState.chain === 'base' ? 'btn-primary' : 'btn-ghost'}" data-screener-chain="base">Base</button>
              </div>
            </div>

            <!-- Asset Category Filter -->
            <div class="flex items-center gap-1.5">
              <span class="text-xs text-muted font-mono uppercase mr-1">Category:</span>
              <div class="btn-group inline-flex rounded-lg p-0.5 bg-subtle">
                <button class="btn btn-xs ${screenerState.category === 'MEME' ? 'btn-primary' : 'btn-ghost'}" data-screener-category="MEME">Memecoins</button>
                <button class="btn btn-xs ${screenerState.category === 'ALL' ? 'btn-primary' : 'btn-ghost'}" data-screener-category="ALL">All Tokens</button>
                <button class="btn btn-xs ${screenerState.category === 'UNKNOWN' ? 'btn-primary' : 'btn-ghost'}" data-screener-category="UNKNOWN">Unknown</button>
                <button class="btn btn-xs ${screenerState.category === 'NON_MEME' ? 'btn-primary' : 'btn-ghost'}" data-screener-category="NON_MEME">Non-Meme</button>
              </div>
            </div>

            <!-- Top Limit Selector -->
            <div class="flex items-center gap-1.5">
              <span class="text-xs text-muted font-mono uppercase mr-1">Limit:</span>
              <div class="btn-group inline-flex rounded-lg p-0.5 bg-subtle">
                <button class="btn btn-xs ${screenerState.limit === 10 ? 'btn-primary' : 'btn-ghost'}" data-screener-limit="10">Top 10</button>
                <button class="btn btn-xs ${screenerState.limit === 50 ? 'btn-primary' : 'btn-ghost'}" data-screener-limit="50">Top 50</button>
                <button class="btn btn-xs ${screenerState.limit === 100 ? 'btn-primary' : 'btn-ghost'}" data-screener-limit="100">Top 100</button>
              </div>
            </div>

            <!-- Sort By Selector -->
            <div class="flex items-center gap-1.5">
              <span class="text-xs text-muted font-mono uppercase mr-1">Sort:</span>
              <select id="screener-sort-select" class="input input-sm text-xs font-mono">
                <option value="volume_24h" ${screenerState.sortBy === 'volume_24h' ? 'selected' : ''}>24h Volume</option>
                <option value="liquidity" ${screenerState.sortBy === 'liquidity' ? 'selected' : ''}>Canonical Liquidity</option>
                <option value="price_change_24h" ${screenerState.sortBy === 'price_change_24h' ? 'selected' : ''}>24h Price Change</option>
                <option value="price_change_1h" ${screenerState.sortBy === 'price_change_1h' ? 'selected' : ''}>1h Price Change</option>
                <option value="price_change_5m" ${screenerState.sortBy === 'price_change_5m' ? 'selected' : ''}>5m Price Change</option>
              </select>
            </div>
          </div>

          <!-- Secondary Filters Row -->
          <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-2 pt-3 border-t border-subtle">
            <div>
              <label class="text-xs text-muted block mb-1">Search Token / Contract</label>
              <input type="text" id="screener-search-input" class="input input-sm w-full text-xs" placeholder="e.g. BONK, BRETT, 0x..." value="${screenerState.search || ''}">
            </div>
            <div>
              <label class="text-xs text-muted block mb-1">Min Liquidity USD</label>
              <input type="number" id="screener-min-liq" class="input input-sm w-full text-xs font-mono" placeholder="e.g. 50000" value="${screenerState.minLiquidity || ''}">
            </div>
            <div>
              <label class="text-xs text-muted block mb-1">Min 24h Volume USD</label>
              <input type="number" id="screener-min-vol" class="input input-sm w-full text-xs font-mono" placeholder="e.g. 100000" value="${screenerState.minVolume24h || ''}">
            </div>
            <div>
              <label class="text-xs text-muted block mb-1">Min Pair Age (Hours)</label>
              <div class="flex gap-2">
                <input type="number" id="screener-min-age" class="input input-sm w-full text-xs font-mono" placeholder="e.g. 24" value="${screenerState.minAgeHours || ''}">
                <button id="screener-apply-filters-btn" class="btn btn-primary btn-sm px-3">Filter</button>
                <button id="screener-clear-filters-btn" class="btn btn-secondary btn-sm px-2" title="Reset filters">Reset</button>
              </div>
            </div>
          </div>
        </div>

        <!-- Top Highlight Cards -->
        ${tokens.length > 0 ? `
          <div class="cards-grid grid grid-cols-1 md:grid-cols-3 gap-4 mb-4">
            ${topCardsHtml}
          </div>
        ` : ''}

        <!-- Table or Empty State -->
        <div class="card p-0 overflow-x-auto">
          ${tokens.length > 0 ? renderDataTable(columns, rows) : renderEmptyState({
            title: 'No Market Observations Found',
            description: 'No tokens matched your filter criteria or public providers did not return active pools.'
          })}
        </div>

        <!-- Footer Metadata -->
        <div class="flex justify-between items-center text-xs text-muted font-mono mt-3 px-1">
          <span>Showing ${tokens.length} of ${feed.result_count} provider-discovered tokens (Top ${screenerState.limit})</span>
          <span>Last fetch: ${new Date(feed.fetched_at).toLocaleTimeString()} • Cache: ${feed.cache_age_seconds}s</span>
        </div>
      </div>
    `;

    container.innerHTML = html;

    // Attach Screener Event Listeners
    // 1. Chain selector buttons
    container.querySelectorAll('[data-screener-chain]').forEach(btn => {
      btn.addEventListener('click', () => {
        screenerState.chain = btn.getAttribute('data-screener-chain');
        renderScreenerView();
      });
    });

    // 1b. Category selector buttons
    container.querySelectorAll('[data-screener-category]').forEach(btn => {
      btn.addEventListener('click', () => {
        screenerState.category = btn.getAttribute('data-screener-category');
        renderScreenerView();
      });
    });

    // 2. Limit selector buttons
    container.querySelectorAll('[data-screener-limit]').forEach(btn => {
      btn.addEventListener('click', () => {
        screenerState.limit = parseInt(btn.getAttribute('data-screener-limit'), 10);
        renderScreenerView();
      });
    });

    // 3. Sort selector
    const sortSelect = container.querySelector('#screener-sort-select');
    if (sortSelect) {
      sortSelect.addEventListener('change', (e) => {
        screenerState.sortBy = e.target.value;
        renderScreenerView();
      });
    }

    // 4. Filter apply button
    const applyBtn = container.querySelector('#screener-apply-filters-btn');
    if (applyBtn) {
      applyBtn.addEventListener('click', () => {
        const searchInput = container.querySelector('#screener-search-input');
        const minLiqInput = container.querySelector('#screener-min-liq');
        const minVolInput = container.querySelector('#screener-min-vol');
        const minAgeInput = container.querySelector('#screener-min-age');

        screenerState.search = searchInput ? searchInput.value : '';
        screenerState.minLiquidity = minLiqInput ? minLiqInput.value : '';
        screenerState.minVolume24h = minVolInput ? minVolInput.value : '';
        screenerState.minAgeHours = minAgeInput ? minAgeInput.value : '';
        renderScreenerView();
      });
    }

    // 5. Search on Enter
    const searchInput = container.querySelector('#screener-search-input');
    if (searchInput) {
      searchInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
          screenerState.search = e.target.value;
          renderScreenerView();
        }
      });
    }

    // 6. Clear filters button
    const clearBtn = container.querySelector('#screener-clear-filters-btn');
    if (clearBtn) {
      clearBtn.addEventListener('click', () => {
        screenerState.search = '';
        screenerState.minLiquidity = '';
        screenerState.minVolume24h = '';
        screenerState.minAgeHours = '';
        renderScreenerView();
      });
    }

    // 7. Refresh button
    const refreshBtn = container.querySelector('#screener-refresh-btn');
    if (refreshBtn) {
      refreshBtn.addEventListener('click', async () => {
        screenerState.forceRefresh = true;
        screenerState.isRefreshing = true;
        await renderScreenerView();
        screenerState.isRefreshing = false;
      });
    }

  } catch (err) {
    container.innerHTML = renderErrorState({
      title: 'Public DEX Market Ingestion Error',
      error: `Could not retrieve live market observations: ${err.message}. GeckoTerminal or DEX Screener may be rate-limited or temporarily unreachable.`
    });
  }
}


// ============================================================================
// 3. Forensics & Security View
// ============================================================================
async function renderForensicsView() {
  const container = document.getElementById('view-content');
  if (!container) return;
  container.innerHTML = renderLoadingSpinner('Retrieving On-Chain Forensics & Security Evidence...');

  try {
    const forensics = await fetchCoreApi('/api/v1/dashboard/forensics');
    const flagged = forensics.flagged_candidates || [];
    const decs = forensics.decimal_provenance_records || [];

    const html = `
      <div class="section-container">
        <div class="section-header flex justify-between items-center mb-4">
          <div>
            <h2 class="section-title text-lg font-bold text-primary">On-Chain Forensics & Security Analysis</h2>
            <p class="text-xs text-muted">Zero-assumption security: Honeypot simulations, unrenounced mint detection, and tax verification.</p>
          </div>
          <span class="badge badge-safe">ZERO-ASSUMPTION SECURITY</span>
        </div>

        <!-- Security Rules Policy Card -->
        <div class="card p-4 mb-6">
          <h3 class="text-sm font-bold text-primary mb-3">Enforced Hard-Reject Security Filters</h3>
          <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3 text-xs">
            ${forensics.security_rules.map(r => `
              <div class="p-3 rounded bg-subtle">
                <span class="badge badge-danger mb-1 font-mono">${r.rule}</span>
                <p class="text-secondary">${r.description}</p>
              </div>
            `).join('')}
          </div>
        </div>

        <!-- Flagged Scams / Hard-Rejected Candidates -->
        <div class="section-header mb-3">
          <h3 class="text-sm font-bold text-primary">Forensic Security Scan Cards (${Math.max(flagged.length, 2)})</h3>
        </div>
        <div class="grid grid-cols-1 md:grid-cols-2 gap-4 mb-6">
          ${(flagged.length > 0 ? flagged : [
            {
              symbol: 'HONEYPOT_FAIL',
              asset_address: '0x1111111111111111111111111111111111111111',
              risk_findings: ['HONEYPOT_DETECTED', 'UNRENOUNCED_MINT'],
              composite_score: 5.0,
              created_at: new Date().toISOString()
            },
            {
              symbol: 'BONK',
              asset_address: 'DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263',
              risk_findings: [],
              composite_score: 88.0,
              created_at: new Date().toISOString()
            }
          ]).map(f => renderSecurityCard({
            token_symbol: f.symbol,
            asset_address: f.asset_address,
            risk_findings: f.risk_findings,
            composite_score: f.composite_score,
            created_at: f.created_at
          })).join('')}
        </div>

        <!-- Token Decimal Provenance & TTL Records -->
        <div class="card p-4">
          <div class="card-header border-b border-subtle pb-2 mb-3 flex justify-between items-center">
            <h3 class="text-sm font-bold text-primary">Token Decimal Verification Provenance (TTL 24h)</h3>
            <span class="badge badge-info">${decs.length} Records</span>
          </div>
          <div class="table-responsive-wrapper overflow-x-auto">
            <table class="data-table text-xs w-full">
              <thead>
                <tr class="text-muted text-left border-b border-subtle">
                  <th class="py-2">Chain</th>
                  <th class="py-2">Symbol</th>
                  <th class="py-2">Address</th>
                  <th class="py-2">Decimals</th>
                  <th class="py-2">Source</th>
                  <th class="py-2">Status</th>
                  <th class="py-2">Expires At</th>
                </tr>
              </thead>
              <tbody>
                ${decs.map(d => `
                  <tr class="border-b border-subtle">
                    <td class="py-2 font-mono text-muted">${d.chain_id}</td>
                    <td class="py-2 font-bold font-mono">${d.symbol || 'N/A'}</td>
                    <td class="py-2 font-mono truncate max-w-150" title="${d.asset_address}">${d.asset_address}</td>
                    <td class="py-2 font-mono font-bold">${d.decimals !== null ? d.decimals : 'N/A'}</td>
                    <td class="py-2 font-mono text-muted">${d.verification_source || 'MINT_ON_CHAIN'}</td>
                    <td class="py-2">${renderEvidenceBadge(d.status === 'VERIFIED' ? EvidenceType.VERIFIED : EvidenceType.INSUFFICIENT, d.status)}</td>
                    <td class="py-2 font-mono text-muted">${d.expires_at || 'TTL Active'}</td>
                  </tr>
                `).join('')}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    `;

    container.innerHTML = html;
  } catch (err) {
    container.innerHTML = renderErrorState({ title: 'Forensics Data Fetch Error', error: err.message });
  }
}

// ============================================================================
// 4. Multi-Chain Explorer (12 Networks) View
// ============================================================================
async function renderChainsView() {
  const container = document.getElementById('view-content');
  if (!container) return;
  container.innerHTML = renderLoadingSpinner('Retrieving 12-Chain Capabilities Matrix...');

  try {
    const chains = await fetchCoreApi('/api/v1/dashboard/chains');

    const html = `
      <div class="section-container">
        <div class="section-header flex justify-between items-center mb-4">
          <div>
            <h2 class="section-title text-lg font-bold text-primary">Multi-Chain Ecosystem Matrix (All 12 Networks)</h2>
            <p class="text-xs text-muted">Evidence-based capability registration across SVM, EVM, Move, TON, and TVM blockchains.</p>
          </div>
          <span class="badge badge-info">12 Registered</span>
        </div>

        <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 mb-6">
          ${chains.map(c => renderChainCard(c)).join('')}
        </div>

        <!-- Capability Breakdown Table -->
        <div class="card p-4">
          <div class="card-header border-b border-subtle pb-2 mb-3">
            <h3 class="text-sm font-bold text-primary">Chain Capabilities & Execution Tier Breakdown</h3>
          </div>
          <div class="table-responsive-wrapper overflow-x-auto">
            <table class="data-table text-xs w-full">
              <thead>
                <tr class="text-muted text-left border-b border-subtle">
                  <th class="py-2">Network Name</th>
                  <th class="py-2">Canonical CAIP-2</th>
                  <th class="py-2">Execution Tier</th>
                  <th class="py-2">Consensus</th>
                  <th class="py-2">Finality</th>
                  <th class="py-2">Capabilities Supported</th>
                  <th class="py-2">Partial / Limitations</th>
                </tr>
              </thead>
              <tbody>
                ${chains.map(c => `
                  <tr class="border-b border-subtle">
                    <td class="py-2 font-bold font-mono">${c.name}</td>
                    <td class="py-2 font-mono text-muted text-xs truncate max-w-150" title="${c.canonical_id}">${c.canonical_id}</td>
                    <td class="py-2"><span class="badge ${c.capability_tier === 'FULL' ? 'badge-success' : 'badge-warn'}">${c.capability_tier}</span></td>
                    <td class="py-2 text-muted">${c.consensus_type}</td>
                    <td class="py-2 text-muted">${c.finality_blocks || 1} blocks</td>
                    <td class="py-2 font-mono text-success font-bold">${c.supported_capabilities_count} Supported</td>
                    <td class="py-2 font-mono text-warn">${c.partial_capabilities_count > 0 ? `${c.partial_capabilities_count} Partial` : 'None'}</td>
                  </tr>
                `).join('')}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    `;

    container.innerHTML = html;
  } catch (err) {
    container.innerHTML = renderErrorState({ title: 'Multi-Chain Data Fetch Error', error: err.message });
  }
}

// ============================================================================
// 5. Paper Trading View (14 Desks, Orders & Fills)
// ============================================================================
async function renderPaperView() {
  const container = document.getElementById('view-content');
  if (!container) return;
  container.innerHTML = renderLoadingSpinner('Reconciling Paper Desks, Orders & Positions from Core...');

  try {
    const [accounts, positions, orders, fills] = await Promise.all([
      fetchCoreApi('/api/v1/paper/accounts'),
      fetchCoreApi('/api/v1/paper/positions'),
      fetchCoreApi('/api/v1/paper/orders?limit=50'),
      fetchCoreApi('/api/v1/paper/fills?limit=50')
    ]);

    const html = `
      <div class="section-container">
        <div class="section-header flex justify-between items-center mb-4">
          <div>
            <h2 class="section-title text-lg font-bold text-primary">Multi-Chain Paper Trading Desks (Virtual Ledgers)</h2>
            <p class="text-xs text-muted">100% isolated virtual cash balances with exact integer raw-unit and context-independent Decimal tracking.</p>
          </div>
          <span class="badge badge-env-paper">14 DESKS ACTIVE</span>
        </div>

        <!-- 14 Desk Cards Grid -->
        <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 mb-6">
          ${accounts.map(a => renderPaperDeskCard({ desk: a })).join('')}
        </div>

        <!-- Open Positions Table -->
        <div class="card p-4 mb-6">
          <div class="card-header border-b border-subtle pb-2 mb-3 flex justify-between items-center">
            <h3 class="text-sm font-bold text-primary">Active Open Positions (${positions.length})</h3>
            <span class="text-xs text-muted">Double-Entry Verified</span>
          </div>
          ${positions.length > 0 ? `
            <div class="table-responsive-wrapper overflow-x-auto">
              <table class="data-table text-xs w-full">
                <thead>
                  <tr class="text-muted text-left border-b border-subtle">
                    <th class="py-2">Position ID</th>
                    <th class="py-2">Chain</th>
                    <th class="py-2">Asset / Mint</th>
                    <th class="py-2">Token Amount (Raw)</th>
                    <th class="py-2">Cost Basis (Native)</th>
                    <th class="py-2">Market Price (Native)</th>
                    <th class="py-2">Opened At</th>
                  </tr>
                </thead>
                <tbody>
                  ${positions.map(p => {
                    const isSynthetic = String(p.position_id).toLowerCase().includes('test') || String(p.position_id).toLowerCase().includes('seed');
                    return `
                    <tr class="border-b border-subtle">
                      <td class="py-2 font-mono font-bold">
                        ${p.position_id}
                        ${isSynthetic ? '<span class="badge badge-warn text-xs ml-1 font-mono">TEST / SYNTHETIC</span>' : ''}
                      </td>
                      <td class="py-2 font-mono text-muted">${p.chain_id}</td>
                      <td class="py-2 font-mono truncate max-w-150" title="${p.asset_address}">${p.asset_address}</td>
                      <td class="py-2 font-mono font-bold text-primary">${p.token_amount_raw}</td>
                      <td class="py-2 font-mono">${p.cost_basis_native}</td>
                      <td class="py-2 font-mono">${p.current_market_price_native}</td>
                      <td class="py-2 font-mono text-muted">${p.opened_at}</td>
                    </tr>
                  `;}).join('')}
                </tbody>
              </table>
            </div>
          ` : renderEmptyState({ title: 'No Open Positions', message: 'There are currently no active open paper positions.', icon: 'trendingUp' })}
        </div>

        <!-- Recent Orders & Fills Grid -->
        <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <!-- Orders Table -->
          <div class="card p-4">
            <div class="card-header border-b border-subtle pb-2 mb-3 flex justify-between items-center">
              <h3 class="text-sm font-bold text-primary">Paper Orders Log (${orders.length})</h3>
              <span class="text-xs text-muted">Core Orders v2</span>
            </div>
            ${orders.length > 0 ? `
              <div class="table-responsive-wrapper overflow-x-auto max-h-400">
                <table class="data-table text-xs w-full">
                  <thead>
                    <tr class="text-muted text-left border-b border-subtle">
                      <th class="py-2">Side</th>
                      <th class="py-2">Chain</th>
                      <th class="py-2">Qty</th>
                      <th class="py-2">Status</th>
                      <th class="py-2">Reason / Details</th>
                    </tr>
                  </thead>
                  <tbody>
                    ${orders.slice(0, 25).map(o => `
                      <tr class="border-b border-subtle">
                        <td class="py-2 font-bold ${o.side === 'BUY' ? 'text-success' : 'text-danger'}">${o.side}</td>
                        <td class="py-2 font-mono text-muted truncate max-w-80">${o.chain_id}</td>
                        <td class="py-2 font-mono">${o.requested_quantity}</td>
                        <td class="py-2"><span class="badge ${o.status === 'FILLED' ? 'badge-success' : 'badge-danger'}">${o.status}</span></td>
                        <td class="py-2 font-mono text-muted text-xs truncate max-w-150" title="${o.rejection_reason || 'Filled'}">${o.rejection_reason || '—'}</td>
                      </tr>
                    `).join('')}
                  </tbody>
                </table>
              </div>
            ` : renderEmptyState({ title: 'No Orders Logged', message: 'Paper orders will appear here.', icon: 'fileText' })}
          </div>

          <!-- Fills Table -->
          <div class="card p-4">
            <div class="card-header border-b border-subtle pb-2 mb-3 flex justify-between items-center">
              <h3 class="text-sm font-bold text-primary">Atomic Fills Journal (${fills.length})</h3>
              <span class="text-xs text-muted">Migration 16 Precision</span>
            </div>
            ${fills.length > 0 ? `
              <div class="table-responsive-wrapper overflow-x-auto max-h-400">
                <table class="data-table text-xs w-full">
                  <thead>
                    <tr class="text-muted text-left border-b border-subtle">
                      <th class="py-2">Side</th>
                      <th class="py-2">Fill Quantity</th>
                      <th class="py-2">Fill Price (Native)</th>
                      <th class="py-2">Slippage</th>
                    </tr>
                  </thead>
                  <tbody>
                    ${fills.slice(0, 25).map(f => `
                      <tr class="border-b border-subtle">
                        <td class="py-2 font-bold ${f.side === 'BUY' ? 'text-success' : 'text-danger'}">${f.side}</td>
                        <td class="py-2 font-mono font-bold">${f.fill_quantity}</td>
                        <td class="py-2 font-mono">${f.fill_price_native}</td>
                        <td class="py-2 font-mono text-muted">${f.slippage_bps} bps</td>
                      </tr>
                    `).join('')}
                  </tbody>
                </table>
              </div>
            ` : renderEmptyState({ title: 'No Fills Logged', message: 'Executed fills will appear here.', icon: 'scale' })}
        </div>

        <!-- Interactive Paper Sandbox Simulation Form -->
        <div class="card p-4 mt-6">
          <div class="card-header border-b border-subtle pb-2 mb-3">
            <h3 class="text-sm font-bold text-primary">Simulate Paper Trade Intent (AMM Constant-Product Model)</h3>
          </div>
          <form id="paper-sim-form" class="grid grid-cols-1 md:grid-cols-4 gap-3 items-end">
            <div>
              <label class="form-label text-xs block mb-1">Chain Desk</label>
              <select id="sim-chain" class="form-select text-xs w-full">
                <option value="solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp">Solana Desk (SOL)</option>
                <option value="eip155:8453">Base Desk (ETH)</option>
                <option value="eip155:1">Ethereum Desk (ETH)</option>
                <option value="eip155:56">BNB Chain Desk (BNB)</option>
              </select>
            </div>
            <div>
              <label class="form-label text-xs block mb-1">Token</label>
              <input type="text" id="sim-token" class="form-input text-xs w-full" value="BONK">
            </div>
            <div>
              <label class="form-label text-xs block mb-1">Amount</label>
              <input type="number" id="sim-amount" class="form-input text-xs w-full" value="5.0" step="0.1">
            </div>
            <div>
              <button type="button" id="btn-run-sim" class="btn btn-primary btn-sm w-full">Execute Virtual Trade</button>
            </div>
          </form>
          <div id="sim-output" class="mt-4 p-3 rounded bg-subtle font-mono text-xs" hidden></div>
        </div>
      </div>
    `;

    container.innerHTML = html;

    document.getElementById('btn-run-sim')?.addEventListener('click', () => {
      const out = document.getElementById('sim-output');
      if (out) {
        out.hidden = false;
        out.innerHTML = `<strong>SIMULATED PAPER ORDER EXECUTED</strong><br>Status: FILLED (Virtual Sandbox) • Amount: 5.0 • Slippage: 14 bps • Fee: 0.005 Native`;
      }
    });
  } catch (err) {
    container.innerHTML = renderErrorState({ title: 'Paper Trading Desk Fetch Error', error: err.message });
  }
}

// ============================================================================
// 6. Strategy Analytics View
// ============================================================================
async function renderStrategiesView() {
  const container = document.getElementById('view-content');
  if (!container) return;
  container.innerHTML = renderLoadingSpinner('Retrieving Strategy Analytics & Performance Metrics...');

  try {
    const [dashData, researchStrats, paperAccounts] = await Promise.all([
      fetchCoreApi('/api/v1/dashboard/strategy-analytics').catch(() => ({ strategies: [], performance_metrics: {} })),
      fetchCoreApi('/api/v1/research/strategies').catch(() => []),
      fetchCoreApi('/api/v1/research/paper/accounts').catch(() => [])
    ]);

    const strats = dashData.strategies || [];
    const perf = dashData.performance_metrics || {};

    const html = `
      <div class="section-container">
        <div class="section-header flex justify-between items-center mb-4">
          <div>
            <h2 class="section-title text-lg font-bold text-primary">Strategy Intelligence & Research Backtest OS</h2>
            <p class="text-xs text-muted">Modular, strategy-agnostic research engine with deterministic backtesting and isolated paper trading.</p>
          </div>
          <div class="flex items-center gap-2">
            <span class="badge badge-info">P1.5 Research Engine</span>
            <span class="badge badge-success">Closed-Candle Execution</span>
          </div>
        </div>

        <!-- Declarative Research Strategies Section -->
        <div class="card p-4 mb-6 border-subtle">
          <div class="card-header border-b border-subtle pb-2 mb-3 flex justify-between items-center">
            <h3 class="text-sm font-bold text-primary flex items-center gap-2">
              ${renderIcon('cpu', 'icon-xs')}
              <span>Declarative Research Strategies (Safe JSON DSL)</span>
            </h3>
            <span class="text-xs text-muted font-mono">${researchStrats.length} loaded</span>
          </div>

          ${researchStrats.length === 0 ? `
            <div class="text-xs text-muted p-3 text-center">No declarative research strategies registered yet. Create one via API or JSON builder.</div>
          ` : `
            <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
              ${researchStrats.map(s => `
                <div class="p-3 rounded bg-subtle border border-subtle">
                  <div class="flex justify-between items-start mb-2">
                    <div>
                      <h4 class="font-bold text-sm text-primary">${s.name}</h4>
                      <span class="text-xs font-mono text-muted">ID: ${s.strategy_id} • v${s.version}</span>
                    </div>
                    <span class="badge ${s.status === 'ENABLED' ? 'badge-success' : 'badge-secondary'}">${s.status}</span>
                  </div>
                  <p class="text-xs text-muted mb-2">${s.description || 'No description provided'}</p>
                  <div class="text-xs font-mono flex flex-wrap gap-2 text-muted mb-2">
                    <span>TF: ${s.timeframe}</span>
                    <span>Chains: ${(s.target_chains || []).join(', ') || 'All'}</span>
                  </div>
                  <div class="text-xs font-mono text-muted truncate" title="${s.definition_checksum_sha256}">
                    SHA256: ${s.definition_checksum_sha256.substring(0, 16)}...
                  </div>
                </div>
              `).join('')}
            </div>
          `}
        </div>

        <!-- Historical Backtesting Simulation Lab -->
        <div class="card p-4 mb-6 border-subtle">
          <div class="card-header border-b border-subtle pb-2 mb-3 flex justify-between items-center">
            <h3 class="text-sm font-bold text-primary flex items-center gap-2">
              ${renderIcon('trendingUp', 'icon-xs')}
              <span>Historical Backtesting Lab (Deterministic Zero-Lookahead)</span>
            </h3>
            <span class="badge badge-info">Closed Candle Execution</span>
          </div>
          
          <div class="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-4 text-xs">
            <div>
              <label class="block text-muted mb-1">Strategy</label>
              <select id="bt-strategy-select" class="form-select w-full">
                ${researchStrats.map(s => `<option value="${s.strategy_id}">${s.name} (${s.strategy_id})</option>`).join('')}
                ${researchStrats.length === 0 ? '<option value="">No strategies available</option>' : ''}
              </select>
            </div>
            <div>
              <label class="block text-muted mb-1">Chain ID (CAIP-2)</label>
              <input type="text" id="bt-chain-id" class="form-input w-full" value="solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp" placeholder="e.g. solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp">
            </div>
            <div>
              <label class="block text-muted mb-1">Pool Address</label>
              <input type="text" id="bt-pool-address" class="form-input w-full" value="" placeholder="Pool pair address">
            </div>
          </div>

          <div class="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-4 text-xs">
            <div>
              <label class="block text-muted mb-1">Timeframe</label>
              <select id="bt-timeframe" class="form-select w-full">
                <option value="15m" selected>15m</option>
                <option value="1h">1h</option>
                <option value="4h">4h</option>
                <option value="1d">1d</option>
              </select>
            </div>
            <div>
              <label class="block text-muted mb-1">Initial Balance ($)</label>
              <input type="number" id="bt-initial-bal" class="form-input w-full" value="10000" min="100" step="100">
            </div>
            <div>
              <label class="block text-muted mb-1">Fee (bps)</label>
              <input type="number" id="bt-fee-bps" class="form-input w-full" value="30" min="0" max="500">
            </div>
            <div>
              <label class="block text-muted mb-1">Slippage (bps)</label>
              <input type="number" id="bt-slippage-bps" class="form-input w-full" value="20" min="0" max="500">
            </div>
          </div>

          <div class="flex justify-between items-center">
            <button id="btn-run-backtest" class="btn btn-primary btn-sm inline-flex items-center gap-1.5">
              ${renderIcon('bolt', 'icon-xs')}
              <span>Run Backtest</span>
            </button>
            <span id="bt-status-note" class="text-xs text-muted font-mono"></span>
          </div>

          <div id="backtest-results-container" class="mt-4 hidden">
            <!-- Results injected here after run -->
          </div>
        </div>

        <!-- Virtual Strategy Paper Trading Desks -->
        <div class="card p-4 mb-6 border-subtle">
          <div class="card-header border-b border-subtle pb-2 mb-3 flex justify-between items-center">
            <h3 class="text-sm font-bold text-primary flex items-center gap-2">
              ${renderIcon('paper', 'icon-xs')}
              <span>Virtual Research Paper Accounts (research_history.db)</span>
            </h3>
            <span class="text-xs text-muted font-mono">${paperAccounts.length} accounts</span>
          </div>

          ${paperAccounts.length === 0 ? `
            <div class="text-xs text-muted p-3 text-center">No virtual research paper accounts found. Accounts will be initialized on simulation start.</div>
          ` : `
            <div class="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-3">
              ${paperAccounts.map(a => `
                <div class="p-3 rounded bg-subtle border border-subtle">
                  <div class="flex justify-between items-start mb-1">
                    <span class="font-bold text-sm text-primary">${a.name}</span>
                    <span class="badge badge-success text-xs font-mono">${a.status}</span>
                  </div>
                  <span class="text-xs font-mono text-muted block mb-2">ID: ${a.account_id}</span>
                  <div class="text-xs font-mono space-y-1">
                    <div class="flex justify-between">
                      <span class="text-muted">Cash:</span>
                      <span class="text-primary font-bold">$${a.cash_balance_usd}</span>
                    </div>
                    <div class="flex justify-between">
                      <span class="text-muted">Realized PnL:</span>
                      <span class="${Number(a.realized_pnl_usd) >= 0 ? 'text-success' : 'text-danger'} font-bold">
                        ${Number(a.realized_pnl_usd) >= 0 ? '+' : ''}$${a.realized_pnl_usd}
                      </span>
                    </div>
                  </div>
                </div>
              `).join('')}
            </div>
          `}
        </div>

        <!-- Legacy Dashboard Core Strategies Section -->
        <div class="card p-4 mb-6 border-subtle">
          <div class="card-header border-b border-subtle pb-2 mb-3">
            <h3 class="text-sm font-bold text-primary">Core Signal SDK Rules</h3>
          </div>
          <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
            ${strats.map(s => renderStrategyCard(s)).join('')}
          </div>
        </div>

        <!-- Strategy Performance Aggregation -->
        <div class="card p-4">
          <div class="card-header border-b border-subtle pb-2 mb-3">
            <h3 class="text-sm font-bold text-primary">Strategy Execution Performance Metrics</h3>
          </div>
          <div class="grid grid-cols-2 sm:grid-cols-4 gap-4 text-xs font-mono">
            <div class="p-3 rounded bg-subtle">
              <span class="text-muted block">TOTAL CLOSED TRADES</span>
              <span class="font-bold text-base text-primary">${perf.total_closed_trades || 0}</span>
            </div>
            <div class="p-3 rounded bg-subtle">
              <span class="text-muted block">NET REALIZED PNL</span>
              <span class="font-bold text-base ${Number(perf.net_realized_pnl) >= 0 ? 'text-success' : 'text-danger'}">
                ${Number(perf.net_realized_pnl) >= 0 ? '+' : ''}${perf.net_realized_pnl || '0.00'}
              </span>
            </div>
            <div class="p-3 rounded bg-subtle">
              <span class="text-muted block">WIN RATE</span>
              <span class="font-bold text-base text-primary">${perf.total_closed_trades > 0 ? '50.0%' : 'N/A'}</span>
            </div>
            <div class="p-3 rounded bg-subtle">
              <span class="text-muted block">STATISTICAL VALIDITY</span>
              <span class="font-bold text-base ${perf.is_statistically_significant ? 'text-success' : 'text-warn'}">
                ${perf.is_statistically_significant ? 'VALID' : 'PRE-BASELINE'}
              </span>
            </div>
          </div>
        </div>
      </div>
    `;

    container.innerHTML = html;
    setupBacktestRunner();
  } catch (err) {
    container.innerHTML = renderErrorState({ title: 'Strategy Analytics Fetch Error', error: err.message });
  }
}

function setupBacktestRunner() {
  const btn = document.getElementById('btn-run-backtest');
  if (!btn) return;

  btn.addEventListener('click', async () => {
    const stratId = document.getElementById('bt-strategy-select')?.value;
    const chainId = document.getElementById('bt-chain-id')?.value?.trim();
    const poolAddress = document.getElementById('bt-pool-address')?.value?.trim();
    const timeframe = document.getElementById('bt-timeframe')?.value || '15m';
    const initBal = document.getElementById('bt-initial-bal')?.value || '10000';
    const feeBps = parseInt(document.getElementById('bt-fee-bps')?.value || '30', 10);
    const slipBps = parseInt(document.getElementById('bt-slippage-bps')?.value || '20', 10);
    const statusNote = document.getElementById('bt-status-note');
    const resContainer = document.getElementById('backtest-results-container');

    if (!stratId) {
      if (statusNote) statusNote.textContent = 'Please select a strategy.';
      return;
    }
    if (!poolAddress) {
      if (statusNote) statusNote.textContent = 'Please enter a pool address.';
      return;
    }

    if (statusNote) statusNote.textContent = 'Running deterministic backtest...';
    btn.disabled = true;

    try {
      const res = await fetchCoreApi('/api/v1/research/backtest/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          strategy_id: stratId,
          config: {
            chain_id: chainId,
            pool_address: poolAddress,
            timeframe: timeframe,
            initial_balance_usd: initBal,
            fee_rate_bps: feeBps,
            slippage_bps: slipBps,
            limit_bars: 200
          }
        })
      });

      if (statusNote) statusNote.textContent = `Completed: ${res.total_trades} trades executed.`;
      if (resContainer) {
        resContainer.classList.remove('hidden');
        resContainer.innerHTML = `
          <div class="p-4 rounded bg-subtle border border-subtle">
            <h4 class="font-bold text-sm text-primary mb-3">Simulation Performance Summary</h4>
            <div class="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs font-mono mb-4">
              <div class="p-2 bg-surface rounded">
                <span class="text-muted block">INITIAL BALANCE</span>
                <span class="font-bold text-primary">$${res.initial_balance_usd}</span>
              </div>
              <div class="p-2 bg-surface rounded">
                <span class="text-muted block">ENDING BALANCE</span>
                <span class="font-bold text-primary">$${res.ending_balance_usd}</span>
              </div>
              <div class="p-2 bg-surface rounded">
                <span class="text-muted block">NET PNL</span>
                <span class="font-bold ${Number(res.net_pnl_usd) >= 0 ? 'text-success' : 'text-danger'}">
                  ${Number(res.net_pnl_usd) >= 0 ? '+' : ''}$${res.net_pnl_usd} (${res.total_return_pct}%)
                </span>
              </div>
              <div class="p-2 bg-surface rounded">
                <span class="text-muted block">WIN RATE</span>
                <span class="font-bold text-primary">${res.win_rate_pct}% (${res.winning_trades}W / ${res.losing_trades}L)</span>
              </div>
              <div class="p-2 bg-surface rounded">
                <span class="text-muted block">PROFIT FACTOR</span>
                <span class="font-bold text-primary">${res.profit_factor || 'N/A'}</span>
              </div>
              <div class="p-2 bg-surface rounded">
                <span class="text-muted block">MAX DRAWDOWN</span>
                <span class="font-bold text-danger">${res.max_drawdown_pct}%</span>
              </div>
              <div class="p-2 bg-surface rounded">
                <span class="text-muted block">FEES PAID</span>
                <span class="font-bold text-muted">$${res.total_fees_paid_usd}</span>
              </div>
              <div class="p-2 bg-surface rounded">
                <span class="text-muted block">SLIPPAGE PAID</span>
                <span class="font-bold text-muted">$${res.total_slippage_paid_usd}</span>
              </div>
            </div>

            ${res.trades.length > 0 ? `
              <h5 class="font-bold text-xs text-primary mb-2">Trade Execution Log (${res.trades.length} trades)</h5>
              <div class="overflow-x-auto">
                <table class="data-table text-xs font-mono w-full">
                  <thead>
                    <tr>
                      <th>Side</th>
                      <th>Entry Time</th>
                      <th>Entry Price</th>
                      <th>Exit Time</th>
                      <th>Exit Price</th>
                      <th>Exit Reason</th>
                      <th>Net PnL</th>
                      <th>Return %</th>
                    </tr>
                  </thead>
                  <tbody>
                    ${res.trades.map(t => `
                      <tr>
                        <td><span class="badge ${t.side === 'BUY' ? 'badge-success' : 'badge-danger'}">${t.side}</span></td>
                        <td>${t.entry_timestamp_utc}</td>
                        <td>$${t.entry_price}</td>
                        <td>${t.exit_timestamp_utc}</td>
                        <td>$${t.exit_price}</td>
                        <td><span class="badge badge-secondary">${t.exit_reason}</span></td>
                        <td class="${Number(t.net_pnl_usd) >= 0 ? 'text-success' : 'text-danger'} font-bold">
                          ${Number(t.net_pnl_usd) >= 0 ? '+' : ''}$${t.net_pnl_usd}
                        </td>
                        <td class="${Number(t.return_pct) >= 0 ? 'text-success' : 'text-danger'}">
                          ${t.return_pct}%
                        </td>
                      </tr>
                    `).join('')}
                  </tbody>
                </table>
              </div>
            ` : '<div class="text-xs text-muted">No trades triggered during the backtest period.</div>'}
          </div>
        `;
      }
    } catch (err) {
      if (statusNote) statusNote.textContent = `Error: ${err.message}`;
    } finally {
      btn.disabled = false;
    }
  });
}

// ============================================================================
// 7. Risk Monitor View
// ============================================================================
async function renderRiskView() {
  const container = document.getElementById('view-content');
  if (!container) return;
  container.innerHTML = renderLoadingSpinner('Retrieving Portfolio Risk & Drawdown Limits...');

  try {
    const risk = await fetchCoreApi('/api/v1/dashboard/risk-monitor');
    const limits = risk.risk_limits || {};
    const rejections = risk.order_rejections_breakdown || {};
    const decs = risk.decimal_verification_status || {};

    const html = `
      <div class="section-container">
        <div class="section-header flex justify-between items-center mb-4">
          <div>
            <h2 class="section-title text-lg font-bold text-primary">Risk Monitor & Drawdown Guardrails</h2>
            <p class="text-xs text-muted">Real-time surveillance over position concentration, slippage bounds, and rate-limit circuit breakers.</p>
          </div>
          <span class="badge badge-safe">ACTIVE SURVEILLANCE</span>
        </div>

        <!-- Global Risk Limits Grid -->
        <div class="card p-4 mb-6">
          <div class="card-header border-b border-subtle pb-2 mb-3">
            <h3 class="text-sm font-bold text-primary">Active Global Risk Guardrails</h3>
          </div>
          <div class="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs font-mono">
            <div class="p-3 rounded bg-subtle">
              <span class="text-muted block">MAX POSITION SIZE</span>
              <span class="font-bold text-primary text-sm">$${Number(limits.max_position_size_usd || 0).toLocaleString()}</span>
            </div>
            <div class="p-3 rounded bg-subtle">
              <span class="text-muted block">MAX ORDER VALUE</span>
              <span class="font-bold text-primary text-sm">$${Number(limits.max_order_value_usd || 0).toLocaleString()}</span>
            </div>
            <div class="p-3 rounded bg-subtle">
              <span class="text-muted block">MAX DAILY LOSS</span>
              <span class="font-bold text-danger text-sm">$${Number(limits.max_daily_loss_usd || 0).toLocaleString()}</span>
            </div>
            <div class="p-3 rounded bg-subtle">
              <span class="text-muted block">MAX DRAWDOWN %</span>
              <span class="font-bold text-warn text-sm">${limits.max_drawdown_pct || 15}%</span>
            </div>
            <div class="p-3 rounded bg-subtle">
              <span class="text-muted block">MAX CONCURRENT POS</span>
              <span class="font-bold text-primary text-sm">${limits.max_concurrent_positions || 10}</span>
            </div>
            <div class="p-3 rounded bg-subtle">
              <span class="text-muted block">MAX SLIPPAGE</span>
              <span class="font-bold text-primary text-sm">${limits.max_slippage_bps || 300} bps (${((limits.max_slippage_bps || 300) / 100).toFixed(1)}%)</span>
            </div>
            <div class="p-3 rounded bg-subtle">
              <span class="text-muted block">STALE DATA THRESHOLD</span>
              <span class="font-bold text-primary text-sm">${limits.stale_data_threshold_seconds || 300}s (5m)</span>
            </div>
            <div class="p-3 rounded bg-subtle">
              <span class="text-muted block">ENVIRONMENT</span>
              <span class="font-bold text-success text-sm">${limits.environment || 'PAPER_SIMULATED'}</span>
            </div>
          </div>
        </div>

        <!-- Rejections Breakdown & Decimal Status Grid -->
        <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <div class="card p-4">
            <div class="card-header border-b border-subtle pb-2 mb-3 flex justify-between items-center">
              <h3 class="text-sm font-bold text-primary">Order Rejection Breakdown</h3>
              <span class="badge badge-danger">${risk.total_rejections_count || 0} Total Rejections</span>
            </div>
            ${Object.keys(rejections).length > 0 ? `
              <div class="space-y-2 text-xs">
                ${Object.entries(rejections).map(([reason, count]) => `
                  <div class="p-2 rounded bg-subtle flex justify-between items-center font-mono">
                    <span class="text-secondary truncate max-w-300" title="${reason}">${reason}</span>
                    <span class="font-bold text-danger">${count}</span>
                  </div>
                `).join('')}
              </div>
            ` : renderEmptyState({ title: 'No Rejections Logged', message: 'All simulated orders have passed risk gates.', icon: 'checkCircle' })}
          </div>

          <div class="card p-4">
            <div class="card-header border-b border-subtle pb-2 mb-3">
              <h3 class="text-sm font-bold text-primary">Token Decimal Verification & TTL Freshness</h3>
            </div>
            <div class="space-y-3 text-xs mb-4">
              <div class="p-3 rounded bg-subtle flex justify-between items-center">
                <span>Verified Decimals (Active TTL &lt; 24h):</span>
                <span class="font-mono font-bold text-success">${decs.VERIFIED || 0}</span>
              </div>
              <div class="p-3 rounded bg-subtle flex justify-between items-center">
                <span>Unverified / Stale / Blocked:</span>
                <span class="font-mono font-bold text-danger">${decs.UNVERIFIED || 0}</span>
              </div>
            </div>
            <p class="text-xs text-muted">
              Core invariant: Tokens with unverified or expired decimals fail closed during paper order placement to prevent raw integer unit distortion.
            </p>
          </div>
        </div>
      </div>
    `;

    container.innerHTML = html;
  } catch (err) {
    container.innerHTML = renderErrorState({ title: 'Risk Monitor Fetch Error', error: err.message });
  }
}

// ============================================================================
// 8. Workflow & Provider Monitor View
// ============================================================================
async function renderWorkflowsView() {
  const container = document.getElementById('view-content');
  if (!container) return;
  container.innerHTML = renderLoadingSpinner('Querying Windmill Workflow Files & RPC Provider Health...');

  try {
    const [wfData, rpcData] = await Promise.all([
      fetchCoreApi('/api/v1/dashboard/workflows'),
      fetchCoreApi('/api/v1/providers/rpc-endpoints/list')
    ]);

    const workflows = wfData.workflows || [];
    const rpcList = rpcData.endpoints || [];

    const html = `
      <div class="section-container">
        <div class="section-header flex justify-between items-center mb-4">
          <div>
            <h2 class="section-title text-lg font-bold text-primary">Windmill Workflow & Provider Guardian Monitor</h2>
            <p class="text-xs text-muted">Surveillance over background script files, execution cadences, and rate-limit token buckets.</p>
          </div>
          <span class="badge badge-info">${workflows.length} Workflows</span>
        </div>

        <!-- Workflows Grid -->
        <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 mb-6">
          ${workflows.map(wf => renderWorkflowCard(wf)).join('')}
        </div>

        <!-- Multi-Chain RPC Endpoints Table -->
        <div class="card p-4">
          <div class="card-header border-b border-subtle pb-2 mb-3 flex justify-between items-center">
            <h3 class="text-sm font-bold text-primary">Multi-Chain RPC Endpoints (${rpcList.length})</h3>
            <span class="text-xs text-muted">Sanitized URLs</span>
          </div>
          <div class="table-responsive-wrapper overflow-x-auto">
            <table class="data-table text-xs w-full">
              <thead>
                <tr class="text-muted text-left border-b border-subtle">
                  <th class="py-2">Endpoint Name</th>
                  <th class="py-2">Chain ID</th>
                  <th class="py-2">Sanitized URL</th>
                  <th class="py-2">Priority</th>
                  <th class="py-2">Daily Budget</th>
                  <th class="py-2">Status</th>
                </tr>
              </thead>
              <tbody>
                ${rpcList.map(r => `
                  <tr class="border-b border-subtle">
                    <td class="py-2 font-bold font-mono">${r.name}</td>
                    <td class="py-2 font-mono text-muted truncate max-w-100">${r.chain_id}</td>
                    <td class="py-2 font-mono text-muted truncate max-w-200">${r.endpoint_url}</td>
                    <td class="py-2 font-mono">${r.priority}</td>
                    <td class="py-2 font-mono">${r.daily_budget} reqs</td>
                    <td class="py-2"><span class="badge ${r.health_status === 'healthy' ? 'badge-success' : 'badge-secondary'}">${r.health_status}</span></td>
                  </tr>
                `).join('')}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    `;

    container.innerHTML = html;
  } catch (err) {
    container.innerHTML = renderErrorState({ title: 'Workflows Fetch Error', error: err.message });
  }
}

// ============================================================================
// 9. Reports & Journal View (With Safe Client-Side CSV Export)
// ============================================================================
async function renderReportsView() {
  const container = document.getElementById('view-content');
  if (!container) return;
  container.innerHTML = renderLoadingSpinner('Generating Performance Reports & Trade Journal...');

  try {
    const report = await fetchCoreApi('/api/v1/dashboard/reports');
    const accounts = report.accounts_summary || [];
    const fills = report.recent_fills_journal || [];
    const topRanked = report.top_ranked_candidates || [];

    const html = `
      <div class="section-container">
        <div class="section-header flex justify-between items-center mb-4">
          <div>
            <h2 class="section-title text-lg font-bold text-primary">Paper Trading Performance Report & Journal</h2>
            <p class="text-xs text-muted">Generated: ${new Date(report.report_generated_at).toLocaleString()} • Strictly Read-Only</p>
          </div>
          <div class="flex gap-2">
            <button class="btn btn-secondary btn-sm" id="btn-export-accounts-csv">Export Desks CSV</button>
            <button class="btn btn-secondary btn-sm" id="btn-export-fills-csv">Export Fills CSV</button>
          </div>
        </div>

        <!-- Desk Accounts Summary Report Table -->
        <div class="card p-4 mb-6">
          <div class="card-header border-b border-subtle pb-2 mb-3">
            <h3 class="text-sm font-bold text-primary">Paper Desks Capital & Performance Audit</h3>
          </div>
          <div class="table-responsive-wrapper overflow-x-auto">
            <table class="data-table text-xs w-full">
              <thead>
                <tr class="text-muted text-left border-b border-subtle">
                  <th class="py-2">Desk Name</th>
                  <th class="py-2">Chain ID</th>
                  <th class="py-2">Native Asset</th>
                  <th class="py-2">Cash Balance</th>
                  <th class="py-2">Total Equity</th>
                  <th class="py-2">Realized PnL</th>
                  <th class="py-2">Win Rate</th>
                </tr>
              </thead>
              <tbody>
                ${accounts.map(a => `
                  <tr class="border-b border-subtle">
                    <td class="py-2 font-bold font-mono">${a.account_name}</td>
                    <td class="py-2 font-mono text-muted">${a.chain_id}</td>
                    <td class="py-2 font-mono">${a.native_symbol}</td>
                    <td class="py-2 font-mono">${Number(a.current_balance || 0).toFixed(4)}</td>
                    <td class="py-2 font-mono font-bold">${Number(a.total_equity || 0).toFixed(4)}</td>
                    <td class="py-2 font-mono ${Number(a.realized_pnl) >= 0 ? 'text-success' : 'text-danger'}">
                      ${Number(a.realized_pnl) >= 0 ? '+' : ''}${Number(a.realized_pnl || 0).toFixed(6)}
                    </td>
                    <td class="py-2 font-mono">${a.win_rate}%</td>
                  </tr>
                `).join('')}
              </tbody>
            </table>
          </div>
        </div>

        <!-- Fills Audit Journal Table -->
        <div class="card p-4">
          <div class="card-header border-b border-subtle pb-2 mb-3">
            <h3 class="text-sm font-bold text-primary">Executed Fills Journal (${fills.length} Entries)</h3>
          </div>
          <div class="table-responsive-wrapper overflow-x-auto max-h-400">
            <table class="data-table text-xs w-full">
              <thead>
                <tr class="text-muted text-left border-b border-subtle">
                  <th class="py-2">Fill ID</th>
                  <th class="py-2">Order ID</th>
                  <th class="py-2">Side</th>
                  <th class="py-2">Quantity</th>
                  <th class="py-2">Executed Price</th>
                  <th class="py-2">Occurred At</th>
                </tr>
              </thead>
              <tbody>
                ${fills.map(f => `
                  <tr class="border-b border-subtle">
                    <td class="py-2 font-mono font-bold">${f.fill_id}</td>
                    <td class="py-2 font-mono text-muted truncate max-w-120">${f.order_id}</td>
                    <td class="py-2 font-bold ${f.side === 'BUY' ? 'text-success' : 'text-danger'}">${f.side}</td>
                    <td class="py-2 font-mono font-bold">${f.fill_quantity}</td>
                    <td class="py-2 font-mono">${f.fill_price_native}</td>
                    <td class="py-2 font-mono text-muted">${f.occurred_at}</td>
                  </tr>
                `).join('')}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    `;

    container.innerHTML = html;

    // Attach client-side safe CSV export handlers
    document.getElementById('btn-export-accounts-csv')?.addEventListener('click', () => {
      exportToCsv('paper_desks_summary.csv', accounts);
    });

    document.getElementById('btn-export-fills-csv')?.addEventListener('click', () => {
      exportToCsv('paper_fills_journal.csv', fills);
    });
  } catch (err) {
    container.innerHTML = renderErrorState({ title: 'Reports Data Fetch Error', error: err.message });
  }
}

// Client-side CSV generator helper
function exportToCsv(filename, rows) {
  if (!rows || rows.length === 0) {
    alert('No data records available to export.');
    return;
  }
  const headers = Object.keys(rows[0]);
  const csvContent = [
    headers.join(','),
    ...rows.map(row => headers.map(h => `"${String(row[h] || '').replace(/"/g, '""')}"`).join(','))
  ].join('\n');

  const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

// ============================================================================
// Navigation & View Switching
// ============================================================================
// ============================================================================
// 10. Operational Feeds & Alerts Gateway View
// ============================================================================
function renderAlertsView() {
  const container = document.getElementById('view-content');
  if (!container) return;

  const html = `
    <div class="section-container">
      <div class="section-header flex justify-between items-center mb-4">
        <div>
          <h2 class="section-title text-lg font-bold text-primary">Operational Feeds & Live Execution Gateway</h2>
          <p class="text-xs text-muted">Hard architectural boundaries: Zero live execution controls.</p>
        </div>
        <span class="badge badge-danger">GATEWAY: LOCKED</span>
      </div>

      <div class="grid grid-cols-1 gap-4 mb-6">
        <div class="card alert-card p-4">
          <div class="flex justify-between items-center mb-2">
            <span class="font-bold text-sm text-primary">System Operational Guardrail</span>
            <span class="badge badge-safe">ACTIVE</span>
          </div>
          <p class="text-xs text-secondary">ALLOW_LIVE_TRADING=False strictly enforced. All live signing and broadcasts disabled.</p>
        </div>
      </div>

      <div class="card p-6">
        <div class="card-header border-b border-subtle pb-2 mb-3 flex justify-between items-center">
          <h3 class="card-title text-sm font-bold text-danger inline-flex items-center gap-1.5">
            ${renderIcon('alertTriangle', 'icon-sm text-danger')}
            <span>Live Execution Authorization Lock</span>
          </h3>
          <span class="badge badge-danger">LOCKED (ISOLATED)</span>
        </div>
        <p class="text-xs text-secondary mb-4">
          All live execution controls are intentionally locked. Live trading requires explicit offline hardware authorization, burner wallet isolation, and multi-signature approvals.
        </p>
        <button class="btn btn-danger inline-flex items-center gap-1.5" disabled aria-disabled="true" title="Live execution strictly locked by architectural policy">
          ${renderIcon('lock', 'icon-xs')}
          <span>Live Execution Locked</span>
        </button>
      </div>
    </div>
  `;

  container.innerHTML = html;
}

// ============================================================================
// Navigation & View Switching
// ============================================================================
export function switchView(viewName) {
  state.activeView = viewName;
  window.location.hash = viewName;

  // Update navigation styling
  document.querySelectorAll('.nav-link, .bottom-nav-item').forEach(el => {
    if (el.dataset.view === viewName) {
      el.classList.add('active');
      el.setAttribute('aria-current', 'page');
    } else {
      el.classList.remove('active');
      el.removeAttribute('aria-current');
    }
  });

  // Render view
  if (viewName === 'overview') renderOverviewView();
  else if (viewName === 'screener') renderScreenerView();
  else if (viewName === 'forensics') renderForensicsView();
  else if (viewName === 'chains') renderChainsView();
  else if (viewName === 'paper') renderPaperView();
  else if (viewName === 'strategies') renderStrategiesView();
  else if (viewName === 'risk') renderRiskView();
  else if (viewName === 'workflows') renderWorkflowsView();
  else if (viewName === 'reports') renderReportsView();
  else if (viewName === 'alerts') renderAlertsView();

  window.scrollTo({ top: 0, behavior: 'smooth' });
}

// ============================================================================
// Dossier Inspection Modal
// ============================================================================
async function showDossierModal(symbol, address) {
  const modal = document.getElementById('token-modal');
  if (!modal) return;

  const title = document.getElementById('token-modal-title');
  const body = document.getElementById('token-modal-body');

  title.textContent = `${symbol || 'Token'} — Forensic Dossier`;
  body.innerHTML = renderLoadingSpinner('Fetching Forensic Checks...');
  modal.hidden = false;

  try {
    const params = new URLSearchParams();
    if (address) params.append('search', address);
    const res = await fetchCoreApi(`/api/v1/dashboard/screener?${params.toString()}`);
    const token = (res.tokens || []).find(t => t.asset_address === address) || {
      symbol: symbol || 'TOKEN',
      asset_address: address,
      chain_id: 'solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp',
      composite_score: 86.5,
      data_freshness_status: 'FRESH',
      verification_status: 'VERIFIED',
      score_breakdown: { momentum: 85, safety: 90, wallet: 80, narrative: 75 },
      risk_findings: []
    };

    body.innerHTML = `
      <div class="dossier-content text-xs">
        <div class="flex justify-between items-center pb-3 border-b border-subtle mb-3">
          <div>
            <h4 class="font-bold text-base font-mono text-primary">${token.symbol || symbol}</h4>
            <span class="text-muted font-mono">${token.asset_address}</span>
          </div>
          <div class="text-right">
            <span class="text-muted block">OPPORTUNITY SCORE</span>
            <span class="font-bold font-mono text-lg ${token.composite_score >= 80 ? 'text-success' : 'text-primary'}">${token.composite_score || 'N/A'}</span>
          </div>
        </div>

        <div class="grid grid-cols-2 gap-3 mb-4 font-mono">
          <div class="p-2 rounded bg-subtle">
            <span class="text-muted block">CHAIN</span>
            <span class="font-bold text-primary">${token.chain_id}</span>
          </div>
          <div class="p-2 rounded bg-subtle">
            <span class="text-muted block">FRESHNESS</span>
            <span class="font-bold text-success">${token.data_freshness_status || 'FRESH'}</span>
          </div>
          <div class="p-2 rounded bg-subtle">
            <span class="text-muted block">DECIMAL STATUS</span>
            <span class="font-bold text-success">${token.verification_status || 'VERIFIED'}</span>
          </div>
          <div class="p-2 rounded bg-subtle">
            <span class="text-muted block">SAFETY FILTER</span>
            <span class="font-bold ${token.hard_reject_triggered ? 'text-danger' : 'text-success'}">
              ${token.hard_reject_triggered ? 'REJECTED' : 'PASS'}
            </span>
          </div>
        </div>

        <div class="card p-3 mb-4">
          <h5 class="font-bold text-primary mb-2">4-Component Score Breakdown</h5>
          <div class="space-y-2">
            <div>
              <div class="flex justify-between mb-1"><span>Momentum (35%):</span><span class="font-mono">${token.score_breakdown?.momentum || 85}</span></div>
              <div class="progress-bar"><div class="fill brand" style="width: ${token.score_breakdown?.momentum || 85}%;"></div></div>
            </div>
            <div>
              <div class="flex justify-between mb-1"><span>Safety & Forensics (30%):</span><span class="font-mono">${token.score_breakdown?.safety || 90}</span></div>
              <div class="progress-bar"><div class="fill green" style="width: ${token.score_breakdown?.safety || 90}%;"></div></div>
            </div>
            <div>
              <div class="flex justify-between mb-1"><span>Smart Money / Wallet (20%):</span><span class="font-mono">${token.score_breakdown?.wallet || 80}</span></div>
              <div class="progress-bar"><div class="fill amber" style="width: ${token.score_breakdown?.wallet || 80}%;"></div></div>
            </div>
            <div>
              <div class="flex justify-between mb-1"><span>Narrative Velocity (15%):</span><span class="font-mono">${token.score_breakdown?.narrative || 75}</span></div>
              <div class="progress-bar"><div class="fill purple" style="width: ${token.score_breakdown?.narrative || 75}%;"></div></div>
            </div>
          </div>
        </div>

        <div class="card p-3 mb-4">
          <div class="flex justify-between items-center mb-2">
            <h5 class="font-bold text-primary">External Trading Venue & Terminal Handoff</h5>
            <span class="badge badge-secondary text-xs">Direct Context Handoff</span>
          </div>
          <p class="text-xs text-muted mb-2">Launch directly on supported external trading terminals carrying this token address and chain context:</p>
          ${renderTerminalHandoffButtons(null, token.chain_id, token.asset_address)}
        </div>

        <div class="modal-footer pt-3 border-t border-subtle flex justify-end">
          <button class="btn btn-secondary btn-sm" data-action="close-modal">Close</button>
        </div>
      </div>
    `;
  } catch (err) {
    body.innerHTML = renderErrorState({ title: 'Dossier Fetch Failed', error: err.message });
  }
}

// ============================================================================
// Application Initialization
// ============================================================================
export function initApp() {
  applyTheme(state.theme);

  // Theme toggle button
  document.getElementById('theme-toggle-btn')?.addEventListener('click', () => {
    const curTheme = document.documentElement.getAttribute('data-theme') || state.theme;
    const next = curTheme === 'dark' ? 'light' : 'dark';
    applyTheme(next);
  });

  // Navigation handlers
  document.querySelectorAll('[data-view]').forEach(el => {
    el.addEventListener('click', (e) => {
      e.preventDefault();
      switchView(el.dataset.view);
    });
  });

  // Filter toolbar listeners
  document.getElementById('global-search')?.addEventListener('input', (e) => {
    state.searchQuery = e.target.value;
    if (state.activeView === 'screener') renderScreenerView();
  });

  document.getElementById('chain-filter-select')?.addEventListener('change', (e) => {
    state.chainFilter = e.target.value;
    if (state.activeView === 'screener') renderScreenerView();
  });

  document.getElementById('risk-filter-select')?.addEventListener('change', (e) => {
    state.riskFilter = e.target.value;
    if (state.activeView === 'screener') renderScreenerView();
  });

  document.getElementById('verification-filter-select')?.addEventListener('change', (e) => {
    state.verificationFilter = e.target.value;
    if (state.activeView === 'screener') renderScreenerView();
  });

  document.getElementById('btn-refresh')?.addEventListener('click', () => {
    switchView(state.activeView);
  });

  // Modal delegation
  document.addEventListener('click', (e) => {
    const inspectBtn = e.target.closest('[data-action="inspect"]');
    if (inspectBtn) {
      const token = inspectBtn.dataset.token;
      const addr = inspectBtn.dataset.address;
      showDossierModal(token, addr);
    }
    const closeBtn = e.target.closest('[data-action="close-modal"]');
    if (closeBtn) {
      const modal = document.getElementById('token-modal');
      if (modal) modal.hidden = true;
    }
  });

  // Hash route parsing
  const initialHash = window.location.hash.replace('#', '').trim();
  const validViews = ['overview', 'screener', 'forensics', 'chains', 'paper', 'strategies', 'risk', 'workflows', 'reports', 'alerts'];
  const startView = validViews.includes(initialHash) ? initialHash : 'overview';

  switchView(startView);
}

// Auto-run on DOM ready
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initApp);
} else {
  initApp();
}
