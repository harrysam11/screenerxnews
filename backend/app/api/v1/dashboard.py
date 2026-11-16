from typing import Dict, Any, List, Optional
from decimal import Decimal
from fastapi import APIRouter, Query
from app.services.dashboard_service import dashboard_service

router = APIRouter(prefix="/dashboard", tags=["Analyst Dashboard"])

@router.get("/overview")
async def get_dashboard_overview() -> Dict[str, Any]:
    """
    Returns verified multi-chain command center KPIs from SQLite:
    balances, realized PnL, open positions, order/fill counts, and system status.
    """
    return dashboard_service.get_overview_kpis()

@router.get("/screener")
async def get_dashboard_screener(
    chain_id: Optional[str] = Query(None, description="Filter by CAIP-2 chain_id"),
    min_liquidity: Optional[Decimal] = Query(None, description="Minimum pool liquidity USD"),
    min_score: Optional[Decimal] = Query(None, description="Minimum composite opportunity score (0-100)"),
    sector: Optional[str] = Query(None, description="Filter by thematic sector (e.g. AI, ANIMALS, MEME)"),
    verification_status: Optional[str] = Query(None, description="Filter by decimal verification status"),
    search: Optional[str] = Query(None, description="Search by symbol or asset address"),
    mode: Optional[str] = Query(None, description="'live' for public DEX observations, or 'candidates' for SQLite research scores"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0)
) -> Dict[str, Any]:
    """
    Multi-chain token discovery screener.
    When mode='live', queries normalized DEX market observations.
    Otherwise queries verified SQLite research candidates.
    """
    if mode == "live":
        from app.services.market_data_service import market_data_coordinator
        chain_arg = None
        if chain_id:
            if "solana" in chain_id.lower():
                chain_arg = "solana"
            elif "8453" in chain_id or "base" in chain_id.lower():
                chain_arg = "base"
        screener_res = await market_data_coordinator.query_screener(
            chain=chain_arg,
            limit=min(limit, 100),
            offset=offset,
            min_liquidity=min_liquidity,
            search=search
        )
        return screener_res.model_dump()

    return dashboard_service.get_screener_feed(
        chain_id=chain_id,
        min_liquidity=min_liquidity,
        min_score=min_score,
        sector=sector,
        verification_status=verification_status,
        search=search,
        limit=limit,
        offset=offset
    )


@router.get("/chains")
async def get_chains_status() -> List[Dict[str, Any]]:
    """
    Live operational capability matrix across all 12 supported blockchain networks.
    """
    return dashboard_service.get_chains_matrix()

@router.get("/forensics")
async def get_forensics_status() -> Dict[str, Any]:
    """
    Returns on-chain forensic checks, honeypot detection results, and security findings.
    """
    return dashboard_service.get_forensics_overview()

@router.get("/risk-monitor")
async def get_risk_monitor_status() -> Dict[str, Any]:
    """
    Returns risk limits, position concentrations, order rejection reasons, and circuit breaker status.
    """
    return dashboard_service.get_risk_monitor()

@router.get("/workflows")
async def get_workflows_status() -> Dict[str, Any]:
    """
    Returns runtime and configuration status for all 7 Windmill research workflows.
    """
    return dashboard_service.get_workflow_monitor()

@router.get("/strategy-analytics")
async def get_strategy_analytics_status() -> Dict[str, Any]:
    """
    Returns registered strategies, configuration schemas, parameters, and closed-trade performance metrics.
    """
    return dashboard_service.get_strategy_analytics()

@router.get("/reports")
async def get_dashboard_reports() -> Dict[str, Any]:
    """
    Returns daily/weekly paper trading reports, fills journal, and performance summaries.
    """
    return dashboard_service.get_reports()

@router.get("/narratives")
async def get_dashboard_narratives() -> Dict[str, Any]:
    """
    Trending narrative sectors, sentiment scores, and breaking news headlines.
    """
    return dashboard_service.get_narratives_overview()

@router.get("/terminals")
async def get_dashboard_trading_terminals(
    chain_id: Optional[str] = Query(None, description="Optional CAIP-2 chain identifier for context handoff"),
    asset_address: Optional[str] = Query(None, description="Optional asset/mint address for context handoff")
) -> Dict[str, Any]:
    """
    Returns verified memecoin trading terminal capability matrix (Axiom, Photon, Jupiter, GMGN, etc.)
    and the strictly disabled Future Live Trading Gateway architecture status.
    """
    return dashboard_service.get_trading_terminals(chain_id=chain_id, asset_address=asset_address)
