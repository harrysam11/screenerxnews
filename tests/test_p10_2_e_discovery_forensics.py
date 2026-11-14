import sys
import os
import pytest
from decimal import Decimal
from datetime import datetime, timezone
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath("./backend"))
from app.main import app
from app.schemas.contracts import TriState, EvidenceStatus
from app.services.discovery_engine import DiscoveryEngine, discovery_engine

client = TestClient(app)

def test_data_01_discovery_tokens_have_source_and_timestamp():
    """
    DATA-01: Discovered tokens must contain source and timestamp attribution.
    """
    response = client.get("/api/v1/discovery/tokens")
    assert response.status_code == 200
    tokens = response.json()
    assert len(tokens) > 0
    for tok in tokens:
        assert "source" in tok
        assert tok["source"] is not None and len(tok["source"]) > 0
        assert "observed_at" in tok
        assert tok["observed_at"] is not None

def test_data_01_registration_and_retrieval_preserves_source():
    """
    DATA-01: Registering a candidate token preserves source and observed_at.
    """
    custom_mint = "CustomMintTest11111111111111111111111111111111"
    reg_payload = {
        "mint_address": custom_mint,
        "chain_id": "solana:mainnet",
        "symbol": "CUST",
        "source": "raydium_clmm_stream"
    }
    reg_resp = client.post("/api/v1/discovery/register", json=reg_payload)
    assert reg_resp.status_code == 200
    data = reg_resp.json()
    assert data["mint_address"] == custom_mint
    assert data["source"] == "raydium_clmm_stream"

    # Query tokens and verify our custom token is listed with source and observed_at
    tokens = discovery_engine.list_tokens(min_liquidity=0.0, min_volume_1h=0.0, max_risk_score=100.0)
    matching = [t for t in tokens if t.mint_address == custom_mint]
    assert len(matching) >= 1
    assert matching[0].source == "raydium_clmm_stream"
    assert matching[0].observed_at is not None

def test_data_01_forensics_report_and_findings_have_source_and_timestamp():
    """
    DATA-01: Forensics report and all findings must contain source and timestamp attribution.
    """
    mint = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
    response = client.get(f"/api/v1/forensics/token/{mint}")
    assert response.status_code == 200
    report = response.json()
    assert "source" in report
    assert report["source"] == "solana_rpc:getAccountInfo"
    assert "observed_at" in report
    assert report["observed_at"] is not None

    findings = report.get("findings", [])
    assert len(findings) > 0
    for f in findings:
        assert "source" in f
        assert f["source"] is not None
        assert "observed_at" in f
        assert f["observed_at"] is not None

def test_data_02_missing_evidence_awards_zero_safety_points():
    """
    DATA-02: Missing or unverified evidence (TriState.UNKNOWN) awards ZERO safety points.
    Only verified affirmative evidence earns safety points.
    """
    # 1. Completely missing evidence -> 0 safety points
    points_unknown = DiscoveryEngine.calculate_safety_points(
        mint_revoked=TriState.UNKNOWN,
        lp_burned=TriState.UNKNOWN,
        is_honeypot=TriState.UNKNOWN
    )
    assert points_unknown == Decimal("0.0")

    # 2. Only mint revoked verified (+15)
    points_mint = DiscoveryEngine.calculate_safety_points(
        mint_revoked=TriState.TRUE,
        lp_burned=TriState.UNKNOWN,
        is_honeypot=TriState.UNKNOWN
    )
    assert points_mint == Decimal("15.0")

    # 3. Mint revoked (+15) and LP burned (+20) -> 35
    points_mint_lp = DiscoveryEngine.calculate_safety_points(
        mint_revoked=TriState.TRUE,
        lp_burned=TriState.TRUE,
        is_honeypot=TriState.UNKNOWN
    )
    assert points_mint_lp == Decimal("35.0")

    # 4. Fully verified clean token -> 50 points
    points_full = DiscoveryEngine.calculate_safety_points(
        mint_revoked=TriState.TRUE,
        lp_burned=TriState.TRUE,
        is_honeypot=TriState.FALSE
    )
    assert points_full == Decimal("50.0")

def test_data_02_unverified_token_forensics_fails_closed():
    """
    DATA-02: Querying forensics for an unknown or unverified token mint returns
    INSUFFICIENT confidence and UNKNOWN tri-states with maximum risk score.
    """
    unknown_mint = "UnknownUnverifiedMint1111111111111111111111"
    response = client.get(f"/api/v1/forensics/token/{unknown_mint}")
    assert response.status_code == 200
    report = response.json()
    assert report["confidence"] == "INSUFFICIENT"
    assert report["is_mint_revoked"] == "unknown"
    assert report["is_freeze_revoked"] == "unknown"
    assert report["lp_burned"] == "unknown"
    assert report["is_honeypot"] == "unknown"
    assert Decimal(str(report["risk_score"])) == Decimal("100.0")
    assert report["risk_level"] == "CRITICAL"
    assert report["source"] == "rpc_scan:fallback"
    assert report["observed_at"] is not None
