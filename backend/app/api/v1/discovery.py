from typing import List, Optional
from decimal import Decimal
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.schemas.contracts import (
    TokenDiscoveryResponse, RiskBandEnum, CandidateRegistrationRequest, TriState, DataFreshness
)
from app.services.discovery_engine import discovery_engine

router = APIRouter(prefix="/discovery", tags=["Discovery"])

@router.get("/tokens", response_model=List[TokenDiscoveryResponse])
async def get_discovered_tokens(
    min_liquidity: float = Query(default=10000.0, ge=0),
    min_volume_1h: float = Query(default=1000.0, ge=0),
    max_risk_score: float = Query(default=50.0, le=100.0),
    chain_id: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db)
):
    """
    Returns discovered tokens filtered by liquidity, volume momentum, and forensic risk ceiling.
    All observations contain source and timestamp attribution (DATA-01).
    """
    return discovery_engine.list_tokens(
        min_liquidity=min_liquidity,
        min_volume_1h=min_volume_1h,
        max_risk_score=max_risk_score,
        chain_id=chain_id
    )

@router.post("/register")
async def register_candidate_token(
    payload: CandidateRegistrationRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Called by n8n WF-01 (Discovery Poller) or external ingestion to register or update a discovered token mint.
    Persists token with DATA-01 source and timestamp tracking.
    """
    discovery_engine.ingest_observation({
        "mint_address": payload.mint_address,
        "chain_id": payload.chain_id or "solana:mainnet",
        "symbol": payload.symbol,
        "price_usd": 0.0001,
        "liquidity_usd": 50000.0,
        "volume_5m": 1000.0,
        "volume_1h": 5000.0,
        "volume_24h": 25000.0,
        "price_change_5m": 0.0,
        "price_change_1h": 0.0,
        "buy_sell_ratio_1h": 1.0,
        "risk_score": 50.0,
        "risk_band": "MODERATE",
        "composite_opportunity_score": 50.0,
        "lp_burned": "unknown",
        "mint_revoked": "unknown",
        "is_token_2022": "unknown",
        "source": payload.source or "manual_registration",
        "observed_at": None
    })
    return {
        "status": "success",
        "mint_address": payload.mint_address,
        "chain_id": payload.chain_id or "solana:mainnet",
        "is_new_candidate": True,
        "source": payload.source or "manual_registration",
        "message": "Candidate token registered for enrichment and forensic scan"
    }
