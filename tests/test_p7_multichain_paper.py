import sys
import os
import uuid
import pytest
from decimal import Decimal
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath("./backend"))
from app.main import app
from app.schemas.contracts import PaperOrderRequest
from app.services.paper_simulation_engine import PaperSimulationEngine, paper_simulation_engine

client = TestClient(app)

# =====================================================================
# 1. Multi-Chain Desk Account Isolation Tests
# =====================================================================
def test_desk_initialization_and_isolation():
    engine = PaperSimulationEngine()

    sol_acc = engine.get_account("solana:mainnet")
    assert sol_acc.native_symbol == "SOL"
    assert sol_acc.current_virtual_balance == Decimal("100.0")

    base_acc = engine.get_account("eip155:8453")
    assert base_acc.native_symbol == "ETH"
    assert base_acc.current_virtual_balance == Decimal("10.0")

    bnb_acc = engine.get_account("eip155:56")
    assert bnb_acc.native_symbol == "BNB"
    assert bnb_acc.current_virtual_balance == Decimal("50.0")

    sui_acc = engine.get_account("sui:mainnet")
    assert sui_acc.native_symbol == "SUI"
    assert sui_acc.current_virtual_balance == Decimal("10000.0")

    # Execute BUY on Base
    req = PaperOrderRequest(
        account_id=base_acc.account_id,
        client_order_id=str(uuid.uuid4()),
        asset_id="0xBaseToken",
        pool_id="0xBasePool",
        side="BUY",
        input_mint="ETH",
        output_mint="0xBaseToken",
        input_amount_raw=Decimal("2.0"),
        max_slippage_bps=200,
        chain_id="eip155:8453"
    )
    res = engine.execute_order(req, current_market_price_native=Decimal("0.0001"), pool_liquidity_usd=Decimal("200000.0"))
    assert res.status == "FILLED"

    # Base balance must drop from 10 to 8 ETH
    updated_base = engine.get_account("eip155:8453")
    assert updated_base.current_virtual_balance == Decimal("8.0")
    assert updated_base.allocated_virtual_balance is None or updated_base.total_equity_native == Decimal("10.0")

    # Solana balance must remain strictly untouched at 100 SOL
    untouched_sol = engine.get_account("solana:mainnet")
    assert untouched_sol.current_virtual_balance == Decimal("100.0")


# =====================================================================
# 2. AMM Impact Curve & Slippage Protection Tests
# =====================================================================
def test_amm_price_impact_curve():
    engine = PaperSimulationEngine()

    # $500 trade on $100k pool -> 50 bps impact
    low_impact = engine.calculate_price_impact_bps(Decimal("500.0"), Decimal("100000.0"))
    assert low_impact == 50

    # $15,000 trade on $100k pool -> 1500 bps impact
    high_impact = engine.calculate_price_impact_bps(Decimal("15000.0"), Decimal("100000.0"))
    assert high_impact == 1500


def test_order_slippage_rejection():
    engine = PaperSimulationEngine()
    # Trade with high impact exceeding strict 20 bps slippage limit
    req = PaperOrderRequest(
        account_id="dummy",
        client_order_id=str(uuid.uuid4()),
        asset_id="0xTokenA",
        pool_id="0xPoolA",
        side="BUY",
        input_mint="SOL",
        output_mint="0xTokenA",
        input_amount_raw=Decimal("10.0"), # $2,000
        max_slippage_bps=20, # Very tight 0.2% max
        chain_id="solana:mainnet"
    )
    # On $10,000 pool, $2,000 trade has 2000 bps impact -> must reject
    res = engine.execute_order(req, current_market_price_native=Decimal("0.001"), pool_liquidity_usd=Decimal("10000.0"))
    assert res.status == "REJECTED"
    assert "SLIPPAGE_EXCEEDED" in res.rejection_reason


# =====================================================================
# 3. Balance & Idempotency Invariants
# =====================================================================
def test_insufficient_virtual_balance_rejection():
    engine = PaperSimulationEngine()
    # Attempt to spend 500 SOL when virtual desk only has 100 SOL
    req = PaperOrderRequest(
        account_id="dummy",
        client_order_id=str(uuid.uuid4()),
        asset_id="0xTokenB",
        pool_id="0xPoolB",
        side="BUY",
        input_mint="SOL",
        output_mint="0xTokenB",
        input_amount_raw=Decimal("500.0"),
        max_slippage_bps=500,
        chain_id="solana:mainnet"
    )
    res = engine.execute_order(req, current_market_price_native=Decimal("0.001"))
    assert res.status == "REJECTED"
    assert "INSUFFICIENT_VIRTUAL_BALANCE" in res.rejection_reason


def test_idempotent_order_rejection():
    engine = PaperSimulationEngine()
    order_id = f"client-order-key-{uuid.uuid4()}"
    req = PaperOrderRequest(
        account_id="dummy",
        client_order_id=order_id,
        asset_id="0xTokenC",
        pool_id="0xPoolC",
        side="BUY",
        input_mint="SOL",
        output_mint="0xTokenC",
        input_amount_raw=Decimal("1.0"),
        max_slippage_bps=500,
        chain_id="solana:mainnet"
    )
    first_res = engine.execute_order(req, current_market_price_native=Decimal("0.001"))
    assert first_res.status == "FILLED"

    # Second submission with duplicate client_order_id must be rejected
    second_res = engine.execute_order(req, current_market_price_native=Decimal("0.001"))
    assert second_res.status == "REJECTED"
    assert "DUPLICATE_CLIENT_ORDER_ID" in second_res.rejection_reason


# =====================================================================
# 4. Full Cycle Buy -> Price Doubling -> Sell -> Realized PnL Test
# =====================================================================
def test_full_trade_cycle_and_pnl_realization():
    engine = PaperSimulationEngine()
    asset = "0xProfitableMeme"
    pool = "0xProfitablePool"

    # 1. Buy with 10 SOL at price 0.001 SOL
    buy_req = PaperOrderRequest(
        account_id="dummy",
        client_order_id=str(uuid.uuid4()),
        asset_id=asset,
        pool_id=pool,
        side="BUY",
        input_mint="SOL",
        output_mint=asset,
        input_amount_raw=Decimal("10.0"),
        max_slippage_bps=500,
        chain_id="solana:mainnet"
    )
    buy_res = engine.execute_order(buy_req, current_market_price_native=Decimal("0.001"), pool_liquidity_usd=Decimal("500000.0"))
    assert buy_res.status == "FILLED"
    tokens_held = buy_res.output_amount_raw

    # Verify position is open
    positions = engine.get_positions("solana:mainnet")
    assert len(positions) == 1
    assert positions[0].asset_address == asset

    # 2. Token price doubles to 0.002 SOL; Sell 100% of tokens
    sell_req = PaperOrderRequest(
        account_id="dummy",
        client_order_id=str(uuid.uuid4()),
        asset_id=asset,
        pool_id=pool,
        side="SELL",
        input_mint=asset,
        output_mint="SOL",
        input_amount_raw=tokens_held,
        max_slippage_bps=500,
        chain_id="solana:mainnet"
    )
    sell_res = engine.execute_order(sell_req, current_market_price_native=Decimal("0.002"), pool_liquidity_usd=Decimal("500000.0"))
    assert sell_res.status == "FILLED"
    native_received = sell_res.output_amount_raw

    # Proceeds should be roughly double (minus spread/impact)
    assert native_received > Decimal("19.0")

    # Account realized PnL should be positive (~9-10 SOL)
    acc = engine.get_account("solana:mainnet")
    assert acc.realized_pnl_native > Decimal("9.0")
    assert acc.win_rate == Decimal("100.00")
    assert acc.current_virtual_balance > Decimal("109.0")

    # Position should now be closed
    post_positions = engine.get_positions("solana:mainnet")
    assert len(post_positions) == 0


# =====================================================================
# 5. Multi-Chain API Endpoints
# =====================================================================
def test_api_paper_multichain_endpoints():
    # Query Base account
    base_resp = client.get("/api/v1/paper/account?chain_id=eip155:8453")
    assert base_resp.status_code == 200
    base_data = base_resp.json()
    assert base_data["chain_id"] == "eip155:8453"
    assert base_data["native_symbol"] == "ETH"

    # Query BNB account
    bnb_resp = client.get("/api/v1/paper/account?chain_id=eip155:56")
    assert bnb_resp.status_code == 200
    bnb_data = bnb_resp.json()
    assert bnb_data["chain_id"] == "eip155:56"
    assert bnb_data["native_symbol"] == "BNB"

    # Submit paper order on Base
    order_payload = {
        "account_id": base_data["account_id"],
        "client_order_id": f"api-test-{uuid.uuid4()}",
        "asset_id": "0x4ed4e862860bed51a9570b96d89af5e1b0efefed",
        "pool_id": "0xpool123",
        "side": "BUY",
        "input_mint": "ETH",
        "output_mint": "0x4ed4e862860bed51a9570b96d89af5e1b0efefed",
        "input_amount_raw": "0.5",
        "max_slippage_bps": 200,
        "chain_id": "eip155:8453"
    }
    order_resp = client.post("/api/v1/paper/order", json=order_payload)
    assert order_resp.status_code == 200
    order_data = order_resp.json()
    assert order_data["status"] == "FILLED"
    assert order_data["chain_id"] == "eip155:8453"
    assert len(order_data["execution_events"]) == 1
