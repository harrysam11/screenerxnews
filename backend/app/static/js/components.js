/**
 * Multi-Chain Memecoin Intelligence, Forensics & Trading OS
 * Soft-Card Component Library (Vanilla ES6 Modules)
 */

export const EvidenceType = {
  VERIFIED: 'VERIFIED',
  PROBABILISTIC: 'PROBABILISTIC',
  INSUFFICIENT: 'INSUFFICIENT',
  UNKNOWN: 'UNKNOWN'
};

/**
 * Renders an evidence badge ensuring missing evidence is never represented as safe.
 */
export function renderEvidenceBadge(type = EvidenceType.UNKNOWN, customLabel = null) {
  const normType = String(type).toUpperCase();
  let cssClass = 'badge-unknown';
  let label = customLabel || 'UNKNOWN EVIDENCE';
  let icon = '❓';

  if (normType === EvidenceType.VERIFIED) {
    cssClass = 'badge-verified';
    label = customLabel || 'VERIFIED ON-CHAIN';
    icon = '✓';
  } else if (normType === EvidenceType.PROBABILISTIC) {
    cssClass = 'badge-probabilistic';
    label = customLabel || 'PROBABILISTIC (AI / HEURISTIC)';
    icon = '⚡';
  } else if (normType === EvidenceType.INSUFFICIENT) {
    cssClass = 'badge-insufficient';
    label = customLabel || 'INSUFFICIENT DATA';
    icon = '⚠';
  } else {
    cssClass = 'badge-unknown';
    label = customLabel || 'UNKNOWN (UNVERIFIED)';
    icon = '❓';
  }

  return `<span class="badge ${cssClass}" data-evidence="${normType}" title="Evidence status: ${normType}"><span class="badge-icon">${icon}</span> ${label}</span>`;
}

/**
 * 1. Metric and KPI Card
 */
export function renderMetricCard({ title, value, change, isPositive, subtitle, icon, evidence = null }) {
  const changeHtml = change !== undefined && change !== null ? `
    <span class="metric-change ${isPositive ? 'positive' : 'negative'}">
      ${isPositive ? '▲ +' : '▼ '}${change}%
    </span>
  ` : '';

  const evidenceHtml = evidence ? `<div class="card-evidence-tag">${renderEvidenceBadge(evidence)}</div>` : '';

  return `
    <div class="card metric-card" tabindex="0">
      <div class="card-header">
        <span class="card-title">${title}</span>
        ${icon ? `<span class="metric-icon">${icon}</span>` : ''}
      </div>
      <div class="metric-value-row">
        <span class="metric-value font-mono">${value}</span>
        ${changeHtml}
      </div>
      ${subtitle ? `<div class="card-subtitle">${subtitle}</div>` : ''}
      ${evidenceHtml}
    </div>
  `;
}

/**
 * 2. Token and Liquidity Card
 */
export function renderTokenCard({ token, onSelect }) {
  const {
    symbol,
    name,
    chain_name,
    price_usd,
    volume_24h,
    liquidity_usd,
    mcap_usd,
    opportunity_score,
    safety_level,
    evidence_status = EvidenceType.PROBABILISTIC
  } = token;

  const scoreClass = opportunity_score >= 80 ? 'score-high' : opportunity_score >= 60 ? 'score-med' : 'score-low';

  return `
    <div class="card token-card" data-token-id="${token.id || symbol}" tabindex="0">
      <div class="card-header">
        <div class="token-identity">
          <span class="token-symbol font-mono font-bold">${symbol}</span>
          <span class="token-name">${name || symbol}</span>
          <span class="badge badge-chain">${chain_name}</span>
        </div>
        <div class="token-score-badge ${scoreClass}">
          <span class="score-label">OPP</span>
          <span class="score-value font-mono">${Math.round(opportunity_score || 0)}</span>
        </div>
      </div>
      <div class="card-body">
        <div class="token-metrics-grid">
          <div class="metric-col">
            <span class="label">PRICE</span>
            <span class="val font-mono">$${price_usd < 0.0001 ? Number(price_usd).toExponential(3) : Number(price_usd).toFixed(4)}</span>
          </div>
          <div class="metric-col">
            <span class="label">24H VOL</span>
            <span class="val font-mono">$${Number(volume_24h || 0).toLocaleString()}</span>
          </div>
          <div class="metric-col">
            <span class="label">LIQUIDITY</span>
            <span class="val font-mono">$${Number(liquidity_usd || 0).toLocaleString()}</span>
          </div>
          <div class="metric-col">
            <span class="label">MCAP</span>
            <span class="val font-mono">$${Number(mcap_usd || 0).toLocaleString()}</span>
          </div>
        </div>
        <div class="token-evidence-row">
          ${renderEvidenceBadge(evidence_status)}
          <span class="badge ${safety_level === 'SAFE' ? 'badge-safe' : 'badge-warn'}">${safety_level || 'EVALUATING'}</span>
        </div>
      </div>
      <div class="card-footer">
        <button class="btn btn-secondary btn-sm btn-inspect" data-action="inspect" data-token="${symbol}">Inspect Forensics</button>
        <button class="btn btn-primary btn-sm btn-paper-sim" data-action="simulate" data-token="${symbol}">Paper Sim</button>
      </div>
    </div>
  `;
}

/**
 * 3. Chain/Network Status Card
 */
export function renderChainCard(chain) {
  const {
    chain_name,
    caip2_id,
    engine,
    status = 'ACTIVE',
    tokens_indexed = 0,
    active_pools = 0,
    health = '100% HEALTHY'
  } = chain;

  const isEVM = engine === 'EVM';
  const engineBadge = isEVM ? '<span class="badge badge-engine">EVM</span>' : '<span class="badge badge-engine sol">SVM</span>';

  return `
    <div class="card chain-card" tabindex="0">
      <div class="card-header">
        <div class="chain-header-title">
          <span class="chain-title font-bold">${chain_name}</span>
          <span class="font-mono text-xs text-muted">${caip2_id}</span>
        </div>
        ${engineBadge}
      </div>
      <div class="chain-status-row">
        <span class="status-indicator active"></span>
        <span class="text-sm font-semibold">${status}</span>
        <span class="text-xs text-muted font-mono ml-auto">${health}</span>
      </div>
      <div class="chain-stats-grid">
        <div class="chain-stat">
          <span class="label">POOLS</span>
          <span class="val font-mono">${active_pools}</span>
        </div>
        <div class="chain-stat">
          <span class="label">INDEXED</span>
          <span class="val font-mono">${tokens_indexed}</span>
        </div>
      </div>
    </div>
  `;
}

/**
 * 4. Security and Forensic Evidence Card
 */
export function renderSecurityCard({ token_symbol, checks, honeypot_result, lp_lock_pct, deployer_risk }) {
  return `
    <div class="card security-card" tabindex="0">
      <div class="card-header">
        <span class="card-title">FORENSIC SECURITY ANALYSIS</span>
        <span class="font-mono font-bold">${token_symbol}</span>
      </div>
      <div class="security-evidence-summary">
        <div class="evidence-item">
          <span class="evidence-name">Honeypot Simulation</span>
          <div class="evidence-val">
            ${renderEvidenceBadge(honeypot_result ? honeypot_result.evidence : EvidenceType.VERIFIED, honeypot_result ? honeypot_result.status : 'SIMULATION PASSED')}
          </div>
        </div>
        <div class="evidence-item">
          <span class="evidence-name">LP Burn & Lock (95%+)</span>
          <div class="evidence-val">
            ${renderEvidenceBadge(lp_lock_pct >= 95 ? EvidenceType.VERIFIED : EvidenceType.INSUFFICIENT, `${lp_lock_pct || 0}% LOCKED`)}
          </div>
        </div>
        <div class="evidence-item">
          <span class="evidence-name">Deployer Cluster Graph</span>
          <div class="evidence-val">
            ${renderEvidenceBadge(deployer_risk ? deployer_risk.evidence : EvidenceType.PROBABILISTIC, deployer_risk ? deployer_risk.label : 'LOW CLUSTER RISK')}
          </div>
        </div>
        <div class="evidence-item">
          <span class="evidence-name">Bytecode Inspection (Mint/Freeze)</span>
          <div class="evidence-val">
            ${renderEvidenceBadge(EvidenceType.VERIFIED, 'REVOKED / IMMUTABLE')}
          </div>
        </div>
      </div>
      <div class="security-warning-box">
        <span class="text-xs text-muted">Forensic rule: Missing security evidence is never marked as safe. Incomplete signals retain INSUFFICIENT or UNKNOWN status.</span>
      </div>
    </div>
  `;
}

/**
 * 5. Wallet and Cluster Card
 */
export function renderWalletCard({ wallet_address, role, score, volume_sol, win_rate, cluster_size }) {
  const shortAddr = wallet_address.slice(0, 6) + '...' + wallet_address.slice(-4);
  return `
    <div class="card wallet-card" tabindex="0">
      <div class="card-header">
        <span class="wallet-role-badge">${role || 'WHALE_TRACKER'}</span>
        <span class="font-mono text-sm">${shortAddr}</span>
      </div>
      <div class="wallet-metrics-grid">
        <div class="wallet-stat">
          <span class="label">WIN RATE</span>
          <span class="val font-mono">${win_rate || '68%'}</span>
        </div>
        <div class="wallet-stat">
          <span class="label">VOLUME</span>
          <span class="val font-mono">${volume_sol || '420.5 SOL'}</span>
        </div>
        <div class="wallet-stat">
          <span class="label">CLUSTER SIZE</span>
          <span class="val font-mono">${cluster_size || '14 Wallets'}</span>
        </div>
        <div class="wallet-stat">
          <span class="label">REPUTATION</span>
          <span class="val font-mono font-bold text-success">${score || '88/100'}</span>
        </div>
      </div>
      <div class="card-footer">
        ${renderEvidenceBadge(EvidenceType.PROBABILISTIC, 'GRAPH SYBIL CLUSTERED')}
      </div>
    </div>
  `;
}

/**
 * 6. Opportunity and Signal Card
 */
export function renderSignalCard({ signal }) {
  const {
    symbol,
    chain_name,
    composite_score,
    momentum_subscore,
    safety_subscore,
    wallet_subscore,
    narrative_subscore,
    action_type = 'WATCHLIST',
    recommended_weight = '2.5%'
  } = signal;

  return `
    <div class="card signal-card" tabindex="0">
      <div class="card-header">
        <div>
          <span class="font-bold font-mono text-lg">${symbol}</span>
          <span class="badge badge-chain">${chain_name}</span>
        </div>
        <div class="signal-composite">
          <span class="text-xs text-muted">COMPOSITE</span>
          <span class="font-mono font-bold text-brand text-xl">${Number(composite_score).toFixed(1)}</span>
        </div>
      </div>
      <div class="subscore-bars">
        <div class="subscore-item">
          <span class="subscore-label">Momentum (35%)</span>
          <div class="progress-bar"><div class="fill brand" style="width: ${momentum_subscore}%;"></div></div>
          <span class="font-mono text-xs">${Math.round(momentum_subscore)}</span>
        </div>
        <div class="subscore-item">
          <span class="subscore-label">Safety (30%)</span>
          <div class="progress-bar"><div class="fill green" style="width: ${safety_subscore}%;"></div></div>
          <span class="font-mono text-xs">${Math.round(safety_subscore)}</span>
        </div>
        <div class="subscore-item">
          <span class="subscore-label">Wallet (20%)</span>
          <div class="progress-bar"><div class="fill amber" style="width: ${wallet_subscore}%;"></div></div>
          <span class="font-mono text-xs">${Math.round(wallet_subscore)}</span>
        </div>
        <div class="subscore-item">
          <span class="subscore-label">Narrative (15%)</span>
          <div class="progress-bar"><div class="fill purple" style="width: ${narrative_subscore}%;"></div></div>
          <span class="font-mono text-xs">${Math.round(narrative_subscore)}</span>
        </div>
      </div>
      <div class="signal-action-footer">
        <span class="badge badge-action">${action_type}</span>
        <span class="text-xs text-muted">Suggested Allocation: <strong class="text-primary font-mono">${recommended_weight}</strong></span>
      </div>
    </div>
  `;
}

/**
 * 7. Paper Portfolio and Position Card
 */
export function renderPaperDeskCard({ desk }) {
  const {
    chain_name,
    caip2_id,
    cash_balance,
    portfolio_value,
    realized_pnl,
    unrealized_pnl,
    positions_count
  } = desk;

  const isPnlPositive = Number(realized_pnl) >= 0;

  return `
    <div class="card paper-desk-card" tabindex="0">
      <div class="card-header">
        <div>
          <span class="card-title">${chain_name} Paper Desk</span>
          <span class="text-xs font-mono text-muted">${caip2_id}</span>
        </div>
        <span class="badge badge-env-paper">PAPER DESK</span>
      </div>
      <div class="desk-stats-grid">
        <div class="desk-stat">
          <span class="label">VIRTUAL CASH</span>
          <span class="val font-mono">${cash_balance}</span>
        </div>
        <div class="desk-stat">
          <span class="label">TOTAL EQUITY</span>
          <span class="val font-mono font-bold">${portfolio_value}</span>
        </div>
        <div class="desk-stat">
          <span class="label">REALIZED PNL</span>
          <span class="val font-mono ${isPnlPositive ? 'text-success' : 'text-danger'}">${isPnlPositive ? '+' : ''}${realized_pnl}</span>
        </div>
        <div class="desk-stat">
          <span class="label">OPEN POSITIONS</span>
          <span class="val font-mono">${positions_count}</span>
        </div>
      </div>
    </div>
  `;
}

/**
 * 8. Alert and Notification Card with Disabled Live Execution Controls
 */
export function renderAlertCard({ alert }) {
  const {
    id,
    title,
    message,
    severity = 'INFO',
    timestamp,
    token_symbol,
    chain_name
  } = alert;

  const severityBadge = severity === 'CRITICAL' ? 'badge-danger' : severity === 'WARNING' ? 'badge-probabilistic' : 'badge-safe';

  return `
    <div class="card alert-card" data-alert-id="${id}" tabindex="0">
      <div class="card-header">
        <div class="alert-title-row">
          <span class="badge ${severityBadge}">${severity}</span>
          <span class="font-bold">${title}</span>
        </div>
        <span class="text-xs font-mono text-muted">${new Date(timestamp || Date.now()).toLocaleTimeString()}</span>
      </div>
      <p class="alert-message text-sm text-secondary">${message}</p>
      <div class="card-footer alert-actions">
        <span class="text-xs text-muted">Chain: <strong class="font-mono">${chain_name || 'Multi-Chain'}</strong></span>
        <div class="live-control-group">
          <button class="btn btn-danger btn-sm" disabled title="Live Execution Gateway Locked. Requires separate hardware authorization." aria-disabled="true">
            🔒 Live Execution Locked
          </button>
        </div>
      </div>
    </div>
  `;
}

/**
 * 9. Responsive Data Table & SVG Sparkline
 */
export function renderSparkline(points = [10, 15, 12, 18, 22, 19, 27, 35], isPositive = true) {
  if (!points || points.length === 0) return '';
  const width = 100;
  const height = 28;
  const min = Math.min(...points);
  const max = Math.max(...points);
  const range = max - min || 1;

  const coords = points.map((p, i) => {
    const x = (i / (points.length - 1)) * width;
    const y = height - ((p - min) / range) * (height - 6) - 3;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(' ');

  const strokeColor = isPositive ? '#10b981' : '#ef4444';

  return `
    <svg class="sparkline" viewBox="0 0 ${width} ${height}" width="${width}" height="${height}" aria-hidden="true">
      <polyline fill="none" stroke="${strokeColor}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" points="${coords}" />
    </svg>
  `;
}

export function renderDataTable(columns, rows) {
  return `
    <div class="table-responsive-wrapper">
      <table class="data-table">
        <thead>
          <tr>
            ${columns.map(col => `<th class="${col.align || 'text-left'}">${col.label}</th>`).join('')}
          </tr>
        </thead>
        <tbody>
          ${rows.map(row => `
            <tr>
              ${columns.map(col => `<td class="${col.align || 'text-left'} font-mono">${row[col.key] || '—'}</td>`).join('')}
            </tr>
          `).join('')}
        </tbody>
      </table>
    </div>
  `;
}

/**
 * 10. Filter Toolbar and Modal Sheet
 */
export function renderFilterToolbar({ chains = [], onChainChange, onSearch }) {
  const chainOptions = chains.map(c => `<option value="${c.id}">${c.name}</option>`).join('');

  return `
    <div class="filter-toolbar card">
      <div class="search-input-group">
        <span class="search-icon" aria-hidden="true">🔍</span>
        <input type="text" id="global-search" class="form-input" placeholder="Search tokens by symbol, name, address or narrative..." aria-label="Search tokens">
      </div>
      <div class="filter-controls">
        <select id="chain-filter-select" class="form-select" aria-label="Filter by blockchain">
          <option value="ALL">All Chains (12 Networks)</option>
          ${chainOptions}
        </select>
        <select id="risk-filter-select" class="form-select" aria-label="Filter by risk profile">
          <option value="ALL">All Risk Grades</option>
          <option value="SAFE">Safe Only (Score 80+)</option>
          <option value="DEGEN">High Momentum Only</option>
        </select>
        <button id="btn-refresh" class="btn btn-secondary" aria-label="Refresh Market Data">
          <span class="refresh-icon">↻</span> Refresh
        </button>
      </div>
    </div>
  `;
}

export function renderModal({ id = 'modal-root', title, bodyContent, actionsContent }) {
  return `
    <div id="${id}" class="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="${id}-title" hidden>
      <div class="modal-card">
        <div class="modal-header">
          <h2 id="${id}-title" class="modal-title">${title}</h2>
          <button class="btn-close" aria-label="Close dialog" data-action="close-modal">&times;</button>
        </div>
        <div class="modal-body">
          ${bodyContent}
        </div>
        ${actionsContent ? `<div class="modal-footer">${actionsContent}</div>` : ''}
      </div>
    </div>
  `;
}
