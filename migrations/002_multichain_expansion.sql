-- ============================================================================
-- SOLANA MEMECOIN OS - MULTI-CHAIN EXPANSION (MIGRATION 002)
-- PostgreSQL 14+ compatible
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1. CHAIN REGISTRY & INFRASTRUCTURE (CAIP-2 Identifiers)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS chains (
    id TEXT PRIMARY KEY, -- CAIP-2: 'solana:mainnet', 'eip155:1', 'eip155:8453', 'eip155:56'
    namespace TEXT NOT NULL,
    reference TEXT NOT NULL,
    name TEXT NOT NULL,
    environment TEXT NOT NULL DEFAULT 'mainnet' CHECK (environment IN ('mainnet', 'testnet', 'devnet')),
    native_asset_symbol TEXT NOT NULL,
    native_asset_decimals SMALLINT NOT NULL DEFAULT 18,
    finality_blocks INTEGER NOT NULL DEFAULT 32,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    rpc_urls JSONB NOT NULL DEFAULT '[]'::jsonb,
    explorer_base_url TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS chain_networks (
    chain_id TEXT PRIMARY KEY REFERENCES chains(id) ON DELETE CASCADE,
    network_type TEXT NOT NULL, -- 'solana_cluster', 'evm_mainnet', 'evm_l2_rollup'
    consensus_mechanism TEXT NOT NULL DEFAULT 'pos',
    block_time_target_ms INTEGER NOT NULL DEFAULT 400,
    supports_eip1559 BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS chain_rpc_providers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    chain_id TEXT NOT NULL REFERENCES chains(id) ON DELETE CASCADE,
    provider_name TEXT NOT NULL,
    endpoint_url TEXT NOT NULL,
    priority SMALLINT NOT NULL DEFAULT 1,
    weight SMALLINT NOT NULL DEFAULT 100,
    rate_limit_rps INTEGER NOT NULL DEFAULT 10,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS chain_provider_health (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    provider_id UUID NOT NULL REFERENCES chain_rpc_providers(id) ON DELETE CASCADE,
    checked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    latency_ms INTEGER NOT NULL,
    is_healthy BOOLEAN NOT NULL DEFAULT TRUE,
    latest_block_number BIGINT,
    error_message TEXT
);

CREATE TABLE IF NOT EXISTS chain_sync_cursors (
    chain_id TEXT PRIMARY KEY REFERENCES chains(id) ON DELETE CASCADE,
    last_synced_block BIGINT NOT NULL DEFAULT 0,
    last_synced_hash TEXT,
    last_synced_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    is_syncing BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS chain_adapter_versions (
    chain_id TEXT PRIMARY KEY REFERENCES chains(id) ON DELETE CASCADE,
    adapter_class TEXT NOT NULL,
    version TEXT NOT NULL,
    capabilities JSONB NOT NULL DEFAULT '[]'::jsonb,
    registered_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Seed initial supported networks
INSERT INTO chains (id, namespace, reference, name, environment, native_asset_symbol, native_asset_decimals, finality_blocks, rpc_urls)
VALUES
    ('solana:mainnet', 'solana', 'mainnet', 'Solana', 'mainnet', 'SOL', 9, 32, '["https://api.mainnet-beta.solana.com"]'::jsonb),
    ('eip155:1', 'eip155', '1', 'Ethereum', 'mainnet', 'ETH', 18, 12, '["https://eth.llamarpc.com"]'::jsonb),
    ('eip155:8453', 'eip155', '8453', 'Base', 'mainnet', 'ETH', 18, 64, '["https://mainnet.base.org"]'::jsonb),
    ('eip155:56', 'eip155', '56', 'BNB Chain', 'mainnet', 'BNB', 18, 15, '["https://bsc-dataseed.binance.org"]'::jsonb)
ON CONFLICT (id) DO NOTHING;

-- ----------------------------------------------------------------------------
-- 2. EXTEND ASSETS & POOLS TO MULTI-CHAIN
-- ----------------------------------------------------------------------------
ALTER TABLE assets 
    ADD COLUMN IF NOT EXISTS chain_id TEXT REFERENCES chains(id) DEFAULT 'solana:mainnet',
    ADD COLUMN IF NOT EXISTS asset_address TEXT;

UPDATE assets SET asset_address = mint_address WHERE asset_address IS NULL;

ALTER TABLE assets
    ADD COLUMN IF NOT EXISTS is_proxy BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS implementation_address TEXT,
    ADD COLUMN IF NOT EXISTS is_pausable BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS is_blacklistable BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS buy_tax_bps INTEGER,
    ADD COLUMN IF NOT EXISTS sell_tax_bps INTEGER;

ALTER TABLE pools
    ADD COLUMN IF NOT EXISTS chain_id TEXT REFERENCES chains(id) DEFAULT 'solana:mainnet',
    ADD COLUMN IF NOT EXISTS factory_address TEXT;

-- ----------------------------------------------------------------------------
-- 3. EVM REORGANIZATION, BLOCKS & EVENT OBSERVATIONS
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS chain_blocks (
    chain_id TEXT NOT NULL REFERENCES chains(id) ON DELETE CASCADE,
    block_number BIGINT NOT NULL,
    block_hash TEXT NOT NULL,
    parent_hash TEXT NOT NULL,
    block_time TIMESTAMPTZ NOT NULL,
    is_canonical BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (chain_id, block_hash)
);

CREATE INDEX IF NOT EXISTS idx_chain_blocks_num ON chain_blocks(chain_id, block_number, is_canonical);

CREATE TABLE IF NOT EXISTS chain_reorg_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    chain_id TEXT NOT NULL REFERENCES chains(id) ON DELETE CASCADE,
    detected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    fork_block_number BIGINT NOT NULL,
    orphaned_block_hashes JSONB NOT NULL,
    canonical_block_hashes JSONB NOT NULL,
    reverted_events_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL CHECK (status IN ('DETECTED', 'REVERTED', 'RECONCILED'))
);

CREATE TABLE IF NOT EXISTS chain_event_observations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    chain_id TEXT NOT NULL REFERENCES chains(id) ON DELETE CASCADE,
    block_number BIGINT NOT NULL,
    block_hash TEXT NOT NULL,
    tx_hash TEXT NOT NULL,
    log_index INTEGER NOT NULL,
    event_signature TEXT,
    emitter_address TEXT NOT NULL,
    raw_topics JSONB NOT NULL DEFAULT '[]'::jsonb,
    raw_data TEXT,
    is_canonical BOOLEAN NOT NULL DEFAULT TRUE,
    observed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_chain_event_log UNIQUE (chain_id, tx_hash, log_index)
);

-- ----------------------------------------------------------------------------
-- 4. MULTI-CHAIN PAPER TRADING DESKS
-- ----------------------------------------------------------------------------
ALTER TABLE paper_accounts
    ADD COLUMN IF NOT EXISTS chain_id TEXT REFERENCES chains(id) DEFAULT 'solana:mainnet',
    ADD COLUMN IF NOT EXISTS native_symbol TEXT DEFAULT 'SOL';

ALTER TABLE paper_orders
    ADD COLUMN IF NOT EXISTS chain_id TEXT REFERENCES chains(id) DEFAULT 'solana:mainnet';

ALTER TABLE paper_positions
    ADD COLUMN IF NOT EXISTS chain_id TEXT REFERENCES chains(id) DEFAULT 'solana:mainnet';
