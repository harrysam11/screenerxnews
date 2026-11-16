"""
Phase P10.2-I.2 Comprehensive Test Suite:
- I.2-01: Structurally Isolated Test Fixtures & Decimal Provenance Trust Boundary
- I.2-02: Migration 12 Data-Safety Repair & Forward-Only Migration 13 Verification
- I.2-03: Decimal Provenance & Static Seed Reclassification (BONK & seeds unverified)
- I.2-04: Complete EVM RPC Chain-ID Support (All 7 registered EVM networks)
"""

import os
import sys
import json
import sqlite3
import tempfile
import asyncio
import pytest
from decimal import Decimal
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock, AsyncMock

from app.core.config import settings
from app.core.chains import (
    SUPPORTED_CHAINS,
    SOLANA_CAIP2_CANONICAL,
    SOLANA_LEGACY_ALIAS,
    normalize_chain_id
)
from app.core.network import OfflineModeBlockedException
from app.db.sqlite_migrator import SqliteMigrator
from app.services.persistent_paper_service import PersistentPaperService
from app.services.paper_simulation_engine import PaperSimulationEngine
from app.services.evm_rpc import EvmRpcClient
from app.services.evm_provider_manager import evm_provider_manager
from app.schemas.contracts import (
    PaperOrderRequest,
    DecimalProvenanceSource,
    AUTHORITATIVE_DECIMAL_PROVENANCE_SOURCES,
    UNAUTHORIZED_DECIMAL_PROVENANCE_SOURCES
)
from tests.fixtures.test_asset_helper import register_test_asset_fixture


@pytest.fixture
def isolated_i2_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    migrator = SqliteMigrator(path)
    migrator.apply_migrations()
    yield path
    if os.path.exists(path):
        os.remove(path)


# =============================================================================
# I.2-01: Structurally Isolated Test Fixtures & Provenance Trust Policy
# =============================================================================

def test_i2_01_structural_removal_from_production_interfaces():
    """Verifies that production services do not expose register_test_asset."""
    assert not hasattr(PersistentPaperService, "register_test_asset")
    assert not hasattr(PaperSimulationEngine, "register_test_asset")


def test_i2_01_fixture_rejected_when_app_env_unset(isolated_i2_db, monkeypatch):
    """Verifies test fixture registration fails closed when APP_ENV is empty or unset."""
    monkeypatch.setattr(settings, "APP_ENV", "")
    with pytest.raises(PermissionError) as exc_info:
        register_test_asset_fixture(isolated_i2_db, "solana:mainnet", "0xFailUnset", 9)
    assert "strictly forbidden outside explicit test context" in str(exc_info.value)


def test_i2_01_fixture_rejected_in_development(isolated_i2_db, monkeypatch):
    """Verifies test fixture registration fails closed when APP_ENV is development."""
    monkeypatch.setattr(settings, "APP_ENV", "development")
    with pytest.raises(PermissionError) as exc_info:
        register_test_asset_fixture(isolated_i2_db, "solana:mainnet", "0xFailDev", 9)
    assert "strictly forbidden outside explicit test context" in str(exc_info.value)


def test_i2_01_fixture_rejected_in_production(isolated_i2_db, monkeypatch):
    """Verifies test fixture registration fails closed when APP_ENV is production."""
    monkeypatch.setattr(settings, "APP_ENV", "production")
    with pytest.raises(PermissionError) as exc_info:
        register_test_asset_fixture(isolated_i2_db, "solana:mainnet", "0xFailProd", 9)
    assert "strictly forbidden outside explicit test context" in str(exc_info.value)


def test_i2_01_controlled_test_fixture_cannot_authorize_paper_orders(isolated_i2_db):
    """Verifies that non-authoritative CONTROLLED_TEST_FIXTURE cannot authorize paper orders."""
    service = PersistentPaperService(isolated_i2_db)
    asset_addr = "0xSyntheticTestAsset111111111111111111111"

    # Register as non-authoritative test fixture
    res = register_test_asset_fixture(
        isolated_i2_db, SOLANA_CAIP2_CANONICAL, asset_addr, decimals=9, symbol="SYNTH", authoritative=False
    )
    assert res["status"] == "UNVERIFIED"
    assert res["verification_source"] == DecimalProvenanceSource.CONTROLLED_TEST_FIXTURE.value

    # Attempt order placement -> must be rejected due to unverified decimals
    order_req = PaperOrderRequest(
        account_id="dummy",
        client_order_id="synth-order-001",
        asset_id=asset_addr,
        pool_id="pool_synth",
        side="BUY",
        input_mint="native",
        output_mint=asset_addr,
        input_amount_raw=Decimal("1.0"),
        chain_id=SOLANA_CAIP2_CANONICAL
    )
    order_res = service.execute_order(order_req, current_market_price_native=Decimal("0.001"))
    assert order_res.status == "REJECTED"
    assert "UNVERIFIED_ASSET_DECIMALS" in order_res.rejection_reason


def test_i2_01_static_seed_cannot_authorize_paper_orders(isolated_i2_db):
    """Verifies that STATIC_SEED provenance cannot authorize paper orders."""
    service = PersistentPaperService(isolated_i2_db)
    bonk_mint = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"

    with service._get_connection() as conn:
        dec, is_ver = service._resolve_asset_decimals(SOLANA_CAIP2_CANONICAL, bonk_mint, conn)
        assert is_ver is False

    order_req = PaperOrderRequest(
        account_id="dummy",
        client_order_id="static-seed-order-001",
        asset_id=bonk_mint,
        pool_id="pool_bonk",
        side="BUY",
        input_mint="native",
        output_mint=bonk_mint,
        input_amount_raw=Decimal("1.0"),
        chain_id=SOLANA_CAIP2_CANONICAL
    )
    order_res = service.execute_order(order_req, current_market_price_native=Decimal("0.000045"))
    assert order_res.status == "REJECTED"
    assert "UNVERIFIED_ASSET_DECIMALS" in order_res.rejection_reason


def test_i2_01_arbitrary_source_string_rejected(isolated_i2_db):
    """Verifies record_decimal_verification rejects unknown arbitrary source strings."""
    service = PersistentPaperService(isolated_i2_db)
    with pytest.raises(ValueError) as exc_info:
        service.record_decimal_verification(
            chain_id=SOLANA_CAIP2_CANONICAL,
            asset_address="0xArbitrarySourceToken",
            decimals=9,
            verification_source="MY_CUSTOM_TRUSTED_SOURCE",
            verification_method="MANUAL_INPUT",
            raw_metadata={"foo": "bar"}
        )
    assert "Invalid decimal verification source 'MY_CUSTOM_TRUSTED_SOURCE'" in str(exc_info.value)


def test_i2_01_unauthorized_source_forces_unverified_status(isolated_i2_db):
    """Verifies that unauthorized provenance sources are recorded with status='UNVERIFIED'."""
    service = PersistentPaperService(isolated_i2_db)
    res = service.record_decimal_verification(
        chain_id=SOLANA_CAIP2_CANONICAL,
        asset_address="0xDiscoveryMetadataToken",
        decimals=18,
        verification_source=DecimalProvenanceSource.DISCOVERY_METADATA.value,
        verification_method="COINGECKO_SCRAPE",
        raw_metadata={"provider": "coingecko", "decimals": 18}
    )
    assert res["status"] == "UNVERIFIED"

    with service._get_connection() as conn:
        dec, is_ver = service._resolve_asset_decimals(SOLANA_CAIP2_CANONICAL, "0xDiscoveryMetadataToken", conn)
        assert is_ver is False


def test_i2_01_revoked_verification_cannot_authorize_orders(isolated_i2_db):
    """Verifies that revoked verification fails closed and rejects subsequent paper orders."""
    service = PersistentPaperService(isolated_i2_db)
    asset_addr = "0xRevocableToken1111111111111111111111"

    # Register authoritative
    register_test_asset_fixture(
        isolated_i2_db, SOLANA_CAIP2_CANONICAL, asset_addr, decimals=9, symbol="REVOK", authoritative=True
    )
    with service._get_connection() as conn:
        dec, is_ver = service._resolve_asset_decimals(SOLANA_CAIP2_CANONICAL, asset_addr, conn)
        assert is_ver is True

    # Revoke verification
    service.revoke_decimal_verification(SOLANA_CAIP2_CANONICAL, asset_addr, reason="Token contract exploited")

    with service._get_connection() as conn:
        dec2, is_ver2 = service._resolve_asset_decimals(SOLANA_CAIP2_CANONICAL, asset_addr, conn)
        assert is_ver2 is False

    order_req = PaperOrderRequest(
        account_id="dummy",
        client_order_id="revoked-order-001",
        asset_id=asset_addr,
        pool_id="pool_revok",
        side="BUY",
        input_mint="native",
        output_mint=asset_addr,
        input_amount_raw=Decimal("1.0"),
        chain_id=SOLANA_CAIP2_CANONICAL
    )
    order_res = service.execute_order(order_req, current_market_price_native=Decimal("0.001"))
    assert order_res.status == "REJECTED"
    assert "UNVERIFIED_ASSET_DECIMALS" in order_res.rejection_reason


# =============================================================================
# I.2-02: Migration 12 Data-Safety Repair & Migration 13
# =============================================================================

def test_i2_02_migration_sequence_fresh_database():
    """Verifies a fresh database runs all 13 migrations cleanly and passes integrity checks."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        migrator = SqliteMigrator(path)
        applied = migrator.apply_migrations()
        assert 13 in applied
        assert migrator.get_current_version() >= 13

        with sqlite3.connect(path) as conn:
            # PRAGMA integrity_check
            cur = conn.execute("PRAGMA integrity_check;")
            assert cur.fetchone()[0] == "ok"

            # PRAGMA foreign_key_check
            cur = conn.execute("PRAGMA foreign_key_check;")
            fk_violations = cur.fetchall()
            assert len(fk_violations) == 0
    finally:
        if os.path.exists(path):
            os.remove(path)


def test_i2_02_reconciles_omitted_tables_safely():
    """Verifies that Migration 13 updates strategy_runs, reorg_events, risk_limits, and provider records."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        # Run migrations 1 through 12
        migrator = SqliteMigrator(path)
        # Apply up to migration 12
        with migrator._get_connection() as conn:
            migrator._ensure_migration_table(conn)
            for m in migrator.MIGRATIONS[:12]:
                statements = m["statements"]
                for s in statements:
                    try:
                        conn.execute(s)
                    except Exception:
                        pass
                now_str = datetime.now(timezone.utc).isoformat()
                conn.execute(
                    "INSERT INTO schema_migrations (version, name, applied_at, checksum) VALUES (?, ?, ?, 'c');",
                    (m["version"], m["name"], now_str)
                )

        # Seed legacy records into omitted tables
        with sqlite3.connect(path) as conn:
            now_str = datetime.now(timezone.utc).isoformat()
            # 1. strategy_runs
            conn.execute(
                """
                INSERT INTO strategy_runs (
                    run_id, strategy_id, strategy_version, chain_id, mode, input_dataset_hash, status, started_at
                ) VALUES ('run_leg_01', 'momentum', '1.0', 'solana:mainnet', 'RESEARCH', 'hash1', 'COMPLETED', ?);
                """,
                (now_str,)
            )
            # 2. reorg_invalidation_events
            conn.execute(
                """
                INSERT INTO reorg_invalidation_events (
                    id, chain_id, block_number, block_hash, reason
                ) VALUES ('reorg_leg_01', 'solana:mainnet', 12345, '0xhash', 'test reorg');
                """
            )
            # 3. paper_risk_limits
            conn.execute(
                """
                INSERT INTO paper_risk_limits (
                    id, chain_id, max_order_value_usd
                ) VALUES ('rl_leg_01', 'solana:mainnet', 50000.0);
                """
            )
            # 4. provider_reservations
            conn.execute(
                """
                INSERT INTO provider_reservations (
                    id, provider_id, capability, chain_id, reserved_at, created_at, updated_at
                ) VALUES ('res_leg_01', 'p_test', 'rpc', 'solana:mainnet', ?, ?, ?);
                """,
                (now_str, now_str, now_str)
            )
            # 5. provider_usage_records
            conn.execute(
                """
                INSERT INTO provider_usage_records (
                    id, provider_id, chain_id, capability, day_date, created_at, updated_at
                ) VALUES ('use_leg_01', 'p_test', 'solana:mainnet', 'rpc', '2026-11-15', ?, ?);
                """,
                (now_str, now_str)
            )
            # 6. providers.chains_json
            conn.execute(
                """
                INSERT INTO providers (
                    id, name, provider_type, chains_json, created_at, updated_at
                ) VALUES ('p_test', 'Provider Test', 'rpc', '["solana:mainnet", "eip155:1"]', ?, ?);
                """,
                (now_str, now_str)
            )

        # Apply Migration 13 forward-only
        applied = migrator.apply_migrations()
        assert 13 in applied

        # Verify all omitted tables reconciled to canonical Solana
        with sqlite3.connect(path) as conn:
            conn.row_factory = sqlite3.Row

            run_row = conn.execute("SELECT chain_id FROM strategy_runs WHERE run_id = 'run_leg_01';").fetchone()
            assert run_row["chain_id"] == SOLANA_CAIP2_CANONICAL

            reorg_row = conn.execute("SELECT chain_id FROM reorg_invalidation_events WHERE id = 'reorg_leg_01';").fetchone()
            assert reorg_row["chain_id"] == SOLANA_CAIP2_CANONICAL

            rl_row = conn.execute("SELECT chain_id FROM paper_risk_limits WHERE id = 'rl_leg_01';").fetchone()
            assert rl_row["chain_id"] == SOLANA_CAIP2_CANONICAL

            res_row = conn.execute("SELECT chain_id FROM provider_reservations WHERE id = 'res_leg_01';").fetchone()
            assert res_row["chain_id"] == SOLANA_CAIP2_CANONICAL

            use_row = conn.execute("SELECT chain_id FROM provider_usage_records WHERE id = 'use_leg_01';").fetchone()
            assert use_row["chain_id"] == SOLANA_CAIP2_CANONICAL

            p_row = conn.execute("SELECT chains_json FROM providers WHERE id = 'p_test';").fetchone()
            chains = json.loads(p_row["chains_json"])
            assert SOLANA_CAIP2_CANONICAL in chains
            assert "solana:mainnet" not in chains
            assert "eip155:1" in chains
    finally:
        if os.path.exists(path):
            os.remove(path)


def test_i2_02_preflight_conflict_detection_aborts_transaction():
    """Verifies that unresolvable collisions between legacy and canonical state abort Migration 13 before mutation."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        migrator = SqliteMigrator(path)
        with migrator._get_connection() as conn:
            migrator._ensure_migration_table(conn)
            for m in migrator.MIGRATIONS[:12]:
                for s in m["statements"]:
                    try:
                        conn.execute(s)
                    except Exception:
                        pass
                now_str = datetime.now(timezone.utc).isoformat()
                conn.execute(
                    "INSERT INTO schema_migrations (version, name, applied_at, checksum) VALUES (?, ?, ?, 'c');",
                    (m["version"], m["name"], now_str)
                )

        # Seed conflicting paper accounts with distinct balances and orders
        with sqlite3.connect(path) as conn:
            now_str = datetime.now(timezone.utc).isoformat()
            conn.execute(
                """
                INSERT INTO paper_accounts (
                    account_id, chain_id, initial_balance_usd, current_balance_usd, unrealized_pnl_usd, realized_pnl_usd, created_at, updated_at
                ) VALUES ('acc_leg', 'solana:mainnet', 100.0, 100.0, 0.0, 0.0, ?, ?);
                """,
                (now_str, now_str)
            )
            conn.execute(
                """
                INSERT INTO paper_accounts (
                    account_id, chain_id, initial_balance_usd, current_balance_usd, unrealized_pnl_usd, realized_pnl_usd, created_at, updated_at
                ) VALUES ('acc_can', ?, 500.0, 500.0, 0.0, 0.0, ?, ?);
                """,
                (SOLANA_CAIP2_CANONICAL, now_str, now_str)
            )
            conn.execute(
                """
                INSERT INTO paper_orders_v2 (
                    order_id, client_order_id, account_id, chain_id, asset_id, side, status, requested_quantity, created_at, updated_at
                ) VALUES ('ord_l', 'cl_l', 'acc_leg', 'solana:mainnet', '0xTok1', 'BUY', 'FILLED', '1.0', ?, ?),
                         ('ord_c', 'cl_c', 'acc_can', ?, '0xTok2', 'BUY', 'FILLED', '2.0', ?, ?);
                """,
                (now_str, now_str, SOLANA_CAIP2_CANONICAL, now_str, now_str)
            )

        # Applying Migration 13 MUST abort due to unresolvable account conflict
        with pytest.raises(RuntimeError) as exc_info:
            migrator.apply_migrations()
        assert "Preflight conflict in paper_accounts" in str(exc_info.value)

        # Verify rollback: database state is completely unchanged
        with sqlite3.connect(path) as conn:
            leg_acc = conn.execute("SELECT current_balance_usd FROM paper_accounts WHERE account_id = 'acc_leg';").fetchone()
            assert leg_acc is not None
            assert float(leg_acc[0]) == 100.0

            can_acc = conn.execute("SELECT current_balance_usd FROM paper_accounts WHERE account_id = 'acc_can';").fetchone()
            assert can_acc is not None
            assert float(can_acc[0]) == 500.0
    finally:
        if os.path.exists(path):
            os.remove(path)


def test_i2_02_idempotency_of_migration_13(isolated_i2_db):
    """Verifies that re-running apply_migrations after Migration 13 has run is safe and produces no changes."""
    migrator = SqliteMigrator(isolated_i2_db)
    v1 = migrator.get_current_version()
    assert v1 >= 13

    # Re-run
    newly_applied = migrator.apply_migrations()
    assert len(newly_applied) == 0
    assert migrator.get_current_version() == v1


# =============================================================================
# I.2-03: Decimal Provenance & Static Seed Reclassification
# =============================================================================

def test_i2_03_bonk_reclassified_as_unverified_static_seed(isolated_i2_db):
    """Verifies that BONK is reclassified as UNVERIFIED / STATIC_SEED in Migration 13."""
    PersistentPaperService(isolated_i2_db)
    bonk_mint = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
    with sqlite3.connect(isolated_i2_db) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            "SELECT * FROM token_decimal_verifications WHERE asset_address = ?;",
            (bonk_mint,)
        )
        row = cur.fetchone()
        assert row is not None
        assert row["status"] == "UNVERIFIED"
        assert row["verification_source"] == "STATIC_SEED"
        assert row["decimals"] == 5

        # Check discovered_tokens has is_verified = 0
        t_cur = conn.execute("SELECT is_verified FROM discovered_tokens WHERE address = ?;", (bonk_mint,))
        t_row = t_cur.fetchone()
        assert t_row is not None
        assert t_row["is_verified"] == 0


def test_i2_03_static_seeds_cannot_authorize_without_authentic_rpc(isolated_i2_db):
    """Verifies that static seed metadata cannot authorize paper trading without authentic RPC evidence."""
    service = PersistentPaperService(isolated_i2_db)
    bonk_mint = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"

    with service._get_connection() as conn:
        dec, is_ver = service._resolve_asset_decimals(SOLANA_CAIP2_CANONICAL, bonk_mint, conn)
        assert is_ver is False

    # Promote with authentic test RPC evidence
    register_test_asset_fixture(
        isolated_i2_db, SOLANA_CAIP2_CANONICAL, bonk_mint, decimals=5, symbol="BONK", authoritative=True
    )

    with service._get_connection() as conn:
        dec2, is_ver2 = service._resolve_asset_decimals(SOLANA_CAIP2_CANONICAL, bonk_mint, conn)
        assert is_ver2 is True
        assert dec2 == 5


# =============================================================================
# I.2-04: Complete EVM RPC Chain-ID Support
# =============================================================================

def test_i2_04_all_registered_evm_chains_supported():
    """Verifies that all 7 registered EVM networks in SUPPORTED_CHAINS have correct expected chain IDs."""
    evm_chains = [c for c in SUPPORTED_CHAINS if c.startswith("eip155:")]
    assert len(evm_chains) == 7

    expected_map = {
        "eip155:1": 1,
        "eip155:8453": 8453,
        "eip155:56": 56,
        "eip155:42161": 42161,
        "eip155:10": 10,
        "eip155:137": 137,
        "eip155:43114": 43114
    }

    for chain_id, expected_num in expected_map.items():
        client = EvmRpcClient(chain_id)
        assert client.expected_numeric_id == expected_num


def test_i2_04_unknown_evm_chain_rejected():
    """Verifies that unregistered EVM chain IDs are rejected with descriptive error."""
    with pytest.raises(ValueError) as exc_info:
        EvmRpcClient("eip155:9999999")
    assert "Unsupported EVM chain ID" in str(exc_info.value)

    with pytest.raises(ValueError) as exc_info2:
        EvmRpcClient("solana:mainnet")
    assert "Unsupported EVM chain ID" in str(exc_info2.value)


def test_i2_04_mismatched_chain_id_rejected():
    """Verifies that validate_chain_id rejects RPC endpoints returning an mismatched chain ID."""
    async def _async_test():
        client = EvmRpcClient("eip155:1")  # Expects 1

        # Mock RPC returning chain ID 8453 (Base) instead of 1 (Ethereum)
        with patch("app.services.evm_rpc.evm_provider_manager.get_active_endpoint", return_value="https://mock.rpc"):
            with patch("app.services.evm_rpc.quota_manager.acquire_permit", return_value=True):
                mock_resp = MagicMock()
                mock_resp.status_code = 200
                mock_resp.json.return_value = {"jsonrpc": "2.0", "id": 1, "result": "0x2105"}  # 8453 in hex

                with patch("app.services.evm_rpc.get_http_client") as mock_http:
                    mock_client_instance = MagicMock()
                    mock_client_instance.post = AsyncMock(return_value=mock_resp)
                    mock_http.return_value.__aenter__.return_value = mock_client_instance

                    # Validate chain ID should return False due to mismatch
                    is_valid = await client.validate_chain_id()
                    assert is_valid is False

                    # call_verified_rpc should reject and return None
                    result = await client.call_verified_rpc("eth_blockNumber", [])
                    assert result is None

    asyncio.run(_async_test())


def test_i2_04_offline_mode_blocks_evm_rpc():
    """Verifies that offline research mode strictly prevents outbound network calls in EvmRpcClient."""
    orig_mode = settings.OPERATING_MODE
    try:
        settings.OPERATING_MODE = "offline_research"
        client = EvmRpcClient("eip155:1")

        with pytest.raises(OfflineModeBlockedException):
            asyncio.run(client.call_rpc("eth_blockNumber", []))
    finally:
        settings.OPERATING_MODE = orig_mode
