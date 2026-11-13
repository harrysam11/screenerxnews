-- ============================================================================
-- SOLANA MEMECOIN OS - HARDENED DATABASE DDL (V2)
-- PostgreSQL 14+ compatible
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE SCHEMA IF NOT EXISTS n8n_internal;

-- ----------------------------------------------------------------------------
-- 1. ASSETS & POOLS (AUD-06 & AUD-07)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS assets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    mint_address TEXT UNIQUE NOT NULL,
    token_program TEXT, -- NULL until verified: Tokenkeg... or Tokenz...
    name TEXT,
    symbol TEXT,
    decimals SMALLINT, -- NULL until confirmed by RPC getAccountInfo
    total_supply_raw NUMERIC(40,0), -- Raw units as exact numeric integer
    metadata_uri TEXT,
    is_token_2022 BOOLEAN, -- Tri-state: true, false, NULL (unverified)
    token_2022_extensions JSONB DEFAULT '[]'::jsonb,
    is_verified BOOLEAN NOT NULL DEFAULT FALSE,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS pools (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    asset_id UUID NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    pool_address TEXT UNIQUE NOT NULL,
    dex_program TEXT NOT NULL, -- raydium_v4, raydium_clmm, orca_whirlpool, meteora_dlmm, pumpfun
    dex_version TEXT NOT NULL DEFAULT 'v1',
    base_mint TEXT NOT NULL,
    quote_mint TEXT NOT NULL, -- WSOL: So11111111111111111111111111111111111111112
    created_slot BIGINT,
    created_at TIMESTAMPTZ,
    lp_status TEXT NOT NULL DEFAULT 'unknown', -- burned, locked, unlocked, unknown
    lp_burn_pct NUMERIC(5,2),
    lp_locked_pct NUMERIC(5,2),
    lp_lock_expiry TIMESTAMPTZ,
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS market_snapshots (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    asset_id UUID NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    pool_id UUID NOT NULL REFERENCES pools(id) ON DELETE CASCADE,
    request_id TEXT NOT NULL,
    source TEXT NOT NULL, -- dexscreener, jupiter, rpc
    source_record_id TEXT,
    observed_at TIMESTAMPTZ NOT NULL, -- Publisher timestamp
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT now(), -- Local receipt timestamp
    slot BIGINT,
    price_usd NUMERIC(30,12) NOT NULL,
    price_sol NUMERIC(30,12),
    bid_price_sol NUMERIC(30,12),
    ask_price_sol NUMERIC(30,12),
    market_cap_usd NUMERIC(30,4),
    fdv_usd NUMERIC(30,4),
    liquidity_usd NUMERIC(30,4),
    liquidity_sol NUMERIC(30,4),
    volume_5m NUMERIC(30,4) DEFAULT 0,
    volume_1h NUMERIC(30,4) DEFAULT 0,
    volume_24h NUMERIC(30,4) DEFAULT 0,
    buys_1h INTEGER,
    sells_1h INTEGER,
    data_quality TEXT NOT NULL DEFAULT 'COMPLETE', -- COMPLETE, PARTIAL, DEGRADED
    is_stale BOOLEAN NOT NULL DEFAULT FALSE,
    CONSTRAINT uq_snapshot_pool_time UNIQUE(pool_id, observed_at)
);

CREATE TABLE IF NOT EXISTS ohlcv_candles (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    asset_id UUID NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    pool_id UUID NOT NULL REFERENCES pools(id) ON DELETE CASCADE,
    resolution TEXT NOT NULL CHECK (resolution IN ('1m', '5m', '15m', '1h', '4h', '1d')),
    timestamp TIMESTAMPTZ NOT NULL,
    open_price NUMERIC(30,12) NOT NULL,
    high_price NUMERIC(30,12) NOT NULL,
    low_price NUMERIC(30,12) NOT NULL,
    close_price NUMERIC(30,12) NOT NULL,
    volume NUMERIC(30,4) NOT NULL,
    trades_count INTEGER DEFAULT 0,
    source TEXT NOT NULL DEFAULT 'aggregated',
    CONSTRAINT chk_candle_bounds CHECK (
        high_price >= open_price AND
        high_price >= close_price AND
        low_price <= open_price AND
        low_price <= close_price AND
        low_price >= 0
    ),
    CONSTRAINT uq_candle_window UNIQUE(pool_id, resolution, timestamp)
);

-- ----------------------------------------------------------------------------
-- 2. SECURITY SCANS & EVIDENCE FORENSICS (AUD-08 & AUD-10)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS security_scans (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    asset_id UUID NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    provider_version TEXT NOT NULL DEFAULT 'v1',
    risk_score NUMERIC(5,2),
    risk_level TEXT NOT NULL CHECK (risk_level IN ('LOW', 'MODERATE', 'HIGH', 'CRITICAL', 'UNKNOWN')),
    confidence TEXT NOT NULL CHECK (confidence IN ('HIGH', 'PARTIAL', 'INSUFFICIENT', 'STALE')),
    mint_authority TEXT,
    freeze_authority TEXT,
    is_mint_revoked BOOLEAN,
    is_freeze_revoked BOOLEAN,
    lp_burned BOOLEAN,
    lp_locked BOOLEAN,
    is_honeypot BOOLEAN,
    transfer_fee_bps INTEGER,
    top10_holder_pct NUMERIC(5,2),
    holder_coverage_pct NUMERIC(5,2),
    scanned_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL,
    raw_report JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS security_findings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    scan_id UUID NOT NULL REFERENCES security_scans(id) ON DELETE CASCADE,
    finding_code TEXT NOT NULL,
    severity TEXT NOT NULL CHECK (severity IN ('INFO', 'WARNING', 'CRITICAL_FAIL')),
    description TEXT NOT NULL,
    evidence JSONB
);

CREATE TABLE IF NOT EXISTS holder_snapshots (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    asset_id UUID NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    slot BIGINT NOT NULL,
    captured_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    total_holders_reported INTEGER,
    accounts_inspected INTEGER NOT NULL,
    supply_coverage_pct NUMERIC(5,2) NOT NULL,
    top10_share_pct NUMERIC(5,2),
    top20_share_pct NUMERIC(5,2),
    deployer_balance_pct NUMERIC(5,2),
    holders JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS wallets (
    address TEXT PRIMARY KEY,
    first_seen_slot BIGINT,
    first_seen_at TIMESTAMPTZ,
    last_seen_at TIMESTAMPTZ,
    category TEXT DEFAULT 'UNKNOWN',
    sol_balance_lamports BIGINT DEFAULT 0,
    realized_pnl_usd NUMERIC(30,4) DEFAULT 0,
    win_rate NUMERIC(5,2),
    trades_count INTEGER DEFAULT 0,
    funding_source_address TEXT,
    label_version TEXT,
    label_expires_at TIMESTAMPTZ,
    metadata JSONB DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS wallet_clusters (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    cluster_type TEXT NOT NULL,
    detector_version TEXT NOT NULL,
    observation_window_slots BIGINT,
    confidence NUMERIC(5,2) NOT NULL,
    detected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    evidence JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS wallet_cluster_members (
    cluster_id UUID NOT NULL REFERENCES wallet_clusters(id) ON DELETE CASCADE,
    wallet_address TEXT NOT NULL REFERENCES wallets(address) ON DELETE CASCADE,
    role TEXT NOT NULL DEFAULT 'member',
    joined_at TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (cluster_id, wallet_address)
);

CREATE TABLE IF NOT EXISTS first_buyers_forensics (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    asset_id UUID NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    pool_id UUID NOT NULL REFERENCES pools(id) ON DELETE CASCADE,
    wallet_address TEXT NOT NULL REFERENCES wallets(address),
    buy_rank SMALLINT NOT NULL CHECK (buy_rank >= 1 AND buy_rank <= 25),
    tx_signature TEXT NOT NULL,
    slot BIGINT NOT NULL,
    block_time TIMESTAMPTZ NOT NULL,
    sol_spent_lamports BIGINT NOT NULL,
    tokens_bought_raw NUMERIC(40,0) NOT NULL,
    current_status TEXT NOT NULL CHECK (current_status IN ('HOLDING', 'DUMPED_100', 'DUMPED_PARTIAL', 'TRANSFERRED')),
    holding_pct NUMERIC(5,2) NOT NULL,
    funder_address TEXT,
    is_funder_shared BOOLEAN DEFAULT FALSE,
    is_same_slot_as_pool BOOLEAN DEFAULT FALSE,
    detector_version TEXT NOT NULL,
    evidence JSONB,
    CONSTRAINT uq_asset_pool_rank UNIQUE (asset_id, pool_id, buy_rank)
);

-- ----------------------------------------------------------------------------
-- 3. RISK POLICIES & ONE-TIME APPROVAL GATEWAY (AUD-03 & AUD-05)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS risk_policies (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    environment TEXT NOT NULL CHECK (environment IN ('PAPER', 'LIVE')),
    version INTEGER NOT NULL DEFAULT 1,
    name TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT FALSE,
    max_trade_lamports BIGINT NOT NULL,
    max_daily_loss_lamports BIGINT NOT NULL,
    max_open_positions INTEGER NOT NULL,
    max_slippage_bps INTEGER NOT NULL CHECK (max_slippage_bps <= 500),
    max_price_impact_bps INTEGER NOT NULL CHECK (max_price_impact_bps <= 500),
    max_priority_fee_lamports BIGINT NOT NULL,
    min_liquidity_usd NUMERIC(15,2) NOT NULL,
    max_risk_score NUMERIC(5,2) NOT NULL,
    require_burned_lp BOOLEAN NOT NULL DEFAULT TRUE,
    require_mint_freeze_revoked BOOLEAN NOT NULL DEFAULT TRUE,
    allowed_execution_wallets JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_unique_active_policy_per_env 
ON risk_policies(environment) WHERE is_active = TRUE;

CREATE TABLE IF NOT EXISTS trade_intents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    asset_id UUID NOT NULL REFERENCES assets(id),
    pool_id UUID NOT NULL REFERENCES pools(id),
    policy_id UUID NOT NULL REFERENCES risk_policies(id),
    environment TEXT NOT NULL CHECK (environment IN ('LIVE')),
    wallet_address TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('BUY', 'SELL')),
    input_mint TEXT NOT NULL,
    output_mint TEXT NOT NULL,
    input_amount_raw NUMERIC(40,0) NOT NULL,
    minimum_output_raw NUMERIC(40,0) NOT NULL,
    max_slippage_bps INTEGER NOT NULL,
    estimated_price_impact_bps INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('PENDING_APPROVAL', 'APPROVED', 'CONSUMED', 'REJECTED', 'EXPIRED')),
    expires_at TIMESTAMPTZ NOT NULL,
    canonical_intent_digest TEXT NOT NULL,
    risk_evaluation_snapshot JSONB NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS trade_approvals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    intent_id UUID NOT NULL UNIQUE REFERENCES trade_intents(id) ON DELETE CASCADE,
    approved_by TEXT NOT NULL DEFAULT 'MANUAL_USER',
    transaction_message_hash TEXT NOT NULL,
    is_consumed BOOLEAN NOT NULL DEFAULT FALSE,
    consumed_at TIMESTAMPTZ,
    revoked_at TIMESTAMPTZ,
    revocation_reason TEXT,
    approved_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL
);

-- ----------------------------------------------------------------------------
-- 4. LIVE EXECUTION & IMMUTABLE DOUBLE-ENTRY LEDGER (AUD-01 & AUD-02)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS chain_transactions (
    signature TEXT PRIMARY KEY,
    wallet_address TEXT NOT NULL,
    slot BIGINT NOT NULL,
    block_time TIMESTAMPTZ NOT NULL,
    confirmation_status TEXT NOT NULL CHECK (confirmation_status IN ('confirmed', 'finalized', 'failed')),
    fee_lamports BIGINT NOT NULL,
    err_message TEXT,
    raw_meta JSONB
);

CREATE TABLE IF NOT EXISTS live_orders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    intent_id UUID NOT NULL UNIQUE REFERENCES trade_intents(id),
    client_order_id TEXT NOT NULL UNIQUE,
    wallet_address TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('BUY', 'SELL')),
    input_mint TEXT NOT NULL,
    output_mint TEXT NOT NULL,
    input_amount_raw NUMERIC(40,0) NOT NULL,
    minimum_output_raw NUMERIC(40,0) NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('SUBMITTED', 'CONFIRMED', 'FAILED', 'EXPIRED')),
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS live_fills (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id UUID NOT NULL REFERENCES live_orders(id) ON DELETE CASCADE,
    signature TEXT NOT NULL REFERENCES chain_transactions(signature),
    input_mint TEXT NOT NULL,
    output_mint TEXT NOT NULL,
    input_amount_raw NUMERIC(40,0) NOT NULL,
    output_amount_raw NUMERIC(40,0) NOT NULL,
    fee_lamports BIGINT NOT NULL,
    price_sol NUMERIC(30,12) NOT NULL,
    price_usd NUMERIC(30,12) NOT NULL,
    filled_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS live_ledger_journals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    transaction_signature TEXT NOT NULL UNIQUE REFERENCES chain_transactions(signature),
    order_id UUID REFERENCES live_orders(id),
    idempotency_key TEXT NOT NULL UNIQUE,
    journal_type TEXT NOT NULL CHECK (journal_type IN ('TRADE_BUY', 'TRADE_SELL', 'FEE_EXPENSE', 'TRANSFER')),
    description TEXT NOT NULL,
    posted_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS live_ledger_lines (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    journal_id UUID NOT NULL REFERENCES live_ledger_journals(id) ON DELETE CASCADE,
    account_code TEXT NOT NULL CHECK (account_code IN (
        'WALLET_SOL', 'WALLET_TOKEN', 'EXPENSE_NETWORK_FEE', 'EXPENSE_DEX_FEE', 'REALIZED_PNL'
    )),
    wallet_address TEXT NOT NULL,
    mint_address TEXT NOT NULL,
    debit_amount_raw NUMERIC(40,0) NOT NULL DEFAULT 0,
    credit_amount_raw NUMERIC(40,0) NOT NULL DEFAULT 0,
    usd_rate NUMERIC(30,12) NOT NULL,
    CONSTRAINT chk_debit_credit_positive CHECK (debit_amount_raw >= 0 AND credit_amount_raw >= 0),
    CONSTRAINT chk_at_least_one_side CHECK (debit_amount_raw > 0 OR credit_amount_raw > 0)
);

-- ----------------------------------------------------------------------------
-- 5. DECOUPLED PAPER TRADING DESK (AUD-01 & AUD-04)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS paper_accounts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account_name TEXT NOT NULL UNIQUE DEFAULT 'Default Paper Desk',
    initial_virtual_lamports BIGINT NOT NULL DEFAULT 100000000000,
    current_virtual_lamports BIGINT NOT NULL DEFAULT 100000000000,
    allocated_virtual_lamports BIGINT NOT NULL DEFAULT 0,
    realized_pnl_lamports BIGINT NOT NULL DEFAULT 0,
    unrealized_pnl_lamports BIGINT NOT NULL DEFAULT 0,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS paper_positions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id UUID NOT NULL REFERENCES paper_accounts(id) ON DELETE CASCADE,
    asset_id UUID NOT NULL REFERENCES assets(id),
    pool_id UUID NOT NULL REFERENCES pools(id),
    status TEXT NOT NULL CHECK (status IN ('OPEN', 'CLOSED')),
    token_amount_raw NUMERIC(40,0) NOT NULL,
    cost_basis_lamports BIGINT NOT NULL,
    average_entry_price_sol NUMERIC(30,12) NOT NULL,
    current_market_price_sol NUMERIC(30,12) NOT NULL,
    unrealized_pnl_lamports BIGINT DEFAULT 0,
    take_profit_price_sol NUMERIC(30,12),
    stop_loss_price_sol NUMERIC(30,12),
    opened_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    closed_at TIMESTAMPTZ
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_paper_positions_unique_open 
ON paper_positions(account_id, asset_id) WHERE status = 'OPEN';

CREATE TABLE IF NOT EXISTS paper_orders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id UUID NOT NULL REFERENCES paper_accounts(id) ON DELETE CASCADE,
    client_order_id TEXT NOT NULL UNIQUE,
    asset_id UUID NOT NULL REFERENCES assets(id),
    pool_id UUID NOT NULL REFERENCES pools(id),
    side TEXT NOT NULL CHECK (side IN ('BUY', 'SELL')),
    order_type TEXT NOT NULL DEFAULT 'MARKET' CHECK (order_type IN ('MARKET', 'LIMIT', 'STOP_LOSS', 'TAKE_PROFIT')),
    input_mint TEXT NOT NULL,
    output_mint TEXT NOT NULL,
    input_amount_raw NUMERIC(40,0) NOT NULL,
    minimum_output_raw NUMERIC(40,0) NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('SUBMITTED', 'FILLED', 'PARTIALLY_FILLED', 'REJECTED', 'CANCELLED', 'EXPIRED')),
    rejection_reason TEXT,
    snapshot_quote_id BIGINT REFERENCES market_snapshots(id),
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS paper_execution_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id UUID NOT NULL REFERENCES paper_orders(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL CHECK (event_type IN ('FILL', 'PARTIAL_FILL', 'PRICE_IMPACT_ADJUSTMENT', 'FEE_DEDUCTION')),
    input_amount_raw NUMERIC(40,0) NOT NULL,
    output_amount_raw NUMERIC(40,0) NOT NULL,
    effective_price_sol NUMERIC(30,12) NOT NULL,
    effective_price_usd NUMERIC(30,12) NOT NULL,
    bid_ask_spread_bps INTEGER NOT NULL DEFAULT 10,
    liquidity_impact_bps INTEGER NOT NULL DEFAULT 25,
    simulated_slippage_bps INTEGER NOT NULL,
    simulated_network_fee_lamports BIGINT NOT NULL DEFAULT 5000,
    simulated_priority_fee_lamports BIGINT NOT NULL DEFAULT 50000,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS paper_account_events (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id UUID NOT NULL REFERENCES paper_accounts(id) ON DELETE CASCADE,
    order_id UUID REFERENCES paper_orders(id),
    event_category TEXT NOT NULL CHECK (event_category IN ('DEPOSIT', 'ALLOCATION', 'RELEASE', 'FILL_DEBIT', 'FILL_CREDIT', 'FEE')),
    delta_lamports BIGINT NOT NULL,
    balance_after_lamports BIGINT NOT NULL,
    note TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS paper_performance_snapshots (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id UUID NOT NULL REFERENCES paper_accounts(id) ON DELETE CASCADE,
    snapshot_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    simulation_model_version TEXT NOT NULL DEFAULT 'v1_linear_impact',
    total_equity_lamports BIGINT NOT NULL,
    closed_trades_count INTEGER NOT NULL DEFAULT 0,
    win_count INTEGER NOT NULL DEFAULT 0,
    loss_count INTEGER NOT NULL DEFAULT 0,
    win_rate NUMERIC(5,2) DEFAULT 0.00,
    profit_factor NUMERIC(8,4) DEFAULT 0.0000,
    max_drawdown_pct NUMERIC(5,2) DEFAULT 0.00
);

-- ----------------------------------------------------------------------------
-- 6. WORKFLOW LEDGER & OUTBOX (AUD-09)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS workflow_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workflow_id TEXT NOT NULL,
    execution_id TEXT NOT NULL UNIQUE,
    trigger_type TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('RUNNING', 'SUCCESS', 'FAILED', 'RETRYING')),
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    error_details JSONB
);

CREATE TABLE IF NOT EXISTS outbox_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_topic TEXT NOT NULL,
    payload JSONB NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING', 'PROCESSED', 'DEAD_LETTER')),
    retry_count INTEGER NOT NULL DEFAULT 0,
    next_retry_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- ----------------------------------------------------------------------------
-- 7. INDEXES
-- ----------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_snapshots_lookup ON market_snapshots(pool_id, observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_paper_orders_account_time ON paper_orders(account_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_paper_account_events_acct ON paper_account_events(account_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_live_orders_wallet ON live_orders(wallet_address, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ledger_lines_journal ON live_ledger_lines(journal_id);
