import sys
import os
import pytest
from decimal import Decimal
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath("./backend"))
from app.main import app
from app.core.config import settings

client = TestClient(app)

def test_cold_vault_execution_rejected():
    # Set a mock cold vault in settings
    cold_vault = "ColdVaultMock111111111111111111111111111111"
    settings.COLD_VAULT_ADDRESSES = [cold_vault]

    payload = {
        "asset_id": "00000000-0000-0000-0000-000000000001",
        "pool_id": "00000000-0000-0000-0000-000000000002",
        "wallet_address": cold_vault,
        "side": "BUY",
        "input_mint": "So11111111111111111111111111111111111111112",
        "output_mint": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
        "input_amount_sol": 0.25,
        "max_slippage_bps": 150
    }

    response = client.post("/api/v1/execution/intent", json=payload)
    assert response.status_code == 403
    assert "Cold vault addresses are permanently excluded" in response.json()["detail"]

def test_excessive_trade_amount_rejected():
    burner = "BurnerWalletMock111111111111111111111111111"
    payload = {
        "asset_id": "00000000-0000-0000-0000-000000000001",
        "pool_id": "00000000-0000-0000-0000-000000000002",
        "wallet_address": burner,
        "side": "BUY",
        "input_mint": "So11111111111111111111111111111111111111112",
        "output_mint": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
        "input_amount_sol": 50.0, # Violates 0.5 SOL limit
        "max_slippage_bps": 150
    }

    response = client.post("/api/v1/execution/intent", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["policy_checks_passed"] is False
    assert any("exceeds maximum single trade cap" in r for r in data["rejection_reasons"])

def test_excessive_slippage_tolerance_rejected():
    burner = "BurnerWalletMock111111111111111111111111111"
    payload = {
        "asset_id": "00000000-0000-0000-0000-000000000001",
        "pool_id": "00000000-0000-0000-0000-000000000002",
        "wallet_address": burner,
        "side": "BUY",
        "input_mint": "So11111111111111111111111111111111111111112",
        "output_mint": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
        "input_amount_sol": 0.25,
        "max_slippage_bps": 500 # Violates 250 bps limit
    }

    response = client.post("/api/v1/execution/intent", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["policy_checks_passed"] is False
    assert any("Slippage tolerance exceeds maximum allowed cap" in r for r in data["rejection_reasons"])
