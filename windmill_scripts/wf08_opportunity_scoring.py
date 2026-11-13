"""
WF-08 / WF-35: Multi-Chain Opportunity Scoring (Windmill Pipeline Worker)
Calculates explainable 4-component composite opportunity score.
"""
import os
import sys
from decimal import Decimal
from typing import Dict, Any, Optional

sys.path.insert(0, os.path.abspath("./backend"))
from app.services.multichain_scorer import multichain_scorer
from app.schemas.contracts import TriState

def score_token(
    chain_id: str,
    asset_address: str,
    liquidity_usd: Decimal,
    volume_5m_usd: Decimal,
    volume_1h_usd: Decimal,
    tx_count_5m: int,
    price_change_5m_pct: Decimal,
    is_mint_revoked: TriState = TriState.TRUE,
    is_freeze_revoked: TriState = TriState.TRUE,
    lp_status: str = "BURNED",
    sell_tax_bps: int = 100,
    top10_concentration_pct: Optional[Decimal] = Decimal("15.0"),
    bundler_cluster_detected: bool = False,
    is_mixer_funded: bool = False,
    token_symbol: str = "",
    token_name: str = ""
) -> Dict[str, Any]:
    breakdown = multichain_scorer.compute_composite_opportunity_score(
        chain_id=chain_id,
        asset_address=asset_address,
        liquidity_usd=liquidity_usd,
        volume_5m_usd=volume_5m_usd,
        volume_1h_usd=volume_1h_usd,
        tx_count_5m=tx_count_5m,
        price_change_5m_pct=price_change_5m_pct,
        is_mint_revoked=is_mint_revoked,
        is_freeze_revoked=is_freeze_revoked,
        lp_status=lp_status,
        sell_tax_bps=sell_tax_bps,
        top10_concentration_pct=top10_concentration_pct,
        bundler_cluster_detected=bundler_cluster_detected,
        is_mixer_funded=is_mixer_funded,
        token_symbol=token_symbol,
        token_name=token_name
    )

    should_alert = (
        not breakdown.hard_reject_triggered and
        breakdown.composite_score is not None and
        breakdown.composite_score >= Decimal("80.0")
    )

    return {
        "status": "SUCCESS",
        "chain_id": chain_id,
        "asset_address": asset_address,
        "composite_score": float(breakdown.composite_score) if breakdown.composite_score else None,
        "hard_reject_triggered": breakdown.hard_reject_triggered,
        "rejection_reasons": breakdown.rejection_reasons,
        "should_dispatch_alert": should_alert,
        "breakdown": breakdown.model_dump()
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
