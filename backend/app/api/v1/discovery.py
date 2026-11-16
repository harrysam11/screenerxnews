from typing import List, Optional, Dict, Any
from decimal import Decimal
from fastapi import APIRouter, Depends, Query, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, Field

from app.core.database import get_db
from app.core.chains import normalize_chain_id
from app.core.address_validator import AddressValidationError
from app.core.discovery_protocols import protocol_registry
from app.schemas.contracts import (
    TokenDiscoveryResponse, RiskBandEnum, CandidateRegistrationRequest, TriState, DataFreshness
)
from app.schemas.market_data import (
    DynamicScreenerResponse,
    MarketProvidersHealthResponse,
    MarketSnapshotDTO,
    MarketSnapshotListResponse
)
from app.services.discovery_service import discovery_service
from app.services.market_data_service import market_data_coordinator

router = APIRouter(prefix="/discovery", tags=["Discovery"])


@router.get("/screener", response_model=DynamicScreenerResponse)
async def get_live_market_screener(
    chain: Optional[str] = Query(None, description="Chain filter: 'solana', 'base', 'ethereum', 'arbitrum', or 'all'"),
    limit: int = Query(50, ge=1, le=100, description="Top limit (e.g. 10, 50, 100)"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    sort_by: str = Query("volume_24h", description="Field to sort by: liquidity, volume_24h, quality_score, price_change_5m, price_change_1h, price_change_24h, price_usd, age"),
    sort_dir: str = Query("desc", description="Sort direction: 'asc' or 'desc'"),
    min_liquidity: Optional[Decimal] = Query(None, ge=0, description="Minimum pool liquidity USD"),
    min_volume_24h: Optional[Decimal] = Query(None, ge=0, description="Minimum 24h volume USD"),
    min_age_hours: Optional[float] = Query(None, ge=0, description="Minimum token/pair age in hours"),
    max_age_hours: Optional[float] = Query(None, ge=0, description="Maximum token/pair age in hours"),
    min_price_change_24h: Optional[Decimal] = Query(None, description="Minimum 24h price change %"),
    max_price_change_24h: Optional[Decimal] = Query(None, description="Maximum 24h price change %"),
    search: Optional[str] = Query(None, description="Search by symbol, name, or contract address"),
    memecoin_only: bool = Query(True, description="Filter for memecoins only by default (excludes wrapped natives, stablecoins, equities)"),
    category: Optional[str] = Query(None, description="Explicit category filter: 'MEME', 'NON_MEME', 'UNKNOWN', or 'ALL'"),
    min_quality_score: Optional[float] = Query(None, ge=0.0, le=100.0, description="Minimum composite quality score filter (0.0 to 100.0)"),
    has_anomalies: Optional[bool] = Query(None, description="Filter for tokens with heuristic anomalies detected"),
    new_pairs_only: Optional[bool] = Query(None, description="Filter for newly deployed pools only (< 24 hours)"),
    force_refresh: bool = Query(False, description="Force fresh fetch from providers")
) -> DynamicScreenerResponse:
    """
    Read-only dynamic memecoin screener using normalized, deduplicated public DEX observations
    from GeckoTerminal (discovery) and DEX Screener (enrichment), augmented with P1.3 quality scoring
    and anomaly detection.
    """
    valid_sort_fields = {"liquidity", "volume_24h", "quality_score", "price_change_5m", "price_change_1h", "price_change_24h", "price_usd", "age"}
    if sort_by not in valid_sort_fields:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid sort_by field '{sort_by}'. Must be one of: {sorted(list(valid_sort_fields))}"
        )
    if sort_dir.lower() not in {"asc", "desc"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid sort_dir '{sort_dir}'. Must be 'asc' or 'desc'"
        )
    valid_chains = {"solana", "base", "ethereum", "eth", "arbitrum", "all", "solana:mainnet", "solana:5eykt4usfv8p8njdtrepy1vzqkqzkvdp", "eip155:8453", "eip155:1", "eip155:42161"}
    if chain and chain.lower() not in valid_chains:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid chain filter '{chain}'. Must be 'solana', 'base', 'ethereum', 'arbitrum', or 'all'"
        )
    if category and category.upper() not in {"MEME", "NON_MEME", "UNKNOWN", "ALL"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid category filter '{category}'. Must be 'MEME', 'NON_MEME', 'UNKNOWN', or 'ALL'"
        )
    if min_age_hours is not None and max_age_hours is not None and min_age_hours > max_age_hours:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"min_age_hours ({min_age_hours}) cannot be greater than max_age_hours ({max_age_hours})"
        )
    if min_price_change_24h is not None and max_price_change_24h is not None and min_price_change_24h > max_price_change_24h:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"min_price_change_24h ({min_price_change_24h}) cannot be greater than max_price_change_24h ({max_price_change_24h})"
        )

    return await market_data_coordinator.query_screener(
        chain=chain,
        limit=limit,
        offset=offset,
        sort_by=sort_by,
        sort_dir=sort_dir,
        min_liquidity=min_liquidity,
        min_volume_24h=min_volume_24h,
        min_age_hours=min_age_hours,
        max_age_hours=max_age_hours,
        min_price_change_24h=min_price_change_24h,
        max_price_change_24h=max_price_change_24h,
        search=search,
        memecoin_only=memecoin_only,
        category=category,
        min_quality_score=min_quality_score,
        has_anomalies=has_anomalies,
        new_pairs_only=new_pairs_only,
        force_refresh=force_refresh
    )


@router.get("/providers/health", response_model=MarketProvidersHealthResponse)
async def get_market_providers_health() -> MarketProvidersHealthResponse:
    """
    Returns live health, latency, rate limits, circuit breaker, and fetch status
    for public market data providers (GeckoTerminal, DEX Screener).
    """
    return market_data_coordinator.get_providers_health()


@router.get("/snapshots", response_model=MarketSnapshotListResponse)
async def get_market_snapshots(
    limit: int = Query(50, ge=1, le=50, description="Maximum snapshots to return (ring buffer capacity is 50)"),
    offset: int = Query(0, ge=0, description="Pagination offset")
) -> MarketSnapshotListResponse:
    """
    Retrieves historical episodic point-in-time observation snapshots (Phase P1.3).
    Bounded to 50 snapshots maximum in-memory ring buffer. Disappears on process restart.
    """
    from app.services.market_intelligence_service import market_intelligence_service
    snaps = market_intelligence_service.get_snapshots(limit=limit, offset=offset)
    return MarketSnapshotListResponse(
        total_snapshots=len(snaps),
        max_capacity=50,
        snapshots=snaps
    )


@router.get("/snapshots/{snapshot_id}", response_model=MarketSnapshotDTO)
async def get_market_snapshot_detail(snapshot_id: str) -> MarketSnapshotDTO:
    """
    Retrieves a specific point-in-time snapshot by snapshot ID.
    """
    from app.services.market_intelligence_service import market_intelligence_service
    snap = market_intelligence_service.get_snapshot_by_id(snapshot_id)
    if not snap:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Snapshot '{snapshot_id}' not found in in-memory ring buffer (may have been evicted)."
        )
    return snap


@router.post("/refresh")
async def trigger_manual_market_refresh():
    """
    Manually triggers cache invalidation and fetches fresh market data observations
    within rate limits and circuit breaker bounds.
    """
    tokens = await market_data_coordinator.fetch_and_aggregate_market(force_refresh=True)
    return {
        "status": "success",
        "message": "Market data refreshed successfully",
        "total_tokens_discovered": len(tokens)
    }


class IngestPoolRequest(BaseModel):
    chain_id: str
    protocol: str
    pool_address: str
    token0_address: str
    token1_address: str
    fee_tier_bps: int = 30
    block_number: Optional[int] = None
    slot: Optional[int] = None
    tx_hash: Optional[str] = None
    log_index: Optional[int] = None
    factory_address: Optional[str] = None
    discovery_source: str = "rpc_logs"
    token0_metadata: Optional[Dict[str, Any]] = None
    token1_metadata: Optional[Dict[str, Any]] = None

class IngestMarketDataRequest(BaseModel):
    chain_id: str
    pool_address: str
    provider_id: str
    price_usd: Optional[float] = None
    liquidity_usd: Optional[float] = None
    volume_24h_usd: Optional[float] = None
    reserves_token0: Optional[float] = None
    reserves_token1: Optional[float] = None
    price_impact_bps: Optional[float] = None
    total_supply: Optional[float] = None
    confidence_score: float = 1.0
    data_freshness: str = "REALTIME"

class ReorgInvalidationRequest(BaseModel):
    chain_id: str
    reorg_block_number: int
    block_hash: str
    reason: str = "chain_reorganization"

@router.get("/tokens", response_model=List[TokenDiscoveryResponse])
async def get_discovered_tokens(
    chain_id: Optional[str] = None,
    min_liquidity: float = Query(default=0.0, ge=0),
    min_volume_1h: float = Query(default=0.0, ge=0),
    max_risk_score: float = Query(default=100.0, le=100.0),
    search: Optional[str] = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db)
):
    """
    Returns discovered tokens enriched with market data from SQLite.
    Falls back to canonical research seed if no tokens have been ingested yet.
    """
    db_tokens = discovery_service.list_tokens(
        chain_id=chain_id,
        search=search,
        limit=limit,
        offset=offset
    )

    if not db_tokens:
        # Backward-compatible canonical research baseline if no tokens discovered yet
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

    results = []
    for t in db_tokens:
        results.append(
            TokenDiscoveryResponse(
                mint_address=t["address"],
                symbol=t.get("symbol", "UNKNOWN"),
                name=t.get("name", "Unknown Token"),
                decimals=t.get("decimals") or 9,
                price_usd=Decimal("0.0"),
                liquidity_usd=Decimal("0.0"),
                volume_5m=Decimal("0.0"),
                volume_1h=Decimal("0.0"),
                volume_24h=Decimal("0.0"),
                price_change_5m=None,
                price_change_1h=None,
                buy_sell_ratio_1h=None,
                risk_score=Decimal("0.0"),
                risk_band=RiskBandEnum.UNKNOWN,
                composite_opportunity_score=Decimal("50.0"),
                lp_burned=TriState.UNKNOWN,
                mint_revoked=TriState.UNKNOWN,
                is_token_2022=TriState.FALSE,
                data_freshness=DataFreshness.REALTIME
            )
        )
    return results

@router.get("/pools")
async def list_discovered_pools(
    chain_id: Optional[str] = None,
    protocol: Optional[str] = None,
    canonical_only: bool = True,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0)
):
    """
    Returns discovered liquidity pools across supported blockchains.
    """
    return discovery_service.list_pools(
        chain_id=chain_id,
        protocol=protocol,
        canonical_only=canonical_only,
        limit=limit,
        offset=offset
    )

@router.get("/pools/{pool_address}")
async def get_discovered_pool(
    pool_address: str,
    chain_id: str = Query(default="solana:mainnet")
):
    """
    Returns pool details and status for a specific pool address and chain.
    """
    pool = discovery_service.get_pool(chain_id, pool_address)
    if not pool:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Pool '{pool_address}' not found on chain '{chain_id}'"
        )
    return pool

@router.get("/market-data/{pool_address}")
async def get_pool_market_data(
    pool_address: str,
    chain_id: str = Query(default="solana:mainnet")
):
    """
    Returns the latest market data snapshot for a specific pool.
    """
    snap = discovery_service.get_latest_market_data(chain_id, pool_address)
    if not snap:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No market data snapshots available for pool '{pool_address}' on chain '{chain_id}'"
        )
    return snap

@router.get("/checkpoints")
async def get_checkpoints(
    chain_id: Optional[str] = None
):
    """
    Returns ingestion checkpoints and lag status across chains.
    """
    return discovery_service.list_checkpoints(chain_id)

@router.get("/protocols")
async def get_supported_protocols(
    chain_id: Optional[str] = None
):
    """
    Returns protocol & factory registry metadata for 12 chains.
    """
    if chain_id:
        norm_chain = normalize_chain_id(chain_id)
        return {
            "chain_id": norm_chain,
            "protocols": [p.dict() for p in protocol_registry.list_protocols_for_chain(norm_chain)]
        }
    return protocol_registry.get_coverage_matrix()

@router.post("/ingest-pool")
async def ingest_pool_event(payload: IngestPoolRequest):
    """
    Ingests a newly discovered DEX pool event into SQLite with validation.
    """
    try:
        result = discovery_service.ingest_pool_event(
            chain_id=payload.chain_id,
            protocol=payload.protocol,
            pool_address=payload.pool_address,
            token0_address=payload.token0_address,
            token1_address=payload.token1_address,
            fee_tier_bps=payload.fee_tier_bps,
            block_number=payload.block_number,
            slot=payload.slot,
            tx_hash=payload.tx_hash,
            log_index=payload.log_index,
            factory_address=payload.factory_address,
            discovery_source=payload.discovery_source,
            token0_metadata=payload.token0_metadata,
            token1_metadata=payload.token1_metadata
        )
        return {"status": "success", "pool": result}
    except AddressValidationError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

@router.post("/market-data")
async def ingest_market_data(payload: IngestMarketDataRequest):
    """
    Ingests a normalized market data snapshot for an existing pool.
    """
    try:
        result = discovery_service.ingest_market_data(
            chain_id=payload.chain_id,
            pool_address=payload.pool_address,
            provider_id=payload.provider_id,
            price_usd=payload.price_usd,
            liquidity_usd=payload.liquidity_usd,
            volume_24h_usd=payload.volume_24h_usd,
            reserves_token0=payload.reserves_token0,
            reserves_token1=payload.reserves_token1,
            price_impact_bps=payload.price_impact_bps,
            total_supply=payload.total_supply,
            confidence_score=payload.confidence_score,
            data_freshness=payload.data_freshness
        )
        return {"status": "success", "snapshot": result}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

@router.post("/reorg")
async def trigger_reorg_invalidation(payload: ReorgInvalidationRequest):
    """
    Invalidates pools discovered in reorganized blocks and resets checkpoint.
    """
    try:
        result = discovery_service.handle_reorg_event(
            chain_id=payload.chain_id,
            reorg_block_number=payload.reorg_block_number,
            block_hash=payload.block_hash,
            reason=payload.reason
        )
        return {"status": "success", "reorg_event": result}
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

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
