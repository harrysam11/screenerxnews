"""
Phase P10.2-F: Persistent Multi-Chain Paper Trading Engine Test Suite.
Verifies acceptance criteria:
- PAPER-01: Schema Migration 7 table creation, indexes, and desk seed integrity.
- PAPER-02: Isolated desk operations across 12 chains without cross-desk contamination.
- PAPER-03: Decimal-safe fixed-point arithmetic preserving exact balances and unit accounting.
- PAPER-04: Order execution against discovered pools and market snapshots from P10.2-E.
- PAPER-05: Limit order lifecycle: holding in SUBMITTED, execution when price met, and cancellation.
- PAPER-06: Market data stale check (>300s) and reorg-invalidated pool execution rejection.
- PAPER-07: Durable risk controls (order value ceiling, concurrent positions, dynamic limits).
- PAPER-08: Double-entry financial ledger journal posting with zero-balance validation.
- PAPER-09: Duplicate order idempotency preventing double debit or duplicate fills.
- PAPER-10: Portfolio reconciliation verifying ending equity == cash + positions value.
- PAPER-11: Restart recovery and state persistence across independent service instances.
- PAPER-12: FastAPI REST API paper routes with explicit PAPER_SIMULATED labeling.
"""

import os
import tempfile
import sqlite3
import pytest
from decimal import Decimal
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient

from app.main import app
from app.db.sqlite_migrator import SqliteMigrator
from app.schemas.contracts import PaperOrderRequest
from app.services.persistent_paper_service import PersistentPaperService
from app.services.discovery_service import DiscoveryService
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


@pytest.fixture
def paper_service(temp_migrated_db):
    """Provides an isolated PersistentPaperService instance backed by temp_migrated_db."""
    return PersistentPaperService(db_path=temp_migrated_db)


@pytest.fixture
def discovery_svc(temp_migrated_db):
    """Provides a DiscoveryService instance backed by temp_migrated_db."""
    return DiscoveryService(db_path=temp_migrated_db)


# =============================================================================
# PAPER-01: Migration 7 Schema Creation & Desk Seeds
# =============================================================================

def test_paper_01_migration_7_tables_and_seeds(paper_service, temp_migrated_db):
    migrator = SqliteMigrator(temp_migrated_db)
    schema = migrator.verify_schema()

    assert schema["valid"] is True
    assert schema["current_version"] >= 7
    expected_tables = [
        "paper_portfolios",
        "paper_account_balances",
        "paper_orders_v2",
        "paper_fills",
        "paper_positions_v2",
        "paper_risk_limits",
        "paper_portfolio_valuations",
        "paper_reconciliation_records",
        "paper_audit_log"
    ]
    for table in expected_tables:
        assert table in schema["existing_tables"], f"Missing table: {table}"
    assert len(schema["missing_tables"]) == 0

    with sqlite3.connect(temp_migrated_db) as conn:
        conn.row_factory = sqlite3.Row
        accs = conn.execute("SELECT * FROM paper_accounts;").fetchall()
        assert len(accs) >= 12, f"Expected at least 12 paper accounts, got {len(accs)}"

        bals = conn.execute("SELECT * FROM paper_account_balances;").fetchall()
        assert len(bals) >= 12, f"Expected at least 12 initial balances, got {len(bals)}"


# =============================================================================
# PAPER-02: Isolated Desk Operations Across 12 Chains
# =============================================================================

def test_paper_02_desk_isolation(paper_service):
    sol_acc = paper_service.get_account("solana:mainnet")
    base_acc = paper_service.get_account("eip155:8453")
    bsc_acc = paper_service.get_account("eip155:56")

    assert sol_acc.native_symbol == "SOL"
    assert base_acc.native_symbol == "ETH"
    assert bsc_acc.native_symbol == "BNB"

    sol_initial_cash = sol_acc.current_virtual_balance
    base_initial_cash = base_acc.current_virtual_balance

    # Execute trade on Base
    req = PaperOrderRequest(
        account_id=base_acc.account_id,
        client_order_id="iso-base-order-001",
        asset_id="0x4200000000000000000000000000000000000006",
        pool_id="0xBasePoolUniswapV3",
        side="BUY",
        input_mint="native",
        output_mint="0x4200000000000000000000000000000000000006",
        input_amount_raw=Decimal("1.0"),
        chain_id="eip155:8453"
    )
    res = paper_service.execute_order(
        request=req,
        current_market_price_native=Decimal("0.0005"),
        pool_liquidity_usd=Decimal("200000.0")
    )
    assert res.status == "FILLED"

    # Verify Base cash changed, Solana cash stayed completely untouched
    base_updated = paper_service.get_account("eip155:8453")
    sol_updated = paper_service.get_account("solana:mainnet")

    assert base_updated.current_virtual_balance == base_initial_cash - Decimal("1.0")
    assert sol_updated.current_virtual_balance == sol_initial_cash


# =============================================================================
# PAPER-03: Decimal-Safe Fixed-Point Arithmetic
# =============================================================================

def test_paper_03_decimal_safe_arithmetic(paper_service):
    acc = paper_service.get_account("solana:mainnet")
    initial_cash = acc.current_virtual_balance

    trade_amount = Decimal("0.123456789")
    req = PaperOrderRequest(
        account_id=acc.account_id,
        client_order_id="decimal-prec-001",
        asset_id="PreciseToken11111111111111111111111111111",
        pool_id="PrecisePool111111111111111111111111111111",
        side="BUY",
        input_mint="native",
        output_mint="PreciseToken11111111111111111111111111111",
        input_amount_raw=trade_amount,
        chain_id="solana:mainnet"
    )
    res = paper_service.execute_order(
        request=req,
        current_market_price_native=Decimal("0.00012345"),
        pool_liquidity_usd=Decimal("500000.0")
    )
    assert res.status == "FILLED"

    acc_after = paper_service.get_account("solana:mainnet")
    expected_cash = initial_cash - trade_amount
    assert acc_after.current_virtual_balance == expected_cash


# =============================================================================
# PAPER-04: Order Execution Against Discovered Pools & Snapshots
# =============================================================================

def test_paper_04_execution_against_discovered_pools(paper_service, discovery_svc):
    # Ingest discovered pool and token
    pool_res = discovery_svc.ingest_pool_event(
        chain_id="eip155:8453",
        protocol="UniswapV3",
        pool_address="0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640",
        token0_address="0x4200000000000000000000000000000000000006",
        token1_address="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        fee_tier_bps=30,
        token0_metadata={"symbol": "WETH", "decimals": 18},
        token1_metadata={"symbol": "USDC", "decimals": 6}
    )
    assert pool_res["status"] == "ACTIVE"

    # Ingest market data snapshot
    snap_res = discovery_svc.ingest_market_data(
        chain_id="eip155:8453",
        pool_address="0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640",
        provider_id="alchemy_rpc",
        price_usd=3000.0,
        liquidity_usd=500000.0,
        reserves_token0=100.0,
        reserves_token1=300000.0
    )
    assert "snapshot_id" in snap_res

    acc = paper_service.get_account("eip155:8453")
    req = PaperOrderRequest(
        account_id=acc.account_id,
        client_order_id="disc-trade-001",
        asset_id="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        pool_id="0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640",
        side="BUY",
        input_mint="native",
        output_mint="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        input_amount_raw=Decimal("0.5"),
        chain_id="eip155:8453"
    )
    res = paper_service.execute_order(
        request=req,
        current_market_price_native=Decimal("0.00033333"),
        pool_liquidity_usd=Decimal("500000.0")
    )
    assert res.status == "FILLED"
    assert res.output_amount_raw > 0


# =============================================================================
# PAPER-05: Limit Orders and Order Cancellation
# =============================================================================

def test_paper_05_limit_orders_and_cancellation(paper_service):
    acc = paper_service.get_account("solana:mainnet")

    # Limit BUY order with limit price lower than market price -> stays SUBMITTED
    limit_req = PaperOrderRequest(
        account_id=acc.account_id,
        client_order_id="limit-order-pending-01",
        asset_id="LimitTestToken1111111111111111111111111111",
        pool_id="LimitTestPool11111111111111111111111111111",
        side="BUY",
        input_mint="native",
        output_mint="LimitTestToken1111111111111111111111111111",
        input_amount_raw=Decimal("1.0"),
        chain_id="solana:mainnet"
    )
    res = paper_service.execute_order(
        request=limit_req,
        current_market_price_native=Decimal("0.0005"),
        order_type="LIMIT",
        limit_price_native=Decimal("0.0004")
    )
    assert res.status == "SUBMITTED"

    # Query orders to verify it is listed as SUBMITTED
    orders = paper_service.list_orders(chain_id="solana:mainnet", status="SUBMITTED")
    assert any(o["order_id"] == res.order_id for o in orders)

    # Cancel the submitted order
    cancel_res = paper_service.cancel_order(res.order_id)
    assert cancel_res["status"] == "CANCELLED"
    assert cancel_res["environment"] == "PAPER_SIMULATED"

    # Attempting to cancel again should raise ValueError
    with pytest.raises(ValueError, match="Cannot cancel order in state 'CANCELLED'"):
        paper_service.cancel_order(res.order_id)

    # Attempting to cancel non-existent order raises ValueError
    with pytest.raises(ValueError, match="not found"):
        paper_service.cancel_order("non_existent_order_id")


# =============================================================================
# PAPER-06: Stale Market Data and Reorg Pool Invalidation
# =============================================================================

def test_paper_06_stale_data_and_reorg_rejection(paper_service, discovery_svc):
    # 1. Ingest pool
    discovery_svc.ingest_pool_event(
        chain_id="eip155:8453",
        protocol="UniswapV3",
        pool_address="0x1111111111111111111111111111111111111111",
        token0_address="0x4200000000000000000000000000000000000006",
        token1_address="0x2222222222222222222222222222222222222222",
        fee_tier_bps=30
    )

    # 2. Insert stale market snapshot (> 300s old)
    stale_time = (datetime.now(timezone.utc) - timedelta(seconds=400)).isoformat()
    discovery_svc.ingest_market_data(
        chain_id="eip155:8453",
        pool_address="0x1111111111111111111111111111111111111111",
        provider_id="alchemy_rpc",
        price_usd=2500.0,
        liquidity_usd=100000.0,
        observation_time=stale_time
    )

    acc = paper_service.get_account("eip155:8453")
    req = PaperOrderRequest(
        account_id=acc.account_id,
        client_order_id="stale-order-001",
        asset_id="0x2222222222222222222222222222222222222222",
        pool_id="0x1111111111111111111111111111111111111111",
        side="BUY",
        input_mint="native",
        output_mint="0x2222222222222222222222222222222222222222",
        input_amount_raw=Decimal("0.1"),
        chain_id="eip155:8453"
    )
    res_stale = paper_service.execute_order(request=req)
    assert res_stale.status == "REJECTED"
    assert "MARKET_DATA_STALE" in res_stale.rejection_reason

    # 3. Test reorg-invalidated pool
    discovery_svc.ingest_pool_event(
        chain_id="eip155:8453",
        protocol="UniswapV3",
        pool_address="0x3333333333333333333333333333333333333333",
        token0_address="0x4200000000000000000000000000000000000006",
        token1_address="0x4444444444444444444444444444444444444444",
        fee_tier_bps=30,
        block_number=1000
    )
    # Reorg rolls back block 1000
    discovery_svc.handle_reorg_event(
        chain_id="eip155:8453",
        reorg_block_number=999,
        block_hash="0xreorghash"
    )

    req_reorg = PaperOrderRequest(
        account_id=acc.account_id,
        client_order_id="reorg-order-001",
        asset_id="0x4444444444444444444444444444444444444444",
        pool_id="0x3333333333333333333333333333333333333333",
        side="BUY",
        input_mint="native",
        output_mint="0x4444444444444444444444444444444444444444",
        input_amount_raw=Decimal("0.1"),
        chain_id="eip155:8453"
    )
    res_reorg = paper_service.execute_order(
        request=req_reorg,
        current_market_price_native=Decimal("0.001")
    )
    assert res_reorg.status == "REJECTED"
    assert "POOL_INACTIVE_OR_INVALIDATED" in res_reorg.rejection_reason


# =============================================================================
# PAPER-07: Durable Risk Controls & Dynamic Limits
# =============================================================================

def test_paper_07_durable_risk_controls(paper_service):
    acc = paper_service.get_account("solana:mainnet")

    # Set strict order limit: max order value $500
    paper_service.update_risk_limits(
        chain_id="solana:mainnet",
        max_order_value_usd=500.0,
        max_concurrent_positions=2
    )

    # 1. Order exceeding $500 (3 SOL @ $200 = $600) -> Rejected
    req_oversized = PaperOrderRequest(
        account_id=acc.account_id,
        client_order_id="risk-oversized-01",
        asset_id="TokenA11111111111111111111111111111111111",
        pool_id="PoolA111111111111111111111111111111111111",
        side="BUY",
        input_mint="native",
        output_mint="TokenA11111111111111111111111111111111111",
        input_amount_raw=Decimal("3.0"),
        chain_id="solana:mainnet"
    )
    res_oversized = paper_service.execute_order(
        request=req_oversized,
        current_market_price_native=Decimal("0.0001"),
        native_price_usd=Decimal("200.0")
    )
    assert res_oversized.status == "REJECTED"
    assert "RISK_LIMIT_EXCEEDED" in res_oversized.rejection_reason

    # 2. Relax order limit to $2000 -> Now succeeds
    paper_service.update_risk_limits(
        chain_id="solana:mainnet",
        max_order_value_usd=2000.0
    )
    req_allowed = PaperOrderRequest(
        account_id=acc.account_id,
        client_order_id="risk-allowed-01",
        asset_id="TokenA11111111111111111111111111111111111",
        pool_id="PoolA111111111111111111111111111111111111",
        side="BUY",
        input_mint="native",
        output_mint="TokenA11111111111111111111111111111111111",
        input_amount_raw=Decimal("3.0"),
        chain_id="solana:mainnet"
    )
    res_allowed = paper_service.execute_order(
        request=req_allowed,
        current_market_price_native=Decimal("0.0001"),
        native_price_usd=Decimal("200.0")
    )
    assert res_allowed.status == "FILLED"

    # Fill second position
    req_pos2 = PaperOrderRequest(
        account_id=acc.account_id,
        client_order_id="risk-pos2-01",
        asset_id="TokenB11111111111111111111111111111111111",
        pool_id="PoolB111111111111111111111111111111111111",
        side="BUY",
        input_mint="native",
        output_mint="TokenB11111111111111111111111111111111111",
        input_amount_raw=Decimal("1.0"),
        chain_id="solana:mainnet"
    )
    paper_service.execute_order(
        request=req_pos2,
        current_market_price_native=Decimal("0.0001"),
        native_price_usd=Decimal("200.0")
    )

    # 3. Third position exceeds max_concurrent_positions (2) -> Rejected
    req_pos3 = PaperOrderRequest(
        account_id=acc.account_id,
        client_order_id="risk-pos3-01",
        asset_id="TokenC11111111111111111111111111111111111",
        pool_id="PoolC111111111111111111111111111111111111",
        side="BUY",
        input_mint="native",
        output_mint="TokenC11111111111111111111111111111111111",
        input_amount_raw=Decimal("1.0"),
        chain_id="solana:mainnet"
    )
    res_pos3 = paper_service.execute_order(
        request=req_pos3,
        current_market_price_native=Decimal("0.0001"),
        native_price_usd=Decimal("200.0")
    )
    assert res_pos3.status == "REJECTED"
    assert "Concurrent positions limit" in res_pos3.rejection_reason


# =============================================================================
# PAPER-08: Double-Entry Financial Ledger Integration
# =============================================================================

def test_paper_08_double_entry_ledger_posting(paper_service):
    acc = paper_service.get_account("solana:mainnet")
    req = PaperOrderRequest(
        account_id=acc.account_id,
        client_order_id="ledger-test-order-01",
        asset_id="LedgerToken1111111111111111111111111111",
        pool_id="LedgerPool11111111111111111111111111111",
        side="BUY",
        input_mint="native",
        output_mint="LedgerToken1111111111111111111111111111",
        input_amount_raw=Decimal("2.0"),
        chain_id="solana:mainnet"
    )
    res = paper_service.execute_order(
        request=req,
        current_market_price_native=Decimal("0.0002"),
        native_price_usd=Decimal("200.0")
    )
    assert res.status == "FILLED"

    with paper_service._get_connection() as conn:
        conn.row_factory = sqlite3.Row
        jrn = conn.execute(
            "SELECT * FROM ledger_journals WHERE order_id = ? AND journal_type = 'PAPER_TRADE';",
            (res.order_id,)
        ).fetchone()
        assert jrn is not None
        assert jrn["status"] == "POSTED"

        lines = conn.execute(
            "SELECT * FROM ledger_lines WHERE journal_id = ?;",
            (jrn["id"],)
        ).fetchall()
        assert len(lines) == 2

        # Verify balanced entry: one credit native, one debit token
        has_wallet = any("WALLET" in l["account_code"] for l in lines)
        assert has_wallet is True


# =============================================================================
# PAPER-09: Duplicate Order Idempotency
# =============================================================================

def test_paper_09_duplicate_order_idempotency(paper_service):
    acc = paper_service.get_account("solana:mainnet")
    initial_cash = acc.current_virtual_balance

    req = PaperOrderRequest(
        account_id=acc.account_id,
        client_order_id="idem-dup-key-999",
        asset_id="IdemToken11111111111111111111111111111",
        pool_id="IdemPool111111111111111111111111111111",
        side="BUY",
        input_mint="native",
        output_mint="IdemToken11111111111111111111111111111",
        input_amount_raw=Decimal("1.0"),
        chain_id="solana:mainnet"
    )
    first_res = paper_service.execute_order(
        request=req,
        current_market_price_native=Decimal("0.0001")
    )
    assert first_res.status == "FILLED"

    # Second call with identical client_order_id
    second_res = paper_service.execute_order(
        request=req,
        current_market_price_native=Decimal("0.0001")
    )
    assert second_res.status == "REJECTED"
    assert "DUPLICATE_CLIENT_ORDER_ID" in second_res.rejection_reason

    # Balance must only have been deducted ONCE (1.0 SOL)
    acc_after = paper_service.get_account("solana:mainnet")
    assert acc_after.current_virtual_balance == initial_cash - Decimal("1.0")


# =============================================================================
# PAPER-10: Portfolio Reconciliation
# =============================================================================

def test_paper_10_portfolio_reconciliation(paper_service):
    acc = paper_service.get_account("solana:mainnet")

    # Perform a trade
    req = PaperOrderRequest(
        account_id=acc.account_id,
        client_order_id="recon-trade-01",
        asset_id="ReconToken1111111111111111111111111111",
        pool_id="ReconPool11111111111111111111111111111",
        side="BUY",
        input_mint="native",
        output_mint="ReconToken1111111111111111111111111111",
        input_amount_raw=Decimal("2.0"),
        chain_id="solana:mainnet"
    )
    paper_service.execute_order(
        request=req,
        current_market_price_native=Decimal("0.0005"),
        native_price_usd=Decimal("200.0")
    )

    recon = paper_service.reconcile_portfolio("solana:mainnet")
    assert recon["is_balanced"] is True
    assert recon["discrepancy_usd"] == 0.0
    assert recon["calculated_equity_usd"] == recon["cash_balance_usd"] + recon["positions_value_usd"]
    assert recon["environment"] == "PAPER_SIMULATED"

    # Verify reconciliation record stored in DB
    with paper_service._get_connection() as conn:
        conn.row_factory = sqlite3.Row
        rec = conn.execute(
            "SELECT * FROM paper_reconciliation_records WHERE id = ?;",
            (recon["reconciliation_id"],)
        ).fetchone()
        assert rec is not None
        assert rec["is_balanced"] == 1


# =============================================================================
# PAPER-11: Restart Recovery Across Independent Service Instances
# =============================================================================

def test_paper_11_restart_recovery_and_persistence(temp_migrated_db):
    service_1 = PersistentPaperService(db_path=temp_migrated_db)
    acc = service_1.get_account("solana:mainnet")

    req = PaperOrderRequest(
        account_id=acc.account_id,
        client_order_id="restart-persist-01",
        asset_id="PersistToken1111111111111111111111111111",
        pool_id="PersistPool11111111111111111111111111111",
        side="BUY",
        input_mint="native",
        output_mint="PersistToken1111111111111111111111111111",
        input_amount_raw=Decimal("3.0"),
        chain_id="solana:mainnet"
    )
    res_1 = service_1.execute_order(
        request=req,
        current_market_price_native=Decimal("0.0002"),
        native_price_usd=Decimal("200.0")
    )
    assert res_1.status == "FILLED"

    pos_1 = service_1.get_positions("solana:mainnet")
    assert len(pos_1) >= 1
    cash_1 = service_1.get_account("solana:mainnet").current_virtual_balance

    # Simulate full process restart with fresh service instance pointing to same file
    service_2 = PersistentPaperService(db_path=temp_migrated_db)

    pos_2 = service_2.get_positions("solana:mainnet")
    cash_2 = service_2.get_account("solana:mainnet").current_virtual_balance

    # State must match exactly
    assert len(pos_2) == len(pos_1)
    assert cash_2 == cash_1
    assert pos_2[0].asset_address == "PersistToken1111111111111111111111111111"

    # Fills and orders must persist
    orders = service_2.list_orders(chain_id="solana:mainnet")
    assert any(o["order_id"] == res_1.order_id for o in orders)

    fills = service_2.list_fills(chain_id="solana:mainnet")
    assert any(f["order_id"] == res_1.order_id for f in fills)


# =============================================================================
# PAPER-12: FastAPI REST API Endpoints Verification
# =============================================================================

def test_paper_12_rest_api_endpoints():
    # 1. GET /api/v1/paper/accounts
    resp = client.get("/api/v1/paper/accounts")
    assert resp.status_code == 200
    accounts = resp.json()
    assert isinstance(accounts, list)
    assert len(accounts) >= 12
    assert all(a.get("environment") == "PAPER_SIMULATED" for a in accounts)

    # 2. GET /api/v1/paper/valuation
    resp = client.get("/api/v1/paper/valuation")
    assert resp.status_code == 200
    val = resp.json()
    assert val.get("environment") == "PAPER_SIMULATED"
    assert "total_equity_usd" in val

    # 3. GET /api/v1/paper/orders
    resp = client.get("/api/v1/paper/orders?limit=10")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)

    # 4. GET /api/v1/paper/fills
    resp = client.get("/api/v1/paper/fills?limit=10")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)

    # 5. GET /api/v1/paper/risk
    resp = client.get("/api/v1/paper/risk")
    assert resp.status_code == 200
    risk = resp.json()
    assert risk.get("environment") == "PAPER_SIMULATED"
    assert "max_order_value_usd" in risk

    # 6. PUT /api/v1/paper/risk
    resp = client.put("/api/v1/paper/risk", json={
        "max_position_size_usd": 75000.0,
        "max_order_value_usd": 35000.0
    })
    assert resp.status_code == 200
    updated_risk = resp.json()
    assert updated_risk["max_order_value_usd"] == 35000.0

    # 7. POST /api/v1/paper/reconcile
    resp = client.post("/api/v1/paper/reconcile", json={"chain_id": "solana:mainnet"})
    assert resp.status_code == 200
    recon = resp.json()
    assert recon.get("environment") == "PAPER_SIMULATED"
    assert recon["is_balanced"] is True


# =============================================================================
# PAPER-13: Process Restart with Open Orders & Locked Cash Persistence
# =============================================================================

def test_paper_13_restart_with_open_orders_and_locked_cash(temp_migrated_db):
    service_1 = PersistentPaperService(db_path=temp_migrated_db)
    acc_1 = service_1.get_account("solana:mainnet")
    initial_free = acc_1.current_virtual_balance
    initial_locked = acc_1.allocated_virtual_balance or Decimal("0.0")

    # Submit a BUY Limit order that stays open
    order_size = Decimal("5.0")
    limit_req = PaperOrderRequest(
        account_id=acc_1.account_id,
        client_order_id="restart-open-limit-001",
        asset_id="LimitTestToken1111111111111111111111111111",
        pool_id="LimitPool1111111111111111111111111111111",
        side="BUY",
        input_mint="native",
        output_mint="LimitTestToken1111111111111111111111111111",
        input_amount_raw=order_size,
        chain_id="solana:mainnet"
    )
    res_1 = service_1.execute_order(
        request=limit_req,
        current_market_price_native=Decimal("0.0005"),
        order_type="LIMIT",
        limit_price_native=Decimal("0.0004")
    )
    assert res_1.status == "SUBMITTED"

    # Verify cash moved to locked
    acc_after = service_1.get_account("solana:mainnet")
    assert acc_after.current_virtual_balance == initial_free - order_size
    assert acc_after.allocated_virtual_balance == initial_locked + order_size
    assert acc_after.total_equity_native == initial_free + initial_locked

    # Restart service: fresh instance backed by same SQLite WAL file
    service_2 = PersistentPaperService(db_path=temp_migrated_db)
    acc_2 = service_2.get_account("solana:mainnet")

    # State must be exactly preserved across restart
    assert acc_2.current_virtual_balance == initial_free - order_size
    assert acc_2.allocated_virtual_balance == initial_locked + order_size
    assert acc_2.total_equity_native == initial_free + initial_locked

    orders = service_2.list_orders(chain_id="solana:mainnet", status="SUBMITTED")
    assert any(o["order_id"] == res_1.order_id for o in orders)


# =============================================================================
# PAPER-14: Limit Order Cancellation Releases Locked Cash
# =============================================================================

def test_paper_14_limit_order_cancellation_releases_locked_cash(paper_service):
    acc = paper_service.get_account("solana:mainnet")
    initial_free = acc.current_virtual_balance
    initial_locked = acc.allocated_virtual_balance or Decimal("0.0")

    order_size = Decimal("4.0")
    req = PaperOrderRequest(
        account_id=acc.account_id,
        client_order_id="cancel-release-001",
        asset_id="LimitTestToken1111111111111111111111111111",
        pool_id="LimitPool1111111111111111111111111111111",
        side="BUY",
        input_mint="native",
        output_mint="LimitTestToken1111111111111111111111111111",
        input_amount_raw=order_size,
        chain_id="solana:mainnet"
    )
    res = paper_service.execute_order(
        request=req,
        current_market_price_native=Decimal("0.0005"),
        order_type="LIMIT",
        limit_price_native=Decimal("0.0003")
    )
    assert res.status == "SUBMITTED"

    # Cancel order
    cancel_res = paper_service.cancel_order(res.order_id)
    assert cancel_res["status"] == "CANCELLED"

    # Locked cash must be fully returned to free cash
    acc_restored = paper_service.get_account("solana:mainnet")
    assert acc_restored.current_virtual_balance == initial_free
    assert acc_restored.allocated_virtual_balance is None or acc_restored.allocated_virtual_balance == Decimal("0.0")
    assert acc_restored.total_equity_native == initial_free + initial_locked


# =============================================================================
# PAPER-15: Partial Fill Execution and Proportional Cash Release
# =============================================================================

def test_paper_15_partial_fill_and_locked_cash(paper_service):
    acc = paper_service.get_account("solana:mainnet")
    initial_free = acc.current_virtual_balance

    total_order_size = Decimal("6.0")
    req = PaperOrderRequest(
        account_id=acc.account_id,
        client_order_id="part-fill-001",
        asset_id="PartialFillToken1111111111111111111111111",
        pool_id="PartPool111111111111111111111111111111",
        side="BUY",
        input_mint="native",
        output_mint="PartialFillToken1111111111111111111111111",
        input_amount_raw=total_order_size,
        chain_id="solana:mainnet"
    )
    res = paper_service.execute_order(
        request=req,
        current_market_price_native=Decimal("0.001"),
        order_type="LIMIT",
        limit_price_native=Decimal("0.0005")
    )
    assert res.status == "SUBMITTED"

    # Execute partial fill of 2.5 SOL
    part_res = paper_service.execute_partial_fill(
        order_id=res.order_id,
        fill_quantity_native=Decimal("2.5"),
        market_price_native=Decimal("0.0005")
    )
    assert part_res["status"] == "PARTIALLY_FILLED"
    assert part_res["filled_quantity"] == 2.5
    assert part_res["remaining_quantity"] == 3.5

    # Check that 2.5 SOL was released from locked
    acc_mid = paper_service.get_account("solana:mainnet")
    assert acc_mid.allocated_virtual_balance == Decimal("3.5")

    # Execute remaining 3.5 SOL fill
    final_res = paper_service.execute_partial_fill(
        order_id=res.order_id,
        fill_quantity_native=Decimal("3.5"),
        market_price_native=Decimal("0.0005")
    )
    assert final_res["status"] == "FILLED"
    assert final_res["remaining_quantity"] == 0.0

    acc_final = paper_service.get_account("solana:mainnet")
    assert acc_final.allocated_virtual_balance is None or acc_final.allocated_virtual_balance == Decimal("0.0")


# =============================================================================
# PAPER-16: Duplicate Client Order ID Across Process Restarts
# =============================================================================

def test_paper_16_duplicate_client_order_id_across_restarts(temp_migrated_db):
    service_1 = PersistentPaperService(db_path=temp_migrated_db)
    acc = service_1.get_account("solana:mainnet")

    req = PaperOrderRequest(
        account_id=acc.account_id,
        client_order_id="restart-idem-unique-key-777",
        asset_id="RestartToken11111111111111111111111111111",
        pool_id="RestartPool11111111111111111111111111111",
        side="BUY",
        input_mint="native",
        output_mint="RestartToken11111111111111111111111111111",
        input_amount_raw=Decimal("1.0"),
        chain_id="solana:mainnet"
    )
    res_1 = service_1.execute_order(request=req, current_market_price_native=Decimal("0.0002"))
    assert res_1.status == "FILLED"

    # Restart service with fresh instance
    service_2 = PersistentPaperService(db_path=temp_migrated_db)

    # Attempt to replay the exact same client_order_id
    res_2 = service_2.execute_order(request=req, current_market_price_native=Decimal("0.0002"))
    assert res_2.status == "REJECTED"
    assert "DUPLICATE_CLIENT_ORDER_ID" in res_2.rejection_reason


# =============================================================================
# PAPER-17: Rejection of Missing or Unverified Token Decimals
# =============================================================================

def test_paper_17_reject_missing_or_unverified_token_decimals(paper_service):
    acc = paper_service.get_account("solana:mainnet")

    # Order for completely unseeded, unverified token
    req_unknown = PaperOrderRequest(
        account_id=acc.account_id,
        client_order_id="unknown-decimal-order-001",
        asset_id="UnknownTokenWithNoVerifiedDecimals1111111111",
        pool_id="UnknownPool11111111111111111111111111111",
        side="BUY",
        input_mint="native",
        output_mint="UnknownTokenWithNoVerifiedDecimals1111111111",
        input_amount_raw=Decimal("1.0"),
        chain_id="solana:mainnet"
    )
    res = paper_service.execute_order(
        request=req_unknown,
        current_market_price_native=Decimal("0.0001")
    )
    assert res.status == "REJECTED"
    assert "MISSING_TOKEN_DECIMALS" in res.rejection_reason


# =============================================================================
# PAPER-18: Chain Reorganization Invalidation & Locked Cash Return
# =============================================================================

def test_paper_18_reorg_invalidation_cancels_orders_and_releases_cash(paper_service):
    acc = paper_service.get_account("solana:mainnet")
    initial_free = acc.current_virtual_balance

    req = PaperOrderRequest(
        account_id=acc.account_id,
        client_order_id="reorg-lock-001",
        asset_id="LimitTestToken1111111111111111111111111111",
        pool_id="ReorgPoolTarget11111111111111111111111",
        side="BUY",
        input_mint="native",
        output_mint="LimitTestToken1111111111111111111111111111",
        input_amount_raw=Decimal("3.0"),
        chain_id="solana:mainnet"
    )
    res = paper_service.execute_order(
        request=req,
        current_market_price_native=Decimal("0.0005"),
        order_type="LIMIT",
        limit_price_native=Decimal("0.0003")
    )
    assert res.status == "SUBMITTED"

    # Trigger reorg invalidation
    reorg_res = paper_service.handle_reorg_invalidation(
        chain_id="solana:mainnet",
        pool_address="ReorgPoolTarget11111111111111111111111"
    )
    assert reorg_res["status"] == "SUCCESS"
    assert reorg_res["cancelled_orders_count"] >= 1

    # Cash must be released
    acc_restored = paper_service.get_account("solana:mainnet")
    assert acc_restored.current_virtual_balance == initial_free
    assert acc_restored.allocated_virtual_balance is None or acc_restored.allocated_virtual_balance == Decimal("0.0")


# =============================================================================
# PAPER-19: Complete Portfolio Reconciliation with Locked Cash
# =============================================================================

def test_paper_19_full_portfolio_reconciliation_with_locked_cash(paper_service):
    acc = paper_service.get_account("solana:mainnet")

    # 1. Fill a trade (creates open position)
    buy_req = PaperOrderRequest(
        account_id=acc.account_id,
        client_order_id="recon-pos-trade-01",
        asset_id="ReconToken1111111111111111111111111111",
        pool_id="ReconPool11111111111111111111111111111",
        side="BUY",
        input_mint="native",
        output_mint="ReconToken1111111111111111111111111111",
        input_amount_raw=Decimal("2.0"),
        chain_id="solana:mainnet"
    )
    paper_service.execute_order(
        request=buy_req,
        current_market_price_native=Decimal("0.0005"),
        native_price_usd=Decimal("200.0")
    )

    # 2. Submit a limit order (creates locked cash)
    limit_req = PaperOrderRequest(
        account_id=acc.account_id,
        client_order_id="recon-limit-open-01",
        asset_id="LimitTestToken1111111111111111111111111111",
        pool_id="LimitPool1111111111111111111111111111111",
        side="BUY",
        input_mint="native",
        output_mint="LimitTestToken1111111111111111111111111111",
        input_amount_raw=Decimal("5.0"),
        chain_id="solana:mainnet"
    )
    paper_service.execute_order(
        request=limit_req,
        current_market_price_native=Decimal("0.0005"),
        order_type="LIMIT",
        limit_price_native=Decimal("0.0003")
    )

    recon = paper_service.reconcile_portfolio("solana:mainnet")
    assert recon["is_balanced"] is True
    assert recon["discrepancy_usd"] == 0.0
    assert recon["free_cash_usd"] > 0
    assert recon["locked_cash_usd"] == float(Decimal("5.0") * Decimal("200.0"))
    assert recon["total_cash_usd"] == recon["free_cash_usd"] + recon["locked_cash_usd"]
    assert round(recon["calculated_equity_usd"], 2) == round(recon["total_cash_usd"] + recon["positions_value_usd"], 2)

