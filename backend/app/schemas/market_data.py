from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Dict, List, Optional, Any
from pydantic import BaseModel, Field, model_validator

class MarketDataFreshness(str, Enum):
    FRESH = "FRESH"            # < 60s
    STALE = "STALE"            # >= 60s
    UNAVAILABLE = "UNAVAILABLE"

class AssetCategory(str, Enum):
    MEME = "MEME"
    NON_MEME = "NON_MEME"
    UNKNOWN = "UNKNOWN"

class ClassificationSource(str, Enum):
    CURATED_REGISTRY = "CURATED_REGISTRY"
    HEURISTIC = "HEURISTIC"
    DEFAULT = "DEFAULT"

class ClassificationConfidence(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"

class MarketPairObservation(BaseModel):
    """
    Normalized market-pair observation representation.
    Preserves exact Decimal precision, timestamps, provider source, and missing field indicators.
    """
    chain_id: str = Field(..., description="Internal CAIP-2 chain ID, e.g. 'solana:mainnet' or 'eip155:8453'")
    provider_network_id: str = Field(..., description="Provider network identifier, e.g. 'solana' or 'base'")
    token_address: str = Field(..., description="Target token contract/mint address")
    pair_address: Optional[str] = Field(default=None, description="Pool/pair contract address")
    pool_address: Optional[str] = Field(default=None, description="Pool/pair contract address alias")
    name: Optional[str] = Field(default=None, description="Token name")
    symbol: Optional[str] = Field(default=None, description="Token ticker symbol")
    dex_name: Optional[str] = Field(default="unknown", description="Decentralized exchange name")
    dex_id: Optional[str] = Field(default=None, description="DEX identifier alias")
    quote_token_address: Optional[str] = Field(default=None, description="Quote token contract/mint address")
    quote_token_symbol: Optional[str] = Field(default=None, description="Quote token symbol, e.g. 'SOL', 'USDC', 'WETH'")
    
    asset_category: AssetCategory = Field(default=AssetCategory.UNKNOWN, description="Token classification: MEME, NON_MEME, or UNKNOWN")
    classification_source: ClassificationSource = Field(default=ClassificationSource.DEFAULT, description="Classification evidence source")
    confidence: ClassificationConfidence = Field(default=ClassificationConfidence.LOW, description="Classification confidence level")
    classification_reason: str = Field(default="Unclassified asset", description="Human-readable explanation of classification")
    
    price_usd: Optional[Decimal] = Field(default=None, description="USD price of the token in this pool")
    price_native: Optional[Decimal] = Field(default=None, description="Price in native quote currency")
    liquidity_usd: Optional[Decimal] = Field(default=Decimal("0.0"), description="Total pool liquidity in USD")
    
    volume_5m: Optional[Decimal] = Field(default=None, description="USD volume last 5 minutes")
    volume_1h: Optional[Decimal] = Field(default=None, description="USD volume last 1 hour")
    volume_6h: Optional[Decimal] = Field(default=None, description="USD volume last 6 hours")
    volume_24h: Optional[Decimal] = Field(default=None, description="USD volume last 24 hours")
    volume_24h_usd: Optional[Decimal] = Field(default=None, description="USD volume last 24 hours alias")
    
    txns_5m_buys: Optional[int] = Field(default=None)
    txns_5m_sells: Optional[int] = Field(default=None)
    txns_1h_buys: Optional[int] = Field(default=None)
    txns_1h_sells: Optional[int] = Field(default=None)
    txns_24h_buys: Optional[int] = Field(default=None)
    txns_24h_sells: Optional[int] = Field(default=None)
    
    price_change_5m_pct: Optional[Decimal] = Field(default=None)
    price_change_1h_pct: Optional[Decimal] = Field(default=None)
    price_change_6h_pct: Optional[Decimal] = Field(default=None)
    price_change_24h_pct: Optional[Decimal] = Field(default=None)
    
    fdv_usd: Optional[Decimal] = Field(default=None, description="Fully Diluted Valuation in USD")
    market_cap_usd: Optional[Decimal] = Field(default=None, description="Market Cap in USD")
    
    pair_created_at: Optional[datetime] = Field(default=None, description="Actual pool creation timestamp")
    provider_source: str = Field(default="geckoterminal", description="Origin provider: 'geckoterminal' or 'dexscreener'")
    endpoint_id: Optional[str] = Field(default=None, description="Endpoint-level discovery origin, e.g. 'geckoterminal:trending_pools'")
    provider_url: Optional[str] = Field(default=None, description="Direct provider URL")
    provider_urls: List[str] = Field(default_factory=list, description="List of provider URLs for this pool observation")
    observation_timestamps: Dict[str, datetime] = Field(default_factory=dict, description="Raw observation timestamps keyed by endpoint or provider")
    fetched_at: Optional[datetime] = Field(default_factory=lambda: datetime.now(timezone.utc), description="Timestamp when data was fetched from provider")
    observed_at: Optional[datetime] = Field(default_factory=lambda: datetime.now(timezone.utc), description="Timestamp when data was observed/normalized")
    cached_at: Optional[datetime] = Field(default=None, description="Cache timestamp")
    cache_age_seconds: float = Field(default=0.0, description="Age of cache in seconds")
    freshness_status: MarketDataFreshness = Field(default=MarketDataFreshness.FRESH)
    
    missing_fields: List[str] = Field(default_factory=list, description="Explicit list of fields unavailable from provider")
    is_valid: bool = Field(default=True, description="True if passed validation thresholds")

    @model_validator(mode="before")
    @classmethod
    def reconcile_aliases(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "pair_address" not in data and "pool_address" in data:
                data["pair_address"] = data["pool_address"]
            elif "pool_address" not in data and "pair_address" in data:
                data["pool_address"] = data["pair_address"]
            if "volume_24h" not in data and "volume_24h_usd" in data:
                data["volume_24h"] = data["volume_24h_usd"]
            elif "volume_24h_usd" not in data and "volume_24h" in data:
                data["volume_24h_usd"] = data["volume_24h"]
            if "dex_name" not in data and "dex_id" in data:
                data["dex_name"] = data["dex_id"]
            elif "dex_id" not in data and "dex_name" in data:
                data["dex_id"] = data["dex_name"]
            if "observed_at" not in data:
                data["observed_at"] = data.get("fetched_at") or datetime.now(timezone.utc)
            if "fetched_at" not in data:
                data["fetched_at"] = datetime.now(timezone.utc)
            if "provider_urls" not in data or not data["provider_urls"]:
                p_url = data.get("provider_url")
                data["provider_urls"] = [p_url] if p_url else []
            elif data.get("provider_url") and data["provider_url"] not in data["provider_urls"]:
                data["provider_urls"].append(data["provider_url"])
            if "observation_timestamps" not in data or not data["observation_timestamps"]:
                src = data.get("endpoint_id") or data.get("provider_source") or "unknown"
                obs_ts = data.get("observed_at") or data.get("fetched_at") or datetime.now(timezone.utc)
                data["observation_timestamps"] = {src: obs_ts}
        return data

class AggregatedTokenRanking(BaseModel):

    """
    Token-level ranking representation with deterministic canonical pool selection.
    Avoids multi-pool duplication in token screener rankings.
    """
    chain_id: str
    provider_network_id: str
    token_address: str
    symbol: str
    name: str
    canonical_pool_address: str
    dex_name: str
    canonical_dex_id: Optional[str] = None
    quote_token_symbol: str
    asset_category: AssetCategory = Field(default=AssetCategory.UNKNOWN, description="Token classification: MEME, NON_MEME, or UNKNOWN")
    classification_source: ClassificationSource = Field(default=ClassificationSource.DEFAULT, description="Classification evidence source")
    confidence: ClassificationConfidence = Field(default=ClassificationConfidence.LOW, description="Classification confidence level")
    classification_reason: str = Field(default="Unclassified asset", description="Human-readable explanation of classification")
    
    price_usd: Optional[Decimal] = Field(default=Decimal("0.0"))
    price_native: Optional[Decimal] = None
    liquidity_usd: Decimal
    volume_24h_usd: Decimal
    volume_1h_usd: Optional[Decimal] = None
    volume_5m_usd: Optional[Decimal] = None
    
    price_change_5m_pct: Optional[Decimal] = None
    price_change_1h_pct: Optional[Decimal] = None
    price_change_24h_pct: Optional[Decimal] = None
    
    fdv_usd: Optional[Decimal] = None
    market_cap_usd: Optional[Decimal] = None
    txns_24h_total: Optional[int] = None
    
    pair_created_at: Optional[datetime] = None
    age_hours: Optional[float] = None
    pool_count: int = 1
    multi_pool_count: Optional[int] = None
    distinct_pools_aggregated: int = Field(default=1, description="Number of distinct pool contracts aggregated")
    pool_selection_methodology: str = "MAX_LIQUIDITY_CANONICAL_POOL"
    
    provider_source: str
    endpoint_sources: List[str] = Field(default_factory=list, description="Unique endpoints contributing observations for this token")
    canonical_pool_url: Optional[str] = Field(default=None, description="Direct provider URL for the canonical pool")
    provider_urls: List[str] = Field(default_factory=list, description="All provider URLs for pools aggregated under this token")
    observation_timestamps: Dict[str, datetime] = Field(default_factory=dict, description="Observation timestamps from providers/endpoints")
    fetched_at: datetime
    cache_age_seconds: float
    freshness_status: MarketDataFreshness

    # Phase P1.3: Market Data Quality & Intelligence Fields
    quality_score: Optional[Decimal] = Field(default=None, description="Explainable composite quality score (0.0-100.0)")
    quality_factors: Optional[Dict[str, Any]] = Field(default=None, description="Itemized quality factors, weights, and explicit deductions")
    anomaly_flags: List[str] = Field(default_factory=list, description="Heuristic market anomaly flags (e.g. SUSPICIOUS_TURNOVER, INSUFFICIENT_DATA)")
    anomaly_details: Dict[str, Any] = Field(default_factory=dict, description="Metrics and contextual rationale for triggered anomaly indicators")
    collision_candidates_count: int = Field(default=0, description="Count of distinct contracts sharing the same ticker/name across chains")
    collision_candidate_addresses: List[str] = Field(default_factory=list, description="Chain:Address identifiers of potential ticker collision candidates")
    canonical_candidate_flag: bool = Field(default=False, description="Heuristic indicator for strongest candidate; never implies verified official legitimacy")
    canonical_candidate_rationale: Optional[str] = Field(default=None, description="Explicit heuristic evidence for candidate flag")
    is_new_pair: bool = Field(default=False, description="True if canonical pair creation age is < 24 hours")
    discovery_alerts: List[str] = Field(default_factory=list, description="Evaluated discovery alert rules for this token")

    @model_validator(mode="before")
    @classmethod
    def reconcile_dex_aliases(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "canonical_dex_id" not in data and "dex_name" in data:
                data["canonical_dex_id"] = data["dex_name"]
            elif "dex_name" not in data and "canonical_dex_id" in data:
                data["dex_name"] = data["canonical_dex_id"]
            if "multi_pool_count" not in data or data["multi_pool_count"] is None:
                data["multi_pool_count"] = data.get("pool_count", 1)
            if "distinct_pools_aggregated" not in data or data["distinct_pools_aggregated"] is None:
                data["distinct_pools_aggregated"] = data.get("pool_count", 1)
        return data


class DynamicScreenerResponse(BaseModel):
    """
    Response schema for multi-chain dynamic token screener.
    """
    data_source: str
    chains_requested: List[str]
    chains_with_data: List[str]
    fetched_at: datetime
    cache_age_seconds: float
    freshness_status: MarketDataFreshness
    provider_health: Dict[str, Any]
    result_count: int
    total_matches: int = Field(..., description="Total matching tokens in the currently discovered and aggregated observation dataset (not exhaustive global market)")
    total_pages: Optional[int] = Field(default=None, description="Total pages given current limit and total matches")
    current_page: Optional[int] = Field(default=None, description="Current page number (1-indexed)")
    limit: int
    offset: int
    memecoin_only: bool = Field(default=True, description="Whether memecoin-only filter is applied")
    category_applied: Optional[str] = Field(default=None, description="Category filter applied (MEME, NON_MEME, UNKNOWN, or ALL)")
    filters_applied: Dict[str, Any]
    sort_applied: str
    warnings: List[str] = []
    ranking_scope_note: str = Field(
        default="Rankings represent top discovered pools from public market providers, not the exhaustive global market.",
        description="Explicit disclosure that totals and rankings describe discovered dataset observations rather than exhaustive global DEX markets"
    )
    tokens: List[AggregatedTokenRanking]

class ProviderStatusDTO(BaseModel):
    provider_id: str
    name: str
    status: str  # OPERATIONAL, RATE_LIMITED, DEGRADED, UNAVAILABLE
    circuit_breaker_state: str  # CLOSED, OPEN, HALF_OPEN
    supported_chains: List[str]
    network_identifiers: Dict[str, str]
    last_successful_fetch: Optional[datetime] = None
    last_attempted_fetch: Optional[datetime] = None
    latency_ms: Optional[float] = None
    rate_limit_budget_remaining: int
    error_category: Optional[str] = None
    cache_age_seconds: Optional[float] = None
    freshness_status: MarketDataFreshness

class MarketProvidersHealthResponse(BaseModel):
    timestamp: datetime
    providers: Dict[str, ProviderStatusDTO]


class QualityFactorBreakdown(BaseModel):
    """
    Explainable, deterministic quality evaluation factor breakdown (Phase P1.3).
    Bounded between 0.0 and 100.0. Not an audit or financial safety guarantee.
    """
    liquidity_quality: Decimal = Field(default=Decimal("0.0"), description="Score based on liquidity depth (0-25)")
    volume_activity_quality: Decimal = Field(default=Decimal("0.0"), description="Score based on volume consistency & activity (0-25)")
    pool_depth_corroboration: Decimal = Field(default=Decimal("0.0"), description="Score based on multi-pool & provider corroboration (0-20)")
    provenance_confidence: Decimal = Field(default=Decimal("0.0"), description="Score based on classification confidence (0-15)")
    data_completeness_freshness: Decimal = Field(default=Decimal("0.0"), description="Score based on field presence & freshness (0-15)")
    deductions: Dict[str, Decimal] = Field(default_factory=dict, description="Itemized penalty deductions applied")
    composite_score: Decimal = Field(default=Decimal("0.0"), description="Final bounded score (0.0 to 100.0)")
    scoring_version: str = Field(default="v1.3_market_quality")
    disclaimer: str = Field(
        default="Quality score is a descriptive heuristic of market data depth and completeness, not an audit, endorsement, or safety guarantee.",
        description="Explicit limitation disclosure"
    )
    explanation: str = Field(default="", description="Explainable human-readable rationale of score drivers and deductions")


class MarketSnapshotDTO(BaseModel):
    """
    In-memory point-in-time observation snapshot (Phase P1.3).
    Episodic ring-buffer representation. Disappears on process restart.
    """
    snapshot_id: str
    captured_at: datetime
    total_tokens: int
    total_pools: int
    chains_represented: List[str]
    average_quality_score: Optional[Decimal] = None
    anomalies_detected_count: int = 0
    source_provenance: str = "IN_MEMORY_PUBLIC_OBSERVATIONS"
    note: str = "Episodic in-memory snapshot ring buffer (max 50). Disappears on process restart; not durable backtesting history."
    tokens_summary: List[Dict[str, Any]] = []


class MarketSnapshotListResponse(BaseModel):
    total_snapshots: int
    max_capacity: int = 50
    snapshots: List[MarketSnapshotDTO]

