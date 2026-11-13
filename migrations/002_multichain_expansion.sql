-- ============================================================================
-- SOLANA MEMECOIN OS - MULTI-CHAIN EXPANSION (MIGRATION 002)
-- PostgreSQL 14+ compatible
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1. CHAIN REGISTRY (CAIP-2 Identifiers)
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
-- 3. EVM REORGANIZATION & BLOCK CANONICALITY TRACKER
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
