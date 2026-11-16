"""
P1.5-C Feature Registry for StrategySDK.
Maintains a controlled, audited registry of approved market features derived from
P1.5-A candles and P1.5-B indicator and market structure engines.
Provides type safety, operator compatibility checks, and deterministic value extraction.
"""

from typing import List, Dict, Any, Callable, Optional, Tuple, Set
from decimal import Decimal
from pydantic import BaseModel, Field

from app.schemas.ohlcv import OHLCVCandleDTO
from app.schemas.indicators import (
    INDICATOR_ENGINE_VERSION,
    VOLUME_INTELLIGENCE_VERSION,
    MARKET_STRUCTURE_VERSION,
    IndicatorQualityStatus,
    MarketStructureType,
    FVGStatus,
    OrderBlockStatus
)
from app.services.indicator_engine import indicator_engine
from app.services.volume_intelligence import volume_intelligence_service
from app.services.market_structure_service import market_structure_service
from app.strategy_sdk.schemas import ConditionOperator
from app.strategy_sdk.ict import ict_engine


class FeatureDefinition(BaseModel):
    """Metadata specification for a registered feature."""
    feature_id: str
    data_type: str = Field(..., description="'numeric', 'boolean', or 'string'")
    description: str
    supported_operators: Set[ConditionOperator]
    default_params: Dict[str, Any] = Field(default_factory=dict)
    calculation_version: str
    safe_for_historical: bool = True
    extractor: Callable[[List[OHLCVCandleDTO], int, Dict[str, Any]], Tuple[Optional[Any], IndicatorQualityStatus]] = Field(
        ..., exclude=True
    )


class FeatureRegistry:
    """
    Central, controlled registry of all approved features available to declarative strategies.
    Zero arbitrary or unknown features permitted.
    """

    def __init__(self):
        self._features: Dict[str, FeatureDefinition] = {}
        self._register_all_features()

    def get_feature(self, feature_id: str) -> Optional[FeatureDefinition]:
        return self._features.get(feature_id.lower().strip())

    def list_features(self) -> Dict[str, Dict[str, Any]]:
        return {
            fid: {
                "feature_id": f.feature_id,
                "data_type": f.data_type,
                "description": f.description,
                "supported_operators": [op.value for op in f.supported_operators],
                "default_params": f.default_params,
                "calculation_version": f.calculation_version,
                "safe_for_historical": f.safe_for_historical
            }
            for fid, f in self._features.items()
        }

    def extract_value(
        self,
        feature_id: str,
        candles: List[OHLCVCandleDTO],
        bar_index: int = -1,
        params: Optional[Dict[str, Any]] = None
    ) -> Tuple[Optional[Any], IndicatorQualityStatus]:
        """
        Extracts a feature value for the candle at bar_index.
        Default bar_index = -1 (latest available candle).
        """
        f = self.get_feature(feature_id)
        if not f:
            return None, IndicatorQualityStatus.UNAVAILABLE
        if not candles:
            return None, IndicatorQualityStatus.INSUFFICIENT_HISTORY

        # Resolve index
        n = len(candles)
        actual_idx = bar_index if bar_index >= 0 else (n + bar_index)
        if actual_idx < 0 or actual_idx >= n:
            return None, IndicatorQualityStatus.INSUFFICIENT_HISTORY

        merged_params = dict(f.default_params)
        if params:
            merged_params.update(params)

        return f.extractor(candles, actual_idx, merged_params)

    # -------------------------------------------------------------------------
    # Feature Registrations
    # -------------------------------------------------------------------------
    def _register_all_features(self):
        numeric_ops = {
            ConditionOperator.EQ, ConditionOperator.NEQ, ConditionOperator.GT,
            ConditionOperator.GTE, ConditionOperator.LT, ConditionOperator.LTE,
            ConditionOperator.BETWEEN, ConditionOperator.CROSSES_ABOVE, ConditionOperator.CROSSES_BELOW
        }
        boolean_ops = {ConditionOperator.EQ, ConditionOperator.NEQ}
        string_ops = {ConditionOperator.EQ, ConditionOperator.NEQ}

        # 1. Direct OHLCV Price Features
        self._register_ohlcv_features(numeric_ops, string_ops)

        # 2. P1.5-B Price Indicators
        self._register_indicator_features(numeric_ops)

        # 3. P1.5-B Volume Features
        self._register_volume_features(numeric_ops)

        # 4. P1.5-B Market Structure Features
        self._register_market_structure_features(numeric_ops, boolean_ops)

        # 5. P1.5-D Optional ICT Strategy Features
        self._register_ict_features(boolean_ops)

    def _register_ohlcv_features(self, numeric_ops: Set[ConditionOperator], string_ops: Set[ConditionOperator]):
        self._features["open"] = FeatureDefinition(
            feature_id="open",
            data_type="numeric",
            description="Bar opening price",
            supported_operators=numeric_ops,
            calculation_version="1.0.0",
            extractor=lambda candles, idx, p: (candles[idx].open_price, IndicatorQualityStatus.CLEAN)
        )
        self._features["high"] = FeatureDefinition(
            feature_id="high",
            data_type="numeric",
            description="Bar highest price",
            supported_operators=numeric_ops,
            calculation_version="1.0.0",
            extractor=lambda candles, idx, p: (candles[idx].high_price, IndicatorQualityStatus.CLEAN)
        )
        self._features["low"] = FeatureDefinition(
            feature_id="low",
            data_type="numeric",
            description="Bar lowest price",
            supported_operators=numeric_ops,
            calculation_version="1.0.0",
            extractor=lambda candles, idx, p: (candles[idx].low_price, IndicatorQualityStatus.CLEAN)
        )
        self._features["close"] = FeatureDefinition(
            feature_id="close",
            data_type="numeric",
            description="Bar closing price",
            supported_operators=numeric_ops,
            calculation_version="1.0.0",
            extractor=lambda candles, idx, p: (candles[idx].close_price, IndicatorQualityStatus.CLEAN)
        )
        self._features["volume"] = FeatureDefinition(
            feature_id="volume",
            data_type="numeric",
            description="Bar trading volume",
            supported_operators=numeric_ops,
            calculation_version="1.0.0",
            extractor=lambda candles, idx, p: (candles[idx].volume, IndicatorQualityStatus.CLEAN)
        )
        self._features["bar_status"] = FeatureDefinition(
            feature_id="bar_status",
            data_type="string",
            description="Bar closure status ('CLOSED' or 'INCOMPLETE')",
            supported_operators=string_ops,
            calculation_version="1.0.0",
            extractor=lambda candles, idx, p: (candles[idx].bar_status.value, IndicatorQualityStatus.CLEAN)
        )

    def _register_indicator_features(self, numeric_ops: Set[ConditionOperator]):
        def extract_sma(candles, idx, p):
            period = int(p.get("period", 14))
            series = indicator_engine.calculate_sma(candles[:idx+1], period=period, closed_only=False).points
            if not series:
                return None, IndicatorQualityStatus.INSUFFICIENT_HISTORY
            pt = series[-1]
            return pt.value, pt.quality

        self._features["sma"] = FeatureDefinition(
            feature_id="sma",
            data_type="numeric",
            description="Simple Moving Average of close prices",
            supported_operators=numeric_ops,
            default_params={"period": 14},
            calculation_version=INDICATOR_ENGINE_VERSION,
            extractor=extract_sma
        )

        def extract_ema(candles, idx, p):
            period = int(p.get("period", 14))
            series = indicator_engine.calculate_ema(candles[:idx+1], period=period, closed_only=False).points
            if not series:
                return None, IndicatorQualityStatus.INSUFFICIENT_HISTORY
            pt = series[-1]
            return pt.value, pt.quality

        self._features["ema"] = FeatureDefinition(
            feature_id="ema",
            data_type="numeric",
            description="Exponential Moving Average of close prices",
            supported_operators=numeric_ops,
            default_params={"period": 14},
            calculation_version=INDICATOR_ENGINE_VERSION,
            extractor=extract_ema
        )

        def extract_rsi(candles, idx, p):
            period = int(p.get("period", 14))
            series = indicator_engine.calculate_rsi(candles[:idx+1], period=period, closed_only=False).points
            if not series:
                return None, IndicatorQualityStatus.INSUFFICIENT_HISTORY
            pt = series[-1]
            return pt.value, pt.quality

        self._features["rsi"] = FeatureDefinition(
            feature_id="rsi",
            data_type="numeric",
            description="Relative Strength Index (Wilder's)",
            supported_operators=numeric_ops,
            default_params={"period": 14},
            calculation_version=INDICATOR_ENGINE_VERSION,
            extractor=extract_rsi
        )

        def extract_macd_line(candles, idx, p):
            fast = int(p.get("fast_period", 12))
            slow = int(p.get("slow_period", 26))
            sig = int(p.get("signal_period", 9))
            series = indicator_engine.calculate_macd(candles[:idx+1], fast_period=fast, slow_period=slow, signal_period=sig, closed_only=False).points
            if not series:
                return None, IndicatorQualityStatus.INSUFFICIENT_HISTORY
            pt = series[-1]
            return pt.macd_line, pt.quality

        self._features["macd_line"] = FeatureDefinition(
            feature_id="macd_line",
            data_type="numeric",
            description="MACD Line (Fast EMA - Slow EMA)",
            supported_operators=numeric_ops,
            default_params={"fast_period": 12, "slow_period": 26, "signal_period": 9},
            calculation_version=INDICATOR_ENGINE_VERSION,
            extractor=extract_macd_line
        )

        def extract_macd_signal(candles, idx, p):
            fast = int(p.get("fast_period", 12))
            slow = int(p.get("slow_period", 26))
            sig = int(p.get("signal_period", 9))
            series = indicator_engine.calculate_macd(candles[:idx+1], fast_period=fast, slow_period=slow, signal_period=sig, closed_only=False).points
            if not series:
                return None, IndicatorQualityStatus.INSUFFICIENT_HISTORY
            pt = series[-1]
            return pt.signal_line, pt.quality

        self._features["macd_signal"] = FeatureDefinition(
            feature_id="macd_signal",
            data_type="numeric",
            description="MACD Signal Line (EMA of MACD Line)",
            supported_operators=numeric_ops,
            default_params={"fast_period": 12, "slow_period": 26, "signal_period": 9},
            calculation_version=INDICATOR_ENGINE_VERSION,
            extractor=extract_macd_signal
        )

        def extract_macd_histogram(candles, idx, p):
            fast = int(p.get("fast_period", 12))
            slow = int(p.get("slow_period", 26))
            sig = int(p.get("signal_period", 9))
            series = indicator_engine.calculate_macd(candles[:idx+1], fast_period=fast, slow_period=slow, signal_period=sig, closed_only=False).points
            if not series:
                return None, IndicatorQualityStatus.INSUFFICIENT_HISTORY
            pt = series[-1]
            return pt.histogram, pt.quality

        self._features["macd_histogram"] = FeatureDefinition(
            feature_id="macd_histogram",
            data_type="numeric",
            description="MACD Histogram (MACD Line - Signal Line)",
            supported_operators=numeric_ops,
            default_params={"fast_period": 12, "slow_period": 26, "signal_period": 9},
            calculation_version=INDICATOR_ENGINE_VERSION,
            extractor=extract_macd_histogram
        )

        def extract_atr(candles, idx, p):
            period = int(p.get("period", 14))
            series = indicator_engine.calculate_atr(candles[:idx+1], period=period, closed_only=False).points
            if not series:
                return None, IndicatorQualityStatus.INSUFFICIENT_HISTORY
            pt = series[-1]
            return pt.value, pt.quality

        self._features["atr"] = FeatureDefinition(
            feature_id="atr",
            data_type="numeric",
            description="Average True Range (Wilder's)",
            supported_operators=numeric_ops,
            default_params={"period": 14},
            calculation_version=INDICATOR_ENGINE_VERSION,
            extractor=extract_atr
        )

    def _register_volume_features(self, numeric_ops: Set[ConditionOperator]):
        def extract_vol_sma(candles, idx, p):
            period = int(p.get("period", 20))
            series = volume_intelligence_service.calculate_volume_sma(candles[:idx+1], period=period, closed_only=False).points
            if not series:
                return None, IndicatorQualityStatus.INSUFFICIENT_HISTORY
            pt = series[-1]
            return pt.value, pt.quality

        self._features["volume_sma"] = FeatureDefinition(
            feature_id="volume_sma",
            data_type="numeric",
            description="Volume Simple Moving Average",
            supported_operators=numeric_ops,
            default_params={"period": 20},
            calculation_version=VOLUME_INTELLIGENCE_VERSION,
            extractor=extract_vol_sma
        )

        def extract_rvol(candles, idx, p):
            period = int(p.get("period", 20))
            series = volume_intelligence_service.calculate_rvol(candles[:idx+1], period=period, closed_only=False).points
            if not series:
                return None, IndicatorQualityStatus.INSUFFICIENT_HISTORY
            pt = series[-1]
            return pt.value, pt.quality

        self._features["rvol"] = FeatureDefinition(
            feature_id="rvol",
            data_type="numeric",
            description="Relative Volume (current bar volume / volume SMA)",
            supported_operators=numeric_ops,
            default_params={"period": 20},
            calculation_version=VOLUME_INTELLIGENCE_VERSION,
            extractor=extract_rvol
        )

        def extract_vol_accel(candles, idx, p):
            short_p = int(p.get("short_period", 3))
            long_p = int(p.get("long_period", 20))
            series = volume_intelligence_service.calculate_volume_acceleration(candles[:idx+1], short_period=short_p, long_period=long_p, closed_only=False).points
            if not series:
                return None, IndicatorQualityStatus.INSUFFICIENT_HISTORY
            pt = series[-1]
            return pt.volume_acceleration_ratio, pt.quality

        self._features["volume_acceleration_ratio"] = FeatureDefinition(
            feature_id="volume_acceleration_ratio",
            data_type="numeric",
            description="Short Vol SMA / Long Vol SMA ratio",
            supported_operators=numeric_ops,
            default_params={"short_period": 3, "long_period": 20},
            calculation_version=VOLUME_INTELLIGENCE_VERSION,
            extractor=extract_vol_accel
        )

    def _register_market_structure_features(self, numeric_ops: Set[ConditionOperator], boolean_ops: Set[ConditionOperator]):
        def extract_has_active_bullish_fvg(candles, idx, p):
            fvgs = market_structure_service.detect_fair_value_gaps(candles[:idx+1], closed_only=False)
            active = any(f.fvg_type.value == "BULLISH_FVG" and f.status != FVGStatus.FULLY_FILLED for f in fvgs)
            return active, IndicatorQualityStatus.CLEAN

        self._features["has_active_bullish_fvg"] = FeatureDefinition(
            feature_id="has_active_bullish_fvg",
            data_type="boolean",
            description="True if an unfilled or partially filled Bullish FVG exists",
            supported_operators=boolean_ops,
            calculation_version=MARKET_STRUCTURE_VERSION,
            extractor=extract_has_active_bullish_fvg
        )

        def extract_has_active_bearish_fvg(candles, idx, p):
            fvgs = market_structure_service.detect_fair_value_gaps(candles[:idx+1], closed_only=False)
            active = any(f.fvg_type.value == "BEARISH_FVG" and f.status != FVGStatus.FULLY_FILLED for f in fvgs)
            return active, IndicatorQualityStatus.CLEAN

        self._features["has_active_bearish_fvg"] = FeatureDefinition(
            feature_id="has_active_bearish_fvg",
            data_type="boolean",
            description="True if an unfilled or partially filled Bearish FVG exists",
            supported_operators=boolean_ops,
            calculation_version=MARKET_STRUCTURE_VERSION,
            extractor=extract_has_active_bearish_fvg
        )

        def extract_has_active_bullish_ob(candles, idx, p):
            obs = market_structure_service.detect_order_blocks(candles[:idx+1], closed_only=False)
            active = any(o.block_type.value == "BULLISH_ORDER_BLOCK" and o.status == OrderBlockStatus.ACTIVE for o in obs)
            return active, IndicatorQualityStatus.CLEAN

        self._features["has_active_bullish_ob"] = FeatureDefinition(
            feature_id="has_active_bullish_ob",
            data_type="boolean",
            description="True if an unmitigated Bullish Order Block exists",
            supported_operators=boolean_ops,
            calculation_version=MARKET_STRUCTURE_VERSION,
            extractor=extract_has_active_bullish_ob
        )

        def extract_has_active_bearish_ob(candles, idx, p):
            obs = market_structure_service.detect_order_blocks(candles[:idx+1], closed_only=False)
            active = any(o.block_type.value == "BEARISH_ORDER_BLOCK" and o.status == OrderBlockStatus.ACTIVE for o in obs)
            return active, IndicatorQualityStatus.CLEAN

        self._features["has_active_bearish_ob"] = FeatureDefinition(
            feature_id="has_active_bearish_ob",
            data_type="boolean",
            description="True if an unmitigated Bearish Order Block exists",
            supported_operators=boolean_ops,
            calculation_version=MARKET_STRUCTURE_VERSION,
            extractor=extract_has_active_bearish_ob
        )

        def extract_recent_swing_high(candles, idx, p):
            lookback = int(p.get("lookback", 2))
            lookforward = int(p.get("lookforward", 2))
            swings = market_structure_service.detect_swings(candles[:idx+1], lookback=lookback, lookforward=lookforward, closed_only=False)
            shs = [s for s in swings if s.swing_type == MarketStructureType.SWING_HIGH]
            if not shs:
                return None, IndicatorQualityStatus.INSUFFICIENT_HISTORY
            return shs[-1].swing_price, IndicatorQualityStatus.CLEAN

        self._features["recent_swing_high_price"] = FeatureDefinition(
            feature_id="recent_swing_high_price",
            data_type="numeric",
            description="Price level of the most recent confirmed Swing High",
            supported_operators=numeric_ops,
            default_params={"lookback": 2, "lookforward": 2},
            calculation_version=MARKET_STRUCTURE_VERSION,
            extractor=extract_recent_swing_high
        )

        def extract_recent_swing_low(candles, idx, p):
            lookback = int(p.get("lookback", 2))
            lookforward = int(p.get("lookforward", 2))
            swings = market_structure_service.detect_swings(candles[:idx+1], lookback=lookback, lookforward=lookforward, closed_only=False)
            sls = [s for s in swings if s.swing_type == MarketStructureType.SWING_LOW]
            if not sls:
                return None, IndicatorQualityStatus.INSUFFICIENT_HISTORY
            return sls[-1].swing_price, IndicatorQualityStatus.CLEAN

        self._features["recent_swing_low_price"] = FeatureDefinition(
            feature_id="recent_swing_low_price",
            data_type="numeric",
            description="Price level of the most recent confirmed Swing Low",
            supported_operators=numeric_ops,
            default_params={"lookback": 2, "lookforward": 2},
            calculation_version=MARKET_STRUCTURE_VERSION,
            extractor=extract_recent_swing_low
        )

    def _register_ict_features(self, boolean_ops: Set[ConditionOperator]):
        """Registers optional ICT (Inner Circle Trader) strategy features."""
        def extract_bullish_ote(candles, idx, p):
            res = ict_engine.calculate_ote_zones(candles[:idx+1], bar_index=idx)
            return res.get("in_bullish_ote", False), IndicatorQualityStatus(res.get("status", "CLEAN"))

        self._features["ict_in_bullish_ote_zone"] = FeatureDefinition(
            feature_id="ict_in_bullish_ote_zone",
            data_type="boolean",
            description="True if price is retracing in the 0.618 - 0.786 Fibonacci discount OTE zone",
            supported_operators=boolean_ops,
            calculation_version="1.0.0",
            extractor=extract_bullish_ote
        )

        def extract_bearish_ote(candles, idx, p):
            res = ict_engine.calculate_ote_zones(candles[:idx+1], bar_index=idx)
            return res.get("in_bearish_ote", False), IndicatorQualityStatus(res.get("status", "CLEAN"))

        self._features["ict_in_bearish_ote_zone"] = FeatureDefinition(
            feature_id="ict_in_bearish_ote_zone",
            data_type="boolean",
            description="True if price is retracing in the 0.618 - 0.786 Fibonacci premium OTE zone",
            supported_operators=boolean_ops,
            calculation_version="1.0.0",
            extractor=extract_bearish_ote
        )

        def extract_bullish_cisd(candles, idx, p):
            res = ict_engine.evaluate_cisd(candles[:idx+1], bar_index=idx)
            return res.get("bullish_cisd", False), IndicatorQualityStatus(res.get("status", "CLEAN"))

        self._features["ict_bullish_cisd_confirmed"] = FeatureDefinition(
            feature_id="ict_bullish_cisd_confirmed",
            data_type="boolean",
            description="True if bullish Change in State of Delivery (CISD) confirmed",
            supported_operators=boolean_ops,
            calculation_version="1.0.0",
            extractor=extract_bullish_cisd
        )

        def extract_bearish_cisd(candles, idx, p):
            res = ict_engine.evaluate_cisd(candles[:idx+1], bar_index=idx)
            return res.get("bearish_cisd", False), IndicatorQualityStatus(res.get("status", "CLEAN"))

        self._features["ict_bearish_cisd_confirmed"] = FeatureDefinition(
            feature_id="ict_bearish_cisd_confirmed",
            data_type="boolean",
            description="True if bearish Change in State of Delivery (CISD) confirmed",
            supported_operators=boolean_ops,
            calculation_version="1.0.0",
            extractor=extract_bearish_cisd
        )

        def extract_bullish_smt(candles, idx, p):
            res = ict_engine.evaluate_smt_divergence(candles[:idx+1])
            return res.get("bullish_smt", False), IndicatorQualityStatus(res.get("status", "INSUFFICIENT_HISTORY"))

        self._features["ict_smt_bullish_divergence"] = FeatureDefinition(
            feature_id="ict_smt_bullish_divergence",
            data_type="boolean",
            description="True if bullish SMT divergence confirmed (requires correlated secondary pool)",
            supported_operators=boolean_ops,
            calculation_version="1.0.0",
            extractor=extract_bullish_smt
        )


# Global feature registry instance
feature_registry = FeatureRegistry()

