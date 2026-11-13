import sys
import os
import pytest
from decimal import Decimal
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath("./backend"))
from app.main import app

client = TestClient(app)

def test_health_check():
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["live_trading_allowed"] is False

def test_discovery_tokens_endpoint():
    response = client.get("/api/v1/discovery/tokens")
    assert response.status_code == 200
    tokens = response.json()
    assert isinstance(tokens, list)
    assert len(tokens) > 0
    first = tokens[0]
    assert "mint_address" in first
    assert "price_usd" in first
    assert "composite_opportunity_score" in first

def test_forensics_token_endpoint():
    mint = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
    response = client.get(f"/api/v1/forensics/token/{mint}")
    assert response.status_code == 200
    report = response.json()
    assert report["mint_address"] == mint
    assert "risk_score" in report
    assert "findings" in report
    assert report["lp_status"] == "burned"

def test_first_buyers_endpoint():
    mint = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
    response = client.get(f"/api/v1/forensics/first-buyers/{mint}")
    assert response.status_code == 200
    data = response.json()
    assert data["mint_address"] == mint
    assert "buyers" in data
    assert len(data["buyers"]) >= 1

def test_scoring_endpoint():
    mint = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
    response = client.get(f"/api/v1/scoring/opportunity/{mint}")
    assert response.status_code == 200
    data = response.json()
    assert data["mint_address"] == mint
    assert "composite_score" in data
    assert "confidence_coverage_pct" in data
    # Check that O = 0.35M + 0.30S + 0.20W + 0.15N holds approximately
    m = Decimal(str(data["market_momentum_score"]))
    s = Decimal(str(data["security_safety_score"]))
    w = Decimal(str(data["wallet_intelligence_score"]))
    n = Decimal(str(data["narrative_sentiment_score"]))
    expected = (Decimal("0.35") * m) + (Decimal("0.30") * s) + (Decimal("0.20") * w) + (Decimal("0.15") * n)
    actual = Decimal(str(data["composite_score"]))
    assert abs(actual - expected) <= Decimal("0.05")

def test_paper_desk_endpoints():
    # 1. Check account
    res_acc = client.get("/api/v1/paper/account")
    assert res_acc.status_code == 200
    acc = res_acc.json()
    assert "virtual_sol_balance" in acc
    assert "total_equity_sol" in acc

    # 2. Place paper order
    order_payload = {
        "asset_id": "00000000-0000-0000-0000-000000000001",
        "pool_id": "00000000-0000-0000-0000-000000000002",
        "side": "BUY",
        "amount_sol": 1.5,
        "max_slippage_bps": 150
    }
    res_order = client.post("/api/v1/paper/order", json=order_payload)
    assert res_order.status_code == 200
    order_data = res_order.json()
    assert order_data["status"] == "FILLED"
    assert Decimal(str(order_data["tokens_received"])) > 0
    assert order_data["simulated_slippage_bps"] > 0

    # 3. Check positions
    res_pos = client.get("/api/v1/paper/positions")
    assert res_pos.status_code == 200
    positions = res_pos.json()
    assert len(positions) > 0
