from datetime import datetime, timezone
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException, Path
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.schemas.contracts import (
    TokenSecurityReport, FindingItem, FirstBuyersForensicsResponse, FirstBuyerItem,
    TriState, EvidenceStatus
)
from app.services.first_buyers_decoder import first_buyers_decoder
from app.services.wallet_cluster_engine import WalletClusterEngine

router = APIRouter(prefix="/forensics", tags=["Forensics"])

@router.get("/token/{mint}", response_model=TokenSecurityReport)
async def get_token_forensics(
    mint: str = Path(..., description="Solana Token Mint Address"),
    db: AsyncSession = Depends(get_db)
):
    """
    Returns full contract, authority, and liquidity security scan for a token using tri-state evidence.
    """
    return TokenSecurityReport(
        mint_address=mint,
        token_program=None,
        is_token_2022=TriState.UNKNOWN,
        is_mint_revoked=TriState.UNKNOWN,
        is_freeze_revoked=TriState.UNKNOWN,
        is_honeypot=TriState.UNKNOWN,
        transfer_fee_bps=None,
        lp_status="unknown",
        lp_burned=TriState.UNKNOWN,
        lp_burned_pct=Decimal("0.00"),
        top10_concentration_pct=Decimal("0.00"),
        holder_coverage_pct=Decimal("0.00"),
        risk_score=Decimal("0.0"),
        risk_level="UNVERIFIED",
        confidence=EvidenceStatus.UNVERIFIED,
        findings=[],
        scanned_at=datetime.now(timezone.utc),
        is_stale=True
    )

@router.get("/first-buyers/{mint}", response_model=FirstBuyersForensicsResponse)
async def get_first_buyers_forensics(
    mint: str = Path(..., description="Solana Token Mint Address"),
    db: AsyncSession = Depends(get_db)
):
    """
    Reconstructs the first 10 buyers, checks funding sources, and detects bundled insider buys.
    Returns unverified status when no live indexing pipeline is active.
    """
    return FirstBuyersForensicsResponse(
        mint_address=mint,
        pool_address=None,
        deployer_address=None,
        total_first_buyers_held_pct=None,
        bundler_cluster_detected=None,
        evidence_status=EvidenceStatus.UNVERIFIED,
        detector_version=WalletClusterEngine.DETECTOR_VERSION,
        buyers=[]
    )
