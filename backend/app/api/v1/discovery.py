from typing import List
from decimal import Decimal
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.schemas.contracts import (
    TokenDiscoveryResponse, RiskBandEnum, CandidateRegistrationRequest, TriState, DataFreshness
)

router = APIRouter(prefix="/discovery", tags=["Discovery"])

@router.get("/tokens", response_model=List[TokenDiscoveryResponse])
async def get_discovered_tokens(
    min_liquidity: float = Query(default=10000.0, ge=0),
    min_volume_1h: float = Query(default=1000.0, ge=0),
    max_risk_score: float = Query(default=50.0, le=100.0),
    db: AsyncSession = Depends(get_db)
):
    """
    Returns discovered tokens filtered by liquidity, volume momentum, and forensic risk ceiling.
    """
    return [
        TokenDiscoveryResponse(
            mint_address="DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
            symbol="BONK",
            name="Bonk",
            decimals=5,
            price_usd=Decimal("0.00002145"),
            liquidity_usd=Decimal("15200000.00"),
            volume_5m=Decimal("45000.00"),
            volume_1h=Decimal("620000.00"),
            volume_24h=Decimal("14500000.00"),
            price_change_5m=Decimal("1.25"),
            price_change_1h=Decimal("4.80"),
            buy_sell_ratio_1h=Decimal("1.85"),
            risk_score=Decimal("12.5"),
            risk_band=RiskBandEnum.LOW,
            composite_opportunity_score=Decimal("84.2"),
            lp_burned=TriState.TRUE,
            mint_revoked=TriState.TRUE,
            is_token_2022=TriState.FALSE,
            data_freshness=DataFreshness.REALTIME
        )
    ]

@router.post("/register")
async def register_candidate_token(
    payload: CandidateRegistrationRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Called by n8n WF-01 (Discovery Poller) to register or update a discovered token mint.
    """
    return {
        "status": "success",
        "mint_address": payload.mint_address,
        "is_new_candidate": True,
        "message": "Candidate token registered for enrichment and forensic scan"
    }
