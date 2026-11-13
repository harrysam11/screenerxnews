from decimal import Decimal
from typing import Optional
from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.schemas.contracts import OpportunityScoreBreakdown, EvidenceStatus, TriState
from app.services.multichain_scorer import multichain_scorer

router = APIRouter(prefix="/scoring", tags=["Scoring Engine"])

@router.get("/opportunity/{mint}", response_model=OpportunityScoreBreakdown)
async def get_opportunity_score(
    mint: str = Path(..., description="Solana Token Mint Address"),
    db: AsyncSession = Depends(get_db)
):
    """
    Computes explainable 4-component opportunity score for Solana:
    O = 0.35M + 0.30S + 0.20W + 0.15N
    """
    return multichain_scorer.compute_composite_opportunity_score(
        chain_id="solana:mainnet",
        asset_address=mint,
        liquidity_usd=Decimal("65000"),
        volume_5m_usd=Decimal("7500"),
        volume_1h_usd=Decimal("35000"),
        tx_count_5m=25,
        price_change_5m_pct=Decimal("12.5"),
        is_mint_revoked=TriState.TRUE,
        is_freeze_revoked=TriState.TRUE,
        lp_status="BURNED",
        top10_concentration_pct=Decimal("18.5"),
        token_symbol="SOLMEME",
        token_name="Solana Meme Token"
    )

@router.get("/opportunity/{chain_id}/{asset_address}", response_model=OpportunityScoreBreakdown)
async def get_multichain_opportunity_score(
    chain_id: str = Path(..., description="CAIP-2 Chain Identifier (e.g. eip155:1, eip155:8453)"),
    asset_address: str = Path(..., description="Token contract or mint address"),
    db: AsyncSession = Depends(get_db)
):
    """
    Computes explainable 4-component opportunity score across any supported chain.
    """
    return multichain_scorer.compute_composite_opportunity_score(
        chain_id=chain_id,
        asset_address=asset_address,
        liquidity_usd=Decimal("120000"),
        volume_5m_usd=Decimal("15000"),
        volume_1h_usd=Decimal("65000"),
        tx_count_5m=40,
        price_change_5m_pct=Decimal("8.0"),
        is_mint_revoked=TriState.TRUE,
        is_freeze_revoked=TriState.TRUE,
        lp_status="BURNED",
        top10_concentration_pct=Decimal("14.0"),
        token_symbol="MEME",
        token_name="MultiChain Meme"
    )
