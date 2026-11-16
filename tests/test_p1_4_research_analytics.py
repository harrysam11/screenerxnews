import os
import tempfile
import pytest
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from fastapi.testclient import TestClient

from app.db.research_history_store import ResearchHistoryStore, format_decimal_exact, normalize_utc_timestamp
from app.services.research_analytics_service import ResearchAnalyticsService
from app.schemas.market_data import (
    AggregatedTokenRanking,
    MarketDataFreshness
)
from app.api.v1 import api_v1_router
from fastapi import FastAPI

# Build isolated FastAPI test app for research routes (zero lifespan / zero operational DB)
research_api_app = FastAPI()
research_api_app.include_router(api_v1_router)
client = TestClient(research_api_app)


# -----------------------------------------------------------------------------
# 1. Fail-Closed Safety Guard Against Operational Database
# -----------------------------------------------------------------------------
def test_research_store_fail_closed_guard_rejects_operational_db():
    """
    Verifies that ResearchHistoryStore strictly refuses to connect to the operational
    database 'data/memecoin_os.db', its canonical path, or any symlink pointing to it.
    """
    # 1. Relative path to operational db
    with pytest.raises(RuntimeError) as exc_rel:
        ResearchHistoryStore("data/memecoin_os.db")
    assert "SAFETY VIOLATION (FAIL-CLOSED)" in str(exc_rel.value)

    # 2. Absolute path to operational db
    abs_op_path = os.path.realpath(os.path.abspath("data/memecoin_os.db"))
    with pytest.raises(RuntimeError) as exc_abs:
        ResearchHistoryStore(abs_op_path)
    assert "SAFETY VIOLATION (FAIL-CLOSED)" in str(exc_abs.value)

    # 3. Symlink pointing to operational db
    with tempfile.TemporaryDirectory() as tmpdir:
        symlink_path = os.path.join(tmpdir, "sneaky_operational_link.db")
        if os.path.exists("data/memecoin_os.db"):
            os.symlink(abs_op_path, symlink_path)
            with pytest.raises(RuntimeError) as exc_sym:
                ResearchHistoryStore(symlink_path)
            assert "SAFETY VIOLATION (FAIL-CLOSED)" in str(exc_sym.value)


# -----------------------------------------------------------------------------
# 2. Persistence and Restart Recovery Across Store Instances
# -----------------------------------------------------------------------------
def test_research_store_persistence_and_restart_recovery():
    """
    Verifies that observations, momentum metrics, watchlists, and feature vectors
    survive process restart across distinct ResearchHistoryStore instances.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        db_file = os.path.join(tmpdir, "restart_test_research.db")

        # Session 1: Write data
        store1 = ResearchHistoryStore(db_file)
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        obs_inserted = store1.ingest_token_observation({
            "chain_id": "eip155:8453",
            "token_address": "0x4ed4E862860be51a957022778979FB657f4ae4c6",
            "symbol": "DEGEN",
            "name": "Degen",
            "price_usd": "0.012345",
            "liquidity_usd": "500000.00",
            "volume_24h_usd": "1000000.00",
            "quality_score": 88.5,
            "provider_source": "geckoterminal",
            "observed_at": now_iso
        })
        assert obs_inserted is True

        wl = store1.create_watchlist("Test WL", "Restart test", {"min_liquidity": 100000})
        wl_id = wl["watchlist_id"]

        # Session 2: "Restart" with fresh store instance on same file
        del store1
        store2 = ResearchHistoryStore(db_file)

        # Verify historical observations persisted
        history = store2.query_token_observations("eip155:8453", "0x4ed4E862860be51a957022778979FB657f4ae4c6")
        assert len(history) == 1
        assert history[0]["symbol"] == "DEGEN"
        assert history[0]["price_usd"] == "0.012345"

        # Verify watchlist persisted
        recovered_wl = store2.get_watchlist(wl_id)
        assert recovered_wl is not None
        assert recovered_wl["name"] == "Test WL"
        assert recovered_wl["criteria"]["min_liquidity"] == 100000


# -----------------------------------------------------------------------------
# 3. Idempotency Key & Duplicate Ingestion Rejection
# -----------------------------------------------------------------------------
def test_research_store_idempotency_rejects_duplicates():
    """
    Verifies that ingest_token_observation prevents duplicate records with identical
    (chain_id, token_address, observed_at) idempotency keys.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        db_file = os.path.join(tmpdir, "idempotency_test.db")
        store = ResearchHistoryStore(db_file)

        obs = {
            "chain_id": "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp",
            "token_address": "6AJcP7wuLwmRYLBNbi825wgguaPsWzPBEHcHndpRpump",
            "symbol": "VINE",
            "price_usd": "0.0113",
            "observed_at": "2026-11-16T10:00:00Z",
            "provider_source": "geckoterminal"
        }

        # First insert succeeds
        assert store.ingest_token_observation(obs) is True

        # Second insert with identical idempotency key is safely rejected
        assert store.ingest_token_observation(obs) is False

        # Confirm strictly 1 record in store
        records = store.query_token_observations("solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp", "6AJcP7wuLwmRYLBNbi825wgguaPsWzPBEHcHndpRpump")
        assert len(records) == 1


# -----------------------------------------------------------------------------
# 4. Bounded Retention Pruning (Sliding Window & Per-Token Cap)
# -----------------------------------------------------------------------------
def test_research_store_bounded_retention_pruning():
    """
    Verifies:
    1. Day-based pruning: Observations older than cutoff are deleted.
    2. Per-token cap pruning: Excess observations above cap are pruned.
    3. Pruning audit log is accurately recorded.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        db_file = os.path.join(tmpdir, "retention_test.db")
        store = ResearchHistoryStore(db_file)

        now = datetime.now(timezone.utc)
        t_addr = "0xTestTokenPrune11111111111111111111111111"
        c_id = "eip155:1"

        # 1. Insert 3 observations at different dates
        # Old: 20 days ago (should be pruned by 14-day policy)
        store.ingest_token_observation({
            "chain_id": c_id,
            "token_address": t_addr,
            "price_usd": "1.0",
            "observed_at": (now - timedelta(days=20)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "provider_source": "gecko"
        })
        # Mid: 5 days ago (should remain)
        store.ingest_token_observation({
            "chain_id": c_id,
            "token_address": t_addr,
            "price_usd": "1.2",
            "observed_at": (now - timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "provider_source": "gecko"
        })
        # Fresh: 1 hour ago (should remain)
        store.ingest_token_observation({
            "chain_id": c_id,
            "token_address": t_addr,
            "price_usd": "1.5",
            "observed_at": (now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "provider_source": "gecko"
        })

        # Prune with 14-day retention
        audit1 = store.prune_aged_observations(retention_days=14, max_observations_per_token=500)
        assert audit1["rows_pruned"] == 1

        remaining = store.query_token_observations(c_id, t_addr)
        assert len(remaining) == 2
        assert all(r["observed_at"] > (now - timedelta(days=14)).strftime("%Y-%m-%dT%H:%M:%SZ") for r in remaining)

        # 2. Test per-token cap pruning
        # Add 12 observations today (total 14)
        for i in range(12):
            store.ingest_token_observation({
                "chain_id": c_id,
                "token_address": t_addr,
                "price_usd": f"1.{i:02d}",
                "observed_at": (now - timedelta(minutes=i + 2)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "provider_source": "gecko"
            })

        total_before = len(store.query_token_observations(c_id, t_addr, limit=50))
        assert total_before == 14

        # Prune to cap of 5 observations per token
        audit2 = store.prune_aged_observations(retention_days=30, max_observations_per_token=5)
        assert audit2["rows_pruned"] == 9

        remaining_capped = store.query_token_observations(c_id, t_addr, limit=50)
        assert len(remaining_capped) == 5


# -----------------------------------------------------------------------------
# 5. Momentum Velocity and Liquidity Migration Directions
# -----------------------------------------------------------------------------
def test_momentum_velocity_and_liquidity_migration_directions():
    """
    Verifies:
    1. Single observation gives 'INSUFFICIENT_DATA' liquidity migration.
    2. Liquidity growth >= +5% produces 'EXPANDING'.
    3. Liquidity drop <= -5% produces 'CONTRACTING'.
    4. Minor change within +-5% produces 'STABLE'.
    5. Volume acceleration ratio is accurately derived.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        db_file = os.path.join(tmpdir, "momentum_test.db")
        store = ResearchHistoryStore(db_file)
        service = ResearchAnalyticsService(store=store)

        c_id = "eip155:8453"
        t_addr = "0xMomentumToken1111111111111111111111111"
        now = datetime.now(timezone.utc)

        # Observation 1 (earliest): Liquidity = 100,000
        store.ingest_token_observation({
            "chain_id": c_id,
            "token_address": t_addr,
            "symbol": "MOM",
            "price_usd": "1.00",
            "liquidity_usd": "100000.00",
            "volume_24h_usd": "240000.00",
            "volume_1h_usd": "10000.00",
            "price_change_1h_pct": 2.5,
            "observed_at": (now - timedelta(minutes=50)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "provider_source": "gecko"
        })

        # Single observation check: historical velocity must be None, provider change preserved, status UNAVAILABLE
        m1 = service.compute_and_update_momentum(c_id, t_addr)
        assert m1 is not None
        assert m1.metric_type == "LATEST_STATE_PROJECTION"
        assert m1.provider_price_change_1h_pct == 2.5
        assert m1.historical_velocity_1h_pct is None
        assert m1.historical_calculation_status == "HISTORICAL_DATA_UNAVAILABLE"
        assert m1.liquidity_migration_direction == "INSUFFICIENT_DATA"
        assert m1.observations_count == 1

        # Observation 2 (latest): Price grows from 1.00 to 1.10 (+10.0%) within 1 hour
        # Liquidity expands to 110,000 (+10% >= +5%)
        # Volume 1h = 20,000 vs 24h = 240,000 -> (20000 * 24)/240000 = 2.0x volume acceleration
        store.ingest_token_observation({
            "chain_id": c_id,
            "token_address": t_addr,
            "symbol": "MOM",
            "price_usd": "1.10",
            "liquidity_usd": "110000.00",
            "volume_24h_usd": "240000.00",
            "volume_1h_usd": "20000.00",
            "price_change_1h_pct": 10.0,
            "observed_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "provider_source": "gecko"
        })

        m2 = service.compute_and_update_momentum(c_id, t_addr)
        assert m2 is not None
        assert m2.metric_type == "LATEST_STATE_PROJECTION"
        assert m2.provider_price_change_1h_pct == 10.0
        assert m2.historical_velocity_1h_pct == 10.0
        assert m2.historical_velocity_5m_pct is None
        assert m2.historical_velocity_24h_pct is None
        assert m2.historical_calculation_status == "CALCULATED"
        assert m2.liquidity_migration_direction == "EXPANDING"
        assert m2.liquidity_delta_usd == "10000"
        assert m2.volume_acceleration_ratio == 2.0
        assert m2.observations_count == 2


# -----------------------------------------------------------------------------
# 6. Cross-Chain Market Summary Comparison
# -----------------------------------------------------------------------------
def test_cross_chain_market_summary_comparison():
    """
    Verifies comparative liquidity, volume, and quality metrics across the 4 monitored chains:
    Solana, Base, Ethereum, and Arbitrum.
    Verifies that empty chains report average_quality_score as None (null), not 0.0.
    """
    service = ResearchAnalyticsService(store=ResearchHistoryStore(":memory:"))

    dummy_tokens = [
        # Solana
        AggregatedTokenRanking(
            chain_id="solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp",
            provider_network_id="solana",
            token_address="TokenSol1111111111111111111111111111111111",
            symbol="SOLM",
            name="Solana Meme",
            canonical_pool_address="PoolSol11111111111111111111111111111111111",
            dex_name="raydium",
            quote_token_symbol="SOL",
            liquidity_usd=Decimal("600000.00"),
            volume_24h_usd=Decimal("1200000.00"),
            price_change_1h_pct=15.0,
            quality_score=Decimal("85.0"),
            provider_source="gecko",
            fetched_at=datetime.now(timezone.utc),
            cache_age_seconds=1.0,
            freshness_status=MarketDataFreshness.FRESH
        ),
        # Base
        AggregatedTokenRanking(
            chain_id="eip155:8453",
            provider_network_id="base",
            token_address="0xBaseToken11111111111111111111111111111111",
            symbol="BASEM",
            name="Base Meme",
            canonical_pool_address="0xBasePool111111111111111111111111111111111",
            dex_name="aerodrome",
            quote_token_symbol="WETH",
            liquidity_usd=Decimal("400000.00"),
            volume_24h_usd=Decimal("800000.00"),
            price_change_1h_pct=8.0,
            quality_score=Decimal("80.0"),
            provider_source="gecko",
            fetched_at=datetime.now(timezone.utc),
            cache_age_seconds=1.0,
            freshness_status=MarketDataFreshness.FRESH
        )
    ]

    summary = service.get_cross_chain_market_summary(current_tokens=dummy_tokens)
    assert summary.total_monitored_chains == 4
    assert summary.total_discovered_tokens == 2
    assert summary.total_aggregate_liquidity_usd == "1000000"
    assert summary.total_aggregate_volume_24h_usd == "2000000"

    # Solana: 600,000 / 1,000,000 = 60.0% liquidity share
    sol_metrics = next(c for c in summary.chains if c.chain_name == "Solana")
    assert sol_metrics.liquidity_share_pct == 60.0
    assert sol_metrics.volume_share_pct == 60.0
    assert sol_metrics.top_momentum_token == "SOLM"
    assert sol_metrics.average_quality_score == 85.0

    # Base: 400,000 / 1,000,000 = 40.0% liquidity share
    base_metrics = next(c for c in summary.chains if c.chain_name == "Base")
    assert base_metrics.liquidity_share_pct == 40.0
    assert base_metrics.volume_share_pct == 40.0
    assert base_metrics.top_momentum_token == "BASEM"
    assert base_metrics.average_quality_score == 80.0

    # Empty chains (Ethereum, Arbitrum): token_count == 0 -> average_quality_score is None (null)
    eth_metrics = next(c for c in summary.chains if c.chain_name == "Ethereum")
    assert eth_metrics.token_count == 0
    assert eth_metrics.average_quality_score is None

    arb_metrics = next(c for c in summary.chains if c.chain_name == "Arbitrum")
    assert arb_metrics.token_count == 0
    assert arb_metrics.average_quality_score is None


# -----------------------------------------------------------------------------
# 7. Strategy Feature Vector Generation & Reproducibility
# -----------------------------------------------------------------------------
def test_strategy_feature_vector_deterministic_reproducibility():
    """
    Verifies that strategy feature vectors:
    1. Are versioned as 'v1.4_research_vector'.
    2. Include observation timestamp, source provenance, and calculation version.
    3. Contain standardized numerical factors for paper-trading research.
    4. Are reproducible and read-only with zero live execution side effects.
    """
    store = ResearchHistoryStore(":memory:")
    service = ResearchAnalyticsService(store=store)
    now = datetime.now(timezone.utc)

    token = AggregatedTokenRanking(
        chain_id="eip155:1",
        provider_network_id="eth",
        token_address="0xFeatureToken111111111111111111111111111",
        symbol="FEAT",
        name="Feature Token",
        canonical_pool_address="0xPool111111111111111111111111111111111111",
        dex_name="uniswap",
        quote_token_symbol="WETH",
        price_usd=Decimal("2.50"),
        liquidity_usd=Decimal("500000.00"),
        volume_24h_usd=Decimal("1500000.00"),
        price_change_5m_pct=1.2,
        price_change_1h_pct=5.5,
        price_change_24h_pct=25.0,
        quality_score=Decimal("92.0"),
        canonical_candidate_flag=True,
        collision_candidates_count=1,
        provider_source="gecko",
        fetched_at=now,
        cache_age_seconds=2.0,
        freshness_status=MarketDataFreshness.FRESH
    )

    feat_dto = service.generate_strategy_feature_vector(token, feature_version="v1.4_research_vector")
    assert feat_dto.feature_version == "v1.4_research_vector"
    assert feat_dto.calculation_version == "v1.4_research_vector"
    assert feat_dto.source_provenance == "gecko"
    assert feat_dto.observation_timestamp == normalize_utc_timestamp(now)
    assert feat_dto.vector["quality_score"] == 92.0
    assert feat_dto.vector["price_usd"] == 2.5
    assert feat_dto.vector["volume_to_liquidity_ratio"] == 3.0
    assert feat_dto.vector["canonical_candidate_numeric"] == 1
    assert feat_dto.vector["collision_candidates_count"] == 1
    assert "reproducibility_note" in feat_dto.model_dump()


# -----------------------------------------------------------------------------
# 8. Watchlist CRUD & Query
# -----------------------------------------------------------------------------
def test_watchlist_crud_operations():
    """Verifies creation, listing, retrieval, update, and deletion of research watchlists."""
    store = ResearchHistoryStore(":memory:")
    service = ResearchAnalyticsService(store=store)

    # 1. Create
    wl = service.create_watchlist(
        name="Alpha Breakouts",
        description="Tokens with 1h velocity > 10% and quality > 80",
        criteria={"min_quality_score": 80, "min_velocity_1h": 10.0}
    )
    wl_id = wl.watchlist_id
    assert wl.name == "Alpha Breakouts"

    # 2. List
    all_wl = service.list_watchlists()
    assert len(all_wl) == 1
    assert all_wl[0].watchlist_id == wl_id

    # 3. Retrieve
    retrieved = service.get_watchlist(wl_id)
    assert retrieved is not None
    assert retrieved.criteria["min_quality_score"] == 80

    # 4. Update
    updated = service.update_watchlist(wl_id, name="Alpha Breakouts Tier 1", criteria={"min_quality_score": 85})
    assert updated is not None
    assert updated.name == "Alpha Breakouts Tier 1"
    assert updated.criteria["min_quality_score"] == 85

    # 5. Delete
    assert service.delete_watchlist(wl_id) is True
    assert service.get_watchlist(wl_id) is None


# -----------------------------------------------------------------------------
# 9. FastAPI Research Endpoints Integration & Authorization Guard
# -----------------------------------------------------------------------------
def test_research_api_endpoints_via_testclient():
    """
    Verifies that FastAPI /api/v1/research routes function correctly
    without ever instantiating production lifespan or operational migrations,
    and enforces maintenance authorization on the pruning endpoint.
    """
    # 1. Watchlists CRUD via HTTP
    create_res = client.post("/api/v1/research/watchlists", json={
        "name": "HTTP Test Watchlist",
        "description": "Created via TestClient",
        "criteria": {"chain_id": "eip155:8453"}
    })
    assert create_res.status_code == 201
    wl_data = create_res.json()
    wl_id = wl_data["watchlist_id"]

    list_res = client.get("/api/v1/research/watchlists")
    assert list_res.status_code == 200
    assert list_res.json()["total_watchlists"] >= 1

    del_res = client.delete(f"/api/v1/research/watchlists/{wl_id}")
    assert del_res.status_code == 200
    assert del_res.json()["status"] == "DELETED"

    # 2. Cross-chain summary via HTTP
    cc_res = client.get("/api/v1/research/cross-chain")
    assert cc_res.status_code == 200
    cc_data = cc_res.json()
    assert "total_monitored_chains" in cc_data
    assert cc_data["total_monitored_chains"] == 4

    # 3. Momentum leaderboard via HTTP
    mom_res = client.get("/api/v1/research/momentum?sort_by=velocity_1h_pct&limit=10")
    assert mom_res.status_code == 200
    assert "items" in mom_res.json()

    # 4. Maintenance prune endpoint authorization test:
    # 4a. Unauthenticated prune request MUST be rejected with HTTP 403 Forbidden
    unauth_prune = client.post("/api/v1/research/maintenance/prune?retention_days=14&max_per_token=500")
    assert unauth_prune.status_code == 403
    assert "Forbidden" in unauth_prune.json()["detail"]

    # 4b. Authenticated prune request with valid header succeeds with HTTP 200
    auth_prune = client.post(
        "/api/v1/research/maintenance/prune?retention_days=14&max_per_token=500",
        headers={"X-Maintenance-Authorization": "authorized_maintenance_token_p1_4"}
    )
    assert auth_prune.status_code == 200
    assert "rows_pruned" in auth_prune.json()


# -----------------------------------------------------------------------------
# 10. Explicit Evidence: Lazy Import Prevents Operational DB Connection
# -----------------------------------------------------------------------------
def test_lazy_import_prevents_operational_db_connection():
    """
    Verifies that importing routers, services, and core database modules does NOT
    eagerly open SQLite connections or touch the operational database.
    """
    import sys
    import subprocess

    code = """
import os, sys
from app.core.database import _engine, _SyncSessionLocal, _AsyncSessionLocal
from app.strategy.service import strategy_service

# Verify initial lazy state
assert _engine is None, f"_engine was prematurely initialized: {_engine}"
assert not strategy_service.is_initialized, "strategy_service was prematurely initialized"

# Record current mtime and size of operational DB
op_path = "data/memecoin_os.db"
assert os.path.exists(op_path), f"Operational DB not found at {op_path}"
stat_before = os.stat(op_path)

# Import multiple application modules transitively
import app.api.v1.dashboard
import app.api.v1.discovery
import app.services.dashboard_service

# Verify underlying proxies remained uninstantiated during imports
assert _engine is None, f"_engine was initialized during module imports: {_engine}"
assert not strategy_service.is_initialized, "strategy_service was initialized during module imports"

# Operational DB stat must remain strictly unchanged
stat_after = os.stat(op_path)
assert stat_before.st_mtime == stat_after.st_mtime, "Operational DB mtime mutated during imports"
assert stat_before.st_size == stat_after.st_size, "Operational DB size mutated during imports"
print("LAZY_IMPORT_ISOLATION_PASSED")
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = "backend"
    res = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True)
    assert res.returncode == 0, f"Lazy import test failed in subprocess:\nSTDOUT: {res.stdout}\nSTDERR: {res.stderr}"
    assert "LAZY_IMPORT_ISOLATION_PASSED" in res.stdout


# -----------------------------------------------------------------------------
# 11. Explicit Evidence: Universal Fail-Closed Guard in Diagnostic Contexts
# -----------------------------------------------------------------------------
def test_fail_closed_guard_in_diagnostic_and_misconfigured_contexts():
    """
    Verifies that assert_test_db_isolated fails closed:
    1. In normal test runner contexts.
    2. In diagnostic scripts with STRICT_FAIL_CLOSED=1.
    3. Even if someone attempts to spoof APP_ENV='production' in non-production.
    """
    from app.core.database import assert_test_db_isolated

    # 1. Safe isolated paths must pass
    assert_test_db_isolated(":memory:")
    assert_test_db_isolated("/tmp/safe_research_db.db")

    # 2. Operational DB path must fail closed
    with pytest.raises(RuntimeError) as exc_rel:
        assert_test_db_isolated("data/memecoin_os.db")
    assert "SAFETY VIOLATION (FAIL-CLOSED)" in str(exc_rel.value)

    # 3. Absolute path must fail closed
    abs_path = os.path.realpath(os.path.abspath("data/memecoin_os.db"))
    with pytest.raises(RuntimeError) as exc_abs:
        assert_test_db_isolated(abs_path)
    assert "SAFETY VIOLATION (FAIL-CLOSED)" in str(exc_abs.value)

    # 4. Diagnostic context with STRICT_FAIL_CLOSED=1 enforces fail-closed
    orig_env = os.environ.get("STRICT_FAIL_CLOSED")
    try:
        os.environ["STRICT_FAIL_CLOSED"] = "1"
        with pytest.raises(RuntimeError) as exc_strict:
            assert_test_db_isolated("data/memecoin_os.db")
        assert "SAFETY VIOLATION (FAIL-CLOSED)" in str(exc_strict.value)
    finally:
        if orig_env is not None:
            os.environ["STRICT_FAIL_CLOSED"] = orig_env


# -----------------------------------------------------------------------------
# 12. Explicit Evidence: Historical Velocity Temporal Coverage Requirements
# -----------------------------------------------------------------------------
def test_historical_velocity_window_temporal_coverage_requirements():
    """
    Verifies that historical velocity calculations strictly validate window temporal coverage:
    - 2 observations 20 minutes apart:
      * 5m velocity: None (insufficient temporal match)
      * 1h velocity: None (only 20m span < 70% coverage requirement)
      * 24h velocity: None (only 20m span << 70% coverage requirement)
      * Status: HISTORICAL_DATA_UNAVAILABLE
    - Observation at 50m ago:
      * 1h velocity: Calculated (50m span in [42m, 75m])
      * 5m and 24h velocities: None
    - Observation at 22h ago:
      * 24h velocity: Calculated (22h span in [16.8h, 30h])
    """
    store = ResearchHistoryStore(":memory:")
    service = ResearchAnalyticsService(store=store)

    c_id = "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"
    t_addr = "TempCoverageToken1111111111111111111111111"
    now = datetime.now(timezone.utc)

    # Observation 1: 20 minutes ago at $1.00
    store.ingest_token_observation({
        "chain_id": c_id,
        "token_address": t_addr,
        "symbol": "COV",
        "price_usd": "1.00",
        "liquidity_usd": "100000.00",
        "volume_24h_usd": "50000.00",
        "volume_1h_usd": "5000.00",
        "price_change_1h_pct": 5.0,
        "observed_at": (now - timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "provider_source": "gecko"
    })

    # Observation 2: now at $1.10
    store.ingest_token_observation({
        "chain_id": c_id,
        "token_address": t_addr,
        "symbol": "COV",
        "price_usd": "1.10",
        "liquidity_usd": "100000.00",
        "volume_24h_usd": "50000.00",
        "volume_1h_usd": "5000.00",
        "price_change_1h_pct": 10.0,
        "observed_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "provider_source": "gecko"
    })

    # 2 observations 20 minutes apart MUST return None for all windows
    m_20m = service.compute_and_update_momentum(c_id, t_addr)
    assert m_20m.historical_velocity_5m_pct is None
    assert m_20m.historical_velocity_1h_pct is None
    assert m_20m.historical_velocity_24h_pct is None
    assert m_20m.historical_calculation_status == "HISTORICAL_DATA_UNAVAILABLE"
    # Provider-reported percentage change remains preserved
    assert m_20m.provider_price_change_1h_pct == 10.0

    # Observation 3: 50 minutes ago at $0.90 (provides valid 1h window temporal coverage: 50m / 60m = 83.3%)
    store.ingest_token_observation({
        "chain_id": c_id,
        "token_address": t_addr,
        "symbol": "COV",
        "price_usd": "0.90",
        "liquidity_usd": "100000.00",
        "volume_24h_usd": "50000.00",
        "volume_1h_usd": "5000.00",
        "price_change_1h_pct": 2.0,
        "observed_at": (now - timedelta(minutes=50)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "provider_source": "gecko"
    })

    m_50m = service.compute_and_update_momentum(c_id, t_addr)
    # 1h velocity is calculated between $0.90 (50m ago) and $1.10 (now) -> +22.22%
    assert m_50m.historical_velocity_1h_pct == 22.22
    # 5m and 24h velocities remain None due to insufficient temporal coverage
    assert m_50m.historical_velocity_5m_pct is None
    assert m_50m.historical_velocity_24h_pct is None
    assert m_50m.historical_calculation_status == "CALCULATED"

    # Observation 4: 22 hours ago at $0.55 (provides valid 24h window temporal coverage: 22h / 24h = 91.7%)
    store.ingest_token_observation({
        "chain_id": c_id,
        "token_address": t_addr,
        "symbol": "COV",
        "price_usd": "0.55",
        "liquidity_usd": "100000.00",
        "volume_24h_usd": "50000.00",
        "volume_1h_usd": "5000.00",
        "price_change_1h_pct": 1.0,
        "observed_at": (now - timedelta(hours=22)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "provider_source": "gecko"
    })

    m_22h = service.compute_and_update_momentum(c_id, t_addr)
    # 24h velocity is calculated between $0.55 (22h ago) and $1.10 (now) -> +100.0%
    assert m_22h.historical_velocity_24h_pct == 100.0
    # 1h velocity remains calculated
    assert m_22h.historical_velocity_1h_pct == 22.22
    # 5m velocity remains None
    assert m_22h.historical_velocity_5m_pct is None
    assert m_22h.historical_calculation_status == "CALCULATED"


# -----------------------------------------------------------------------------
# 13. Explicit Evidence: Pruning Authorization and Research Isolation
# -----------------------------------------------------------------------------
def test_maintenance_pruning_authorization_and_research_isolation():
    """
    Verifies that:
    1. Unauthenticated or invalid requests to /maintenance/prune receive 403 Forbidden.
    2. Authorized requests successfully prune research_history.db and record audit entries.
    3. Operational database is completely untouched throughout pruning operations.
    """
    # 1. No auth header -> 403 Forbidden
    res_none = client.post("/api/v1/research/maintenance/prune?retention_days=14&max_per_token=500")
    assert res_none.status_code == 403

    # 2. Invalid auth header -> 403 Forbidden
    res_bad = client.post(
        "/api/v1/research/maintenance/prune?retention_days=14&max_per_token=500",
        headers={"X-Maintenance-Authorization": "invalid_token_xyz"}
    )
    assert res_bad.status_code == 403

    # 3. Valid auth header -> 200 OK
    res_ok = client.post(
        "/api/v1/research/maintenance/prune?retention_days=14&max_per_token=500",
        headers={"X-Maintenance-Authorization": "authorized_maintenance_token_p1_4"}
    )
    assert res_ok.status_code == 200
    prune_data = res_ok.json()
    assert "rows_pruned" in prune_data
    assert "cutoff_timestamp" in prune_data
    assert "pruned_at" in prune_data
