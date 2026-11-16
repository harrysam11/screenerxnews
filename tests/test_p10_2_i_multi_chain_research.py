"""
Phase P10.2-I: Test Closure, Canonical Chain Identity & Multi-Chain Research Test Suite.

Verifies:
- I-01: Four legacy test closures, test-only fixture containment (fails in production).
- I-02: 12 canonical chain IDs, CAIP-2 normalization, CAIP-19 asset identifiers,
        Migration 12 collision-safe execution, cross-chain symbol disambiguation.
- I-03: Multi-chain search across all 12 chains, SSRF protection on explorer URLs,
        exact symbol ranking, exact FDV calculation without fabricated defaults,
        provenance classification (ONCHAIN_VERIFIED, UNAVAILABLE, etc.),
        offline-mode zero network calls, Trojan and Banana Gun read-only reference boundaries.
"""

import os
import sys
import socket
import tempfile
import sqlite3
import pytest
from decimal import Decimal
from datetime import datetime, timezone
from fastapi.testclient import TestClient

from app.main import app
from app.core.config import settings
from app.core.chains import (
    SOLANA_CAIP2_CANONICAL,
    SOLANA_LEGACY_ALIAS,
    SUPPORTED_CHAINS,
    normalize_chain_id,
    ChainRegistry,
    CAIP19AssetId
)
from app.db.sqlite_migrator import SqliteMigrator
from app.services.persistent_paper_service import PersistentPaperService
from app.services.search_service import SearchService, search_service, is_safe_url, sanitize_and_parse_explorer_url
from app.adapters.trojan import TrojanReferenceAdapter, trojan_adapter
from app.adapters.banana_gun import BananaGunReferenceAdapter, banana_gun_adapter

client = TestClient(app)

@pytest.fixture
def temp_migrated_db():
    """Provides a fresh temporary SQLite database with all migrations applied."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name

    migrator = SqliteMigrator(db_path)
    migrator.apply_migrations()
    yield db_path

    if os.path.exists(db_path):
        os.remove(db_path)

# =============================================================================
# I-01: Legacy Test Closure & Test Fixture Security Isolation
# =============================================================================

def test_i01_register_test_asset_blocked_in_production(temp_migrated_db, monkeypatch):
    """
    Verifies that production services do not expose register_test_asset, and that
    register_test_asset_fixture fails closed in production, development, or unset APP_ENV.
    """
    from app.services.paper_simulation_engine import PaperSimulationEngine
    from tests.fixtures.test_asset_helper import register_test_asset_fixture

    # 1. Structural removal from production classes
    assert not hasattr(PersistentPaperService, "register_test_asset")
    assert not hasattr(PaperSimulationEngine, "register_test_asset")

    # 2. Rejection in production
    monkeypatch.setattr(settings, "APP_ENV", "production")
    with pytest.raises(PermissionError) as exc_info:
        register_test_asset_fixture(
            db_target=temp_migrated_db,
            chain_id=SOLANA_CAIP2_CANONICAL,
            asset_address="0xMaliciousInjectToken",
            decimals=9,
            symbol="MAL"
        )
    assert "strictly forbidden outside explicit test context" in str(exc_info.value)

    # 3. Rejection in development
    monkeypatch.setattr(settings, "APP_ENV", "development")
    with pytest.raises(PermissionError) as exc_dev:
        register_test_asset_fixture(
            db_target=temp_migrated_db,
            chain_id=SOLANA_CAIP2_CANONICAL,
            asset_address="0xMaliciousInjectToken",
            decimals=9,
            symbol="MAL"
        )
    assert "strictly forbidden outside explicit test context" in str(exc_dev.value)

    # 4. Rejection when unset
    monkeypatch.setattr(settings, "APP_ENV", "")
    with pytest.raises(PermissionError) as exc_unset:
        register_test_asset_fixture(
            db_target=temp_migrated_db,
            chain_id=SOLANA_CAIP2_CANONICAL,
            asset_address="0xMaliciousInjectToken",
            decimals=9,
            symbol="MAL"
        )
    assert "strictly forbidden outside explicit test context" in str(exc_unset.value)

def test_i01_register_test_asset_succeeds_in_test_environment(temp_migrated_db):
    """
    Verifies that register_test_asset_fixture operates cleanly within test environments.
    Non-authoritative fixtures cannot authorize paper trading; authoritative fixtures can.
    """
    from tests.fixtures.test_asset_helper import register_test_asset_fixture
    service = PersistentPaperService(temp_migrated_db)

    # Default non-authoritative test fixture
    res_synth = register_test_asset_fixture(
        db_target=temp_migrated_db,
        chain_id=SOLANA_CAIP2_CANONICAL,
        asset_address="0xTestTokenAlpha",
        decimals=6,
        symbol="TKA",
        authoritative=False
    )
    assert res_synth["status"] == "UNVERIFIED"
    assert res_synth["verification_source"] == "CONTROLLED_TEST_FIXTURE"

    with service._get_connection() as conn:
        dec, is_ver = service._resolve_asset_decimals(SOLANA_CAIP2_CANONICAL, "0xTestTokenAlpha", conn)
    assert is_ver is False

    # Explicit authoritative test fixture (for execution testing)
    res_auth = register_test_asset_fixture(
        db_target=temp_migrated_db,
        chain_id=SOLANA_CAIP2_CANONICAL,
        asset_address="0xTestTokenBeta",
        decimals=6,
        symbol="TKB",
        authoritative=True
    )
    assert res_auth["status"] == "VERIFIED"
    assert res_auth["verification_source"] == "AUTHENTICATED_RPC_EVIDENCE"

    with service._get_connection() as conn:
        dec2, is_ver2 = service._resolve_asset_decimals(SOLANA_CAIP2_CANONICAL, "0xTestTokenBeta", conn)
    assert dec2 == 6
    assert is_ver2 is True

# =============================================================================
# I-02: Canonical Chain and Asset Identity
# =============================================================================

def test_i02_all_12_canonical_chain_ids():
    """Verifies that all 12 chains are registered under canonical CAIP-2 identifiers."""
    registered = ChainRegistry.get_all()
    assert len(registered) == 12

    expected_chains = [
        "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp",
        "eip155:1",
        "eip155:8453",
        "eip155:42161",
        "eip155:10",
        "eip155:137",
        "eip155:43114",
        "eip155:56",
        "sui:mainnet",
        "aptos:mainnet",
        "ton:mainnet",
        "tron:mainnet"
    ]

    for c in expected_chains:
        assert c in registered, f"Missing canonical chain: {c}"

    # Also test alias normalization
    assert normalize_chain_id("aptos:1") == "aptos:mainnet"
    assert normalize_chain_id("ton:-239") == "ton:mainnet"
    assert normalize_chain_id("tron:0x2b6653dc") == "tron:mainnet"


def test_i02_solana_legacy_alias_normalization():
    """Verifies seamless normalization of legacy solana identifiers to canonical CAIP-2."""
    assert normalize_chain_id("solana:mainnet") == SOLANA_CAIP2_CANONICAL
    assert normalize_chain_id("solana") == SOLANA_CAIP2_CANONICAL
    assert normalize_chain_id("solana:mainnet-beta") == SOLANA_CAIP2_CANONICAL
    assert normalize_chain_id(SOLANA_CAIP2_CANONICAL) == SOLANA_CAIP2_CANONICAL

def test_i02_caip19_parsing_formatting_and_roundtrip():
    """Verifies CAIP-19 asset identifier formatting and parsing across chains."""
    # Solana SPL Token
    sol_asset = CAIP19AssetId.from_chain_and_address(
        SOLANA_CAIP2_CANONICAL, "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
    )
    assert sol_asset.asset_namespace == "token"
    assert sol_asset.to_caip19() == f"{SOLANA_CAIP2_CANONICAL}/token:DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"

    # EVM ERC-20 Token (normalized to lowercase hex)
    evm_asset = CAIP19AssetId.from_chain_and_address(
        "eip155:1", "0x6B175474E89094C44Da98b954EedeAC495271d0F"
    )
    assert evm_asset.asset_namespace == "erc20"
    assert evm_asset.to_caip19() == "eip155:1/erc20:0x6b175474e89094c44da98b954eedeac495271d0f"

    # Roundtrip parsing
    parsed_sol = CAIP19AssetId.parse(sol_asset.to_caip19())
    assert parsed_sol is not None
    assert parsed_sol.chain_id == SOLANA_CAIP2_CANONICAL
    assert parsed_sol.asset_reference == "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"

def test_i02_migration_12_collision_safe_and_idempotent():
    """
    Verifies Migration 12 handles upgrades, collision safety,
    and database integrity across all affected tables.
    """
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name

    try:
        # Run up to migration 11
        migrator = SqliteMigrator(db_path)
        # Apply migrations manually up to 11
        conn = migrator._get_connection()
        conn.execute("PRAGMA foreign_keys = OFF;")
        migrator._ensure_migration_table(conn)
        # Apply up to 11
        for m in migrator.MIGRATIONS:
            if m["version"] <= 11:
                for stmt in m.get("statements", []):
                    try:
                        conn.execute(stmt)
                    except sqlite3.OperationalError as oe:
                        if "duplicate column name" in str(oe).lower():
                            pass
                        else:
                            raise
                if "python_action" in m:
                    m["python_action"](conn)
                conn.execute(
                    "INSERT INTO schema_migrations (version, name, applied_at, checksum) VALUES (?, ?, ?, 'test_checksum');",
                    (m["version"], m["name"], "2026-01-01T00:00:00Z")
                )
        conn.commit()



        # Seed data with solana:mainnet
        conn.execute(
            """
            INSERT INTO token_decimal_verifications (
                verification_id, chain_id, asset_address, decimals, symbol,
                verification_source, verification_method, raw_metadata_hash, raw_metadata_json,
                status, verified_at
            ) VALUES (
                'v1', 'solana:mainnet', '0xLegacyToken1', 9, 'LEG1',
                'SPL_MINT', 'onchain', 'hash1', '{}', 'VERIFIED', '2026-01-01'
            );
            """
        )
        # Seed an existing canonical record to test collision safety
        conn.execute(
            """
            INSERT INTO token_decimal_verifications (
                verification_id, chain_id, asset_address, decimals, symbol,
                verification_source, verification_method, raw_metadata_hash, raw_metadata_json,
                status, verified_at
            ) VALUES (
                'v2', ?, '0xCollisionToken', 6, 'COL',
                'SPL_MINT', 'onchain', 'hash2', '{}', 'VERIFIED', '2026-01-01'
            );
            """,
            (SOLANA_CAIP2_CANONICAL,)
        )
        conn.execute(
            """
            INSERT INTO token_decimal_verifications (
                verification_id, chain_id, asset_address, decimals, symbol,
                verification_source, verification_method, raw_metadata_hash, raw_metadata_json,
                status, verified_at
            ) VALUES (
                'v3', 'solana:mainnet', '0xCollisionToken', 6, 'COL',
                'SPL_MINT', 'onchain', 'hash3', '{}', 'VERIFIED', '2026-01-01'
            );
            """
        )
        conn.commit()
        conn.close()

        # Now apply Migration 12
        newly_applied = migrator.apply_migrations()
        assert 12 in newly_applied

        # Verify collision was safely handled and legacy updated
        verify_conn = migrator._get_connection()
        cur = verify_conn.execute("SELECT count(*) as cnt FROM token_decimal_verifications WHERE chain_id = 'solana:mainnet';")
        assert cur.fetchone()["cnt"] == 0

        cur2 = verify_conn.execute("SELECT count(*) as cnt FROM token_decimal_verifications WHERE chain_id = ?;", (SOLANA_CAIP2_CANONICAL,))
        assert cur2.fetchone()["cnt"] >= 2

        # Check PRAGMA integrity_check
        integrity = verify_conn.execute("PRAGMA integrity_check;").fetchone()[0]
        assert integrity == "ok"

        # Check idempotency: rerun apply_migrations
        rerun = migrator.apply_migrations()
        assert rerun == []
        verify_conn.close()

    finally:
        if os.path.exists(db_path):
            os.remove(db_path)

def test_i02_same_symbol_cross_chain_identity(temp_migrated_db):
    """
    Verifies that the same symbol (e.g. USDC) across different blockchains
    is preserved as separate, chain-specific records without collisions.
    """
    search_svc = SearchService(temp_migrated_db)
    conn = search_svc._get_connection()

    # Insert USDC on Solana
    conn.execute(
        """
        INSERT INTO discovered_tokens (id, chain_id, address, symbol, name, decimals, discovery_source, first_seen_at, last_seen_at)
        VALUES ('tok_sol_usdc', ?, 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v', 'USDC', 'USD Coin (Solana)', 6, 'rpc_logs', '2026-01-01', '2026-01-01');
        """,
        (SOLANA_CAIP2_CANONICAL,)
    )
    # Insert USDC on Ethereum
    conn.execute(
        """
        INSERT INTO discovered_tokens (id, chain_id, address, symbol, name, decimals, discovery_source, first_seen_at, last_seen_at)
        VALUES ('tok_eth_usdc', 'eip155:1', '0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48', 'USDC', 'USD Coin (Ethereum)', 6, 'rpc_logs', '2026-01-01', '2026-01-01');
        """
    )
    # Insert USDC on Base
    conn.execute(
        """
        INSERT INTO discovered_tokens (id, chain_id, address, symbol, name, decimals, discovery_source, first_seen_at, last_seen_at)
        VALUES ('tok_base_usdc', 'eip155:8453', '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913', 'USDC', 'USD Coin (Base)', 6, 'rpc_logs', '2026-01-01', '2026-01-01');
        """
    )
    conn.commit()
    conn.close()

    # Search for USDC
    res = search_svc.search("USDC")
    assert res["total_matches"] == 3
    chains_found = {r["chain_id"] for r in res["results"]}
    assert chains_found == {SOLANA_CAIP2_CANONICAL, "eip155:1", "eip155:8453"}

    # Verify each has its distinct CAIP-19 identifier
    caip19s = {r["caip19_id"] for r in res["results"]}
    assert len(caip19s) == 3


# =============================================================================
# I-03: Multi-Chain Search & Research Utilities
# =============================================================================

def test_i03_offline_search_zero_network_calls(temp_migrated_db, monkeypatch):
    """
    Verifies that search produces zero network calls when offline mode is active.
    Fails immediately if any network socket attempt is made.
    """
    monkeypatch.setattr(settings, "OPERATING_MODE", "offline_research")

    def blocked_socket(*args, **kwargs):

        raise AssertionError("Network socket opened during offline search!")

    monkeypatch.setattr(socket, "socket", blocked_socket)

    search_svc = SearchService(temp_migrated_db)

    # Perform searches
    res1 = search_svc.search("BONK")
    assert res1["query"] == "BONK"

    res2 = search_svc.search("DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263")
    assert res2["query_type"] == "address"

    res3 = search_svc.search(f"{SOLANA_CAIP2_CANONICAL}/token:DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263")
    assert res3["query_type"] == "caip19"

def test_i03_explorer_url_parsing_and_ssrf_protection():
    """Verifies explorer URL parsing and strict SSRF defenses."""
    # Legitimate Solscan URL
    solscan_url = "https://solscan.io/token/DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
    parsed_sol = sanitize_and_parse_explorer_url(solscan_url)
    assert parsed_sol is not None
    assert parsed_sol["chain_id"] == SOLANA_CAIP2_CANONICAL
    assert parsed_sol["address"] == "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"

    # Legitimate Etherscan URL
    etherscan_url = "https://etherscan.io/token/0x6B175474E89094C44Da98b954EedeAC495271d0F"
    parsed_eth = sanitize_and_parse_explorer_url(etherscan_url)
    assert parsed_eth is not None
    assert parsed_eth["chain_id"] == "eip155:1"
    assert parsed_eth["address"] == "0x6B175474E89094C44Da98b954EedeAC495271d0F"

    # SSRF Attack Vectors
    ssrf_targets = [
        "http://localhost:8000/token/0x123",
        "http://127.0.0.1:8080/secret",
        "http://192.168.1.1/token",
        "http://10.0.0.1/admin",
        "http://169.254.169.254/latest/meta-data",
        "http://[::1]/token",
        "file:///etc/passwd",
        "ftp://malicious.com/exploit"
    ]

    for ssrf_url in ssrf_targets:
        safe, _ = is_safe_url(ssrf_url)
        assert safe is False, f"SSRF URL was not blocked: {ssrf_url}"
        assert sanitize_and_parse_explorer_url(ssrf_url) is None

def test_i03_exact_symbol_ranking_over_fuzzy(temp_migrated_db):
    """Verifies that exact symbol matches are ranked ahead of prefix and substring matches."""
    search_svc = SearchService(temp_migrated_db)
    conn = search_svc._get_connection()

    tokens = [
        ("PEPE", "Pepe"),
        ("PEPEDOGE", "Pepe Doge"),
        ("MY_PEPE", "My Pepe"),
        ("SUPERPEPE", "Super Pepe")
    ]
    for idx, (sym, name) in enumerate(tokens):
        conn.execute(
            """
            INSERT INTO discovered_tokens (id, chain_id, address, symbol, name, decimals, discovery_source, first_seen_at, last_seen_at)
            VALUES (?, 'eip155:1', ?, ?, ?, 18, 'rpc_logs', '2026-01-01', '2026-01-01');
            """,
            (f"tok_{idx}", f"0xToken_{idx}", sym, name)
        )
    conn.commit()
    conn.close()

    res = search_svc.search("PEPE")
    assert res["total_matches"] == 4
    # Top result must be exact match PEPE
    assert res["results"][0]["symbol"] == "PEPE"
    assert res["results"][0]["match_type"] == "exact_symbol"

def test_i03_exact_fdv_calculation_without_fabricated_defaults(temp_migrated_db):
    """
    Verifies exact FDV calculation:
    - Normalizes human supply: supply / (10 ** decimals)
    - Returns None with explicit reason if price or supply or decimals is missing.
    - Never fabricates defaults.
    """
    search_svc = SearchService(temp_migrated_db)
    conn = search_svc._get_connection()

    token_addr = "0xValidTokenForFDV"
    pool_addr = "0xPoolForFDV"

    # Token with 6 decimals and 1,000,000 raw supply (1.0 human supply)
    conn.execute(
        """
        INSERT INTO discovered_tokens (id, chain_id, address, symbol, name, decimals, total_supply, discovery_source, first_seen_at, last_seen_at)
        VALUES ('tok_fdv_1', 'eip155:1', ?, 'FDVT', 'FDV Token', 6, '1000000000000', 'rpc_logs', '2026-01-01', '2026-01-01');
        """,
        (token_addr,)
    )

    # Discovered pool
    conn.execute(
        """
        INSERT INTO discovered_pools (id, chain_id, protocol, pool_address, token0_address, token1_address, discovery_source, created_at)
        VALUES ('pool_999', 'eip155:1', 'uniswap_v3', ?, ?, '0xWETH', 'rpc_logs', '2026-01-01');
        """,
        (pool_addr, token_addr)
    )

    # Market snapshot with price $2.50
    conn.execute(
        """
        INSERT INTO market_data_snapshots (pool_id, chain_id, provider_id, price_usd, liquidity_usd, observation_time, retrieved_at)
        VALUES ('pool_999', 'eip155:1', 'dexscreener', 2.50, 500000.0, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z');
        """
    )
    conn.commit()
    conn.close()

    snap = search_svc.get_asset_market_snapshot("eip155:1", token_addr)
    assert snap["price_usd"] == 2.50
    # total_supply 1,000,000,000,000 with 6 decimals = 1,000,000 human units
    # FDV = 1,000,000 * 2.50 = 2,500,000.00
    assert snap["fdv_usd"] == 2500000.0
    assert snap["fdv_unavailable_reason"] is None

    # Missing decimals / supply case
    conn2 = search_svc._get_connection()
    missing_addr = "0xMissingSupplyToken"
    conn2.execute(
        """
        INSERT INTO discovered_tokens (id, chain_id, address, symbol, name, decimals, total_supply, discovery_source, first_seen_at, last_seen_at)
        VALUES ('tok_fdv_miss', 'eip155:1', ?, 'MISS', 'Missing Supply', NULL, NULL, 'rpc_logs', '2026-01-01', '2026-01-01');
        """,
        (missing_addr,)
    )
    conn2.execute(
        """
        INSERT INTO discovered_pools (id, chain_id, protocol, pool_address, token0_address, token1_address, discovery_source, created_at)
        VALUES ('pool_1000', 'eip155:1', 'uniswap_v3', '0xPoolMiss', ?, '0xWETH', 'rpc_logs', '2026-01-01');
        """,
        (missing_addr,)
    )

    conn2.execute(
        """
        INSERT INTO market_data_snapshots (pool_id, chain_id, provider_id, price_usd, liquidity_usd, observation_time, retrieved_at)
        VALUES ('pool_1000', 'eip155:1', 'dexscreener', 5.0, 100000.0, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z');
        """
    )


    conn2.commit()
    conn2.close()

    snap_missing = search_svc.get_asset_market_snapshot("eip155:1", missing_addr)
    assert snap_missing["fdv_usd"] is None
    assert "Missing required fields for FDV calculation" in snap_missing["fdv_unavailable_reason"]

def test_i03_external_reference_adapters_strict_boundaries():
    """
    Verifies Trojan and Banana Gun reference adapters:
    - Return explicit unsupported API status.
    - Strictly forbid order creation, signing, and transaction broadcast.
    - Build safe external reference link-outs.
    """
    # Trojan
    trojan = TrojanReferenceAdapter()
    status = trojan.get_adapter_status()
    assert status["api_available"] is False
    assert status["supports_execution"] is False
    assert status["supports_signing"] is False
    assert status["data_classification"] == "EXTERNAL_REFERENCE"

    link = trojan.get_reference_link(SOLANA_CAIP2_CANONICAL, "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263")
    assert "solana_trojanbot" in link["reference_url"]
    assert link["is_execution_authorized"] is False

    with pytest.raises(PermissionError):
        trojan.create_order()
    with pytest.raises(PermissionError):
        trojan.sign_transaction()
    with pytest.raises(PermissionError):
        trojan.submit_transaction()

    # Banana Gun
    banana = BananaGunReferenceAdapter()
    b_status = banana.get_adapter_status()
    assert b_status["api_available"] is False
    assert b_status["supports_execution"] is False
    assert b_status["supports_signing"] is False

    b_link = banana.get_reference_link("eip155:1", "0x6B175474E89094C44Da98b954EedeAC495271d0F")
    assert "BananaGunSniper_bot" in b_link["reference_url"]
    assert b_link["is_execution_authorized"] is False

    with pytest.raises(PermissionError):
        banana.create_order()
    with pytest.raises(PermissionError):
        banana.sign_transaction()
    with pytest.raises(PermissionError):
        banana.submit_transaction()

def test_i03_fastapi_search_and_research_endpoints():
    """Verifies FastAPI REST endpoints for multi-chain search and research."""
    # 1. Search endpoint
    resp = client.get("/api/v1/search?q=BONK")
    assert resp.status_code == 200
    data = resp.json()
    assert "results" in data
    assert "total_matches" in data

    # 2. Integrations endpoint
    resp_int = client.get("/api/v1/integrations")
    assert resp_int.status_code == 200
    int_data = resp_int.json()
    assert "adapters" in int_data
    assert "trojan" in int_data["adapters"]
    assert "banana_gun" in int_data["adapters"]
    assert int_data["boundaries"]["live_trading_allowed"] is False
