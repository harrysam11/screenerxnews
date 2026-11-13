import sys
import os
import json
import pytest
from decimal import Decimal
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath("./backend"))
sys.path.insert(0, os.path.abspath("./windmill_scripts"))

from app.main import app
from app.services.hermes_analyst import hermes_analyst
from wf01_candidate_discovery import discover_dex_candidates
from wf08_opportunity_scoring import score_token
from wf14_api_health_guardian import check_all_providers
from wf20_simulated_execution import execute_simulated_order
from wf33_chain_reorg_handler import evaluate_block_canonicality

client = TestClient(app)

# =====================================================================
# 1. Windmill Execution Script Tests
# =====================================================================
def test_windmill_wf01_discovery():
    candidates = discover_dex_candidates("eip155:8453")
    assert len(candidates) >= 1
    cand = candidates[0]
    assert cand["chain_id"] == "eip155:8453"
    assert "asset_address" in cand
    assert cand["symbol"] == "DEGEN"


def test_windmill_wf08_scoring_and_alert_trigger():
    # Token with high score -> should trigger alert
    res_high = score_token(
        chain_id="eip155:8453",
        asset_address="0x4ed4e862860bed51a9570b96d89af5e1b0efefed",
        liquidity_usd=Decimal("150000"),
        volume_5m_usd=Decimal("15000"),
        volume_1h_usd=Decimal("60000"),
        tx_count_5m=35,
        price_change_5m_pct=Decimal("12.0"),
        token_symbol="DEGEN",
        token_name="Degen"
    )
    assert res_high["status"] == "SUCCESS"
    assert res_high["composite_score"] >= 80.0
    assert res_high["should_dispatch_alert"] is True


def test_windmill_wf14_health_guardian():
    report = check_all_providers()
    assert len(report) >= 3
    for chain_id, status in report.items():
        assert status["status"] == "HEALTHY"
        assert status["available_tokens"] > 0


def test_windmill_wf20_paper_execution():
    res = execute_simulated_order(
        chain_id="solana:mainnet",
        asset_id="DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
        pool_id="0xbonkpool",
        side="BUY",
        input_mint="SOL",
        output_mint="BONK",
        input_amount_raw=Decimal("1.5")
    )
    assert res["status"] == "FILLED"
    assert res["side"] == "BUY"
    assert res["output_amount_raw"] > 0


def test_windmill_wf33_chain_reorg():
    # Canonical block match
    canonical_res = evaluate_block_canonicality("eip155:8453", 100, "0xHash100", "0xParent99", "0xParent99")
    assert canonical_res["status"] == "CANONICAL"
    assert canonical_res["action_required"] == "NONE"

    # Forked / reorg block
    forked_res = evaluate_block_canonicality("eip155:8453", 100, "0xHash100", "0xAttackerParent99", "0xParent99")
    assert forked_res["status"] == "REORG_DETECTED"
    assert forked_res["action_required"] == "ROLLBACK_UNFINALIZED_EVENTS"


# =====================================================================
# 2. n8n Workflow Validation Tests
# =====================================================================
def test_n8n_workflows_valid_json_and_nodes():
    n8n_dir = "./n8n_workflows"
    expected_workflows = [
        "WF-06_rss_news_collector.json",
        "WF-07_narrative_correlator.json",
        "WF-09_telegram_conviction_alert.json",
        "WF-10_trade_approval_requester.json",
        "WF-16_system_error_handler.json",
        "WF-37_multichain_digest_dispatch.json"
    ]
    for wf_file in expected_workflows:
        filepath = os.path.join(n8n_dir, wf_file)
        assert os.path.exists(filepath), f"Missing n8n workflow file: {wf_file}"
        with open(filepath, "r") as f:
            data = json.load(f)
            assert "name" in data
            assert "nodes" in data
            assert len(data["nodes"]) >= 2
            assert "connections" in data


# =====================================================================
# 3. Hermes AI Research Analyst Tests
# =====================================================================
def test_hermes_synthesize_token_dossier():
    dossier = hermes_analyst.synthesize_token_dossier(
        chain_id="eip155:8453",
        asset_address="0x4ed4e862860bed51a9570b96d89af5e1b0efefed",
        symbol="DEGEN",
        name="Degen",
        liquidity_usd=Decimal("150000"),
        volume_24h_usd=Decimal("800000"),
        top10_concentration_pct=Decimal("14.5"),
        is_honeypot=False,
        is_mint_revoked=True,
        is_lp_burned=True
    )
    assert dossier["conviction_tier"] == "HIGH_CONVICTION"
    assert dossier["composite_score"] >= 75.0
    assert "Hermes Intelligence Brief" in dossier["markdown_brief"]
    assert dossier["hard_reject_triggered"] is False


def test_hermes_critical_reject_dossier():
    dossier = hermes_analyst.synthesize_token_dossier(
        chain_id="eip155:1",
        asset_address="0xHoneypotScam",
        symbol="SCAM",
        name="Scam Token",
        is_honeypot=True, # Trapped
        is_mint_revoked=False,
        is_lp_burned=False
    )
    assert dossier["conviction_tier"] == "CRITICAL_REJECT"
    assert dossier["hard_reject_triggered"] is True
    assert "HONEYPOT_DETECTED" in str(dossier["rejection_reasons"])


def test_hermes_api_endpoints():
    # Dossier endpoint
    res_dossier = client.get("/api/v1/hermes/dossier/eip155:8453/0x4ed4e862860bed51a9570b96d89af5e1b0efefed?symbol=DEGEN&name=Degen")
    assert res_dossier.status_code == 200
    dossier_data = res_dossier.json()
    assert dossier_data["chain_id"] == "eip155:8453"
    assert "markdown_brief" in dossier_data

    # Daily digest endpoint
    res_digest = client.get("/api/v1/hermes/daily-digest")
    assert res_digest.status_code == 200
    digest_data = res_digest.json()
    assert "markdown_content" in digest_data
