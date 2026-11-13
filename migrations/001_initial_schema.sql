-- ============================================================================
-- SOLANA MEMECOIN OS - DATABASE MIGRATION 001
-- PostgreSQL 14+ compatible
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE SCHEMA IF NOT EXISTS n8n_internal;

-- ----------------------------------------------------------------------------
-- 1. ASSETS & POOLS
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS assets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    mint_address TEXT UNIQUE NOT NULL,
    token_program TEXT NOT NULL DEFAULT 'TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA', -- SPL Token or Token-2022
    name TEXT,
    symbol TEXT,
    decimals SMALLINT NOT NULL DEFAULT 9,
    total_supply NUMERIC(40,0),
    metadata_uri TEXT,
    is_token_2022 BOOLEAN NOT NULL DEFAULT FALSE,
    token_2022_extensions JSONB DEFAULT '[]'::jsonb,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS pools (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    asset_id UUID NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    pool_address TEXT UNIQUE NOT NULL,
    dex_program TEXT NOT NULL, -- raydium_v4, raydium_clmm, orca_whirlpool, meteora_dlmm, pumpfun
    base_mint TEXT NOT NULL,
    quote_mint TEXT NOT NULL, -- e.g. So11111111111111111111111111111111111111112 (WSOL)
    created_slot BIGINT,
    created_at TIMESTAMPTZ,
    lp_status TEXT NOT NULL DEFAULT 'unknown', -- burned, locked, unlocked, unknown
    lp_burn_pct NUMERIC(5,2) DEFAULT 0.00,
    lp_locked_pct NUMERIC(5,2) DEFAULT 0.00,
    lp_lock_expiry TIMESTAMPTZ,
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS market_snapshots (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    asset_id UUID NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    pool_id UUID REFERENCES pools(id) ON DELETE CASCADE,
    captured_at TIMESTAMPTZ NOT NULL,
    price_usd NUMERIC(30,12) NOT NULL,
    price_sol NUMERIC(30,12),
    market_cap_usd NUMERIC(30,4),
    fdv_usd NUMERIC(30,4),
    liquidity_usd NUMERIC(30,4),
    liquidity_sol NUMERIC(30,4),
    volume_5m NUMERIC(30,4) DEFAULT 0,
    volume_1h NUMERIC(30,4) DEFAULT 0,
    volume_6h NUMERIC(30,4) DEFAULT 0,
    volume_24h NUMERIC(30,4) DEFAULT 0,
    price_change_5m NUMERIC(8,4),
    price_change_1h NUMERIC(8,4),
    price_change_24h NUMERIC(8,4),
    buys_5m INTEGER DEFAULT 0,
    sells_5m INTEGER DEFAULT 0,
    buys_1h INTEGER DEFAULT 0,
    sells_1h INTEGER DEFAULT 0,
    source TEXT NOT NULL DEFAULT 'dexscreener',
    CONSTRAINT uq_pool_captured_at UNIQUE(pool_id, captured_at)
);

CREATE TABLE IF NOT EXISTS ohlcv_candles (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    asset_id UUID NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    pool_id UUID NOT NULL REFERENCES pools(id) ON DELETE CASCADE,
    resolution TEXT NOT NULL, -- 1m, 5m, 15m, 1h, 4h, 1d
    timestamp TIMESTAMPTZ NOT NULL,
    open_price NUMERIC(30,12) NOT NULL,
    high_price NUMERIC(30,12) NOT NULL,
    low_price NUMERIC(30,12) NOT NULL,
    close_price NUMERIC(30,12) NOT NULL,
    volume NUMERIC(30,4) NOT NULL,
    trades_count INTEGER DEFAULT 0,
    CONSTRAINT uq_candle_window UNIQUE(pool_id, resolution, timestamp)
);

-- ----------------------------------------------------------------------------
-- 2. SECURITY SCANS & FORENSIC ANALYSIS
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS security_scans (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    asset_id UUID NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    provider TEXT NOT NULL, -- goplus, rugcheck, native_rpc
    risk_score NUMERIC(5,2) NOT NULL, -- 0 (Safe) to 100 (Deadly Rug)
    risk_level TEXT NOT NULL, -- low, medium, high, critical
    confidence TEXT NOT NULL, -- high, partial, insufficient
    mint_authority TEXT,
    freeze_authority TEXT,
    lp_burned BOOLEAN DEFAULT FALSE,
    lp_locked BOOLEAN DEFAULT FALSE,
    is_honeypot BOOLEAN DEFAULT FALSE,
    transfer_fee_bps INTEGER DEFAULT 0,
    top10_holder_pct NUMERIC(5,2),
    scanned_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL,
    raw_report JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS security_findings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    scan_id UUID NOT NULL REFERENCES security_scans(id) ON DELETE CASCADE,
    finding_code TEXT NOT NULL,
    severity TEXT NOT NULL, -- INFO, WARNING, CRITICAL_FAIL
    description TEXT NOT NULL,
    evidence JSONB
);

CREATE TABLE IF NOT EXISTS holder_snapshots (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    asset_id UUID NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    slot BIGINT NOT NULL,
    captured_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    total_holders INTEGER NOT NULL,
    top10_share_pct NUMERIC(5,2) NOT NULL,
    top20_share_pct NUMERIC(5,2) NOT NULL,
    deployer_balance_pct NUMERIC(5,2) NOT NULL,
    holders JSONB NOT NULL
);

-- ----------------------------------------------------------------------------
-- 3. WALLET INTELLIGENCE & CLUSTER GRAPH
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS wallets (
    address TEXT PRIMARY KEY,
    first_seen_slot BIGINT,
    first_seen_at TIMESTAMPTZ,
    last_seen_at TIMESTAMPTZ,
    category TEXT DEFAULT 'unknown', -- smart_money, sniper, bundler, whale, deployer, insider, ordinary
    sol_balance NUMERIC(30,9) DEFAULT 0,
    realized_pnl_usd NUMERIC(30,4) DEFAULT 0,
    win_rate NUMERIC(5,2),
    trades_count INTEGER DEFAULT 0,
    funding_source_address TEXT,
    metadata JSONB DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS wallet_clusters (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    cluster_type TEXT NOT NULL, -- same_funder, same_slot_bundler, transfer_ring, insider_pool
    confidence NUMERIC(5,2) NOT NULL,
    detected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    notes TEXT,
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
    buy_rank SMALLINT NOT NULL,
    slot BIGINT NOT NULL,
    block_time TIMESTAMPTZ NOT NULL,
    sol_spent NUMERIC(30,9) NOT NULL,
    tokens_bought NUMERIC(40,0) NOT NULL,
    current_status TEXT NOT NULL,
    holding_pct NUMERIC(5,2) NOT NULL,
    linked_to_deployer BOOLEAN DEFAULT FALSE,
    funder_address TEXT,
    evidence JSONB
);

-- ----------------------------------------------------------------------------
-- 4. NEWS, NARRATIVE & SOCIAL INTELLIGENCE
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS news_sources (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    source_type TEXT NOT NULL, -- rss, official_blog, regulatory, telegram_channel, x_curated
    feed_url TEXT UNIQUE NOT NULL,
    reliability_score NUMERIC(5,2) DEFAULT 80.0,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS news_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id UUID REFERENCES news_sources(id) ON DELETE SET NULL,
    title TEXT NOT NULL,
    url TEXT UNIQUE NOT NULL,
    published_at TIMESTAMPTZ NOT NULL,
    author TEXT,
    content_snippet TEXT,
    category TEXT NOT NULL,
    sentiment_score NUMERIC(5,2) DEFAULT 0.0,
    verification_status TEXT NOT NULL DEFAULT 'unverified',
    affected_tokens JSONB DEFAULT '[]'::jsonb,
    affected_sectors JSONB DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- ----------------------------------------------------------------------------
-- 5. RISK ENGINE, TRADE INTENTS & MANUAL APPROVAL
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS risk_policies (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT FALSE,
    environment TEXT NOT NULL DEFAULT 'paper',
    max_trade_sol NUMERIC(10,4) NOT NULL DEFAULT 0.50,
    max_daily_loss_sol NUMERIC(10,4) NOT NULL DEFAULT 2.00,
    max_open_positions INTEGER NOT NULL DEFAULT 3,
    max_slippage_bps INTEGER NOT NULL DEFAULT 250,
    max_price_impact_bps INTEGER NOT NULL DEFAULT 300,
    max_priority_fee_lamports BIGINT NOT NULL DEFAULT 10000000,
    min_liquidity_usd NUMERIC(15,2) NOT NULL DEFAULT 15000.00,
    max_risk_score NUMERIC(5,2) NOT NULL DEFAULT 35.0,
    require_burned_lp BOOLEAN NOT NULL DEFAULT TRUE,
    require_mint_freeze_revoked BOOLEAN NOT NULL DEFAULT TRUE,
    allowed_wallets JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS trade_intents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    asset_id UUID NOT NULL REFERENCES assets(id),
    pool_id UUID NOT NULL REFERENCES pools(id),
    environment TEXT NOT NULL DEFAULT 'paper',
    wallet_address TEXT NOT NULL,
    side TEXT NOT NULL,
    input_mint TEXT NOT NULL,
    output_mint TEXT NOT NULL,
    input_amount_raw NUMERIC(40,0) NOT NULL,
    expected_output_raw NUMERIC(40,0) NOT NULL,
    estimated_slippage_bps INTEGER NOT NULL,
    estimated_price_impact_bps INTEGER NOT NULL,
    policy_id UUID REFERENCES risk_policies(id),
    status TEXT NOT NULL DEFAULT 'PENDING_APPROVAL',
    expires_at TIMESTAMPTZ NOT NULL,
    risk_evaluation_snapshot JSONB NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS trade_approvals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    intent_id UUID NOT NULL REFERENCES trade_intents(id) ON DELETE CASCADE,
    approved_by TEXT NOT NULL DEFAULT 'MANUAL_USER',
    transaction_message_hash TEXT NOT NULL,
    approved_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ
);

-- ----------------------------------------------------------------------------
-- 6. DOUBLE-ENTRY LEDGER & AUDITABLE LIVE TRADING
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS chain_transactions (
    signature TEXT PRIMARY KEY,
    wallet_address TEXT NOT NULL,
    slot BIGINT NOT NULL,
    block_time TIMESTAMPTZ NOT NULL,
    confirmation_status TEXT NOT NULL,
    err_message TEXT,
    fee_lamports BIGINT NOT NULL,
    raw_meta JSONB
);

CREATE TABLE IF NOT EXISTS orders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    intent_id UUID NOT NULL REFERENCES trade_intents(id),
    environment TEXT NOT NULL DEFAULT 'paper',
    wallet_address TEXT NOT NULL,
    side TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS fills (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id UUID NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    signature TEXT REFERENCES chain_transactions(signature),
    input_mint TEXT NOT NULL,
    output_mint TEXT NOT NULL,
    input_amount_raw NUMERIC(40,0) NOT NULL,
    output_amount_raw NUMERIC(40,0) NOT NULL,
    fee_lamports BIGINT NOT NULL DEFAULT 0,
    effective_price_usd NUMERIC(30,12) NOT NULL,
    filled_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ledger_postings (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    transaction_signature TEXT,
    order_id UUID REFERENCES orders(id),
    wallet_address TEXT NOT NULL,
    account_type TEXT NOT NULL, -- ASSET_SOL, ASSET_TOKEN, EXPENSE_FEE, REALIZED_PNL
    mint_address TEXT NOT NULL,
    debit_amount_raw NUMERIC(40,0) NOT NULL DEFAULT 0,
    credit_amount_raw NUMERIC(40,0) NOT NULL DEFAULT 0,
    usd_valuation NUMERIC(30,8) NOT NULL,
    posted_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS trading_journal (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id UUID NOT NULL REFERENCES orders(id),
    asset_id UUID NOT NULL REFERENCES assets(id),
    strategy_name TEXT NOT NULL,
    entry_rationale TEXT NOT NULL,
    exit_rationale TEXT,
    tags TEXT[] DEFAULT '{}',
    opportunity_score NUMERIC(5,2),
    risk_score NUMERIC(5,2),
    net_pnl_usd NUMERIC(30,4),
    net_pnl_sol NUMERIC(30,9),
    created_at TIMESTAMPTZ DEFAULT now()
);

-- ----------------------------------------------------------------------------
-- 7. ISOLATED PAPER TRADING & STRATEGY VALIDATION
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS paper_accounts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account_name TEXT NOT NULL UNIQUE DEFAULT 'Default Paper Desk',
    initial_virtual_sol NUMERIC(30,9) NOT NULL DEFAULT 100.000000000,
    current_virtual_sol NUMERIC(30,9) NOT NULL DEFAULT 100.000000000,
    allocated_virtual_sol NUMERIC(30,9) NOT NULL DEFAULT 0.000000000,
    realized_pnl_sol NUMERIC(30,9) NOT NULL DEFAULT 0.000000000,
    unrealized_pnl_sol NUMERIC(30,9) NOT NULL DEFAULT 0.000000000,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS paper_positions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id UUID NOT NULL REFERENCES paper_accounts(id) ON DELETE CASCADE,
    asset_id UUID NOT NULL REFERENCES assets(id),
    pool_id UUID NOT NULL REFERENCES pools(id),
    status TEXT NOT NULL DEFAULT 'OPEN',
    token_amount_raw NUMERIC(40,0) NOT NULL,
    cost_basis_sol NUMERIC(30,9) NOT NULL,
    cost_basis_usd NUMERIC(30,4) NOT NULL,
    average_entry_price_sol NUMERIC(30,12) NOT NULL,
    current_market_price_sol NUMERIC(30,12) NOT NULL,
    unrealized_pnl_sol NUMERIC(30,9) DEFAULT 0,
    take_profit_price_sol NUMERIC(30,12),
    stop_loss_price_sol NUMERIC(30,12),
    opened_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    closed_at TIMESTAMPTZ,
    CONSTRAINT uq_paper_open_asset UNIQUE (account_id, asset_id, status)
);

CREATE TABLE IF NOT EXISTS paper_orders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id UUID NOT NULL REFERENCES paper_accounts(id) ON DELETE CASCADE,
    asset_id UUID NOT NULL REFERENCES assets(id),
    pool_id UUID NOT NULL REFERENCES pools(id),
    side TEXT NOT NULL,
    order_type TEXT NOT NULL DEFAULT 'MARKET',
    input_amount_sol NUMERIC(30,9),
    output_tokens_requested NUMERIC(40,0),
    simulated_slippage_bps INTEGER NOT NULL DEFAULT 100,
    simulated_price_impact_bps INTEGER NOT NULL DEFAULT 150,
    status TEXT NOT NULL DEFAULT 'SUBMITTED',
    rejection_reason TEXT,
    snapshot_price_sol NUMERIC(30,12) NOT NULL,
    simulated_fill_price_sol NUMERIC(30,12),
    simulated_fee_sol NUMERIC(30,9) DEFAULT 0.0005,
    submitted_at TIMESTAMPTZ DEFAULT now(),
    filled_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS paper_performance_snapshots (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id UUID NOT NULL REFERENCES paper_accounts(id) ON DELETE CASCADE,
    snapshot_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    total_equity_sol NUMERIC(30,9) NOT NULL,
    open_positions_count INTEGER NOT NULL,
    win_count INTEGER NOT NULL DEFAULT 0,
    loss_count INTEGER NOT NULL DEFAULT 0,
    win_rate NUMERIC(5,2) DEFAULT 0.00,
    profit_factor NUMERIC(8,4) DEFAULT 0.0000,
    max_drawdown_pct NUMERIC(5,2) DEFAULT 0.00
);

-- ----------------------------------------------------------------------------
-- 8. INDEXES
-- ----------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_assets_mint ON assets(mint_address);
CREATE INDEX IF NOT EXISTS idx_pools_asset ON pools(asset_id);
CREATE INDEX IF NOT EXISTS idx_pools_address ON pools(pool_address);
CREATE INDEX IF NOT EXISTS idx_snapshots_pool_time ON market_snapshots(pool_id, captured_at DESC);
CREATE INDEX IF NOT EXISTS idx_candles_lookup ON ohlcv_candles(pool_id, resolution, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_scans_asset_time ON security_scans(asset_id, scanned_at DESC);
CREATE INDEX IF NOT EXISTS idx_wallets_category ON wallets(category);
CREATE INDEX IF NOT EXISTS idx_news_published ON news_items(published_at DESC);
CREATE INDEX IF NOT EXISTS idx_trade_intents_pending ON trade_intents(status, expires_at);
CREATE INDEX IF NOT EXISTS idx_paper_positions_account ON paper_positions(account_id, status);
CREATE INDEX IF NOT EXISTS idx_paper_orders_account ON paper_orders(account_id, submitted_at DESC);
