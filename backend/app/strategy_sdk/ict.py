"""
P1.5-D Optional ICT (Inner Circle Trader) Strategy Module.
Independent, optional, strategy-agnostic research package.
Implements:
1. Optimal Trade Entry (OTE): Fibonacci 0.618 - 0.786 retracement into discount/premium zones.
2. Change in State of Delivery (CISD): Decisive candle close violating opposing order flow.
3. Smart Money Technique (SMT) Divergence: Multi-market correlation divergence (fails closed if secondary pool unavailable).

RULES:
- Disabled by default.
- Never mandatory for standard strategies.
- Never mutates generic P1.5-B definitions.
- Reports missing/insufficient data honestly without fabrication.
"""

from typing import List, Dict, Any, Optional, Tuple
from decimal import Decimal, getcontext, ROUND_HALF_UP

from app.schemas.ohlcv import OHLCVCandleDTO
from app.schemas.indicators import (
    IndicatorQualityStatus,
    MarketStructureType,
    SwingPointDTO
)
from app.services.market_structure_service import market_structure_service

getcontext().prec = 28

DECIMAL_ZERO = Decimal("0")
FIB_500 = Decimal("0.500")
FIB_618 = Decimal("0.618")
FIB_705 = Decimal("0.705")
FIB_786 = Decimal("0.786")
DEFAULT_PLACES = Decimal("0.00000001")


def quantize_dec(val: Optional[Decimal]) -> Optional[Decimal]:
    if val is None:
        return None
    return val.quantize(DEFAULT_PLACES, rounding=ROUND_HALF_UP)


class ICTStrategyEngine:
    """
    Independent ICT technical concepts engine.
    Purely descriptive; zero trade executions.
    """

    @classmethod
    def calculate_ote_zones(
        cls,
        candles: List[OHLCVCandleDTO],
        lookback: int = 2,
        lookforward: int = 2,
        bar_index: int = -1
    ) -> Dict[str, Any]:
        """
        Calculates Optimal Trade Entry (OTE) retracement levels based on the most recent confirmed swing range.
        - Bullish OTE: Retracement into 0.618 - 0.786 discount zone following an upward swing leg (Swing Low to Swing High).
        - Bearish OTE: Retracement into 0.618 - 0.786 premium zone following a downward swing leg (Swing High to Swing Low).
        """
        if not candles:
            return {"status": IndicatorQualityStatus.INSUFFICIENT_HISTORY.value}

        n = len(candles)
        actual_idx = bar_index if bar_index >= 0 else (n + bar_index)
        if actual_idx < 0 or actual_idx >= n:
            actual_idx = n - 1

        eval_candles = candles[:actual_idx + 1]
        swings = market_structure_service.detect_swings(eval_candles, lookback=lookback, lookforward=lookforward, closed_only=False)

        if len(swings) < 2:
            return {
                "status": IndicatorQualityStatus.INSUFFICIENT_HISTORY.value,
                "in_bullish_ote": False,
                "in_bearish_ote": False,
                "explanation": "Insufficient confirmed swings to establish ICT reference range"
            }

        # Find the latest confirmed swing high and swing low
        latest_sh = None
        latest_sl = None
        for s in reversed(swings):
            if s.swing_type == MarketStructureType.SWING_HIGH and latest_sh is None:
                latest_sh = s
            elif s.swing_type == MarketStructureType.SWING_LOW and latest_sl is None:
                latest_sl = s
            if latest_sh and latest_sl:
                break

        if not latest_sh or not latest_sl:
            return {
                "status": IndicatorQualityStatus.INSUFFICIENT_HISTORY.value,
                "in_bullish_ote": False,
                "in_bearish_ote": False
            }

        curr_close = eval_candles[actual_idx].close_price

        # Case 1: Bullish Leg (Swing Low occurred before Swing High)
        if latest_sl.swing_candle_index < latest_sh.swing_candle_index:
            low = latest_sl.swing_price
            high = latest_sh.swing_price
            swing_range = high - low
            if swing_range <= DECIMAL_ZERO:
                return {"status": IndicatorQualityStatus.FLAT_MARKET.value}

            # Retracement from high down towards low: Price = High - (Fib * Range)
            level_50 = high - (swing_range * FIB_500)
            level_618 = high - (swing_range * FIB_618)
            level_705 = high - (swing_range * FIB_705)
            level_786 = high - (swing_range * FIB_786)

            # In Bullish OTE zone if curr_close is between 0.786 and 0.618
            in_ote = (level_786 <= curr_close <= level_618)

            return {
                "status": IndicatorQualityStatus.CLEAN.value,
                "trend_direction": "BULLISH",
                "swing_low": str(low),
                "swing_high": str(high),
                "equilibrium_50": str(quantize_dec(level_50)),
                "ote_618": str(quantize_dec(level_618)),
                "ote_sweetspot_705": str(quantize_dec(level_705)),
                "ote_786": str(quantize_dec(level_786)),
                "current_price": str(curr_close),
                "in_bullish_ote": in_ote,
                "in_bearish_ote": False
            }

        # Case 2: Bearish Leg (Swing High occurred before Swing Low)
        else:
            high = latest_sh.swing_price
            low = latest_sl.swing_price
            swing_range = high - low
            if swing_range <= DECIMAL_ZERO:
                return {"status": IndicatorQualityStatus.FLAT_MARKET.value}

            # Retracement from low up towards high: Price = Low + (Fib * Range)
            level_50 = low + (swing_range * FIB_500)
            level_618 = low + (swing_range * FIB_618)
            level_705 = low + (swing_range * FIB_705)
            level_786 = low + (swing_range * FIB_786)

            in_ote = (level_618 <= curr_close <= level_786)

            return {
                "status": IndicatorQualityStatus.CLEAN.value,
                "trend_direction": "BEARISH",
                "swing_high": str(high),
                "swing_low": str(low),
                "equilibrium_50": str(quantize_dec(level_50)),
                "ote_618": str(quantize_dec(level_618)),
                "ote_sweetspot_705": str(quantize_dec(level_705)),
                "ote_786": str(quantize_dec(level_786)),
                "current_price": str(curr_close),
                "in_bullish_ote": False,
                "in_bearish_ote": in_ote
            }

    @classmethod
    def evaluate_cisd(
        cls,
        candles: List[OHLCVCandleDTO],
        bar_index: int = -1
    ) -> Dict[str, Any]:
        """
        Evaluates Change in State of Delivery (CISD).
        A CISD occurs when a decisive candle close violates the opposing candle series opening/high
        prior to a displacement leg.
        - Bullish CISD: Current candle closes above the high of the last sequence of consecutive down-close candles.
        - Bearish CISD: Current candle closes below the low of the last sequence of consecutive up-close candles.
        """
        if not candles or len(candles) < 4:
            return {
                "status": IndicatorQualityStatus.INSUFFICIENT_HISTORY.value,
                "bullish_cisd": False,
                "bearish_cisd": False
            }

        n = len(candles)
        actual_idx = bar_index if bar_index >= 0 else (n + bar_index)
        if actual_idx < 3:
            return {"status": IndicatorQualityStatus.INSUFFICIENT_HISTORY.value, "bullish_cisd": False, "bearish_cisd": False}

        curr_bar = candles[actual_idx]

        # Check Bullish CISD: Look for preceding down-close block
        bullish_cisd = False
        bearish_cisd = False
        reference_level: Optional[Decimal] = None

        # If current bar is a strong green bar (Close > Open)
        if curr_bar.close_price > curr_bar.open_price:
            # Find the consecutive down candles prior to this expansion
            k = actual_idx - 1
            down_candles = []
            while k >= 0 and candles[k].close_price < candles[k].open_price:
                down_candles.append(candles[k])
                k -= 1

            if down_candles:
                # Highest high of that down-candle sequence
                highest_down = max(c.high_price for c in down_candles)
                reference_level = highest_down
                if curr_bar.close_price > highest_down:
                    bullish_cisd = True

        # If current bar is a strong red bar (Close < Open)
        elif curr_bar.close_price < curr_bar.open_price:
            k = actual_idx - 1
            up_candles = []
            while k >= 0 and candles[k].close_price > candles[k].open_price:
                up_candles.append(candles[k])
                k -= 1

            if up_candles:
                lowest_up = min(c.low_price for c in up_candles)
                reference_level = lowest_up
                if curr_bar.close_price < lowest_up:
                    bearish_cisd = True

        return {
            "status": IndicatorQualityStatus.CLEAN.value,
            "bullish_cisd": bullish_cisd,
            "bearish_cisd": bearish_cisd,
            "reference_level": str(reference_level) if reference_level else None,
            "current_close": str(curr_bar.close_price)
        }

    @classmethod
    def evaluate_smt_divergence(
        cls,
        candles_asset_a: List[OHLCVCandleDTO],
        candles_asset_b: Optional[List[OHLCVCandleDTO]] = None,
        lookback: int = 2,
        lookforward: int = 2
    ) -> Dict[str, Any]:
        """
        Evaluates Smart Money Technique (SMT) Divergence between two correlated assets.
        - Bullish SMT: Asset A makes a Lower Low, while Asset B fails to make a Lower Low (makes a Higher Low).
        - Bearish SMT: Asset A makes a Higher High, while Asset B fails to make a Higher High (makes a Lower High).

        STRICT HONESTY REQUIREMENT:
        If candles_asset_b is None or empty, returns INSUFFICIENT_DATA.
        Never fabricates or assumes correlated market data.
        """
        if not candles_asset_b:
            return {
                "status": IndicatorQualityStatus.INSUFFICIENT_HISTORY.value,
                "bullish_smt": False,
                "bearish_smt": False,
                "explanation": "SMT divergence requires correlated market data from a secondary pool. None provided."
            }

        swings_a = market_structure_service.detect_swings(candles_asset_a, lookback=lookback, lookforward=lookforward, closed_only=False)
        swings_b = market_structure_service.detect_swings(candles_asset_b, lookback=lookback, lookforward=lookforward, closed_only=False)

        lows_a = [s for s in swings_a if s.swing_type == MarketStructureType.SWING_LOW]
        lows_b = [s for s in swings_b if s.swing_type == MarketStructureType.SWING_LOW]
        highs_a = [s for s in swings_a if s.swing_type == MarketStructureType.SWING_HIGH]
        highs_b = [s for s in swings_b if s.swing_type == MarketStructureType.SWING_HIGH]

        bullish_smt = False
        bearish_smt = False
        explanation = "No SMT divergence detected."

        # Check Bullish SMT (need at least 2 swing lows in both)
        if len(lows_a) >= 2 and len(lows_b) >= 2:
            a_prev, a_curr = lows_a[-2], lows_a[-1]
            b_prev, b_curr = lows_b[-2], lows_b[-1]
            # Asset A made Lower Low, Asset B made Higher Low
            if a_curr.swing_price < a_prev.swing_price and b_curr.swing_price >= b_prev.swing_price:
                bullish_smt = True
                explanation = f"Bullish SMT: Asset A printed Lower Low ({a_curr.swing_price} < {a_prev.swing_price}) while Asset B printed Higher Low ({b_curr.swing_price} >= {b_prev.swing_price})."

        # Check Bearish SMT (need at least 2 swing highs in both)
        if len(highs_a) >= 2 and len(highs_b) >= 2:
            a_prev, a_curr = highs_a[-2], highs_a[-1]
            b_prev, b_curr = highs_b[-2], highs_b[-1]
            # Asset A made Higher High, Asset B made Lower High
            if a_curr.swing_price > a_prev.swing_price and b_curr.swing_price <= b_prev.swing_price:
                bearish_smt = True
                explanation = f"Bearish SMT: Asset A printed Higher High ({a_curr.swing_price} > {a_prev.swing_price}) while Asset B printed Lower High ({b_curr.swing_price} <= {b_prev.swing_price})."

        return {
            "status": IndicatorQualityStatus.CLEAN.value,
            "bullish_smt": bullish_smt,
            "bearish_smt": bearish_smt,
            "explanation": explanation
        }


# Global instance
ict_engine = ICTStrategyEngine()
