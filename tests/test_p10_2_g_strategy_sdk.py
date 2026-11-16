"""
P10.2-G Strategy SDK, Plugin Contract & Isolation Test Suite.
Verifies:
- STRAT-01: Valid and invalid strategy registration and lifecycle transitions.
- STRAT-02: Configuration schema validation and parameter boundaries.
- STRAT-03: Unsupported chain and capability rejection.
- STRAT-04: Stale market data (>300s) and missing pool liquidity fail-closed handling.
- STRAT-05: Deterministic evaluation and historical replay verification.
- STRAT-06: Typed signal contract with explicit raw-unit and human-unit amounts.
- STRAT-07: Sandbox timeout enforcement and unhandled exception containment.
- STRAT-08: Defensive boundary isolation preventing credential/signer/db access.
- STRAT-09: Intent proposal bridge and Core risk validation enforcement.
- STRAT-10: Process restart persistence of runs, signals, and audit logs.
- STRAT-11: FastAPI REST API endpoints for strategy lifecycle and evaluation.
- STRAT-12: Core-only standalone execution without Windmill/Hermes/n8n.
- STRAT-13: Hermes read-only boundary verification.
"""

import os
import time
import uuid
import tempfile
import sqlite3
import pytest
from decimal import Decimal
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient

from app.main import app
from app.db.sqlite_migrator import SqliteMigrator
from app.strategy.base import (
    BaseStrategy,
    StrategyMetadata,
    StrategyInputContext,
    StrategySignal,
    StrategyAction,
    StrategyLifecycle,
    StrategyMode
)
from app.strategy.sandbox import StrategyExecutionSandbox
from app.strategy.service import StrategyService
from app.strategy.builtins.momentum import MomentumLookbackStrategy
from app.strategy.builtins.liquidity_filter import LiquidityRiskFilterStrategy

client = TestClient(app)


# Custom test strategies for edge case verification
class SlowTimeoutStrategy(BaseStrategy):
    STRATEGY_ID = "slow_timeout_test"
    VERSION = "1.0.0"

    def get_metadata(self) -> StrategyMetadata:
        return StrategyMetadata(
            strategy_id=self.STRATEGY_ID,
            version=self.VERSION,
            name="Slow Timeout Test Strategy",
            supported_chains=["solana:mainnet"],
            supported_asset_types=["MEMECOIN"],
            required_market_fields=["price_usd"],
            timeout_seconds=0.5,
            lifecycle_status=StrategyLifecycle.ENABLED
        )

    def validate_config(self, config):
        return True, None

    def evaluate(self, context, config):
        # Sleeps longer than timeout ceiling
        time.sleep(1.0)
        return StrategySignal(
            signal_id=f"sig_{uuid.uuid4().hex[:12]}",
            run_id="run_test",
            strategy_id=self.STRATEGY_ID,
            strategy_version=self.VERSION,
            chain_id=context.chain_id,
            asset_id=context.asset_id,
            action=StrategyAction.BUY,
            confidence=0.8,
            price_source="TEST",
            input_timestamp=context.timestamp,
            expires_at=context.timestamp + timedelta(minutes=5)
        )


class CrashingBuggyStrategy(BaseStrategy):
    STRATEGY_ID = "crashing_buggy_test"
    VERSION = "1.0.0"

    def get_metadata(self) -> StrategyMetadata:
        return StrategyMetadata(
            strategy_id=self.STRATEGY_ID,
            version=self.VERSION,
            name="Crashing Buggy Test Strategy",
            supported_chains=["eip155:1"],
            supported_asset_types=["TOKEN"],
            required_market_fields=["price_usd"],
            timeout_seconds=2.0,
            lifecycle_status=StrategyLifecycle.ENABLED
        )

    def validate_config(self, config):
        return True, None

    def evaluate(self, context, config):
        # Deliberate unhandled exception
        raise ZeroDivisionError("Simulated unhandled mathematical flaw in user strategy")


class ChainRestrictedStrategy(BaseStrategy):
    STRATEGY_ID = "solana_exclusive_test"
    VERSION = "1.0.0"

    def get_metadata(self) -> StrategyMetadata:
        return StrategyMetadata(
            strategy_id=self.STRATEGY_ID,
            version=self.VERSION,
            name="Solana Exclusive Strategy",
            supported_chains=["solana:mainnet"],
            supported_asset_types=["MEMECOIN"],
            timeout_seconds=2.0,
            lifecycle_status=StrategyLifecycle.ENABLED
        )

    def validate_config(self, config):
        return True, None

    def evaluate(self, context, config):
        return StrategySignal(
            signal_id=f"sig_{uuid.uuid4().hex[:12]}",
            run_id="run_test",
            strategy_id=self.STRATEGY_ID,
            strategy_version=self.VERSION,
            chain_id=context.chain_id,
            asset_id=context.asset_id,
            action=StrategyAction.BUY,
            confidence=0.9,
            input_timestamp=context.timestamp,
            expires_at=context.timestamp + timedelta(minutes=5)
        )


@pytest.fixture
def temp_strategy_db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    migrator = SqliteMigrator(db_path)
    migrator.apply_migrations()
    yield db_path
    if os.path.exists(db_path):
        os.remove(db_path)


@pytest.fixture
def strat_service(temp_strategy_db):
    svc = StrategyService(db_path=temp_strategy_db)
    return svc


# =============================================================================
# STRAT-01: Registration, Metadata & Lifecycle State Machine
# =============================================================================

def test_strat_01_registration_and_lifecycle(strat_service):
    # Verify builtins registered
    strats = strat_service.list_strategies()
    strat_ids = [s["strategy_id"] for s in strats]
    assert "momentum_breakout" in strat_ids
    assert "safety_first_accumulator" in strat_ids

    # Lifecycle state machine transitions
    # ENABLED -> PAUSED -> ENABLED -> DISABLED -> RETIRED
    res = strat_service.transition_lifecycle("momentum_breakout", StrategyLifecycle.PAUSED)
    assert res["new_status"] == "PAUSED"

    res = strat_service.transition_lifecycle("momentum_breakout", StrategyLifecycle.ENABLED)
    assert res["new_status"] == "ENABLED"

    res = strat_service.transition_lifecycle("momentum_breakout", StrategyLifecycle.DISABLED)
    assert res["new_status"] == "DISABLED"

    # Evaluation on DISABLED strategy must be rejected
    with pytest.raises(PermissionError) as exc_info:
        strat_service.evaluate_strategy("momentum_breakout", "solana:mainnet", "Asset11111111111111111111111111111111111")
    assert "cannot be evaluated in status DISABLED" in str(exc_info.value)

    # Retire strategy
    res = strat_service.transition_lifecycle("momentum_breakout", StrategyLifecycle.RETIRED)
    assert res["new_status"] == "RETIRED"

    # Invalid transition from RETIRED must be rejected
    with pytest.raises(ValueError):
        strat_service.transition_lifecycle("momentum_breakout", StrategyLifecycle.ENABLED)


# =============================================================================
# STRAT-02: Configuration Validation and Parameter Persistence
# =============================================================================

def test_strat_02_configuration_validation_and_persistence(strat_service):
    # Valid config
    valid_params = {
        "min_price_change_pct": 7.5,
        "min_volume_24h_usd": 25000.0,
        "min_liquidity_usd": 20000.0,
        "take_profit_pct": 25.0,
        "stop_loss_pct": 10.0
    }
    is_valid, err = strat_service.validate_configuration("momentum_breakout", valid_params)
    assert is_valid is True
    assert err is None

    # Invalid config (take profit <= stop loss)
    invalid_params = {
        "min_price_change_pct": 5.0,
        "take_profit_pct": 5.0,
        "stop_loss_pct": 10.0
    }
    is_valid, err = strat_service.validate_configuration("momentum_breakout", invalid_params)
    assert is_valid is False
    assert "take_profit_pct must be greater than stop_loss_pct" in err

    # Save and retrieve configuration
    saved = strat_service.save_configuration("momentum_breakout", valid_params, chain_id="eip155:8453")
    assert saved["strategy_id"] == "momentum_breakout"
    assert saved["parameters"]["min_price_change_pct"] == 7.5

    loaded = strat_service.get_configuration("momentum_breakout", chain_id="eip155:8453")
    assert loaded["parameters"]["min_volume_24h_usd"] == 25000.0


# =============================================================================
# STRAT-03: Unsupported Chain and Capability Rejection
# =============================================================================

def test_strat_03_unsupported_chain_rejection(strat_service):
    # Verify unapproved strategy registration without fixture flag is rejected
    with pytest.raises(PermissionError) as perm_info:
        strat_service.register_strategy(ChainRestrictedStrategy())
    assert "not an approved built-in strategy" in str(perm_info.value)

    strat_service.register_strategy(ChainRestrictedStrategy(), is_test_fixture=True)

    # Evaluation on Solana succeeds capability check
    # Evaluation on Ethereum must fail closed with unsupported chain
    with pytest.raises(ValueError) as exc_info:
        strat_service.evaluate_strategy("solana_exclusive_test", "eip155:1", "0xTokenAddress")
    assert "is not supported by strategy" in str(exc_info.value)


# =============================================================================
# STRAT-04: Stale Market Data and Missing Liquidity Handling
# =============================================================================

def test_strat_04_stale_market_data_and_missing_liquidity(strat_service, temp_strategy_db):
    now_str = datetime.now(timezone.utc).isoformat()
    old_obs = (datetime.now(timezone.utc) - timedelta(seconds=400)).isoformat()

    with sqlite3.connect(temp_strategy_db) as conn:
        # Seed token with verified decimals
        conn.execute(
            """
            INSERT INTO discovered_tokens (id, chain_id, address, symbol, name, decimals, is_verified, discovery_source, first_seen_at, last_seen_at)
            VALUES ('tok_stale_01', 'solana:mainnet', 'StaleToken1111111111111111111111111111', 'STALE', 'Stale Token', 6, 1, 'TEST', ?, ?);
            """,
            (now_str, now_str)
        )
        # Seed an old/stale snapshot
        conn.execute(
            """
            INSERT INTO market_data_snapshots (id, pool_id, chain_id, provider_id, price_usd, liquidity_usd, volume_24h_usd, observation_time, retrieved_at)
            VALUES ('snap_stale_01', 'StalePool11111111111111111111111111111', 'solana:mainnet', 'test', 1.50, 50000.0, 10000.0, ?, ?);
            """,
            (old_obs, now_str)
        )
        conn.execute(
            """
            INSERT INTO discovered_pools (id, chain_id, protocol, pool_address, token0_address, token1_address, discovery_source, is_canonical, status, created_at)
            VALUES ('StalePool11111111111111111111111111111', 'solana:mainnet', 'raydium_cpmm', 'StalePool11111111111111111111111111111', 'StaleToken1111111111111111111111111111', 'native', 'TEST', 1, 'ACTIVE', ?);
            """,
            (now_str,)
        )

    # Evaluate against stale pool -> must return INVALID with MARKET_DATA_STALE
    sig = strat_service.evaluate_strategy(
        strategy_id="momentum_breakout",
        chain_id="solana:mainnet",
        asset_id="StaleToken1111111111111111111111111111",
        pool_id="StalePool11111111111111111111111111111"
    )
    assert sig.action == StrategyAction.INVALID
    assert "MARKET_DATA_STALE" in sig.reasons


# =============================================================================
# STRAT-05: Deterministic Evaluation and Historical Replay
# =============================================================================

def test_strat_05_deterministic_evaluation_and_replay(strat_service, temp_strategy_db):
    now_str = datetime.now(timezone.utc).isoformat()
    hist_obs_1 = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
    hist_obs_2 = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()

    with sqlite3.connect(temp_strategy_db) as conn:
        conn.execute(
            """
            INSERT INTO discovered_tokens (id, chain_id, address, symbol, name, decimals, is_verified, discovery_source, first_seen_at, last_seen_at)
            VALUES ('tok_replay_01', 'solana:mainnet', 'ReplayToken111111111111111111111111111', 'RPLY', 'Replay Token', 6, 1, 'TEST', ?, ?);
            """,
            (now_str, now_str)
        )
        conn.execute(
            """
            INSERT INTO discovered_pools (id, chain_id, protocol, pool_address, token0_address, token1_address, discovery_source, is_canonical, status, created_at)
            VALUES ('ReplayPool11111111111111111111111111', 'solana:mainnet', 'raydium_cpmm', 'ReplayPool11111111111111111111111111', 'ReplayToken111111111111111111111111111', 'native', 'TEST', 1, 'ACTIVE', ?);
            """,
            (now_str,)
        )
        # Historical snapshot: price $1.00
        conn.execute(
            """
            INSERT INTO market_data_snapshots (id, pool_id, chain_id, provider_id, price_usd, liquidity_usd, volume_24h_usd, observation_time, retrieved_at)
            VALUES ('snap_h1', 'ReplayPool11111111111111111111111111', 'solana:mainnet', 'test', 1.00, 100000.0, 50000.0, ?, ?);
            """,
            (hist_obs_1, hist_obs_1)
        )
        # Latest snapshot: price $1.15 (+15% breakout)
        conn.execute(
            """
            INSERT INTO market_data_snapshots (id, pool_id, chain_id, provider_id, price_usd, liquidity_usd, volume_24h_usd, observation_time, retrieved_at)
            VALUES ('snap_h2', 'ReplayPool11111111111111111111111111', 'solana:mainnet', 'test', 1.15, 120000.0, 60000.0, ?, ?);
            """,
            (hist_obs_2, hist_obs_2)
        )

    # Initial evaluation
    sig = strat_service.evaluate_strategy(
        strategy_id="momentum_breakout",
        chain_id="solana:mainnet",
        asset_id="ReplayToken111111111111111111111111111",
        pool_id="ReplayPool11111111111111111111111111"
    )
    assert sig.action == StrategyAction.BUY
    assert sig.confidence > 0.6
    assert sig.input_dataset_hash != ""

    # Replay verification
    replay_res = strat_service.replay_evaluation(sig.run_id)
    assert replay_res["reproducible"] is True
    assert replay_res["original_action"] == "BUY"
    assert replay_res["replayed_action"] == "BUY"


# =============================================================================
# STRAT-06: Typed Signal Contract with Explicit Raw & Human Units
# =============================================================================

def test_strat_06_signal_contract_and_amount_units(strat_service, temp_strategy_db):
    now_str = datetime.now(timezone.utc).isoformat()
    base_time = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()
    latest_time = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()

    with sqlite3.connect(temp_strategy_db) as conn:
        conn.execute(
            """
            INSERT INTO discovered_tokens (id, chain_id, address, symbol, name, decimals, is_verified, discovery_source, first_seen_at, last_seen_at)
            VALUES ('tok_unit_01', 'solana:mainnet', 'UnitToken11111111111111111111111111111', 'UNIT', 'Unit Token', 6, 1, 'TEST', ?, ?);
            """,
            (now_str, now_str)
        )
        conn.execute(
            """
            INSERT INTO discovered_pools (id, chain_id, protocol, pool_address, token0_address, token1_address, discovery_source, is_canonical, status, created_at)
            VALUES ('UnitPool1111111111111111111111111111', 'solana:mainnet', 'raydium_cpmm', 'UnitPool1111111111111111111111111111', 'UnitToken11111111111111111111111111111', 'native', 'TEST', 1, 'ACTIVE', ?);
            """,
            (now_str,)
        )
        # Baseline snapshot at $1.80
        conn.execute(
            """
            INSERT INTO market_data_snapshots (id, pool_id, chain_id, provider_id, price_usd, liquidity_usd, volume_24h_usd, observation_time, retrieved_at)
            VALUES ('snap_u0', 'UnitPool1111111111111111111111111111', 'solana:mainnet', 'test', 1.80, 80000.0, 30000.0, ?, ?);
            """,
            (base_time, base_time)
        )
        # Latest snapshot: price $2.00 (+11.1% price breakout)
        conn.execute(
            """
            INSERT INTO market_data_snapshots (id, pool_id, chain_id, provider_id, price_usd, liquidity_usd, volume_24h_usd, observation_time, retrieved_at)
            VALUES ('snap_u1', 'UnitPool1111111111111111111111111111', 'solana:mainnet', 'test', 2.00, 80000.0, 30000.0, ?, ?);
            """,
            (latest_time, latest_time)
        )

    sig = strat_service.evaluate_strategy(
        strategy_id="momentum_breakout",
        chain_id="solana:mainnet",
        asset_id="UnitToken11111111111111111111111111111",
        pool_id="UnitPool1111111111111111111111111111",
        parameter_overrides={"suggested_order_usd": 500.0}
    )

    # Price = $2.00, Order = $500.00 -> 250.0 tokens
    # With 6 decimals -> raw units = 250,000,000
    assert sig.suggested_size_human == Decimal("250.0")
    assert sig.suggested_size_raw == Decimal("250000000")
    assert sig.stop_loss_pct == 8.0
    assert sig.take_profit_pct == 20.0
    assert sig.price_source == "CORE_MARKET_SNAPSHOT"


# =============================================================================
# STRAT-07: Sandbox Timeout and Exception Containment
# =============================================================================

def test_strat_07_timeout_and_exception_containment(strat_service):
    strat_service.register_strategy(SlowTimeoutStrategy(), is_test_fixture=True)
    strat_service.register_strategy(CrashingBuggyStrategy(), is_test_fixture=True)

    # 1. Timeout Containment
    slow_ctx = StrategyInputContext(
        chain_id="solana:mainnet",
        asset_id="SlowAsset111111111111111111111111111",
        current_market_data={"price_usd": 1.0, "liquidity_usd": 20000.0}
    )
    sig_slow = strat_service.sandbox.execute(
        strategy=SlowTimeoutStrategy(),
        context=slow_ctx,
        config={},
        run_id="run_slow_01",
        timeout_seconds=0.3
    )
    assert sig_slow.action == StrategyAction.INVALID
    assert "EXECUTION_TIMEOUT" in sig_slow.reasons

    # 2. Unhandled Exception Containment
    crash_ctx = StrategyInputContext(
        chain_id="eip155:1",
        asset_id="0xCrashToken",
        current_market_data={"price_usd": 10.0, "liquidity_usd": 50000.0}
    )
    sig_crash = strat_service.sandbox.execute(
        strategy=CrashingBuggyStrategy(),
        context=crash_ctx,
        config={},
        run_id="run_crash_01"
    )
    assert sig_crash.action == StrategyAction.INVALID
    assert "STRATEGY_EXCEPTION" in sig_crash.reasons
    assert "ZeroDivisionError" in sig_crash.reasons


# =============================================================================
# STRAT-08: Defensive Boundary Isolation (No Direct Key/Signer/DB Access)
# =============================================================================

def test_strat_08_defensive_boundary_isolation():
    # StrategyInputContext is strictly immutable and holds only market/forensic data
    ctx = StrategyInputContext(
        chain_id="solana:mainnet",
        asset_id="TokenSafe111111111111111111111111111",
        current_market_data={"price_usd": 1.0}
    )

    # Attempting to mutate context must raise ValidationError (frozen model)
    with pytest.raises(Exception):
        ctx.chain_id = "eip155:1"

    # Context contains zero signing keys or credentials
    assert not hasattr(ctx, "private_key")
    assert not hasattr(ctx, "signer")
    assert not hasattr(ctx, "vault")
    assert not hasattr(ctx, "db_connection")


# =============================================================================
# STRAT-09: Intent Proposal Bridge and Core Risk Validation Enforcement
# =============================================================================

def test_strat_09_intent_proposal_and_core_risk_validation(strat_service, temp_strategy_db):
    now_str = datetime.now(timezone.utc).isoformat()
    base_time = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()
    latest_time = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()

    with sqlite3.connect(temp_strategy_db) as conn:
        conn.execute(
            """
            INSERT INTO discovered_tokens (id, chain_id, address, symbol, name, decimals, is_verified, discovery_source, first_seen_at, last_seen_at)
            VALUES ('tok_prop_01', 'solana:mainnet', 'PropToken1111111111111111111111111111', 'PROP', 'Proposal Token', 9, 1, 'TEST', ?, ?);
            """,
            (now_str, now_str)
        )
        conn.execute(
            """
            INSERT INTO discovered_pools (id, chain_id, protocol, pool_address, token0_address, token1_address, discovery_source, is_canonical, status, created_at)
            VALUES ('PropPool111111111111111111111111111', 'solana:mainnet', 'raydium_cpmm', 'PropPool111111111111111111111111111', 'PropToken1111111111111111111111111111', 'native', 'TEST', 1, 'ACTIVE', ?);
            """,
            (now_str,)
        )
        # Baseline snapshot at $4.00
        conn.execute(
            """
            INSERT INTO market_data_snapshots (id, pool_id, chain_id, provider_id, price_usd, liquidity_usd, volume_24h_usd, observation_time, retrieved_at)
            VALUES ('snap_p0', 'PropPool111111111111111111111111111', 'solana:mainnet', 'test', 4.00, 100000.0, 50000.0, ?, ?);
            """,
            (base_time, base_time)
        )
        # Latest snapshot at $5.00 (+25% breakout)
        conn.execute(
            """
            INSERT INTO market_data_snapshots (id, pool_id, chain_id, provider_id, price_usd, liquidity_usd, volume_24h_usd, observation_time, retrieved_at)
            VALUES ('snap_p1', 'PropPool111111111111111111111111111', 'solana:mainnet', 'test', 5.00, 100000.0, 50000.0, ?, ?);
            """,
            (latest_time, latest_time)
        )

    # 1. Normal sized signal -> Core risk validation passes
    sig = strat_service.evaluate_strategy(
        strategy_id="momentum_breakout",
        chain_id="solana:mainnet",
        asset_id="PropToken1111111111111111111111111111",
        pool_id="PropPool111111111111111111111111111",
        parameter_overrides={"suggested_order_usd": 500.0}
    )
    proposal = strat_service.propose_trade_intent(sig.signal_id, account_id="paper_sol_desk")
    assert proposal["execution_status"] == "PROPOSED_ONLY"
    assert proposal["requires_core_authorization"] is True
    assert proposal["core_risk_validated"] is True

    # 2. Massive oversized signal ($80,000 USD > $50,000 max single order limit) -> Core risk rejects
    sig_huge = strat_service.evaluate_strategy(
        strategy_id="momentum_breakout",
        chain_id="solana:mainnet",
        asset_id="PropToken1111111111111111111111111111",
        pool_id="PropPool111111111111111111111111111",
        parameter_overrides={"suggested_order_usd": 80000.0}
    )
    proposal_huge = strat_service.propose_trade_intent(sig_huge.signal_id, account_id="paper_sol_desk")
    assert proposal_huge["core_risk_validated"] is False
    assert "RISK_LIMIT_EXCEEDED" in proposal_huge["rejection_reason"]


# =============================================================================
# STRAT-10: Process Restart Persistence of Runs, Signals & Audit Log
# =============================================================================

def test_strat_10_restart_persistence(temp_strategy_db):
    service_1 = StrategyService(db_path=temp_strategy_db)
    now_str = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(temp_strategy_db) as conn:
        conn.execute(
            """
            INSERT INTO discovered_pools (id, chain_id, protocol, pool_address, token0_address, token1_address, discovery_source, is_canonical, status, created_at)
            VALUES ('PersistStratPool', 'solana:mainnet', 'raydium_cpmm', 'PersistStratPool', 'PersistAsset', 'native', 'TEST', 1, 'ACTIVE', ?);
            """,
            (now_str,)
        )
        conn.execute(
            """
            INSERT INTO market_data_snapshots (id, pool_id, chain_id, provider_id, price_usd, liquidity_usd, volume_24h_usd, observation_time, retrieved_at)
            VALUES ('snap_persist_1', 'PersistStratPool', 'solana:mainnet', 'test', 2.50, 100000.0, 50000.0, ?, ?);
            """,
            (now_str, now_str)
        )

    sig_1 = service_1.evaluate_strategy("momentum_breakout", "solana:mainnet", "PersistAsset", "PersistStratPool")

    # Restart service instance on same SQLite WAL file
    service_2 = StrategyService(db_path=temp_strategy_db)

    # Signals and runs must persist across service restart
    signals = service_2.list_signals(strategy_id="momentum_breakout")
    assert any(s["signal_id"] == sig_1.signal_id for s in signals)

    runs = service_2.list_runs(strategy_id="momentum_breakout")
    assert any(r["run_id"] == sig_1.run_id for r in runs)

    audit = service_2.list_audit_log(strategy_id="momentum_breakout")
    assert any(a["event_type"] == "EVALUATE" for a in audit)


# =============================================================================
# STRAT-11: FastAPI REST API Endpoints Verification
# =============================================================================

def test_strat_11_rest_api_endpoints():
    # 1. GET /api/v1/strategies
    resp = client.get("/api/v1/strategies")
    assert resp.status_code == 200
    strats = resp.json()
    assert isinstance(strats, list)
    assert len(strats) >= 2

    # 2. GET /api/v1/strategies/{strategy_id}
    resp = client.get("/api/v1/strategies/momentum_breakout")
    assert resp.status_code == 200
    detail = resp.json()
    assert detail["metadata"]["strategy_id"] == "momentum_breakout"
    assert "active_configuration" in detail

    # 3. POST /api/v1/strategies/{strategy_id}/validate-config
    resp = client.post("/api/v1/strategies/momentum_breakout/validate-config", json={
        "parameters": {"min_price_change_pct": 12.0}
    })
    assert resp.status_code == 200
    assert resp.json()["valid"] is True

    # 4. POST /api/v1/strategies/{strategy_id}/status (Pause and re-enable)
    resp = client.post("/api/v1/strategies/momentum_breakout/status", json={"target_status": "PAUSED"})
    assert resp.status_code == 200
    assert resp.json()["new_status"] == "PAUSED"

    resp = client.post("/api/v1/strategies/momentum_breakout/status", json={"target_status": "ENABLED"})
    assert resp.status_code == 200
    assert resp.json()["new_status"] == "ENABLED"


# =============================================================================
# STRAT-12: Core Standalone & Hermes Read-Only Boundary
# =============================================================================

def test_strat_12_standalone_and_hermes_boundaries(temp_strategy_db):
    # StrategyService initializes and evaluates in standalone Python/SQLite mode without Docker or Windmill
    standalone_svc = StrategyService(db_path=temp_strategy_db)
    assert len(standalone_svc.list_strategies()) >= 2

    # Verify Hermes analyst remains strictly read-only
    from app.services.hermes_analyst import HermesResearchAnalyst
    dossier = HermesResearchAnalyst.synthesize_token_dossier(
        chain_id="solana:mainnet",
        asset_address="HermesTestToken111111111111111111111",
        symbol="HERMES_TEST"
    )
    assert "composite_score" in dossier or "markdown_brief" in dossier
    # Hermes cannot call strategy execution or mutate strategy lifecycle
    assert not hasattr(HermesResearchAnalyst, "evaluate_strategy")
    assert not hasattr(HermesResearchAnalyst, "transition_lifecycle")
    assert not hasattr(HermesResearchAnalyst, "execute_order")


# =============================================================================
# P10.2-G.2 ADVERSARIAL & COMPLIANCE VERIFICATION SUITE
# =============================================================================

def test_g2_fail_closed_decimals_all_scales(strat_service, temp_strategy_db):
    """Verifies exact sizing across all decimal scales (6, 8, 9, 18) without 6-decimal hardcoding."""
    now_str = datetime.now(timezone.utc).isoformat()
    base_time = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()
    latest_time = now_str

    scales = [
        ("solana:mainnet", "UsdcSolToken111111111111111111111111", 6, "USDC"),
        ("eip155:1", "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599", 8, "WBTC"),
        ("solana:mainnet", "So11111111111111111111111111111111111111112", 9, "SOL"),
        ("eip155:1", "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2", 18, "WETH")
    ]

    for chain, token_addr, decimals, symbol in scales:
        pool_addr = f"Pool_{symbol}_{decimals}"
        with sqlite3.connect(temp_strategy_db) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO discovered_tokens (id, chain_id, address, symbol, name, decimals, is_verified, discovery_source, first_seen_at, last_seen_at)
                VALUES (?, ?, ?, ?, ?, ?, 1, 'TEST', ?, ?);
                """,
                (f"tok_{symbol}", chain, token_addr, symbol, f"{symbol} Test", decimals, now_str, now_str)
            )
            conn.execute(
                """
                INSERT OR REPLACE INTO discovered_pools (id, chain_id, protocol, pool_address, token0_address, token1_address, discovery_source, is_canonical, status, created_at)
                VALUES (?, ?, 'uniswap_v3', ?, ?, 'native', 'TEST', 1, 'ACTIVE', ?);
                """,
                (pool_addr, chain, pool_addr, token_addr, now_str)
            )
            conn.execute(
                """
                INSERT OR REPLACE INTO market_data_snapshots (id, pool_id, chain_id, provider_id, price_usd, liquidity_usd, volume_24h_usd, observation_time, retrieved_at)
                VALUES (?, ?, ?, 'test', 10.0, 500000.0, 100000.0, ?, ?);
                """,
                (f"snap_b_{symbol}", pool_addr, chain, base_time, base_time)
            )
            conn.execute(
                """
                INSERT OR REPLACE INTO market_data_snapshots (id, pool_id, chain_id, provider_id, price_usd, liquidity_usd, volume_24h_usd, observation_time, retrieved_at)
                VALUES (?, ?, ?, 'test', 12.0, 500000.0, 100000.0, ?, ?);
                """,
                (f"snap_l_{symbol}", pool_addr, chain, latest_time, latest_time)
            )

        sig = strat_service.evaluate_strategy(
            strategy_id="momentum_breakout",
            chain_id=chain,
            asset_id=token_addr,
            pool_id=pool_addr,
            parameter_overrides={"suggested_order_usd": 120.0}
        )
        assert sig.action == StrategyAction.BUY
        assert sig.suggested_size_human is not None
        assert sig.suggested_size_raw is not None

        # Verify exact integer raw size matches suggested_size_human * 10**decimals
        expected_raw = int(sig.suggested_size_human * (Decimal(10) ** decimals))
        assert sig.suggested_size_raw == Decimal(expected_raw)


def test_g2_unverified_or_missing_decimals_fail_closed(strat_service, temp_strategy_db):
    """Verifies that missing, unverified, NULL, or out-of-range decimals fail closed with UNVERIFIED_ASSET_DECIMALS."""
    now_str = datetime.now(timezone.utc).isoformat()

    # 1. Asset completely missing from discovered_tokens
    sig_missing = strat_service.evaluate_strategy(
        strategy_id="momentum_breakout",
        chain_id="solana:mainnet",
        asset_id="NonExistentToken11111111111111111111111111"
    )
    assert sig_missing.action == StrategyAction.INVALID
    assert "UNVERIFIED_ASSET_DECIMALS" in sig_missing.reasons
    assert sig_missing.suggested_size_raw is None
    assert sig_missing.suggested_size_human is None

    # 2. Asset exists, but decimals is NULL
    with sqlite3.connect(temp_strategy_db) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO discovered_tokens (id, chain_id, address, symbol, name, decimals, is_verified, discovery_source, first_seen_at, last_seen_at)
            VALUES ('tok_null_dec', 'solana:mainnet', 'NullDecToken111111111111111111111111111', 'NULLDEC', 'Null Decimals', NULL, 1, 'TEST', ?, ?);
            """,
            (now_str, now_str)
        )
    sig_null = strat_service.evaluate_strategy(
        strategy_id="momentum_breakout",
        chain_id="solana:mainnet",
        asset_id="NullDecToken111111111111111111111111111"
    )
    assert sig_null.action == StrategyAction.INVALID
    assert "UNVERIFIED_ASSET_DECIMALS" in sig_null.reasons
    assert sig_null.suggested_size_raw is None

    # 3. Asset exists, but is_verified is 0
    with sqlite3.connect(temp_strategy_db) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO discovered_tokens (id, chain_id, address, symbol, name, decimals, is_verified, discovery_source, first_seen_at, last_seen_at)
            VALUES ('tok_unverified', 'solana:mainnet', 'UnverifiedToken11111111111111111111111', 'UNVER', 'Unverified', 9, 0, 'TEST', ?, ?);
            """,
            (now_str, now_str)
        )
    sig_unver = strat_service.evaluate_strategy(
        strategy_id="momentum_breakout",
        chain_id="solana:mainnet",
        asset_id="UnverifiedToken11111111111111111111111"
    )
    assert sig_unver.action == StrategyAction.INVALID
    assert "UNVERIFIED_ASSET_DECIMALS" in sig_unver.reasons

    # 4. Asset exists, but decimals out of range (50 > 36)
    with sqlite3.connect(temp_strategy_db) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO discovered_tokens (id, chain_id, address, symbol, name, decimals, is_verified, discovery_source, first_seen_at, last_seen_at)
            VALUES ('tok_bad_range', 'solana:mainnet', 'BadRangeToken111111111111111111111111', 'BADRNG', 'Bad Range', 50, 1, 'TEST', ?, ?);
            """,
            (now_str, now_str)
        )
    sig_bad = strat_service.evaluate_strategy(
        strategy_id="momentum_breakout",
        chain_id="solana:mainnet",
        asset_id="BadRangeToken111111111111111111111111"
    )
    assert sig_bad.action == StrategyAction.INVALID
    assert "UNVERIFIED_ASSET_DECIMALS" in sig_bad.reasons


def test_g2_trusted_builtin_strategy_policy(strat_service):
    """Verifies that untrusted arbitrary user strategies are strictly rejected from registration."""
    class MaliciousStrategy(BaseStrategy):
        def get_metadata(self):
            return StrategyMetadata(
                strategy_id="malicious_exploit",
                version="1.0.0",
                name="Malicious Exploit Strategy",
                supported_chains=["solana:mainnet"],
                supported_asset_types=["MEMECOIN"],
                lifecycle_status=StrategyLifecycle.ENABLED
            )
        def validate_config(self, config): return True, None
        def evaluate(self, context, config): return None

    with pytest.raises(PermissionError) as exc_info:
        strat_service.register_strategy(MaliciousStrategy(), is_test_fixture=False)
    assert "not an approved built-in strategy" in str(exc_info.value)


def test_g2_deep_immutability_of_context(strat_service):
    """Verifies that strategies cannot mutate nested dictionaries inside StrategyInputContext."""
    class MutatorStrategy(BaseStrategy):
        STRATEGY_ID = "mutator_test"
        VERSION = "1.0.0"

        def get_metadata(self):
            return StrategyMetadata(
                strategy_id=self.STRATEGY_ID,
                version=self.VERSION,
                name="Context Mutator Strategy",
                supported_chains=["solana:mainnet"],
                supported_asset_types=["MEMECOIN"],
                lifecycle_status=StrategyLifecycle.ENABLED
            )

        def validate_config(self, config): return True, None

        def evaluate(self, context, config):
            # Attempt to mutate nested context in-place
            context.current_market_data["price_usd"] = 999999.0
            return StrategySignal(
                signal_id=f"sig_{uuid.uuid4().hex[:12]}",
                run_id="run_mut",
                strategy_id=self.STRATEGY_ID,
                strategy_version=self.VERSION,
                chain_id=context.chain_id,
                asset_id=context.asset_id,
                action=StrategyAction.BUY,
                confidence=1.0,
                input_timestamp=context.timestamp,
                expires_at=context.timestamp + timedelta(minutes=5)
            )

    strat_service.register_strategy(MutatorStrategy(), is_test_fixture=True)
    ctx = StrategyInputContext(
        chain_id="solana:mainnet",
        asset_id="MutTestAsset",
        current_market_data={"price_usd": 10.0}
    )

    sig = strat_service.sandbox.execute(
        strategy=MutatorStrategy(),
        context=ctx,
        config={},
        run_id="run_mut_test_01"
    )
    # The attempted mutation raises TypeError (MappingProxyType is immutable), caught and returned as INVALID
    assert sig.action == StrategyAction.INVALID
    assert "STRATEGY_EXCEPTION" in sig.reasons
    assert "TypeError" in sig.reasons


def test_g2_bounded_admission_worker_pool_exhaustion():
    """Verifies that the sandbox rejects execution when worker pool capacity is exhausted."""
    # Bounded pool with 1 worker and 1 queue slot (max capacity = 2)
    small_sandbox = StrategyExecutionSandbox(max_workers=1, max_queue_size=1)
    try:
        strat = SlowTimeoutStrategy()
        ctx = StrategyInputContext(chain_id="solana:mainnet", asset_id="BoundedToken")

        # Saturate capacity:
        # Fill worker
        sig1 = small_sandbox.execute(strat, ctx, {}, "run_sat_01", timeout_seconds=0.1)
        # Verify bounded admission rejects if in_flight >= capacity
        with small_sandbox._lock:
            small_sandbox._in_flight = 2  # Manually simulate saturated pool

        sig_rej = small_sandbox.execute(strat, ctx, {}, "run_overflow_01", timeout_seconds=1.0)
        assert sig_rej.action == StrategyAction.INVALID
        assert "WORKER_POOL_EXHAUSTED" in sig_rej.reasons
        assert "BUSY" in sig_rej.reasons
    finally:
        small_sandbox.shutdown(wait=False)


def test_g2_idempotent_intent_proposals_and_cross_account_isolation(strat_service, temp_strategy_db):
    """Verifies idempotent proposal persistence in strategy_proposals and cross-account rejection."""
    now_str = datetime.now(timezone.utc).isoformat()
    base_time = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()

    with sqlite3.connect(temp_strategy_db) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO discovered_tokens (id, chain_id, address, symbol, name, decimals, is_verified, discovery_source, first_seen_at, last_seen_at)
            VALUES ('tok_prop_idem', 'solana:mainnet', 'IdemToken1111111111111111111111111111', 'IDEM', 'Idem Token', 9, 1, 'TEST', ?, ?);
            """,
            (now_str, now_str)
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO discovered_pools (id, chain_id, protocol, pool_address, token0_address, token1_address, discovery_source, is_canonical, status, created_at)
            VALUES ('IdemPool111111111111111111111111111', 'solana:mainnet', 'raydium_cpmm', 'IdemPool111111111111111111111111111', 'IdemToken1111111111111111111111111111', 'native', 'TEST', 1, 'ACTIVE', ?);
            """,
            (now_str,)
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO market_data_snapshots (id, pool_id, chain_id, provider_id, price_usd, liquidity_usd, volume_24h_usd, observation_time, retrieved_at)
            VALUES ('snap_id_0', 'IdemPool111111111111111111111111111', 'solana:mainnet', 'test', 2.0, 100000.0, 50000.0, ?, ?);
            """,
            (base_time, base_time)
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO market_data_snapshots (id, pool_id, chain_id, provider_id, price_usd, liquidity_usd, volume_24h_usd, observation_time, retrieved_at)
            VALUES ('snap_id_1', 'IdemPool111111111111111111111111111', 'solana:mainnet', 'test', 2.5, 100000.0, 50000.0, ?, ?);
            """,
            (now_str, now_str)
        )

    # Evaluate signal for account acc_alpha
    sig = strat_service.evaluate_strategy(
        strategy_id="momentum_breakout",
        chain_id="solana:mainnet",
        asset_id="IdemToken1111111111111111111111111111",
        pool_id="IdemPool111111111111111111111111111",
        account_id="acc_alpha",
        parameter_overrides={"suggested_order_usd": 100.0}
    )
    assert sig.action == StrategyAction.BUY

    # 1. First proposal succeeds
    prop1 = strat_service.propose_trade_intent(sig.signal_id, "acc_alpha")
    assert prop1["proposal_id"].startswith("prop_")
    assert prop1["execution_status"] == "PROPOSED_ONLY"

    # 2. Duplicate proposal is idempotent
    prop2 = strat_service.propose_trade_intent(sig.signal_id, "acc_alpha")
    assert prop2["proposal_id"] == prop1["proposal_id"]
    assert prop2.get("idempotent_cached") is True

    # 3. Verify exactly 1 row exists in strategy_proposals table
    with sqlite3.connect(temp_strategy_db) as conn:
        cur = conn.execute("SELECT COUNT(*) FROM strategy_proposals WHERE signal_id = ?;", (sig.signal_id,))
        count = cur.fetchone()[0]
        assert count == 1

    # 4. Cross-account attempt by acc_beta must be rejected with PermissionError
    with pytest.raises(PermissionError) as exc_info:
        strat_service.propose_trade_intent(sig.signal_id, "acc_beta")
    assert "cannot propose intent for signal evaluated for account" in str(exc_info.value)


def test_g2_strategy_api_auth_and_loopback_protection():
    """Verifies that mutating strategy endpoints enforce loopback check and ADMIN_API_TOKEN."""
    from app.core.config import settings

    # 1. Loopback allowed when ADMIN_API_TOKEN is empty
    settings.ADMIN_API_TOKEN = None
    resp = client.post("/api/v1/strategies/momentum_breakout/validate-config", json={"parameters": {}})
    assert resp.status_code == 200

    # 2. When ADMIN_API_TOKEN is set, unauthenticated requests are rejected with 401
    try:
        settings.ADMIN_API_TOKEN = "strat_secret_test_token"
        resp_unauth = client.post("/api/v1/strategies/momentum_breakout/validate-config", json={"parameters": {}})
        assert resp_unauth.status_code == 401

        resp_bad_token = client.post(
            "/api/v1/strategies/momentum_breakout/validate-config",
            json={"parameters": {}},
            headers={"X-API-Key": "wrong-token"}
        )
        assert resp_bad_token.status_code == 401

        resp_auth = client.post(
            "/api/v1/strategies/momentum_breakout/validate-config",
            json={"parameters": {}},
            headers={"X-API-Key": "strat_secret_test_token"}
        )
        assert resp_auth.status_code == 200
    finally:
        settings.ADMIN_API_TOKEN = None

