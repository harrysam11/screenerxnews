"""
P1.5-B Market Structure Service.
Implements deterministic, strategy-agnostic market structure features:
- Swing Highs and Swing Lows (with strictly enforced confirmation timing).
- Basic Liquidity Levels (Buy-side and Sell-side) derived from confirmed swings.
- Fair Value Gaps (FVG) 3-candle discrete price imbalance detection.
- Generic Order Blocks (OB) based on pre-displacement opposite-color candles.

All definitions are generic, reusable, and mathematically explicit.
Zero lookahead bias is guaranteed by recording both originating and confirmation timestamps.
"""

from typing import List, Optional, Tuple, Dict, Any
from decimal import Decimal, getcontext, ROUND_HALF_UP

from app.schemas.ohlcv import OHLCVCandleDTO, CandleBarStatus, Timeframe
from app.schemas.indicators import (
    MARKET_STRUCTURE_VERSION,
    MarketStructureType,
    LiquidityLevelType,
    LiquidityLevelStatus,
    FVGType,
    FVGStatus,
    OrderBlockType,
    OrderBlockStatus,
    SwingPointDTO,
    LiquidityLevelDTO,
    FairValueGapDTO,
    OrderBlockDTO,
    MarketStructureResponseDTO
)
from app.services.indicator_engine import indicator_engine

getcontext().prec = 28

DECIMAL_ZERO = Decimal("0")
DEFAULT_DECIMAL_PLACES = Decimal("0.00000001")


def quantize_financial(val: Optional[Decimal], places: Decimal = DEFAULT_DECIMAL_PLACES) -> Optional[Decimal]:
    """Helper to cleanly quantize a Decimal without float imprecision."""
    if val is None:
        return None
    return val.quantize(places, rounding=ROUND_HALF_UP)


class MarketStructureService:
    """
    Independent engine for detecting structural market milestones.
    Purely descriptive features; zero buy/sell signals.
    """

    @staticmethod
    def _filter_candles(
        candles: List[OHLCVCandleDTO],
        closed_only: bool = True
    ) -> List[OHLCVCandleDTO]:
        """Filters input candles, dropping incomplete bars if closed_only is True."""
        if not candles:
            return []
        if closed_only:
            return [c for c in candles if c.bar_status == CandleBarStatus.CLOSED]
        return candles

    @classmethod
    def detect_swings(
        cls,
        candles: List[OHLCVCandleDTO],
        lookback: int = 2,
        lookforward: int = 2,
        closed_only: bool = True
    ) -> List[SwingPointDTO]:
        """
        Detects swing high and swing low pivot points.
        A swing at index i requires:
          Swing High: High_i > High_{i-k} for k in 1..lookback AND High_i > High_{i+m} for m in 1..lookforward
          Swing Low:  Low_i < Low_{i-k} for k in 1..lookback AND Low_i < Low_{i+m} for m in 1..lookforward

        CONFIRMATION RULE (NO LOOKAHEAD):
          The swing occurred at candle index i.
          It CANNOT be confirmed until candle index i + lookforward has CLOSED.
          Confirmation timestamp = candle[i + lookforward].timestamp_utc.
          Unconfirmed swings (where i + lookforward >= len(candles)) are strictly excluded.
        """
        if lookback <= 0 or lookforward <= 0:
            raise ValueError(f"Lookback and lookforward must be positive, got lookback={lookback}, lookforward={lookforward}")

        eval_candles = cls._filter_candles(candles, closed_only=closed_only)
        n = len(eval_candles)
        swings: List[SwingPointDTO] = []

        if n < lookback + lookforward + 1:
            return []

        # Candle index i can only be confirmed if i + lookforward < n
        for i in range(lookback, n - lookforward):
            curr_bar = eval_candles[i]
            confirm_bar = eval_candles[i + lookforward]

            # Check Swing High
            is_swing_high = True
            for left_k in range(1, lookback + 1):
                if eval_candles[i - left_k].high_price >= curr_bar.high_price:
                    is_swing_high = False
                    break
            if is_swing_high:
                for right_m in range(1, lookforward + 1):
                    if eval_candles[i + right_m].high_price >= curr_bar.high_price:
                        is_swing_high = False
                        break

            if is_swing_high:
                swings.append(SwingPointDTO(
                    swing_id=f"sh_{curr_bar.timestamp_unix}_{i}",
                    swing_type=MarketStructureType.SWING_HIGH,
                    swing_price=quantize_financial(curr_bar.high_price),
                    swing_candle_index=i,
                    swing_timestamp_utc=curr_bar.timestamp_utc,
                    swing_timestamp_unix=curr_bar.timestamp_unix,
                    confirmation_candle_index=i + lookforward,
                    confirmation_timestamp_utc=confirm_bar.timestamp_utc,
                    confirmation_timestamp_unix=confirm_bar.timestamp_unix,
                    lookback_bars=lookback,
                    lookforward_bars=lookforward
                ))

            # Check Swing Low
            is_swing_low = True
            for left_k in range(1, lookback + 1):
                if eval_candles[i - left_k].low_price <= curr_bar.low_price:
                    is_swing_low = False
                    break
            if is_swing_low:
                for right_m in range(1, lookforward + 1):
                    if eval_candles[i + right_m].low_price <= curr_bar.low_price:
                        is_swing_low = False
                        break

            if is_swing_low:
                swings.append(SwingPointDTO(
                    swing_id=f"sl_{curr_bar.timestamp_unix}_{i}",
                    swing_type=MarketStructureType.SWING_LOW,
                    swing_price=quantize_financial(curr_bar.low_price),
                    swing_candle_index=i,
                    swing_timestamp_utc=curr_bar.timestamp_utc,
                    swing_timestamp_unix=curr_bar.timestamp_unix,
                    confirmation_candle_index=i + lookforward,
                    confirmation_timestamp_utc=confirm_bar.timestamp_utc,
                    confirmation_timestamp_unix=confirm_bar.timestamp_unix,
                    lookback_bars=lookback,
                    lookforward_bars=lookforward
                ))

        return swings

    @classmethod
    def derive_liquidity_levels(
        cls,
        candles: List[OHLCVCandleDTO],
        swings: List[SwingPointDTO],
        closed_only: bool = True
    ) -> List[LiquidityLevelDTO]:
        """
        Derives Buy-Side (BSL) and Sell-Side (SSL) liquidity levels from confirmed swing points.
        Tracks life-cycle status chronologically starting after confirmation:
        - ACTIVE: Untested resting liquidity.
        - SWEPT: Wick pierced the level, but candle closed back inside.
        - VIOLATED: Candle body closed beyond the level.
        """
        eval_candles = cls._filter_candles(candles, closed_only=closed_only)
        n = len(eval_candles)
        levels: List[LiquidityLevelDTO] = []

        for sw in swings:
            level_type = (
                LiquidityLevelType.BUY_SIDE_LIQUIDITY
                if sw.swing_type == MarketStructureType.SWING_HIGH
                else LiquidityLevelType.SELL_SIDE_LIQUIDITY
            )
            price = sw.swing_price
            status = LiquidityLevelStatus.ACTIVE
            tested_count = 0
            swept_ts: Optional[str] = None
            swept_idx: Optional[int] = None
            violated_ts: Optional[str] = None
            violated_idx: Optional[int] = None

            # Evaluate chronological progression strictly after confirmation
            start_eval_idx = sw.confirmation_candle_index + 1
            for j in range(start_eval_idx, n):
                bar = eval_candles[j]
                if level_type == LiquidityLevelType.BUY_SIDE_LIQUIDITY:
                    if bar.close_price > price:
                        status = LiquidityLevelStatus.VIOLATED
                        violated_ts = bar.timestamp_utc
                        violated_idx = j
                        tested_count += 1
                        break
                    elif bar.high_price >= price:
                        status = LiquidityLevelStatus.SWEPT
                        if swept_ts is None:
                            swept_ts = bar.timestamp_utc
                            swept_idx = j
                        tested_count += 1
                else:  # SELL_SIDE_LIQUIDITY
                    if bar.close_price < price:
                        status = LiquidityLevelStatus.VIOLATED
                        violated_ts = bar.timestamp_utc
                        violated_idx = j
                        tested_count += 1
                        break
                    elif bar.low_price <= price:
                        status = LiquidityLevelStatus.SWEPT
                        if swept_ts is None:
                            swept_ts = bar.timestamp_utc
                            swept_idx = j
                        tested_count += 1

            levels.append(LiquidityLevelDTO(
                level_id=f"liq_{sw.swing_id}",
                level_type=level_type,
                price_level=price,
                originating_swing_id=sw.swing_id,
                originating_timestamp_utc=sw.swing_timestamp_utc,
                confirmed_timestamp_utc=sw.confirmation_timestamp_utc,
                status=status,
                tested_count=tested_count,
                swept_timestamp_utc=swept_ts,
                swept_candle_index=swept_idx,
                violated_timestamp_utc=violated_ts,
                violated_candle_index=violated_idx
            ))

        return levels

    @classmethod
    def detect_fair_value_gaps(
        cls,
        candles: List[OHLCVCandleDTO],
        closed_only: bool = True
    ) -> List[FairValueGapDTO]:
        """
        Detects generic 3-candle Fair Value Gaps (discrete imbalances):
        - Bullish FVG: Candle 0 High < Candle 2 Low. Gap zone = [Candle 0 High, Candle 2 Low].
        - Bearish FVG: Candle 0 Low > Candle 2 High. Gap zone = [Candle 2 High, Candle 0 Low].
        Confirmed strictly upon close of Candle 2.
        Tracks subsequent fill status: UNFILLED, PARTIALLY_FILLED, FULLY_FILLED.
        """
        eval_candles = cls._filter_candles(candles, closed_only=closed_only)
        n = len(eval_candles)
        gaps: List[FairValueGapDTO] = []

        if n < 3:
            return []

        for i in range(2, n):
            c0 = eval_candles[i - 2]
            c1 = eval_candles[i - 1]
            c2 = eval_candles[i]

            # Bullish FVG check
            if c0.high_price < c2.low_price:
                bottom = c0.high_price
                top = c2.low_price
                gap_size = top - bottom
                gap_id = f"fvg_bull_{c1.timestamp_unix}_{i-1}"

                # Evaluate subsequent fills
                status = FVGStatus.UNFILLED
                mitigated_ts: Optional[str] = None
                mitigated_idx: Optional[int] = None

                for j in range(i + 1, n):
                    sub = eval_candles[j]
                    if sub.low_price <= bottom:
                        status = FVGStatus.FULLY_FILLED
                        mitigated_ts = sub.timestamp_utc
                        mitigated_idx = j
                        break
                    elif sub.low_price <= top:
                        status = FVGStatus.PARTIALLY_FILLED
                        if mitigated_ts is None:
                            mitigated_ts = sub.timestamp_utc
                            mitigated_idx = j

                gaps.append(FairValueGapDTO(
                    gap_id=gap_id,
                    fvg_type=FVGType.BULLISH_FVG,
                    candle_0_index=i - 2,
                    candle_1_index=i - 1,
                    candle_2_index=i,
                    originating_timestamp_utc=c1.timestamp_utc,
                    confirmed_timestamp_utc=c2.timestamp_utc,
                    top_price=quantize_financial(top),
                    bottom_price=quantize_financial(bottom),
                    gap_size=quantize_financial(gap_size),
                    status=status,
                    mitigated_timestamp_utc=mitigated_ts,
                    mitigated_candle_index=mitigated_idx
                ))

            # Bearish FVG check
            elif c0.low_price > c2.high_price:
                bottom = c2.high_price
                top = c0.low_price
                gap_size = top - bottom
                gap_id = f"fvg_bear_{c1.timestamp_unix}_{i-1}"

                # Evaluate subsequent fills
                status = FVGStatus.UNFILLED
                mitigated_ts: Optional[str] = None
                mitigated_idx: Optional[int] = None

                for j in range(i + 1, n):
                    sub = eval_candles[j]
                    if sub.high_price >= top:
                        status = FVGStatus.FULLY_FILLED
                        mitigated_ts = sub.timestamp_utc
                        mitigated_idx = j
                        break
                    elif sub.high_price >= bottom:
                        status = FVGStatus.PARTIALLY_FILLED
                        if mitigated_ts is None:
                            mitigated_ts = sub.timestamp_utc
                            mitigated_idx = j

                gaps.append(FairValueGapDTO(
                    gap_id=gap_id,
                    fvg_type=FVGType.BEARISH_FVG,
                    candle_0_index=i - 2,
                    candle_1_index=i - 1,
                    candle_2_index=i,
                    originating_timestamp_utc=c1.timestamp_utc,
                    confirmed_timestamp_utc=c2.timestamp_utc,
                    top_price=quantize_financial(top),
                    bottom_price=quantize_financial(bottom),
                    gap_size=quantize_financial(gap_size),
                    status=status,
                    mitigated_timestamp_utc=mitigated_ts,
                    mitigated_candle_index=mitigated_idx
                ))

        return gaps

    @classmethod
    def detect_order_blocks(
        cls,
        candles: List[OHLCVCandleDTO],
        displacement_atr_multiple: Decimal = Decimal("1.5"),
        atr_period: int = 14,
        closed_only: bool = True
    ) -> List[OrderBlockDTO]:
        """
        Generic Order Block detection:
        - Bullish OB: The last down candle (Close < Open) immediately preceding a decisive upward displacement
          (displacement candle body >= displacement_atr_multiple * ATR).
        - Bearish OB: The last up candle (Close > Open) immediately preceding a decisive downward displacement
          (displacement candle body >= displacement_atr_multiple * ATR).

        Confirmation rule: Confirmed upon close of the displacement candle.
        Zone: [Low, High] range of the originating opposite candle.
        Status tracking:
          - ACTIVE: Unmitigated.
          - MITIGATED: Price retraced into zone.
          - VIOLATED: Price closed through opposite extreme of the zone.
        """
        eval_candles = cls._filter_candles(candles, closed_only=closed_only)
        n = len(eval_candles)
        if n < 2:
            return []

        # Precompute ATR
        atr_series = indicator_engine.calculate_atr(eval_candles, period=atr_period, closed_only=False).points
        blocks: List[OrderBlockDTO] = []

        for i in range(1, n):
            c_disp = eval_candles[i]
            c_orig = eval_candles[i - 1]
            atr_pt = atr_series[i]

            if atr_pt.value is None or atr_pt.value == DECIMAL_ZERO:
                continue

            threshold = displacement_atr_multiple * atr_pt.value
            body = abs(c_disp.close_price - c_disp.open_price)

            if body < threshold:
                continue

            # Bullish displacement: Candle i is green, Candle i-1 was red
            if c_disp.close_price > c_disp.open_price and c_orig.close_price < c_orig.open_price:
                top = c_orig.high_price
                bottom = c_orig.low_price
                block_id = f"ob_bull_{c_orig.timestamp_unix}_{i-1}"

                status = OrderBlockStatus.ACTIVE
                mitigated_ts: Optional[str] = None
                mitigated_idx: Optional[int] = None

                for j in range(i + 1, n):
                    sub = eval_candles[j]
                    if sub.close_price < bottom:
                        status = OrderBlockStatus.VIOLATED
                        mitigated_ts = sub.timestamp_utc
                        mitigated_idx = j
                        break
                    elif sub.low_price <= top:
                        status = OrderBlockStatus.MITIGATED
                        if mitigated_ts is None:
                            mitigated_ts = sub.timestamp_utc
                            mitigated_idx = j

                blocks.append(OrderBlockDTO(
                    block_id=block_id,
                    block_type=OrderBlockType.BULLISH_ORDER_BLOCK,
                    originating_candle_index=i - 1,
                    originating_timestamp_utc=c_orig.timestamp_utc,
                    confirmation_candle_index=i,
                    confirmation_timestamp_utc=c_disp.timestamp_utc,
                    open_price=quantize_financial(c_orig.open_price),
                    high_price=quantize_financial(c_orig.high_price),
                    low_price=quantize_financial(c_orig.low_price),
                    close_price=quantize_financial(c_orig.close_price),
                    top_price=quantize_financial(top),
                    bottom_price=quantize_financial(bottom),
                    displacement_atr_multiple=displacement_atr_multiple,
                    status=status,
                    mitigated_timestamp_utc=mitigated_ts,
                    mitigated_candle_index=mitigated_idx
                ))

            # Bearish displacement: Candle i is red, Candle i-1 was green
            elif c_disp.close_price < c_disp.open_price and c_orig.close_price > c_orig.open_price:
                top = c_orig.high_price
                bottom = c_orig.low_price
                block_id = f"ob_bear_{c_orig.timestamp_unix}_{i-1}"

                status = OrderBlockStatus.ACTIVE
                mitigated_ts: Optional[str] = None
                mitigated_idx: Optional[int] = None

                for j in range(i + 1, n):
                    sub = eval_candles[j]
                    if sub.close_price > top:
                        status = OrderBlockStatus.VIOLATED
                        mitigated_ts = sub.timestamp_utc
                        mitigated_idx = j
                        break
                    elif sub.high_price >= bottom:
                        status = OrderBlockStatus.MITIGATED
                        if mitigated_ts is None:
                            mitigated_ts = sub.timestamp_utc
                            mitigated_idx = j

                blocks.append(OrderBlockDTO(
                    block_id=block_id,
                    block_type=OrderBlockType.BEARISH_ORDER_BLOCK,
                    originating_candle_index=i - 1,
                    originating_timestamp_utc=c_orig.timestamp_utc,
                    confirmation_candle_index=i,
                    confirmation_timestamp_utc=c_disp.timestamp_utc,
                    open_price=quantize_financial(c_orig.open_price),
                    high_price=quantize_financial(c_orig.high_price),
                    low_price=quantize_financial(c_orig.low_price),
                    close_price=quantize_financial(c_orig.close_price),
                    top_price=quantize_financial(top),
                    bottom_price=quantize_financial(bottom),
                    displacement_atr_multiple=displacement_atr_multiple,
                    status=status,
                    mitigated_timestamp_utc=mitigated_ts,
                    mitigated_candle_index=mitigated_idx
                ))

        return blocks

    @classmethod
    def analyze_market_structure(
        cls,
        chain_id: str,
        pool_address: str,
        timeframe: Timeframe,
        candles: List[OHLCVCandleDTO],
        swing_lookback: int = 2,
        swing_lookforward: int = 2,
        displacement_atr_multiple: Decimal = Decimal("1.5"),
        closed_only: bool = True
    ) -> MarketStructureResponseDTO:
        """
        Consolidated market structure analysis.
        Runs swing detection, liquidity level derivation, FVG identification, and generic OB detection.
        """
        eval_candles = cls._filter_candles(candles, closed_only=closed_only)
        swings = cls.detect_swings(eval_candles, lookback=swing_lookback, lookforward=swing_lookforward, closed_only=False)
        liquidity = cls.derive_liquidity_levels(eval_candles, swings, closed_only=False)
        fvgs = cls.detect_fair_value_gaps(eval_candles, closed_only=False)
        obs = cls.detect_order_blocks(eval_candles, displacement_atr_multiple=displacement_atr_multiple, closed_only=False)

        return MarketStructureResponseDTO(
            chain_id=chain_id,
            pool_address=pool_address,
            timeframe=timeframe,
            total_candles_evaluated=len(eval_candles),
            calculation_version=MARKET_STRUCTURE_VERSION,
            swing_lookback=swing_lookback,
            swing_lookforward=swing_lookforward,
            swings=swings,
            liquidity_levels=liquidity,
            fair_value_gaps=fvgs,
            order_blocks=obs
        )


# Global singleton instance
market_structure_service = MarketStructureService()
