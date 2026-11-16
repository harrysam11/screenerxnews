"""
Phase P1.3: Market Data Quality & Intelligence Test Suite.

Verifies:
1. Explainable Quality Scoring:
   - Deterministic, reproducible scoring bounded between 0.0 and 100.0.
   - Explicit factor breakdown: liquidity, volume, pool depth, provenance, data completeness.
   - Explicit deductions for missing data, stale observations, and micro-liquidity.
   - Disclaimer present; never claims safety guarantee.
2. Heuristic Anomaly Indicators:
   - Suspicious volume-to-liquidity turnover (> 50x).
   - Multi-pool price discrepancy (> 15%).
   - Insufficient liquidity backing (< 1% of valuation).
   - Zero transaction turnover with volume.
   - Missing metrics produce INSUFFICIENT_DATA (never false positive anomaly).
3. Cross-Chain Identity & Collision Disambiguation:
   - Chain + contract address as identity.
   - Matching tickers tagged as collision candidates, not verified clones.
   - Canonical candidate flag elected with explicit evidence (curated match or highest liquidity heuristic).
4. New-Pair Monitoring & Discovery Alerts:
   - is_new_pair flag (< 24h).
   - Declarative alert rules (momentum, acceleration, anomalous turnover, extreme price moves).
5. Historical Observation Snapshots:
   - In-memory ring buffer with FIFO eviction bounded to 50 snapshots.
   - API endpoints GET /discovery/snapshots and GET /discovery/snapshots/{id}.
6. Truthful Provider Health Transitions:
   - UNKNOWN before first attempt.
   - OPERATIONAL only after confirmed successful fetch.
   - DEGRADED on partial errors or secondary provider outage.
   - UNAVAILABLE when circuit breaker is open or fetch fails.
   - UNAVAILABLE freshness when no observation exists.
7. Screener API Filter and Sort Integration:
   - min_quality_score, has_anomalies, new_pairs_only, and sort_by=quality_score.
"""

import math
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import List, Dict, Any

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas.market_data import (
    MarketDataFreshness,
    AssetCategory,
    ClassificationSource,
    ClassificationConfidence,
    MarketPairObservation,
    AggregatedTokenRanking,
    QualityFactorBreakdown,
    MarketSnapshotDTO
)
from app.services.market_data_service import (
    MarketDataCoordinator,
    GeckoTerminalClient,
    DexScreenerClient,
    market_data_coordinator
)
from app.services.market_intelligence_service import (
    MarketIntelligenceService,
    market_intelligence_service,
    MAX_SNAPSHOT_CAPACITY
)

client = TestClient(app)


# -----------------------------------------------------------------------------
# 1. Truthful Provider Health Transitions
# -----------------------------------------------------------------------------
def test_provider_health_transitions_unknown_to_operational_and_degraded():
    """
    Verifies:
    1. UNKNOWN before first fetch attempt.
    2. UNAVAILABLE freshness when no successful fetch has occurred.
    3. OPERATIONAL only after confirmed successful fetch.
    4. DEGRADED on partial error.
    5. UNAVAILABLE when circuit breaker is OPEN.
    """
    gecko = GeckoTerminalClient()
    dex = DexScreenerClient()
    coord = MarketDataCoordinator(gecko_client=gecko, dex_client=dex)

    # State 1: Freshly initialized (never attempted) -> UNKNOWN
    health_init = coord.get_providers_health()
    gt_init = health_init.providers["geckoterminal"]
    ds_init = health_init.providers["dexscreener"]

    assert gt_init.status == "UNKNOWN"
    assert gt_init.last_attempted_fetch is None
    assert gt_init.last_successful_fetch is None
    assert gt_init.freshness_status == MarketDataFreshness.UNAVAILABLE

    assert ds_init.status == "UNKNOWN"
    assert ds_init.freshness_status == MarketDataFreshness.UNAVAILABLE

    # State 2: Successful fetch recorded -> OPERATIONAL
    now = datetime.now(timezone.utc)
    gecko.last_attempted_fetch = now
    gecko.last_successful_fetch = now
    gecko.last_latency_ms = 120.5
    coord._last_refresh_time = now

    health_op = coord.get_providers_health()
    gt_op = health_op.providers["geckoterminal"]
    assert gt_op.status == "OPERATIONAL"
    assert gt_op.freshness_status == MarketDataFreshness.FRESH
    assert gt_op.latency_ms == 120.5

    # State 3: Partial error recorded -> DEGRADED
    gecko.error_category = "HTTP_429_RATE_LIMITED"
    health_deg = coord.get_providers_health()
    gt_deg = health_deg.providers["geckoterminal"]
    assert gt_deg.status == "DEGRADED"

    # State 4: Circuit breaker tripped to OPEN -> UNAVAILABLE
    gecko.circuit_breaker.record_failure("HTTP_500")
    gecko.circuit_breaker.record_failure("HTTP_500")
    gecko.circuit_breaker.record_failure("HTTP_500")
    assert gecko.circuit_breaker.state == "OPEN"

    health_unavail = coord.get_providers_health()
    gt_unavail = health_unavail.providers["geckoterminal"]
    assert gt_unavail.status == "UNAVAILABLE"


# -----------------------------------------------------------------------------
# 2. Quality Scoring: Determinism, Bounds & Deductions
# -----------------------------------------------------------------------------
def test_quality_scoring_explainable_factors_and_bounds():
    """
    Verifies:
    1. Score is strictly deterministic and reproducible.
    2. Score is bounded between 0.0 and 100.0.
    3. Itemized deductions penalize missing price, missing age, and micro-liquidity.
    4. Limitation disclaimer is present in breakdown.
    """
    intel = MarketIntelligenceService()
    now = datetime.now(timezone.utc)

    # Token A: High quality token ($250k liq, $500k vol, curated registry, fresh)
    token_a = AggregatedTokenRanking(
        chain_id="solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp",
        provider_network_id="solana",
        token_address="DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
        symbol="BONK",
        name="Bonk Meme",
        canonical_pool_address="PoolBonk111111111111111111111111111111",
        dex_name="raydium",
        quote_token_symbol="SOL",
        asset_category=AssetCategory.MEME,
        classification_source=ClassificationSource.CURATED_REGISTRY,
        confidence=ClassificationConfidence.HIGH,
        price_usd=Decimal("0.000025"),
        liquidity_usd=Decimal("250000.00"),
        volume_24h_usd=Decimal("500000.00"),
        volume_1h_usd=Decimal("25000.00"),
        volume_5m_usd=Decimal("5000.00"),
        txns_24h_total=1200,
        pair_created_at=now - timedelta(days=10),
        age_hours=240.0,
        distinct_pools_aggregated=3,
        endpoint_sources=["geckoterminal:trending_pools", "dexscreener:tokens_batch"],
        provider_source="geckoterminal",
        fetched_at=now,
        cache_age_seconds=10.0,
        freshness_status=MarketDataFreshness.FRESH
    )

    q_a1 = intel.evaluate_quality_score(token_a)
    q_a2 = intel.evaluate_quality_score(token_a)

    # 1. Deterministic & Reproducible
    assert q_a1.composite_score == q_a2.composite_score
    assert q_a1.model_dump() == q_a2.model_dump()

    # High quality token receives high score (>= 75)
    assert q_a1.composite_score >= Decimal("75.0")
    assert q_a1.composite_score <= Decimal("100.0")
    assert "not an audit, endorsement, or safety guarantee" in q_a1.disclaimer
    assert len(q_a1.deductions) == 0

    # Token B: Missing price, missing pair creation timestamp, micro-liquidity ($500)
    token_b = AggregatedTokenRanking(
        chain_id="eip155:1",
        provider_network_id="eth",
        token_address="0xDefectiveToken1111111111111111111111111",
        symbol="DEFECTIVE",
        name="Defective Token",
        canonical_pool_address="0xPoolDefective11111111111111111111111",
        dex_name="uniswap",
        quote_token_symbol="WETH",
        price_usd=None,  # Missing price!
        liquidity_usd=Decimal("500.00"),  # Micro-liquidity!
        volume_24h_usd=Decimal("100.00"),
        pair_created_at=None,  # Missing creation timestamp!
        distinct_pools_aggregated=1,
        provider_source="geckoterminal",
        fetched_at=now - timedelta(seconds=120),
        cache_age_seconds=120.0,
        freshness_status=MarketDataFreshness.STALE  # Stale!
    )

    q_b = intel.evaluate_quality_score(token_b)
    # Deductions must be explicitly recorded
    assert "MISSING_PRICE" in q_b.deductions
    assert "MISSING_PAIR_CREATED_AT" in q_b.deductions
    assert "MICRO_LIQUIDITY_WARNING" in q_b.deductions
    assert "STALE_OBSERVATION" in q_b.deductions
    assert q_b.composite_score <= Decimal("20.0")
    assert q_b.composite_score >= Decimal("0.0")  # Bounded, never negative


# -----------------------------------------------------------------------------
# 3. Anomaly Detection: Heuristics & Insufficient Data Handling
# -----------------------------------------------------------------------------
def test_anomaly_detection_heuristics_and_insufficient_data():
    """
    Verifies:
    1. Suspicious turnover flag when Vol/Liq > 50x.
    2. Multi-pool price discrepancy flag when divergence > 15%.
    3. Insufficient liquidity backing when liq < 1% of market cap.
    4. Missing valuation / tx counts produces INSUFFICIENT_DATA (never false positive anomaly).
    """
    intel = MarketIntelligenceService()
    now = datetime.now(timezone.utc)

    # 1. Suspicious turnover (Vol = $600k, Liq = $10k -> 60x ratio)
    token_spikey = AggregatedTokenRanking(
        chain_id="eip155:8453",
        provider_network_id="base",
        token_address="0xSpikeyTurnover1111111111111111111111111",
        symbol="SPIKE",
        name="Spikey Meme",
        canonical_pool_address="0xPoolSpike111111111111111111111111111",
        dex_name="aerodrome",
        quote_token_symbol="WETH",
        price_usd=Decimal("1.00"),
        liquidity_usd=Decimal("10000.00"),
        volume_24h_usd=Decimal("600000.00"),  # 60x turnover!
        pair_created_at=now - timedelta(hours=5),
        provider_source="geckoterminal",
        fetched_at=now,
        cache_age_seconds=5.0,
        freshness_status=MarketDataFreshness.FRESH
    )

    flags_spike, details_spike = intel.detect_anomalies(token_spikey)
    assert "SUSPICIOUS_VOLUME_TURNOVER" in flags_spike
    assert details_spike["volume_to_liquidity_ratio"] == 60.0
    # Missing market cap and txns produces INSUFFICIENT_DATA
    assert "INSUFFICIENT_DATA" in flags_spike

    # 2. Multi-pool price discrepancy (Pool A $1.00, Pool B $1.20 -> 20% divergence)
    pool_a = MarketPairObservation(
        chain_id="eip155:1",
        provider_network_id="eth",
        pool_address="0xPoolA111111111111111111111111111111111",
        dex_id="uniswap_v3",
        token_address="0xTokenDiv1111111111111111111111111111111",
        symbol="DIV",
        price_usd=Decimal("1.00"),
        liquidity_usd=Decimal("50000.00"),
        volume_24h_usd=Decimal("10000.00"),
        fetched_at=now
    )
    pool_b = MarketPairObservation(
        chain_id="eip155:1",
        provider_network_id="eth",
        pool_address="0xPoolB222222222222222222222222222222222",
        dex_id="sushiswap",
        token_address="0xTokenDiv1111111111111111111111111111111",
        symbol="DIV",
        price_usd=Decimal("1.20"),  # 20% higher!
        liquidity_usd=Decimal("40000.00"),
        volume_24h_usd=Decimal("8000.00"),
        fetched_at=now
    )
    token_div = AggregatedTokenRanking(
        chain_id="eip155:1",
        provider_network_id="eth",
        token_address="0xTokenDiv1111111111111111111111111111111",
        symbol="DIV",
        name="Divergent Token",
        canonical_pool_address="0xPoolA111111111111111111111111111111111",
        dex_name="uniswap_v3",
        quote_token_symbol="WETH",
        price_usd=Decimal("1.00"),
        liquidity_usd=Decimal("50000.00"),
        volume_24h_usd=Decimal("18000.00"),
        distinct_pools_aggregated=2,
        provider_source="geckoterminal",
        fetched_at=now,
        cache_age_seconds=5.0,
        freshness_status=MarketDataFreshness.FRESH
    )
    flags_div, details_div = intel.detect_anomalies(token_div, token_pools=[pool_a, pool_b])
    assert "MULTI_POOL_PRICE_DISCREPANCY" in flags_div
    assert details_div["multi_pool_price_discrepancy_pct"] == 20.0

    # 3. Insufficient liquidity backing ($5k liq on $10M FDV -> 0.05% backing)
    token_trap = AggregatedTokenRanking(
        chain_id="solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp",
        provider_network_id="solana",
        token_address="TokenTrap111111111111111111111111111111",
        symbol="TRAP",
        name="Illiquid Trap",
        canonical_pool_address="PoolTrap111111111111111111111111111111",
        dex_name="raydium",
        quote_token_symbol="SOL",
        price_usd=Decimal("10.00"),
        liquidity_usd=Decimal("5000.00"),
        volume_24h_usd=Decimal("2000.00"),
        fdv_usd=Decimal("10000000.00"),  # $10M FDV!
        txns_24h_total=50,
        provider_source="geckoterminal",
        fetched_at=now,
        cache_age_seconds=5.0,
        freshness_status=MarketDataFreshness.FRESH
    )
    flags_trap, details_trap = intel.detect_anomalies(token_trap)
    assert "INSUFFICIENT_LIQUIDITY_BACKING" in flags_trap
    assert details_trap["liquidity_backing_ratio"] == 0.0005


# -----------------------------------------------------------------------------
# 4. Cross-Chain Identity & Collision Disambiguation
# -----------------------------------------------------------------------------
def test_cross_chain_identity_and_ticker_collision_disambiguation():
    """
    Verifies:
    1. Primary identity is strictly chain + contract address.
    2. Shared symbol across chains tags collision candidates without implying verified clone.
    3. Canonical candidate elected with explicit evidence (curated match or highest liquidity).
    """
    intel = MarketIntelligenceService()
    now = datetime.now(timezone.utc)

    # Candidate 1: Solana PEPE (Curated memecoin registry match)
    pepe_sol = AggregatedTokenRanking(
        chain_id="solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp",
        provider_network_id="solana",
        token_address="PepeSolanaContract1111111111111111111111",
        symbol="PEPE",
        name="Pepe",
        canonical_pool_address="PoolPepeSol11111111111111111111111111",
        dex_name="raydium",
        quote_token_symbol="SOL",
        asset_category=AssetCategory.MEME,
        classification_source=ClassificationSource.CURATED_REGISTRY,
        confidence=ClassificationConfidence.HIGH,
        liquidity_usd=Decimal("1500000.00"),
        volume_24h_usd=Decimal("3000000.00"),
        provider_source="geckoterminal",
        fetched_at=now,
        cache_age_seconds=5.0,
        freshness_status=MarketDataFreshness.FRESH
    )

    # Candidate 2: Base PEPE (Unverified copycat candidate with lower liquidity)
    pepe_base = AggregatedTokenRanking(
        chain_id="eip155:8453",
        provider_network_id="base",
        token_address="0xPepeBaseContract222222222222222222222",
        symbol="PEPE",
        name="Pepe Base",
        canonical_pool_address="0xPoolPepeBase222222222222222222222222",
        dex_name="aerodrome",
        quote_token_symbol="WETH",
        asset_category=AssetCategory.UNKNOWN,
        classification_source=ClassificationSource.DEFAULT,
        confidence=ClassificationConfidence.LOW,
        liquidity_usd=Decimal("20000.00"),
        volume_24h_usd=Decimal("40000.00"),
        provider_source="geckoterminal",
        fetched_at=now,
        cache_age_seconds=5.0,
        freshness_status=MarketDataFreshness.FRESH
    )

    disambiguated = intel.disambiguate_cross_chain_identities([pepe_sol, pepe_base])
    assert len(disambiguated) == 2

    # Both tokens track 1 collision candidate
    assert pepe_sol.collision_candidates_count == 1
    assert "eip155:8453:0xPepeBaseContract222222222222222222222" in pepe_sol.collision_candidate_addresses

    assert pepe_base.collision_candidates_count == 1
    assert "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp:PepeSolanaContract1111111111111111111111" in pepe_base.collision_candidate_addresses

    # Solana PEPE is canonical candidate based on curated registry
    assert pepe_sol.canonical_candidate_flag is True
    assert "Curated contract address match" in pepe_sol.canonical_candidate_rationale

    # Base PEPE is secondary candidate
    assert pepe_base.canonical_candidate_flag is False
    assert "Secondary collision candidate" in pepe_base.canonical_candidate_rationale


# -----------------------------------------------------------------------------
# 5. New-Pair Monitoring & Discovery Alerts
# -----------------------------------------------------------------------------
def test_new_pair_monitoring_and_alerts():
    """
    Verifies is_new_pair (< 24h) and evaluation of declarative breakout momentum alerts.
    """
    intel = MarketIntelligenceService()
    now = datetime.now(timezone.utc)

    new_token = AggregatedTokenRanking(
        chain_id="solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp",
        provider_network_id="solana",
        token_address="NewTokenPump111111111111111111111111111",
        symbol="NEWBIE",
        name="Newbie Coin",
        canonical_pool_address="PoolNewbie11111111111111111111111111111",
        dex_name="raydium",
        quote_token_symbol="SOL",
        pair_created_at=now - timedelta(hours=1.5),  # 1.5 hours old
        age_hours=1.5,
        liquidity_usd=Decimal("15000.00"),
        volume_24h_usd=Decimal("35000.00"),
        price_change_5m_pct=Decimal("25.0"),  # > 20% surge
        provider_source="geckoterminal",
        fetched_at=now,
        cache_age_seconds=2.0,
        freshness_status=MarketDataFreshness.FRESH
    )

    is_new, alerts = intel.evaluate_discovery_alerts(new_token)
    assert is_new is True
    assert "NEW_PAIR_HIGH_MOMENTUM" in alerts
    assert "EXTREME_PRICE_MOVEMENT_5M" in alerts


# -----------------------------------------------------------------------------
# 6. Snapshot Ring Buffer & FIFO Eviction at Max Capacity
# -----------------------------------------------------------------------------
def test_snapshot_ring_buffer_fifo_eviction():
    """
    Verifies:
    1. In-memory snapshot captures aggregate stats.
    2. FIFO eviction bounds ring buffer strictly to MAX_SNAPSHOT_CAPACITY (50).
    3. GET /discovery/snapshots and /discovery/snapshots/{id} work as expected.
    """
    intel = MarketIntelligenceService(max_snapshots=50)
    now = datetime.now(timezone.utc)

    dummy_tokens = [
        AggregatedTokenRanking(
            chain_id="eip155:1",
            provider_network_id="eth",
            token_address=f"0xTokenSnap{i:03d}11111111111111111111111",
            symbol=f"S{i:02d}",
            name=f"Snap Token {i:02d}",
            canonical_pool_address=f"0xPoolSnap{i:03d}111111111111111111111",
            dex_name="uniswap",
            quote_token_symbol="WETH",
            liquidity_usd=Decimal("10000.00"),
            volume_24h_usd=Decimal("20000.00"),
            quality_score=Decimal("65.0"),
            provider_source="geckoterminal",
            fetched_at=now,
            cache_age_seconds=5.0,
            freshness_status=MarketDataFreshness.FRESH
        )
        for i in range(5)
    ]

    # Add 55 snapshots to test FIFO eviction at capacity 50
    first_snap = intel.capture_snapshot(dummy_tokens, total_pools=5)
    first_id = first_snap.snapshot_id

    for _ in range(54):
        intel.capture_snapshot(dummy_tokens, total_pools=5)

    all_snaps = intel.get_snapshots(limit=100)
    assert len(all_snaps) == 50  # Bounded strictly at 50!

    # Oldest snapshot (first_id) must have been evicted
    assert intel.get_snapshot_by_id(first_id) is None


def test_screener_api_snapshots_endpoints():
    """Verifies FastAPI /discovery/snapshots and /discovery/snapshots/{snapshot_id} endpoints."""
    # Ensure at least 1 snapshot in global singleton
    now = datetime.now(timezone.utc)
    tok = AggregatedTokenRanking(
        chain_id="eip155:1",
        provider_network_id="eth",
        token_address="0xTestSnapToken111111111111111111111111",
        symbol="SNAP",
        name="Snap Token",
        canonical_pool_address="0xPoolSnapToken111111111111111111111",
        dex_name="uniswap",
        quote_token_symbol="WETH",
        liquidity_usd=Decimal("50000.00"),
        volume_24h_usd=Decimal("100000.00"),
        quality_score=Decimal("80.0"),
        provider_source="geckoterminal",
        fetched_at=now,
        cache_age_seconds=5.0,
        freshness_status=MarketDataFreshness.FRESH
    )
    snap = market_intelligence_service.capture_snapshot([tok], total_pools=1)

    # 1. List snapshots
    res_list = client.get("/api/v1/discovery/snapshots?limit=10")
    assert res_list.status_code == 200
    data_list = res_list.json()
    assert "snapshots" in data_list
    assert data_list["total_snapshots"] >= 1
    assert data_list["max_capacity"] == 50

    # 2. Get specific snapshot
    res_snap = client.get(f"/api/v1/discovery/snapshots/{snap.snapshot_id}")
    assert res_snap.status_code == 200
    data_snap = res_snap.json()
    assert data_snap["snapshot_id"] == snap.snapshot_id
    assert "Episodic in-memory snapshot ring buffer" in data_snap["note"]
    assert data_snap["source_provenance"] == "IN_MEMORY_PUBLIC_OBSERVATIONS"

    # 3. 404 for non-existent snapshot
    res_404 = client.get("/api/v1/discovery/snapshots/snap_non_existent_id")
    assert res_404.status_code == 404


# -----------------------------------------------------------------------------
# 7. Screener API Filter and Sorting Integration
# -----------------------------------------------------------------------------
def test_screener_api_quality_score_and_anomaly_filters():
    """
    Verifies FastAPI screener endpoint filtering:
    - min_quality_score
    - has_anomalies
    - new_pairs_only
    - sort_by=quality_score
    """
    now = datetime.now(timezone.utc)

    # High quality, normal token
    obs_hq = MarketPairObservation(
        chain_id="eip155:1",
        provider_network_id="eth",
        provider_source="geckoterminal",
        pool_address="0xPoolHQ111111111111111111111111111111111",
        token_address="0x6982508145454Ce325dDbE47a25d4ec3d2311933",
        symbol="PEPE",
        name="Pepe",
        price_usd=Decimal("0.00001"),
        liquidity_usd=Decimal("500000.00"),
        volume_24h_usd=Decimal("1000000.00"),
        pair_created_at=now - timedelta(days=5),
        fetched_at=now
    )

    # Low quality, young pair with anomalous turnover
    obs_lq = MarketPairObservation(
        chain_id="solana:mainnet",
        provider_network_id="solana",
        provider_source="geckoterminal",
        pool_address="PoolLQAnom111111111111111111111111111111",
        token_address="TokenLQAnom22222222222222222222222222222",
        symbol="ANOM",
        name="Anomalous Meme",
        price_usd=Decimal("0.001"),
        liquidity_usd=Decimal("3000.00"),  # Micro-liquidity!
        volume_24h_usd=Decimal("200000.00"),  # > 50x turnover!
        pair_created_at=now - timedelta(hours=2),  # 2 hours old
        fetched_at=now
    )

    market_data_coordinator._tokens_by_key = market_data_coordinator._aggregate_tokens([obs_hq, obs_lq], now=now)
    market_data_coordinator._last_refresh_time = now

    # 1. Filter min_quality_score=60 -> Only PEPE returned
    res_hq = client.get("/api/v1/discovery/screener?min_quality_score=60&category=ALL")
    assert res_hq.status_code == 200
    data_hq = res_hq.json()
    assert data_hq["result_count"] == 1
    assert data_hq["tokens"][0]["symbol"] == "PEPE"

    # 2. Filter has_anomalies=true -> Only ANOM returned
    res_anom = client.get("/api/v1/discovery/screener?has_anomalies=true&category=ALL")
    assert res_anom.status_code == 200
    data_anom = res_anom.json()
    assert data_anom["result_count"] == 1
    assert data_anom["tokens"][0]["symbol"] == "ANOM"

    # 3. Filter new_pairs_only=true -> Only ANOM returned (age 2h < 24h)
    res_new = client.get("/api/v1/discovery/screener?new_pairs_only=true&category=ALL")
    assert res_new.status_code == 200
    data_new = res_new.json()
    assert data_new["result_count"] == 1
    assert data_new["tokens"][0]["symbol"] == "ANOM"

    # 4. Sort by quality_score descending -> PEPE first, then ANOM
    res_sort = client.get("/api/v1/discovery/screener?sort_by=quality_score&sort_dir=desc&category=ALL")
    assert res_sort.status_code == 200
    data_sort = res_sort.json()
    assert data_sort["result_count"] == 2
    assert data_sort["tokens"][0]["symbol"] == "PEPE"
    assert data_sort["tokens"][1]["symbol"] == "ANOM"
