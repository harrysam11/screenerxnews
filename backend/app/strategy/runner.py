import uuid
from typing import Dict, List, Optional, Any
from decimal import Decimal
from datetime import datetime, timezone
from app.schemas.contracts import (
    TokenDiscoveryResponse, TokenSecurityReport, PaperOrderRequest, PaperOrderResponse
)
from app.strategy.base import BaseStrategy, StrategySignal, SignalAction
from app.strategy.builtins.momentum import MomentumBreakoutStrategy
from app.strategy.builtins.safety_first import SafetyFirstConservativeStrategy
from app.services.paper_simulation_engine import paper_simulation_engine

class StrategyRegistry:
    """
    Registry for loading and discovering user-owned and built-in trading strategies.
    """
    def __init__(self):
        self._strategies: Dict[str, BaseStrategy] = {}
        # Register default strategies
        self.register(MomentumBreakoutStrategy())
        self.register(SafetyFirstConservativeStrategy())

    def register(self, strategy: BaseStrategy):
        self._strategies[strategy.strategy_id] = strategy

    def unregister(self, strategy_id: str) -> bool:
        if strategy_id in self._strategies:
            del self._strategies[strategy_id]
            return True
        return False

    def get(self, strategy_id: str) -> Optional[BaseStrategy]:
        return self._strategies.get(strategy_id)

    def list_strategies(self) -> List[Dict[str, Any]]:
        return [
            {
                "strategy_id": s.strategy_id,
                "strategy_version": s.strategy_version,
                "description": s.description,
                "supported_chains": s.supported_chains
            }
            for s in self._strategies.values()
        ]

class StrategyRunner:
    """
    Evaluates incoming market events against active strategies and bridges signals
    to execution or simulation safely.
    """
    def __init__(self, registry: Optional[StrategyRegistry] = None):
        self.registry = registry or strategy_registry

    def evaluate_discovery(
        self, token: TokenDiscoveryResponse, strategy_ids: Optional[List[str]] = None
    ) -> List[StrategySignal]:
        signals: List[StrategySignal] = []
        active_ids = strategy_ids or list(self.registry._strategies.keys())

        for s_id in active_ids:
            strat = self.registry.get(s_id)
            if strat and strat.supports_chain("solana:mainnet"):
                sig = strat.on_discovery(token)
                if sig:
                    signals.append(sig)
        return signals

    def evaluate_forensics(
        self, report: TokenSecurityReport, strategy_ids: Optional[List[str]] = None
    ) -> List[StrategySignal]:
        signals: List[StrategySignal] = []
        active_ids = strategy_ids or list(self.registry._strategies.keys())

        for s_id in active_ids:
            strat = self.registry.get(s_id)
            if strat and strat.supports_chain("solana:mainnet"):
                sig = strat.on_forensics(report)
                if sig:
                    signals.append(sig)
        return signals

    def execute_signal_on_paper(
        self,
        signal: StrategySignal,
        paper_engine = None,
        pool_liquidity_usd: Decimal = Decimal("100000.0"),
        current_market_price_native: Decimal = Decimal("0.0001")
    ) -> PaperOrderResponse:
        """
        Executes an emitted strategy signal on the isolated paper trading desk.
        """
        engine = paper_engine or paper_simulation_engine
        account = engine.get_account(signal.chain_id)

        input_mint = "SOL" if signal.action == SignalAction.BUY else signal.asset_id
        output_mint = signal.asset_id if signal.action == SignalAction.BUY else "SOL"

        order_req = PaperOrderRequest(
            account_id=account.account_id,
            client_order_id=f"strat-{signal.strategy_id}-{uuid.uuid4().hex[:12]}",
            asset_id=signal.asset_id,
            pool_id=f"pool-{signal.asset_id[:8]}",
            side=signal.action.value,
            input_mint=input_mint,
            output_mint=output_mint,
            input_amount_raw=signal.allocated_amount_native,
            max_slippage_bps=signal.max_slippage_bps,
            chain_id=signal.chain_id
        )

        return engine.execute_order(
            request=order_req,
            current_market_price_native=current_market_price_native,
            pool_liquidity_usd=pool_liquidity_usd
        )

    def backtest(
        self,
        observations: List[TokenDiscoveryResponse],
        strategy_id: str
    ) -> Dict[str, Any]:
        """
        Backtests a registered strategy against historical discovery observations.
        """
        strat = self.registry.get(strategy_id)
        if not strat:
            raise ValueError(f"Strategy {strategy_id} not found in registry")

        signals_emitted = []
        for obs in observations:
            sig = strat.on_discovery(obs)
            if sig:
                signals_emitted.append(sig)

        buy_count = sum(1 for s in signals_emitted if s.action == SignalAction.BUY)
        sell_count = sum(1 for s in signals_emitted if s.action == SignalAction.SELL)
        avg_confidence = (
            sum(s.confidence_score for s in signals_emitted) / len(signals_emitted)
            if signals_emitted else Decimal("0.0")
        )

        return {
            "strategy_id": strategy_id,
            "observations_evaluated": len(observations),
            "signals_generated": len(signals_emitted),
            "buy_signals": buy_count,
            "sell_signals": sell_count,
            "avg_confidence": round(avg_confidence, 2),
            "signals": [s.model_dump() for s in signals_emitted]
        }

strategy_registry = StrategyRegistry()
strategy_runner = StrategyRunner(strategy_registry)
