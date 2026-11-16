"""
P10.2-G Built-in Reference Strategy 1: Volume-Confirmed Momentum Breakout.
Evaluates historical snapshot lookbacks and volume trends to produce directional signals.
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


class MomentumLookbackStrategy(BaseStrategy):
    """
    Reference Momentum Strategy:
    1. Analyzes price change between latest market snapshot and earliest historical lookback.
    2. Verifies 24-hour volume and minimum pool liquidity thresholds.
    3. Produces BUY proposal if upward momentum exceeds threshold, SELL if downward momentum exceeds threshold,
       or HOLD otherwise.
    """

    STRATEGY_ID = "momentum_breakout"
    VERSION = "1.0.0"

    def get_metadata(self) -> StrategyMetadata:
        return StrategyMetadata(
            strategy_id=self.STRATEGY_ID,
            version=self.VERSION,
            name="Volume-Confirmed Momentum Breakout",
            description="Proposes directional trades on volume-confirmed momentum breakouts across supported chains",
            author="SYSTEM",
            supported_chains=[
                "solana:mainnet", "eip155:1", "eip155:8453", "eip155:56",
                "eip155:42161", "eip155:10", "eip155:137", "eip155:43114",
                "sui:mainnet", "aptos:mainnet", "ton:mainnet", "tron:mainnet"
            ],
            supported_asset_types=["MEMECOIN", "TOKEN"],
            required_market_fields=["price_usd", "liquidity_usd"],
            required_forensic_fields=[],
            config_schema={
                "type": "object",
                "properties": {
                    "min_price_change_pct": {"type": "number", "default": 5.0},
                    "min_volume_24h_usd": {"type": "number", "default": 10000.0},
                    "min_liquidity_usd": {"type": "number", "default": 15000.0},
                    "take_profit_pct": {"type": "number", "default": 20.0},
                    "stop_loss_pct": {"type": "number", "default": 8.0},
                    "suggested_order_usd": {"type": "number", "default": 500.0}
                }
            },
            lookback_window_seconds=3600,
            supported_modes=["RESEARCH", "BACKTEST", "PAPER"],
            timeout_seconds=3.0,
            lifecycle_status=StrategyLifecycle.ENABLED
        )

    def validate_config(self, config: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
        min_change = config.get("min_price_change_pct", 5.0)
        if not isinstance(min_change, (int, float)) or min_change < 0.1 or min_change > 500.0:
            return False, "min_price_change_pct must be between 0.1 and 500.0"

        min_liq = config.get("min_liquidity_usd", 15000.0)
        if not isinstance(min_liq, (int, float)) or min_liq < 0.0:
            return False, "min_liquidity_usd must be non-negative"

        tp = config.get("take_profit_pct", 20.0)
        sl = config.get("stop_loss_pct", 8.0)
        if tp <= sl:
            return False, "take_profit_pct must be greater than stop_loss_pct"

        return True, None

    def evaluate(self, context: StrategyInputContext, config: Dict[str, Any]) -> StrategySignal:
        now = datetime.now(timezone.utc)
        sig_id = f"sig_{uuid.uuid4().hex[:12]}"
        run_id = f"run_{uuid.uuid4().hex[:12]}"

        # 1. Parameter extraction
        min_change = Decimal(str(config.get("min_price_change_pct", 5.0)))
        min_vol = Decimal(str(config.get("min_volume_24h_usd", 10000.0)))
        min_liq = Decimal(str(config.get("min_liquidity_usd", 15000.0)))
        tp = float(config.get("take_profit_pct", 20.0))
        sl = float(config.get("stop_loss_pct", 8.0))
        order_usd = Decimal(str(config.get("suggested_order_usd", 500.0)))

        mkt = context.current_market_data or {}
        curr_price_usd = Decimal(str(mkt.get("price_usd", "0"))) if mkt.get("price_usd") else Decimal("0")
        curr_liq_usd = Decimal(str(mkt.get("liquidity_usd", "0"))) if mkt.get("liquidity_usd") else Decimal("0")
        curr_vol_usd = Decimal(str(mkt.get("volume_24h", "0"))) if mkt.get("volume_24h") else Decimal("0")

        # 2. Market Data Completeness
        if curr_price_usd <= Decimal("0") or curr_liq_usd <= Decimal("0"):
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
                price_source="CORE_MARKET_SNAPSHOT",
                reasons=["INSUFFICIENT_MARKET_DATA", "MISSING_PRICE_OR_LIQUIDITY"],
                human_explanation="Current market snapshot lacks valid non-zero price or pool liquidity",
                input_timestamp=context.timestamp,
                expires_at=now + timedelta(minutes=5),
                created_at=now
            )

        # 3. Minimum Liquidity Filter
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
                confidence=0.2,
                entry_reference_price_usd=curr_price_usd,
                price_source="CORE_MARKET_SNAPSHOT",
                reasons=["INSUFFICIENT_LIQUIDITY"],
                human_explanation=f"Pool liquidity (${curr_liq_usd:,.2f}) is below minimum (${min_liq:,.2f})",
                input_timestamp=context.timestamp,
                expires_at=now + timedelta(minutes=5),
                created_at=now
            )

        # 4. Lookback Momentum Calculation
        snaps = context.historical_snapshots or []
        if len(snaps) > 0 and "price_usd" in snaps[0]:
            baseline_price = Decimal(str(snaps[0]["price_usd"]))
        else:
            baseline_price = curr_price_usd

        price_diff = curr_price_usd - baseline_price
        pct_change = (price_diff / baseline_price * Decimal("100.0")) if baseline_price > 0 else Decimal("0")

        # Decimal resolution for explicit raw & human unit sizing (Fail-closed)
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
                price_source="CORE_MARKET_SNAPSHOT",
                suggested_size_raw=None,
                suggested_size_human=None,
                reasons=["UNVERIFIED_ASSET_DECIMALS"],
                human_explanation="Asset decimals missing, NULL, or unverified in desk_context; sizing rejected fail-closed",
                input_timestamp=context.timestamp,
                expires_at=now + timedelta(minutes=5),
                created_at=now
            )

        # Exact Decimal sizing preserving verified token precision without float conversion
        from decimal import ROUND_DOWN
        if curr_price_usd > Decimal("0") and order_usd > Decimal("0"):
            scale = Decimal(10) ** -token_decimals
            suggested_human = (order_usd / curr_price_usd).quantize(scale, rounding=ROUND_DOWN)
            suggested_raw = Decimal(int(suggested_human * (Decimal(10) ** token_decimals)))
        else:
            suggested_human = Decimal("0")
            suggested_raw = Decimal("0")

        # 5. Signal Decision
        if pct_change >= min_change and curr_vol_usd >= min_vol:
            action = StrategyAction.BUY
            confidence = min(0.95, float(Decimal("0.60") + (pct_change / Decimal("100.0")) * Decimal("0.35")))
            reasons = ["MOMENTUM_BREAKOUT_CONFIRMED", f"PRICE_CHANGE_{pct_change:.1f}PCT"]
            expl = f"Upward breakout of +{pct_change:.1f}% with 24h volume of ${curr_vol_usd:,.2f}"
        elif pct_change <= -min_change:
            action = StrategyAction.SELL
            confidence = min(0.90, float(Decimal("0.60") + (abs(pct_change) / Decimal("100.0")) * Decimal("0.30")))
            reasons = ["MOMENTUM_BREAKDOWN_DETECTED", f"PRICE_DROP_{pct_change:.1f}PCT"]
            expl = f"Downward breakdown of {pct_change:.1f}% detected against lookback"
        else:
            action = StrategyAction.HOLD
            confidence = 0.5
            reasons = ["WITHIN_CONSOLIDATION_RANGE"]
            expl = f"Price change of {pct_change:.1f}% within neutral threshold"

        return StrategySignal(
            signal_id=sig_id,
            run_id=run_id,
            strategy_id=self.STRATEGY_ID,
            strategy_version=self.VERSION,
            chain_id=context.chain_id,
            asset_id=context.asset_id,
            pool_id=context.pool_id,
            action=action,
            confidence=round(confidence, 3),
            entry_reference_price_native=None,
            entry_reference_price_usd=curr_price_usd,
            price_source="CORE_MARKET_SNAPSHOT",
            suggested_size_raw=suggested_raw if action == StrategyAction.BUY else None,
            suggested_size_human=suggested_human if action == StrategyAction.BUY else None,
            stop_loss_pct=sl,
            take_profit_pct=tp,
            reasons=reasons,
            human_explanation=expl,
            data_provenance={
                "lookback_snapshots_count": len(snaps),
                "baseline_price_usd": float(baseline_price),
                "current_price_usd": float(curr_price_usd),
                "volume_24h_usd": float(curr_vol_usd),
                "liquidity_usd": float(curr_liq_usd)
            },
            input_timestamp=context.timestamp,
            expires_at=now + timedelta(minutes=15),
            created_at=now
        )
