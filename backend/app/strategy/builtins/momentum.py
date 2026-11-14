from typing import Optional, List
from decimal import Decimal
from app.schemas.contracts import TokenDiscoveryResponse, TokenSecurityReport, TriState
from app.strategy.base import BaseStrategy, StrategySignal, SignalAction

class MomentumBreakoutStrategy(BaseStrategy):
    """
    Momentum breakout strategy.
    Triggers BUY signals on tokens displaying strong volume momentum,
    favorable buy/sell ratio, and verified minimal safety checks.
    """
    def __init__(
        self,
        min_liquidity_usd: Decimal = Decimal("25000.0"),
        min_volume_1h_usd: Decimal = Decimal("5000.0"),
        min_buy_sell_ratio: Decimal = Decimal("1.25"),
        max_risk_score: Decimal = Decimal("30.0"),
        default_trade_size_native: Decimal = Decimal("1.0"),
        supported_chains: Optional[List[str]] = None
    ):
        super().__init__(
            strategy_id="momentum_breakout",
            strategy_version="1.0.0",
            description="Trades strong 1h volume breakout with buy/sell pressure and verified authorities.",
            supported_chains=supported_chains
        )
        self.min_liquidity_usd = min_liquidity_usd
        self.min_volume_1h_usd = min_volume_1h_usd
        self.min_buy_sell_ratio = min_buy_sell_ratio
        self.max_risk_score = max_risk_score
        self.default_trade_size_native = default_trade_size_native

    def on_discovery(self, token: TokenDiscoveryResponse) -> Optional[StrategySignal]:
        # Filter liquidity and volume
        if token.liquidity_usd < self.min_liquidity_usd:
            return None
        if token.volume_1h < self.min_volume_1h_usd:
            return None
        if token.buy_sell_ratio_1h < self.min_buy_sell_ratio:
            return None
        if token.risk_score > self.max_risk_score:
            return None

        # Require verified non-honeypot / safety fundamentals
        if token.mint_revoked == TriState.FALSE or token.lp_burned == TriState.FALSE:
            return None

        confidence = min(Decimal("95.0"), Decimal("50.0") + (token.buy_sell_ratio_1h * Decimal("15.0")))

        return StrategySignal(
            strategy_id=self.strategy_id,
            strategy_version=self.strategy_version,
            chain_id="solana:mainnet",
            asset_id=token.mint_address,
            action=SignalAction.BUY,
            confidence_score=confidence,
            allocated_amount_native=self.default_trade_size_native,
            max_slippage_bps=200,
            rationale=f"Breakout criteria met: 1h Vol ${token.volume_1h}, Ratio {token.buy_sell_ratio_1h}, Risk {token.risk_score}",
            metadata={
                "volume_1h": str(token.volume_1h),
                "liquidity_usd": str(token.liquidity_usd),
                "buy_sell_ratio_1h": str(token.buy_sell_ratio_1h),
                "risk_score": str(token.risk_score)
            }
        )

    def on_forensics(self, report: TokenSecurityReport) -> Optional[StrategySignal]:
        if report.is_honeypot == TriState.TRUE:
            return StrategySignal(
                strategy_id=self.strategy_id,
                strategy_version=self.strategy_version,
                chain_id="solana:mainnet",
                asset_id=report.mint_address,
                action=SignalAction.SELL,
                confidence_score=Decimal("100.0"),
                allocated_amount_native=Decimal("1.0"),
                max_slippage_bps=500,
                rationale="Honeypot confirmed in forensic scan: emergency exit"
            )
        return None
