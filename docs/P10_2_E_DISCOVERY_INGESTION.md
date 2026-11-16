# Phase P10.2-E: Real Multi-Chain Discovery & Ingestion Engine

## 1. Overview & Objectives

Phase P10.2-E replaces mock and sample token discovery with an automated, SQLite-backed discovery and ingestion pipeline spanning all 12 registered blockchains.

Key architectural properties:
- **Comprehensive DEX & Factory Coverage**: Automated mapping of DEX factories, routers, program IDs, and pool creation event signatures across Solana, 7 EVM chains, Sui, Aptos, TON, and TRON.
- **Strict Deduplication & Idempotency**: Pool and token entries are keyed uniquely by `(chain_id, pool_address)` and `(chain_id, address)`. Re-ingesting identical events updates timestamps without creating duplicate records.
- **Checkpoint & Lag Tracking**: `ingestion_checkpoints` stores high-water marks (`last_block_or_slot`) and ingestion lag per `(chain_id, protocol, source_type)`. Enables seamless crash-recovery and restart resumption.
- **Blockchain Reorganization Rollback**: `handle_reorg_event()` detects chain reorgs, invalidates orphaned pools (`is_canonical = 0`, `status = 'REORGED'`), creates audit trails in `reorg_invalidation_events`, and rolls back checkpoints.
- **Exact FDV & Non-Fabricated Data**: Market data snapshots compute Fully Diluted Valuation (FDV) strictly as `price_usd * total_supply` only when both verified values exist. Missing data remains `None` (no synthetic placeholders or hallucinated liquidity).
- **Strict Offline Mode Enforcement**: All ingestion and enrichment workflows respect `settings.is_offline` and `provider_manager.is_offline_mode()`. Network requests are intercepted and blocked when operating in offline mode.

---

## 2. SQLite Schema Migration 6 (`006_real_multichain_discovery_and_ingestion`)

The migration creates five dedicated relational tables with indexing:

```sql
-- 1. Discovered Tokens
CREATE TABLE IF NOT EXISTS discovered_tokens (
    id TEXT PRIMARY KEY,
    chain_id TEXT NOT NULL,
    address TEXT NOT NULL,
    symbol TEXT NOT NULL DEFAULT 'UNKNOWN',
    name TEXT NOT NULL DEFAULT 'Unknown Token',
    decimals INTEGER,
    total_supply TEXT,
    is_verified INTEGER NOT NULL DEFAULT 0,
    discovery_source TEXT NOT NULL,
    first_seen_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    last_seen_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    metadata_json TEXT,
    UNIQUE(chain_id, address)
);

-- 2. Discovered Liquidity Pools
CREATE TABLE IF NOT EXISTS discovered_pools (
    id TEXT PRIMARY KEY,
    chain_id TEXT NOT NULL,
    pool_address TEXT NOT NULL,
    protocol TEXT NOT NULL,
    factory_address TEXT,
    token0_address TEXT NOT NULL,
    token1_address TEXT NOT NULL,
    fee_tier_bps INTEGER DEFAULT 30,
    block_number INTEGER,
    slot INTEGER,
    tx_hash TEXT,
    log_index INTEGER,
    discovery_source TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    is_canonical INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK(status IN ('ACTIVE', 'REORGED', 'INACTIVE')),
    UNIQUE(chain_id, pool_address)
);

-- 3. Market Data Snapshots
CREATE TABLE IF NOT EXISTS market_data_snapshots (
    id TEXT PRIMARY KEY,
    pool_id TEXT NOT NULL,
    chain_id TEXT NOT NULL,
    provider_id TEXT NOT NULL,
    price_usd REAL,
    liquidity_usd REAL,
    volume_24h_usd REAL,
    reserves_token0 REAL,
    reserves_token1 REAL,
    price_impact_bps REAL,
    fdv_usd REAL,
    confidence_score REAL NOT NULL DEFAULT 1.0,
    data_freshness TEXT NOT NULL DEFAULT 'REALTIME',
    observation_time TEXT NOT NULL,
    retrieved_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    raw_payload_sanitized TEXT
);

-- 4. Ingestion Checkpoints
CREATE TABLE IF NOT EXISTS ingestion_checkpoints (
    id TEXT PRIMARY KEY,
    chain_id TEXT NOT NULL,
    protocol TEXT NOT NULL,
    source_type TEXT NOT NULL,
    last_block_or_slot INTEGER NOT NULL DEFAULT 0,
    last_block_hash TEXT,
    last_event_id TEXT,
    lag_blocks_or_slots INTEGER DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE(chain_id, protocol, source_type)
);

-- 5. Reorganization Invalidation Events
CREATE TABLE IF NOT EXISTS reorg_invalidation_events (
    id TEXT PRIMARY KEY,
    chain_id TEXT NOT NULL,
    block_number INTEGER NOT NULL,
    block_hash TEXT NOT NULL,
    affected_pools_count INTEGER NOT NULL DEFAULT 0,
    invalidated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    reason TEXT NOT NULL
);
```

---

## 3. Protocol & Factory Registry Coverage Matrix

| Network | Chain ID | Protocols Supported | Factory / Program ID | Ingestion Event |
|---|---|---|---|---|
| Solana | `solana:mainnet` | Raydium AMM V4, Raydium CPMM, Pump.fun, Orca Whirlpool | `675kPX9M...`, `CPMMoo8L...`, `6EF8rrec...`, `whirLbMi...` | `initialize2`, `initialize`, `Create`, `initialize_pool` |
| Ethereum | `eip155:1` | Uniswap V2, Uniswap V3 | `0x5C69bEe7...`, `0x1F98431c...` | `PairCreated`, `PoolCreated` |
| Base | `eip155:8453` | Aerodrome V2, Uniswap V3 | `0x420DD381...`, `0x33128a8f...` | `PairCreated`, `PoolCreated` |
| BNB Chain | `eip155:56` | PancakeSwap V2, PancakeSwap V3 | `0xcA143Ce3...`, `0x0BFbCF9f...` | `PairCreated`, `PoolCreated` |
| Arbitrum | `eip155:42161` | Camelot V2, Uniswap V3 | `0x6EcDa838...`, `0x1F98431c...` | `PairCreated`, `PoolCreated` |
| Optimism | `eip155:10` | Velodrome V2, Uniswap V3 | `0xF1046053...`, `0x1F98431c...` | `PairCreated`, `PoolCreated` |
| Polygon | `eip155:137` | QuickSwap V2, Uniswap V3 | `0x57573714...`, `0x1F98431c...` | `PairCreated`, `PoolCreated` |
| Avalanche | `eip155:43114` | TraderJoe V2.1, Pangolin | `0x8e42f2F4...`, `0xefa94DE7...` | `LBPairCreated`, `PairCreated` |
| Sui | `sui:mainnet` | Cetus CLMM, Turbos Finance | `0x1eabed72...`, `0x91bfbc38...` | `CreatePoolEvent`, `PoolCreatedEvent` |
| Aptos | `aptos:mainnet` | Liquidswap (Pontem) | `0x190d4426...` | `CreatePairEvent` |
| TON | `ton:mainnet` | DeDust, STON.fi | `EQB-ajMy...`, `EQB3ncyB...` | `create_pool` |
| TRON | `tron:mainnet` | SunSwap V2 | `416b23d9...` | `PairCreated` |

---

## 4. API Specification

- `GET /api/v1/discovery/tokens` — List discovered tokens with market data.
- `GET /api/v1/discovery/pools` — List discovered liquidity pools with filtering by chain, protocol, and canonical status.
- `GET /api/v1/discovery/pools/{pool_address}` — Get pool details by address and chain.
- `GET /api/v1/discovery/market-data/{pool_address}` — Get latest market data snapshot.
- `GET /api/v1/discovery/checkpoints` — Ingestion checkpoints and lag status.
- `GET /api/v1/discovery/protocols` — Protocol and factory registry coverage metadata.
- `POST /api/v1/discovery/ingest-pool` — Ingest a newly discovered pool event.
- `POST /api/v1/discovery/market-data` — Ingest a market data snapshot with exact FDV.
- `POST /api/v1/discovery/reorg` — Trigger blockchain reorganization rollback.
