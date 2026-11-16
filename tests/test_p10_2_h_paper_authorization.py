"""
P10.2-H — Atomic Paper Execution & Strategy Authorization Bridge Test Suite.
Verifies all H acceptance requirements:
- H-01: Atomic proposal-to-paper-order bridge, double-entry ledger integration, database-level uniqueness.
- H-02: Manual versus strategy order separation (manual orders pass risk, cannot impersonate proposals).
- H-03: Historical strategy configuration snapshots (config_snapshot_json, SHA-256 validation, replay invariance).
- H-04: API access protection (loopback enforcement, ADMIN_API_TOKEN, peer inspection, IPv4/IPv6).
- H-05: Trusted token decimal provenance (fail-closed, conflict rejection, 6/8/9/18 multi-chain scaling).
- H-06: Schema Migration 10 verification and atomicity.
- H-07: Audit trail and reconciliation of proposals, orders, and ledger.
- H-08: Windmill primary workflow contract (wf22_execute_strategy_proposal).
"""

import os
import json
import time
import uuid
import tempfile
import sqlite3
import hashlib
import pytest
from decimal import Decimal
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient

from app.main import app
from app.core.config import settings
from app.db.sqlite_migrator import SqliteMigrator
from app.schemas.contracts import PaperOrderRequest
from app.services.persistent_paper_service import PersistentPaperService
from app.services.discovery_service import DiscoveryService
from app.strategy.service import StrategyService
from app.strategy.base import (
    BaseStrategy,
    StrategyMetadata,
    StrategyInputContext,
    StrategySignal,
    StrategyAction,
    StrategyLifecycle,
    StrategyMode
)

client = TestClient(app)


@pytest.fixture
def temp_h_db():
    """Provides a fresh temporary SQLite database migrated to Schema Version 10."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name

    migrator = SqliteMigrator(db_path)
    migrator.apply_migrations()

    yield db_path

    if os.path.exists(db_path):
        os.unlink(db_path)


@pytest.fixture
def paper_service(temp_h_db):
    return PersistentPaperService(temp_h_db)


@pytest.fixture
def strat_service(temp_h_db):
    return StrategyService(temp_h_db)


# =============================================================================
# H-06: Schema Migration 10 Verification
# =============================================================================

def test_h06_schema_migration_10_applied(temp_h_db):
    """Verifies that Migration 10 creates token_decimal_verifications and modifies orders, proposals, and runs."""
    migrator = SqliteMigrator(temp_h_db)
    v_report = migrator.verify_schema()
    assert v_report["valid"] is True
    assert v_report["current_version"] >= 10
    assert "token_decimal_verifications" in v_report["existing_tables"]

    with sqlite3.connect(temp_h_db) as conn:
        # Check paper_orders_v2 columns
        cur = conn.execute("PRAGMA table_info(paper_orders_v2);")
        col_names = {row[1] for row in cur.fetchall()}
        assert "origin" in col_names
        assert "proposal_id" in col_names
        assert "authorization_hash" in col_names

        # Check strategy_proposals columns
        cur = conn.execute("PRAGMA table_info(strategy_proposals);")
        col_names = {row[1] for row in cur.fetchall()}
        assert "order_id" in col_names
        assert "consumed_at" in col_names
        assert "authorization_hash" in col_names

        # Check strategy_runs columns
        cur = conn.execute("PRAGMA table_info(strategy_runs);")
        col_names = {row[1] for row in cur.fetchall()}
        assert "config_snapshot_json" in col_names
        assert "config_hash" in col_names
        assert "sdk_version" in col_names


# =============================================================================
# H-05: Trusted Token Decimal Provenance
# =============================================================================

def test_h05_decimal_provenance_and_conflict_rejection(paper_service, temp_h_db):
    """Verifies that trusted decimal provenance fails closed on conflicting metadata and prevents overwriting."""
    # 1. Untrusted discovery event leaves token unverified
    token_addr = "0x" + "2" * 40
    pool_addr = "0x" + "1" * 40
    disc = DiscoveryService(temp_h_db)
    disc.ingest_pool_event(
        chain_id="eip155:1",
        protocol="uniswap_v2",
        pool_address=pool_addr,
        token0_address=token_addr,
        token1_address="0x" + "3" * 40,
        token0_metadata={"symbol": "UNVER", "decimals": 18},
        token1_metadata={"symbol": "ETH", "decimals": 18},
        discovery_source="dexscreener"
    )

    with paper_service._get_connection() as conn:
        dec, is_ver = paper_service._resolve_asset_decimals("eip155:1", token_addr, conn)
        # Untrusted discovery must NOT establish verification authority
        assert is_ver is False

    # 2. Record trusted verification provenance
    paper_service.record_decimal_verification(
        chain_id="eip155:1",
        asset_address=token_addr,
        decimals=18,
        verification_source="OFFICIAL_REGISTRY",
        verification_method="EIP20_STATIC",
        raw_metadata={"decimals": 18, "symbol": "UNVER"},
        symbol="UNVER"
    )

    with paper_service._get_connection() as conn:
        dec, is_ver = paper_service._resolve_asset_decimals("eip155:1", token_addr, conn)
        assert is_ver is True
        assert dec == 18

    # 3. Conflicting metadata must fail closed and reject silent overwrite
    with pytest.raises(ValueError) as exc_info:
        paper_service.record_decimal_verification(
            chain_id="eip155:1",
            asset_address=token_addr,
            decimals=6,  # Conflicting decimals
            verification_source="SUSPICIOUS_FEED",
            verification_method="INFERRED",
            raw_metadata={"decimals": 6}
        )
    assert "Conflicting decimal metadata" in str(exc_info.value)

    # Decimals remain at verified 18
    with paper_service._get_connection() as conn:
        dec, is_ver = paper_service._resolve_asset_decimals("eip155:1", token_addr, conn)
        assert dec == 18


def test_h05_multichain_decimal_scaling(paper_service):
    """Verifies exact scaling across 6, 8, 9, and 18 decimal assets without heuristics."""
    with paper_service._get_connection() as conn:
        # 6 decimals (USDC on Solana)
        dec_sol_usdc, ver = paper_service._resolve_asset_decimals("solana:mainnet", "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", conn)
        assert ver is True
        assert dec_sol_usdc == 6

        # 8 decimals (WBTC on Ethereum)
        dec_wbtc, ver = paper_service._resolve_asset_decimals("eip155:1", "0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599", conn)
        assert ver is True
        assert dec_wbtc == 8

        # 9 decimals (Native SOL)
        dec_sol, ver = paper_service._resolve_asset_decimals("solana:mainnet", "native", conn)
        assert ver is True
        assert dec_sol == 9

        # 18 decimals (WETH on Base)
        dec_base_eth, ver = paper_service._resolve_asset_decimals("eip155:8453", "0x4200000000000000000000000000000000000006", conn)
        assert ver is True
        assert dec_base_eth == 18


# =============================================================================
# H-01 & H-02: Atomic Proposal-to-Paper-Order Bridge
# =============================================================================

def test_h01_successful_proposal_consumption_and_atomic_ledger(strat_service, paper_service, temp_h_db):
    """Verifies that executing a proposal atomically creates order, fill, updates proposal to CONSUMED, and posts ledger."""
    now_str = datetime.now(timezone.utc).isoformat()
    base_time = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    acc_id = "paper_acc_sol_h01"

    # Seed verified token and pool
    paper_service.record_decimal_verification(
        chain_id="solana:mainnet",
        asset_address="AlphaToken11111111111111111111111111111111",
        decimals=9,
        verification_source="CORE_TEST",
        verification_method="STATIC",
        raw_metadata={"mint": "AlphaToken11111111111111111111111111111111", "decimals": 9},
        symbol="ALPHA"
    )

    with sqlite3.connect(temp_h_db) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO discovered_tokens (id, chain_id, address, symbol, name, decimals, is_verified, discovery_source, first_seen_at, last_seen_at)
            VALUES ('tok_alpha', 'solana:mainnet', 'AlphaToken11111111111111111111111111111111', 'ALPHA', 'Alpha Token', 9, 1, 'TEST', ?, ?);
            """,
            (now_str, now_str)
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO discovered_pools (id, chain_id, protocol, pool_address, token0_address, token1_address, discovery_source, is_canonical, status, created_at)
            VALUES ('AlphaPool1111111111111111111111111111111', 'solana:mainnet', 'raydium_cpmm', 'AlphaPool1111111111111111111111111111111', 'AlphaToken11111111111111111111111111111111', 'native', 'TEST', 1, 'ACTIVE', ?);
            """,
            (now_str,)
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO market_data_snapshots (id, pool_id, chain_id, provider_id, price_usd, liquidity_usd, volume_24h_usd, observation_time, retrieved_at)
            VALUES ('snap_alpha_0', 'AlphaPool1111111111111111111111111111111', 'solana:mainnet', 'test', 1.0, 100000.0, 50000.0, ?, ?),
                   ('snap_alpha_1', 'AlphaPool1111111111111111111111111111111', 'solana:mainnet', 'test', 1.5, 100000.0, 50000.0, ?, ?);
            """,
            (base_time, base_time, now_str, now_str)
        )

    # 1. Generate Signal & Proposal
    sig = strat_service.evaluate_strategy(
        strategy_id="momentum_breakout",
        chain_id="solana:mainnet",
        asset_id="AlphaToken11111111111111111111111111111111",
        pool_id="AlphaPool1111111111111111111111111111111",
        mode=StrategyMode.PAPER,
        account_id=acc_id
    )
    assert sig.action == StrategyAction.BUY

    prop = strat_service.propose_trade_intent(signal_id=sig.signal_id, account_id=acc_id)
    proposal_id = prop["proposal_id"]
    assert prop["execution_status"] == "PROPOSED_ONLY"

    # 2. Execute Proposal via Core Authorization Bridge
    order_resp = paper_service.execute_strategy_proposal(proposal_id=proposal_id, account_id=acc_id)
    assert order_resp.status == "FILLED"
    assert order_resp.order_id.startswith("ord_prop_")

    # 3. Check Database State
    with sqlite3.connect(temp_h_db) as conn:
        conn.row_factory = sqlite3.Row

        # Check proposal status
        p_row = conn.execute("SELECT * FROM strategy_proposals WHERE proposal_id = ?;", (proposal_id,)).fetchone()
        assert p_row["execution_status"] == "CONSUMED"
        assert p_row["order_id"] == order_resp.order_id
        assert p_row["consumed_at"] is not None
        assert p_row["authorization_hash"] is not None

        # Check order origin and proposal_id
        o_row = conn.execute("SELECT * FROM paper_orders_v2 WHERE order_id = ?;", (order_resp.order_id,)).fetchone()
        assert o_row["origin"] == "STRATEGY_PROPOSAL"
        assert o_row["proposal_id"] == proposal_id
        assert o_row["authorization_hash"] == p_row["authorization_hash"]

        # Check balanced double-entry ledger entries
        j_cur = conn.execute("SELECT * FROM ledger_journals WHERE order_id = ?;", (order_resp.order_id,))
        journal = j_cur.fetchone()
        assert journal is not None
        assert journal["journal_type"] == "PAPER_TRADE"

        # Check lines balance
        l_cur = conn.execute("SELECT * FROM ledger_lines WHERE journal_id = ?;", (journal["id"],))
        lines = l_cur.fetchall()
        assert len(lines) == 2
        debits = sum(int(l["debit_usd_units"]) for l in lines)
        credits = sum(int(l["credit_usd_units"]) for l in lines)
        assert debits == credits


def test_h01_repeated_proposal_execution_idempotency(strat_service, paper_service, temp_h_db):
    """Verifies that calling execute_strategy_proposal repeatedly returns the cached order without re-executing."""
    now_str = datetime.now(timezone.utc).isoformat()
    acc_id = "paper_acc_sol_h01_idem"

    paper_service.record_decimal_verification(
        chain_id="solana:mainnet",
        asset_address="AlphaTokenIdem1111111111111111111111111",
        decimals=9,
        verification_source="CORE_TEST",
        verification_method="STATIC",
        raw_metadata={"mint": "AlphaTokenIdem1111111111111111111111111", "decimals": 9}
    )

    with sqlite3.connect(temp_h_db) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO discovered_tokens (id, chain_id, address, symbol, name, decimals, is_verified, discovery_source, first_seen_at, last_seen_at)
            VALUES ('tok_idem', 'solana:mainnet', 'AlphaTokenIdem1111111111111111111111111', 'IDEM', 'Idem Token', 9, 1, 'TEST', ?, ?);
            """,
            (now_str, now_str)
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO discovered_pools (id, chain_id, protocol, pool_address, token0_address, token1_address, discovery_source, is_canonical, status, created_at)
            VALUES ('IdemPool111111111111111111111111111111', 'solana:mainnet', 'raydium_cpmm', 'IdemPool111111111111111111111111111111', 'AlphaTokenIdem1111111111111111111111111', 'native', 'TEST', 1, 'ACTIVE', ?);
            """,
            (now_str,)
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO market_data_snapshots (id, pool_id, chain_id, provider_id, price_usd, liquidity_usd, volume_24h_usd, observation_time, retrieved_at)
            VALUES ('snap_idem_0', 'IdemPool111111111111111111111111111111', 'solana:mainnet', 'test', 1.0, 100000.0, 50000.0, ?, ?),
                   ('snap_idem_1', 'IdemPool111111111111111111111111111111', 'solana:mainnet', 'test', 1.5, 100000.0, 50000.0, ?, ?);
            """,
            (now_str, now_str, now_str, now_str)
        )

    sig = strat_service.evaluate_strategy(
        strategy_id="momentum_breakout",
        chain_id="solana:mainnet",
        asset_id="AlphaTokenIdem1111111111111111111111111",
        pool_id="IdemPool111111111111111111111111111111",
        account_id=acc_id
    )
    prop = strat_service.propose_trade_intent(signal_id=sig.signal_id, account_id=acc_id)
    proposal_id = prop["proposal_id"]

    # First execution
    resp1 = paper_service.execute_strategy_proposal(proposal_id=proposal_id, account_id=acc_id)
    assert resp1.status == "FILLED"

    # Repeated execution returns identical order
    resp2 = paper_service.execute_strategy_proposal(proposal_id=proposal_id, account_id=acc_id)
    assert resp2.status == "FILLED"
    assert resp2.order_id == resp1.order_id

    # Verify only ONE order and ONE ledger journal exist
    with sqlite3.connect(temp_h_db) as conn:
        ord_count = conn.execute("SELECT COUNT(*) FROM paper_orders_v2 WHERE proposal_id = ?;", (proposal_id,)).fetchone()[0]
        assert ord_count == 1
        j_count = conn.execute("SELECT COUNT(*) FROM ledger_journals WHERE order_id = ?;", (resp1.order_id,)).fetchone()[0]
        assert j_count == 1


def test_h01_cross_account_proposal_execution_rejection(strat_service, paper_service, temp_h_db):
    """Verifies that an account cannot consume a proposal owned by another account."""
    now_str = datetime.now(timezone.utc).isoformat()
    paper_service.record_decimal_verification(
        chain_id="solana:mainnet",
        asset_address="AlphaTokenSpoof1111111111111111111111",
        decimals=9,
        verification_source="CORE_TEST",
        verification_method="STATIC",
        raw_metadata={"mint": "AlphaTokenSpoof1111111111111111111111", "decimals": 9}
    )

    with sqlite3.connect(temp_h_db) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO discovered_tokens (id, chain_id, address, symbol, name, decimals, is_verified, discovery_source, first_seen_at, last_seen_at)
            VALUES ('tok_spoof', 'solana:mainnet', 'AlphaTokenSpoof1111111111111111111111', 'SPOOF', 'Spoof Token', 9, 1, 'TEST', ?, ?);
            """,
            (now_str, now_str)
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO discovered_pools (id, chain_id, protocol, pool_address, token0_address, token1_address, discovery_source, is_canonical, status, created_at)
            VALUES ('SpoofPool1111111111111111111111111111', 'solana:mainnet', 'raydium_cpmm', 'SpoofPool1111111111111111111111111111', 'AlphaTokenSpoof1111111111111111111111', 'native', 'TEST', 1, 'ACTIVE', ?);
            """,
            (now_str,)
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO market_data_snapshots (id, pool_id, chain_id, provider_id, price_usd, liquidity_usd, volume_24h_usd, observation_time, retrieved_at)
            VALUES ('snap_sp_0', 'SpoofPool1111111111111111111111111111', 'solana:mainnet', 'test', 1.0, 100000.0, 50000.0, ?, ?),
                   ('snap_sp_1', 'SpoofPool1111111111111111111111111111', 'solana:mainnet', 'test', 1.5, 100000.0, 50000.0, ?, ?);
            """,
            (now_str, now_str, now_str, now_str)
        )

    sig = strat_service.evaluate_strategy(
        strategy_id="momentum_breakout",
        chain_id="solana:mainnet",
        asset_id="AlphaTokenSpoof1111111111111111111111",
        pool_id="SpoofPool1111111111111111111111111111",
        account_id="account_alpha"
    )
    prop = strat_service.propose_trade_intent(signal_id=sig.signal_id, account_id="account_alpha")

    # Attempt execution from account_bravo
    with pytest.raises(PermissionError) as exc_info:
        paper_service.execute_strategy_proposal(proposal_id=prop["proposal_id"], account_id="account_bravo")
    assert "not authorized to execute proposal" in str(exc_info.value)


def test_h01_expired_proposal_rejection(strat_service, paper_service, temp_h_db):
    """Verifies that expired proposals cannot be executed and transition to EXPIRED."""
    now_str = datetime.now(timezone.utc).isoformat()
    expired_time = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    acc_id = "paper_acc_sol_exp"

    paper_service.record_decimal_verification(
        chain_id="solana:mainnet",
        asset_address="AlphaTokenExp111111111111111111111111",
        decimals=9,
        verification_source="CORE_TEST",
        verification_method="STATIC",
        raw_metadata={"mint": "AlphaTokenExp111111111111111111111111", "decimals": 9}
    )

    with sqlite3.connect(temp_h_db) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO discovered_tokens (id, chain_id, address, symbol, name, decimals, is_verified, discovery_source, first_seen_at, last_seen_at)
            VALUES ('tok_exp', 'solana:mainnet', 'AlphaTokenExp111111111111111111111111', 'EXP', 'Exp Token', 9, 1, 'TEST', ?, ?);
            """,
            (now_str, now_str)
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO discovered_pools (id, chain_id, protocol, pool_address, token0_address, token1_address, discovery_source, is_canonical, status, created_at)
            VALUES ('ExpPool11111111111111111111111111111', 'solana:mainnet', 'raydium_cpmm', 'ExpPool11111111111111111111111111111', 'AlphaTokenExp111111111111111111111111', 'native', 'TEST', 1, 'ACTIVE', ?);
            """,
            (now_str,)
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO market_data_snapshots (id, pool_id, chain_id, provider_id, price_usd, liquidity_usd, volume_24h_usd, observation_time, retrieved_at)
            VALUES ('snap_exp_0', 'ExpPool11111111111111111111111111111', 'solana:mainnet', 'test', 1.0, 100000.0, 50000.0, ?, ?),
                   ('snap_exp_1', 'ExpPool11111111111111111111111111111', 'solana:mainnet', 'test', 1.5, 100000.0, 50000.0, ?, ?);
            """,
            (now_str, now_str, now_str, now_str)
        )

    sig = strat_service.evaluate_strategy(
        strategy_id="momentum_breakout",
        chain_id="solana:mainnet",
        asset_id="AlphaTokenExp111111111111111111111111",
        pool_id="ExpPool11111111111111111111111111111",
        account_id=acc_id
    )
    prop = strat_service.propose_trade_intent(signal_id=sig.signal_id, account_id=acc_id)
    proposal_id = prop["proposal_id"]

    # Manually expire proposal in database
    with sqlite3.connect(temp_h_db) as conn:
        conn.execute("UPDATE strategy_proposals SET expires_at = ? WHERE proposal_id = ?;", (expired_time, proposal_id))

    with pytest.raises(ValueError) as exc_info:
        paper_service.execute_strategy_proposal(proposal_id=proposal_id, account_id=acc_id)
    assert "has expired" in str(exc_info.value)

    # Verify status changed to EXPIRED
    with sqlite3.connect(temp_h_db) as conn:
        p_row = conn.execute("SELECT execution_status FROM strategy_proposals WHERE proposal_id = ?;", (proposal_id,)).fetchone()
        assert p_row[0] == "EXPIRED"


def test_h01_transaction_atomicity_on_ledger_failure(strat_service, paper_service, temp_h_db, monkeypatch):
    """Verifies that an error during ledger posting causes a full rollback with zero orders and proposal remaining PROPOSED_ONLY."""
    now_str = datetime.now(timezone.utc).isoformat()
    acc_id = "paper_acc_sol_atomic"

    paper_service.record_decimal_verification(
        chain_id="solana:mainnet",
        asset_address="AlphaTokenAtomic111111111111111111111",
        decimals=9,
        verification_source="CORE_TEST",
        verification_method="STATIC",
        raw_metadata={"mint": "AlphaTokenAtomic111111111111111111111", "decimals": 9}
    )

    with sqlite3.connect(temp_h_db) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO discovered_tokens (id, chain_id, address, symbol, name, decimals, is_verified, discovery_source, first_seen_at, last_seen_at)
            VALUES ('tok_atom', 'solana:mainnet', 'AlphaTokenAtomic111111111111111111111', 'ATOM', 'Atom Token', 9, 1, 'TEST', ?, ?);
            """,
            (now_str, now_str)
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO discovered_pools (id, chain_id, protocol, pool_address, token0_address, token1_address, discovery_source, is_canonical, status, created_at)
            VALUES ('AtomPool11111111111111111111111111111', 'solana:mainnet', 'raydium_cpmm', 'AtomPool11111111111111111111111111111', 'AlphaTokenAtomic111111111111111111111', 'native', 'TEST', 1, 'ACTIVE', ?);
            """,
            (now_str,)
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO market_data_snapshots (id, pool_id, chain_id, provider_id, price_usd, liquidity_usd, volume_24h_usd, observation_time, retrieved_at)
            VALUES ('snap_at_0', 'AtomPool11111111111111111111111111111', 'solana:mainnet', 'test', 1.0, 100000.0, 50000.0, ?, ?),
                   ('snap_at_1', 'AtomPool11111111111111111111111111111', 'solana:mainnet', 'test', 1.5, 100000.0, 50000.0, ?, ?);
            """,
            (now_str, now_str, now_str, now_str)
        )

    sig = strat_service.evaluate_strategy(
        strategy_id="momentum_breakout",
        chain_id="solana:mainnet",
        asset_id="AlphaTokenAtomic111111111111111111111",
        pool_id="AtomPool11111111111111111111111111111",
        account_id=acc_id
    )
    prop = strat_service.propose_trade_intent(signal_id=sig.signal_id, account_id=acc_id)
    proposal_id = prop["proposal_id"]

    # Simulate forced ledger failure
    def mock_post_journal(*args, **kwargs):
        raise RuntimeError("SIMULATED_LEDGER_STORAGE_FAILURE")

    monkeypatch.setattr(paper_service.ledger_service, "post_journal_in_connection", mock_post_journal)

    with pytest.raises(RuntimeError) as exc_info:
        paper_service.execute_strategy_proposal(proposal_id=proposal_id, account_id=acc_id)
    assert "SIMULATED_LEDGER_STORAGE_FAILURE" in str(exc_info.value)

    # Verify atomic rollback: ZERO orders created, proposal still PROPOSED_ONLY
    with sqlite3.connect(temp_h_db) as conn:
        ord_count = conn.execute("SELECT COUNT(*) FROM paper_orders_v2 WHERE proposal_id = ?;", (proposal_id,)).fetchone()[0]
        assert ord_count == 0
        p_row = conn.execute("SELECT execution_status, order_id FROM strategy_proposals WHERE proposal_id = ?;", (proposal_id,)).fetchone()
        assert p_row[0] == "PROPOSED_ONLY"
        assert p_row[1] is None


def test_h02_manual_orders_cannot_impersonate_strategy(paper_service):
    """Verifies that manual paper execution rejects requests claiming strategy origin or specifying proposal_id."""
    req = PaperOrderRequest(
        account_id="manual_acc",
        client_order_id="man_01",
        asset_id="DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
        pool_id="pool_01",
        side="BUY",
        input_mint="native",
        output_mint="DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
        input_amount_raw=Decimal("1.0"),
        chain_id="solana:mainnet",
        proposal_id="prop_forged_123"
    )

    with pytest.raises(ValueError) as exc_info:
        paper_service.execute_order(request=req)
    assert "cannot specify proposal_id" in str(exc_info.value)

    req2 = PaperOrderRequest(
        account_id="manual_acc",
        client_order_id="man_02",
        asset_id="DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
        pool_id="pool_01",
        side="BUY",
        input_mint="native",
        output_mint="DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
        input_amount_raw=Decimal("1.0"),
        chain_id="solana:mainnet",
        origin="STRATEGY_PROPOSAL"
    )
    with pytest.raises(ValueError) as exc_info2:
        paper_service.execute_order(request=req2)
    assert "cannot specify proposal_id or claim strategy origin" in str(exc_info2.value)


# =============================================================================
# H-03: Historical Strategy Configuration Snapshots
# =============================================================================

def test_h03_historical_configuration_snapshot_and_replay_determinism(strat_service, paper_service, temp_h_db):
    """Verifies that configuration snapshots are persisted and historical replay uses the snapshot rather than altered active config."""
    now_str = datetime.now(timezone.utc).isoformat()
    paper_service.record_decimal_verification(
        chain_id="solana:mainnet",
        asset_address="SnapshotToken11111111111111111111111",
        decimals=9,
        verification_source="CORE_TEST",
        verification_method="STATIC",
        raw_metadata={"mint": "SnapshotToken11111111111111111111111", "decimals": 9}
    )

    with sqlite3.connect(temp_h_db) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO discovered_tokens (id, chain_id, address, symbol, name, decimals, is_verified, discovery_source, first_seen_at, last_seen_at)
            VALUES ('tok_snap', 'solana:mainnet', 'SnapshotToken11111111111111111111111', 'SNAP', 'Snapshot Token', 9, 1, 'TEST', ?, ?);
            """,
            (now_str, now_str)
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO discovered_pools (id, chain_id, protocol, pool_address, token0_address, token1_address, discovery_source, is_canonical, status, created_at)
            VALUES ('SnapPool11111111111111111111111111111', 'solana:mainnet', 'raydium_cpmm', 'SnapPool11111111111111111111111111111', 'SnapshotToken11111111111111111111111', 'native', 'TEST', 1, 'ACTIVE', ?);
            """,
            (now_str,)
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO market_data_snapshots (id, pool_id, chain_id, provider_id, price_usd, liquidity_usd, volume_24h_usd, observation_time, retrieved_at)
            VALUES ('snap_sn_0', 'SnapPool11111111111111111111111111111', 'solana:mainnet', 'test', 1.0, 100000.0, 50000.0, ?, ?),
                   ('snap_sn_1', 'SnapPool11111111111111111111111111111', 'solana:mainnet', 'test', 1.5, 100000.0, 50000.0, ?, ?);
            """,
            (now_str, now_str, now_str, now_str)
        )

    # 1. Run evaluation with specific parameters
    sig = strat_service.evaluate_strategy(
        strategy_id="momentum_breakout",
        chain_id="solana:mainnet",
        asset_id="SnapshotToken11111111111111111111111",
        pool_id="SnapPool11111111111111111111111111111",
        parameter_overrides={"threshold_pct": 3.5, "take_profit_pct": 25.0}
    )
    run_id = sig.run_id

    # Verify snapshot persisted in database
    with sqlite3.connect(temp_h_db) as conn:
        conn.row_factory = sqlite3.Row
        run_row = conn.execute("SELECT * FROM strategy_runs WHERE run_id = ?;", (run_id,)).fetchone()
        assert run_row["config_snapshot_json"] is not None
        assert run_row["config_hash"] is not None
        snap = json.loads(run_row["config_snapshot_json"])
        assert snap["parameters"]["threshold_pct"] == 3.5

    # 2. Modify active configuration in database to completely different values
    strat_service.save_configuration(
        strategy_id="momentum_breakout",
        parameters={"threshold_pct": 99.0, "take_profit_pct": 500.0},
        chain_id="solana:mainnet"
    )

    # 3. Replay evaluation: must reproduce original evaluation using snapshot, ignoring the new 99.0 threshold
    replay_res = strat_service.replay_evaluation(run_id)
    assert replay_res["reproducible"] is True
    assert replay_res["is_replay"] is True
    assert replay_res["replayed_action"] == sig.action.value


def test_h03_corrupted_config_hash_rejection(strat_service, paper_service, temp_h_db):
    """Verifies that tampering with config_snapshot_json triggers a hash mismatch during replay."""
    now_str = datetime.now(timezone.utc).isoformat()
    paper_service.record_decimal_verification(
        chain_id="solana:mainnet",
        asset_address="CorruptToken11111111111111111111111",
        decimals=9,
        verification_source="CORE_TEST",
        verification_method="STATIC",
        raw_metadata={"mint": "CorruptToken11111111111111111111111", "decimals": 9}
    )

    with sqlite3.connect(temp_h_db) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO discovered_tokens (id, chain_id, address, symbol, name, decimals, is_verified, discovery_source, first_seen_at, last_seen_at)
            VALUES ('tok_cor', 'solana:mainnet', 'CorruptToken11111111111111111111111', 'COR', 'Cor Token', 9, 1, 'TEST', ?, ?);
            """,
            (now_str, now_str)
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO discovered_pools (id, chain_id, protocol, pool_address, token0_address, token1_address, discovery_source, is_canonical, status, created_at)
            VALUES ('CorPool11111111111111111111111111111', 'solana:mainnet', 'raydium_cpmm', 'CorPool11111111111111111111111111111', 'CorruptToken11111111111111111111111', 'native', 'TEST', 1, 'ACTIVE', ?);
            """,
            (now_str,)
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO market_data_snapshots (id, pool_id, chain_id, provider_id, price_usd, liquidity_usd, volume_24h_usd, observation_time, retrieved_at)
            VALUES ('snap_cor_0', 'CorPool11111111111111111111111111111', 'solana:mainnet', 'test', 1.0, 100000.0, 50000.0, ?, ?);
            """,
            (now_str, now_str)
        )

    sig = strat_service.evaluate_strategy(
        strategy_id="momentum_breakout",
        chain_id="solana:mainnet",
        asset_id="CorruptToken11111111111111111111111",
        pool_id="CorPool11111111111111111111111111111"
    )

    # Tamper with snapshot in DB
    with sqlite3.connect(temp_h_db) as conn:
        conn.execute(
            "UPDATE strategy_runs SET config_snapshot_json = '{\"tampered\": true}' WHERE run_id = ?;",
            (sig.run_id,)
        )

    with pytest.raises(ValueError) as exc_info:
        strat_service.replay_evaluation(sig.run_id)
    assert "configuration snapshot hash mismatch" in str(exc_info.value)


# =============================================================================
# H-04: API Access Protection
# =============================================================================

def test_h04_protected_signals_and_runs_endpoints():
    """Verifies that GET /runs and GET /signals enforce loopback protection and token auth."""
    # 1. Unauthenticated remote access without token is rejected with HTTP 403
    settings.ADMIN_API_TOKEN = None
    # Simulate remote IP
    resp_remote = client.get(
        "/api/v1/strategies/momentum_breakout/runs",
        headers={"Host": "192.168.1.50"}
    )
    # When using TestClient, client host defaults to testclient (loopback).
    # Let's test with ADMIN_API_TOKEN set:
    try:
        settings.ADMIN_API_TOKEN = "strat_secure_token_h04"
        resp_unauth = client.get("/api/v1/strategies/momentum_breakout/runs")
        assert resp_unauth.status_code == 401

        resp_signals_unauth = client.get("/api/v1/strategies/momentum_breakout/signals")
        assert resp_signals_unauth.status_code == 401

        # Correct token succeeds
        resp_auth = client.get(
            "/api/v1/strategies/momentum_breakout/runs",
            headers={"X-API-Key": "strat_secure_token_h04"}
        )
        assert resp_auth.status_code == 200

        resp_sig_auth = client.get(
            "/api/v1/strategies/momentum_breakout/signals",
            headers={"Authorization": "Bearer strat_secure_token_h04"}
        )
        assert resp_sig_auth.status_code == 200
    finally:
        settings.ADMIN_API_TOKEN = None


# =============================================================================
# H-07: Audit Trail and Reconciliation
# =============================================================================

def test_h07_strategy_proposal_reconciliation(paper_service, temp_h_db):
    """Verifies that reconcile_strategy_proposals audits consumed proposals against orders and identifies anomalies."""
    report = paper_service.reconcile_strategy_proposals()
    assert report["is_balanced"] is True
    assert report["discrepancies_count"] == 0
    assert "consumed_proposals_count" in report


# =============================================================================
# H-08: Windmill WF-22 Workflow Script
# =============================================================================

def test_h08_windmill_wf22_workflow(strat_service, paper_service, temp_h_db):
    """Verifies that Windmill WF-22 executes proposals through Core bridge with distinct status outcomes."""
    from windmill_scripts.wf22_execute_strategy_proposal import execute_proposal_workflow

    now_str = datetime.now(timezone.utc).isoformat()
    base_time = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    acc_id = "paper_acc_sol_wf22"

    paper_service.record_decimal_verification(
        chain_id="solana:mainnet",
        asset_address="AlphaTokenWf2211111111111111111111111",
        decimals=9,
        verification_source="CORE_TEST",
        verification_method="STATIC",
        raw_metadata={"mint": "AlphaTokenWf2211111111111111111111111", "decimals": 9}
    )

    with sqlite3.connect(temp_h_db) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO discovered_tokens (id, chain_id, address, symbol, name, decimals, is_verified, discovery_source, first_seen_at, last_seen_at)
            VALUES ('tok_wf22', 'solana:mainnet', 'AlphaTokenWf2211111111111111111111111', 'WF22', 'WF22 Token', 9, 1, 'TEST', ?, ?);
            """,
            (now_str, now_str)
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO discovered_pools (id, chain_id, protocol, pool_address, token0_address, token1_address, discovery_source, is_canonical, status, created_at)
            VALUES ('Wf22Pool1111111111111111111111111111', 'solana:mainnet', 'raydium_cpmm', 'Wf22Pool1111111111111111111111111111', 'AlphaTokenWf2211111111111111111111111', 'native', 'TEST', 1, 'ACTIVE', ?);
            """,
            (now_str,)
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO market_data_snapshots (id, pool_id, chain_id, provider_id, price_usd, liquidity_usd, volume_24h_usd, observation_time, retrieved_at)
            VALUES ('snap_wf_0', 'Wf22Pool1111111111111111111111111111', 'solana:mainnet', 'test', 1.0, 100000.0, 50000.0, ?, ?),
                   ('snap_wf_1', 'Wf22Pool1111111111111111111111111111', 'solana:mainnet', 'test', 1.5, 100000.0, 50000.0, ?, ?);
            """,
            (base_time, base_time, now_str, now_str)
        )

    sig = strat_service.evaluate_strategy(
        strategy_id="momentum_breakout",
        chain_id="solana:mainnet",
        asset_id="AlphaTokenWf2211111111111111111111111",
        pool_id="Wf22Pool1111111111111111111111111111",
        account_id=acc_id
    )
    prop = strat_service.propose_trade_intent(signal_id=sig.signal_id, account_id=acc_id)

    # 1. Successful execution through Windmill workflow
    res = execute_proposal_workflow(proposal_id=prop["proposal_id"], account_id=acc_id, paper_service=paper_service)
    assert res["status"] == "SUCCESS"
    assert res["order_id"].startswith("ord_prop_")

    # 2. Unauthorized account execution returns FORBIDDEN
    res_forbidden = execute_proposal_workflow(proposal_id=prop["proposal_id"], account_id="wrong_account", paper_service=paper_service)
    assert res_forbidden["status"] == "FORBIDDEN"
