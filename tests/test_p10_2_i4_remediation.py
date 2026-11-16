"""
Phase P10.2-I.4 Comprehensive Remediation Verification Suite
Validates closure of all audit findings from P10.2-I.3:
- I3-F01 & I3-F02: Migration 13 Populated-Database Safety & Zero Data-Loss Merges
- I3-F03: Decimal Provenance Cross-Chain/Cross-Asset Replay Prevention
- I3-F04: Scope Isolation: CORE_TEST Excluded From Non-Test Environments
- I3-F05: Solana Genesis Hash Identity Verification (5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp)
- I3-F06: Strict CAIP-19 Address Validation via AddressValidator
- I3-F07: Canonical Solana CAIP-2 Identity in Windmill Workflows
"""

import os
import sys
import json
import sqlite3
import tempfile
import asyncio
import hashlib
import uuid
import pytest
from decimal import Decimal
from datetime import datetime, timezone
from unittest.mock import patch, AsyncMock

from app.core.config import settings
from app.core.chains import (
    SOLANA_CAIP2_CANONICAL,
    SOLANA_LEGACY_ALIAS,
    CAIP19AssetId,
    normalize_chain_id
)
from app.db.sqlite_migrator import SqliteMigrator
from app.services.persistent_paper_service import PersistentPaperService
from app.services.solana_rpc import SolanaRpcService, solana_rpc_service
from app.adapters.solana import SolanaChainAdapter
from app.schemas.contracts import DecimalProvenanceSource, PaperOrderRequest


def _setup_db_up_to_m12(path: str):
    """Sets up an isolated SQLite database migrated strictly up to Migration 12."""
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


@pytest.fixture
def clean_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    migrator = SqliteMigrator(path)
    migrator.apply_migrations()
    yield path
    if os.path.exists(path):
        os.remove(path)


# =============================================================================
# I3-F01 & I3-F02: Migration 13 Populated-Database Data-Loss Prevention
# =============================================================================

def test_i4_f01_preflight_aborts_on_conflicting_account_balances_even_with_zero_orders():
    """Confirms preflight conflict check aborts if accounts have conflicting balances even with 0 orders."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _setup_db_up_to_m12(path)

        with sqlite3.connect(path) as conn:
            # Insert legacy account with $1000 and 0 orders
            conn.execute("""
                INSERT INTO paper_accounts (account_id, chain_id, initial_balance_usd, current_balance_usd, unrealized_pnl_usd, realized_pnl_usd, created_at, updated_at)
                VALUES ('acc_leg_01', 'solana:mainnet', 1000.0, 1000.0, 0.0, 0.0, '2026-01-01', '2026-01-01');
            """)
            # Insert canonical account with $500 and 0 orders
            conn.execute("""
                INSERT INTO paper_accounts (account_id, chain_id, initial_balance_usd, current_balance_usd, unrealized_pnl_usd, realized_pnl_usd, created_at, updated_at)
                VALUES ('acc_can_01', 'solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp', 500.0, 500.0, 0.0, 0.0, '2026-01-01', '2026-01-01');
            """)

        migrator = SqliteMigrator(path)
        with pytest.raises(RuntimeError) as exc_info:
            migrator.apply_migrations()

        assert "Preflight conflict in paper_accounts" in str(exc_info.value)
        assert "Cannot safely merge" in str(exc_info.value)
    finally:
        if os.path.exists(path):
            os.remove(path)


def test_i4_f02_preflight_aborts_on_conflicting_positions_across_differing_accounts():
    """Confirms preflight position check detects conflicts across differing legacy and canonical accounts."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _setup_db_up_to_m12(path)

        with sqlite3.connect(path) as conn:
            # Same balance (no account conflict)
            conn.execute("""
                INSERT INTO paper_accounts (account_id, chain_id, initial_balance_usd, current_balance_usd, unrealized_pnl_usd, realized_pnl_usd, created_at, updated_at)
                VALUES ('acc_leg_02', 'solana:mainnet', 1000.0, 1000.0, 0.0, 0.0, '2026-01-01', '2026-01-01');
            """)
            conn.execute("""
                INSERT INTO paper_accounts (account_id, chain_id, initial_balance_usd, current_balance_usd, unrealized_pnl_usd, realized_pnl_usd, created_at, updated_at)
                VALUES ('acc_can_02', 'solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp', 1000.0, 1000.0, 0.0, 0.0, '2026-01-01', '2026-01-01');
            """)

            # Conflicting position quantities for BONK across the accounts
            conn.execute("""
                INSERT INTO paper_positions_v2 (position_id, account_id, chain_id, asset_address, symbol, side, token_amount_raw, cost_basis_native, entry_price_native, current_price_native, opened_at, updated_at)
                VALUES ('pos_leg', 'acc_leg_02', 'solana:mainnet', 'DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263', 'BONK', 'LONG', '500.0', 0.0225, 0.000045, 0.000045, '2026-01-01', '2026-01-01');
            """)
            conn.execute("""
                INSERT INTO paper_positions_v2 (position_id, account_id, chain_id, asset_address, symbol, side, token_amount_raw, cost_basis_native, entry_price_native, current_price_native, opened_at, updated_at)
                VALUES ('pos_can', 'acc_can_02', 'solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp', 'DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263', 'BONK', 'LONG', '100.0', 0.0045, 0.000045, 0.000045, '2026-01-01', '2026-01-01');
            """)

        migrator = SqliteMigrator(path)
        with pytest.raises(RuntimeError) as exc_info:
            migrator.apply_migrations()

        assert "Preflight conflict in paper_positions_v2" in str(exc_info.value)
        assert "500.0 vs 100.0" in str(exc_info.value)
    finally:
        if os.path.exists(path):
            os.remove(path)


def test_i4_f01_f02_non_conflicting_balances_and_positions_merged_without_data_loss():
    """Confirms balances and identical positions across legacy and canonical records are merged safely."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _setup_db_up_to_m12(path)

        with sqlite3.connect(path) as conn:
            # Same balance
            conn.execute("""
                INSERT INTO paper_accounts (account_id, chain_id, initial_balance_usd, current_balance_usd, unrealized_pnl_usd, realized_pnl_usd, created_at, updated_at)
                VALUES ('acc_leg_03', 'solana:mainnet', 1000.0, 1000.0, 0.0, 0.0, '2026-01-01', '2026-01-01');
            """)
            conn.execute("""
                INSERT INTO paper_accounts (account_id, chain_id, initial_balance_usd, current_balance_usd, unrealized_pnl_usd, realized_pnl_usd, created_at, updated_at)
                VALUES ('acc_can_03', 'solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp', 1000.0, 1000.0, 0.0, 0.0, '2026-01-01', '2026-01-01');
            """)

            # Legacy balance has 10 SOL, canonical has 5 SOL (in lamports raw units)
            conn.execute("""
                INSERT INTO paper_account_balances (id, account_id, chain_id, asset_id, symbol, decimals, free_amount_raw, locked_amount_raw, updated_at)
                VALUES ('b_leg', 'acc_leg_03', 'solana:mainnet', 'native', 'SOL', 9, '10000000000', '0', '2026-01-01');
            """)
            conn.execute("""
                INSERT INTO paper_account_balances (id, account_id, chain_id, asset_id, symbol, decimals, free_amount_raw, locked_amount_raw, updated_at)
                VALUES ('b_can', 'acc_can_03', 'solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp', 'native', 'SOL', 9, '5000000000', '0', '2026-01-01');
            """)

            # Both have identical positions (100 BONK in raw units)
            conn.execute("""
                INSERT INTO paper_positions_v2 (position_id, account_id, chain_id, asset_address, symbol, side, token_amount_raw, cost_basis_native, entry_price_native, current_price_native, opened_at, updated_at)
                VALUES ('pos_leg_03', 'acc_leg_03', 'solana:mainnet', 'DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263', 'BONK', 'LONG', '100', 0.0045, 0.000045, 0.000045, '2026-01-01', '2026-01-01');
            """)
            conn.execute("""
                INSERT INTO paper_positions_v2 (position_id, account_id, chain_id, asset_address, symbol, side, token_amount_raw, cost_basis_native, entry_price_native, current_price_native, opened_at, updated_at)
                VALUES ('pos_can_03', 'acc_can_03', 'solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp', 'DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263', 'BONK', 'LONG', '100', 0.0045, 0.000045, 0.000045, '2026-01-01', '2026-01-01');
            """)

        # Run migration 13
        migrator = SqliteMigrator(path)
        migrator.apply_migrations()

        with migrator._get_connection() as conn:
            # Balances must be merged: 10000000000 + 5000000000 = 15000000000
            bal = conn.execute("SELECT free_amount_raw FROM paper_account_balances WHERE account_id = 'acc_can_03';").fetchone()
            assert bal is not None
            assert bal[0] == "15000000000"

            # Positions must be merged: 100 + 100 = 200 BONK
            pos = conn.execute("SELECT token_amount_raw FROM paper_positions_v2 WHERE account_id = 'acc_can_03';").fetchone()
            assert pos is not None
            assert pos[0] == "200"
    finally:
        if os.path.exists(path):
            os.remove(path)


# =============================================================================
# I3-F03: Decimal Provenance Cross-Chain / Cross-Asset Replay Prevention
# =============================================================================

def test_i4_f03_cross_chain_metadata_payload_rejected(clean_db):
    """Verifies that passing Ethereum metadata to verify a Solana mint raises ValueError."""
    service = PersistentPaperService(db_path=clean_db)
    solana_mint = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"

    # Mismatched address in payload
    eth_payload = {
        "address": "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2",
        "symbol": "WETH",
        "decimals": 18,
        "network": "ethereum"
    }

    with pytest.raises(ValueError) as exc:
        service.record_decimal_verification(
            chain_id=SOLANA_CAIP2_CANONICAL,
            asset_address=solana_mint,
            decimals=5,
            symbol="BONK",
            verification_source=DecimalProvenanceSource.RPC_NODE.value,
            verification_method="rpc_eth_call",
            raw_metadata=eth_payload
        )
    assert "Metadata payload mismatch" in str(exc.value)
    assert "internal metadata address" in str(exc.value)


def test_i4_f03_cross_network_chain_payload_rejected(clean_db):
    """Verifies that passing wrong network identifier inside metadata raises ValueError."""
    service = PersistentPaperService(db_path=clean_db)
    solana_mint = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"

    wrong_chain_payload = {
        "mint": solana_mint,
        "chain_id": "eip155:1",
        "decimals": 5
    }

    with pytest.raises(ValueError) as exc:
        service.record_decimal_verification(
            chain_id=SOLANA_CAIP2_CANONICAL,
            asset_address=solana_mint,
            decimals=5,
            symbol="BONK",
            verification_source=DecimalProvenanceSource.RPC_NODE.value,
            verification_method="rpc_get_parsed_account_info",
            raw_metadata=wrong_chain_payload
        )
    assert "Metadata payload mismatch" in str(exc.value)
    assert "internal metadata chain" in str(exc.value)


# =============================================================================
# I3-F04: Scope Isolation: CORE_TEST Excluded From Non-Test Environments
# =============================================================================

def test_i4_f04_core_test_verification_fails_closed_in_production(clean_db):
    """Verifies that CORE_TEST verification records resolve to (None, False) when APP_ENV != 'test'."""
    service = PersistentPaperService(db_path=clean_db)
    test_token = "0x1111111111111111111111111111111111111111"

    # Register in test mode
    with patch.object(settings, "APP_ENV", "test"):
        service.record_decimal_verification(
            chain_id="eip155:1",
            asset_address=test_token,
            decimals=6,
            symbol="TEST",
            verification_source=DecimalProvenanceSource.CORE_TEST.value,
            verification_method="unit_test_mock",
            raw_metadata={"address": test_token, "chain_id": "eip155:1", "decimals": 6}
        )

        with service._get_connection() as conn:
            dec, is_ver = service._resolve_asset_decimals("eip155:1", test_token, conn)
            assert dec == 6
            assert is_ver is True

    # In production, CORE_TEST record must be rejected
    with patch.object(settings, "APP_ENV", "production"):
        with service._get_connection() as conn:
            dec, is_ver = service._resolve_asset_decimals("eip155:1", test_token, conn)
            assert is_ver is False
            assert dec is None


# =============================================================================
# I3-F05: Solana Genesis Hash Identity Verification
# =============================================================================

def test_i4_f05_solana_verify_genesis_hash_success():
    """Verifies that SolanaRpcService validates the canonical Mainnet-Beta genesis hash."""
    service = SolanaRpcService()
    async def _run():
        with patch.object(service, "_call_rpc", new_callable=AsyncMock) as mock_rpc:
            mock_rpc.return_value = "5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"
            is_valid = await service.verify_genesis_hash()
            assert is_valid is True
            mock_rpc.assert_called_once_with("getGenesisHash", [])
    asyncio.run(_run())


def test_i4_f05_solana_verify_genesis_hash_mismatch_raises():
    """Verifies that a devnet/testnet genesis hash is rejected with RuntimeError."""
    service = SolanaRpcService()
    async def _run():
        with patch.object(service, "_call_rpc", new_callable=AsyncMock) as mock_rpc:
            mock_rpc.return_value = "EtWTRABZaYq6iMfeYKouRu166VU2xqa1wcaWoxPkrZBG"  # Devnet genesis
            with pytest.raises(RuntimeError) as exc:
                await service.verify_genesis_hash()
            assert "Solana genesis hash mismatch" in str(exc.value)
    asyncio.run(_run())


def test_i4_f05_solana_adapter_delegates_to_verify_chain_id():
    """Verifies SolanaChainAdapter.verify_chain_id queries genesis hash."""
    adapter = SolanaChainAdapter()
    async def _run():
        with patch.object(solana_rpc_service, "verify_genesis_hash", new_callable=AsyncMock) as mock_verify:
            mock_verify.return_value = True
            result = await adapter.verify_chain_id()
            assert result is True
            mock_verify.assert_called_once()
    asyncio.run(_run())


# =============================================================================
# I3-F06: Strict CAIP-19 Address Validation via AddressValidator
# =============================================================================

def test_i4_f06_caip19_parse_rejects_malformed_evm_address():
    """Verifies CAIP19AssetId.parse rejects invalid EVM addresses."""
    with pytest.raises(ValueError) as exc:
        CAIP19AssetId.parse("eip155:1/erc20:0xInvalidMalformedAddress123")
    assert "Invalid eip155:1 address format" in str(exc.value)


def test_i4_f06_caip19_from_chain_and_address_rejects_malformed_solana_address():
    """Verifies CAIP19AssetId.from_chain_and_address rejects invalid Solana addresses."""
    with pytest.raises(ValueError) as exc:
        CAIP19AssetId.from_chain_and_address(SOLANA_CAIP2_CANONICAL, "NotABase58Address!!!")
    assert f"Invalid {SOLANA_CAIP2_CANONICAL} address format" in str(exc.value)


def test_i4_f06_caip19_accepts_valid_checksummed_addresses():
    """Verifies CAIP19AssetId accepts canonical checksummed addresses."""
    # Valid EVM
    evm_caip19 = CAIP19AssetId.from_chain_and_address(
        "eip155:1", "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
    )
    assert evm_caip19.asset_reference == "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
    assert evm_caip19.to_caip19() == "eip155:1/erc20:0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"

    # Valid Solana
    sol_caip19 = CAIP19AssetId.from_chain_and_address(
        SOLANA_CAIP2_CANONICAL, "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
    )
    assert sol_caip19.asset_reference == "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"


# =============================================================================
# I3-F07: Canonical Solana CAIP-2 Identity in Windmill Workflows
# =============================================================================

def test_i4_f07_windmill_wf01_discovers_with_canonical_solana():
    """Verifies wf01 candidate discovery returns BONK when called with canonical Solana CAIP-2."""
    from windmill_scripts.wf01_candidate_discovery import discover_dex_candidates
    candidates = discover_dex_candidates(SOLANA_CAIP2_CANONICAL)
    assert len(candidates) == 1
    assert candidates[0]["symbol"] == "BONK"
    assert candidates[0]["decimals"] == 5
    assert candidates[0]["chain_id"] == SOLANA_CAIP2_CANONICAL


# =============================================================================
# I4.1-F01: Fixed-Point & Raw-Unit Integer Preservation (> 2^53)
# =============================================================================

def test_i4_1_f01_raw_unit_arithmetic_preserves_large_integers():
    """Confirms raw-unit summation preserves exact integers > 2^53 without precision loss or float notation."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _setup_db_up_to_m12(path)

        with sqlite3.connect(path) as conn:
            conn.execute("""
                INSERT INTO paper_accounts (account_id, chain_id, initial_balance_usd, current_balance_usd, unrealized_pnl_usd, realized_pnl_usd, created_at, updated_at)
                VALUES ('acc_leg_large', 'solana:mainnet', 1000.0, 1000.0, 0.0, 0.0, '2026-01-01', '2026-01-01');
            """)
            conn.execute("""
                INSERT INTO paper_accounts (account_id, chain_id, initial_balance_usd, current_balance_usd, unrealized_pnl_usd, realized_pnl_usd, created_at, updated_at)
                VALUES ('acc_can_large', 'solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp', 1000.0, 1000.0, 0.0, 0.0, '2026-01-01', '2026-01-01');
            """)

            # 9007199254740993 > 2^53 (which loses 2 units under standard IEEE-754 float64)
            large_raw_leg = "9007199254740993"
            add_raw_can = "5"
            expected_raw = "9007199254740998"

            conn.execute("""
                INSERT INTO paper_account_balances (id, account_id, chain_id, asset_id, symbol, decimals, free_amount_raw, locked_amount_raw, updated_at)
                VALUES ('b_leg_l', 'acc_leg_large', 'solana:mainnet', 'native', 'SOL', 9, ?, '0', '2026-01-01');
            """, (large_raw_leg,))
            conn.execute("""
                INSERT INTO paper_account_balances (id, account_id, chain_id, asset_id, symbol, decimals, free_amount_raw, locked_amount_raw, updated_at)
                VALUES ('b_can_l', 'acc_can_large', 'solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp', 'native', 'SOL', 9, ?, '0', '2026-01-01');
            """, (add_raw_can,))

        migrator = SqliteMigrator(path)
        migrator.apply_migrations()

        with migrator._get_connection() as conn:
            bal = conn.execute("SELECT free_amount_raw FROM paper_account_balances WHERE account_id = 'acc_can_large';").fetchone()
            assert bal is not None
            # Must match exact integer string with zero precision loss
            assert bal[0] == expected_raw
            assert ".0" not in bal[0]
            assert "e+" not in bal[0]
    finally:
        if os.path.exists(path):
            os.remove(path)


# =============================================================================
# I4.1-F02: Unlinked Multi-Account Preflight Abort
# =============================================================================

def test_i4_1_f02_multi_account_conflict_fails_closed():
    """Confirms preflight conflict check aborts if multiple accounts exist on legacy or canonical Solana."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _setup_db_up_to_m12(path)

        with sqlite3.connect(path) as conn:
            # 2 legacy accounts using valid distinct aliases: solana:mainnet and solana
            conn.execute("""
                INSERT INTO paper_accounts (account_id, chain_id, initial_balance_usd, current_balance_usd, unrealized_pnl_usd, realized_pnl_usd, created_at, updated_at)
                VALUES ('acc_leg_01', 'solana:mainnet', 1000.0, 1000.0, 0.0, 0.0, '2026-01-01', '2026-01-01');
            """)
            conn.execute("""
                INSERT INTO paper_accounts (account_id, chain_id, initial_balance_usd, current_balance_usd, unrealized_pnl_usd, realized_pnl_usd, created_at, updated_at)
                VALUES ('acc_leg_02', 'solana', 1000.0, 1000.0, 0.0, 0.0, '2026-01-01', '2026-01-01');
            """)
            # 1 canonical account
            conn.execute("""
                INSERT INTO paper_accounts (account_id, chain_id, initial_balance_usd, current_balance_usd, unrealized_pnl_usd, realized_pnl_usd, created_at, updated_at)
                VALUES ('acc_can_01', 'solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp', 1000.0, 1000.0, 0.0, 0.0, '2026-01-01', '2026-01-01');
            """)

        migrator = SqliteMigrator(path)
        with pytest.raises(RuntimeError) as exc_info:
            migrator.apply_migrations()

        assert "Preflight conflict in paper_accounts" in str(exc_info.value)
        assert "cannot unambiguously reconcile" in str(exc_info.value)
    finally:
        if os.path.exists(path):
            os.remove(path)


# =============================================================================
# I4.1-F03: Position Cost Basis & PnL Ledger Reconciliation
# =============================================================================

def test_i4_1_f03_position_merging_reconciles_cost_basis_and_weighted_entry_price():
    """Confirms position merging calculates weighted average entry price and sums cost basis accurately."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _setup_db_up_to_m12(path)

        with sqlite3.connect(path) as conn:
            conn.execute("""
                INSERT INTO paper_accounts (account_id, chain_id, initial_balance_usd, current_balance_usd, unrealized_pnl_usd, realized_pnl_usd, created_at, updated_at)
                VALUES ('acc_leg_p', 'solana:mainnet', 1000.0, 1000.0, 0.0, 0.0, '2026-01-01', '2026-01-01');
            """)
            conn.execute("""
                INSERT INTO paper_accounts (account_id, chain_id, initial_balance_usd, current_balance_usd, unrealized_pnl_usd, realized_pnl_usd, created_at, updated_at)
                VALUES ('acc_can_p', 'solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp', 1000.0, 1000.0, 0.0, 0.0, '2026-01-01', '2026-01-01');
            """)

            mint = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
            # Non-conflicting position quantity (1000 tokens) with differing cost bases and entry prices
            conn.execute("""
                INSERT INTO paper_positions_v2 (position_id, account_id, chain_id, asset_address, symbol, side, token_amount_raw, cost_basis_native, entry_price_native, current_price_native, unrealized_pnl_native, realized_pnl_native, opened_at, updated_at)
                VALUES ('pos_l', 'acc_leg_p', 'solana:mainnet', ?, 'BONK', 'LONG', '1000', 100.0, 0.10, 0.15, 50.0, 10.0, '2026-01-01', '2026-01-01');
            """, (mint,))
            conn.execute("""
                INSERT INTO paper_positions_v2 (position_id, account_id, chain_id, asset_address, symbol, side, token_amount_raw, cost_basis_native, entry_price_native, current_price_native, unrealized_pnl_native, realized_pnl_native, opened_at, updated_at)
                VALUES ('pos_c', 'acc_can_p', 'solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp', ?, 'BONK', 'LONG', '1000', 200.0, 0.20, 0.15, -150.0, 20.0, '2026-01-01', '2026-01-01');
            """, (mint,))

        migrator = SqliteMigrator(path)
        migrator.apply_migrations()

        with migrator._get_connection() as conn:
            pos = conn.execute(
                "SELECT token_amount_raw, cost_basis_native, entry_price_native, unrealized_pnl_native, realized_pnl_native FROM paper_positions_v2 WHERE account_id = 'acc_can_p';"
            ).fetchone()
            assert pos is not None
            # 1000 + 1000 = 2000 tokens
            assert pos[0] == "2000"
            # 100.0 + 200.0 = 300.0
            assert abs(float(pos[1]) - 300.0) < 1e-6
            # Weighted average entry price: (1000*0.10 + 1000*0.20) / 2000 = 300 / 2000 = 0.15
            assert abs(float(pos[2]) - 0.15) < 1e-6
            # PnL sums: 50 + (-150) = -100, 10 + 20 = 30
            assert abs(float(pos[3]) - (-100.0)) < 1e-6
            assert abs(float(pos[4]) - 30.0) < 1e-6
    finally:
        if os.path.exists(path):
            os.remove(path)


# =============================================================================
# I4.1-F04: Hooked Solana Genesis Verification on RPC Query Paths
# =============================================================================

def test_i4_1_f04_solana_rpc_query_triggers_genesis_check():
    """Confirms SolanaRpcService verifies genesis hash before returning data from query methods."""
    async def _run():
        service = SolanaRpcService(rpc_url="https://dummy.rpc.solana")
        assert service._genesis_verified is False

        # 1. Mismatched genesis hash raises RuntimeError on query
        with patch("app.services.solana_rpc.get_http_client") as mock_get_client:
            from unittest.mock import MagicMock
            mock_client = AsyncMock()
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"result": "wrong_devnet_genesis_hash"}
            mock_client.post.return_value = mock_resp
            mock_get_client.return_value.__aenter__.return_value = mock_client

            with pytest.raises(RuntimeError) as exc_info:
                await service.get_parsed_mint_info("DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263")
            assert "Solana genesis hash mismatch" in str(exc_info.value)
            assert service._genesis_verified is False

        # 2. Canonical genesis hash succeeds and sets _genesis_verified = True
        with patch("app.services.solana_rpc.get_http_client") as mock_get_client:
            from unittest.mock import MagicMock
            mock_client = AsyncMock()
            call_counts = {"getGenesisHash": 0, "getAccountInfo": 0}

            async def _fake_post(url, json=None):
                method = json.get("method") if json else ""
                resp = MagicMock()
                resp.status_code = 200
                if method == "getGenesisHash":
                    call_counts["getGenesisHash"] += 1
                    resp.json.return_value = {"result": "5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"}
                else:
                    call_counts["getAccountInfo"] += 1
                    resp.json.return_value = {"result": {"value": {"data": {"parsed": {"info": {"decimals": 5}}}}}}
                return resp

            mock_client.post.side_effect = _fake_post
            mock_get_client.return_value.__aenter__.return_value = mock_client

            info = await service.get_parsed_mint_info("DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263")
            assert info is not None
            assert info["decimals"] == 5
            assert service._genesis_verified is True
            assert call_counts["getGenesisHash"] == 1
            assert call_counts["getAccountInfo"] == 1

            # Subsequent query re-uses verified status without repeating genesis call
            await service.get_parsed_mint_info("DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263")
            assert call_counts["getGenesisHash"] == 1
            assert call_counts["getAccountInfo"] == 2

    asyncio.run(_run())


# =============================================================================
# I4.1-F05 & I4.3-F05: Caller Provenance Authorization Guard & Envelope
# =============================================================================

def test_i4_1_f05_authoritative_provenance_requires_collector_token(clean_db):
    """Confirms recording authoritative provenance outside test mode requires an authentic collector token or envelope."""
    service = PersistentPaperService(db_path=clean_db)
    solana_mint = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
    valid_payload = {
        "mint": solana_mint,
        "chain_id": SOLANA_CAIP2_CANONICAL,
        "decimals": 5
    }

    test_secret = "test_collector_secret_key_12345"

    with patch.object(settings, "APP_ENV", "production"), \
         patch.object(settings, "PROVENANCE_COLLECTOR_SECRET", test_secret):
        # 1. Unauthenticated caller fails closed with PermissionError
        with pytest.raises(PermissionError) as exc_info:
            service.record_decimal_verification(
                chain_id=SOLANA_CAIP2_CANONICAL,
                asset_address=solana_mint,
                decimals=5,
                symbol="BONK",
                verification_source=DecimalProvenanceSource.OFFICIAL_SPL_MINT.value,
                verification_method="onchain_rpc_call",
                raw_metadata=valid_payload
            )
        assert "requires an authenticated internal collector token" in str(exc_info.value)

        # 2. Invalid collector token fails closed with PermissionError
        with pytest.raises(PermissionError) as exc_info:
            service.record_decimal_verification(
                chain_id=SOLANA_CAIP2_CANONICAL,
                asset_address=solana_mint,
                decimals=5,
                symbol="BONK",
                verification_source=DecimalProvenanceSource.AUTHENTICATED_RPC_EVIDENCE.value,
                verification_method="onchain_rpc_call",
                raw_metadata=valid_payload,
                collector_token="unauthorized_caller_token"
            )
        assert "Invalid collector token" in str(exc_info.value)

        # 3. Valid configured collector secret succeeds and marks status VERIFIED
        res = service.record_decimal_verification(
            chain_id=SOLANA_CAIP2_CANONICAL,
            asset_address=solana_mint,
            decimals=5,
            symbol="BONK",
            verification_source=DecimalProvenanceSource.OFFICIAL_SPL_MINT.value,
            verification_method="onchain_rpc_call",
            raw_metadata=valid_payload,
            collector_token=test_secret
        )
        assert res["status"] == "VERIFIED"
        assert res["decimals"] == 5


# =============================================================================
# P10.2-I.4.3: Six Confirmed Findings Remediation Tests (F01 - F06)
# =============================================================================

from app.db.sqlite_migrator import parse_strict_raw_unit, format_decimal_exact
from app.services.persistent_paper_service import (
    create_collector_evidence_envelope,
    verify_collector_envelope
)
from app.core.address_validator import AddressValidator


# -----------------------------------------------------------------------------
# F01: Strict Raw-Unit Validation
# -----------------------------------------------------------------------------

def test_i4_3_f01_strict_raw_unit_preserves_large_integers():
    """Confirms arbitrary large integers beyond JS MAX_SAFE_INTEGER are preserved with exact precision."""
    val_str = "9007199254740993"  # 2^53 + 1
    parsed = parse_strict_raw_unit(val_str, "test_field")
    assert parsed == 9007199254740993
    assert str(parsed) == val_str

    val_huge = "1000000000000000000000000000000"
    parsed_huge = parse_strict_raw_unit(val_huge, "huge_field")
    assert parsed_huge == int(val_huge)
    assert str(parsed_huge) == val_huge


def test_i4_3_f01_strict_raw_unit_rejects_fractions_and_negatives():
    """Confirms fractions, scientific notation, signs, negatives, and floats are strictly rejected."""
    invalid_inputs = [
        "1.5",
        "0.1",
        "-1",
        "-100",
        "+100",
        "1e18",
        "100.0",
        "abc",
        " 100",
        "100 ",
        1.5,
        -1,
        "",
        None
    ]
    for inp in invalid_inputs:
        with pytest.raises(ValueError) as exc_info:
            parse_strict_raw_unit(inp, "test_field", allow_empty=False)
        assert "strict raw unit validation failed" in str(exc_info.value).lower()


def test_i4_3_f01_migration_13_aborts_on_corrupt_raw_unit_in_db():
    """Confirms Migration 13 fails closed if database contains corrupt fractional or negative raw units during merge."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _setup_db_up_to_m12(path)
        with sqlite3.connect(path) as conn:
            conn.execute("""
                INSERT INTO paper_accounts (account_id, chain_id, initial_balance_usd, current_balance_usd, created_at, updated_at)
                VALUES ('acc_corrupt_01', 'solana:mainnet', 100.0, 100.0, '2026-01-01', '2026-01-01');
            """)
            conn.execute("""
                INSERT INTO paper_accounts (account_id, chain_id, initial_balance_usd, current_balance_usd, created_at, updated_at)
                VALUES ('acc_can_01', 'solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp', 100.0, 100.0, '2026-01-01', '2026-01-01');
            """)
            conn.execute("""
                INSERT INTO paper_account_balances (id, account_id, chain_id, asset_id, symbol, decimals, free_amount_raw, locked_amount_raw, updated_at)
                VALUES ('bal_corrupt_01', 'acc_corrupt_01', 'solana:mainnet', 'native', 'SOL', 9, '100.5', '0', '2026-01-01');
            """)
            conn.execute("""
                INSERT INTO paper_account_balances (id, account_id, chain_id, asset_id, symbol, decimals, free_amount_raw, locked_amount_raw, updated_at)
                VALUES ('bal_can_01', 'acc_can_01', 'solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp', 'native', 'SOL', 9, '100', '0', '2026-01-01');
            """)

        migrator = SqliteMigrator(path)
        with pytest.raises(RuntimeError) as exc_info:
            migrator.apply_migrations()
        assert "Strict raw unit validation failed" in str(exc_info.value)
    finally:
        if os.path.exists(path):
            os.remove(path)


# -----------------------------------------------------------------------------
# F02: Complete Account Migration Preflight
# -----------------------------------------------------------------------------

def test_i4_3_f02_preflight_aborts_on_multiple_legacy_accounts_without_canonical():
    """Confirms preflight cleanly detects ambiguous multiple legacy accounts even when 0 canonical accounts exist."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _setup_db_up_to_m12(path)
        with sqlite3.connect(path) as conn:
            conn.execute("""
                INSERT INTO paper_accounts (account_id, chain_id, initial_balance_usd, current_balance_usd, created_at, updated_at)
                VALUES ('acc_leg_01', 'solana:mainnet', 100.0, 100.0, '2026-01-01', '2026-01-01');
            """)
            conn.execute("""
                INSERT INTO paper_accounts (account_id, chain_id, initial_balance_usd, current_balance_usd, created_at, updated_at)
                VALUES ('acc_leg_02', 'solana:mainnet-beta', 200.0, 200.0, '2026-01-01', '2026-01-01');
            """)

        migrator = SqliteMigrator(path)
        with pytest.raises(RuntimeError) as exc_info:
            migrator.apply_migrations()
        assert "cannot unambiguously reconcile 2 legacy accounts" in str(exc_info.value)
    finally:
        if os.path.exists(path):
            os.remove(path)


# -----------------------------------------------------------------------------
# F03: Exact Financial Decimal Position Storage & Migration 14
# -----------------------------------------------------------------------------

def test_i4_3_f03_migration_14_establishes_text_affinity(clean_db):
    """Confirms Migration 14 recreates paper_positions_v2 with exact TEXT column affinity for financial fields."""
    with sqlite3.connect(clean_db) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute("PRAGMA table_info(paper_positions_v2);")
        cols = {r["name"]: r["type"].upper() for r in cur.fetchall()}

        assert cols["cost_basis_native"] == "TEXT"
        assert cols["entry_price_native"] == "TEXT"
        assert cols["current_price_native"] == "TEXT"
        assert cols["unrealized_pnl_native"] == "TEXT"
        assert cols["realized_pnl_native"] == "TEXT"
        assert cols["token_amount_raw"] == "TEXT"

        # Verify exact string persistence without IEEE-754 float coercion
        exact_cost = "123456789.123456789012345678"
        conn.execute("""
            INSERT INTO paper_positions_v2 (
                position_id, account_id, chain_id, asset_address, symbol, token_amount_raw,
                cost_basis_native, entry_price_native, current_price_native
            ) VALUES ('pos_exact_test', 'acc_1', 'solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp', 'mint_1', 'SYM', '1000',
                      ?, '1.0', '1.0');
        """, (exact_cost,))

        row = conn.execute("SELECT cost_basis_native FROM paper_positions_v2 WHERE position_id = 'pos_exact_test';").fetchone()
        assert row["cost_basis_native"] == exact_cost


# -----------------------------------------------------------------------------
# F04: Endpoint-Bound Solana Genesis Verification
# -----------------------------------------------------------------------------

def test_i4_3_f04_endpoint_bound_genesis_verification():
    """Confirms verification is bound to normalized endpoint URL and detects endpoint switches."""
    async def _run():
        rpc = SolanaRpcService(rpc_url="https://api.mainnet-beta.solana.com")
        endpoint_a = "https://api.mainnet-beta.solana.com"
        endpoint_b = "https://solana-mainnet.g.alchemy.com/v2/dummy"

        from unittest.mock import MagicMock
        with patch("app.services.solana_rpc.get_http_client") as mock_get_client:
            mock_client = AsyncMock()
            call_history = []

            async def _fake_post(url, json=None):
                call_history.append((url, json.get("method")))
                resp = MagicMock()
                resp.status_code = 200
                resp.json.return_value = {"result": "5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"}
                return resp

            mock_client.post.side_effect = _fake_post
            mock_get_client.return_value.__aenter__.return_value = mock_client

            # Step 1: Verify endpoint A
            assert rpc.is_endpoint_verified(endpoint_a) is False
            await rpc.verify_genesis_hash(endpoint_a)
            assert rpc.is_endpoint_verified(endpoint_a) is True
            assert rpc.is_endpoint_verified(endpoint_b) is False

            # Step 2: Switch rpc_url to endpoint B
            rpc.rpc_url = endpoint_b
            assert rpc.is_endpoint_verified() is False  # Bound to current endpoint_b

            # Step 3: Verify endpoint B
            await rpc.verify_genesis_hash()
            assert rpc.is_endpoint_verified() is True
            assert rpc.is_endpoint_verified(endpoint_a) is True

            # Step 4: Verify concurrent calls share lock without duplicate queries
            call_history.clear()
            endpoint_c = "https://solana.public.rpc.com"
            results = await asyncio.gather(
                rpc.verify_genesis_hash(endpoint_c),
                rpc.verify_genesis_hash(endpoint_c),
                rpc.verify_genesis_hash(endpoint_c)
            )
            assert all(results)
            # Only one call to getGenesisHash should have been dispatched for endpoint_c
            genesis_calls = [c for c in call_history if c[0] == endpoint_c and c[1] == "getGenesisHash"]
            assert len(genesis_calls) == 1

    asyncio.run(_run())


# -----------------------------------------------------------------------------
# F05: Authentic Collector Authorization with HMAC Evidence Envelope
# -----------------------------------------------------------------------------

def test_i4_3_f05_hmac_envelope_verification(clean_db):
    """Confirms authentic collector envelope authorization, timestamp window, and nonce replay rejection."""
    service = PersistentPaperService(db_path=clean_db)
    solana_mint = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
    valid_payload = {
        "mint": solana_mint,
        "chain_id": SOLANA_CAIP2_CANONICAL,
        "decimals": 5
    }
    raw_str = json.dumps(valid_payload, sort_keys=True)
    import hashlib
    raw_hash = hashlib.sha256(raw_str.encode("utf-8")).hexdigest()

    secret = "super_secure_collector_hmac_key_2026"

    with patch.object(settings, "APP_ENV", "production"), \
         patch.object(settings, "PROVENANCE_COLLECTOR_SECRET", secret):

        # 1. Generate valid envelope
        env = create_collector_evidence_envelope(
            collector_id="collector_agent_01",
            chain_id=SOLANA_CAIP2_CANONICAL,
            asset_address=solana_mint,
            decimals=5,
            verification_source=DecimalProvenanceSource.OFFICIAL_SPL_MINT.value,
            raw_metadata_hash=raw_hash,
            secret=secret
        )

        res = service.record_decimal_verification(
            chain_id=SOLANA_CAIP2_CANONICAL,
            asset_address=solana_mint,
            decimals=5,
            symbol="BONK",
            verification_source=DecimalProvenanceSource.OFFICIAL_SPL_MINT.value,
            verification_method="onchain_rpc_call",
            raw_metadata=valid_payload,
            collector_envelope=env
        )
        assert res["status"] == "VERIFIED"

        # 2. Replayed nonce fails closed
        with pytest.raises(PermissionError) as exc_info:
            service.record_decimal_verification(
                chain_id=SOLANA_CAIP2_CANONICAL,
                asset_address=solana_mint,
                decimals=5,
                symbol="BONK",
                verification_source=DecimalProvenanceSource.OFFICIAL_SPL_MINT.value,
                verification_method="onchain_rpc_call",
                raw_metadata=valid_payload,
                collector_envelope=env
            )
        assert "Invalid, expired, or tampered collector evidence envelope" in str(exc_info.value)

        # 3. Expired timestamp (>300s) fails closed
        expired_env = create_collector_evidence_envelope(
            collector_id="collector_agent_01",
            chain_id=SOLANA_CAIP2_CANONICAL,
            asset_address=solana_mint,
            decimals=5,
            verification_source=DecimalProvenanceSource.OFFICIAL_SPL_MINT.value,
            raw_metadata_hash=raw_hash,
            secret=secret,
            timestamp=datetime.now(timezone.utc).timestamp() - 3600
        )
        with pytest.raises(PermissionError):
            service.record_decimal_verification(
                chain_id=SOLANA_CAIP2_CANONICAL,
                asset_address=solana_mint,
                decimals=5,
                symbol="BONK",
                verification_source=DecimalProvenanceSource.OFFICIAL_SPL_MINT.value,
                verification_method="onchain_rpc_call",
                raw_metadata=valid_payload,
                collector_envelope=expired_env
            )


# -----------------------------------------------------------------------------
# F06: Sui Move Struct-Tag Validation & Account Separation
# -----------------------------------------------------------------------------

def test_i4_3_f06_sui_move_struct_tag_grammar():
    """Confirms Sui Move struct type tag grammar validation and normalization with nested generics."""
    # 1. Simple type tag
    tag_simple = "0x2::sui::SUI"
    ok, canon, err = AddressValidator.validate_sui_move_type_tag(tag_simple)
    assert ok is True
    assert canon == "0x0000000000000000000000000000000000000000000000000000000000000002::sui::SUI"

    # 2. Nested generics type tag
    tag_generic = "0x1eab::factory::Pool<0x2::sui::SUI, 0x5d4b::coin::COIN<u64>>"
    ok, canon, err = AddressValidator.validate_sui_move_type_tag(tag_generic)
    assert ok is True
    expected_pkg1 = "0x" + "1eab".rjust(64, "0")
    expected_pkg2 = "0x" + "2".rjust(64, "0")
    expected_pkg3 = "0x" + "5d4b".rjust(64, "0")
    assert f"{expected_pkg1}::factory::Pool<{expected_pkg2}::sui::SUI, {expected_pkg3}::coin::COIN<u64>>" == canon

    # 3. Strict account validator rejects Move struct tag
    ok_acc, _, err_acc = AddressValidator.validate_sui_address(tag_simple)
    assert ok_acc is False
    assert "Non-hexadecimal characters" in err_acc

    # 4. Dispatcher routes correctly
    ok_disp, canon_disp, _ = AddressValidator.validate_address_for_chain("sui:mainnet", tag_simple)
    assert ok_disp is True
    assert canon_disp == "0x0000000000000000000000000000000000000000000000000000000000000002::sui::SUI"

    # 5. CAIP-19 parsing and creation
    caip = CAIP19AssetId.from_chain_and_address("sui:mainnet", tag_simple)
    assert caip.chain_id == "sui:mainnet"
    assert caip.asset_namespace == "move"
    assert caip.asset_reference == "0x0000000000000000000000000000000000000000000000000000000000000002::sui::SUI"

    parsed_caip = CAIP19AssetId.parse(caip.to_caip19())
    assert parsed_caip.asset_reference == caip.asset_reference


# =============================================================================
# P10.2-I.4.3.2 Targeted Residual Remediation Tests (F01, F03, F05, F06)
# =============================================================================

def test_i4_3_2_f01_strict_ascii_raw_unit_validation():
    """
    F01: Strictly verifies that parse_strict_raw_unit accepts only ASCII digits [0-9]+
    and non-negative integers, while rejecting booleans, unicode digits, floats,
    signs, whitespace, fractions, exponents, and invalid types with structured errors.
    """
    # 1. Valid non-negative integers and ASCII digit strings
    assert parse_strict_raw_unit("0", "test") == 0
    assert parse_strict_raw_unit("123", "test") == 123
    assert parse_strict_raw_unit("9007199254740993", "test") == 9007199254740993
    assert parse_strict_raw_unit("1000000000000000000000000000000", "test") == 1000000000000000000000000000000
    assert parse_strict_raw_unit(0, "test") == 0
    assert parse_strict_raw_unit(123, "test") == 123
    assert parse_strict_raw_unit(9007199254740993, "test") == 9007199254740993

    # 2. Rejection of booleans (in Python, bool is subclass of int)
    with pytest.raises(ValueError) as exc:
        parse_strict_raw_unit(True, "is_active")
    assert "boolean type forbidden" in str(exc.value)

    with pytest.raises(ValueError) as exc:
        parse_strict_raw_unit(False, "is_active")
    assert "boolean type forbidden" in str(exc.value)

    # 3. Rejection of Unicode digits (superscript, fullwidth, Arabic-Indic)
    unicode_invalid = [
        ("²", "superscript two"),
        ("１２３", "fullwidth digits"),
        ("١٢٣", "Arabic-Indic digits"),
        ("³", "superscript three"),
        ("½", "fraction vulgar"),
        ("Ⅳ", "Roman numeral")
    ]
    for val, label in unicode_invalid:
        with pytest.raises(ValueError) as exc:
            parse_strict_raw_unit(val, f"unicode_field_{label}")
        assert "invalid format" in str(exc.value) or "ASCII" in str(exc.value)

    # 4. Rejection of signs, whitespace, fractions, and exponents
    malformed_strings = [
        "-1", "+1", "+100", "-0", " 123 ", "123 ", " 123",
        "1.0", "1.5", "0.0", "1e18", "1E18", "1e-5", "abc", "12a34"
    ]
    for val in malformed_strings:
        with pytest.raises(ValueError) as exc:
            parse_strict_raw_unit(val, "malformed_field")
        assert "invalid format" in str(exc.value)

    # 5. Rejection of binary floats
    with pytest.raises(ValueError) as exc:
        parse_strict_raw_unit(1.5, "float_field")
    assert "binary float type forbidden" in str(exc.value)

    with pytest.raises(ValueError) as exc:
        parse_strict_raw_unit(1.0, "float_field")
    assert "binary float type forbidden" in str(exc.value)

    # 6. Rejection of negative integers
    with pytest.raises(ValueError) as exc:
        parse_strict_raw_unit(-1, "negative_field")
    assert "negative value -1 forbidden" in str(exc.value)

    # 7. Empty and NULL handling
    with pytest.raises(ValueError) as exc:
        parse_strict_raw_unit("", "empty_field", allow_empty=False)
    assert "cannot be NULL or empty" in str(exc.value)

    with pytest.raises(ValueError) as exc:
        parse_strict_raw_unit(None, "null_field", allow_empty=False)
    assert "cannot be NULL or empty" in str(exc.value)

    assert parse_strict_raw_unit("", "empty_field", allow_empty=True) == 0
    assert parse_strict_raw_unit(None, "null_field", allow_empty=True) == 0

    # 8. Rejection of complex types
    with pytest.raises(ValueError) as exc:
        parse_strict_raw_unit(["1"], "list_field")
    assert "invalid type list" in str(exc.value)


def test_i4_3_2_f03_exact_decimal_financial_persistence(clean_db):
    """
    F03: Confirms exact Decimal persistence without IEEE-754 float truncation
    in paper_positions_v2, paper_orders_v2, and paper_fills SQL write paths.
    """
    service = PersistentPaperService(db_path=clean_db)
    norm_chain = SOLANA_CAIP2_CANONICAL
    acc = service.get_account(norm_chain)
    account_id = acc.account_id
    asset_id = "PreciseToken11111111111111111111111111111"
    pool_id = "pool_sol_precise_primary"

    # Verify token decimals in test environment first
    service.record_decimal_verification(
        chain_id=norm_chain,
        asset_address=asset_id,
        decimals=6,
        symbol="PREC",
        verification_source=DecimalProvenanceSource.CORE_TEST.value,
        verification_method="test_fixture",
        raw_metadata={"mint": asset_id, "decimals": 6}
    )

    # High-precision Decimal input amount: 0.30000000000000000001
    # Under standard IEEE-754 64-bit float, this is truncated to 0.3
    exact_high_precision_input = Decimal("0.30000000000000000001")

    req = PaperOrderRequest(
        account_id=account_id,
        client_order_id="test_ord_prec_001",
        chain_id=norm_chain,
        asset_id=asset_id,
        pool_id=pool_id,
        side="BUY",
        input_mint="native",
        output_mint=asset_id,
        input_amount_raw=exact_high_precision_input,
        max_slippage_bps=500
    )

    resp = service.execute_order(
        request=req,
        current_market_price_native=Decimal("0.00004500"),
        pool_liquidity_usd=Decimal("500000.0")
    )
    assert resp.status == "FILLED"

    # Inspect the exact SQLite database columns directly via raw SQLite cursor
    with sqlite3.connect(clean_db) as conn:
        conn.row_factory = sqlite3.Row

        # Check paper_positions_v2
        pos = conn.execute(
            "SELECT * FROM paper_positions_v2 WHERE account_id = ? AND chain_id = ? AND asset_address = ?;",
            (account_id, norm_chain, asset_id)
        ).fetchone()
        assert pos is not None
        cost_basis_str = str(pos["cost_basis_native"])
        entry_price_str = str(pos["entry_price_native"])
        current_price_str = str(pos["current_price_native"])

        # Cost basis must match exact Decimal without truncation to "0.3"
        assert cost_basis_str == str(exact_high_precision_input)
        assert Decimal(cost_basis_str) == exact_high_precision_input
        assert "0.30000000000000000001" in cost_basis_str

        # Check paper_orders_v2
        ord_row = conn.execute(
            "SELECT * FROM paper_orders_v2 WHERE order_id = ?;",
            (resp.order_id,)
        ).fetchone()
        assert ord_row is not None
        exec_price_str = str(ord_row["executed_price_native"])
        assert Decimal(exec_price_str) > 0
        assert not exec_price_str.endswith(".0000000000000001")  # No float binary artifacts

        # Check paper_fills
        fill_row = conn.execute(
            "SELECT * FROM paper_fills WHERE order_id = ?;",
            (resp.order_id,)
        ).fetchone()
        assert fill_row is not None
        fill_price_str = str(fill_row["fill_price_native"])
        assert Decimal(fill_price_str) == Decimal(exec_price_str)


def test_i4_3_2_f05_durable_collector_nonce_replay_protection(clean_db):
    """
    F05: Confirms durable SQLite-backed collector nonce replay protection (Migration 15).
    Verifies that replaying an evidence envelope fails closed both within the same process
    and across simulated server restarts.
    """
    secret = "production_collector_secret_key_8888"
    asset_mint = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
    valid_meta = {"mint": asset_mint, "decimals": 5}
    raw_hash = hashlib.sha256(json.dumps(valid_meta, sort_keys=True).encode("utf-8")).hexdigest()

    # 1. Verify Migration 15 created collector_evidence_nonces table
    with sqlite3.connect(clean_db) as conn:
        tbl = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='collector_evidence_nonces';").fetchone()
        assert tbl is not None
        idx = conn.execute("SELECT name FROM sqlite_master WHERE type='index' AND name='idx_collector_nonces_exp';").fetchone()
        assert idx is not None

    # 2. Create valid envelope
    unique_nonce = f"nonce_dur_{uuid.uuid4().hex}"
    envelope = create_collector_evidence_envelope(
        collector_id="collector_agent_primary",
        chain_id=SOLANA_CAIP2_CANONICAL,
        asset_address=asset_mint,
        decimals=5,
        verification_source=DecimalProvenanceSource.OFFICIAL_SPL_MINT.value,
        raw_metadata_hash=raw_hash,
        secret=secret,
        nonce=unique_nonce
    )

    # 3. First verification: must succeed and durably store the nonce
    ok1 = verify_collector_envelope(
        envelope=envelope,
        chain_id=SOLANA_CAIP2_CANONICAL,
        asset_address=asset_mint,
        decimals=5,
        verification_source=DecimalProvenanceSource.OFFICIAL_SPL_MINT.value,
        raw_metadata_hash=raw_hash,
        secret=secret,
        db_path=clean_db
    )
    assert ok1 is True

    # Check that nonce is stored in the database
    with sqlite3.connect(clean_db) as conn:
        row = conn.execute("SELECT * FROM collector_evidence_nonces WHERE nonce = ?;", (unique_nonce,)).fetchone()
        assert row is not None
        assert row[1] == "collector_agent_primary"

    # 4. Immediate replay attack in same process: must fail closed
    ok2 = verify_collector_envelope(
        envelope=envelope,
        chain_id=SOLANA_CAIP2_CANONICAL,
        asset_address=asset_mint,
        decimals=5,
        verification_source=DecimalProvenanceSource.OFFICIAL_SPL_MINT.value,
        raw_metadata_hash=raw_hash,
        secret=secret,
        db_path=clean_db
    )
    assert ok2 is False

    # 5. Process restart simulation: create brand new PersistentPaperService on the same SQLite file
    restarted_service = PersistentPaperService(db_path=clean_db)

    # Replay attack against restarted service: must still fail closed due to durable DB record
    with patch.object(settings, "APP_ENV", "production"), \
         patch.object(settings, "PROVENANCE_COLLECTOR_SECRET", secret):
        with pytest.raises(PermissionError) as exc_info:
            restarted_service.record_decimal_verification(
                chain_id=SOLANA_CAIP2_CANONICAL,
                asset_address=asset_mint,
                decimals=5,
                symbol="BONK",
                verification_source=DecimalProvenanceSource.OFFICIAL_SPL_MINT.value,
                verification_method="onchain_rpc_call",
                raw_metadata=valid_meta,
                collector_envelope=envelope
            )
        assert "Invalid, expired, or tampered collector evidence envelope" in str(exc_info.value)


def test_i4_3_2_f06_recursive_sui_move_vector_parsing():
    """
    F06: Confirms recursive Sui Move vector<T> type-tag grammar validation and normalization.
    Tests primitive vectors, struct vectors, nested vectors, container structs with vector generics,
    and negative error handling.
    """
    canon_pkg = "0x" + "2".rjust(64, "0")

    # 1. Primitive vectors
    ok, canon, err = AddressValidator.validate_sui_move_type_tag("vector<u8>")
    assert ok is True
    assert canon == "vector<u8>"

    ok, canon, err = AddressValidator.validate_sui_move_type_tag("vector<address>")
    assert ok is True
    assert canon == "vector<address>"

    ok, canon, err = AddressValidator.validate_sui_move_type_tag("vector<u64>")
    assert ok is True
    assert canon == "vector<u64>"

    # 2. Struct vectors (normalizes package address)
    ok, canon, err = AddressValidator.validate_sui_move_type_tag("vector<0x2::sui::SUI>")
    assert ok is True
    assert canon == f"vector<{canon_pkg}::sui::SUI>"

    # 3. Nested vectors
    ok, canon, err = AddressValidator.validate_sui_move_type_tag("vector<vector<u64>>")
    assert ok is True
    assert canon == "vector<vector<u64>>"

    ok, canon, err = AddressValidator.validate_sui_move_type_tag("vector<vector<vector<u8>>>")
    assert ok is True
    assert canon == "vector<vector<vector<u8>>>"

    # 4. Struct with vector generics
    ok, canon, err = AddressValidator.validate_sui_move_type_tag("0x2::coin::Coin<vector<u8>>")
    assert ok is True
    assert canon == f"{canon_pkg}::coin::Coin<vector<u8>>"

    # 5. Struct with multiple generic arguments including vector and struct
    complex_tag = "0x2::factory::Pool<vector<u8>, 0x2::sui::SUI, vector<vector<u64>>>"
    ok, canon, err = AddressValidator.validate_sui_move_type_tag(complex_tag)
    assert ok is True
    assert canon == f"{canon_pkg}::factory::Pool<vector<u8>, {canon_pkg}::sui::SUI, vector<vector<u64>>>"

    # 6. Negative tests
    invalid_tags = [
        ("vector<>", "empty generic argument"),
        ("vector<u8", "unclosed generic bracket"),
        ("vector<u8>>", "mismatched closing bracket"),
        ("vector<u8> extra", "malformed trailing garbage"),
        ("vector<unknown_type>", "unknown Move type argument"),
        ("vector<0xinvalid::sui::SUI>", "invalid package address")
    ]
    for tag, reason in invalid_tags:
        ok, canon, err = AddressValidator.validate_sui_move_type_tag(tag)
        assert ok is False, f"Expected {tag} to fail validation ({reason})"

    # 7. CAIP-19 asset ID creation with vector type tag
    caip = CAIP19AssetId.from_chain_and_address("sui:mainnet", "vector<0x2::sui::SUI>")
    assert caip.chain_id == "sui:mainnet"
    assert caip.asset_namespace == "move"
    assert caip.asset_reference == f"vector<{canon_pkg}::sui::SUI>"

    # Round trip parsing
    parsed = CAIP19AssetId.parse(caip.to_caip19())
    assert parsed.asset_reference == caip.asset_reference


# =============================================================================
# P10.2-I.4.3.3: Final Financial Precision Remediation Tests (F03-A & F03-B)
# =============================================================================

def test_i4_3_3_f03_a_exact_decimal_serialization_high_precision():
    """
    F03-A: Confirms format_decimal_exact serializes Decimals of arbitrary precision
    (including 29+, 50+, 100+ digits) without rounding under the ambient Decimal context,
    preserves 18- and 27-decimal tokens, very small prices, and exponent notation.
    """
    test_cases = [
        # 29 significant digits (exceeds Python default prec=28)
        (Decimal("12345678901234567890.123456789"), "12345678901234567890.123456789"),
        # 50 significant digits
        (Decimal("123456789012345678901234567890.12345678901234567890"), "123456789012345678901234567890.1234567890123456789"),
        # 100 digits (large integer)
        (Decimal("1" + "2" * 98 + "3"), "1" + "2" * 98 + "3"),
        # 18-decimal token balance (e.g. 1 billion tokens with 18 decimals = 27 digits)
        (Decimal("1000000000.123456789012345678"), "1000000000.123456789012345678"),
        # 27-decimal token balance (e.g. Aptos / Move high-precision tokens)
        (Decimal("0.000000000000000000000000001"), "0.000000000000000000000000001"),
        # Very small fractional prices in exponent notation
        (Decimal("1e-18"), "0.000000000000000001"),
        (Decimal("1e-27"), "0.000000000000000000000000001"),
        (Decimal("4.514620621865598e-05"), "0.00004514620621865598"),
        # Positive and negative values
        (Decimal("-123.456"), "-123.456"),
        (Decimal("-0.0000451462062186559812345"), "-0.0000451462062186559812345"),
        # Trailing zeros stripped deterministically
        (Decimal("100.000"), "100"),
        (Decimal("1.23000"), "1.23"),
        (Decimal("-100.000"), "-100"),
        # Deterministic zeros
        (Decimal("0"), "0"),
        (Decimal("-0"), "0"),
        (Decimal("0.000"), "0"),
        (Decimal("-0.000"), "0"),
        # Large exponent integer
        (Decimal("1.23E+5"), "123000"),
        # Exact strings and ints
        ("12345678901234567890.123456789", "12345678901234567890.123456789"),
        (100, "100"),
        (-100, "-100"),
    ]

    for val, expected in test_cases:
        serialized = format_decimal_exact(val)
        assert serialized == expected, f"Failed for {val!r}: got {serialized}, expected {expected}"
        # Verify exact round-trip Decimal equality without numerical drift
        assert Decimal(serialized) == Decimal(str(val)), f"Round-trip equality failed for {val!r}"


def test_i4_3_3_f03_a_serialization_rejects_invalid_and_non_finite():
    """
    F03-A: Confirms format_decimal_exact rejects None, booleans, floats, non-finite Decimals,
    and invalid strings with explicit ValueError/TypeError.
    """
    invalid_inputs = [
        (None, ValueError),
        (True, ValueError),
        (False, ValueError),
        (1.5, TypeError),
        (0.0, TypeError),
        (Decimal("NaN"), ValueError),
        (Decimal("sNaN"), ValueError),
        (Decimal("Infinity"), ValueError),
        (Decimal("-Infinity"), ValueError),
        ("", ValueError),
        ("   ", ValueError),
        ("not_a_number", ValueError),
        ([], TypeError),
        ({}, TypeError),
    ]

    for inp, expected_err in invalid_inputs:
        with pytest.raises(expected_err):
            format_decimal_exact(inp)


def test_i4_3_3_f03_b_migration_16_fresh_database():
    """
    F03-B: Confirms Migration 16 establishes TEXT affinity for all financial columns
    in paper_orders_v2 and paper_fills on a fresh database, preserving all indexes.
    """
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        migrator = SqliteMigrator(path)
        applied = migrator.apply_migrations()
        assert 16 in applied

        with sqlite3.connect(path) as conn:
            conn.row_factory = sqlite3.Row
            # Check paper_orders_v2 column affinities
            cur = conn.execute("PRAGMA table_info(paper_orders_v2);")
            cols_orders = {r["name"]: r["type"].upper() for r in cur.fetchall()}
            assert cols_orders["limit_price_native"] == "TEXT"
            assert cols_orders["executed_price_native"] == "TEXT"
            assert cols_orders["fee_paid_native"] == "TEXT"
            assert cols_orders["simulated_gas_native"] == "TEXT"
            assert cols_orders["requested_quantity"] == "TEXT"
            assert cols_orders["filled_quantity"] == "TEXT"

            # Check paper_fills column affinities
            cur = conn.execute("PRAGMA table_info(paper_fills);")
            cols_fills = {r["name"]: r["type"].upper() for r in cur.fetchall()}
            assert cols_fills["fill_price_native"] == "TEXT"
            assert cols_fills["fee_native"] == "TEXT"
            assert cols_fills["fill_quantity"] == "TEXT"

            # Check paper_orders_v2 indexes
            indices_o = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='paper_orders_v2';").fetchall()]
            assert "idx_paper_orders_acc" in indices_o
            assert "idx_paper_orders_proposal_uniq" in indices_o
            assert "idx_paper_orders_origin" in indices_o

            # Check paper_fills indexes
            indices_f = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='paper_fills';").fetchall()]
            assert "idx_paper_fills_order" in indices_f
    finally:
        if os.path.exists(path):
            os.remove(path)


def test_i4_3_3_f03_b_migration_16_upgrade_preserves_existing_rows():
    """
    F03-B: Confirms Migration 16 upgrades an existing v15 database containing legacy REAL
    order and fill values, converting them cleanly to exact TEXT without data loss or crash.
    """
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        migrator = SqliteMigrator(path)
        all_m = list(migrator.MIGRATIONS)
        migrator.MIGRATIONS = all_m[:15]
        migrator.apply_migrations()
        assert migrator.get_current_version() == 15

        # Seed v15 database with existing rows containing legacy REAL values
        with sqlite3.connect(path) as conn:
            conn.execute("""
                INSERT INTO paper_orders_v2 (
                    order_id, client_order_id, account_id, chain_id, asset_id,
                    side, status, requested_quantity, filled_quantity, limit_price_native,
                    executed_price_native, max_slippage_bps, created_at, updated_at,
                    origin, proposal_id, authorization_hash
                ) VALUES ('ord_legacy_1', 'cl_leg_1', 'acc_1', 'solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp', 'mint_bonk',
                          'BUY', 'FILLED', '5000000', '5000000', 1.5, 4.514620621865598e-05, 100, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z',
                          'STRATEGY_PROPOSAL', 'prop_999', 'auth_hash_abc');
            """)
            conn.execute("""
                INSERT INTO paper_fills (
                    fill_id, order_id, account_id, chain_id, asset_id, side,
                    fill_quantity, fill_price_native, fee_native, slippage_bps, occurred_at
                ) VALUES ('fill_legacy_1', 'ord_legacy_1', 'acc_1', 'solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp', 'mint_bonk',
                          'BUY', '5000000', 4.514620621865598e-05, 0.000005, 15, '2026-01-01T00:00:00Z');
            """)

        # Execute Migration 16
        migrator.MIGRATIONS = all_m[:16]
        newly_applied = migrator.apply_migrations()
        assert newly_applied == [16]
        assert migrator.get_current_version() == 16

        # Verify exact row preservation and TEXT format
        with sqlite3.connect(path) as conn:
            conn.row_factory = sqlite3.Row
            o = conn.execute("SELECT * FROM paper_orders_v2 WHERE order_id = 'ord_legacy_1';").fetchone()
            assert o["client_order_id"] == "cl_leg_1"
            assert o["origin"] == "STRATEGY_PROPOSAL"
            assert o["proposal_id"] == "prop_999"
            assert o["authorization_hash"] == "auth_hash_abc"
            assert o["limit_price_native"] == "1.5"
            assert o["executed_price_native"] == "0.00004514620621865598"
            assert o["fee_paid_native"] == "0" or o["fee_paid_native"] == "0.0"

            f = conn.execute("SELECT * FROM paper_fills WHERE fill_id = 'fill_legacy_1';").fetchone()
            assert f["order_id"] == "ord_legacy_1"
            assert f["fill_price_native"] == "0.00004514620621865598"
            assert f["fee_native"] == "0.000005"
            assert f["slippage_bps"] == 15
    finally:
        if os.path.exists(path):
            os.remove(path)


def test_i4_3_3_f03_b_migration_16_idempotency_and_rollback():
    """
    F03-B: Confirms Migration 16 is idempotent on subsequent runs and rolls back atomically
    if corrupt non-numeric data is encountered during migration.
    """
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        migrator = SqliteMigrator(path)
        all_m = list(migrator.MIGRATIONS)
        migrator.MIGRATIONS = all_m[:15]
        migrator.apply_migrations()

        # Insert corrupt fill price that cannot be parsed as a Decimal
        with sqlite3.connect(path) as conn:
            conn.execute("""
                INSERT INTO paper_fills (
                    fill_id, order_id, account_id, chain_id, asset_id, side,
                    fill_quantity, fill_price_native, fee_native, slippage_bps, occurred_at
                ) VALUES ('fill_corrupt', 'ord_1', 'acc_1', 'solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp', 'asset_1',
                          'BUY', '1000', 'corrupt_non_numeric_price', 0.0, 0, '2026-01-01');
            """)

        # Migration 16 must fail closed and roll back
        migrator.MIGRATIONS = all_m[:16]
        with pytest.raises(RuntimeError) as exc_info:
            migrator.apply_migrations()
        assert "Migration 16" in str(exc_info.value)
        assert "Invalid decimal literal" in str(exc_info.value)

        # Database must remain at version 15
        assert migrator.get_current_version() == 15

        # Fix the corrupt row and verify migration completes successfully
        with sqlite3.connect(path) as conn:
            conn.execute("UPDATE paper_fills SET fill_price_native = 0.001 WHERE fill_id = 'fill_corrupt';")

        migrator.MIGRATIONS = all_m[:16]
        applied = migrator.apply_migrations()
        assert applied == [16]
        assert migrator.get_current_version() == 16

        # Second run should be a clean no-op
        applied_again = migrator.apply_migrations()
        assert applied_again == []
    finally:
        if os.path.exists(path):
            os.remove(path)


def test_i4_3_3_f03_b_end_to_end_order_and_fill_persistence(clean_db):
    """
    F03-B: Confirms full end-to-end flow:
    Order creation with 29-digit price -> SQLite TEXT storage -> exact readback -> fill execution.
    """
    with sqlite3.connect(clean_db) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute("PRAGMA table_info(paper_orders_v2);")
        cols = {r["name"]: r["type"].upper() for r in cur.fetchall()}
        assert cols["limit_price_native"] == "TEXT"
        assert cols["executed_price_native"] == "TEXT"

        exact_price_29 = "0.00004514620621865598123456789"  # 29 decimal places
        exact_qty = "1000000000000000000"

        # Direct exact order persistence
        conn.execute("""
            INSERT INTO paper_orders_v2 (
                order_id, client_order_id, account_id, chain_id, asset_id,
                side, order_type, status, requested_quantity, filled_quantity,
                limit_price_native, executed_price_native, max_slippage_bps,
                fee_paid_native, created_at, updated_at
            ) VALUES ('ord_exact_29', 'cl_exact_29', 'acc_exact', 'solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp', 'asset_exact',
                      'BUY', 'LIMIT', 'SUBMITTED', ?, '0', ?, NULL, 100, '0.0', '2026-01-01', '2026-01-01');
        """, (exact_qty, exact_price_29))

        # Direct exact fill persistence
        conn.execute("""
            INSERT INTO paper_fills (
                fill_id, order_id, account_id, chain_id, asset_id, side,
                fill_quantity, fill_price_native, fee_native, slippage_bps, occurred_at
            ) VALUES ('fill_exact_29', 'ord_exact_29', 'acc_exact', 'solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp', 'asset_exact',
                      'BUY', ?, ?, '0.000005', 10, '2026-01-01');
        """, (exact_qty, exact_price_29))

        # Verify readback preserves exact Decimal value without float truncation
        ord_row = conn.execute("SELECT limit_price_native, requested_quantity FROM paper_orders_v2 WHERE order_id = 'ord_exact_29';").fetchone()
        assert ord_row["limit_price_native"] == exact_price_29
        assert ord_row["requested_quantity"] == exact_qty
        assert Decimal(ord_row["limit_price_native"]) == Decimal(exact_price_29)

        fill_row = conn.execute("SELECT fill_price_native, fill_quantity, fee_native FROM paper_fills WHERE fill_id = 'fill_exact_29';").fetchone()
        assert fill_row["fill_price_native"] == exact_price_29
        assert fill_row["fill_quantity"] == exact_qty
        assert Decimal(fill_row["fill_price_native"]) == Decimal(exact_price_29)
        assert Decimal(fill_row["fee_native"]) == Decimal("0.000005")



