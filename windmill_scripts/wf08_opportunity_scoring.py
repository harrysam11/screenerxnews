"""
summary: Multi-Chain Opportunity Scoring (WF-08 / WF-35)
description: Evaluates research candidate with explainable 4-component composite score, decimal TTL verification, and hard-reject security filters.
schema:
  type: object
  properties:
    chain_id:
      type: string
      default: eip155:8453
      description: Target CAIP-2 chain identifier
    asset_address:
      type: string
      default: 0x4ed4e862860bed51a9570b96d89af5e1b0efefed
      description: Asset contract or mint address to evaluate
"""
import os
import sys
from decimal import Decimal
from typing import Dict, Any, Optional

sys.path.insert(0, os.path.abspath("./backend"))
from app.services.candidate_scoring_service import candidate_research_scorer
from app.schemas.contracts import TriState

def score_token(
    chain_id: str,
    asset_address: str,
    liquidity_usd: Decimal = Decimal("50000"),
    volume_5m_usd: Decimal = Decimal("5000"),
    volume_1h_usd: Decimal = Decimal("25000"),
    tx_count_5m: int = 20,
    price_change_5m_pct: Decimal = Decimal("5.0"),
    is_honeypot: bool = False,
    sell_tax_bps: Optional[int] = 100,
    is_mint_revoked: Optional[TriState] = TriState.TRUE,
    is_freeze_revoked: Optional[TriState] = TriState.TRUE,
    lp_status: Optional[str] = "BURNED",
    top10_concentration_pct: Optional[Decimal] = Decimal("15.0"),
    bundler_cluster_detected: bool = False,
    is_mixer_funded: bool = False,
    token_symbol: str = "",
    token_name: str = ""
) -> Dict[str, Any]:
    """
    Evaluates candidate asset and persists score in candidate_research_scores.
    """
    res = candidate_research_scorer.score_candidate(
        chain_id=chain_id,
        asset_address=asset_address,
        symbol=token_symbol,
        name=token_name,
        liquidity_usd=liquidity_usd,
        volume_5m_usd=volume_5m_usd,
        volume_1h_usd=volume_1h_usd,
        tx_count_5m=tx_count_5m,
        price_change_5m_pct=price_change_5m_pct,
        is_honeypot=is_honeypot,
        sell_tax_bps=sell_tax_bps,
        is_mint_revoked=is_mint_revoked,
        is_freeze_revoked=is_freeze_revoked,
        lp_status=lp_status,
        top10_concentration_pct=top10_concentration_pct,
        bundler_cluster_detected=bundler_cluster_detected,
        is_mixer_funded=is_mixer_funded
    )

    should_alert = (
        not res["hard_reject_triggered"] and
        res["composite_score"] is not None and
        res["composite_score"] >= 80.0
    )

    return {
        "status": "SUCCESS",
        "score_id": res["score_id"],
        "chain_id": res["chain_id"],
        "asset_address": res["asset_address"],
        "composite_score": res["composite_score"],
        "confidence": res["confidence"],
        "hard_reject_triggered": res["hard_reject_triggered"],
        "data_freshness_status": res["data_freshness_status"],
        "verification_status": res["verification_status"],
        "should_dispatch_alert": should_alert,
        "score_breakdown": res["score_breakdown"],
        "risk_findings": res["risk_findings"],
        "limitations": res["limitations"]
    }

def main(chain_id: str = "eip155:8453", asset_address: str = "0x4ed4e862860bed51a9570b96d89af5e1b0efefed") -> Dict[str, Any]:
    return score_token(
        chain_id=chain_id,
        asset_address=asset_address,
        liquidity_usd=Decimal("120000"),
        volume_5m_usd=Decimal("15000"),
        volume_1h_usd=Decimal("70000"),
        tx_count_5m=40,
        price_change_5m_pct=Decimal("12.0"),
        token_symbol="DEGEN",
        token_name="Degen"
    )

if __name__ == "__main__":
    import json
    print(json.dumps(main(), default=str))
