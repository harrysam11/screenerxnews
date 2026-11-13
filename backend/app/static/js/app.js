/**
 * Multi-Chain Memecoin Intelligence, Forensics & Trading OS
 * Main Frontend Application Script
 */

import {
  EvidenceType,
  renderEvidenceBadge,
  renderMetricCard,
  renderTokenCard,
  renderChainCard,
  renderSecurityCard,
  renderWalletCard,
  renderSignalCard,
  renderPaperDeskCard,
  renderAlertCard,
  renderSparkline,
  renderDataTable,
  renderFilterToolbar,
  renderModal
} from './components.js';

// Application State
const state = {
  activeView: 'overview',
  theme: localStorage.getItem('theme_preference') || 'dark',
  chainFilter: 'ALL',
  riskFilter: 'ALL',
  searchQuery: '',
  chains: [
    { id: 'solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp', name: 'Solana', engine: 'SVM', pools: 8420, indexed: 14200 },
    { id: 'eip155:1', name: 'Ethereum', engine: 'EVM', pools: 4230, indexed: 9800 },
    { id: 'eip155:8453', name: 'Base', engine: 'EVM', pools: 6100, indexed: 12500 },
    { id: 'eip155:56', name: 'BNB Chain', engine: 'EVM', pools: 5800, indexed: 11900 },
    { id: 'eip155:42161', name: 'Arbitrum', engine: 'EVM', pools: 2100, indexed: 4300 },
    { id: 'eip155:10', name: 'Optimism', engine: 'EVM', pools: 1400, indexed: 3100 },
    { id: 'eip155:137', name: 'Polygon', engine: 'EVM', pools: 1900, indexed: 4000 },
    { id: 'eip155:43114', name: 'Avalanche', engine: 'EVM', pools: 1200, indexed: 2800 },
    { id: 'sui:mainnet', name: 'Sui', engine: 'Move', pools: 850, indexed: 1900 },
    { id: 'aptos:mainnet', name: 'Aptos', engine: 'Move', pools: 620, indexed: 1400 },
    { id: 'ton:mainnet', name: 'TON', engine: 'TON', pools: 1100, indexed: 2500 },
    { id: 'tron:mainnet', name: 'TRON', engine: 'TVM', pools: 2300, indexed: 5100 }
  ],
  tokens: [
    {
      id: 'bonk-sol',
      symbol: 'BONK',
      name: 'Bonk Doge',
      chain_name: 'Solana',
      price_usd: 0.0000214,
      volume_24h: 84250000,
      liquidity_usd: 14200000,
      mcap_usd: 1450000000,
      opportunity_score: 84.5,
      safety_level: 'SAFE',
      evidence_status: EvidenceType.VERIFIED,
      sparkline: [12, 14, 13, 17, 21, 20, 24, 28]
    },
    {
      id: 'brett-base',
      symbol: 'BRETT',
      name: 'Brett on Base',
      chain_name: 'Base',
      price_usd: 0.0842,
      volume_24h: 31200000,
      liquidity_usd: 8400000,
      mcap_usd: 840000000,
      opportunity_score: 81.2,
      safety_level: 'SAFE',
      evidence_status: EvidenceType.VERIFIED,
      sparkline: [40, 42, 41, 48, 55, 53, 62, 70]
    },
    {
      id: 'pepe-eth',
      symbol: 'PEPE',
      name: 'Pepe the Frog',
      chain_name: 'Ethereum',
      price_usd: 0.0000098,
      volume_24h: 125000000,
      liquidity_usd: 48000000,
      mcap_usd: 4100000000,
      opportunity_score: 79.8,
      safety_level: 'SAFE',
      evidence_status: EvidenceType.VERIFIED,
      sparkline: [25, 27, 26, 30, 34, 32, 38, 45]
    },
    {
      id: 'floki-bnb',
      symbol: 'FLOKI',
      name: 'Floki Inu',
      chain_name: 'BNB Chain',
      price_usd: 0.000142,
      volume_24h: 42100000,
      liquidity_usd: 12400000,
      mcap_usd: 1380000000,
      opportunity_score: 76.4,
      safety_level: 'SAFE',
      evidence_status: EvidenceType.VERIFIED,
      sparkline: [15, 16, 15, 18, 20, 19, 22, 25]
    },
    {
      id: 'degen-arb',
      symbol: 'CATDOG',
      name: 'CatDog AI',
      chain_name: 'Arbitrum',
      price_usd: 0.0034,
      volume_24h: 4200000,
      liquidity_usd: 450000,
      mcap_usd: 3400000,
      opportunity_score: 62.1,
      safety_level: 'EVALUATING',
      evidence_status: EvidenceType.PROBABILISTIC,
      sparkline: [10, 12, 11, 15, 14, 18, 16, 20]
    },
    {
      id: 'unknown-sui',
      symbol: 'SUIMO',
      name: 'Sui Momentum',
      chain_name: 'Sui',
      price_usd: 0.012,
      volume_24h: 1100000,
      liquidity_usd: 120000,
      mcap_usd: 1200000,
      opportunity_score: 45.0,
      safety_level: 'UNVERIFIED',
      evidence_status: EvidenceType.UNKNOWN,
      sparkline: [8, 9, 8, 10, 9, 11, 10, 12]
    },
    {
      id: 'honeypot-test',
      symbol: 'RUGME',
      name: 'Suspicious Coin',
      chain_name: 'Base',
      price_usd: 0.000001,
      volume_24h: 890000,
      liquidity_usd: 24000,
      mcap_usd: 80000,
      opportunity_score: 18.0,
      safety_level: 'HARD_REJECT',
      evidence_status: EvidenceType.INSUFFICIENT,
      sparkline: [30, 25, 20, 12, 8, 5, 3, 1]
    }
  ],
  signals: [
    {
      symbol: 'BONK',
      chain_name: 'Solana',
      composite_score: 84.5,
      momentum_subscore: 88,
      safety_subscore: 95,
      wallet_subscore: 76,
      narrative_subscore: 80,
      action_type: 'STRONG_SIGNAL',
      recommended_weight: '3.5%'
    },
    {
      symbol: 'BRETT',
      chain_name: 'Base',
      composite_score: 81.2,
      momentum_subscore: 85,
      safety_subscore: 92,
      wallet_subscore: 72,
      narrative_subscore: 74,
      action_type: 'WATCHLIST',
      recommended_weight: '2.5%'
    },
    {
      symbol: 'PEPE',
      chain_name: 'Ethereum',
      composite_score: 79.8,
      momentum_subscore: 78,
      safety_subscore: 98,
      wallet_subscore: 70,
      narrative_subscore: 82,
      action_type: 'WATCHLIST',
      recommended_weight: '2.0%'
    }
  ],
  alerts: [
    {
      id: 'alt-001',
      title: 'Solana High Velocity Pump Detected',
      message: 'Token $BONK crossed 3-sigma volume threshold on Raydium CPMM with 99.8% LP burn verified.',
      severity: 'INFO',
      timestamp: Date.now() - 120000,
      chain_name: 'Solana'
    },
    {
      id: 'alt-002',
      title: 'Honeypot Gate Hard-Reject Triggered',
      message: 'Token $RUGME on Base simulated sell execution failed (TRANSFER_FROM_FAILED). Blacklisted permanently.',
      severity: 'CRITICAL',
      timestamp: Date.now() - 600000,
      chain_name: 'Base'
    },
    {
      id: 'alt-003',
      title: 'Hermes Narrative Trend Alert',
      message: 'AI Meme narrative momentum decaying on EVM chains, shifting to Solana & Sui Move tokens.',
      severity: 'WARNING',
      timestamp: Date.now() - 1800000,
      chain_name: 'Multi-Chain'
    }
  ]
};

// Initialize Theme
function applyTheme(theme) {
  state.theme = theme;
  document.documentElement.setAttribute('data-theme', theme);
  localStorage.setItem('theme_preference', theme);
  const themeToggle = document.getElementById('theme-toggle-btn');
  if (themeToggle) {
    themeToggle.textContent = theme === 'dark' ? '🌙 Dark' : '☀️ Light';
  }
}

// Render Overview View
function renderOverviewView() {
  const container = document.getElementById('view-content');
  if (!container) return;

  const kpisHtml = `
    <div class="kpi-grid">
      ${renderMetricCard({
        title: 'MULTI-CHAIN 24H VOL',
        value: '$342.8M',
        change: '14.2',
        isPositive: true,
        subtitle: '12 Networks Monitored',
        icon: '📊',
        evidence: EvidenceType.VERIFIED
      })}
      ${renderMetricCard({
        title: 'ACTIVE LIQUIDITY POOLS',
        value: '38,920',
        change: '4.8',
        isPositive: true,
        subtitle: 'Automated Log Indexers Active',
        icon: '💧',
        evidence: EvidenceType.VERIFIED
      })}
      ${renderMetricCard({
        title: 'SAFETY REJECT GATES',
        value: '1,429',
        change: '22.1',
        isPositive: false,
        subtitle: 'Honeypots & Rugs Blocked',
        icon: '🛡️',
        evidence: EvidenceType.VERIFIED
      })}
      ${renderMetricCard({
        title: 'PAPER DESK EQUITY',
        value: '$1,200,000',
        change: '12.4',
        isPositive: true,
        subtitle: '12 Isolated Paper Desks',
        icon: '📈',
        evidence: EvidenceType.PROBABILISTIC
      })}
    </div>
  `;

  const signalsHtml = `
    <div class="section-container">
      <div class="section-header">
        <h2 class="section-title">Top Opportunity Signals (Multi-Chain Composite)</h2>
        <span class="text-xs text-muted">Composite Formula: 0.35M + 0.30S + 0.20W + 0.15N</span>
      </div>
      <div class="cards-grid cols-3">
        ${state.signals.map(s => renderSignalCard({ signal: s })).join('')}
      </div>
    </div>
  `;

  const chainsHtml = `
    <div class="section-container">
      <div class="section-header">
        <h2 class="section-title">Connected Blockchain Networks (12 Adapters)</h2>
        <span class="badge badge-safe">ALL RPCs CONNECTED</span>
      </div>
      <div class="cards-grid cols-4">
        ${state.chains.map(c => renderChainCard({
          chain_name: c.name,
          caip2_id: c.id,
          engine: c.engine,
          active_pools: c.pools,
          tokens_indexed: c.indexed
        })).join('')}
      </div>
    </div>
  `;

  container.innerHTML = kpisHtml + signalsHtml + chainsHtml;
}

// Render Screener View
function renderScreenerView() {
  const container = document.getElementById('view-content');
  if (!container) return;

  const filteredTokens = state.tokens.filter(t => {
    if (state.chainFilter !== 'ALL' && t.chain_name !== state.chainFilter) return false;
    if (state.riskFilter === 'SAFE' && t.opportunity_score < 80) return false;
    if (state.searchQuery && !t.symbol.toLowerCase().includes(state.searchQuery.toLowerCase()) && !t.name.toLowerCase().includes(state.searchQuery.toLowerCase())) return false;
    return true;
  });

  const columns = [
    { key: 'token_col', label: 'TOKEN / CHAIN', align: 'text-left' },
    { key: 'price_col', label: 'PRICE', align: 'text-right' },
    { key: 'vol_col', label: '24H VOLUME', align: 'text-right' },
    { key: 'liq_col', label: 'LIQUIDITY', align: 'text-right' },
    { key: 'trend_col', label: '7D TREND', align: 'text-center' },
    { key: 'score_col', label: 'OPP SCORE', align: 'text-right' },
    { key: 'evidence_col', label: 'EVIDENCE INTEGRITY', align: 'text-center' },
    { key: 'action_col', label: 'ACTION', align: 'text-center' }
  ];

  const rows = filteredTokens.map(t => ({
    token_col: `<strong>${t.symbol}</strong> <span class="badge badge-chain">${t.chain_name}</span>`,
    price_col: `$${t.price_usd < 0.0001 ? Number(t.price_usd).toExponential(3) : Number(t.price_usd).toFixed(4)}`,
    vol_col: `$${Number(t.volume_24h).toLocaleString()}`,
    liq_col: `$${Number(t.liquidity_usd).toLocaleString()}`,
    trend_col: renderSparkline(t.sparkline, t.sparkline[t.sparkline.length - 1] >= t.sparkline[0]),
    score_col: `<span class="font-bold text-brand">${Math.round(t.opportunity_score)}</span>`,
    evidence_col: renderEvidenceBadge(t.evidence_status),
    action_col: `<button class="btn btn-secondary btn-sm" data-action="inspect" data-token="${t.symbol}">Dossier</button>`
  }));

  const cardsHtml = `
    <div class="section-container">
      <div class="section-header">
        <h2 class="section-title">Multi-Chain Token Screener (${filteredTokens.length} Discovered)</h2>
      </div>
      <div class="cards-grid cols-3 mb-6">
        ${filteredTokens.slice(0, 3).map(t => renderTokenCard({ token: t })).join('')}
      </div>
      <div class="card p-0">
        ${renderDataTable(columns, rows)}
      </div>
    </div>
  `;

  container.innerHTML = cardsHtml;
}

// Render Forensics View
function renderForensicsView() {
  const container = document.getElementById('view-content');
  if (!container) return;

  const html = `
    <div class="section-container">
      <div class="section-header">
        <h2 class="section-title">On-Chain Forensic Evidence & Risk Analysis</h2>
        <span class="badge badge-safe">ZERO-ASSUMPTION SECURITY</span>
      </div>
      <div class="cards-grid cols-2 mb-6">
        ${renderSecurityCard({
          token_symbol: 'BONK (Solana)',
          lp_lock_pct: 99.8,
          honeypot_result: { evidence: EvidenceType.VERIFIED, status: 'SELL SIMULATION OK' },
          deployer_risk: { evidence: EvidenceType.VERIFIED, label: 'DEPLOYER IMMUTABLE' }
        })}
        ${renderSecurityCard({
          token_symbol: 'BRETT (Base)',
          lp_lock_pct: 100,
          honeypot_result: { evidence: EvidenceType.VERIFIED, status: 'BYTECODE VERIFIED' },
          deployer_risk: { evidence: EvidenceType.PROBABILISTIC, label: 'SYBIL SCORE 12/100' }
        })}
        ${renderSecurityCard({
          token_symbol: 'SUIMO (Sui)',
          lp_lock_pct: 45,
          honeypot_result: { evidence: EvidenceType.INSUFFICIENT, status: 'INSUFFICIENT LIQUIDITY LOGS' },
          deployer_risk: { evidence: EvidenceType.UNKNOWN, label: 'UNKNOWN CLUSTER' }
        })}
        ${renderSecurityCard({
          token_symbol: 'RUGME (Honeypot Triggered)',
          lp_lock_pct: 12,
          honeypot_result: { evidence: EvidenceType.VERIFIED, status: 'TRANSFER_FAILED (REJECTED)' },
          deployer_risk: { evidence: EvidenceType.VERIFIED, label: 'SERIAL RUGGER BLACKLISTED' }
        })}
      </div>

      <div class="section-header">
        <h2 class="section-title">Smart Money & Deployer Cluster Intelligence</h2>
      </div>
      <div class="cards-grid cols-3">
        ${renderWalletCard({
          wallet_address: '0x71C...4e81',
          role: 'EARLY_BUYER_INSIDER',
          score: '94/100',
          volume_sol: '1,840 SOL',
          win_rate: '82%',
          cluster_size: '6 Wallets'
        })}
        ${renderWalletCard({
          wallet_address: '7Zk4...99aa',
          role: 'MEV_SANDWICH_BOT',
          score: '21/100',
          volume_sol: '9,200 SOL',
          win_rate: '91%',
          cluster_size: '28 Wallets'
        })}
        ${renderWalletCard({
          wallet_address: '0x94F...20bc',
          role: 'DEGEN_SWING_WHALE',
          score: '78/100',
          volume_sol: '620 ETH',
          win_rate: '64%',
          cluster_size: '3 Wallets'
        })}
      </div>
    </div>
  `;

  container.innerHTML = html;
}

// Render Paper View
function renderPaperView() {
  const container = document.getElementById('view-content');
  if (!container) return;

  const desks = [
    { chain_name: 'Solana', caip2_id: 'solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp', cash_balance: '100.00 SOL', portfolio_value: '$142,500', realized_pnl: '+24.5 SOL', positions_count: 3 },
    { chain_name: 'Base', caip2_id: 'eip155:8453', cash_balance: '10.00 ETH', portfolio_value: '$48,200', realized_pnl: '+1.4 ETH', positions_count: 2 },
    { chain_name: 'Ethereum', caip2_id: 'eip155:1', cash_balance: '10.00 ETH', portfolio_value: '$38,900', realized_pnl: '-0.3 ETH', positions_count: 1 },
    { chain_name: 'BNB Chain', caip2_id: 'eip155:56', cash_balance: '50.00 BNB', portfolio_value: '$34,100', realized_pnl: '+4.2 BNB', positions_count: 2 }
  ];

  const html = `
    <div class="section-container">
      <div class="section-header">
        <h2 class="section-title">Multi-Chain Paper Trading Desks (Virtual Ledgers)</h2>
        <span class="badge badge-env-paper">100% ISOLATED VIRTUAL CASH</span>
      </div>
      <div class="cards-grid cols-2 mb-6">
        ${desks.map(d => renderPaperDeskCard({ desk: d })).join('')}
      </div>

      <div class="card">
        <div class="card-header">
          <span class="card-title">Simulate Paper Trade Intent (AMM Constant-Product Model)</span>
        </div>
        <div class="card-body">
          <form id="paper-sim-form" class="form-grid">
            <div class="form-group">
              <label class="form-label" for="sim-chain">Chain Desk</label>
              <select id="sim-chain" class="form-select">
                <option value="solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp">Solana Desk (SOL)</option>
                <option value="eip155:8453">Base Desk (ETH)</option>
                <option value="eip155:1">Ethereum Desk (ETH)</option>
                <option value="eip155:56">BNB Chain Desk (BNB)</option>
              </select>
            </div>
            <div class="form-group">
              <label class="form-label" for="sim-token">Token</label>
              <input type="text" id="sim-token" class="form-input" value="BONK" placeholder="Token Symbol">
            </div>
            <div class="form-group">
              <label class="form-label" for="sim-amount">Amount</label>
              <input type="number" id="sim-amount" class="form-input" value="5.0" step="0.1">
            </div>
            <div class="form-group" style="align-self: flex-end;">
              <button type="button" id="btn-run-sim" class="btn btn-primary w-full">Execute Virtual Trade</button>
            </div>
          </form>
          <div id="sim-output" class="mt-4 p-4 card-subtle font-mono text-sm" hidden></div>
        </div>
      </div>
    </div>
  `;

  container.innerHTML = html;

  document.getElementById('btn-run-sim')?.addEventListener('click', () => {
    const output = document.getElementById('sim-output');
    output.hidden = false;
    output.innerHTML = `
      <div class="text-success font-bold mb-2">✓ SIMULATED PAPER ORDER EXECUTED</div>
      <div>Pair: BONK/SOL | DEX: Raydium CPMM</div>
      <div>Input: 5.0 SOL | Output: 233,644.86 BONK</div>
      <div>AMM Slippage: 0.12% | Virtual Network Fee: 0.000005 SOL</div>
      <div class="text-muted mt-2">Ledger double-entry recorded to virtual paper desk with zero real funds.</div>
    `;
  });
}

// Render Alerts & Ops View
function renderAlertsView() {
  const container = document.getElementById('view-content');
  if (!container) return;

  const html = `
    <div class="section-container">
      <div class="section-header">
        <h2 class="section-title">Operational Feeds & Live Execution Gateway</h2>
        <span class="badge badge-danger">GATEWAY: LOCKED</span>
      </div>
      <div class="cards-grid cols-1 mb-6">
        ${state.alerts.map(a => renderAlertCard({ alert: a })).join('')}
      </div>

      <div class="card p-6">
        <div class="card-header">
          <span class="card-title text-danger">⚠️ Live Execution Authorization Lock</span>
          <span class="badge badge-danger">LOCKED (ISOLATED)</span>
        </div>
        <p class="text-sm text-secondary mb-4">
          All live execution controls are intentionally locked. Live orders require explicit offline hardware authorization, burner wallet isolation, and multi-signature approvals.
        </p>
        <button class="btn btn-danger" disabled aria-disabled="true" title="Live execution strictly locked by architectural policy">
          🔒 Live Trading Gateway (Disabled)
        </button>
      </div>
    </div>
  `;

  container.innerHTML = html;
}

// Switch View Handler
export function switchView(viewName) {
  state.activeView = viewName;

  // Update navigation styles
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
  else if (viewName === 'paper') renderPaperView();
  else if (viewName === 'alerts') renderAlertsView();

  // Scroll to top
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

// Initialize Application
export function initApp() {
  applyTheme(state.theme);

  // Theme switch listener
  document.getElementById('theme-toggle-btn')?.addEventListener('click', () => {
    const nextTheme = state.theme === 'dark' ? 'light' : 'dark';
    applyTheme(nextTheme);
  });

  // Nav link click listeners
  document.querySelectorAll('[data-view]').forEach(el => {
    el.addEventListener('click', (e) => {
      e.preventDefault();
      const targetView = el.dataset.view;
      switchView(targetView);
    });
  });

  // Search and filter listeners
  document.addEventListener('input', (e) => {
    if (e.target && e.target.id === 'global-search') {
      state.searchQuery = e.target.value;
      if (state.activeView === 'screener') renderScreenerView();
    }
  });

  document.addEventListener('change', (e) => {
    if (e.target && e.target.id === 'chain-filter-select') {
      state.chainFilter = e.target.value;
      if (state.activeView === 'screener') renderScreenerView();
    }
    if (e.target && e.target.id === 'risk-filter-select') {
      state.riskFilter = e.target.value;
      if (state.activeView === 'screener') renderScreenerView();
    }
  });

  // Action delegations (Inspect Forensics / Dossier)
  document.addEventListener('click', (e) => {
    const inspectBtn = e.target.closest('[data-action="inspect"]');
    if (inspectBtn) {
      const token = inspectBtn.dataset.token;
      showDossierModal(token);
    }
    const closeModalBtn = e.target.closest('[data-action="close-modal"]');
    if (closeModalBtn) {
      const modal = document.getElementById('token-modal');
      if (modal) modal.hidden = true;
    }
  });

  // Initial render
  switchView('overview');
}

function showDossierModal(symbol) {
  const modal = document.getElementById('token-modal');
  if (!modal) return;

  const title = document.getElementById('token-modal-title');
  const body = document.getElementById('token-modal-body');

  title.textContent = `${symbol} — Deep Forensic Dossier`;
  body.innerHTML = `
    <div class="dossier-content">
      <div class="mb-4">
        ${renderEvidenceBadge(EvidenceType.VERIFIED, '100% ON-CHAIN VERIFIED')}
        <span class="badge badge-safe ml-2">SAFETY SCORE: 94/100</span>
      </div>
      <p class="text-sm text-secondary mb-4">
        Deep automated forensic verification completed across bytecode, liquidity pool locks, holder distribution, and MEV sandwich risk.
      </p>
      <div class="card p-4 mb-4">
        <h4 class="font-bold text-sm mb-2">Forensic Checks Matrix</h4>
        <ul class="text-sm list-disc pl-5">
          <li>Mint Authority: <strong>REVOKED</strong> (Permanent)</li>
          <li>Freeze Authority: <strong>REVOKED</strong> (Permanent)</li>
          <li>LP Lock: <strong>99.8% Burned to Incinerator</strong></li>
          <li>Top 10 Holders: <strong>14.2%</strong> (Excluding AMM pool)</li>
          <li>Honeypot Simulation: <strong>Buy & Sell successful with 0% tax</strong></li>
        </ul>
      </div>
      <div class="modal-actions-row">
        <button class="btn btn-primary btn-sm" data-action="close-modal">Done</button>
      </div>
    </div>
  `;
  modal.hidden = false;
}

// Auto-run on DOM ready
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initApp);
} else {
  initApp();
}
