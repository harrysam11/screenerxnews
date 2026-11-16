from fastapi import APIRouter, Query, HTTPException, status
from typing import Optional, List, Dict, Any
from decimal import Decimal
from app.services.research_analytics_service import research_analytics_service
from app.services.market_data_service import market_data_coordinator
from app.schemas.research_analytics import (
    HistoricalObservationListResponse,
    TokenMomentumMetricsDTO,
    MomentumLeaderboardResponse,
    CrossChainMarketSummaryResponse,
    WatchlistCreateRequest,
    WatchlistUpdateRequest,
    WatchlistResponse,
    WatchlistListResponse,
    StrategyFeatureVectorDTO,
    PruneAgedObservationsResponse
)
from app.schemas.ohlcv import (
    Timeframe,
    OHLCVCandleDTO,
    CandleListResponse,
    ProtectedCandleFetchRequest,
    ProtectedCandleFetchResponse,
    CandleBarStatus
)
from app.services.candle_integrity_service import candle_integrity_validator
from app.services.market_data_service import CAIP2_TO_GECKO_NETWORK
from app.schemas.indicators import (
    TechnicalIndicatorsResponseDTO,
    MarketStructureResponseDTO,
    INDICATOR_ENGINE_VERSION
)
from app.services.indicator_engine import indicator_engine
from app.services.volume_intelligence import volume_intelligence_service
from app.services.market_structure_service import market_structure_service
from app.strategy_sdk.schemas import (
    DeclarativeStrategyDTO,
    StrategyCreateRequest,
    StrategyUpdateRequest,
    StrategyStatusUpdateRequest,
    StrategyVersionSummaryDTO,
    StrategyEvaluateRequest,
    StrategyEvaluateRawRequest,
    StrategyEvaluationResponseDTO,
    StrategyValidationResultDTO
)
from app.strategy_sdk.registry import feature_registry
from app.strategy_sdk.evaluator import strategy_evaluator
from app.strategy_sdk.manager import strategy_manager
from app.strategy_sdk.ict import ict_engine
from app.schemas.backtesting import BacktestRunRequest, BacktestResultDTO
from app.services.backtesting_engine import backtesting_engine
from app.schemas.research_paper import (
    PaperAccountCreateRequest,
    StrategyPaperAccountDTO,
    StrategyPaperOrderDTO,
    StrategyPaperPositionDTO,
    StrategyPaperStepRequest,
    StrategyPaperStepResponse
)
from app.services.research_paper_engine import research_paper_engine

router = APIRouter(prefix="/research", tags=["Research Intelligence & Analytics"])


@router.get("/observations/{chain_id}/{token_address}", response_model=HistoricalObservationListResponse)
async def get_token_historical_observations(
    chain_id: str,
    token_address: str,
    limit: int = Query(50, ge=1, le=500, description="Maximum historical observations to return"),
    start_time: Optional[str] = Query(None, description="ISO UTC timestamp filter start"),
    end_time: Optional[str] = Query(None, description="ISO UTC timestamp filter end")
):
    """
    Retrieves immutable historical observation time series for an asset from research_history.db.
    """
    return research_analytics_service.get_token_history(
        chain_id=chain_id,
        token_address=token_address,
        limit=limit,
        start_time=start_time,
        end_time=end_time
    )


@router.get("/momentum", response_model=MomentumLeaderboardResponse)
async def get_momentum_leaderboards(
    chain_id: Optional[str] = Query(None, description="Optional chain identifier filter"),
    sort_by: str = Query("velocity_1h_pct", description="Momentum sort metric: velocity_5m_pct, velocity_1h_pct, velocity_24h_pct, volume_acceleration_ratio"),
    limit: int = Query(50, ge=1, le=100)
):
    """
    Returns momentum leaderboards ordered by chosen velocity metric.
    """
    valid_sorts = {"velocity_5m_pct", "velocity_1h_pct", "velocity_24h_pct", "volume_acceleration_ratio"}
    if sort_by not in valid_sorts:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid sort_by '{sort_by}'. Must be one of: {sorted(list(valid_sorts))}"
        )
    return research_analytics_service.get_momentum_leaderboard(
        chain_id=chain_id,
        sort_by=sort_by,
        limit=limit
    )


@router.get("/momentum/{chain_id}/{token_address}", response_model=TokenMomentumMetricsDTO)
async def get_token_momentum_detail(chain_id: str, token_address: str):
    """
    Retrieves derived velocity, volume acceleration, and liquidity migration direction for a token.
    """
    metrics = research_analytics_service.compute_and_update_momentum(chain_id, token_address)
    if not metrics:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No historical observations found for asset '{token_address}' on chain '{chain_id}'"
        )
    return metrics


@router.get("/cross-chain", response_model=CrossChainMarketSummaryResponse)
async def get_cross_chain_market_summary():
    """
    Returns comparative liquidity depth, volume share, and quality metrics across Solana, Base, Ethereum, and Arbitrum.
    """
    # Use coordinator's current tokens for immediate real-time comparison
    current_tokens = list(market_data_coordinator._tokens_by_key.values())
    return research_analytics_service.get_cross_chain_market_summary(current_tokens=current_tokens)


@router.get("/features/{chain_id}/{token_address}", response_model=StrategyFeatureVectorDTO)
async def get_strategy_feature_vector(
    chain_id: str,
    token_address: str,
    version: str = Query("v1.4_research_vector", description="Feature vector version identifier")
):
    """
    Generates a reproducible numeric feature vector for paper trading research.
    Read-only for consumers with zero live execution side effects.
    """
    # First search current screener tokens
    norm_key = (chain_id, token_address.lower())
    token = None
    for (cid, addr), t in market_data_coordinator._tokens_by_key.items():
        if (cid == chain_id or (cid in ("solana:mainnet", "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp") and chain_id in ("solana:mainnet", "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"))) and addr.lower() == token_address.lower():
            token = t
            break

    if not token:
        # Check historical store directly
        raw_feat = research_analytics_service.store.get_strategy_feature_vector(chain_id, token_address, version)
        if raw_feat:
            return StrategyFeatureVectorDTO(
                feature_id=raw_feat["feature_id"],
                chain_id=raw_feat["chain_id"],
                token_address=raw_feat["token_address"],
                symbol=raw_feat["symbol"],
                feature_version=raw_feat["feature_version"],
                vector=raw_feat["vector"],
                generated_at=raw_feat["generated_at"]
            )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Token '{token_address}' on chain '{chain_id}' not found in current screener or feature store."
        )

    return research_analytics_service.generate_strategy_feature_vector(token, feature_version=version)


# -----------------------------------------------------------------------------
# Watchlists Endpoints (Controlled CRUD)
# -----------------------------------------------------------------------------
@router.get("/watchlists", response_model=WatchlistListResponse)
async def list_research_watchlists():
    """Lists all user-defined research watchlists."""
    items = research_analytics_service.list_watchlists()
    return WatchlistListResponse(total_watchlists=len(items), watchlists=items)


@router.post("/watchlists", response_model=WatchlistResponse, status_code=status.HTTP_201_CREATED)
async def create_research_watchlist(body: WatchlistCreateRequest):
    """Creates a new research watchlist with persistent filter criteria."""
    return research_analytics_service.create_watchlist(
        name=body.name,
        description=body.description,
        criteria=body.criteria
    )


@router.get("/watchlists/{watchlist_id}", response_model=WatchlistResponse)
async def get_research_watchlist(watchlist_id: str):
    """Retrieves a research watchlist by ID."""
    wl = research_analytics_service.get_watchlist(watchlist_id)
    if not wl:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Watchlist '{watchlist_id}' not found."
        )
    return wl


@router.put("/watchlists/{watchlist_id}", response_model=WatchlistResponse)
async def update_research_watchlist(watchlist_id: str, body: WatchlistUpdateRequest):
    """Updates an existing research watchlist."""
    updated = research_analytics_service.update_watchlist(
        watchlist_id=watchlist_id,
        name=body.name,
        description=body.description,
        criteria=body.criteria
    )
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Watchlist '{watchlist_id}' not found."
        )
    return updated


@router.delete("/watchlists/{watchlist_id}")
async def delete_research_watchlist(watchlist_id: str):
    """Deletes a research watchlist."""
    deleted = research_analytics_service.delete_watchlist(watchlist_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Watchlist '{watchlist_id}' not found."
        )
    return {"status": "DELETED", "watchlist_id": watchlist_id}


import os
from fastapi import Header

MAINTENANCE_AUTH_KEY_DEFAULT = "authorized_maintenance_token_p1_4"

# -----------------------------------------------------------------------------
# Retention Pruning Maintenance Endpoint
# -----------------------------------------------------------------------------
@router.post("/maintenance/prune", response_model=PruneAgedObservationsResponse)
async def prune_research_history(
    retention_days: int = Query(14, ge=1, le=365, description="Retention window in days"),
    max_per_token: int = Query(500, ge=10, le=10000, description="Maximum observations retained per token"),
    authorization_token: Optional[str] = Header(None, alias="X-Maintenance-Authorization"),
    key: Optional[str] = Query(None, description="Maintenance authorization key")
):
    """
    Executes bounded retention pruning strictly against research_history.db.
    Requires explicit maintenance authorization (X-Maintenance-Authorization header or key query param).
    Operational storage is completely inaccessible to this endpoint.
    """
    expected_token = os.environ.get("RESEARCH_MAINTENANCE_KEY", MAINTENANCE_AUTH_KEY_DEFAULT)
    provided_token = authorization_token or key
    if not provided_token or provided_token != expected_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Explicit maintenance authorization required to prune research observations."
        )

    return research_analytics_service.prune_history(
        retention_days=retention_days,
        max_per_token=max_per_token
    )


# -----------------------------------------------------------------------------
# OHLCV Historical Candles Endpoints
# -----------------------------------------------------------------------------
@router.get("/candles/{chain_id}/{pool_address}", response_model=CandleListResponse)
async def get_historical_candles(
    chain_id: str,
    pool_address: str,
    timeframe: str = Query("15m", description="Candle interval resolution: 1m, 5m, 15m, 1h, 4h, 12h, 1d"),
    start_time: Optional[str] = Query(None, description="ISO 8601 UTC start timestamp"),
    end_time: Optional[str] = Query(None, description="ISO 8601 UTC end timestamp"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum candles to return"),
    min_bars_required: Optional[int] = Query(None, ge=1, description="Strategy-dependent minimum history threshold"),
    include_incomplete: bool = Query(True, description="Whether to include active unclosed latest bar")
):
    """
    Public read-only retrieval of persisted OHLCV candles from research_history.db.
    Includes deterministic candle series integrity report detailing bounds, gaps, and ordering.
    """
    try:
        tf_enum = Timeframe(timeframe)
    except ValueError:
        valid_tfs = [t.value for t in Timeframe]
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid timeframe '{timeframe}'. Must be one of: {valid_tfs}"
        )

    store = research_analytics_service.store
    raw_rows = store.get_candles(
        chain_id=chain_id,
        pool_address=pool_address,
        timeframe=tf_enum.value,
        start_time=start_time,
        end_time=end_time,
        limit=limit
    )

    if not raw_rows:
        return CandleListResponse(
            chain_id=chain_id,
            pool_address=pool_address,
            timeframe=tf_enum,
            total_candles=0,
            candles=[],
            integrity_report=None,
            provider_status="OPERATIONAL"
        )

    # Evaluate integrity on raw stored candles
    sorted_dtos, integrity_rep = candle_integrity_validator.evaluate_series_integrity(
        raw_candles=raw_rows,
        chain_id=chain_id,
        pool_address=pool_address,
        timeframe=tf_enum,
        min_bars_required=min_bars_required
    )

    # Filter out incomplete bar if caller requested closed bars only
    final_candles = sorted_dtos
    if not include_incomplete:
        final_candles = [c for c in sorted_dtos if c.bar_status == CandleBarStatus.CLOSED]

    return CandleListResponse(
        chain_id=chain_id,
        pool_address=pool_address,
        timeframe=tf_enum,
        total_candles=len(final_candles),
        candles=final_candles,
        integrity_report=integrity_rep,
        provider_status="OPERATIONAL"
    )


@router.post("/candles/fetch", response_model=ProtectedCandleFetchResponse)
async def fetch_and_persist_provider_candles(
    body: ProtectedCandleFetchRequest,
    authorization_token: Optional[str] = Header(None, alias="X-Maintenance-Authorization"),
    key: Optional[str] = Query(None, description="Maintenance authorization key")
):
    """
    Protected maintenance endpoint: Fetches historical OHLCV from provider and persists to research_history.db.
    Requires explicit maintenance authorization (X-Maintenance-Authorization header or key query param).
    Operational storage is completely inaccessible to this endpoint.
    """
    expected_token = os.environ.get("RESEARCH_MAINTENANCE_KEY", MAINTENANCE_AUTH_KEY_DEFAULT)
    provided_token = authorization_token or key
    if not provided_token or provided_token != expected_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Explicit maintenance authorization required to fetch provider candles."
        )

    # Resolve provider network from chain_id
    network = CAIP2_TO_GECKO_NETWORK.get(body.chain_id)
    if not network:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported chain_id '{body.chain_id}' for GeckoTerminal OHLCV provider."
        )

    gecko_client = market_data_coordinator.gecko_client
    fetch_result = await gecko_client.fetch_ohlcv_candles(
        network=network,
        pool_address=body.pool_address,
        timeframe=body.timeframe.value,
        before_timestamp=body.before_timestamp,
        limit=body.limit
    )

    fetch_status = fetch_result.get("status", "PROVIDER_UNAVAILABLE")
    if fetch_status in ("PROVIDER_UNAVAILABLE", "OFFLINE"):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"OHLCV provider is currently {fetch_status}: {fetch_result.get('error', 'Rate limited or network offline')}"
        )
    elif fetch_status == "POOL_NOT_FOUND":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Pool '{body.pool_address}' not found on network '{network}'"
        )

    raw_candles = fetch_result.get("candles", [])

    # Enrich with base/quote tokens if provided
    for c in raw_candles:
        if body.base_token_address:
            c["base_token_address"] = body.base_token_address
        if body.quote_token_address:
            c["quote_token_address"] = body.quote_token_address

    # Batch upsert into research history store
    store = research_analytics_service.store
    summary = store.upsert_candles_batch(raw_candles)

    return ProtectedCandleFetchResponse(
        chain_id=body.chain_id,
        pool_address=body.pool_address,
        timeframe=body.timeframe,
        provider="geckoterminal",
        fetched_count=len(raw_candles),
        new_inserts=summary["new_inserts"],
        identical_duplicates=summary["identical_duplicates"],
        conflicting_updates=summary["conflicting_updates"],
        rejected_bounds=summary["rejected_bounds"],
        status="SUCCESS"
    )


@router.get("/indicators/{chain_id}/{pool_address}", response_model=TechnicalIndicatorsResponseDTO)
async def get_technical_indicators(
    chain_id: str,
    pool_address: str,
    timeframe: Timeframe = Query(Timeframe.M15, description="Candle interval resolution"),
    indicators: Optional[str] = Query(None, description="Comma-separated indicators: sma,ema,rsi,macd,atr,volume_sma,rvol,volume_acceleration. Default is all."),
    sma_period: int = Query(14, ge=1, le=200, description="SMA period"),
    ema_period: int = Query(14, ge=1, le=200, description="EMA period"),
    rsi_period: int = Query(14, ge=1, le=100, description="RSI period"),
    atr_period: int = Query(14, ge=1, le=100, description="ATR period"),
    volume_sma_period: int = Query(20, ge=1, le=200, description="Volume SMA period"),
    rvol_period: int = Query(20, ge=1, le=200, description="RVOL baseline period"),
    vol_accel_short: int = Query(3, ge=1, le=50, description="Short volume acceleration period"),
    vol_accel_long: int = Query(20, ge=2, le=200, description="Long volume acceleration period"),
    fast_macd: int = Query(12, ge=1, le=100, description="Fast MACD period"),
    slow_macd: int = Query(26, ge=2, le=200, description="Slow MACD period"),
    signal_macd: int = Query(9, ge=1, le=50, description="Signal MACD period"),
    limit: int = Query(100, ge=1, le=1000, description="Number of historical candles to evaluate"),
    include_incomplete: bool = Query(False, description="Whether to include active unclosed bar")
):
    """
    Public read-only research endpoint calculating deterministic technical and volume indicators
    over validated historical OHLCV candles stored in research_history.db.
    """
    store = research_analytics_service.store
    status_filter = None if include_incomplete else "CLOSED"
    raw_rows = store.get_candles(
        chain_id=chain_id,
        pool_address=pool_address,
        timeframe=timeframe.value,
        limit=limit,
        bar_status=status_filter
    )

    candles: List[OHLCVCandleDTO] = []
    for r in raw_rows:
        try:
            candles.append(OHLCVCandleDTO(
                chain_id=r["chain_id"],
                pool_address=r["pool_address"],
                base_token_address=r.get("base_token_address"),
                quote_token_address=r.get("quote_token_address"),
                timeframe=Timeframe(r["timeframe"]),
                timestamp_utc=r["timestamp_utc"],
                timestamp_unix=r["timestamp_unix"],
                end_timestamp_utc=r.get("end_timestamp_utc", r["timestamp_utc"]),
                open_price=r["open_price"],
                high_price=r["high_price"],
                low_price=r["low_price"],
                close_price=r["close_price"],
                volume=r["volume"],
                price_currency=r.get("price_currency", "USD"),
                volume_currency=r.get("volume_currency", "USD"),
                bar_status=r.get("bar_status", "CLOSED"),
                provider=r.get("provider", "geckoterminal"),
                provider_version=r.get("provider_version", "v2_20230302"),
                revision_count=r.get("revision_count", 0)
            ))
        except Exception:
            continue

    # Determine requested indicators
    selected = set(i.strip().lower() for i in indicators.split(",")) if indicators else {
        "sma", "ema", "rsi", "macd", "atr", "volume_sma", "rvol", "volume_acceleration"
    }

    sma_res = indicator_engine.calculate_sma(candles, period=sma_period, closed_only=not include_incomplete) if "sma" in selected else None
    ema_res = indicator_engine.calculate_ema(candles, period=ema_period, closed_only=not include_incomplete) if "ema" in selected else None
    rsi_res = indicator_engine.calculate_rsi(candles, period=rsi_period, closed_only=not include_incomplete) if "rsi" in selected else None
    macd_res = indicator_engine.calculate_macd(candles, fast_period=fast_macd, slow_period=slow_macd, signal_period=signal_macd, closed_only=not include_incomplete) if "macd" in selected else None
    atr_res = indicator_engine.calculate_atr(candles, period=atr_period, closed_only=not include_incomplete) if "atr" in selected else None
    vol_sma_res = volume_intelligence_service.calculate_volume_sma(candles, period=volume_sma_period, closed_only=not include_incomplete) if "volume_sma" in selected else None
    rvol_res = volume_intelligence_service.calculate_rvol(candles, period=rvol_period, closed_only=not include_incomplete) if "rvol" in selected else None
    vol_accel_res = volume_intelligence_service.calculate_volume_acceleration(candles, short_period=vol_accel_short, long_period=vol_accel_long, closed_only=not include_incomplete) if "volume_acceleration" in selected else None

    return TechnicalIndicatorsResponseDTO(
        chain_id=chain_id,
        pool_address=pool_address,
        timeframe=timeframe,
        total_candles_evaluated=len(candles),
        calculation_version=INDICATOR_ENGINE_VERSION,
        sma=sma_res,
        ema=ema_res,
        rsi=rsi_res,
        macd=macd_res,
        atr=atr_res,
        volume_sma=vol_sma_res,
        rvol=rvol_res,
        volume_acceleration=vol_accel_res
    )


@router.get("/market-structure/{chain_id}/{pool_address}", response_model=MarketStructureResponseDTO)
async def get_market_structure(
    chain_id: str,
    pool_address: str,
    timeframe: Timeframe = Query(Timeframe.M15, description="Candle interval resolution"),
    swing_lookback: int = Query(2, ge=1, le=50, description="Bars required to left of swing"),
    swing_lookforward: int = Query(2, ge=1, le=50, description="Bars required to right for swing confirmation"),
    displacement_atr_mult: float = Query(1.5, ge=0.1, le=20.0, description="ATR expansion threshold for generic Order Block"),
    limit: int = Query(100, ge=1, le=1000, description="Number of historical candles to evaluate"),
    include_incomplete: bool = Query(False, description="Whether to include active unclosed bar")
):
    """
    Public read-only research endpoint calculating generic market structure features:
    swings, liquidity levels, FVGs, and generic Order Blocks.
    """
    store = research_analytics_service.store
    status_filter = None if include_incomplete else "CLOSED"
    raw_rows = store.get_candles(
        chain_id=chain_id,
        pool_address=pool_address,
        timeframe=timeframe.value,
        limit=limit,
        bar_status=status_filter
    )

    candles: List[OHLCVCandleDTO] = []
    for r in raw_rows:
        try:
            candles.append(OHLCVCandleDTO(
                chain_id=r["chain_id"],
                pool_address=r["pool_address"],
                base_token_address=r.get("base_token_address"),
                quote_token_address=r.get("quote_token_address"),
                timeframe=Timeframe(r["timeframe"]),
                timestamp_utc=r["timestamp_utc"],
                timestamp_unix=r["timestamp_unix"],
                end_timestamp_utc=r.get("end_timestamp_utc", r["timestamp_utc"]),
                open_price=r["open_price"],
                high_price=r["high_price"],
                low_price=r["low_price"],
                close_price=r["close_price"],
                volume=r["volume"],
                price_currency=r.get("price_currency", "USD"),
                volume_currency=r.get("volume_currency", "USD"),
                bar_status=r.get("bar_status", "CLOSED"),
                provider=r.get("provider", "geckoterminal"),
                provider_version=r.get("provider_version", "v2_20230302"),
                revision_count=r.get("revision_count", 0)
            ))
        except Exception:
            continue

    return market_structure_service.analyze_market_structure(
        chain_id=chain_id,
        pool_address=pool_address,
        timeframe=timeframe,
        candles=candles,
        swing_lookback=swing_lookback,
        swing_lookforward=swing_lookforward,
        displacement_atr_multiple=Decimal(str(displacement_atr_mult)),
        closed_only=not include_incomplete
    )


# ============================================================================
# StrategySDK Declarative Strategy Endpoints (P1.5-C)
# ============================================================================

@router.get("/features", response_model=Dict[str, Any])
async def list_registered_features():
    """Returns the controlled registry of approved market features, operators, and parameters."""
    return feature_registry.list_features()


@router.post("/strategies/validate", response_model=StrategyValidationResultDTO)
async def validate_declarative_strategy(body: DeclarativeStrategyDTO):
    """
    Dry-run validation of a declarative strategy definition without persisting.
    Validates syntax, security boundaries, operator-type compatibility, and nesting limits.
    """
    return strategy_evaluator.validate_strategy_safety(body)


@router.post("/strategies", response_model=DeclarativeStrategyDTO, status_code=status.HTTP_201_CREATED)
async def create_declarative_strategy(body: StrategyCreateRequest):
    """
    Registers and persists a new declarative strategy into research_history.db.
    Operational trading database is completely isolated.
    """
    try:
        return strategy_manager.register_strategy(
            strategy=body.strategy_definition,
            change_summary=body.change_summary
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc)
        )


@router.get("/strategies", response_model=List[Dict[str, Any]])
async def list_declarative_strategies(limit: int = Query(50, ge=1, le=100)):
    """Lists registered declarative strategies."""
    return strategy_manager.list_strategies(limit=limit)


@router.get("/strategies/{strategy_id}", response_model=DeclarativeStrategyDTO)
async def get_declarative_strategy(
    strategy_id: str,
    version: Optional[str] = Query(None, description="Optional specific immutable version to retrieve")
):
    """Retrieves a declarative strategy by ID and optional version."""
    strategy = strategy_manager.get_strategy(strategy_id=strategy_id, version=version)
    if not strategy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Declarative strategy '{strategy_id}' (version: {version or 'latest'}) not found."
        )
    return strategy


@router.get("/strategies/{strategy_id}/versions", response_model=List[StrategyVersionSummaryDTO])
async def get_declarative_strategy_versions(strategy_id: str):
    """Retrieves all immutable version records for a strategy."""
    versions = strategy_manager.get_strategy_versions(strategy_id=strategy_id)
    if not versions:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No versions found for strategy '{strategy_id}'."
        )
    return versions


@router.put("/strategies/{strategy_id}", response_model=DeclarativeStrategyDTO)
async def update_declarative_strategy(strategy_id: str, body: StrategyUpdateRequest):
    """
    Updates an existing declarative strategy by creating a new immutable version.
    Historical version definitions are never overwritten.
    """
    try:
        return strategy_manager.update_strategy(
            strategy_id=strategy_id,
            new_version=body.new_version,
            updated_strategy=body.strategy_definition,
            change_summary=body.change_summary
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc)
        )


@router.post("/strategies/{strategy_id}/status")
async def set_declarative_strategy_status(strategy_id: str, body: StrategyStatusUpdateRequest):
    """Enables or disables evaluation for a strategy."""
    success = strategy_manager.set_strategy_status(strategy_id=strategy_id, enabled=body.enabled)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Strategy '{strategy_id}' not found."
        )
    return {
        "strategy_id": strategy_id,
        "enabled": body.enabled,
        "status": "SUCCESS"
    }


@router.post("/strategies/{strategy_id}/evaluate", response_model=StrategyEvaluationResponseDTO)
async def evaluate_declarative_strategy(strategy_id: str, body: StrategyEvaluateRequest):
    """
    Evaluates a registered declarative strategy against historical OHLCV candles stored in research_history.db.
    Purely descriptive output; strictly does not execute trades or create orders.
    """
    store = research_analytics_service.store
    status_filter = None if body.include_incomplete else "CLOSED"
    raw_rows = store.get_candles(
        chain_id=body.chain_id,
        pool_address=body.pool_address,
        timeframe=body.timeframe.value,
        limit=body.limit,
        bar_status=status_filter
    )

    candles: List[OHLCVCandleDTO] = []
    for r in raw_rows:
        try:
            candles.append(OHLCVCandleDTO(
                chain_id=r["chain_id"],
                pool_address=r["pool_address"],
                base_token_address=r.get("base_token_address"),
                quote_token_address=r.get("quote_token_address"),
                timeframe=Timeframe(r["timeframe"]),
                timestamp_utc=r["timestamp_utc"],
                timestamp_unix=r["timestamp_unix"],
                end_timestamp_utc=r.get("end_timestamp_utc", r["timestamp_utc"]),
                open_price=r["open_price"],
                high_price=r["high_price"],
                low_price=r["low_price"],
                close_price=r["close_price"],
                volume=r["volume"],
                price_currency=r.get("price_currency", "USD"),
                volume_currency=r.get("volume_currency", "USD"),
                bar_status=r.get("bar_status", "CLOSED"),
                provider=r.get("provider", "geckoterminal"),
                provider_version=r.get("provider_version", "v2_20230302"),
                revision_count=r.get("revision_count", 0)
            ))
        except Exception:
            continue

    return strategy_manager.evaluate_strategy(
        strategy_id=strategy_id,
        candles=candles,
        chain_id=body.chain_id,
        pool_address=body.pool_address,
        timeframe=body.timeframe
    )


@router.post("/strategies/evaluate-raw", response_model=StrategyEvaluationResponseDTO)
async def evaluate_raw_declarative_strategy(body: StrategyEvaluateRawRequest):
    """
    Evaluates an unpersisted declarative strategy definition directly against pool candles.
    """
    store = research_analytics_service.store
    status_filter = None if body.include_incomplete else "CLOSED"
    raw_rows = store.get_candles(
        chain_id=body.chain_id,
        pool_address=body.pool_address,
        timeframe=body.timeframe.value,
        limit=body.limit,
        bar_status=status_filter
    )

    candles: List[OHLCVCandleDTO] = []
    for r in raw_rows:
        try:
            candles.append(OHLCVCandleDTO(
                chain_id=r["chain_id"],
                pool_address=r["pool_address"],
                base_token_address=r.get("base_token_address"),
                quote_token_address=r.get("quote_token_address"),
                timeframe=Timeframe(r["timeframe"]),
                timestamp_utc=r["timestamp_utc"],
                timestamp_unix=r["timestamp_unix"],
                end_timestamp_utc=r.get("end_timestamp_utc", r["timestamp_utc"]),
                open_price=r["open_price"],
                high_price=r["high_price"],
                low_price=r["low_price"],
                close_price=r["close_price"],
                volume=r["volume"],
                price_currency=r.get("price_currency", "USD"),
                volume_currency=r.get("volume_currency", "USD"),
                bar_status=r.get("bar_status", "CLOSED"),
                provider=r.get("provider", "geckoterminal"),
                provider_version=r.get("provider_version", "v2_20230302"),
                revision_count=r.get("revision_count", 0)
            ))
        except Exception:
            continue

    return strategy_manager.evaluate_raw(
        strategy_def=body.strategy_definition,
        candles=candles,
        chain_id=body.chain_id,
        pool_address=body.pool_address,
        timeframe=body.timeframe
    )


def _fetch_closed_candles(
    chain_id: str,
    pool_address: str,
    timeframe: str,
    limit: int
) -> List[OHLCVCandleDTO]:
    store = research_analytics_service.store
    raw_rows = store.get_candles(
        chain_id=chain_id,
        pool_address=pool_address,
        timeframe=timeframe,
        limit=limit,
        bar_status="CLOSED"
    )
    candles: List[OHLCVCandleDTO] = []
    for r in raw_rows:
        try:
            candles.append(OHLCVCandleDTO(
                chain_id=r["chain_id"],
                pool_address=r["pool_address"],
                base_token_address=r.get("base_token_address"),
                quote_token_address=r.get("quote_token_address"),
                timeframe=Timeframe(r["timeframe"]),
                timestamp_utc=r["timestamp_utc"],
                timestamp_unix=r["timestamp_unix"],
                end_timestamp_utc=r.get("end_timestamp_utc", r["timestamp_utc"]),
                open_price=r["open_price"],
                high_price=r["high_price"],
                low_price=r["low_price"],
                close_price=r["close_price"],
                volume=r["volume"],
                price_currency=r.get("price_currency", "USD"),
                volume_currency=r.get("volume_currency", "USD"),
                bar_status=r.get("bar_status", "CLOSED"),
                provider=r.get("provider", "geckoterminal"),
                provider_version=r.get("provider_version", "v2_20230302"),
                revision_count=r.get("revision_count", 0)
            ))
        except Exception:
            continue
    return candles


# -----------------------------------------------------------------------------
# P1.5-D ICT (Inner Circle Trader) Strategy Concepts
# -----------------------------------------------------------------------------

@router.get("/ict/evaluate/{chain_id}/{pool_address}")
async def evaluate_ict_concepts(
    chain_id: str,
    pool_address: str,
    timeframe: Timeframe = Query(Timeframe.M15, description="Candle timeframe"),
    limit: int = Query(100, ge=10, le=500, description="Number of closed candles to evaluate"),
    correlated_pool_address: Optional[str] = Query(None, description="Optional secondary pool address for SMT divergence")
):
    """
    Evaluates optional ICT technical concepts: OTE (0.618 - 0.786 retracement),
    CISD (Change in State of Delivery), and SMT divergence.
    """
    candles = _fetch_closed_candles(chain_id, pool_address, timeframe.value, limit)
    if not candles:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No closed candles found for {chain_id}/{pool_address} ({timeframe.value}). Ingest candles first."
        )

    ote = ict_engine.calculate_ote_zones(candles)
    cisd = ict_engine.evaluate_cisd(candles)

    correlated_candles = None
    if correlated_pool_address:
        correlated_candles = _fetch_closed_candles(chain_id, correlated_pool_address, timeframe.value, limit)

    smt = ict_engine.evaluate_smt_divergence(candles, correlated_candles)

    return {
        "chain_id": chain_id,
        "pool_address": pool_address,
        "timeframe": timeframe.value,
        "candles_evaluated": len(candles),
        "optimal_trade_entry": ote,
        "change_in_state_of_delivery": cisd,
        "smart_money_technique": smt
    }


# -----------------------------------------------------------------------------
# P1.5-E Historical Backtesting Engine
# -----------------------------------------------------------------------------

@router.post("/backtest/run", response_model=BacktestResultDTO)
async def run_backtest(body: BacktestRunRequest):
    """
    Executes a deterministic historical backtest for a declarative strategy.
    Enforces closed candle evaluation, next-bar open fill, fees, slippage, and drawdown tracking.
    """
    candles = _fetch_closed_candles(
        body.config.chain_id,
        body.config.pool_address,
        body.config.timeframe.value,
        body.config.limit_bars
    )
    if not candles or len(candles) < 5:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Insufficient closed candles ({len(candles) if candles else 0}) for backtesting. Minimum 5 closed candles required."
        )

    strategy: Optional[DeclarativeStrategyDTO] = None
    if body.strategy_id:
        strategy = strategy_manager.get_strategy(body.strategy_id)
        if not strategy:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Strategy '{body.strategy_id}' not found."
            )
    elif body.strategy_definition:
        try:
            strategy = DeclarativeStrategyDTO.model_validate(body.strategy_definition)
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid strategy definition schema: {e}"
            )
        val_res = strategy_evaluator.validate_strategy_safety(strategy)
        if not val_res.is_valid:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Strategy failed safety checks: {val_res.errors}"
            )
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Either 'strategy_id' or 'strategy_definition' must be provided."
        )

    return backtesting_engine.run_backtest(
        strategy=strategy,
        candles=candles,
        config=body.config
    )


# -----------------------------------------------------------------------------
# P1.5-F Strategy Paper Trading Virtual Broker
# -----------------------------------------------------------------------------

@router.post("/paper/accounts", response_model=StrategyPaperAccountDTO)
async def create_paper_account(body: PaperAccountCreateRequest):
    """Creates a new virtual strategy paper-trading account."""
    try:
        return research_paper_engine.create_account(
            account_id=body.account_id,
            name=body.name,
            initial_balance=body.initial_balance_usd
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/paper/accounts", response_model=List[Dict[str, Any]])
async def list_paper_accounts():
    """Lists all virtual paper-trading accounts."""
    return research_paper_engine.list_accounts()


@router.get("/paper/accounts/{account_id}", response_model=StrategyPaperAccountDTO)
async def get_paper_account_detail(account_id: str):
    """Retrieves full account state with mark-to-market positions and equity."""
    acc = research_paper_engine.get_account(account_id)
    if not acc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Paper account '{account_id}' not found."
        )
    return acc


@router.get("/paper/accounts/{account_id}/positions", response_model=List[StrategyPaperPositionDTO])
async def get_paper_account_positions(
    account_id: str,
    status_filter: Optional[str] = Query(None, description="Optional status: OPEN or CLOSED")
):
    """Retrieves simulated positions for a paper trading account."""
    acc = research_paper_engine.get_account(account_id)
    if not acc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Paper account '{account_id}' not found."
        )
    raw_positions = research_analytics_service.store.get_strategy_paper_positions(account_id, status=status_filter)
    result = []
    for rp in raw_positions:
        result.append(StrategyPaperPositionDTO(
            position_id=rp["position_id"],
            account_id=rp["account_id"],
            strategy_id=rp["strategy_id"],
            chain_id=rp["chain_id"],
            pool_address=rp["pool_address"],
            side=rp["side"],
            quantity=Decimal(rp["quantity"]),
            entry_price=Decimal(rp["entry_price"]),
            current_price=Decimal(rp["entry_price"]),
            cost_basis_usd=Decimal(rp["cost_basis_usd"]),
            unrealized_pnl_usd=Decimal("0"),
            realized_pnl_usd=Decimal(rp["realized_pnl_usd"]),
            status=rp["status"],
            opened_at=rp["opened_at"],
            closed_at=rp.get("closed_at")
        ))
    return result


@router.get("/paper/accounts/{account_id}/orders", response_model=List[StrategyPaperOrderDTO])
async def get_paper_account_orders(
    account_id: str,
    limit: int = Query(50, ge=1, le=200)
):
    """Retrieves recent simulated orders for a paper trading account."""
    acc = research_paper_engine.get_account(account_id)
    if not acc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Paper account '{account_id}' not found."
        )
    raw_orders = research_analytics_service.store.get_strategy_paper_orders(account_id, limit=limit)
    return [
        StrategyPaperOrderDTO(
            order_id=ro["order_id"],
            account_id=ro["account_id"],
            strategy_id=ro["strategy_id"],
            chain_id=ro["chain_id"],
            pool_address=ro["pool_address"],
            side=ro["side"],
            quantity=Decimal(ro["quantity"]),
            price=Decimal(ro["price"]),
            fee_usd=Decimal(ro["fee_usd"]),
            slippage_usd=Decimal(ro["slippage_usd"]),
            status=ro["status"],
            idempotency_key=ro["idempotency_key"],
            created_at=ro["created_at"],
            filled_at=ro.get("filled_at")
        )
        for ro in raw_orders
    ]


@router.post("/paper/step", response_model=StrategyPaperStepResponse)
async def step_strategy_paper_trading(body: StrategyPaperStepRequest):
    """
    Evaluates strategy conditions against closed candles for pool and executes
    simulated paper order if triggered.
    """
    candles = _fetch_closed_candles(
        body.chain_id,
        body.pool_address,
        body.timeframe.value,
        limit=100
    )
    if not candles:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"No closed candles available for {body.chain_id}/{body.pool_address} ({body.timeframe.value}) to evaluate."
        )

    try:
        return research_paper_engine.step_strategy_paper_trading(
            account_id=body.account_id,
            strategy_id=body.strategy_id,
            candles=candles,
            position_size_usd=body.position_size_usd,
            fee_rate_bps=body.fee_rate_bps,
            slippage_bps=body.slippage_bps
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))



