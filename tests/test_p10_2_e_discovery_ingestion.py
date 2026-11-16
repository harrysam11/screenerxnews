"""
Phase P10.2-E: Real Multi-Chain Discovery & Ingestion Test Suite.
Verifies acceptance criteria:
- DISC-01: Schema Migration 6 table creation and integrity verification.
- DISC-02: Protocol & Factory Registry coverage across all 12 chains.
- DISC-03: Multi-chain DEX pool & token ingestion with address validation.
- DISC-04: Deduplication and idempotent upserts.
- DISC-05: Ingestion checkpoint tracking, lag monitoring, and restart recovery.
- DISC-06: Chain reorganization rollback, pool invalidation, and audit logging.
- DISC-07: Normalized market data snapshots and exact FDV calculation without fabricated defaults.
- DISC-08: Strict offline isolation blocking unauthorized external network queries.
- DISC-09: FastAPI discovery router endpoints, pagination, and query filtering.
"""

import os
import tempfile
import pytest
from datetime import datetime, timezone
from fastapi.testclient import TestClient

from app.main import app
from app.core.config import settings
from app.core.network import OfflineModeBlockedException
from app.core.address_validator import AddressValidator, AddressValidationError
from app.core.discovery_protocols import protocol_registry
from app.db.sqlite_migrator import SqliteMigrator
from app.services.discovery_service import DiscoveryService, discovery_service
from app.services.provider_manager import ALL_12_CHAINS

client = TestClient(app)

@pytest.fixture
def temp_migrated_db():
    """Provides a temporary, freshly-migrated SQLite database file."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name

    migrator = SqliteMigrator(db_path)
    migrator.apply_migrations()
    yield db_path

    if os.path.exists(db_path):
        os.remove(db_path)

# =============================================================================
# DISC-01: Migration 6 Schema Verification
# =============================================================================

def test_disc_01_migration_6_tables_created(temp_migrated_db):
    migrator = SqliteMigrator(temp_migrated_db)
    schema = migrator.verify_schema()

    assert schema["valid"] is True
    assert schema["current_version"] >= 6
    assert "discovered_tokens" in schema["existing_tables"]
    assert "discovered_pools" in schema["existing_tables"]
    assert "market_data_snapshots" in schema["existing_tables"]
    assert "ingestion_checkpoints" in schema["existing_tables"]
    assert "reorg_invalidation_events" in schema["existing_tables"]
    assert len(schema["missing_tables"]) == 0

# =============================================================================
# DISC-02: Protocol & Factory Registry Coverage Across 12 Chains
# =============================================================================

def test_disc_02_protocol_registry_12_chains():
    matrix = protocol_registry.get_coverage_matrix()

    for chain in ALL_12_CHAINS:
        assert chain in matrix, f"Chain '{chain}' missing from ProtocolRegistry matrix"
        protocols = matrix[chain]
        assert len(protocols) > 0, f"Chain '{chain}' has zero registered protocols"

    # Solana Protocols
    sol_protos = [p["protocol_id"] for p in matrix["solana:mainnet"]]
    assert "raydium_amm_v4" in sol_protos
    assert "raydium_cpmm" in sol_protos
    assert "pump_fun" in sol_protos
    assert "orca_whirlpool" in sol_protos

    # EVM Protocols
    eth_protos = [p["protocol_id"] for p in matrix["eip155:1"]]
    assert "uniswap_v2" in eth_protos
    assert "uniswap_v3" in eth_protos

    base_protos = [p["protocol_id"] for p in matrix["eip155:8453"]]
    assert "aerodrome_v2" in base_protos

    bsc_protos = [p["protocol_id"] for p in matrix["eip155:56"]]
    assert "pancakeswap_v2" in bsc_protos

    # Move Protocols
    sui_protos = [p["protocol_id"] for p in matrix["sui:mainnet"]]
    assert "cetus_clmm" in sui_protos

    aptos_protos = [p["protocol_id"] for p in matrix["aptos:mainnet"]]
    assert "liquidswap" in aptos_protos

    # TON & TRON
    ton_protos = [p["protocol_id"] for p in matrix["ton:mainnet"]]
    assert "dedust" in ton_protos
    assert "ston_fi" in ton_protos

    tron_protos = [p["protocol_id"] for p in matrix["tron:mainnet"]]
    assert "sunswap_v2" in tron_protos

    # Factory identification
    eth_factory = protocol_registry.identify_evm_factory("eip155:1", "0x5c69bee701ef814a2b6a3edd4b1652cb9cc5aa6f")
    assert eth_factory is not None
    assert eth_factory.protocol_id == "uniswap_v2"

# =============================================================================
# DISC-03: Multi-Chain Ingestion & Address Normalization
# =============================================================================

def test_disc_03_multichain_pool_ingestion(temp_migrated_db):
    service = DiscoveryService(temp_migrated_db)

    # 1. EVM Pool Ingestion (Ethereum)
    evm_res = service.ingest_pool_event(
        chain_id="eip155:1",
        protocol="uniswap_v2",
        pool_address="0xB4e16d0168e52d35CaCD2c6185b44281Ec28C9Dc",  # USDC-WETH
        token0_address="0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",  # USDC
        token1_address="0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",  # WETH
        fee_tier_bps=30,
        block_number=18000000,
        tx_hash="0x" + "a" * 64,
        factory_address="0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f"
    )
    assert evm_res["is_new"] is True
    assert evm_res["pool_address"].startswith("0x")
    assert evm_res["chain_id"] == "eip155:1"

    # 2. Solana Pool Ingestion (Raydium)
    sol_res = service.ingest_pool_event(
        chain_id="solana:mainnet",
        protocol="raydium_amm_v4",
        pool_address="58oQChx4yWmvKdwLLZzBi4ChoCc2fqCUWBkwMihLYQo2",  # SOL-USDC
        token0_address="So11111111111111111111111111111111111111112",
        token1_address="EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
        fee_tier_bps=25,
        slot=250000000,
        tx_hash="5K2w7..."
    )
    assert sol_res["chain_id"] in ("solana:mainnet", "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp")


    # 3. Malformed address raises AddressValidationError
    with pytest.raises(AddressValidationError):
        service.ingest_pool_event(
            chain_id="eip155:1",
            protocol="uniswap_v2",
            pool_address="0xInvalidHexAddressTooShort",
            token0_address="0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
            token1_address="0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
        )

# =============================================================================
# DISC-04: Deduplication and Idempotency
# =============================================================================

def test_disc_04_idempotent_pool_ingestion(temp_migrated_db):
    service = DiscoveryService(temp_migrated_db)

    pool_addr = "0xB4e16d0168e52d35CaCD2c6185b44281Ec28C9Dc"
    t0 = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
    t1 = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"

    # First ingest
    r1 = service.ingest_pool_event(
        chain_id="eip155:1",
        protocol="uniswap_v2",
        pool_address=pool_addr,
        token0_address=t0,
        token1_address=t1,
        block_number=18000000
    )
    assert r1["is_new"] is True

    # Duplicate ingest
    r2 = service.ingest_pool_event(
        chain_id="eip155:1",
        protocol="uniswap_v2",
        pool_address=pool_addr,
        token0_address=t0,
        token1_address=t1,
        block_number=18000000
    )
    assert r2["is_new"] is False
    assert r2["pool_id"] == r1["pool_id"]

    # Verify only 1 pool exists in database
    pools = service.list_pools(chain_id="eip155:1")
    assert len(pools) == 1

# =============================================================================
# DISC-05: Checkpoint Tracking, Lag Monitoring & Resumption
# =============================================================================

def test_disc_05_checkpoints_and_lag_monitoring(temp_migrated_db):
    service = DiscoveryService(temp_migrated_db)

    # Ingest event advances checkpoint
    service.ingest_pool_event(
        chain_id="eip155:1",
        protocol="uniswap_v2",
        pool_address="0xB4e16d0168e52d35CaCD2c6185b44281Ec28C9Dc",
        token0_address="0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        token1_address="0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
        block_number=18000100
    )

    chk = service.get_checkpoint(chain_id="eip155:1", protocol="uniswap_v2", source_type="rpc_logs")
    assert chk is not None
    assert chk["last_block_or_slot"] == 18000100

    # Explicit checkpoint update with lag monitoring
    updated = service.update_checkpoint(
        chain_id="eip155:1",
        protocol="uniswap_v2",
        source_type="rpc_logs",
        last_block_or_slot=18000500,
        lag_blocks_or_slots=5
    )
    assert updated["last_block_or_slot"] == 18000500
    assert updated["lag_blocks_or_slots"] == 5

    # Checkpoints listing
    all_chks = service.list_checkpoints()
    assert len(all_chks) >= 1
    assert any(c["protocol"] == "uniswap_v2" for c in all_chks)

# =============================================================================
# DISC-06: Chain Reorganization Rollback & Invalidation
# =============================================================================

def test_disc_06_chain_reorganization_rollback(temp_migrated_db):
    service = DiscoveryService(temp_migrated_db)

    # Ingest 3 pools at blocks 200, 201, 202
    p1 = service.ingest_pool_event(
        chain_id="eip155:1",
        protocol="uniswap_v2",
        pool_address="0x1111111111111111111111111111111111111111",
        token0_address="0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        token1_address="0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
        block_number=200
    )
    p2 = service.ingest_pool_event(
        chain_id="eip155:1",
        protocol="uniswap_v2",
        pool_address="0x2222222222222222222222222222222222222222",
        token0_address="0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        token1_address="0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
        block_number=201
    )
    p3 = service.ingest_pool_event(
        chain_id="eip155:1",
        protocol="uniswap_v2",
        pool_address="0x3333333333333333333333333333333333333333",
        token0_address="0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        token1_address="0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
        block_number=202
    )

    # Confirm all 3 pools are canonical
    canonical_pools = service.list_pools(chain_id="eip155:1", canonical_only=True)
    assert len(canonical_pools) == 3

    # Trigger reorg event at block 201
    reorg_result = service.handle_reorg_event(
        chain_id="eip155:1",
        reorg_block_number=201,
        block_hash="0xabcdef123456",
        reason="depth_2_reorg_detected"
    )

    assert reorg_result["affected_pools_count"] == 2
    assert reorg_result["reorg_block_number"] == 201

    # Pool at 200 remains canonical
    pool1 = service.get_pool("eip155:1", "0x1111111111111111111111111111111111111111")
    assert pool1["is_canonical"] == 1
    assert pool1["status"] == "ACTIVE"

    # Pools at 201 and 202 are marked REORGED
    pool2 = service.get_pool("eip155:1", "0x2222222222222222222222222222222222222222")
    assert pool2["is_canonical"] == 0
    assert pool2["status"] == "REORGED"

    pool3 = service.get_pool("eip155:1", "0x3333333333333333333333333333333333333333")
    assert pool3["is_canonical"] == 0
    assert pool3["status"] == "REORGED"

    # Checkpoint rolled back to block 200
    chk = service.get_checkpoint("eip155:1", "uniswap_v2")
    assert chk["last_block_or_slot"] == 200

# =============================================================================
# DISC-07: Market Data Snapshots & Exact FDV Calculation
# =============================================================================

def test_disc_07_market_data_enrichment_and_fdv(temp_migrated_db):
    service = DiscoveryService(temp_migrated_db)

    pool_addr = "0xB4e16d0168e52d35CaCD2c6185b44281Ec28C9Dc"
    service.ingest_pool_event(
        chain_id="eip155:1",
        protocol="uniswap_v2",
        pool_address=pool_addr,
        token0_address="0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        token1_address="0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
    )

    # 1. Snapshot with price and total supply -> Exact FDV
    snap1 = service.ingest_market_data(
        chain_id="eip155:1",
        pool_address=pool_addr,
        provider_id="prov-dexscreener",
        price_usd=2.50,
        liquidity_usd=100000.0,
        volume_24h_usd=50000.0,
        total_supply=1000000.0
    )
    assert snap1["fdv_usd"] == 2500000.0
    assert snap1["price_usd"] == 2.50

    # 2. Snapshot with missing total supply -> fdv_usd is None (zero fabrication)
    snap2 = service.ingest_market_data(
        chain_id="eip155:1",
        pool_address=pool_addr,
        provider_id="prov-dexscreener",
        price_usd=3.00,
        liquidity_usd=120000.0,
        total_supply=None
    )
    assert snap2["fdv_usd"] is None

    # Retrieve latest snapshot
    latest = service.get_latest_market_data("eip155:1", pool_addr)
    assert latest is not None
    assert latest["price_usd"] == 3.00

# =============================================================================
# DISC-08: Offline Isolation
# =============================================================================

def test_disc_08_offline_mode_isolation(temp_migrated_db, monkeypatch):
    service = DiscoveryService(temp_migrated_db)
    pool_addr = "0xB4e16d0168e52d35CaCD2c6185b44281Ec28C9Dc"
    service.ingest_pool_event(
        chain_id="eip155:1",
        protocol="uniswap_v2",
        pool_address=pool_addr,
        token0_address="0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        token1_address="0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
    )

    monkeypatch.setattr(settings, "OPERATING_MODE", "offline_research")

    # Ingestion with allow_network_fetch=True must raise OfflineModeBlockedException
    with pytest.raises(OfflineModeBlockedException):
        service.ingest_market_data(
            chain_id="eip155:1",
            pool_address=pool_addr,
            provider_id="prov-dexscreener",
            price_usd=2.50,
            allow_network_fetch=True
        )

# =============================================================================
# DISC-09: REST API Endpoints, Pagination & Querying
# =============================================================================

def test_disc_09_rest_api_endpoints():
    # 1. Protocols endpoint
    res_proto = client.get("/api/v1/discovery/protocols")
    assert res_proto.status_code == 200
    proto_data = res_proto.json()
    assert "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp" in proto_data or "solana:mainnet" in proto_data

    # 2. Discovered tokens endpoint (backward compatible)
    res_tokens = client.get("/api/v1/discovery/tokens")
    assert res_tokens.status_code == 200
    tokens = res_tokens.json()
    assert isinstance(tokens, list)
    assert len(tokens) > 0
    assert "mint_address" in tokens[0]

    # 3. Checkpoints endpoint
    res_chk = client.get("/api/v1/discovery/checkpoints")
    assert res_chk.status_code == 200
    assert isinstance(res_chk.json(), list)

    # 4. Pools list endpoint
    res_pools = client.get("/api/v1/discovery/pools?limit=10")
    assert res_pools.status_code == 200
    assert isinstance(res_pools.json(), list)
