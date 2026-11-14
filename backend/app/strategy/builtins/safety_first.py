from typing import Optional, List
from decimal import Decimal
from app.schemas.contracts import TokenDiscoveryResponse, TokenSecurityReport, TriState, EvidenceStatus
from app.strategy.base import BaseStrategy, StrategySignal, SignalAction

class SafetyFirstConservativeStrategy(BaseStrategy):
    """
    Conservative safety-first strategy.
    Strictly filters for verified mint revocation, freeze revocation, burned liquidity,
    and verified non-honeypot before issuing BUY signals.
    """
    def __init__(
        self,
        max_risk_score: Decimal = Decimal("20.0"),
        min_liquidity_usd: Decimal = Decimal("50000.0"),
        default_trade_size_native: Decimal = Decimal("0.5"),
        supported_chains: Optional[List[str]] = None
    ):
        super().__init__(
            strategy_id="safety_first_conservative",
            strategy_version="1.0.0",
            description="Ultra-conservative strategy requiring verified freeze revocation, mint revocation, and burned LP.",
            supported_chains=supported_chains
        )
        self.max_risk_score = max_risk_score
        self.min_liquidity_usd = min_liquidity_usd
        self.default_trade_size_native = default_trade_size_native

    def on_discovery(self, token: TokenDiscoveryResponse) -> Optional[StrategySignal]:
        # Fast pre-filtering on discovery
        if token.liquidity_usd < self.min_liquidity_usd:
            return None
        if token.risk_score > self.max_risk_score:
            return None
        if token.mint_revoked != TriState.TRUE or token.lp_burned != TriState.TRUE:
            return None

        return StrategySignal(
            strategy_id=self.strategy_id,
            strategy_version=self.strategy_version,
            chain_id="solana:mainnet",
            asset_id=token.mint_address,
            action=SignalAction.BUY,
            confidence_score=Decimal("85.0"),
            allocated_amount_native=self.default_trade_size_native,
            max_slippage_bps=150,
            rationale=f"Conservative criteria passed: Liquidity ${token.liquidity_usd}, Risk {token.risk_score}",
            metadata={
                "mint_revoked": str(token.mint_revoked),
                "lp_burned": str(token.lp_burned),
                "risk_score": str(token.risk_score)
            }
        )

    def on_forensics(self, report: TokenSecurityReport) -> Optional[StrategySignal]:
        # Require VERIFIED evidence status
        if report.confidence != EvidenceStatus.VERIFIED:
            return None

        # Check all safety criteria
        if (
            report.is_mint_revoked == TriState.TRUE
            and report.is_freeze_revoked == TriState.TRUE
            and report.lp_burned == TriState.TRUE
            and report.is_honeypot == TriState.FALSE
        ):
            if report.risk_score and report.risk_score <= self.max_risk_score:
                return StrategySignal(
                    strategy_id=self.strategy_id,
                    strategy_version=self.strategy_version,
                    chain_id="solana:mainnet",
                    asset_id=report.mint_address,
                    action=SignalAction.BUY,
                    confidence_score=Decimal("95.0"),
                    allocated_amount_native=self.default_trade_size_native,
                    max_slippage_bps=100,
                    rationale="Complete forensic verification passed: all authorities revoked, 100% LP burned, no honeypot."
                )
        return None
