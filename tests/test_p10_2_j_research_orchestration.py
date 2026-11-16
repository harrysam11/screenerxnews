import pytest
import sqlite3
import uuid
import json
import time
from decimal import Decimal
from datetime import datetime, timezone, timedelta

from app.schemas.contracts import TriState
from app.core.config import settings
from app.core.chains import normalize_chain_id
from app.core.network import OfflineModeBlockedException
from app.core.chain_capabilities import ChainCapability, CapabilityStatus, chain_capability_registry
from app.services.provider_manager import ProviderManager, CircuitState, ResponseClassification, ALL_12_CHAINS
from app.services.persistent_paper_service import PersistentPaperService, DecimalProvenanceSource
from app.services.ingestion_coordinator import IngestionCoordinator, IngestionCapabilityError
from app.services.candidate_scoring_service import CandidateResearchScorer

@pytest.fixture
def test_db_path(tmp_path):
    db_file = str(tmp_path / "test_research_j.db")
    from app.db.sqlite_migrator import SqliteMigrator
    migrator = SqliteMigrator(db_file)
    migrator.apply_migrations()
    return db_file

# =============================================================================
# Deliverable 1: Provider Reliability Manager Tests
# =============================================================================

def test_circuit_breaker_transitions_and_backoff(test_db_path):
    pm = ProviderManager(db_path=test_db_path)
    # Register provider with threshold of 3 failures
    prov = pm.register_provider(
        provider_id="test_rpc_1",
        name="Test RPC 1",
        provider_type="rpc",
        is_enabled=True,
        capabilities=["CHAIN_RPC"],
        chains=["solana:mainnet"],
        rate_limit_rpm=60,
        daily_request_budget=100
    )

    # Initial state should be CLOSED
    assert pm.get_circuit_state("test_rpc_1") == CircuitState.CLOSED.value

    # First failure -> degraded, still CLOSED
    pm.record_failure("test_rpc_1", "connection reset", latency_ms=120.0)
    assert pm.get_circuit_state("test_rpc_1") == CircuitState.CLOSED.value

    # Second failure -> degraded, still CLOSED
    pm.record_failure("test_rpc_1", "timeout", latency_ms=5000.0)
    assert pm.get_circuit_state("test_rpc_1") == CircuitState.CLOSED.value

    # Third failure (threshold) -> trips to OPEN
    pm.record_failure("test_rpc_1", "server error", latency_ms=250.0)
    assert pm.get_circuit_state("test_rpc_1") == CircuitState.OPEN.value

    # Check that latency and failure timestamp are recorded
    with pm._get_connection() as conn:
        row = conn.execute("SELECT * FROM providers WHERE id = 'test_rpc_1';").fetchone()
        assert row["circuit_state"] == CircuitState.OPEN.value
        assert row["last_latency_ms"] == 250.0
        assert row["last_failure_at"] is not None
        assert row["consecutive_failures"] == 3

    # Success resets to CLOSED
    pm.record_success("test_rpc_1", latency_ms=45.0)
    assert pm.get_circuit_state("test_rpc_1") == CircuitState.CLOSED.value
    with pm._get_connection() as conn:
        row = conn.execute("SELECT * FROM providers WHERE id = 'test_rpc_1';").fetchone()
        assert row["circuit_state"] == CircuitState.CLOSED.value
        assert row["consecutive_failures"] == 0
        assert row["last_latency_ms"] == 45.0
        assert row["last_success_at"] is not None

def test_circuit_breaker_half_open_auto_transition(test_db_path):
    pm = ProviderManager(db_path=test_db_path)
    pm.register_provider(
        provider_id="test_rpc_half_open",
        name="Test RPC Half Open",
        provider_type="rpc",
        is_enabled=True,
        capabilities=["CHAIN_RPC"]
    )

    # Trip the breaker
    pm.record_failure("test_rpc_half_open", "rate limited", is_rate_limit=True)
    assert pm.get_circuit_state("test_rpc_half_open") == CircuitState.OPEN.value

    # Fast forward cooldown expiration by updating cooldown_until to the past
    past_ts = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
    with pm._get_connection() as conn:
        conn.execute("UPDATE providers SET cooldown_until = ? WHERE id = 'test_rpc_half_open';", (past_ts,))

    # Now get_circuit_state should evaluate to HALF_OPEN
    assert pm.get_circuit_state("test_rpc_half_open") == CircuitState.HALF_OPEN.value

    # If failure happens in HALF_OPEN, re-trips to OPEN
    pm.record_failure("test_rpc_half_open", "probe failed", latency_ms=300.0)
    assert pm.get_circuit_state("test_rpc_half_open") == CircuitState.OPEN.value

def test_provider_rotation_and_quota_selection(test_db_path):
    pm = ProviderManager(db_path=test_db_path)
    pm.register_provider(
        provider_id="prov_a",
        name="Provider A",
        provider_type="market_data",
        is_enabled=True,
        priority=10,
        capabilities=["MARKET_DATA"],
        chains=["solana:mainnet"]
    )
    pm.register_provider(
        provider_id="prov_b",
        name="Provider B",
        provider_type="market_data",
        is_enabled=True,
        priority=20,
        capabilities=["MARKET_DATA"],
        chains=["solana:mainnet"]
    )

    # Rotation should pick prov_a first (highest priority / lowest number)
    best = pm.rotate_provider("MARKET_DATA", "solana:mainnet")
    assert best is not None
    assert best["id"] == "prov_a"

    # If prov_a is excluded or circuit tripped, it should pick prov_b
    pm.record_failure("prov_a", "rate limited", is_rate_limit=True)
    best2 = pm.rotate_provider("MARKET_DATA", "solana:mainnet")
    assert best2 is not None
    assert best2["id"] == "prov_b"

def test_offline_mode_isolation_enforcement(test_db_path, monkeypatch):
    pm = ProviderManager(db_path=test_db_path)
    monkeypatch.setattr(settings, "OPERATING_MODE", "offline_research")
    assert pm.is_offline_mode() is True

    with pytest.raises(OfflineModeBlockedException):
        pm.assert_online("some_provider")

# =============================================================================
# Deliverable 2: TTL-Based Decimal Re-Verification Tests
# =============================================================================

def test_decimal_verification_ttl_and_stale_fail_closed(test_db_path):
    pps = PersistentPaperService(db_path=test_db_path)
    token_addr = "TokenTest1111111111111111111111111111111111"
    meta = {"mint": token_addr, "decimals": 6, "symbol": "TEST"}

    # 1. Record verification with 1-second TTL
    res = pps.record_decimal_verification(
        chain_id="solana:mainnet",
        asset_address=token_addr,
        decimals=6,
        verification_source=DecimalProvenanceSource.CORE_TEST.value,
        verification_method="CORE_TEST_FIXTURE",
        raw_metadata=meta,
        symbol="TEST",
        ttl_seconds=1,
        evidence_provenance={"collector": "test_collector", "latency_ms": 15.0}
    )
    assert res["status"] == "VERIFIED"
    assert res["ttl_seconds"] == 1
    assert res["expires_at"] is not None

    # Status check should report not expired immediately
    status = pps.get_decimal_verification_status("solana:mainnet", token_addr)
    assert status["is_expired"] is False
    assert status["evidence_provenance"]["collector"] == "test_collector"

    # Immediately, _resolve_asset_decimals succeeds
    with pps._get_connection() as conn:
        dec, is_ver = pps._resolve_asset_decimals("solana:mainnet", token_addr, conn)
        assert dec == 6
        assert is_ver is True

    # 2. Simulate expiration by setting expires_at to past
    past_iso = (datetime.now(timezone.utc) - timedelta(seconds=10)).strftime('%Y-%m-%dT%H:%M:%SZ')
    with pps._get_connection() as conn:
        conn.execute(
            "UPDATE token_decimal_verifications SET expires_at = ? WHERE asset_address = ?;",
            (past_iso, token_addr)
        )

    # Status should now indicate expired
    stale_status = pps.get_decimal_verification_status("solana:mainnet", token_addr)
    assert stale_status["is_expired"] is True

    # _resolve_asset_decimals MUST fail closed and return (None, False)!
    with pps._get_connection() as conn:
        stale_dec, stale_ver = pps._resolve_asset_decimals("solana:mainnet", token_addr, conn)
        assert stale_dec is None
        assert stale_ver is False

def test_decimal_reverification_audit_history_preservation(test_db_path):
    pps = PersistentPaperService(db_path=test_db_path)
    token_addr = "TokenHist1111111111111111111111111111111111"
    meta1 = {"mint": token_addr, "decimals": 9, "symbol": "HIST"}

    # Initial verification
    pps.record_decimal_verification(
        chain_id="solana:mainnet",
        asset_address=token_addr,
        decimals=9,
        verification_source=DecimalProvenanceSource.CORE_TEST.value,
        verification_method="INITIAL_TEST_VERIFY",
        raw_metadata=meta1,
        symbol="HIST",
        ttl_seconds=3600
    )

    # Re-verify with updated evidence
    meta2 = {"mint": token_addr, "decimals": 9, "symbol": "HIST", "reverified": True}
    pps.reverify_token_decimals(
        chain_id="solana:mainnet",
        asset_address=token_addr,
        decimals=9,
        verification_source=DecimalProvenanceSource.CORE_TEST.value,
        verification_method="REVERIFICATION_TEST",
        raw_metadata=meta2,
        symbol="HIST",
        ttl_seconds=7200,
        evidence_provenance={"reason": "periodic_freshness_refresh"}
    )

    # Check that previous verification was archived in token_decimal_verification_history
    with pps._get_connection() as conn:
        cur = conn.execute(
            "SELECT * FROM token_decimal_verification_history WHERE asset_address = ?;",
            (token_addr,)
        )
        hist_rows = cur.fetchall()
        assert len(hist_rows) == 1
        assert hist_rows[0]["verification_method"] == "INITIAL_TEST_VERIFY"
        assert hist_rows[0]["decimals"] == 9

        # Active verification has new method and ttl
        active_cur = conn.execute(
            "SELECT * FROM token_decimal_verifications WHERE asset_address = ?;",
            (token_addr,)
        )
        active_row = active_cur.fetchone()
        assert active_row["verification_method"] == "REVERIFICATION_TEST"
        assert active_row["ttl_seconds"] == 7200

# =============================================================================
# Deliverable 3: Multi-Chain Ingestion Reliability Coordinator Tests
import asyncio
from app.services.discovery_service import DiscoveryService

def test_ingestion_coordinator_capability_gating_and_concurrency(test_db_path):
    disc = DiscoveryService(db_path=test_db_path)
    coord = IngestionCoordinator(discovery=disc)

    # Supported chain capability check
    cap = coord.check_chain_capability("solana:mainnet", ChainCapability.POOL_LIQUIDITY_DATA)
    assert cap["status"] in ("SUPPORTED", "PARTIAL")

    # Run ingestion on supported chain with simulated pool event
    pool_event = {
        "pool_address": "0x1111111111111111111111111111111111111111",
        "token0_address": "0x2222222222222222222222222222222222222222",
        "token1_address": "0x3333333333333333333333333333333333333333",
        "fee_tier_bps": 30,
        "block_number": 1000
    }
    res = asyncio.run(coord.run_ingestion_for_chain("eip155:8453", events=[pool_event]))
    assert res["status"] == "SUCCESS"
    assert res["checkpoint"]["last_block_or_slot"] == 1000

    # Freshness check
    freshness = coord.check_ingestion_freshness("eip155:8453")
    assert freshness["is_stale"] is False
    assert freshness["last_block_or_slot"] == 1000

# =============================================================================
# Deliverable 5: Candidate Research Scoring & Rankings Tests
# =============================================================================

def test_candidate_research_scoring_and_rankings(test_db_path):
    pps = PersistentPaperService(db_path=test_db_path)
    scorer = CandidateResearchScorer(db_path=test_db_path, paper_service=pps)

    verified_tok = "0x4ed4e862860bed51a9570b96d89af5e1b0efefed"
    meta = {"address": verified_tok, "chain_id": "eip155:8453", "decimals": 18, "symbol": "VRFD"}

    # Seed verified token
    pps.record_decimal_verification(
        chain_id="eip155:8453",
        asset_address=verified_tok,
        decimals=18,
        verification_source=DecimalProvenanceSource.CORE_TEST.value,
        verification_method="CORE_TEST_FIXTURE",
        raw_metadata=meta,
        symbol="VRFD",
        ttl_seconds=86400
    )

    # 1. Score verified candidate with strong liquidity & volume
    v_res = scorer.score_candidate(
        chain_id="eip155:8453",
        asset_address=verified_tok,
        symbol="VRFD",
        name="Verified Token",
        liquidity_usd=Decimal("150000"),
        volume_5m_usd=Decimal("20000"),
        volume_1h_usd=Decimal("80000"),
        tx_count_5m=50,
        price_change_5m_pct=Decimal("15.0"),
        is_mint_revoked=TriState.TRUE,
        is_freeze_revoked=TriState.TRUE,
        lp_status="BURNED",
        sell_tax_bps=100
    )
    assert v_res["composite_score"] is not None
    assert v_res["composite_score"] > 60.0
    assert v_res["data_freshness_status"] == "FRESH"
    assert v_res["verification_status"] == "VERIFIED"
    assert v_res["hard_reject_triggered"] is False

    # 2. Score unverified candidate: MUST be penalized and hard-rejected
    unverified_tok = "0x1234567890123456789012345678901234567890"
    u_res = scorer.score_candidate(
        chain_id="eip155:8453",
        asset_address=unverified_tok,
        symbol="UNVRF",
        name="Unverified Token",
        liquidity_usd=Decimal("150000"),
        volume_5m_usd=Decimal("20000")
    )
    assert u_res["hard_reject_triggered"] is True
    assert u_res["composite_score"] <= 10.0
    assert u_res["confidence"] == "REJECTED"
    assert "MISSING_DECIMAL_VERIFICATION" in u_res["risk_findings"]

    # 3. Retrieve rankings: verified candidate should be ranked above unverified
    rankings = scorer.get_rankings(chain_id="eip155:8453", min_score=0.0)
    assert len(rankings) >= 2
    assert rankings[0]["asset_address"] == verified_tok

# =============================================================================
# Deliverable 4: Windmill Research Orchestration Tests
# =============================================================================

def test_windmill_workflows_execution(test_db_path, monkeypatch):
    import windmill_scripts.wf14_api_health_guardian as wf14
    import windmill_scripts.wf01_candidate_discovery as wf01
    import windmill_scripts.wf08_opportunity_scoring as wf08
    import windmill_scripts.wf25_research_orchestrator as wf25

    # Run WF-14
    res_14 = wf14.main()
    assert res_14["status"] == "SUCCESS"
    assert res_14["guardian"] == "WF-14"

    # Run WF-01
    res_01 = wf01.main(chain_id="solana:mainnet")
    assert res_01["status"] == "SUCCESS"
    assert "candidates" in res_01

    # Run WF-08
    res_08 = wf08.main(chain_id="eip155:8453")
    assert res_08["status"] == "SUCCESS"
    assert "composite_score" in res_08

    # Run WF-25
    res_25 = wf25.main(chains=["solana:mainnet", "eip155:8453"])
    assert res_25["status"] == "SUCCESS"
    assert res_25["orchestrator"] == "WF-25"
    assert len(res_25["chains_inspected"]) == 2
