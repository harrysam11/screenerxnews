import sys
import os
import uuid
import pytest
from decimal import Decimal
from datetime import datetime, timezone
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath("./backend"))
from app.main import app
from app.schemas.contracts import (
    TokenDiscoveryResponse, TokenSecurityReport, FindingItem, TriState, RiskBandEnum, EvidenceStatus
)
from app.strategy.base import BaseStrategy, StrategySignal, SignalAction
from app.strategy.builtins.momentum import MomentumBreakoutStrategy
from app.strategy.builtins.safety_first import SafetyFirstConservativeStrategy
from app.strategy.runner import StrategyRegistry, StrategyRunner
from app.services.paper_simulation_engine import PaperSimulationEngine

client = TestClient(app)

def create_sample_token(
    mint: str = "TestToken111111111111111111111111111111111",
    liquidity: float = 60000.0,
    volume_1h: float = 12000.0,
    buy_sell_ratio: float = 1.8,
    risk_score: float = 15.0,
    mint_revoked: TriState = TriState.TRUE,
    lp_burned: TriState = TriState.TRUE
) -> TokenDiscoveryResponse:
    return TokenDiscoveryResponse(
        mint_address=mint,
        symbol="MOMENTUM",
        name="Momentum Coin",
        decimals=9,
        price_usd=Decimal("0.00005"),
        liquidity_usd=Decimal(str(liquidity)),
        volume_5m=Decimal("1000.0"),
        volume_1h=Decimal(str(volume_1h)),
        volume_24h=Decimal("50000.0"),
        price_change_5m=Decimal("2.0"),
        price_change_1h=Decimal("6.5"),
        buy_sell_ratio_1h=Decimal(str(buy_sell_ratio)),
        risk_score=Decimal(str(risk_score)),
        risk_band=RiskBandEnum.LOW,
        composite_opportunity_score=Decimal("82.0"),
        lp_burned=lp_burned,
        mint_revoked=mint_revoked,
        is_token_2022=TriState.FALSE,
        source="dexscreener:pair_feed",
        observed_at=datetime.now(timezone.utc)
    )

def test_strategy_registry_lifecycle():
    registry = StrategyRegistry()
    strats = registry.list_strategies()
    strat_ids = [s["strategy_id"] for s in strats]
    assert "momentum_breakout" in strat_ids
    assert "safety_first_conservative" in strat_ids

    # Register custom strategy
    class CustomScalpStrategy(BaseStrategy):
        def on_discovery(self, token):
            return None
        def on_forensics(self, report):
            return None

    custom = CustomScalpStrategy(strategy_id="custom_scalp", description="Scalp test strategy")
    registry.register(custom)
    assert registry.get("custom_scalp") is not None
    assert len(registry.list_strategies()) == 3

    # Unregister
    assert registry.unregister("custom_scalp") is True
    assert registry.get("custom_scalp") is None
    assert len(registry.list_strategies()) == 2

def test_momentum_breakout_strategy_logic():
    strat = MomentumBreakoutStrategy()
    
    # 1. Matching token -> Generates BUY signal
    valid_token = create_sample_token(liquidity=80000.0, volume_1h=15000.0, buy_sell_ratio=2.0, risk_score=18.0)
    sig = strat.on_discovery(valid_token)
    assert sig is not None
    assert sig.action == SignalAction.BUY
    assert sig.confidence_score >= Decimal("70.0")
    assert sig.asset_id == valid_token.mint_address

    # 2. Low volume -> No signal
    low_vol_token = create_sample_token(volume_1h=1000.0)
    assert strat.on_discovery(low_vol_token) is None

    # 3. High risk -> No signal
    high_risk_token = create_sample_token(risk_score=65.0)
    assert strat.on_discovery(high_risk_token) is None

    # 4. Forensic emergency sell on honeypot
    now = datetime.now(timezone.utc)
    honeypot_report = TokenSecurityReport(
        mint_address=valid_token.mint_address,
        is_token_2022=TriState.FALSE,
        is_mint_revoked=TriState.FALSE,
        is_freeze_revoked=TriState.FALSE,
        is_honeypot=TriState.TRUE, # Honeypot!
        lp_status="unverified",
        lp_burned=TriState.FALSE,
        confidence=EvidenceStatus.VERIFIED,
        risk_score=Decimal("95.0"),
        risk_level="CRITICAL",
        findings=[],
        scanned_at=now,
        source="solana_rpc",
        observed_at=now
    )
    sell_sig = strat.on_forensics(honeypot_report)
    assert sell_sig is not None
    assert sell_sig.action == SignalAction.SELL
    assert sell_sig.confidence_score == Decimal("100.0")

def test_safety_first_conservative_strategy_logic():
    strat = SafetyFirstConservativeStrategy()

    # 1. Discovery without full verification of burn/revocation -> No signal
    unverified_token = create_sample_token(mint_revoked=TriState.UNKNOWN, lp_burned=TriState.TRUE)
    assert strat.on_discovery(unverified_token) is None

    # 2. Discovery with verified fundamentals -> Conservative BUY signal
    verified_token = create_sample_token(liquidity=100000.0, risk_score=10.0, mint_revoked=TriState.TRUE, lp_burned=TriState.TRUE)
    sig = strat.on_discovery(verified_token)
    assert sig is not None
    assert sig.action == SignalAction.BUY
    assert sig.max_slippage_bps == 150

    # 3. Full forensic verification
    now = datetime.now(timezone.utc)
    clean_report = TokenSecurityReport(
        mint_address=verified_token.mint_address,
        is_token_2022=TriState.FALSE,
        is_mint_revoked=TriState.TRUE,
        is_freeze_revoked=TriState.TRUE,
        is_honeypot=TriState.FALSE,
        lp_status="burned",
        lp_burned=TriState.TRUE,
        confidence=EvidenceStatus.VERIFIED,
        risk_score=Decimal("12.5"),
        risk_level="LOW",
        findings=[],
        scanned_at=now,
        source="solana_rpc",
        observed_at=now
    )
    forensic_sig = strat.on_forensics(clean_report)
    assert forensic_sig is not None
    assert forensic_sig.action == SignalAction.BUY
    assert forensic_sig.confidence_score == Decimal("95.0")

def test_signal_to_paper_execution_pipeline():
    registry = StrategyRegistry()
    runner = StrategyRunner(registry)
    isolated_paper_engine = PaperSimulationEngine()

    token = create_sample_token(liquidity=75000.0, volume_1h=20000.0, buy_sell_ratio=2.2, risk_score=12.0)
    signals = runner.evaluate_discovery(token, strategy_ids=["momentum_breakout"])
    assert len(signals) == 1
    sig = signals[0]
    assert sig.action == SignalAction.BUY

    # Execute signal on paper desk
    paper_order = runner.execute_signal_on_paper(
        signal=sig,
        paper_engine=isolated_paper_engine,
        pool_liquidity_usd=Decimal("200000.0"),
        current_market_price_native=Decimal("0.00005")
    )
    assert paper_order.status == "FILLED"
    assert paper_order.side == "BUY"
    assert len(paper_order.execution_events) == 1

    # Verify paper engine holds the new position
    positions = isolated_paper_engine.get_positions("solana:mainnet")
    assert len(positions) == 1
    assert positions[0].asset_address == token.mint_address

def test_backtesting_runner():
    registry = StrategyRegistry()
    runner = StrategyRunner(registry)

    observations = [
        create_sample_token(mint=f"Token_{i}", liquidity=40000.0 + (i * 10000.0), volume_1h=2000.0 * i, buy_sell_ratio=1.0 + (i * 0.3))
        for i in range(1, 6)
    ]
    report = runner.backtest(observations, strategy_id="momentum_breakout")
    assert report["strategy_id"] == "momentum_breakout"
    assert report["observations_evaluated"] == 5
    assert report["signals_generated"] > 0
    assert report["buy_signals"] == report["signals_generated"]
    assert report["avg_confidence"] > Decimal("50.0")

def test_api_strategies_endpoints():
    # 1. List strategies
    list_resp = client.get("/api/v1/strategies")
    assert list_resp.status_code == 200
    strats = list_resp.json()
    assert len(strats) >= 2
    strat_ids = [s["strategy_id"] for s in strats]
    assert "momentum_breakout" in strat_ids

    # 2. Evaluate token
    token_payload = {
        "mint_address": "ApiEvalToken11111111111111111111111111111",
        "symbol": "EVAL",
        "name": "Eval Token",
        "decimals": 9,
        "price_usd": "0.0001",
        "liquidity_usd": "100000.0",
        "volume_5m": "5000.0",
        "volume_1h": "25000.0",
        "volume_24h": "100000.0",
        "price_change_5m": "3.5",
        "price_change_1h": "10.0",
        "buy_sell_ratio_1h": "2.5",
        "risk_score": "15.0",
        "risk_band": "LOW",
        "composite_opportunity_score": "88.0",
        "lp_burned": "true",
        "mint_revoked": "true",
        "is_token_2022": "false",
        "data_freshness": "REALTIME",
        "source": "dexscreener:pair_feed"
    }
    eval_resp = client.post("/api/v1/strategies/evaluate", json=token_payload)
    assert eval_resp.status_code == 200
    signals = eval_resp.json()
    assert len(signals) >= 1
    assert signals[0]["action"] == "BUY"

    # 3. Backtest endpoint
    backtest_resp = client.post("/api/v1/strategies/backtest/momentum_breakout", json=[token_payload])
    assert backtest_resp.status_code == 200
    bt_data = backtest_resp.json()
    assert bt_data["strategy_id"] == "momentum_breakout"
    assert bt_data["observations_evaluated"] == 1
    assert bt_data["signals_generated"] == 1
