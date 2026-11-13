import sys
import os
import pytest
from decimal import Decimal
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath("./backend"))
from app.main import app
from app.schemas.contracts import TriState, EvidenceStatus, OpportunityScoreBreakdown
from app.services.narrative_correlator import narrative_correlator, NarrativeCorrelator
from app.services.multichain_scorer import multichain_scorer, MultiChainScorer


client = TestClient(app)

# =====================================================================
# 1. Narrative Correlator Tests
# =====================================================================
def test_narrative_correlator_sector_extraction():
    sectors = narrative_correlator.extract_sectors_from_text("DeepSeek releases open weights AI agent")
    assert "AI" in sectors

    animal_sectors = narrative_correlator.extract_sectors_from_text("New cute dog meme inspired by wif")
    assert "ANIMALS" in animal_sectors


def test_narrative_correlator_freshness_decay():
    now = datetime.now(timezone.utc)
    fresh_news = [{
        "title": "Exciting AI Agent Launch with DEEPSEEK token",
        "summary": "AI agents taking over DeFi",
        "source_name": "theblock",
        "sentiment_score": Decimal("0.8"),
        "published_at": (now - timedelta(hours=1)).isoformat()
    }]
    stale_news = [{
        "title": "Exciting AI Agent Launch with DEEPSEEK token",
        "summary": "AI agents taking over DeFi",
        "source_name": "theblock",
        "sentiment_score": Decimal("0.8"),
        "published_at": (now - timedelta(days=7)).isoformat() # 7 days old
    }]

    fresh_res = narrative_correlator.calculate_narrative_score("DEEPSEEK", "DeepSeek Meme", ["AI"], fresh_news, now=now)
    stale_res = narrative_correlator.calculate_narrative_score("DEEPSEEK", "DeepSeek Meme", ["AI"], stale_news, now=now)

    # Fresh news should have higher narrative score due to decay
    assert fresh_res["narrative_score"] > stale_res["narrative_score"]
    assert fresh_res["direct_mentions_count"] == 1


# =====================================================================
# 2. Market Momentum & Chain Calibration Tests
# =====================================================================
def test_market_momentum_chain_calibration():
    # $30,000 liquidity is solid on Solana, but below the $50,000 threshold on Ethereum
    sol_score = multichain_scorer.evaluate_market_momentum(
        chain_id="solana:mainnet",
        liquidity_usd=Decimal("30000"),
        volume_5m_usd=Decimal("5000"),
        volume_1h_usd=Decimal("20000"),
        tx_count_5m=20,
        price_change_5m_pct=Decimal("10.0")
    )
    eth_score = multichain_scorer.evaluate_market_momentum(
        chain_id="eip155:1",
        liquidity_usd=Decimal("30000"),
        volume_5m_usd=Decimal("5000"),
        volume_1h_usd=Decimal("20000"),
        tx_count_5m=20,
        price_change_5m_pct=Decimal("10.0")
    )
    assert sol_score > eth_score


# =====================================================================
# 3. Security Hard Rejection Invariant Tests
# =====================================================================
def test_hard_reject_on_honeypot():
    res = multichain_scorer.compute_composite_opportunity_score(
        chain_id="eip155:1",
        asset_address="0xHoneypot1",
        liquidity_usd=Decimal("100000"),
        volume_5m_usd=Decimal("10000"),
        volume_1h_usd=Decimal("50000"),
        tx_count_5m=30,
        price_change_5m_pct=Decimal("5.0"),
        is_honeypot=True, # Honeypot trap!
        is_mint_revoked=TriState.TRUE,
        lp_status="BURNED"
    )
    assert res.hard_reject_triggered is True
    assert res.composite_score is None
    assert any("HONEYPOT_DETECTED" in r for r in res.rejection_reasons)


def test_hard_reject_on_unrenounced_mint():
    res = multichain_scorer.compute_composite_opportunity_score(
        chain_id="solana:mainnet",
        asset_address="SolMint1",
        liquidity_usd=Decimal("50000"),
        volume_5m_usd=Decimal("5000"),
        volume_1h_usd=Decimal("20000"),
        tx_count_5m=15,
        price_change_5m_pct=Decimal("4.0"),
        is_mint_revoked=TriState.FALSE, # Unrenounced mint
        lp_status="BURNED"
    )
    assert res.hard_reject_triggered is True
    assert res.composite_score is None
    assert any("UNRENOUNCED_MINT_AUTHORITY" in r for r in res.rejection_reasons)


def test_hard_reject_on_unlocked_liquidity():
    res = multichain_scorer.compute_composite_opportunity_score(
        chain_id="eip155:8453",
        asset_address="0xRugLP",
        liquidity_usd=Decimal("60000"),
        volume_5m_usd=Decimal("5000"),
        volume_1h_usd=Decimal("20000"),
        tx_count_5m=15,
        price_change_5m_pct=Decimal("4.0"),
        is_mint_revoked=TriState.TRUE,
        lp_status="UNLOCKED" # Dev can pull LP
    )
    assert res.hard_reject_triggered is True
    assert any("UNLOCKED_LIQUIDITY" in r for r in res.rejection_reasons)


def test_hard_reject_on_excessive_sell_tax():
    res = multichain_scorer.compute_composite_opportunity_score(
        chain_id="eip155:56",
        asset_address="0xHighTax",
        liquidity_usd=Decimal("60000"),
        volume_5m_usd=Decimal("5000"),
        volume_1h_usd=Decimal("20000"),
        tx_count_5m=15,
        price_change_5m_pct=Decimal("4.0"),
        sell_tax_bps=2000, # 20% sell tax
        is_mint_revoked=TriState.TRUE,
        lp_status="BURNED"
    )
    assert res.hard_reject_triggered is True
    assert any("EXCESSIVE_SELL_TAX" in r for r in res.rejection_reasons)


# =====================================================================
# 4. Wallet Intelligence & Penalties
# =====================================================================
def test_wallet_intelligence_penalties():
    # Clean wallet profile
    clean_score = multichain_scorer.evaluate_wallet_intelligence(
        top10_concentration_pct=Decimal("12.0"),
        bundler_cluster_detected=False,
        is_mixer_funded=False,
        prior_deployments_count=0
    )
    # Dirty wallet profile (Tornado funded + bundler cluster)
    dirty_score = multichain_scorer.evaluate_wallet_intelligence(
        top10_concentration_pct=Decimal("45.0"),
        bundler_cluster_detected=True,
        is_mixer_funded=True,
        prior_deployments_count=4
    )
    assert clean_score == Decimal("70.0")
    assert dirty_score == Decimal("0.0")


# =====================================================================
# 5. Composite Scoring & API Tests
# =====================================================================
def test_composite_opportunity_score_safe_asset():
    res = multichain_scorer.compute_composite_opportunity_score(
        chain_id="eip155:42161",
        asset_address="0xSafeArbToken",
        liquidity_usd=Decimal("150000"),
        volume_5m_usd=Decimal("12000"),
        volume_1h_usd=Decimal("60000"),
        tx_count_5m=35,
        price_change_5m_pct=Decimal("12.0"),
        is_mint_revoked=TriState.TRUE,
        is_freeze_revoked=TriState.TRUE,
        lp_status="LOCKED",
        sell_tax_bps=100,
        top10_concentration_pct=Decimal("14.0"),
        bundler_cluster_detected=False,
        token_symbol="ARBAPE",
        token_name="Arbitrum Ape",
        token_sectors=["ANIMALS"]
    )
    assert res.hard_reject_triggered is False
    assert res.composite_score is not None
    assert res.composite_score > Decimal("75.0")
    assert res.evidence_status == EvidenceStatus.VERIFIED


def test_api_multichain_opportunity_score_endpoint():
    response = client.get("/api/v1/scoring/opportunity/eip155:8453/0x4ed4e862860bed51a9570b96d89af5e1b0efefed")
    assert response.status_code == 200
    data = response.json()
    assert data["mint_address"] == "0x4ed4e862860bed51a9570b96d89af5e1b0efefed"
    assert data["composite_score"] is not None
    assert data["hard_reject_triggered"] is False
    assert "market_momentum_score" in data
    assert "security_safety_score" in data
