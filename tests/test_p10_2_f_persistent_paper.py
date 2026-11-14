import os
import tempfile
import uuid
import pytest
from decimal import Decimal
from app.schemas.contracts import PaperOrderRequest
from app.services.paper_simulation_engine import PaperSimulationEngine

def test_sqlite_paper_engine_restart_recovery():
    """
    P10.2-F: Verify that paper accounts, balances, open positions, and orders
    survive engine restart when backed by persistent SQLite.
    """
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name

    try:
        # 1. First engine lifecycle: initialize and place trades
        engine_v1 = PaperSimulationEngine(db_path=db_path)
        sol_acc_v1 = engine_v1.get_account("solana:mainnet")
        arb_acc_v1 = engine_v1.get_account("eip155:42161")
        assert sol_acc_v1.current_virtual_balance == Decimal("100.0")
        assert arb_acc_v1.current_virtual_balance == Decimal("10.0")

        order_sol_id = f"sol-order-{uuid.uuid4()}"
        req_sol = PaperOrderRequest(
            account_id=sol_acc_v1.account_id,
            client_order_id=order_sol_id,
            asset_id="0xPersistentSolToken",
            pool_id="0xSolPool",
            side="BUY",
            input_mint="SOL",
            output_mint="0xPersistentSolToken",
            input_amount_raw=Decimal("10.0"),
            max_slippage_bps=250,
            chain_id="solana:mainnet"
        )
        res_sol = engine_v1.execute_order(
            req_sol, current_market_price_native=Decimal("0.002"), pool_liquidity_usd=Decimal("500000.0")
        )
        assert res_sol.status == "FILLED"

        order_arb_id = f"arb-order-{uuid.uuid4()}"
        req_arb = PaperOrderRequest(
            account_id=arb_acc_v1.account_id,
            client_order_id=order_arb_id,
            asset_id="0xPersistentArbToken",
            pool_id="0xArbPool",
            side="BUY",
            input_mint="ETH",
            output_mint="0xPersistentArbToken",
            input_amount_raw=Decimal("3.0"),
            max_slippage_bps=200,
            chain_id="eip155:42161"
        )
        res_arb = engine_v1.execute_order(
            req_arb, current_market_price_native=Decimal("0.0002"), pool_liquidity_usd=Decimal("300000.0")
        )
        assert res_arb.status == "FILLED"

        # Verify state before restart
        assert engine_v1.get_account("solana:mainnet").current_virtual_balance == Decimal("90.0")
        assert engine_v1.get_account("eip155:42161").current_virtual_balance == Decimal("7.0")
        assert len(engine_v1.get_positions("solana:mainnet")) == 1
        assert len(engine_v1.get_positions("eip155:42161")) == 1

        # Simulate crash / restart: drop instance v1
        del engine_v1

        # 2. Second engine lifecycle: reboot from same database file
        engine_v2 = PaperSimulationEngine(db_path=db_path)

        # Verify balances were completely recovered
        sol_acc_v2 = engine_v2.get_account("solana:mainnet")
        arb_acc_v2 = engine_v2.get_account("eip155:42161")
        assert sol_acc_v2.current_virtual_balance == Decimal("90.0")
        assert arb_acc_v2.current_virtual_balance == Decimal("7.0")

        # Verify open positions were recovered
        sol_positions = engine_v2.get_positions("solana:mainnet")
        assert len(sol_positions) == 1
        assert sol_positions[0].mint_address == "0xPersistentSolToken"
        assert sol_positions[0].cost_basis_native == Decimal("10.0")

        arb_positions = engine_v2.get_positions("eip155:42161")
        assert len(arb_positions) == 1
        assert arb_positions[0].mint_address == "0xPersistentArbToken"
        assert arb_positions[0].cost_basis_native == Decimal("3.0")

        # 3. Verify idempotency survives restart
        dup_sol_res = engine_v2.execute_order(
            req_sol, current_market_price_native=Decimal("0.002")
        )
        assert dup_sol_res.status == "REJECTED"
        assert "DUPLICATE_CLIENT_ORDER_ID" in dup_sol_res.rejection_reason

        # 4. Sell recovered position on engine_v2
        tokens_to_sell = sol_positions[0].token_amount_raw
        sell_order_id = f"sol-sell-{uuid.uuid4()}"
        req_sell = PaperOrderRequest(
            account_id=sol_acc_v2.account_id,
            client_order_id=sell_order_id,
            asset_id="0xPersistentSolToken",
            pool_id="0xSolPool",
            side="SELL",
            input_mint="0xPersistentSolToken",
            output_mint="SOL",
            input_amount_raw=tokens_to_sell,
            max_slippage_bps=250,
            chain_id="solana:mainnet"
        )
        # Sell at 2x price (0.004)
        res_sell = engine_v2.execute_order(
            req_sell, current_market_price_native=Decimal("0.004"), pool_liquidity_usd=Decimal("500000.0")
        )
        assert res_sell.status == "FILLED"
        assert engine_v2.get_account("solana:mainnet").winning_trades == 1
        assert len(engine_v2.get_positions("solana:mainnet")) == 0

        # Drop instance v2 and reboot engine_v3 to confirm closed position and PnL persistence
        del engine_v2
        engine_v3 = PaperSimulationEngine(db_path=db_path)
        sol_acc_v3 = engine_v3.get_account("solana:mainnet")
        assert sol_acc_v3.winning_trades == 1
        assert sol_acc_v3.realized_pnl_native > Decimal("9.0")
        assert sol_acc_v3.current_virtual_balance > Decimal("109.0")
        assert len(engine_v3.get_positions("solana:mainnet")) == 0

    finally:
        if os.path.exists(db_path):
            os.remove(db_path)

def test_multichain_12_desks_isolated_in_sqlite():
    """
    P10.2-F: Verify all 12 chains are provisioned and isolated in SQLite.
    """
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name

    try:
        engine = PaperSimulationEngine(db_path=db_path)
        expected_chains = [
            "solana:mainnet", "eip155:1", "eip155:8453", "eip155:56",
            "eip155:42161", "eip155:10", "eip155:137", "eip155:43114",
            "sui:mainnet", "aptos:mainnet", "ton:mainnet", "tron:mainnet"
        ]
        for chain_id in expected_chains:
            acc = engine.get_account(chain_id)
            assert acc.chain_id == chain_id
            assert acc.current_virtual_balance > Decimal("0.0")
    finally:
        if os.path.exists(db_path):
            os.remove(db_path)
