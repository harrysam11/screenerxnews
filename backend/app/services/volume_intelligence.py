"""
P1.5-B Volume Intelligence Service.
Implements deterministic volume analytics:
Volume SMA, Relative Volume (RVOL), and Volume Acceleration.
Handles zero baseline volumes, flat periods, candle gaps, and decimal precision.
"""

from typing import List, Optional, Tuple, Dict, Any
from decimal import Decimal, getcontext, ROUND_HALF_UP

from app.schemas.ohlcv import OHLCVCandleDTO, CandleBarStatus
from app.schemas.indicators import (
    VOLUME_INTELLIGENCE_VERSION,
    IndicatorQualityStatus,
    IndicatorPointDTO,
    VolumeAccelerationPointDTO,
    SingleIndicatorSeriesDTO,
    VolumeAccelerationSeriesDTO
)

getcontext().prec = 28

DECIMAL_ZERO = Decimal("0")
DECIMAL_ONE = Decimal("1")
DECIMAL_HUNDRED = Decimal("100")
DEFAULT_DECIMAL_PLACES = Decimal("0.00000001")


def quantize_financial(val: Optional[Decimal], places: Decimal = DEFAULT_DECIMAL_PLACES) -> Optional[Decimal]:
    """Helper to cleanly quantize a Decimal without float imprecision."""
    if val is None:
        return None
    return val.quantize(places, rounding=ROUND_HALF_UP)


class VolumeIntelligenceService:
    """
    Deterministic volume feature computation engine.
    Stateless and strategy-agnostic.
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

    @staticmethod
    def _detect_gaps_in_window(
        candles: List[OHLCVCandleDTO],
        end_idx: int,
        window_size: int
    ) -> bool:
        """Checks for missing bar intervals within the lookback window."""
        if end_idx < 1 or window_size <= 1:
            return False
        start_idx = max(0, end_idx - window_size + 1)
        expected_sec = candles[0].timeframe.seconds
        for i in range(start_idx + 1, end_idx + 1):
            diff = candles[i].timestamp_unix - candles[i - 1].timestamp_unix
            if diff > expected_sec:
                return True
        return False

    @classmethod
    def calculate_volume_sma(
        cls,
        candles: List[OHLCVCandleDTO],
        period: int = 20,
        closed_only: bool = True
    ) -> SingleIndicatorSeriesDTO:
        """
        Calculates Simple Moving Average of trading volume.
        VolSMA_t = (1 / N) * sum(Volume_{t-i} for i in 0..N-1)
        """
        if period <= 0:
            raise ValueError(f"Volume SMA period must be positive, got {period}")

        eval_candles = cls._filter_candles(candles, closed_only=closed_only)
        points: List[IndicatorPointDTO] = []
        n_candles = len(eval_candles)
        dec_period = Decimal(str(period))

        for i in range(n_candles):
            c = eval_candles[i]
            if i < period - 1:
                points.append(IndicatorPointDTO(
                    timestamp_utc=c.timestamp_utc,
                    timestamp_unix=c.timestamp_unix,
                    value=None,
                    quality=IndicatorQualityStatus.INSUFFICIENT_HISTORY
                ))
            else:
                window = eval_candles[i - period + 1 : i + 1]
                has_gap = cls._detect_gaps_in_window(eval_candles, i, period)
                vol_sum = sum(bar.volume for bar in window)
                sma_val = quantize_financial(vol_sum / dec_period)
                quality = IndicatorQualityStatus.GAPS_IN_WINDOW if has_gap else IndicatorQualityStatus.CLEAN

                points.append(IndicatorPointDTO(
                    timestamp_utc=c.timestamp_utc,
                    timestamp_unix=c.timestamp_unix,
                    value=sma_val,
                    quality=quality
                ))

        return SingleIndicatorSeriesDTO(
            indicator_name="VOLUME_SMA",
            period=period,
            calculation_version=VOLUME_INTELLIGENCE_VERSION,
            points=points
        )

    @classmethod
    def calculate_rvol(
        cls,
        candles: List[OHLCVCandleDTO],
        period: int = 20,
        closed_only: bool = True
    ) -> SingleIndicatorSeriesDTO:
        """
        Calculates Relative Volume (RVOL).
        RVOL_t = Volume_t / VolSMA_t(period)
        Handles zero-baseline cases explicitly:
        - If VolSMA == 0 and Volume == 0 -> RVOL = 1.0 (neutral) with ZERO_BASELINE_VOLUME flag.
        - If VolSMA == 0 and Volume > 0 -> RVOL = None with ZERO_BASELINE_VOLUME flag.
        """
        if period <= 0:
            raise ValueError(f"RVOL period must be positive, got {period}")

        eval_candles = cls._filter_candles(candles, closed_only=closed_only)
        sma_series = cls.calculate_volume_sma(eval_candles, period=period, closed_only=False).points
        points: List[IndicatorPointDTO] = []
        n_candles = len(eval_candles)

        for i in range(n_candles):
            c = eval_candles[i]
            sma_pt = sma_series[i]

            if sma_pt.value is None:
                points.append(IndicatorPointDTO(
                    timestamp_utc=c.timestamp_utc,
                    timestamp_unix=c.timestamp_unix,
                    value=None,
                    quality=IndicatorQualityStatus.INSUFFICIENT_HISTORY
                ))
            else:
                baseline = sma_pt.value
                curr_vol = c.volume
                has_gap = (sma_pt.quality == IndicatorQualityStatus.GAPS_IN_WINDOW)

                if baseline == DECIMAL_ZERO:
                    if curr_vol == DECIMAL_ZERO:
                        points.append(IndicatorPointDTO(
                            timestamp_utc=c.timestamp_utc,
                            timestamp_unix=c.timestamp_unix,
                            value=quantize_financial(DECIMAL_ONE),
                            quality=IndicatorQualityStatus.ZERO_BASELINE_VOLUME
                        ))
                    else:
                        points.append(IndicatorPointDTO(
                            timestamp_utc=c.timestamp_utc,
                            timestamp_unix=c.timestamp_unix,
                            value=None,
                            quality=IndicatorQualityStatus.ZERO_BASELINE_VOLUME
                        ))
                else:
                    rvol_val = quantize_financial(curr_vol / baseline)
                    qual = IndicatorQualityStatus.GAPS_IN_WINDOW if has_gap else IndicatorQualityStatus.CLEAN
                    points.append(IndicatorPointDTO(
                        timestamp_utc=c.timestamp_utc,
                        timestamp_unix=c.timestamp_unix,
                        value=rvol_val,
                        quality=qual
                    ))

        return SingleIndicatorSeriesDTO(
            indicator_name="RVOL",
            period=period,
            calculation_version=VOLUME_INTELLIGENCE_VERSION,
            points=points
        )

    @classmethod
    def calculate_volume_acceleration(
        cls,
        candles: List[OHLCVCandleDTO],
        short_period: int = 3,
        long_period: int = 20,
        closed_only: bool = True
    ) -> VolumeAccelerationSeriesDTO:
        """
        Calculates volume acceleration:
        - volume_acceleration_ratio = Short Vol SMA / Long Vol SMA
        - volume_change_pct = (Volume_t - Volume_{t-1}) / Volume_{t-1} * 100
        """
        if short_period >= long_period:
            raise ValueError(f"Short period ({short_period}) must be strictly less than long period ({long_period})")

        eval_candles = cls._filter_candles(candles, closed_only=closed_only)
        n_candles = len(eval_candles)

        short_sma = cls.calculate_volume_sma(eval_candles, period=short_period, closed_only=False).points
        long_sma = cls.calculate_volume_sma(eval_candles, period=long_period, closed_only=False).points

        points: List[VolumeAccelerationPointDTO] = []

        for i in range(n_candles):
            c = eval_candles[i]
            s_pt = short_sma[i]
            l_pt = long_sma[i]

            # Compute bar-over-bar change
            vol_change_pct: Optional[Decimal] = None
            if i > 0:
                prev_vol = eval_candles[i - 1].volume
                curr_vol = c.volume
                if prev_vol > DECIMAL_ZERO:
                    vol_change_pct = quantize_financial(((curr_vol - prev_vol) / prev_vol) * DECIMAL_HUNDRED)
                elif curr_vol == DECIMAL_ZERO and prev_vol == DECIMAL_ZERO:
                    vol_change_pct = quantize_financial(DECIMAL_ZERO)
                else:
                    vol_change_pct = None  # Infinite jump from 0

            # Compute acceleration ratio
            accel_ratio: Optional[Decimal] = None
            quality = IndicatorQualityStatus.CLEAN

            if l_pt.value is None or s_pt.value is None:
                quality = IndicatorQualityStatus.INSUFFICIENT_HISTORY
            else:
                has_gap = (l_pt.quality == IndicatorQualityStatus.GAPS_IN_WINDOW or s_pt.quality == IndicatorQualityStatus.GAPS_IN_WINDOW)
                if l_pt.value == DECIMAL_ZERO:
                    quality = IndicatorQualityStatus.ZERO_BASELINE_VOLUME
                    if s_pt.value == DECIMAL_ZERO:
                        accel_ratio = quantize_financial(DECIMAL_ONE)
                    else:
                        accel_ratio = None
                else:
                    accel_ratio = quantize_financial(s_pt.value / l_pt.value)
                    quality = IndicatorQualityStatus.GAPS_IN_WINDOW if has_gap else IndicatorQualityStatus.CLEAN

            points.append(VolumeAccelerationPointDTO(
                timestamp_utc=c.timestamp_utc,
                timestamp_unix=c.timestamp_unix,
                volume_acceleration_ratio=accel_ratio,
                volume_change_pct=vol_change_pct,
                short_volume_sma=s_pt.value,
                long_volume_sma=l_pt.value,
                quality=quality
            ))

        return VolumeAccelerationSeriesDTO(
            indicator_name="VOLUME_ACCELERATION",
            short_period=short_period,
            long_period=long_period,
            calculation_version=VOLUME_INTELLIGENCE_VERSION,
            points=points
        )


# Global singleton instance
volume_intelligence_service = VolumeIntelligenceService()
