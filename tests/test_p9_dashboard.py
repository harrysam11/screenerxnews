import sys
import os
import pytest
from decimal import Decimal
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath("./backend"))
from app.main import app
from app.services.dashboard_service import dashboard_service

client = TestClient(app)

# =====================================================================
# 1. Dashboard Overview KPI Tests
# =====================================================================
def test_dashboard_overview_kpis():
    response = client.get("/api/v1/dashboard/overview")
    assert response.status_code == 200
    data = response.json()

    assert "tracked_tokens_count" in data
    assert data["active_chains_count"] == 12
    assert "scams_blocked_count" in data
    assert float(data["total_paper_equity_usd"]) > 0

    orchestrators = data["dual_orchestrator_status"]
    assert orchestrators["windmill"]["status"] in ("NOT_CONNECTED", "ONLINE")
    assert orchestrators["n8n"]["status"] in ("DEFERRED", "ONLINE")
    assert orchestrators["hermes"]["status"] in ("STANDALONE_CLI", "READ_ONLY")


# =====================================================================
# 2. Multi-Chain Screener Feed & Filter Tests
# =====================================================================
def test_dashboard_screener_unconstrained():
    response = client.get("/api/v1/dashboard/screener")
    assert response.status_code == 200
    data = response.json()
    assert data["total_matches"] >= 3
    assert len(data["tokens"]) >= 3


def test_dashboard_screener_chain_filter():
    # Filter only Base tokens
    response = client.get("/api/v1/dashboard/screener?chain_id=eip155:8453")
    assert response.status_code == 200
    data = response.json()
    assert len(data["tokens"]) > 0
    for t in data["tokens"]:
        assert t["chain_id"] in ("eip155:8453", "base")
    assert any(t["symbol"] == "DEGEN" for t in data["tokens"])


def test_dashboard_screener_score_filter():
    # Only tokens with score >= 85
    response = client.get("/api/v1/dashboard/screener?min_score=85.0")
    assert response.status_code == 200
    data = response.json()
    for t in data["tokens"]:
        assert float(t["composite_score"]) >= 85.0
        assert t["hard_reject_triggered"] is False


def test_dashboard_screener_sector_filter():
    # Only ANIMALS sector tokens
    response = client.get("/api/v1/dashboard/screener?sector=ANIMALS")
    assert response.status_code == 200
    data = response.json()
    for t in data["tokens"]:
        assert t["sector"] == "ANIMALS"


# =====================================================================
# 3. Chains Status Matrix Tests
# =====================================================================
def test_dashboard_chains_matrix():
    response = client.get("/api/v1/dashboard/chains")
    assert response.status_code == 200
    chains = response.json()
    assert len(chains) == 12

    chain_ids = [c["chain_id"] for c in chains]
    assert "solana:mainnet" in chain_ids
    assert "eip155:1" in chain_ids
    assert "eip155:8453" in chain_ids
    assert "eip155:42161" in chain_ids
    assert "sui:mainnet" in chain_ids
    assert "ton:mainnet" in chain_ids

    for c in chains:
        assert c["status"] == "OPERATIONAL"
        assert c["finality_blocks"] >= 1


# =====================================================================
# 4. Narratives Intelligence Feed Tests
# =====================================================================
def test_dashboard_narratives():
    response = client.get("/api/v1/dashboard/narratives")
    assert response.status_code == 200
    data = response.json()
    assert "trending_sectors" in data
    assert "recent_headlines" in data
    assert data.get("status") in ("NO_NARRATIVE_PIPELINE_CONFIGURED", "CONFIGURED")
