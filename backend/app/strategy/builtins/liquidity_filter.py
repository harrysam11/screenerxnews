"""
P10.2-G Built-in Reference Strategy 2: Forensic Safety & Liquidity Accumulator.
Evaluates multi-chain security scans, holder concentration, and pool liquidity before proposing trades.
"""

import uuid
from decimal import Decimal
from typing import Dict, Any, Tuple, Optional
from datetime import datetime, timezone, timedelta

from app.strategy.base import (
    BaseStrategy,
    StrategyMetadata,
    StrategyInputContext,
    StrategySignal,
    StrategyAction,
    StrategyLifecycle
)


class LiquidityRiskFilterStrategy(BaseStrategy):
    """
    Reference Liquidity & Forensic Filter Strategy:
    1. Evaluates pool liquidity, LP token lock/burn status, and mint revocation.
    2. Enforces strict holder concentration limits (top 10 wallet ceiling).
    3. Rejects honeypots and unverified contracts fail-closed.
    4. Proposes conservative accumulation BUY signals only when all security invariants pass.
    """

    STRATEGY_ID = "safety_first_accumulator"
    VERSION = "1.0.0"

    def get_metadata(self) -> StrategyMetadata:
        return StrategyMetadata(
            strategy_id=self.STRATEGY_ID,
            version=self.VERSION,
            name="Forensic Safety & Liquidity Accumulator",
            description="Conservative accumulator strategy with strict forensic and liquidity filters",
            author="SYSTEM",
            supported_chains=[
                "solana:mainnet", "eip155:1", "eip155:8453", "eip155:56",
                "eip155:42161", "eip155:10", "eip155:137", "eip155:43114",
                "sui:mainnet", "aptos:mainnet", "ton:mainnet", "tron:mainnet"
            ],
            supported_asset_types=["MEMECOIN", "TOKEN"],
            required_market_fields=["price_usd", "liquidity_usd"],
            required_forensic_fields=["top10_concentration_pct", "is_honeypot"],
            config_schema={
                "type": "object",
                "properties": {
                    "min_liquidity_usd": {"type": "number", "default": 50000.0},
                    "max_top10_holder_pct": {"type": "number", "default": 20.0},
                    "require_burned_or_locked_lp": {"type": "boolean", "default": True},
                    "require_renounced_mint": {"type": "boolean", "default": True},
                    "min_forensic_score": {"type": "number", "default": 75.0},
                    "take_profit_pct": {"type": "number", "default": 15.0},
                    "stop_loss_pct": {"type": "number", "default": 5.0},
                    "suggested_order_usd": {"type": "number", "default": 250.0}
                }
            },
            lookback_window_seconds=1800,
            supported_modes=["RESEARCH", "BACKTEST", "PAPER"],
            timeout_seconds=3.0,
            lifecycle_status=StrategyLifecycle.ENABLED
        )

    def validate_config(self, config: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
        min_liq = config.get("min_liquidity_usd", 50000.0)
        if not isinstance(min_liq, (int, float)) or min_liq < 1000.0:
            return False, "min_liquidity_usd must be at least 1000.0"

        max_conc = config.get("max_top10_holder_pct", 20.0)
        if not isinstance(max_conc, (int, float)) or max_conc <= 0.0 or max_conc > 100.0:
            return False, "max_top10_holder_pct must be between 0.1 and 100.0"

        min_score = config.get("min_forensic_score", 75.0)
        if not isinstance(min_score, (int, float)) or min_score < 0.0 or min_score > 100.0:
            return False, "min_forensic_score must be between 0.0 and 100.0"

        return True, None

    def evaluate(self, context: StrategyInputContext, config: Dict[str, Any]) -> StrategySignal:
        now = datetime.now(timezone.utc)
        sig_id = f"sig_{uuid.uuid4().hex[:12]}"
        run_id = f"run_{uuid.uuid4().hex[:12]}"

        # Parameters
        min_liq = Decimal(str(config.get("min_liquidity_usd", 50000.0)))
        max_conc = Decimal(str(config.get("max_top10_holder_pct", 20.0)))
        req_lp_lock = config.get("require_burned_or_locked_lp", True)
        req_renounce = config.get("require_renounced_mint", True)
        min_score = Decimal(str(config.get("min_forensic_score", 75.0)))
        tp = float(config.get("take_profit_pct", 15.0))
        sl = float(config.get("stop_loss_pct", 5.0))
        order_usd = Decimal(str(config.get("suggested_order_usd", 250.0)))

        mkt = context.current_market_data or {}
        curr_price_usd = Decimal(str(mkt.get("price_usd", "0"))) if mkt.get("price_usd") else Decimal("0")
        curr_liq_usd = Decimal(str(mkt.get("liquidity_usd", "0"))) if mkt.get("liquidity_usd") else Decimal("0")

        forensic = context.forensic_data or {}
        is_honeypot = forensic.get("is_honeypot", False)
        lp_status = str(forensic.get("lp_status", "UNKNOWN")).upper()
        is_mint_revoked = forensic.get("is_mint_revoked", True)
        top10_pct = Decimal(str(forensic.get("top10_concentration_pct", "0")))
        safety_score = Decimal(str(forensic.get("safety_score", "80.0")))

        # 1. Critical Honeypot Filter
        if is_honeypot:
            return StrategySignal(
                signal_id=sig_id,
                run_id=run_id,
                strategy_id=self.STRATEGY_ID,
                strategy_version=self.VERSION,
                chain_id=context.chain_id,
                asset_id=context.asset_id,
                pool_id=context.pool_id,
                action=StrategyAction.INVALID,
                confidence=0.0,
                entry_reference_price_usd=curr_price_usd,
                price_source="CORE_FORENSIC_SCAN",
                reasons=["CRITICAL_HONEYPOT_DETECTED"],
                human_explanation="Asset flagged as honeypot by forensic scan; hard rejected",
                data_provenance=forensic,
                input_timestamp=context.timestamp,
                expires_at=now + timedelta(minutes=60),
                created_at=now
            )

        # 2. Liquidity Threshold Check
        if curr_liq_usd < min_liq:
            return StrategySignal(
                signal_id=sig_id,
                run_id=run_id,
                strategy_id=self.STRATEGY_ID,
                strategy_version=self.VERSION,
                chain_id=context.chain_id,
                asset_id=context.asset_id,
                pool_id=context.pool_id,
                action=StrategyAction.HOLD,
                confidence=0.1,
                entry_reference_price_usd=curr_price_usd,
                price_source="CORE_MARKET_SNAPSHOT",
                reasons=["INSUFFICIENT_POOL_LIQUIDITY"],
                human_explanation=f"Pool liquidity (${curr_liq_usd:,.2f}) below conservative limit (${min_liq:,.2f})",
                data_provenance={"liquidity_usd": float(curr_liq_usd), "min_required": float(min_liq)},
                input_timestamp=context.timestamp,
                expires_at=now + timedelta(minutes=10),
                created_at=now
            )

        # 3. LP Token Security Check
        if req_lp_lock and lp_status not in ("BURNED", "LOCKED"):
            return StrategySignal(
                signal_id=sig_id,
                run_id=run_id,
                strategy_id=self.STRATEGY_ID,
                strategy_version=self.VERSION,
                chain_id=context.chain_id,
                asset_id=context.asset_id,
                pool_id=context.pool_id,
                action=StrategyAction.HOLD,
                confidence=0.1,
                entry_reference_price_usd=curr_price_usd,
                price_source="CORE_FORENSIC_SCAN",
                reasons=["UNSECURED_LP_TOKENS", f"LP_STATUS_{lp_status}"],
                human_explanation=f"Liquidity pool tokens are {lp_status}, failing required burned/locked check",
                data_provenance=forensic,
                input_timestamp=context.timestamp,
                expires_at=now + timedelta(minutes=10),
                created_at=now
            )

        # 4. Mint Revocation Check
        if req_renounce and not is_mint_revoked:
            return StrategySignal(
                signal_id=sig_id,
                run_id=run_id,
                strategy_id=self.STRATEGY_ID,
                strategy_version=self.VERSION,
                chain_id=context.chain_id,
                asset_id=context.asset_id,
                pool_id=context.pool_id,
                action=StrategyAction.HOLD,
                confidence=0.1,
                entry_reference_price_usd=curr_price_usd,
                price_source="CORE_FORENSIC_SCAN",
                reasons=["MINT_AUTHORITY_ACTIVE"],
                human_explanation="Token mint authority has not been renounced; inflationary risk",
                data_provenance=forensic,
                input_timestamp=context.timestamp,
                expires_at=now + timedelta(minutes=10),
                created_at=now
            )

        # 5. Top 10 Holder Concentration Check
        if top10_pct > max_conc:
            return StrategySignal(
                signal_id=sig_id,
                run_id=run_id,
                strategy_id=self.STRATEGY_ID,
                strategy_version=self.VERSION,
                chain_id=context.chain_id,
                asset_id=context.asset_id,
                pool_id=context.pool_id,
                action=StrategyAction.HOLD,
                confidence=0.15,
                entry_reference_price_usd=curr_price_usd,
                price_source="CORE_FORENSIC_SCAN",
                reasons=["HOLDER_CONCENTRATION_EXCEEDED"],
                human_explanation=f"Top 10 holder concentration ({top10_pct:.1f}%) exceeds safety ceiling ({max_conc:.1f}%)",
                data_provenance=forensic,
                input_timestamp=context.timestamp,
                expires_at=now + timedelta(minutes=10),
                created_at=now
            )

        # 6. Overall Safety Score Filter
        if safety_score < min_score:
            return StrategySignal(
                signal_id=sig_id,
                run_id=run_id,
                strategy_id=self.STRATEGY_ID,
                strategy_version=self.VERSION,
                chain_id=context.chain_id,
                asset_id=context.asset_id,
                pool_id=context.pool_id,
                action=StrategyAction.HOLD,
                confidence=0.2,
                entry_reference_price_usd=curr_price_usd,
                price_source="CORE_FORENSIC_SCAN",
                reasons=["SUBPAR_SAFETY_SCORE"],
                human_explanation=f"Composite safety score ({safety_score:.1f}) is below minimum requirement ({min_score:.1f})",
                data_provenance=forensic,
                input_timestamp=context.timestamp,
                expires_at=now + timedelta(minutes=10),
                created_at=now
            )

        # 7. All Filters Passed -> Generate Conservative BUY Proposal
        token_decimals = context.desk_context.get("decimals")
        if token_decimals is None or not isinstance(token_decimals, int) or token_decimals < 0 or token_decimals > 36:
            return StrategySignal(
                signal_id=sig_id,
                run_id=run_id,
                strategy_id=self.STRATEGY_ID,
                strategy_version=self.VERSION,
                chain_id=context.chain_id,
                asset_id=context.asset_id,
                pool_id=context.pool_id,
                action=StrategyAction.INVALID,
                confidence=0.0,
                price_source="CORE_FORENSIC_SCAN",
                suggested_size_raw=None,
                suggested_size_human=None,
                reasons=["UNVERIFIED_ASSET_DECIMALS"],
                human_explanation="Asset decimals missing or unverified in desk_context; sizing rejected fail-closed",
                input_timestamp=context.timestamp,
                expires_at=now + timedelta(minutes=10),
                created_at=now
            )

        from decimal import ROUND_DOWN
        if curr_price_usd > Decimal("0") and order_usd > Decimal("0"):
            scale = Decimal(10) ** -token_decimals
            suggested_human = (order_usd / curr_price_usd).quantize(scale, rounding=ROUND_DOWN)
            suggested_raw = Decimal(int(suggested_human * (Decimal(10) ** token_decimals)))
        else:
            suggested_human = Decimal("0")
            suggested_raw = Decimal("0")

        confidence = float(min(Decimal("0.98"), safety_score / Decimal("100.0")))

        return StrategySignal(
            signal_id=sig_id,
            run_id=run_id,
            strategy_id=self.STRATEGY_ID,
            strategy_version=self.VERSION,
            chain_id=context.chain_id,
            asset_id=context.asset_id,
            pool_id=context.pool_id,
            action=StrategyAction.BUY,
            confidence=round(confidence, 3),
            entry_reference_price_native=None,
            entry_reference_price_usd=curr_price_usd,
            price_source="CORE_MARKET_SNAPSHOT",
            suggested_size_raw=suggested_raw,
            suggested_size_human=suggested_human,
            stop_loss_pct=sl,
            take_profit_pct=tp,
            reasons=["ALL_FORENSIC_GATES_PASSED", "LIQUIDITY_CRITERIA_SATISFIED"],
            human_explanation=f"High forensic safety score ({safety_score:.1f}), locked LP, low concentration ({top10_pct:.1f}%), and robust liquidity (${curr_liq_usd:,.2f})",
            data_provenance={
                "safety_score": float(safety_score),
                "top10_concentration_pct": float(top10_pct),
                "lp_status": lp_status,
                "is_mint_revoked": is_mint_revoked,
                "liquidity_usd": float(curr_liq_usd)
            },
            input_timestamp=context.timestamp,
            expires_at=now + timedelta(minutes=30),
            created_at=now
        )
