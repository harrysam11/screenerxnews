"""
Comprehensive Final Ship Test Suite for P1.5:
- ICT Technical Concepts (OTE, CISD, SMT Divergence)
- Deterministic Historical Backtesting Engine (No-Lookahead, Next-Bar Fill, Realistic Fees/Slippage, SL/TP)
- Virtual Paper Trading Engine in research_history.db (Accounts, Orders, Positions, Idempotency)
- REST API Endpoints in /api/v1/research
- Strict Invariant Verification (ALLOW_LIVE_TRADING=False, operational DB isolation)
"""

import os
import sys
import tempfile
import pytest
from decimal import Decimal
from fastapi.testclient import TestClient

# Ensure backend in PYTHONPATH
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))

from app.schemas.ohlcv import OHLCVCandleDTO, Timeframe, CandleBarStatus
from app.strategy_sdk.schemas import (
    DeclarativeStrategyDTO,
    StrategyMetadataDTO,
    ConditionGroupDTO,
    ConditionLeafDTO,
    ConditionOperator,
    LogicalOperator,
    EvaluationStatus
)
from app.strategy_sdk.ict import ict_engine, ICTStrategyEngine
from app.schemas.backtesting import (
    BacktestConfigDTO,
    TradeSide,
    TradeExitReason,
    BacktestRunRequest
)
from app.services.backtesting_engine import backtesting_engine
from app.schemas.research_paper import (
    PaperAccountCreateRequest,
    StrategyPaperStepRequest,
    PaperOrderStatus,
    PaperOrderSide
)
from app.services.research_paper_engine import research_paper_engine
from app.strategy_sdk.manager import strategy_manager
from app.db.research_history_store import ResearchHistoryStore
from app.services.research_analytics_service import research_analytics_service
from app.core.config import settings
from app.main import app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def isolated_env(monkeypatch):
    """Sets up completely isolated temporary databases for research testing."""
    test_dir = tempfile.mkdtemp()
    test_rh_db = os.path.join(test_dir, "test_research_history.db")
    test_op_db = os.path.join(test_dir, "test_memecoin_os.db")

    monkeypatch.setenv("RESEARCH_HISTORY_DB_PATH", test_rh_db)
    monkeypatch.setenv("SQLITE_DB_PATH", test_op_db)
    monkeypatch.setattr(settings, "SQLITE_DB_PATH", test_op_db)

    # Re-initialize store with new path
    isolated_store = ResearchHistoryStore(db_path=test_rh_db)
    monkeypatch.setattr(research_analytics_service, "store", isolated_store)

    yield {
        "dir": test_dir,
        "rh_db": test_rh_db,
        "op_db": test_op_db,
        "store": isolated_store
    }


def make_candle(
    idx: int,
    open_p: float,
    high_p: float,
    low_p: float,
    close_p: float,
    vol: float = 1000.0,
    pool: str = "0xpool123",
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
        bar_status=CandleBarStatus(status),
        provider="geckoterminal",
        provider_version="v2_20230302",
        revision_count=0
    )


# ---------------------------------------------------------------------------
# P1.5-D: ICT Module Tests
# ---------------------------------------------------------------------------

def test_ict_bullish_and_bearish_ote_retracement():
    """
    Verifies ICT Optimal Trade Entry (0.618 - 0.786 retracement).
    Bullish: Swing Low at 100, Swing High at 200 (range = 100).
    Equilibrium 50% = 150.
    OTE 61.8% = 138.2.
    OTE 78.6% = 121.4.
    Price retracing to 130 is inside Bullish OTE.
    """
    candles = [
        make_candle(0, 100, 105, 95, 102),
        make_candle(1, 102, 103, 90, 92),    # Swing Low at index 1: low=90
        make_candle(2, 92, 110, 91, 108),
        make_candle(3, 108, 150, 105, 145),
        make_candle(4, 145, 200, 140, 195),  # Swing High at index 4: high=200
        make_candle(5, 195, 196, 160, 165),
        make_candle(6, 165, 170, 130, 132),  # Retracement into 132 (between 121.4 and 138.2)
        make_candle(7, 132, 135, 128, 130),  # Close inside OTE zone
    ]

    ote_res = ict_engine.calculate_ote_zones(candles, lookback=1, lookforward=1)
    assert ote_res["status"] == "CLEAN"
    assert ote_res["trend_direction"] == "BULLISH"
    assert ote_res["in_bullish_ote"] is True
    assert ote_res["in_bearish_ote"] is False
    assert Decimal(ote_res["ote_618"]) == Decimal("132.02")  # 200 - (110 * 0.618) = 132.02
    assert Decimal(ote_res["ote_786"]) == Decimal("113.54")  # 200 - (110 * 0.786) = 113.54


def test_ict_cisd_detection():
    """
    Verifies Change in State of Delivery (CISD) when a decisive candle closes
    beyond opposing candle sequences.
    """
    # Create 3 red candles followed by strong green candle closing above all 3
    candles = [
        make_candle(0, 100, 102, 95, 96),   # Red: Open 100, Close 96, High 102
        make_candle(1, 96, 97, 90, 91),     # Red: Open 96, Close 91, High 97
        make_candle(2, 91, 92, 85, 86),     # Red: Open 91, Close 86, High 92
        make_candle(3, 86, 105, 85, 104),   # Green: Close 104 > max(102, 97, 92)
    ]
    cisd_res = ict_engine.evaluate_cisd(candles)
    assert cisd_res["status"] == "CLEAN"
    assert cisd_res["bullish_cisd"] is True
    assert cisd_res["bearish_cisd"] is False
    assert cisd_res["reference_level"] == "102"


def test_ict_smt_divergence_honesty_and_detection():
    """
    Verifies SMT divergence:
    - Fails closed with INSUFFICIENT_HISTORY if secondary pool data is missing.
    - Accurately detects divergence when secondary pool is provided.
    """
    candles_a = [
        make_candle(0, 100, 105, 95, 100),
        make_candle(1, 100, 101, 80, 85),   # Low 1 = 80
        make_candle(2, 85, 110, 84, 105),
        make_candle(3, 105, 106, 75, 78),   # Low 2 = 75 (Lower Low)
        make_candle(4, 78, 90, 77, 85),
    ]

    # Without secondary pool: MUST report insufficient data honestly
    smt_no_b = ict_engine.evaluate_smt_divergence(candles_a, candles_asset_b=None)
    assert smt_no_b["status"] == "INSUFFICIENT_HISTORY"
    assert smt_no_b["bullish_smt"] is False

    # Correlated asset B makes a HIGHER Low (170 -> 175)
    candles_b = [
        make_candle(0, 200, 210, 190, 200),
        make_candle(1, 200, 202, 170, 175), # Low 1 = 170
        make_candle(2, 190, 220, 185, 210), # Open 190, High 220, Low 185, Close 210
        make_candle(3, 210, 212, 175, 180), # Low 2 = 175 (Higher Low >= 170)
        make_candle(4, 180, 195, 179, 190),
    ]

    smt_divergence = ict_engine.evaluate_smt_divergence(
        candles_a,
        candles_b,
        lookback=1,
        lookforward=1
    )
    assert smt_divergence["status"] == "CLEAN"
    assert smt_divergence["bullish_smt"] is True
    assert "Bullish SMT" in smt_divergence["explanation"]


# ---------------------------------------------------------------------------
# P1.5-E: Backtesting Engine Tests
# ---------------------------------------------------------------------------

def test_backtest_execution_no_lookahead_and_slippage_fees():
    """
    Tests historical backtest simulation:
    - Signal triggered at Bar t Close.
    - Fill happens at Bar t+1 Open (strictly no lookahead).
    - Slippage and DEX fee deductions are properly calculated.
    - End of dataset liquidates any remaining position.
    """
    # Simple strategy: Buy when Close > Open
    strat = DeclarativeStrategyDTO(
        strategy=StrategyMetadataDTO(id="strat_test_long", name="Test Long Strategy"),
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

    candles = [
        make_candle(0, 10, 12, 9, 11),   # Bar 0: C(11) > O(10) -> ENTRY SIGNAL at Close
        make_candle(1, 12, 15, 11, 14),  # Bar 1: Fill BUY at Open(12) + 20 bps slippage
        make_candle(2, 14, 16, 13, 15),  # Bar 2: In trade
        make_candle(3, 15, 15.5, 9.5, 10), # Bar 3: C(10) < O(15) -> EXIT SIGNAL at Close
        make_candle(4, 10, 11, 8, 9),    # Bar 4: Fill SELL at Open(10) - 20 bps slippage
        make_candle(5, 9, 10, 8.5, 9.5), # Bar 5: Flat
    ]

    config = BacktestConfigDTO(
        chain_id="eip155:8453",
        pool_address="0xpool123",
        timeframe=Timeframe.M15,
        initial_balance_usd=Decimal("10000.0"),
        position_size_usd=Decimal("1000.0"),
        fee_rate_bps=30,      # 0.30%
        slippage_bps=20,      # 0.20%
        limit_bars=20
    )

    res = backtesting_engine.run_backtest(strat, candles, config)

    assert res.total_trades == 1
    t = res.trades[0]
    assert t.side == TradeSide.BUY
    assert t.entry_bar_index == 1
    # Entry price must be Open(12) * (1 + 0.002) = 12.024
    assert t.entry_price == Decimal("12.024")
    assert t.exit_bar_index == 4
    # Exit price must be Open(10) * (1 - 0.002) = 9.98
    assert t.exit_price == Decimal("9.98")
    assert t.exit_reason == TradeExitReason.SIGNAL
    assert t.net_pnl_usd < Decimal("0") # Loss trade
    assert res.ending_balance_usd == res.initial_balance_usd + res.net_pnl_usd


def test_backtest_stop_loss_trigger():
    """Verifies that a stop loss trigger exits the trade immediately during the bar."""
    strat = DeclarativeStrategyDTO(
        strategy=StrategyMetadataDTO(id="strat_test_sl", name="Test Stop Loss Strategy"),
        entry=ConditionGroupDTO(
            operator=LogicalOperator.AND,
            conditions=[
                ConditionLeafDTO(
                    feature="close",
                    operator=ConditionOperator.GT,
                    target_feature="open"
                )
            ]
        )
    )

    candles = [
        make_candle(0, 10, 12, 9, 11),   # Bar 0: Entry Signal
        make_candle(1, 12, 14, 11, 13),  # Bar 1: Fill Buy at 12 + slip
        make_candle(2, 13, 13.5, 8.0, 9), # Bar 2: Low plunges to 8.0 (triggers 10% SL)
        make_candle(3, 9, 10, 8.5, 9.5),
    ]

    config = BacktestConfigDTO(
        chain_id="eip155:8453",
        pool_address="0xpool123",
        timeframe=Timeframe.M15,
        initial_balance_usd=Decimal("10000.0"),
        position_size_usd=Decimal("1000.0"),
        stop_loss_pct=Decimal("10.0"), # 10% Stop Loss
        fee_rate_bps=30,
        slippage_bps=20,
        limit_bars=20
    )

    res = backtesting_engine.run_backtest(strat, candles, config)
    assert res.total_trades == 1
    t = res.trades[0]
    assert t.exit_reason == TradeExitReason.STOP_LOSS
    assert t.exit_bar_index == 2


# ---------------------------------------------------------------------------
# P1.5-F: Virtual Strategy Paper Trading Tests
# ---------------------------------------------------------------------------

def test_paper_trading_account_and_virtual_execution(isolated_env):
    """
    Tests research paper trading engine:
    - Account creation & duplicate rejection.
    - Evaluates strategy on candle and executes simulated order.
    - Idempotency prevents duplicate order execution.
    - Position mark-to-market and exit execution.
    """
    store = isolated_env["store"]

    # 1. Create Account
    acc = research_paper_engine.create_account("acc_alpha", "Alpha Test Desk", Decimal("10000.0"))
    assert acc.account_id == "acc_alpha"
    assert acc.cash_balance_usd == Decimal("10000.0")

    # Duplicate account should fail
    with pytest.raises(ValueError):
        research_paper_engine.create_account("acc_alpha", "Alpha Test Desk")

    # 2. Register a simple strategy in research_history.db
    strat = DeclarativeStrategyDTO(
        strategy=StrategyMetadataDTO(id="strat_paper_test", name="Paper Momentum"),
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
    strategy_manager.register_strategy(strat)

    # 3. Step Paper Trading with Entry Signal
    candle_entry = make_candle(0, 10, 12, 9, 11) # Close(11) > Open(10)
    step1 = research_paper_engine.step_strategy_paper_trading(
        account_id="acc_alpha",
        strategy_id="strat_paper_test",
        candles=[candle_entry],
        position_size_usd=Decimal("1000.0")
    )

    assert step1.evaluation_matched_entry is True
    assert step1.order_executed is True
    assert step1.order is not None
    assert step1.order.side == PaperOrderSide.BUY
    assert step1.account_summary.cash_balance_usd < Decimal("10000.0")
    assert len(step1.account_summary.open_positions) == 1

    # 4. Idempotency Check: Stepping on the same candle should NOT execute a second order
    step_idempotent = research_paper_engine.step_strategy_paper_trading(
        account_id="acc_alpha",
        strategy_id="strat_paper_test",
        candles=[candle_entry],
        position_size_usd=Decimal("1000.0")
    )
    assert step_idempotent.order_executed is False

    # 5. Step Paper Trading with Exit Signal
    candle_exit = make_candle(1, 15, 16, 12, 13) # Close(13) < Open(15) -> Exit trigger
    step2 = research_paper_engine.step_strategy_paper_trading(
        account_id="acc_alpha",
        strategy_id="strat_paper_test",
        candles=[candle_entry, candle_exit],
        position_size_usd=Decimal("1000.0")
    )

    assert step2.evaluation_matched_exit is True
    assert step2.order_executed is True
    assert step2.order.side == PaperOrderSide.SELL
    assert len(step2.account_summary.open_positions) == 0
    # Profit was realized (entry around 11, exit around 13)
    assert step2.account_summary.realized_pnl_usd > Decimal("0")


# ---------------------------------------------------------------------------
# API Integration Tests
# ---------------------------------------------------------------------------

def test_api_ict_backtest_and_paper_endpoints(isolated_env):
    """Verifies all new research REST endpoints."""
    store = isolated_env["store"]
    client = TestClient(app)

    # Ingest a series of candles
    candles = [
        make_candle(0, 100, 110, 95, 105),
        make_candle(1, 105, 108, 90, 92),
        make_candle(2, 92, 115, 91, 110),
        make_candle(3, 110, 150, 108, 145),
        make_candle(4, 145, 200, 140, 195),
        make_candle(5, 195, 196, 160, 165),
        make_candle(6, 165, 170, 130, 135),
        make_candle(7, 135, 140, 128, 132),
    ]
    store.upsert_candles_batch([c.model_dump() for c in candles])

    # 1. Test ICT API
    resp_ict = client.get("/api/v1/research/ict/evaluate/eip155:8453/0xpool123?timeframe=15m")
    assert resp_ict.status_code == 200
    data_ict = resp_ict.json()
    assert "optimal_trade_entry" in data_ict
    assert "change_in_state_of_delivery" in data_ict
    assert "smart_money_technique" in data_ict

    # 2. Test Backtest API
    strat_def = {
        "strategy": {
            "id": "api_backtest_strat",
            "name": "API Backtest"
        },
        "entry": {
            "operator": "AND",
            "conditions": [
                {"feature": "close", "operator": "gt", "target_feature": "open"}
            ]
        }
    }
    resp_bt = client.post("/api/v1/research/backtest/run", json={
        "strategy_definition": strat_def,
        "config": {
            "chain_id": "eip155:8453",
            "pool_address": "0xpool123",
            "timeframe": "15m",
            "initial_balance_usd": 10000.0,
            "fee_rate_bps": 30,
            "slippage_bps": 20,
            "limit_bars": 10
        }
    })
    assert resp_bt.status_code == 200
    data_bt = resp_bt.json()
    assert "backtest_id" in data_bt
    assert "ending_balance_usd" in data_bt
    assert "win_rate_pct" in data_bt

    # 3. Test Paper Accounts API
    resp_create_acc = client.post("/api/v1/research/paper/accounts", json={
        "account_id": "api_paper_desk",
        "name": "API Paper Desk",
        "initial_balance_usd": 50000.0
    })
    assert resp_create_acc.status_code == 200
    assert resp_create_acc.json()["account_id"] == "api_paper_desk"

    resp_list_accs = client.get("/api/v1/research/paper/accounts")
    assert resp_list_accs.status_code == 200
    assert any(a["account_id"] == "api_paper_desk" for a in resp_list_accs.json())

    resp_get_acc = client.get("/api/v1/research/paper/accounts/api_paper_desk")
    assert resp_get_acc.status_code == 200
    assert resp_get_acc.json()["name"] == "API Paper Desk"


def test_strict_operational_database_hash_invariant():
    """
    Ensures that the operational database hash is strictly preserved and
    ALLOW_LIVE_TRADING is False.
    """
    assert settings.ALLOW_LIVE_TRADING is False

    # Check memecoin_os.db hash
    import hashlib
    op_db_path = "/home/sam/Documents/screenerxnews/data/memecoin_os.db"
    if os.path.exists(op_db_path):
        with open(op_db_path, "rb") as f:
            digest = hashlib.sha256(f.read()).hexdigest()
        assert digest == "6395fe0ca50000a69bb13cc4bb612318c1773ce04015c96946a1e7c1ce6fbcf8"
