import sys
import os
import pytest
from decimal import Decimal
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath("./backend"))
from app.main import app

client = TestClient(app)

def test_tri_state_semantics_in_forensics():
    mint = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
    response = client.get(f"/api/v1/forensics/token/{mint}")
    assert response.status_code == 200
    data = response.json()
    # Ensure tri-state values are valid enum literals
    assert data["is_token_2022"] in ["true", "false", "unknown"]
    assert data["is_mint_revoked"] in ["true", "false", "unknown"]
    assert data["is_freeze_revoked"] in ["true", "false", "unknown"]
    assert data["lp_burned"] in ["true", "false", "unknown"]
    assert data["is_honeypot"] in ["true", "false", "unknown"]
    assert "holder_coverage_pct" in data
    assert data["holder_coverage_pct"] is not None

def test_symmetrical_paper_sell_order():
    """
    AUD-04: Test that paper order supports SELL side where input_amount_raw 
    is denominated in raw meme token units and output is SOL.
    """
    from tests.fixtures.test_asset_helper import register_test_asset_fixture
    from app.core.config import settings
    register_test_asset_fixture(settings.get_canonical_sqlite_path(), "solana:mainnet", "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263", decimals=5, symbol="BONK", authoritative=True)
    import sqlite3
    with sqlite3.connect(settings.get_canonical_sqlite_path()) as conn:
        conn.execute("""
            INSERT OR REPLACE INTO paper_positions_v2 (
                position_id, account_id, chain_id, asset_address, symbol,
                side, token_amount_raw, cost_basis_native, entry_price_native,
                current_price_native, unrealized_pnl_native, opened_at, updated_at
            ) VALUES (
                'pos_test_bonk_seed', '00000000-0000-0000-0000-000000000001',
                'solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp', 'DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263',
                'BONK', 'LONG', '100000000000.0', '450.0', '0.000045', '0.000045', '0.0',
                datetime('now'), datetime('now')
            );
        """)
        conn.commit()

    order_payload = {
        "account_id": "00000000-0000-0000-0000-000000000001",
        "client_order_id": "client-sell-test-001",
        "asset_id": "00000000-0000-0000-0000-000000000001",
        "pool_id": "00000000-0000-0000-0000-000000000002",
        "side": "SELL",
        "input_mint": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263", # Selling BONK
        "output_mint": "So11111111111111111111111111111111111111112", # Receiving WSOL
        "input_amount_raw": 50000000000, # 50,000 BONK raw units
        "max_slippage_bps": 200
    }
    response = client.post("/api/v1/paper/order", json=order_payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "FILLED", f"Order was not filled, full data: {data}"
    assert data["side"] == "SELL"
    assert data["client_order_id"] == "client-sell-test-001"
    assert data["output_amount_raw"] is not None
    assert Decimal(str(data["output_amount_raw"])) > 0
    assert len(data["execution_events"]) == 1
    event = data["execution_events"][0]
    assert event["event_type"] == "FILL"
    assert event["liquidity_impact_bps"] > 0
    assert event["simulated_slippage_bps"] == 200

def test_canonical_digest_and_hash_generation():
    """
    AUD-03: Test that trade intent returns both canonical_intent_digest and transaction_message_hash.
    """
    payload = {
        "asset_id": "00000000-0000-0000-0000-000000000001",
        "pool_id": "00000000-0000-0000-0000-000000000002",
        "wallet_address": "BurnerWalletMock111111111111111111111111111",
        "side": "BUY",
        "input_mint": "So11111111111111111111111111111111111111112",
        "output_mint": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
        "input_amount_raw": 200000000, # 0.20 SOL
        "max_slippage_bps": 150
    }
    response = client.post("/api/v1/execution/intent", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["policy_checks_passed"] is True
    assert "canonical_intent_digest" in data
    assert len(data["canonical_intent_digest"]) == 64
    assert "transaction_message_hash" in data
    assert len(data["transaction_message_hash"]) == 64
