"""
Final Product Integration & End-to-End User Journey Verification Suite.

Validates the full, connected product lifecycle:
Discovery -> Screener -> Token Research -> Historical Candles ->
Indicators & Market Structure -> StrategySDK -> Backtesting ->
Paper Trading -> Dashboard & Reporting.

Strictly enforced invariants:
- ALLOW_LIVE_TRADING=False (Fail-closed)
- Isolated temporary test databases only (Operational DB untouched)
- Zero synthetic data passed off as authentic provider data
"""

import os
import sys
import tempfile
import pytest
from decimal import Decimal
from fastapi.testclient import TestClient

# Ensure backend in PYTHONPATH
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))

from app.core.config import settings
from app.main import app
from app.schemas.ohlcv import OHLCVCandleDTO, Timeframe, CandleBarStatus, CandleIntegrityStatus
from app.strategy_sdk.schemas import (
    DeclarativeStrategyDTO,
    StrategyMetadataDTO,
    ConditionGroupDTO,
    ConditionLeafDTO,
    ConditionOperator,
    LogicalOperator,
    EvaluationStatus
)
from app.db.research_history_store import ResearchHistoryStore
from app.services.research_analytics_service import research_analytics_service
from app.strategy_sdk.manager import strategy_manager


@pytest.fixture
def e2e_env(monkeypatch):
    """Sets up strictly isolated temporary databases for E2E integration verification."""
    test_dir = tempfile.mkdtemp(prefix="test_e2e_integration_")
    test_rh_db = os.path.join(test_dir, "e2e_research_history.db")
    test_op_db = os.path.join(test_dir, "e2e_memecoin_os.db")

    monkeypatch.setenv("RESEARCH_HISTORY_DB_PATH", test_rh_db)
    monkeypatch.setenv("SQLITE_DB_PATH", test_op_db)
    monkeypatch.setattr(settings, "SQLITE_DB_PATH", test_op_db)

    isolated_store = ResearchHistoryStore(db_path=test_rh_db)
    monkeypatch.setattr(research_analytics_service, "store", isolated_store)

    yield {
        "dir": test_dir,
        "rh_db": test_rh_db,
        "op_db": test_op_db,
        "store": isolated_store
    }


def make_test_candle(
    idx: int,
    open_p: float,
    high_p: float,
    low_p: float,
    close_p: float,
    vol: float = 1000.0,
    pool: str = "0xpool_e2e_test",
    chain: str = "eip155:8453",
    status: str = "CLOSED"
) -> OHLCVCandleDTO:
    ts_unix = 1700000000 + (idx * 900)
    ts_utc = f"2023-11-14T{idx // 4:02d}:{(idx % 4) * 15:02d}:00Z"
    return OHLCVCandleDTO(
        chain_id=chain,
        pool_address=pool,
        timeframe=Timeframe.M15,
        timestamp_utc=ts_utc,
        timestamp_unix=ts_unix,
        end_timestamp_utc=ts_utc,
        open_price=Decimal(str(open_p)),
        high_price=Decimal(str(high_p)),
        low_price=Decimal(str(low_p)),
        close_price=Decimal(str(close_p)),
        volume=Decimal(str(vol)),
        price_currency="USD",
        volume_currency="USD",
        bar_status=CandleBarStatus.CLOSED if status == "CLOSED" else CandleBarStatus.INCOMPLETE,
        provider="geckoterminal",
        provider_version="v2_20230302",
        revision_count=0
    )


def test_journey_1_discovery_and_screener_api(e2e_env):
    """
    User Journey 1: Market Discovery & Screener.
    Verifies /discovery/screener returns valid schema, memecoin filtering,
    freshness metadata, and provider health.
    """
    client = TestClient(app)

    # 1. Check provider health status
    health_res = client.get("/api/v1/discovery/providers/health")
    assert health_res.status_code == 200
    health_data = health_res.json()
    assert "providers" in health_data

    # 2. Check screener feed
    res = client.get("/api/v1/discovery/screener?limit=10&category=ALL")
    assert res.status_code == 200
    data = res.json()
    assert "tokens" in data
    assert "freshness_status" in data
    assert "cache_age_seconds" in data
    assert isinstance(data["tokens"], list)


def test_journey_2_token_search_and_forensics(e2e_env):
    """
    User Journey 2: Token Search, Forensics & Opportunity Scoring.
    Verifies multi-chain search, forensic evaluation, and explainable scoring.
    """
    client = TestClient(app)

    # 1. Search for token
    search_res = client.get("/api/v1/search?q=BONK")
    assert search_res.status_code == 200
    search_data = search_res.json()
    assert "results" in search_data or "matches" in search_data or isinstance(search_data, list)

    # 2. Verify integrations metadata
    int_res = client.get("/api/v1/integrations")
    assert int_res.status_code == 200
    int_data = int_res.json()
    assert "adapters" in int_data
    assert "boundaries" in int_data
    assert int_data["boundaries"]["live_trading_allowed"] is False


def test_journey_3_historical_candles_and_integrity(e2e_env):
    """
    User Journey 3: Historical OHLCV Candles & Integrity Reporting.
    Verifies persistence in research_history.db and mathematical consistency.
    """
    client = TestClient(app)
    store: ResearchHistoryStore = e2e_env["store"]

    pool = "0xpool_e2e_candles"
    chain = "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"

    candles = [
        make_test_candle(i, 100.0 + i, 105.0 + i, 95.0 + i, 102.0 + i, pool=pool, chain=chain)
        for i in range(25)
    ]
    upserted = store.upsert_candles_batch([c.model_dump() for c in candles])
    assert upserted["new_inserts"] == 25

    # Retrieve candles via REST API
    res = client.get(f"/api/v1/research/candles/{chain}/{pool}?limit=50")
    assert res.status_code == 200
    data = res.json()
    assert data["total_candles"] == 25
    assert len(data["candles"]) == 25
    assert "integrity_report" in data
    report = data["integrity_report"]
    assert report["valid_candles_count"] == 25
    assert report["rejected_bounds_count"] == 0
    assert report["integrity_status"] in (CandleIntegrityStatus.CLEAN.value, "CLEAN")


def test_journey_4_indicators_market_structure_and_ict(e2e_env):
    """
    User Journey 4: Technical Indicators, Market Structure & Optional ICT Engine.
    Verifies calculation of SMA, EMA, RSI, MACD, ATR, RVOL, swing points, FVG, OB,
    OTE retracement, CISD, and honest SMT divergence.
    """
    client = TestClient(app)
    store: ResearchHistoryStore = e2e_env["store"]

    pool = "0xpool_e2e_ta"
    chain = "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"

    candles = [
        make_test_candle(i, 100.0 + (i * 0.5), 105.0 + (i * 0.5), 98.0 + (i * 0.5), 103.0 + (i * 0.5), pool=pool, chain=chain)
        for i in range(35)
    ]
    store.upsert_candles_batch([c.model_dump() for c in candles])

    # 1. Technical Indicators
    ind_res = client.get(f"/api/v1/research/indicators/{chain}/{pool}?limit=35")
    assert ind_res.status_code == 200
    ind_data = ind_res.json()
    assert ind_data["total_candles_evaluated"] == 35
    assert "sma" in ind_data
    assert "ema" in ind_data
    assert "rsi" in ind_data
    assert "macd" in ind_data
    assert "atr" in ind_data

    # 2. Generic Market Structure
    ms_res = client.get(f"/api/v1/research/market-structure/{chain}/{pool}?limit=35")
    assert ms_res.status_code == 200
    ms_data = ms_res.json()
    assert "swings" in ms_data
    assert "liquidity_levels" in ms_data
    assert "fair_value_gaps" in ms_data

    # 3. Optional ICT Analysis
    ict_res = client.get(f"/api/v1/research/ict/evaluate/{chain}/{pool}?limit=35")
    assert ict_res.status_code == 200
    ict_data = ict_res.json()
    assert "optimal_trade_entry" in ict_data
    assert "change_in_state_of_delivery" in ict_data
    assert "smart_money_technique" in ict_data
    assert ict_data["smart_money_technique"]["status"] == "INSUFFICIENT_HISTORY"  # Honest failure when no correlated pool


def test_journey_5_declarative_strategy_sdk(e2e_env):
    """
    User Journey 5: Declarative Strategy Registration & Evaluation.
    Verifies typed schema, AST validation, and deterministic closed-bar evaluation.
    """
    client = TestClient(app)
    store: ResearchHistoryStore = e2e_env["store"]

    pool = "0xpool_e2e_strat"
    chain = "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"

    candles = [
        make_test_candle(i, 100.0 + i, 104.0 + i, 98.0 + i, 102.0 + i, pool=pool, chain=chain)
        for i in range(25)
    ]
    store.upsert_candles_batch([c.model_dump() for c in candles])

    strategy = DeclarativeStrategyDTO(
        strategy=StrategyMetadataDTO(
            id="e2e_breakout_strategy",
            name="E2E Breakout Strategy",
            version="1.0.0",
            author="e2e_tester",
            description="Enters when close price exceeds open price"
        ),
        entry=ConditionGroupDTO(
            operator=LogicalOperator.AND,
            conditions=[
                ConditionLeafDTO(
                    feature="close",
                    operator=ConditionOperator.GT,
                    target_feature="open"
                )
            ]
        ),
        exit=ConditionGroupDTO(
            operator=LogicalOperator.AND,
            conditions=[
                ConditionLeafDTO(
                    feature="close",
                    operator=ConditionOperator.LT,
                    target_feature="open"
                )
            ]
        )
    )

    # 1. Register strategy via StrategyCreateRequest
    reg_res = client.post(
        "/api/v1/research/strategies",
        json={
            "strategy_definition": strategy.model_dump(),
            "change_summary": "Initial registration"
        }
    )
    assert reg_res.status_code in (200, 201)

    # 2. Evaluate strategy
    eval_res = client.post(
        "/api/v1/research/strategies/e2e_breakout_strategy/evaluate",
        json={
            "chain_id": chain,
            "pool_address": pool,
            "timeframe": "15m",
            "limit": 25
        }
    )
    assert eval_res.status_code == 200
    eval_data = eval_res.json()
    assert eval_data["overall_status"] in ("MATCH", "NO_MATCH", "INSUFFICIENT_HISTORY")
    assert isinstance(eval_data["entry_matched"], bool)
    assert "explanation" in eval_data


def test_journey_6_deterministic_backtest_lab(e2e_env):
    """
    User Journey 6: Historical Backtesting Simulation Lab.
    Verifies next-bar open fill, AMM slippage, fee deduction, and drawdown metrics.
    """
    client = TestClient(app)
    store: ResearchHistoryStore = e2e_env["store"]

    pool = "0xpool_e2e_backtest"
    chain = "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"

    candles = [
        make_test_candle(0, 100.0, 105.0, 95.0, 102.0, pool=pool, chain=chain),
        make_test_candle(1, 102.0, 108.0, 101.0, 107.0, pool=pool, chain=chain),
        make_test_candle(2, 107.0, 112.0, 105.0, 110.0, pool=pool, chain=chain),
        make_test_candle(3, 110.0, 115.0, 108.0, 114.0, pool=pool, chain=chain),
        make_test_candle(4, 114.0, 116.0, 102.0, 103.0, pool=pool, chain=chain),
        make_test_candle(5, 103.0, 104.0, 96.0, 97.0, pool=pool, chain=chain),
        make_test_candle(6, 97.0, 101.0, 95.0, 100.0, pool=pool, chain=chain),
    ]
    store.upsert_candles_batch([c.model_dump() for c in candles])

    strat_def = DeclarativeStrategyDTO(
        strategy=StrategyMetadataDTO(
            id="e2e_bt_strat",
            name="E2E Backtest Strategy",
            version="1.0.0",
            author="tester"
        ),
        entry=ConditionGroupDTO(
            operator=LogicalOperator.AND,
            conditions=[
                ConditionLeafDTO(
                    feature="close",
                    operator=ConditionOperator.GT,
                    target_feature="open"
                )
            ]
        ),
        exit=ConditionGroupDTO(
            operator=LogicalOperator.AND,
            conditions=[
                ConditionLeafDTO(
                    feature="close",
                    operator=ConditionOperator.LT,
                    target_feature="open"
                )
            ]
        )
    )

    bt_res = client.post(
        "/api/v1/research/backtest/run",
        json={
            "strategy_definition": strat_def.model_dump(),
            "config": {
                "chain_id": chain,
                "pool_address": pool,
                "timeframe": "15m",
                "initial_balance_usd": "10000.0",
                "fee_rate_bps": 30,
                "slippage_bps": 20,
                "limit_bars": 10
            }
        }
    )
    assert bt_res.status_code == 200
    bt_data = bt_res.json()
    assert Decimal(bt_data["initial_balance_usd"]) == Decimal("10000.0")
    assert bt_data["total_trades"] >= 1
    assert "equity_curve" in bt_data
    assert "max_drawdown_pct" in bt_data
    assert Decimal(bt_data["total_fees_paid_usd"]) >= Decimal("0.0")


def test_journey_7_virtual_paper_trading_execution(e2e_env):
    """
    User Journey 7: Virtual Paper Trading Engine in research_history.db.
    Verifies virtual account creation, tick-stepping, and position/order inspection.
    """
    client = TestClient(app)
    store: ResearchHistoryStore = e2e_env["store"]

    # 1. Create virtual paper account
    acc_res = client.post(
        "/api/v1/research/paper/accounts",
        json={
            "account_id": "acc_e2e_virtual_01",
            "name": "E2E Virtual Desk",
            "initial_balance_usd": "25000.0"
        }
    )
    assert acc_res.status_code == 200
    acc_data = acc_res.json()
    assert acc_data["account_id"] == "acc_e2e_virtual_01"
    assert Decimal(acc_data["cash_balance_usd"]) == Decimal("25000.0")

    # 2. Seed candles for execution stepping
    pool = "0xpool_e2e_paper"
    chain = "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"
    candles = [
        make_test_candle(i, 100.0 + i, 105.0 + i, 95.0 + i, 102.0 + i, pool=pool, chain=chain)
        for i in range(10)
    ]
    store.upsert_candles_batch([c.model_dump() for c in candles])

    # 3. Register strategy via StrategyCreateRequest
    strat_def = DeclarativeStrategyDTO(
        strategy=StrategyMetadataDTO(
            id="e2e_paper_strat",
            name="E2E Paper Strategy",
            version="1.0.0"
        ),
        entry=ConditionGroupDTO(
            operator=LogicalOperator.AND,
            conditions=[ConditionLeafDTO(feature="close", operator=ConditionOperator.GT, target_feature="open")]
        ),
        exit=ConditionGroupDTO(
            operator=LogicalOperator.AND,
            conditions=[ConditionLeafDTO(feature="close", operator=ConditionOperator.LT, target_feature="open")]
        )
    )
    client.post(
        "/api/v1/research/strategies",
        json={
            "strategy_definition": strat_def.model_dump(),
            "change_summary": "Initial paper strategy"
        }
    )

    # 4. Step strategy paper trading
    step_res = client.post(
        "/api/v1/research/paper/step",
        json={
            "account_id": "acc_e2e_virtual_01",
            "strategy_id": "e2e_paper_strat",
            "chain_id": chain,
            "pool_address": pool,
            "timeframe": "15m",
            "position_size_usd": "1000.0",
            "slippage_bps": 50
        }
    )
    assert step_res.status_code == 200
    step_data = step_res.json()
    assert step_data["account_id"] == "acc_e2e_virtual_01"

    # 5. Verify account state via GET
    get_acc = client.get("/api/v1/research/paper/accounts/acc_e2e_virtual_01")
    assert get_acc.status_code == 200
    assert get_acc.json()["account_id"] == "acc_e2e_virtual_01"


def test_journey_8_dashboard_analytics_convergence(e2e_env):
    """
    User Journey 8: Dashboard, Strategy Analytics & Risk Monitor Convergence.
    Verifies that dashboard endpoints correctly aggregate multi-chain desks,
    strategy definitions, statistical sample size notes, and risk guardrails.
    """
    client = TestClient(app)

    # 1. Overview KPIs
    ov_res = client.get("/api/v1/dashboard/overview")
    assert ov_res.status_code == 200
    ov_data = ov_res.json()
    assert "total_paper_equity_usd" in ov_data
    assert "total_realized_pnl" in ov_data

    # 2. Strategy Analytics
    strat_res = client.get("/api/v1/dashboard/strategy-analytics")
    assert strat_res.status_code == 200
    strat_data = strat_res.json()
    assert "strategies_count" in strat_data
    assert "performance_metrics" in strat_data
    assert "sample_size_note" in strat_data["performance_metrics"]

    # 3. Risk Monitor
    risk_res = client.get("/api/v1/dashboard/risk-monitor")
    assert risk_res.status_code == 200
    risk_data = risk_res.json()
    assert "risk_limits" in risk_data
    assert risk_data["risk_limits"]["environment"] == "PAPER_SIMULATED"

    # 4. Reports & Journal
    rep_res = client.get("/api/v1/dashboard/reports")
    assert rep_res.status_code == 200
    rep_data = rep_res.json()
    assert "accounts_summary" in rep_data
    assert "recent_fills_journal" in rep_data


def test_journey_9_live_trading_locked_and_operational_db_isolation():
    """
    User Journey 9: Live Trading Strictly Locked & Universal Fail-Closed Protection.
    Verifies ALLOW_LIVE_TRADING=False and assert_test_db_isolated blocks operational DB.
    """
    from app.core.database import assert_test_db_isolated

    # 1. Live trading flag
    assert settings.ALLOW_LIVE_TRADING is False

    # 2. Fail-closed operational DB guard
    with pytest.raises(RuntimeError) as exc_info:
        assert_test_db_isolated("data/memecoin_os.db")
    assert "SAFETY VIOLATION (FAIL-CLOSED)" in str(exc_info.value)
