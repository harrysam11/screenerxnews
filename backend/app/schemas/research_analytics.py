from pydantic import BaseModel, Field, ConfigDict
from typing import List, Dict, Any, Optional
from datetime import datetime
from decimal import Decimal


class HistoricalObservationDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    chain_id: str
    token_address: str
    symbol: Optional[str] = None
    name: Optional[str] = None
    price_usd: Optional[str] = None
    price_native: Optional[str] = None
    liquidity_usd: Optional[str] = None
    volume_24h_usd: Optional[str] = None
    volume_1h_usd: Optional[str] = None
    volume_5m_usd: Optional[str] = None
    price_change_5m_pct: Optional[float] = None
    price_change_1h_pct: Optional[float] = None
    price_change_24h_pct: Optional[float] = None
    fdv_usd: Optional[str] = None
    market_cap_usd: Optional[str] = None
    txns_24h_total: Optional[int] = None
    quality_score: Optional[float] = None
    scoring_version: str = "v1.4_research_vector"
    anomaly_flags: List[str] = Field(default_factory=list)
    canonical_candidate_flag: bool = False
    provider_source: str
    endpoint_sources: List[str] = Field(default_factory=list)
    observed_at: str
    created_at: str


class HistoricalObservationListResponse(BaseModel):
    chain_id: str
    token_address: str
    total_observations: int
    observations: List[HistoricalObservationDTO]


class TokenMomentumMetricsDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    chain_id: str
    token_address: str
    symbol: Optional[str] = None
    metric_type: str = "LATEST_STATE_PROJECTION"
    documentation: str = "Latest-state projection of derived momentum and liquidity metrics; immutable historical series is maintained in historical_token_observations."
    provider_price_change_5m_pct: Optional[float] = None
    provider_price_change_1h_pct: Optional[float] = None
    provider_price_change_24h_pct: Optional[float] = None
    historical_velocity_5m_pct: Optional[float] = None
    historical_velocity_1h_pct: Optional[float] = None
    historical_velocity_24h_pct: Optional[float] = None
    historical_calculation_status: str = "HISTORICAL_DATA_UNAVAILABLE"
    volume_acceleration_ratio: Optional[float] = None
    liquidity_migration_direction: str = "INSUFFICIENT_DATA"
    liquidity_delta_usd: Optional[str] = None
    observations_count: int = 1
    first_observed_at: str
    last_observed_at: str
    updated_at: str


class MomentumLeaderboardResponse(BaseModel):
    chain_id: Optional[str] = None
    sort_by: str
    total_items: int
    items: List[TokenMomentumMetricsDTO]


class CrossChainChainMetrics(BaseModel):
    chain_id: str
    chain_name: str
    token_count: int
    total_liquidity_usd: str
    total_volume_24h_usd: str
    average_quality_score: Optional[float] = None
    liquidity_share_pct: float
    volume_share_pct: float
    top_momentum_token: Optional[str] = None


class CrossChainMarketSummaryResponse(BaseModel):
    timestamp: str
    total_monitored_chains: int
    total_discovered_tokens: int
    total_aggregate_liquidity_usd: str
    total_aggregate_volume_24h_usd: str
    chains: List[CrossChainChainMetrics]
    disclaimer: str = "Cross-chain comparison reflects aggregated decentralized pool observations across monitored networks; not an exhaustive global market metric."


class WatchlistCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = Field(default="", max_length=500)
    criteria: Dict[str, Any] = Field(default_factory=dict)


class WatchlistUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    description: Optional[str] = Field(default=None, max_length=500)
    criteria: Optional[Dict[str, Any]] = None


class WatchlistResponse(BaseModel):
    watchlist_id: str
    name: str
    description: str
    criteria: Dict[str, Any]
    created_at: str
    updated_at: str


class WatchlistListResponse(BaseModel):
    total_watchlists: int
    watchlists: List[WatchlistResponse]


class StrategyFeatureVectorDTO(BaseModel):
    feature_id: str
    chain_id: str
    token_address: str
    symbol: Optional[str] = None
    feature_version: str = "v1.4_research_vector"
    observation_timestamp: str
    source_provenance: str
    calculation_version: str = "v1.4_research_vector"
    vector: Dict[str, Any]
    generated_at: str
    reproducibility_note: str = "Deterministic, read-only research feature vector for paper trading research. Zero live execution side effects."


class PruneAgedObservationsResponse(BaseModel):
    prune_id: str
    retention_days: int
    max_per_token: int
    rows_pruned: int
    cutoff_timestamp: str
    pruned_at: str
