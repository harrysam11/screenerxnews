"""
P1.5-B Technical Indicators & Market Structure Schemas.
Defines typed DTOs, quality flags, version metadata, and market structure contracts
for deterministic, strategy-agnostic technical analysis.
"""

from enum import Enum
from typing import List, Optional, Dict, Any
from decimal import Decimal
from pydantic import BaseModel, Field

from app.schemas.ohlcv import Timeframe, CandleIntegrityStatus


# Version identifiers for calculation auditing
INDICATOR_ENGINE_VERSION = "1.0.0"
VOLUME_INTELLIGENCE_VERSION = "1.0.0"
MARKET_STRUCTURE_VERSION = "1.0.0"


class IndicatorQualityStatus(str, Enum):
    """Data quality and integrity status of a calculated indicator point."""
    CLEAN = "CLEAN"
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
    GAPS_IN_WINDOW = "GAPS_IN_WINDOW"
    FLAT_MARKET = "FLAT_MARKET"
    ZERO_BASELINE_VOLUME = "ZERO_BASELINE_VOLUME"
    UNAVAILABLE = "UNAVAILABLE"


class MarketStructureType(str, Enum):
    """Type of swing pivot point."""
    SWING_HIGH = "SWING_HIGH"
    SWING_LOW = "SWING_LOW"


class LiquidityLevelType(str, Enum):
    """Directional liquidity pool classification."""
    BUY_SIDE_LIQUIDITY = "BUY_SIDE_LIQUIDITY"   # Resting buy-stops above swing high
    SELL_SIDE_LIQUIDITY = "SELL_SIDE_LIQUIDITY" # Resting sell-stops below swing low


class LiquidityLevelStatus(str, Enum):
    """Life-cycle state of a liquidity level."""
    ACTIVE = "ACTIVE"       # Untested resting liquidity
    SWEPT = "SWEPT"         # Wick pierced level, but candle closed back inside
    VIOLATED = "VIOLATED"   # Candle body closed beyond level


class FVGType(str, Enum):
    """Classification of Fair Value Gap."""
    BULLISH_FVG = "BULLISH_FVG" # Candle 0 High < Candle 2 Low
    BEARISH_FVG = "BEARISH_FVG" # Candle 0 Low > Candle 2 High


class FVGStatus(str, Enum):
    """Mitigation status of a Fair Value Gap."""
    UNFILLED = "UNFILLED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FULLY_FILLED = "FULLY_FILLED"


class OrderBlockType(str, Enum):
    """Classification of Generic Order Block."""
    BULLISH_ORDER_BLOCK = "BULLISH_ORDER_BLOCK" # Down candle preceding upward displacement
    BEARISH_ORDER_BLOCK = "BEARISH_ORDER_BLOCK" # Up candle preceding downward displacement


class OrderBlockStatus(str, Enum):
    """Mitigation status of a Generic Order Block."""
    ACTIVE = "ACTIVE"       # Unmitigated zone
    MITIGATED = "MITIGATED" # Price retraced into body/range
    VIOLATED = "VIOLATED"   # Price invalidated opposing boundary of block


# ============================================================================
# Indicator Series DTOs
# ============================================================================

class IndicatorPointDTO(BaseModel):
    """Single point in a standard indicator series."""
    timestamp_utc: str = Field(..., description="Bar opening UTC timestamp")
    timestamp_unix: int = Field(..., description="Bar opening Unix timestamp")
    value: Optional[Decimal] = Field(None, description="Calculated value, or None if unavailable/insufficient history")
    quality: IndicatorQualityStatus = Field(default=IndicatorQualityStatus.CLEAN)


class MACDPointDTO(BaseModel):
    """Single point for Moving Average Convergence Divergence."""
    timestamp_utc: str
    timestamp_unix: int
    macd_line: Optional[Decimal] = Field(None, description="Fast EMA minus Slow EMA")
    signal_line: Optional[Decimal] = Field(None, description="Signal line (EMA of MACD line)")
    histogram: Optional[Decimal] = Field(None, description="MACD line minus Signal line")
    quality: IndicatorQualityStatus = Field(default=IndicatorQualityStatus.CLEAN)


class VolumeAccelerationPointDTO(BaseModel):
    """Single point for volume acceleration and short/long volume comparison."""
    timestamp_utc: str
    timestamp_unix: int
    volume_acceleration_ratio: Optional[Decimal] = Field(None, description="Short Vol SMA / Long Vol SMA")
    volume_change_pct: Optional[Decimal] = Field(None, description="Percentage change in volume vs previous bar")
    short_volume_sma: Optional[Decimal] = None
    long_volume_sma: Optional[Decimal] = None
    quality: IndicatorQualityStatus = Field(default=IndicatorQualityStatus.CLEAN)


class SingleIndicatorSeriesDTO(BaseModel):
    """Container for a single calculated indicator time series."""
    indicator_name: str
    period: int
    calculation_version: str = INDICATOR_ENGINE_VERSION
    points: List[IndicatorPointDTO] = Field(default_factory=list)


class MACDSeriesDTO(BaseModel):
    """Container for MACD time series."""
    indicator_name: str = "MACD"
    fast_period: int = 12
    slow_period: int = 26
    signal_period: int = 9
    calculation_version: str = INDICATOR_ENGINE_VERSION
    points: List[MACDPointDTO] = Field(default_factory=list)


class VolumeAccelerationSeriesDTO(BaseModel):
    """Container for volume acceleration time series."""
    indicator_name: str = "VOLUME_ACCELERATION"
    short_period: int = 3
    long_period: int = 20
    calculation_version: str = VOLUME_INTELLIGENCE_VERSION
    points: List[VolumeAccelerationPointDTO] = Field(default_factory=list)


# ============================================================================
# Market Structure DTOs
# ============================================================================

class SwingPointDTO(BaseModel):
    """
    Confirmed swing pivot point.
    Strictly documents both the candle where the swing occurred AND the subsequent
    candle where the swing became confirmed, eliminating lookahead bias.
    """
    swing_id: str
    swing_type: MarketStructureType
    swing_price: Decimal = Field(..., description="High price for Swing High, Low price for Swing Low")
    swing_candle_index: int = Field(..., description="0-indexed position in series where swing occurred")
    swing_timestamp_utc: str = Field(..., description="Bar opening UTC timestamp of the swing candle")
    swing_timestamp_unix: int
    confirmation_candle_index: int = Field(..., description="0-indexed position where confirmation criteria was met")
    confirmation_timestamp_utc: str = Field(..., description="Bar opening UTC timestamp when swing was confirmed")
    confirmation_timestamp_unix: int
    lookback_bars: int = Field(..., description="Required lower-high/higher-low bars to the left")
    lookforward_bars: int = Field(..., description="Required lower-high/higher-low bars to the right for confirmation")


class LiquidityLevelDTO(BaseModel):
    """
    Generic liquidity level derived from a confirmed swing point.
    Tracks chronological status (ACTIVE, SWEPT, VIOLATED) starting after confirmation.
    """
    level_id: str
    level_type: LiquidityLevelType
    price_level: Decimal
    originating_swing_id: str
    originating_timestamp_utc: str
    confirmed_timestamp_utc: str
    status: LiquidityLevelStatus = LiquidityLevelStatus.ACTIVE
    tested_count: int = Field(default=0, ge=0)
    swept_timestamp_utc: Optional[str] = None
    swept_candle_index: Optional[int] = None
    violated_timestamp_utc: Optional[str] = None
    violated_candle_index: Optional[int] = None


class FairValueGapDTO(BaseModel):
    """
    Generic 3-candle Fair Value Gap (imbalance) detection.
    Bullish: Candle 0 High < Candle 2 Low (gap between High 0 and Low 2).
    Bearish: Candle 0 Low > Candle 2 High (gap between Low 0 and High 2).
    """
    gap_id: str
    fvg_type: FVGType
    candle_0_index: int
    candle_1_index: int = Field(..., description="Originating expansion/displacement candle index")
    candle_2_index: int = Field(..., description="Confirmation candle index")
    originating_timestamp_utc: str
    confirmed_timestamp_utc: str
    top_price: Decimal = Field(..., description="Upper boundary of the gap")
    bottom_price: Decimal = Field(..., description="Lower boundary of the gap")
    gap_size: Decimal = Field(..., description="Absolute distance (top_price - bottom_price)")
    status: FVGStatus = FVGStatus.UNFILLED
    mitigated_timestamp_utc: Optional[str] = None
    mitigated_candle_index: Optional[int] = None


class OrderBlockDTO(BaseModel):
    """
    Generic Order Block detection.
    Identifies the last opposite-direction candle preceding a decisive expansion/displacement move.
    Bullish: Last down-close candle before an upward expansion (>= displacement_atr_mult * ATR).
    Bearish: Last up-close candle before a downward expansion (>= displacement_atr_mult * ATR).
    """
    block_id: str
    block_type: OrderBlockType
    originating_candle_index: int
    originating_timestamp_utc: str
    confirmation_candle_index: int
    confirmation_timestamp_utc: str
    open_price: Decimal
    high_price: Decimal
    low_price: Decimal
    close_price: Decimal
    top_price: Decimal = Field(..., description="Upper zone limit of the order block")
    bottom_price: Decimal = Field(..., description="Lower zone limit of the order block")
    displacement_atr_multiple: Decimal
    status: OrderBlockStatus = OrderBlockStatus.ACTIVE
    mitigated_timestamp_utc: Optional[str] = None
    mitigated_candle_index: Optional[int] = None


# ============================================================================
# API Response Containers
# ============================================================================

class TechnicalIndicatorsResponseDTO(BaseModel):
    """Consolidated API response container for technical and volume indicators."""
    chain_id: str
    pool_address: str
    timeframe: Timeframe
    total_candles_evaluated: int
    calculation_version: str = INDICATOR_ENGINE_VERSION
    sma: Optional[SingleIndicatorSeriesDTO] = None
    ema: Optional[SingleIndicatorSeriesDTO] = None
    rsi: Optional[SingleIndicatorSeriesDTO] = None
    macd: Optional[MACDSeriesDTO] = None
    atr: Optional[SingleIndicatorSeriesDTO] = None
    volume_sma: Optional[SingleIndicatorSeriesDTO] = None
    rvol: Optional[SingleIndicatorSeriesDTO] = None
    volume_acceleration: Optional[VolumeAccelerationSeriesDTO] = None


class MarketStructureResponseDTO(BaseModel):
    """Consolidated API response container for generic market structure features."""
    chain_id: str
    pool_address: str
    timeframe: Timeframe
    total_candles_evaluated: int
    calculation_version: str = MARKET_STRUCTURE_VERSION
    swing_lookback: int
    swing_lookforward: int
    swings: List[SwingPointDTO] = Field(default_factory=list)
    liquidity_levels: List[LiquidityLevelDTO] = Field(default_factory=list)
    fair_value_gaps: List[FairValueGapDTO] = Field(default_factory=list)
    order_blocks: List[OrderBlockDTO] = Field(default_factory=list)
