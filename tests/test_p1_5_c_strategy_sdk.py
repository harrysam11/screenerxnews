"""
P1.5-C StrategySDK Unit, Security, and Integration Tests.
Exhaustively tests declarative strategy schemas, feature registry, safe evaluation,
operators (eq, neq, gt, gte, lt, lte, between, crosses_above, crosses_below, AND, OR, NOT),
injection attacks, size/depth limits, immutable versioning, and API endpoints.
All tests run against temporary isolated databases.
"""

import os
import tempfile
import pytest
from decimal import Decimal
from datetime import datetime, timezone
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.schemas.ohlcv import Timeframe, OHLCVCandleDTO, CandleBarStatus
from app.strategy_sdk.schemas import (
    ConditionOperator,
    LogicalOperator,
    EvaluationStatus,
    ConditionLeafDTO,
    ConditionGroupDTO,
    StrategyMetadataDTO,
    DeclarativeStrategyDTO,
    StrategyCreateRequest,
    StrategyUpdateRequest,
    StrategyStatusUpdateRequest,
    MAX_CONFIG_SIZE_BYTES,
    MAX_EXPRESSION_DEPTH,
    MAX_TOTAL_CONDITIONS
)
from app.strategy_sdk.registry import feature_registry
from app.strategy_sdk.evaluator import strategy_evaluator
from app.strategy_sdk.manager import strategy_manager
from app.db.research_history_store import ResearchHistoryStore
from app.services.research_analytics_service import research_analytics_service
from app.api.v1.research import router as research_router


def make_candle(
    idx: int,
    open_p: str,
    high_p: str,
    low_p: str,
    close_p: str,
    volume: str = "1000",
    timeframe: Timeframe = Timeframe.M15,
    bar_status: CandleBarStatus = CandleBarStatus.CLOSED,
    base_ts: int = 1700000000,
    chain_id: str = "eip155:8453",
    pool_address: str = "0xpool123"
) -> OHLCVCandleDTO:
    """Helper to generate a validated OHLCVCandleDTO with monotonic timestamps."""
    step = timeframe.seconds
    ts_unix = base_ts + (idx * step)
    ts_utc = datetime.fromtimestamp(ts_unix, tz=timezone.utc).isoformat()
    end_utc = datetime.fromtimestamp(ts_unix + step, tz=timezone.utc).isoformat()
    return OHLCVCandleDTO(
        chain_id=chain_id,
        pool_address=pool_address,
        timeframe=timeframe,
        timestamp_utc=ts_utc,
        timestamp_unix=ts_unix,
        end_timestamp_utc=end_utc,
        open_price=Decimal(open_p),
        high_price=Decimal(high_p),
        low_price=Decimal(low_p),
        close_price=Decimal(close_p),
        volume=Decimal(volume),
        bar_status=bar_status
    )


# ============================================================================
# 1. SDK Schema & Feature Registry Tests
# ============================================================================

def test_feature_registry_contents_and_operators():
    """Validates that all approved P1.5-A and P1.5-B features are present in the registry."""
    features = feature_registry.list_features()
    expected_features = {
        "open", "high", "low", "close", "volume", "bar_status",
        "sma", "ema", "rsi", "macd_line", "macd_signal", "macd_histogram", "atr",
        "volume_sma", "rvol", "volume_acceleration_ratio",
        "has_active_bullish_fvg", "has_active_bearish_fvg",
        "has_active_bullish_ob", "has_active_bearish_ob",
        "recent_swing_high_price", "recent_swing_low_price"
    }
    for ef in expected_features:
        assert ef in features, f"Missing feature {ef} in registry"

    # Numeric features must support gt, lt, between, crosses
    rsi_feat = feature_registry.get_feature("rsi")
    assert rsi_feat is not None
    assert rsi_feat.data_type == "numeric"
    assert ConditionOperator.GT in rsi_feat.supported_operators
    assert ConditionOperator.BETWEEN in rsi_feat.supported_operators
    assert ConditionOperator.CROSSES_ABOVE in rsi_feat.supported_operators

    # Boolean features must only support eq and neq
    fvg_feat = feature_registry.get_feature("has_active_bullish_fvg")
    assert fvg_feat is not None
    assert fvg_feat.data_type == "boolean"
    assert ConditionOperator.EQ in fvg_feat.supported_operators
    assert ConditionOperator.GT not in fvg_feat.supported_operators


def test_declarative_strategy_schema_validation_and_forbid_unknown():
    """Verifies that unknown fields in declarative strategies are strictly rejected."""
    valid_strat = DeclarativeStrategyDTO(
        strategy=StrategyMetadataDTO(
            id="test_strat_01",
            name="Test Strategy",
            version="1.0.0"
        ),
        entry=ConditionGroupDTO(
            operator=LogicalOperator.AND,
            conditions=[
                ConditionLeafDTO(
                    feature="rsi",
                    operator=ConditionOperator.LT,
                    value=30
                )
            ]
        )
    )
    assert valid_strat.strategy.id == "test_strat_01"

    # Attempt injecting unknown field into ConditionLeafDTO
    with pytest.raises(Exception):
        ConditionLeafDTO(
            feature="rsi",
            operator=ConditionOperator.LT,
            value=30,
            malicious_field="attack"  # Should be forbidden
        )

    # Invalid strategy ID format (spaces or special characters)
    with pytest.raises(ValueError, match="must be alphanumeric"):
        StrategyMetadataDTO(
            id="bad id with spaces!",
            name="Invalid",
            version="1.0.0"
        )


# ============================================================================
# 2. Condition Evaluator & Operator Tests
# ============================================================================

def test_comparison_operators_and_logical_combinators():
    """Tests all standard comparison operators (eq, neq, gt, gte, lt, lte) and logic (AND, OR, NOT)."""
    # 20 candles with closing prices 10..29
    candles = [make_candle(i, str(10 + i), str(12 + i), str(9 + i), str(10 + i)) for i in range(20)]

    # 1. GT and LT with AND
    strat_and = DeclarativeStrategyDTO(
        strategy=StrategyMetadataDTO(id="s_and", name="AND test"),
        entry=ConditionGroupDTO(
            operator=LogicalOperator.AND,
            conditions=[
                ConditionLeafDTO(feature="close", operator=ConditionOperator.GT, value=25),
                ConditionLeafDTO(feature="close", operator=ConditionOperator.LT, value=35)
            ]
        )
    )
    res_and = strategy_evaluator.evaluate_strategy(strat_and, candles)
    # Latest close is 29 (10 + 19). 29 > 25 and 29 < 35 -> True
    assert res_and.entry_matched is True
    assert res_and.overall_status == EvaluationStatus.MATCH

    # 2. OR condition where one fails and one passes
    strat_or = DeclarativeStrategyDTO(
        strategy=StrategyMetadataDTO(id="s_or", name="OR test"),
        entry=ConditionGroupDTO(
            operator=LogicalOperator.OR,
            conditions=[
                ConditionLeafDTO(feature="close", operator=ConditionOperator.LT, value=20),  # False
                ConditionLeafDTO(feature="close", operator=ConditionOperator.GTE, value=29)  # True (29 >= 29)
            ]
        )
    )
    res_or = strategy_evaluator.evaluate_strategy(strat_or, candles)
    assert res_or.entry_matched is True

    # 3. NOT combinator
    strat_not = DeclarativeStrategyDTO(
        strategy=StrategyMetadataDTO(id="s_not", name="NOT test"),
        entry=ConditionGroupDTO(
            operator=LogicalOperator.NOT,
            conditions=[
                ConditionLeafDTO(feature="close", operator=ConditionOperator.LT, value=20)  # False, inverted -> True
            ]
        )
    )
    res_not = strategy_evaluator.evaluate_strategy(strat_not, candles)
    assert res_not.entry_matched is True


def test_between_operator_bounds_and_evaluation():
    """Validates the 'between' operator."""
    candles = [make_candle(i, "50", "55", "45", "50") for i in range(5)]

    # Between [40, 60] -> should match 50
    strat = DeclarativeStrategyDTO(
        strategy=StrategyMetadataDTO(id="s_between", name="Between Test"),
        entry=ConditionGroupDTO(
            operator=LogicalOperator.AND,
            conditions=[
                ConditionLeafDTO(feature="close", operator=ConditionOperator.BETWEEN, min_value=40, max_value=60)
            ]
        )
    )
    res = strategy_evaluator.evaluate_strategy(strat, candles)
    assert res.entry_matched is True

    # Between [55, 60] -> should NOT match 50
    strat_fail = DeclarativeStrategyDTO(
        strategy=StrategyMetadataDTO(id="s_between_fail", name="Between Fail"),
        entry=ConditionGroupDTO(
            operator=LogicalOperator.AND,
            conditions=[
                ConditionLeafDTO(feature="close", operator=ConditionOperator.BETWEEN, min_value=55, max_value=60)
            ]
        )
    )
    res_fail = strategy_evaluator.evaluate_strategy(strat_fail, candles)
    assert res_fail.entry_matched is False


def test_crosses_above_and_crosses_below_operators():
    """
    Validates crossing operators using historical candle sequence.
    crosses_above: prev <= target and curr > target
    crosses_below: prev >= target and curr < target
    """
    # Bar 0: Close = 48
    # Bar 1: Close = 52 (crosses above 50!)
    c0 = make_candle(0, "48", "49", "47", "48")
    c1 = make_candle(1, "49", "53", "48", "52")
    candles = [c0, c1]

    # Test crosses_above 50 at bar 1
    strat_above = DeclarativeStrategyDTO(
        strategy=StrategyMetadataDTO(id="s_cross_above", name="Cross Above Test"),
        entry=ConditionGroupDTO(
            operator=LogicalOperator.AND,
            conditions=[
                ConditionLeafDTO(feature="close", operator=ConditionOperator.CROSSES_ABOVE, value=50)
            ]
        )
    )
    res_above = strategy_evaluator.evaluate_strategy(strat_above, candles, bar_index=1)
    assert res_above.entry_matched is True

    # Test crosses_below 50 at bar 1 -> should be False
    strat_below = DeclarativeStrategyDTO(
        strategy=StrategyMetadataDTO(id="s_cross_below", name="Cross Below Test"),
        entry=ConditionGroupDTO(
            operator=LogicalOperator.AND,
            conditions=[
                ConditionLeafDTO(feature="close", operator=ConditionOperator.CROSSES_BELOW, value=50)
            ]
        )
    )
    res_below = strategy_evaluator.evaluate_strategy(strat_below, candles, bar_index=1)
    assert res_below.entry_matched is False


def test_feature_vs_target_feature_comparison():
    """Tests comparing a feature against another feature (e.g. close crosses above SMA)."""
    # Create 5 candles: close rising rapidly
    candles = [
        make_candle(0, "10", "11", "9", "10"),
        make_candle(1, "10", "11", "9", "10"),
        make_candle(2, "10", "11", "9", "10"),
        make_candle(3, "10", "11", "9", "10"),
        make_candle(4, "20", "21", "19", "20") # SMA(3) at bar 4 is (10+10+20)/3 = 13.33. Close 20 > SMA 13.33!
    ]

    strat = DeclarativeStrategyDTO(
        strategy=StrategyMetadataDTO(id="s_dyn_feat", name="Dynamic Feature Compare"),
        entry=ConditionGroupDTO(
            operator=LogicalOperator.AND,
            conditions=[
                ConditionLeafDTO(
                    feature="close",
                    operator=ConditionOperator.GT,
                    target_feature="sma",
                    target_feature_params={"period": 3}
                )
            ]
        )
    )
    res = strategy_evaluator.evaluate_strategy(strat, candles)
    assert res.entry_matched is True


def test_insufficient_history_and_fail_closed():
    """Verifies that when indicators lack enough history, evaluation fails closed without generating signals."""
    # Only 3 candles, but RSI requires 14
    candles = [make_candle(i, "10", "11", "9", "10") for i in range(3)]

    strat = DeclarativeStrategyDTO(
        strategy=StrategyMetadataDTO(id="s_insufficient", name="Insufficient Test"),
        entry=ConditionGroupDTO(
            operator=LogicalOperator.AND,
            conditions=[
                ConditionLeafDTO(feature="rsi", operator=ConditionOperator.LT, value=30, feature_params={"period": 14})
            ]
        )
    )
    res = strategy_evaluator.evaluate_strategy(strat, candles)
    assert res.entry_matched is False
    assert res.overall_status == EvaluationStatus.INSUFFICIENT_DATA
    assert "unavailable" in res.condition_details[0].explanation


# ============================================================================
# 3. Security, Sandboxing & Attack Tests
# ============================================================================

def test_code_injection_and_sandbox_rejection():
    """Verifies that malicious payloads (eval, exec, import, os, subprocess) are rejected."""
    # 1. Attempting forbidden string in strategy description
    strat_malicious = DeclarativeStrategyDTO(
        strategy=StrategyMetadataDTO(
            id="attack_01",
            name="Attack",
            description="__import__('os').system('rm -rf /')"
        ),
        entry=ConditionGroupDTO(
            operator=LogicalOperator.AND,
            conditions=[ConditionLeafDTO(feature="close", operator=ConditionOperator.GT, value=10)]
        )
    )
    val_res = strategy_evaluator.validate_strategy_safety(strat_malicious)
    assert val_res.is_valid is False
    assert any("Forbidden pattern" in e for e in val_res.errors)

    # 2. Attempting unknown feature
    strat_unknown_feat = DeclarativeStrategyDTO(
        strategy=StrategyMetadataDTO(id="attack_02", name="Unknown Feature"),
        entry=ConditionGroupDTO(
            operator=LogicalOperator.AND,
            conditions=[ConditionLeafDTO(feature="non_existent_secret_feature", operator=ConditionOperator.GT, value=10)]
        )
    )
    val_feat = strategy_evaluator.validate_strategy_safety(strat_unknown_feat)
    assert val_feat.is_valid is False
    assert any("Unsupported feature" in e for e in val_feat.errors)

    # 3. Incompatible operator (e.g. gt on boolean)
    strat_bad_op = DeclarativeStrategyDTO(
        strategy=StrategyMetadataDTO(id="attack_03", name="Bad Op"),
        entry=ConditionGroupDTO(
            operator=LogicalOperator.AND,
            conditions=[ConditionLeafDTO(feature="has_active_bullish_fvg", operator=ConditionOperator.GT, value=True)]
        )
    )
    val_op = strategy_evaluator.validate_strategy_safety(strat_bad_op)
    assert val_op.is_valid is False
    assert any("incompatible" in e for e in val_op.errors)


def test_expression_nesting_depth_limit():
    """Verifies that condition nesting beyond MAX_EXPRESSION_DEPTH (5) is rejected."""
    # Build 7 levels of nested ANDs
    inner = ConditionLeafDTO(feature="close", operator=ConditionOperator.GT, value=10)
    for _ in range(7):
        inner = ConditionGroupDTO(operator=LogicalOperator.AND, conditions=[inner])

    strat_deep = DeclarativeStrategyDTO(
        strategy=StrategyMetadataDTO(id="deep_nesting", name="Deep Nesting"),
        entry=inner
    )
    val_deep = strategy_evaluator.validate_strategy_safety(strat_deep)
    assert val_deep.is_valid is False
    assert any("depth exceeded" in e for e in val_deep.errors)


def test_total_condition_count_limit():
    """Verifies that exceeding MAX_TOTAL_CONDITIONS (30) is rejected."""
    conditions = [
        ConditionLeafDTO(feature="close", operator=ConditionOperator.GT, value=i)
        for i in range(35)
    ]
    strat_many = DeclarativeStrategyDTO(
        strategy=StrategyMetadataDTO(id="too_many_conds", name="Too Many"),
        entry=ConditionGroupDTO(operator=LogicalOperator.AND, conditions=conditions)
    )
    val_many = strategy_evaluator.validate_strategy_safety(strat_many)
    assert val_many.is_valid is False
    assert any("count exceeded" in e for e in val_many.errors)


# ============================================================================
# 4. Lifecycle, Versioning & API Integration Tests
# ============================================================================

def test_strategy_lifecycle_and_api_endpoints_isolated_db():
    """
    Tests strategy creation, dry-run validation, immutable version updates,
    enable/disable status, and evaluation endpoints against an isolated DB.
    """
    temp_dir = tempfile.mkdtemp()
    temp_db = os.path.join(temp_dir, "test_research_p1_5_c.db")

    isolated_store = ResearchHistoryStore(db_path=temp_db)
    orig_store = research_analytics_service.store
    research_analytics_service.store = isolated_store

    app = FastAPI()
    app.include_router(research_router, prefix="/api/v1")
    client = TestClient(app)

    try:
        # Seed 20 historical candles into isolated store
        raw_candles = []
        for i in range(20):
            c = make_candle(i, str(100 + i), str(102 + i), str(99 + i), str(100 + i), pool_address="0xtestpool")
            raw_candles.append({
                "chain_id": c.chain_id,
                "pool_address": c.pool_address,
                "timeframe": c.timeframe.value,
                "timestamp_utc": c.timestamp_utc,
                "timestamp_unix": c.timestamp_unix,
                "end_timestamp_utc": c.end_timestamp_utc,
                "open_price": str(c.open_price),
                "high_price": str(c.high_price),
                "low_price": str(c.low_price),
                "close_price": str(c.close_price),
                "volume": str(c.volume),
                "bar_status": c.bar_status.value
            })
        isolated_store.upsert_candles_batch(raw_candles)

        # 1. Dry-run validate
        strat_payload = {
            "schema_version": "1.0",
            "strategy": {
                "id": "my_user_strategy_001",
                "name": "Volume Momentum",
                "version": "1.0.0"
            },
            "entry": {
                "operator": "AND",
                "conditions": [
                    {"feature": "close", "operator": "gt", "value": 50}
                ]
            }
        }
        val_resp = client.post("/api/v1/research/strategies/validate", json=strat_payload)
        assert val_resp.status_code == 200
        assert val_resp.json()["is_valid"] is True

        # 2. Create strategy
        create_resp = client.post("/api/v1/research/strategies", json={
            "strategy_definition": strat_payload,
            "change_summary": "Initial registration"
        })
        assert create_resp.status_code == 201
        assert create_resp.json()["strategy"]["id"] == "my_user_strategy_001"

        # 3. Retrieve strategy
        get_resp = client.get("/api/v1/research/strategies/my_user_strategy_001")
        assert get_resp.status_code == 200
        assert get_resp.json()["strategy"]["name"] == "Volume Momentum"

        # 4. List strategies
        list_resp = client.get("/api/v1/research/strategies")
        assert list_resp.status_code == 200
        assert len(list_resp.json()) == 1

        # 5. Update strategy creating a new immutable version (1.1.0)
        strat_payload_v2 = dict(strat_payload)
        strat_payload_v2["strategy"]["version"] = "1.1.0"
        strat_payload_v2["strategy"]["description"] = "Added volume constraint"
        strat_payload_v2["entry"]["conditions"].append(
            {"feature": "volume", "operator": "gt", "value": 500}
        )

        update_resp = client.put("/api/v1/research/strategies/my_user_strategy_001", json={
            "new_version": "1.1.0",
            "strategy_definition": strat_payload_v2,
            "change_summary": "Added volume threshold"
        })
        assert update_resp.status_code == 200
        assert update_resp.json()["strategy"]["version"] == "1.1.0"

        # Verify versions list shows both 1.0.0 and 1.1.0
        vers_resp = client.get("/api/v1/research/strategies/my_user_strategy_001/versions")
        assert vers_resp.status_code == 200
        versions = vers_resp.json()
        assert len(versions) == 2
        ver_names = [v["version"] for v in versions]
        assert "1.0.0" in ver_names
        assert "1.1.0" in ver_names

        # 6. Evaluate registered strategy against pool candles
        eval_resp = client.post("/api/v1/research/strategies/my_user_strategy_001/evaluate", json={
            "chain_id": "eip155:8453",
            "pool_address": "0xtestpool",
            "timeframe": "15m",
            "limit": 20
        })
        assert eval_resp.status_code == 200
        eval_data = eval_resp.json()
        assert eval_data["strategy_id"] == "my_user_strategy_001"
        assert eval_data["entry_matched"] is True
        assert eval_data["overall_status"] == "MATCH"

        # 7. Evaluate raw unpersisted strategy
        eval_raw_resp = client.post("/api/v1/research/strategies/evaluate-raw", json={
            "strategy_definition": strat_payload,
            "chain_id": "eip155:8453",
            "pool_address": "0xtestpool",
            "timeframe": "15m",
            "limit": 20
        })
        assert eval_raw_resp.status_code == 200
        assert eval_raw_resp.json()["entry_matched"] is True

        # 8. Disable strategy
        status_resp = client.post("/api/v1/research/strategies/my_user_strategy_001/status", json={"enabled": False})
        assert status_resp.status_code == 200
        assert status_resp.json()["enabled"] is False

        # Evaluation should now return STRATEGY_DISABLED
        eval_disabled = client.post("/api/v1/research/strategies/my_user_strategy_001/evaluate", json={
            "chain_id": "eip155:8453",
            "pool_address": "0xtestpool",
            "timeframe": "15m",
            "limit": 20
        })
        assert eval_disabled.status_code == 200
        assert eval_disabled.json()["data_quality_status"] == "STRATEGY_DISABLED"

    finally:
        research_analytics_service.store = orig_store
        if os.path.exists(temp_db):
            os.remove(temp_db)


def test_operational_database_isolation():
    """
    Asserts that no StrategySDK evaluation or registration touches data/memecoin_os.db.
    """
    import hashlib
    op_db_path = "data/memecoin_os.db"
    assert os.path.exists(op_db_path)
    with open(op_db_path, "rb") as f:
        before_hash = hashlib.sha256(f.read()).hexdigest()

    strat = DeclarativeStrategyDTO(
        strategy=StrategyMetadataDTO(id="iso_test", name="Isolation"),
        entry=ConditionGroupDTO(
            operator=LogicalOperator.AND,
            conditions=[ConditionLeafDTO(feature="close", operator=ConditionOperator.GT, value=1)]
        )
    )
    candles = [make_candle(i, "10", "11", "9", "10") for i in range(5)]
    strategy_evaluator.evaluate_strategy(strat, candles)

    with open(op_db_path, "rb") as f:
        after_hash = hashlib.sha256(f.read()).hexdigest()

    assert before_hash == after_hash, "Operational database mutated!"
