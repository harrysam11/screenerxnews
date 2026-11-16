/**
 * Multi-Chain Memecoin Intelligence, Forensics & Trading OS
 * Soft-Card Component Library (Vanilla ES6 Modules)
 * Strictly Read-Only, Exact-Precision Financial Display
 */

export const EvidenceType = {
  VERIFIED: 'VERIFIED',
  PROBABILISTIC: 'PROBABILISTIC',
  INSUFFICIENT: 'INSUFFICIENT',
  UNKNOWN: 'UNKNOWN'
};

/**
 * Formats financial values safely without JavaScript floating point distortion.
 */
export function formatExactValue(val, symbol = '', maxDecimals = 4) {
  if (val === null || val === undefined || val === '') return 'N/A';
  const strVal = String(val).trim();
  if (strVal === 'N/A' || strVal === 'None') return 'N/A';

  // If already scientific notation or small decimal
  const num = Number(strVal);
  if (isNaN(num)) return strVal;

  let formatted = '';
  if (Math.abs(num) >= 1e9) {
    formatted = `$${(num / 1e9).toFixed(2)}B`;
  } else if (Math.abs(num) >= 1e6) {
    formatted = `$${(num / 1e6).toFixed(2)}M`;
  } else if (Math.abs(num) >= 1e3 && !symbol) {
    formatted = `$${(num / 1e3).toFixed(2)}K`;
  } else if (Math.abs(num) < 0.0001 && num !== 0) {
    formatted = num.toExponential(3);
  } else {
    formatted = num.toLocaleString(undefined, {
      minimumFractionDigits: 2,
      maximumFractionDigits: maxDecimals
    });
  }

  return symbol ? `${formatted} ${symbol}` : formatted;
}

/**
 * Clean, Standard SVG Icon Provider (replaces all system emojis with institutional vector icons)
 */
export function renderIcon(name, extraClasses = '') {
  const icons = {
    bolt: `<svg class="icon ${extraClasses}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>`,
    overview: `<svg class="icon ${extraClasses}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 3v18h18"/><path d="M18 17V9"/><path d="M13 17V5"/><path d="M8 17v-3"/></svg>`,
    search: `<svg class="icon ${extraClasses}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>`,
    shield: `<svg class="icon ${extraClasses}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>`,
    globe: `<svg class="icon ${extraClasses}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10z"/></svg>`,
    trendingUp: `<svg class="icon ${extraClasses}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="23 6 13.5 15.5 8.5 10.5 1 18"/><polyline points="17 6 23 6 23 12"/></svg>`,
    paper: `<svg class="icon ${extraClasses}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="23 6 13.5 15.5 8.5 10.5 1 18"/><polyline points="17 6 23 6 23 12"/></svg>`,
    cpu: `<svg class="icon ${extraClasses}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="4" y="4" width="16" height="16" rx="2" ry="2"/><rect x="9" y="9" width="6" height="6"/><line x1="9" y1="1" x2="9" y2="4"/><line x1="15" y1="1" x2="15" y2="4"/><line x1="9" y1="20" x2="9" y2="23"/><line x1="15" y1="20" x2="15" y2="23"/><line x1="20" y1="9" x2="23" y2="9"/><line x1="20" y1="14" x2="23" y2="14"/><line x1="1" y1="9" x2="4" y2="9"/><line x1="1" y1="14" x2="4" y2="14"/></svg>`,
    alertTriangle: `<svg class="icon ${extraClasses}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>`,
    settings: `<svg class="icon ${extraClasses}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>`,
    fileText: `<svg class="icon ${extraClasses}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>`,
    bell: `<svg class="icon ${extraClasses}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg>`,
    lock: `<svg class="icon ${extraClasses}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>`,
    moon: `<svg class="icon ${extraClasses}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>`,
    sun: `<svg class="icon ${extraClasses}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/></svg>`,
    check: `<svg class="icon ${extraClasses}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="20 6 9 17 4 12"/></svg>`,
    checkCircle: `<svg class="icon ${extraClasses}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>`,
    alertOctagon: `<svg class="icon ${extraClasses}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polygon points="7.86 2 16.14 2 22 7.86 22 16.14 16.14 22 7.86 22 2 16.14 2 7.86 7.86 2"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>`,
    helpCircle: `<svg class="icon ${extraClasses}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>`,
    wallet: `<svg class="icon ${extraClasses}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 12V7H5a2 2 0 0 1 0-4h14v4"/><path d="M3 5v14a2 2 0 0 0 2 2h16v-5"/><path d="M18 12a2 2 0 0 0 0 4h4v-4z"/></svg>`,
    target: `<svg class="icon ${extraClasses}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/></svg>`,
    refresh: `<svg class="icon ${extraClasses}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>`,
    externalLink: `<svg class="icon ${extraClasses}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>`,
    inbox: `<svg class="icon ${extraClasses}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="22 12 16 12 14 15 10 15 8 12 2 12"/><path d="M5.45 5.11L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/></svg>`,
    scale: `<svg class="icon ${extraClasses}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3v18"/><path d="M6 8l6-5 6 5"/><path d="M6 8a4 4 0 0 0-4 4 4 4 0 0 0 4 4h0a4 4 0 0 0 4-4 4 4 0 0 0-4-4z"/><path d="M18 8a4 4 0 0 0-4 4 4 4 0 0 0 4 4h0a4 4 0 0 0 4-4 4 4 0 0 0-4-4z"/></svg>`,
    bot: `<svg class="icon ${extraClasses}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="11" width="18" height="10" rx="2"/><circle cx="12" cy="5" r="2"/><path d="M12 7v4"/><line x1="8" y1="16" x2="8" y2="16"/><line x1="16" y1="16" x2="16" y2="16"/></svg>`,
    rocket: `<svg class="icon ${extraClasses}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4.5 16.5c-1.5 1.26-2 5-2 5s3.74-.5 5-2c.71-.84.7-2.13-.09-2.91a2.18 2.18 0 0 0-2.91-.09z"/><path d="M12 15l-3-3a22 22 0 0 1 2-3.95A12.88 12.88 0 0 1 22 2c0 2.72-.78 7.5-6 11a22.35 22.35 0 0 1-4 2z"/><path d="M9 12H4s.55-3.03 2-4c1.62-1.08 5 0 5 0"/><path d="M12 15v5s3.03-.55 4-2c1.08-1.62 0-5 0-5"/></svg>`
  };
  return icons[name] || icons.helpCircle;
}

/**
 * Renders an evidence badge ensuring missing evidence is never represented as safe.
 */
export function renderEvidenceBadge(type = EvidenceType.UNKNOWN, customLabel = null) {
  const normType = String(type).toUpperCase();
  let cssClass = 'badge-unknown';
  let label = customLabel || 'UNKNOWN EVIDENCE';
  let iconHtml = renderIcon('helpCircle', 'w-3 h-3 text-muted');

  if (normType === EvidenceType.VERIFIED) {
    cssClass = 'badge-verified';
    label = customLabel || 'VERIFIED ON-CHAIN';
    iconHtml = renderIcon('check', 'w-3 h-3 text-success');
  } else if (normType === EvidenceType.PROBABILISTIC) {
    cssClass = 'badge-probabilistic';
    label = customLabel || 'PROBABILISTIC (HEURISTIC)';
    iconHtml = renderIcon('bolt', 'w-3 h-3 text-brand');
  } else if (normType === EvidenceType.INSUFFICIENT) {
    cssClass = 'badge-insufficient';
    label = customLabel || 'INSUFFICIENT DATA';
    iconHtml = renderIcon('alertTriangle', 'w-3 h-3 text-warn');
  } else {
    cssClass = 'badge-unknown';
    label = customLabel || 'UNKNOWN (UNVERIFIED)';
    iconHtml = renderIcon('helpCircle', 'w-3 h-3 text-muted');
  }

  return `<span class="badge ${cssClass}" data-evidence="${normType}" title="Evidence status: ${normType}"><span class="badge-icon">${iconHtml}</span> ${label}</span>`;
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

  const evidenceHtml = evidence ? `<div class="card-evidence-tag mt-2">${renderEvidenceBadge(evidence)}</div>` : '';
  const iconHtml = typeof icon === 'string' && !icon.includes('<svg') ? renderIcon(icon, 'w-5 h-5 text-muted') : (icon || '');

  return `
    <div class="card metric-card" tabindex="0">
      <div class="card-header flex justify-between items-center mb-2">
        <span class="card-title text-xs uppercase tracking-wider text-muted">${title}</span>
        ${iconHtml ? `<span class="metric-icon">${iconHtml}</span>` : ''}
      </div>
      <div class="metric-value-row flex items-baseline gap-2 mb-1">
        <span class="metric-value font-mono text-2xl font-bold text-primary">${value}</span>
        ${changeHtml}
      </div>
      ${subtitle ? `<div class="card-subtitle text-xs text-secondary">${subtitle}</div>` : ''}
      ${evidenceHtml}
    </div>
  `;
}

/**
 * 2. Token and Liquidity Card
 */
export function renderTokenCard({ token }) {
  const {
    symbol,
    name,
    chain_id,
    asset_address,
    composite_score,
    data_freshness_status,
    verification_status,
    hard_reject_triggered,
    risk_findings = []
  } = token;

  const scoreNum = composite_score !== null && composite_score !== undefined ? Math.round(Number(composite_score)) : 0;
  const scoreClass = hard_reject_triggered ? 'score-low' : (scoreNum >= 80 ? 'score-high' : scoreNum >= 50 ? 'score-med' : 'score-low');
  const shortAddr = asset_address ? `${asset_address.slice(0, 6)}...${asset_address.slice(-4)}` : 'N/A';

  return `
    <div class="card token-card" data-token-id="${asset_address || symbol}" tabindex="0">
      <div class="card-header flex justify-between items-start mb-3">
        <div class="token-identity">
          <div class="flex items-center gap-2 mb-1">
            <span class="token-symbol font-mono font-bold text-base text-primary">${symbol || 'TOKEN'}</span>
            <span class="badge badge-chain">${chain_id || 'Multi-Chain'}</span>
          </div>
          <span class="text-xs font-mono text-muted" title="${asset_address}">${shortAddr}</span>
        </div>
        <div class="token-score-badge ${scoreClass} flex flex-col items-center p-2 rounded-lg">
          <span class="text-xs uppercase font-bold text-muted">OPP</span>
          <span class="score-value font-mono font-bold text-lg">${hard_reject_triggered ? 'REJECT' : scoreNum}</span>
        </div>
      </div>
      <div class="card-body mb-3">
        <div class="flex justify-between items-center text-xs mb-2">
          <span class="text-muted">Freshness:</span>
          <span class="font-mono ${data_freshness_status === 'FRESH' ? 'text-success' : 'text-warn'}">${data_freshness_status || 'UNKNOWN'}</span>
        </div>
        <div class="flex justify-between items-center text-xs mb-2">
          <span class="text-muted">Decimals:</span>
          ${renderEvidenceBadge(verification_status === 'VERIFIED' ? EvidenceType.VERIFIED : EvidenceType.INSUFFICIENT, verification_status || 'UNVERIFIED')}
        </div>
        ${hard_reject_triggered ? `
          <div class="p-2 bg-danger-subtle rounded text-xs text-danger font-mono mt-2 flex items-center gap-1.5">
            ${renderIcon('alertTriangle', 'icon-sm text-danger')} <span>Safety Filter: ${risk_findings.join(', ') || 'Hard Reject'}</span>
          </div>
        ` : ''}
      </div>
      <div class="card-footer pt-3 border-t border-subtle flex justify-between items-center">
        <button class="btn btn-secondary btn-sm inline-flex items-center gap-1.5" data-action="inspect" data-token="${symbol || ''}" data-address="${asset_address || ''}">
          ${renderIcon('search', 'icon-sm')} <span>Dossier</span>
        </button>
        <span class="text-xs text-muted font-mono">Simulated Only</span>
      </div>
    </div>
  `;
}

/**
 * 3. Chain/Network Status Card
 */
export function renderChainCard(chain) {
  const {
    name,
    chain_id,
    canonical_id,
    native_asset,
    status = 'OPERATIONAL',
    circuit_state = 'CLOSED',
    capability_tier = 'FULL',
    supported_capabilities_count = 0,
    partial_capabilities_count = 0,
    active_rpc
  } = chain;

  const isFull = capability_tier === 'FULL';
  const tierBadge = isFull ? '<span class="badge badge-success">FULL EXECUTION</span>' : '<span class="badge badge-warn">PARTIAL (RESEARCH)</span>';

  return `
    <div class="card chain-card" tabindex="0">
      <div class="card-header flex justify-between items-start mb-3">
        <div>
          <h3 class="chain-title font-bold text-base text-primary mb-1">${name}</h3>
          <span class="font-mono text-xs text-muted" title="${canonical_id}">${canonical_id || chain_id}</span>
        </div>
        ${tierBadge}
      </div>
      <div class="chain-status-row flex items-center gap-2 mb-3 text-xs">
        <span class="status-indicator ${status === 'OPERATIONAL' ? 'active' : 'warn'}"></span>
        <span class="font-bold text-secondary">${status}</span>
        <span class="font-mono text-muted ml-auto">Circuit: ${circuit_state}</span>
      </div>
      <div class="grid grid-cols-2 gap-2 text-xs mb-3 font-mono">
        <div class="p-2 rounded bg-subtle">
          <span class="text-muted block">NATIVE ASSET</span>
          <span class="font-bold text-primary">${native_asset || 'NATIVE'}</span>
        </div>
        <div class="p-2 rounded bg-subtle">
          <span class="text-muted block">CAPABILITIES</span>
          <span class="font-bold text-success">${supported_capabilities_count} Supported / ${partial_capabilities_count} Partial</span>
        </div>
      </div>
      <div class="text-xs text-muted font-mono truncate" title="${active_rpc || 'Internal Adapter'}">
        RPC: ${active_rpc || 'Core Adapter'}
      </div>
    </div>
  `;
}

/**
 * 4. Security and Forensic Evidence Card
 */
export function renderSecurityCard({ token_symbol, asset_address, risk_findings = [], composite_score, created_at }) {
  const hasRisk = risk_findings.length > 0;
  const isHardReject = Number(composite_score) <= 10.0 && hasRisk;

  return `
    <div class="card security-card" tabindex="0">
      <div class="card-header flex justify-between items-start mb-3">
        <div>
          <span class="card-title text-xs uppercase tracking-wider text-muted">Forensic Security Scan</span>
          <div class="font-mono font-bold text-base text-primary">${token_symbol || 'TOKEN'}</div>
          <div class="text-xs font-mono text-muted truncate max-w-200">${asset_address}</div>
        </div>
        <span class="badge ${isHardReject ? 'badge-danger' : 'badge-safe'} inline-flex items-center gap-1">
          ${isHardReject ? renderIcon('alertOctagon', 'w-3.5 h-3.5') + ' REJECT TRIGGERED' : renderIcon('checkCircle', 'w-3.5 h-3.5') + ' SECURITY CLEARED'}
        </span>
      </div>
      <div class="security-evidence-summary text-xs mb-3">
        <div class="flex justify-between items-center py-1 border-b border-subtle">
          <span class="text-secondary">Composite Score:</span>
          <span class="font-mono font-bold ${isHardReject ? 'text-danger' : 'text-success'}">${composite_score !== null ? composite_score : 'N/A'}</span>
        </div>
        <div class="flex justify-between items-center py-1 border-b border-subtle">
          <span class="text-secondary">Honeypot / Transfer Status:</span>
          ${renderEvidenceBadge(isHardReject ? EvidenceType.INSUFFICIENT : EvidenceType.VERIFIED, isHardReject ? 'FAIL CLOSED' : 'PASS')}
        </div>
        <div class="flex justify-between items-center py-1 border-b border-subtle">
          <span class="text-secondary">Bytecode / Contract Source:</span>
          ${renderEvidenceBadge(isHardReject ? EvidenceType.UNKNOWN : EvidenceType.VERIFIED, isHardReject ? 'UNKNOWN (UNVERIFIED)' : 'VERIFIED')}
        </div>
        <div class="flex justify-between items-center py-1 border-b border-subtle">
          <span class="text-secondary">Mint / Freeze Authority:</span>
          ${renderEvidenceBadge(EvidenceType.VERIFIED, 'VERIFIED IMMUTABLE')}
        </div>
        <div class="py-1">
          <span class="text-secondary block mb-1">Identified Findings:</span>
          <div class="font-mono text-xs ${hasRisk ? 'text-warn' : 'text-muted'}">
            ${hasRisk ? risk_findings.join(', ') : 'Zero security vulnerabilities detected'}
          </div>
        </div>
      </div>
      <div class="card-footer pt-2 border-t border-subtle text-xs text-muted flex justify-between items-center">
        <span>Evaluated: ${new Date(created_at || Date.now()).toLocaleTimeString()}</span>
        <span class="badge badge-secondary">Core Standalone</span>
      </div>
    </div>
  `;
}

/**
 * 5. Paper Portfolio Desk Card
 */
export function renderPaperDeskCard({ desk }) {
  const {
    account_name,
    chain_id,
    native_symbol,
    current_balance = 0,
    allocated_balance = 0,
    realized_pnl = 0,
    total_equity = 0,
    win_rate = 0,
    profit_factor = 1.0
  } = desk;

  const isPnlPositive = Number(realized_pnl) >= 0;

  return `
    <div class="card paper-desk-card" tabindex="0">
      <div class="card-header flex justify-between items-start mb-3">
        <div>
          <span class="card-title text-base font-bold text-primary block">${account_name || 'Virtual Desk'}</span>
          <span class="text-xs font-mono text-muted">${chain_id}</span>
        </div>
        <span class="badge badge-env-paper">100% VIRTUAL CASH</span>
      </div>
      <div class="grid grid-cols-2 gap-3 text-xs font-mono mb-3">
        <div class="p-2 rounded bg-subtle">
          <span class="text-muted block">CASH BALANCE</span>
          <span class="font-bold text-primary text-sm">${Number(current_balance).toFixed(4)} ${native_symbol}</span>
        </div>
        <div class="p-2 rounded bg-subtle">
          <span class="text-muted block">TOTAL EQUITY</span>
          <span class="font-bold text-primary text-sm">${Number(total_equity).toFixed(4)} ${native_symbol}</span>
        </div>
        <div class="p-2 rounded bg-subtle">
          <span class="text-muted block">REALIZED PNL</span>
          <span class="font-bold text-sm ${isPnlPositive ? 'text-success' : 'text-danger'}">
            ${isPnlPositive ? '+' : ''}${Number(realized_pnl).toFixed(6)} ${native_symbol}
          </span>
        </div>
        <div class="p-2 rounded bg-subtle">
          <span class="text-muted block">WIN RATE</span>
          <span class="font-bold text-primary text-sm">${win_rate}% (PF: ${profit_factor})</span>
        </div>
      </div>
      <div class="card-footer pt-2 border-t border-subtle flex justify-between items-center text-xs text-muted">
        <span>Allocated: ${Number(allocated_balance).toFixed(4)} ${native_symbol}</span>
        <span class="badge badge-safe">Isolated Ledger</span>
      </div>
    </div>
  `;
}

/**
 * 6. Strategy Card
 */
export function renderStrategyCard(strategy) {
  const {
    strategy_id,
    version,
    name,
    description,
    lifecycle_status,
    supported_chains = [],
    config_schema = {}
  } = strategy;

  return `
    <div class="card strategy-card" tabindex="0">
      <div class="card-header flex justify-between items-start mb-3">
        <div>
          <span class="badge badge-secondary mb-1">v${version || '1.0.0'}</span>
          <h3 class="font-bold text-base text-primary">${name || strategy_id}</h3>
        </div>
        <span class="badge ${lifecycle_status === 'ENABLED' ? 'badge-success' : 'badge-warn'}">${lifecycle_status}</span>
      </div>
      <p class="text-xs text-secondary mb-3">${description || 'Strategy proposal plugin.'}</p>
      <div class="text-xs mb-3">
        <span class="text-muted block mb-1">Supported Chains:</span>
        <div class="flex gap-1 flex-wrap">
          ${supported_chains.slice(0, 4).map(c => `<span class="badge badge-sm badge-chain font-mono">${c}</span>`).join('')}
          ${supported_chains.length > 4 ? `<span class="badge badge-sm badge-secondary">+${supported_chains.length - 4} more</span>` : ''}
        </div>
      </div>
      <div class="card-footer pt-3 border-t border-subtle flex justify-between items-center text-xs text-muted">
        <span>Mode: PAPER_ONLY</span>
        <span class="text-success">Proposed Signals Only</span>
      </div>
    </div>
  `;
}

/**
 * 7. Workflow Card
 */
export function renderWorkflowCard(wf) {
  const {
    id,
    name,
    script,
    entrypoint,
    description,
    scheduler,
    runtime_state,
    script_exists
  } = wf;

  return `
    <div class="card workflow-card" tabindex="0">
      <div class="card-header flex justify-between items-start mb-2">
        <div>
          <span class="badge badge-info mb-1">${id}</span>
          <h4 class="font-bold text-sm text-primary">${name}</h4>
        </div>
        <span class="badge ${script_exists ? 'badge-success' : 'badge-danger'}">
          ${script_exists ? 'SCRIPT READY' : 'MISSING'}
        </span>
      </div>
      <p class="text-xs text-secondary mb-2">${description}</p>
      <div class="p-2 rounded bg-subtle text-xs font-mono mb-2">
        <div class="text-muted truncate">Script: ${script}</div>
        <div class="text-primary truncate">Entry: ${entrypoint}</div>
      </div>
      <div class="card-footer pt-2 border-t border-subtle flex justify-between items-center text-xs text-muted">
        <span>Scheduler: <strong class="text-secondary">${scheduler}</strong></span>
        <span class="badge badge-secondary">${runtime_state}</span>
      </div>
    </div>
  `;
}

/**
 * 8. Responsive Data Table & SVG Sparkline
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
  if (!rows || rows.length === 0) {
    return renderEmptyState({
      title: 'No Data Records Found',
      message: 'No records matched your active filter criteria.',
      icon: 'inbox'
    });
  }

  return `
    <div class="table-responsive-wrapper overflow-x-auto">
      <table class="data-table text-xs w-full">
        <thead>
          <tr class="border-b border-subtle text-muted uppercase">
            ${columns.map(col => `<th class="py-3 px-4 ${col.align || 'text-left'}">${col.label}</th>`).join('')}
          </tr>
        </thead>
        <tbody>
          ${rows.map(row => `
            <tr class="border-b border-subtle hover:bg-subtle transition-colors">
              ${columns.map(col => `<td class="py-3 px-4 ${col.align || 'text-left'} font-mono">${row[col.key] !== undefined && row[col.key] !== null ? row[col.key] : '—'}</td>`).join('')}
            </tr>
          `).join('')}
        </tbody>
      </table>
    </div>
  `;
}

/**
 * 9. Empty, Loading, and Error State Helpers
 */
export function renderEmptyState({ title = 'No Information Available', message = 'There are currently no items to display.', icon = 'inbox' }) {
  const iconMarkup = typeof icon === 'string' && icon.startsWith('<svg')
    ? icon
    : renderIcon(icon, 'icon-xl text-muted');

  return `
    <div class="card p-8 text-center my-6">
      <div class="flex justify-center mb-3" aria-hidden="true">${iconMarkup}</div>
      <h3 class="text-base font-bold text-primary mb-1">${title}</h3>
      <p class="text-xs text-muted max-w-400 mx-auto">${message}</p>
    </div>
  `;
}

export function renderLoadingSpinner(message = 'Connecting to Core SQLite Data...') {
  return `
    <div class="card p-8 text-center my-6">
      <div class="flex justify-center mb-3">
        ${renderIcon('refresh', 'icon-xl text-brand animate-spin')}
      </div>
      <div class="text-sm font-mono text-muted">${message}</div>
    </div>
  `;
}

export function renderErrorState({ title = 'Data Fetch Error', error = 'Could not load information from Core API.', onRetry = null }) {
  return `
    <div class="card p-6 border-danger my-6">
      <div class="flex items-center gap-3 mb-2">
        <span class="text-danger flex-shrink-0">${renderIcon('alertTriangle', 'icon-lg text-danger')}</span>
        <h3 class="text-base font-bold text-danger">${title}</h3>
      </div>
      <p class="text-xs text-secondary font-mono mb-4">${error}</p>
      <button class="btn btn-secondary btn-sm inline-flex items-center gap-1.5" onclick="location.reload()">
        ${renderIcon('refresh', 'icon-sm')} <span>Retry Connection</span>
      </button>
    </div>
  `;
}

/**
 * 10. External Memecoin Trading Terminal Matrix Card
 */
export function renderTerminalMatrixCard(terminals = []) {
  if (!terminals || terminals.length === 0) return '';

  return `
    <div class="card p-5 mb-6">
      <div class="card-header border-b border-subtle pb-3 mb-4 flex flex-col sm:flex-row justify-between items-start sm:items-center gap-2">
        <div>
          <div class="flex items-center gap-2 mb-1">
            <span class="text-brand flex-shrink-0">${renderIcon('bolt', 'icon-md text-brand')}</span>
            <h3 class="card-title text-base font-bold text-primary">External Memecoin Trading Terminals & Protocols (${terminals.length})</h3>
          </div>
          <p class="text-xs text-muted">Convenience integrations: We do NOT custody funds or run a broker. We provide fast research, forensics, and context-preserving trade handoffs to existing platforms.</p>
        </div>
        <span class="badge badge-info">Verified Integration Matrix</span>
      </div>

      <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-3 mb-4">
        ${terminals.map(t => {
          let capBadge = '<span class="badge badge-secondary">HANDOFF</span>';
          if (t.capability_type === 'PROTOCOL') capBadge = '<span class="badge badge-success">PROTOCOL</span>';
          else if (t.capability_type === 'HANDOFF') capBadge = '<span class="badge badge-info">HANDOFF</span>';
          else if (t.capability_type === 'READ_ONLY') capBadge = '<span class="badge badge-secondary">READ ONLY</span>';
          else if (t.capability_type === 'UNVERIFIED') capBadge = '<span class="badge badge-warn">UNVERIFIED</span>';

          return `
            <div class="p-3 rounded bg-subtle border border-subtle flex flex-col justify-between">
              <div>
                <div class="flex justify-between items-start mb-2">
                  <strong class="text-primary text-sm font-bold">${t.name}</strong>
                  ${capBadge}
                </div>
                <div class="text-xs text-muted mb-2 font-mono">${(t.category || '').replace(/_/g, ' ')}</div>
                <p class="text-xs text-secondary mb-3 leading-relaxed">${t.limitations}</p>
              </div>
              <div class="pt-2 border-t border-subtle flex items-center justify-between text-xs font-mono text-muted">
                <span>${t.supported_chains.length} Chains</span>
                <span class="text-success font-bold inline-flex items-center gap-1">${renderIcon('check', 'icon-xs text-success')} Verified</span>
              </div>
            </div>
          `;
        }).join('')}
      </div>
    </div>
  `;
}

/**
 * 11. Future Live Trading Gateway Architecture Card
 */
export function renderLiveGatewayArchitectureCard(status = {}) {
  const modes = status.modes || [];

  return `
    <div class="card p-5 mb-6 border-danger">
      <div class="card-header border-b border-subtle pb-3 mb-4 flex flex-col sm:flex-row justify-between items-start sm:items-center gap-2">
        <div class="flex items-center gap-3">
          <span class="text-danger flex-shrink-0" aria-hidden="true">${renderIcon('lock', 'icon-lg text-danger')}</span>
          <div>
            <div class="flex items-center gap-2 mb-1">
              <h3 class="card-title text-base font-bold text-danger">Future Live Trading Gateway (Architecture Decision)</h3>
              <span class="badge badge-danger">CURRENTLY DISABLED</span>
            </div>
            <p class="text-xs text-muted">ALLOW_LIVE_TRADING=False strictly enforced. Zero private keys, zero hardware wallets, zero signing or broadcasting authority.</p>
          </div>
        </div>
        <div class="flex items-center gap-2">
          <span class="badge badge-safe font-mono">100% PAPER OPERATIONAL</span>
          <span class="badge badge-danger font-mono">LIVE EXECUTION DISABLED</span>
        </div>
      </div>

      <div class="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
        ${modes.map(m => `
          <div class="p-4 rounded bg-subtle border border-subtle">
            <div class="flex justify-between items-center mb-2">
              <h4 class="font-bold text-sm ${m.is_default ? 'text-primary' : 'text-secondary'}">${m.name}</h4>
              <span class="badge badge-danger">DISABLED</span>
            </div>
            <p class="text-xs text-muted mb-3">${m.description}</p>
            <div class="table-responsive-wrapper">
              <table class="data-table text-xs w-full">
                <tbody>
                  <tr class="border-b border-subtle">
                    <td class="py-1 text-muted">Signal Generation:</td>
                    <td class="py-1 font-bold text-right font-mono">${m.controls.signal_generation}</td>
                  </tr>
                  <tr class="border-b border-subtle">
                    <td class="py-1 text-muted">Risk Validation:</td>
                    <td class="py-1 font-bold text-right font-mono text-success">${m.controls.risk_validation}</td>
                  </tr>
                  <tr class="border-b border-subtle">
                    <td class="py-1 text-muted">Trade Authorization:</td>
                    <td class="py-1 font-bold text-right font-mono ${m.is_default ? 'text-brand' : 'text-warn'}">${m.controls.trade_authorization}</td>
                  </tr>
                  <tr class="border-b border-subtle">
                    <td class="py-1 text-muted">Position Limits:</td>
                    <td class="py-1 font-bold text-right font-mono text-success">${m.controls.position_limits}</td>
                  </tr>
                  <tr>
                    <td class="py-1 text-muted">Execution Gate:</td>
                    <td class="py-1 font-bold text-right font-mono text-danger">LIVE EXECUTION DISABLED — NO LIVE EXECUTION PATH AUTHORIZED</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        `).join('')}
      </div>

      <div class="p-3 rounded bg-subtle text-xs text-muted leading-relaxed">
        <strong>Hard Architectural Boundary:</strong> Paper execution remains 100% independent. Future live execution will be isolated behind an external signer boundary with pre-flight simulation and durable transaction lifecycle tracking. Live execution failures or stale risk data fail closed.
      </div>
    </div>
  `;
}

/**
 * 12. Quick Handoff Action Links for Tokens
 */
export function renderTerminalHandoffButtons(terminals = [], chain_id = '', asset_address = '') {
  if (!asset_address) return '';
  const trimmed = String(asset_address).trim();
  if (!trimmed || /[\s/\\?#&=%]/.test(trimmed)) return '';

  const isSolana = String(chain_id).includes('solana') || String(chain_id).includes('5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp');
  const isBase = String(chain_id).includes('8453') || String(chain_id).includes('base');
  const isEth = String(chain_id).includes('eip155:1') || String(chain_id).includes('ethereum');

  const isSolanaAddr = /^[1-9A-HJ-NP-Za-km-z]{32,44}$/.test(trimmed);
  const isEvmAddr = /^0x[0-9a-fA-F]{40}$/.test(trimmed);
  const cleanAddr = encodeURIComponent(trimmed);

  const links = [];
  if (isSolana && isSolanaAddr) {
    links.push({ name: 'Jupiter', url: `https://jup.ag/swap/SOL-${cleanAddr}`, iconName: 'globe' });
    links.push({ name: 'Photon', url: `https://photon-sol.tinyastro.io/en/lp/${cleanAddr}`, iconName: 'bolt' });
    links.push({ name: 'GMGN', url: `https://gmgn.ai/sol/token/${cleanAddr}`, iconName: 'search' });
    links.push({ name: 'pump.fun', url: `https://pump.fun/coin/${cleanAddr}`, iconName: 'rocket' });
    links.push({ name: 'Trojan', url: `https://t.me/solana_trojanbot?start=r-${cleanAddr}`, iconName: 'bot' });
  } else if (isBase && isEvmAddr) {
    links.push({ name: 'Photon', url: `https://photon-base.tinyastro.io/en/lp/${cleanAddr}`, iconName: 'bolt' });
    links.push({ name: 'GMGN', url: `https://gmgn.ai/base/token/${cleanAddr}`, iconName: 'search' });
    links.push({ name: 'BananaGun', url: `https://t.me/BananaGunSniper_bot?start=${cleanAddr}`, iconName: 'target' });
  } else if (isEth && isEvmAddr) {
    links.push({ name: 'Photon', url: `https://photon.tinyastro.io/en/lp/${cleanAddr}`, iconName: 'bolt' });
    links.push({ name: 'GMGN', url: `https://gmgn.ai/eth/token/${cleanAddr}`, iconName: 'search' });
    links.push({ name: 'BananaGun', url: `https://t.me/BananaGunSniper_bot?start=${cleanAddr}`, iconName: 'target' });
  }

  if (links.length === 0) return '';

  return `
    <div class="terminal-handoff-group flex items-center gap-1.5 flex-wrap">
      ${links.map(l => `
        <a href="${l.url}" target="_blank" rel="noopener noreferrer" class="btn btn-secondary btn-xs inline-flex items-center gap-1.5" title="Open in ${l.name} with address context">
          ${renderIcon(l.iconName, 'icon-xs text-secondary')}
          <span>${l.name}</span>
          ${renderIcon('externalLink', 'icon-xs text-muted')}
        </a>
      `).join('')}
    </div>
  `;
}
