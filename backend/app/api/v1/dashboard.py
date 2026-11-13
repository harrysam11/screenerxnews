from typing import Dict, Any, List, Optional
from decimal import Decimal
from fastapi import APIRouter, Query
from app.services.dashboard_service import dashboard_service

router = APIRouter(prefix="/dashboard", tags=["Analyst Dashboard"])

@router.get("/overview")
async def get_dashboard_overview() -> Dict[str, Any]:
    """
    Returns multi-chain command center KPIs: tracked tokens, scams blocked, and virtual paper equity.
    """
    return dashboard_service.get_overview_kpis()

@router.get("/screener")
async def get_dashboard_screener(
    chain_id: Optional[str] = Query(None, description="Filter by CAIP-2 chain_id"),
    min_liquidity: Optional[Decimal] = Query(None, description="Minimum pool liquidity USD"),
    min_score: Optional[Decimal] = Query(None, description="Minimum composite opportunity score (0-100)"),
    sector: Optional[str] = Query(None, description="Filter by thematic sector (e.g. AI, ANIMALS, MEME)"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0)
) -> Dict[str, Any]:
    """
    Multi-chain filtered token discovery screener.
    """
    return dashboard_service.get_screener_feed(
        chain_id=chain_id,
        min_liquidity=min_liquidity,
        min_score=min_score,
        sector=sector,
        limit=limit,
        offset=offset
    )

@router.get("/chains")
async def get_chains_status() -> List[Dict[str, Any]]:
    """
    Live operational status matrix across all 12 supported blockchain networks.
    """
    return dashboard_service.get_chains_matrix()

@router.get("/narratives")
async def get_dashboard_narratives() -> Dict[str, Any]:
    """
    Trending narrative sectors, sentiment scores, and breaking news headlines.
    """
    return dashboard_service.get_narratives_overview()
