"""
P1.5-B Indicator Calculation Service.
Implements deterministic, strategy-agnostic technical price indicators:
SMA, EMA, RSI, MACD, and ATR.
Enforces closed-candle execution, gap awareness, no-lookahead calculations,
and rigorous edge-case handling (flat markets, zero denominators, insufficient history).
"""

from typing import List, Optional, Tuple, Dict, Any
from decimal import Decimal, getcontext, ROUND_HALF_UP

from app.schemas.ohlcv import OHLCVCandleDTO, CandleBarStatus
from app.schemas.indicators import (
    INDICATOR_ENGINE_VERSION,
    IndicatorQualityStatus,
    IndicatorPointDTO,
    MACDPointDTO,
    SingleIndicatorSeriesDTO,
    MACDSeriesDTO
)

# Standard high-precision context for financial operations
getcontext().prec = 28

DECIMAL_ZERO = Decimal("0")
DECIMAL_ONE = Decimal("1")
DECIMAL_HUNDRED = Decimal("100")
DECIMAL_FIFTY = Decimal("50")
DEFAULT_DECIMAL_PLACES = Decimal("0.00000001")


def quantize_financial(val: Optional[Decimal], places: Decimal = DEFAULT_DECIMAL_PLACES) -> Optional[Decimal]:
    """Helper to cleanly quantize a Decimal without float imprecision."""
    if val is None:
        return None
    return val.quantize(places, rounding=ROUND_HALF_UP)


class IndicatorEngine:
    """
    Deterministic technical indicator computation engine.
    Stateless, reproducible, and fully isolated from strategy logic.
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
        """
        Determines if any missing temporal bar interval exists within the lookback window.
        A gap is present if (t_curr - t_prev) > expected_seconds for any consecutive pair.
        """
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
    def calculate_sma(
        cls,
        candles: List[OHLCVCandleDTO],
        period: int = 14,
        closed_only: bool = True
    ) -> SingleIndicatorSeriesDTO:
        """
        Calculates Simple Moving Average (SMA) of close prices.
        Formula: SMA_t = (1 / N) * sum(Close_{t-i} for i in 0..N-1)
        """
        if period <= 0:
            raise ValueError(f"SMA period must be positive, got {period}")

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
                window_sum = sum(bar.close_price for bar in window)
                sma_val = quantize_financial(window_sum / dec_period)
                quality = IndicatorQualityStatus.GAPS_IN_WINDOW if has_gap else IndicatorQualityStatus.CLEAN

                points.append(IndicatorPointDTO(
                    timestamp_utc=c.timestamp_utc,
                    timestamp_unix=c.timestamp_unix,
                    value=sma_val,
                    quality=quality
                ))

        return SingleIndicatorSeriesDTO(
            indicator_name="SMA",
            period=period,
            calculation_version=INDICATOR_ENGINE_VERSION,
            points=points
        )

    @classmethod
    def calculate_ema(
        cls,
        candles: List[OHLCVCandleDTO],
        period: int = 14,
        closed_only: bool = True
    ) -> SingleIndicatorSeriesDTO:
        """
        Calculates Exponential Moving Average (EMA) of close prices.
        Multiplier: alpha = 2 / (period + 1)
        Initial EMA_{N-1} = SMA_{N-1}(N)
        EMA_t = Close_t * alpha + EMA_{t-1} * (1 - alpha)
        """
        if period <= 0:
            raise ValueError(f"EMA period must be positive, got {period}")

        eval_candles = cls._filter_candles(candles, closed_only=closed_only)
        points: List[IndicatorPointDTO] = []
        n_candles = len(eval_candles)
        dec_period = Decimal(str(period))
        alpha = Decimal("2") / (dec_period + DECIMAL_ONE)
        one_minus_alpha = DECIMAL_ONE - alpha

        prev_ema: Optional[Decimal] = None

        for i in range(n_candles):
            c = eval_candles[i]
            if i < period - 1:
                points.append(IndicatorPointDTO(
                    timestamp_utc=c.timestamp_utc,
                    timestamp_unix=c.timestamp_unix,
                    value=None,
                    quality=IndicatorQualityStatus.INSUFFICIENT_HISTORY
                ))
            elif i == period - 1:
                # Initial seed is the SMA of first period bars
                window = eval_candles[0 : period]
                has_gap = cls._detect_gaps_in_window(eval_candles, i, period)
                initial_sma = sum(bar.close_price for bar in window) / dec_period
                prev_ema = initial_sma
                quality = IndicatorQualityStatus.GAPS_IN_WINDOW if has_gap else IndicatorQualityStatus.CLEAN
                points.append(IndicatorPointDTO(
                    timestamp_utc=c.timestamp_utc,
                    timestamp_unix=c.timestamp_unix,
                    value=quantize_financial(prev_ema),
                    quality=quality
                ))
            else:
                has_gap = cls._detect_gaps_in_window(eval_candles, i, 2)
                curr_ema = (c.close_price * alpha) + (prev_ema * one_minus_alpha)
                prev_ema = curr_ema
                quality = IndicatorQualityStatus.GAPS_IN_WINDOW if has_gap else IndicatorQualityStatus.CLEAN
                points.append(IndicatorPointDTO(
                    timestamp_utc=c.timestamp_utc,
                    timestamp_unix=c.timestamp_unix,
                    value=quantize_financial(curr_ema),
                    quality=quality
                ))

        return SingleIndicatorSeriesDTO(
            indicator_name="EMA",
            period=period,
            calculation_version=INDICATOR_ENGINE_VERSION,
            points=points
        )

    @classmethod
    def calculate_rsi(
        cls,
        candles: List[OHLCVCandleDTO],
        period: int = 14,
        closed_only: bool = True
    ) -> SingleIndicatorSeriesDTO:
        """
        Calculates standard Wilder's Relative Strength Index (RSI).
        Requires at least period + 1 candles for first calculation.
        Handles flat markets (gain=0, loss=0 -> RSI=50), zero-loss (RSI=100), zero-gain (RSI=0).
        """
        if period <= 0:
            raise ValueError(f"RSI period must be positive, got {period}")

        eval_candles = cls._filter_candles(candles, closed_only=closed_only)
        points: List[IndicatorPointDTO] = []
        n_candles = len(eval_candles)
        dec_period = Decimal(str(period))

        if n_candles == 0:
            return SingleIndicatorSeriesDTO(
                indicator_name="RSI",
                period=period,
                calculation_version=INDICATOR_ENGINE_VERSION,
                points=[]
            )

        # Precompute deltas
        deltas: List[Decimal] = [DECIMAL_ZERO]
        for i in range(1, n_candles):
            deltas.append(eval_candles[i].close_price - eval_candles[i - 1].close_price)

        prev_avg_gain: Optional[Decimal] = None
        prev_avg_loss: Optional[Decimal] = None

        for i in range(n_candles):
            c = eval_candles[i]
            if i < period:
                points.append(IndicatorPointDTO(
                    timestamp_utc=c.timestamp_utc,
                    timestamp_unix=c.timestamp_unix,
                    value=None,
                    quality=IndicatorQualityStatus.INSUFFICIENT_HISTORY
                ))
            elif i == period:
                # First Wilder seed is average of first period gains and losses
                init_gains = [max(deltas[k], DECIMAL_ZERO) for k in range(1, period + 1)]
                init_losses = [max(-deltas[k], DECIMAL_ZERO) for k in range(1, period + 1)]
                prev_avg_gain = sum(init_gains) / dec_period
                prev_avg_loss = sum(init_losses) / dec_period
                has_gap = cls._detect_gaps_in_window(eval_candles, i, period)

                rsi_val, qual = cls._evaluate_rsi_value(prev_avg_gain, prev_avg_loss, has_gap)
                points.append(IndicatorPointDTO(
                    timestamp_utc=c.timestamp_utc,
                    timestamp_unix=c.timestamp_unix,
                    value=quantize_financial(rsi_val),
                    quality=qual
                ))
            else:
                curr_delta = deltas[i]
                curr_gain = max(curr_delta, DECIMAL_ZERO)
                curr_loss = max(-curr_delta, DECIMAL_ZERO)

                prev_avg_gain = ((prev_avg_gain * (dec_period - DECIMAL_ONE)) + curr_gain) / dec_period
                prev_avg_loss = ((prev_avg_loss * (dec_period - DECIMAL_ONE)) + curr_loss) / dec_period
                has_gap = cls._detect_gaps_in_window(eval_candles, i, 2)

                rsi_val, qual = cls._evaluate_rsi_value(prev_avg_gain, prev_avg_loss, has_gap)
                points.append(IndicatorPointDTO(
                    timestamp_utc=c.timestamp_utc,
                    timestamp_unix=c.timestamp_unix,
                    value=quantize_financial(rsi_val),
                    quality=qual
                ))

        return SingleIndicatorSeriesDTO(
            indicator_name="RSI",
            period=period,
            calculation_version=INDICATOR_ENGINE_VERSION,
            points=points
        )

    @classmethod
    def _evaluate_rsi_value(
        cls,
        avg_gain: Decimal,
        avg_loss: Decimal,
        has_gap: bool
    ) -> Tuple[Decimal, IndicatorQualityStatus]:
        """Helper to resolve RSI edge cases deterministically."""
        base_quality = IndicatorQualityStatus.GAPS_IN_WINDOW if has_gap else IndicatorQualityStatus.CLEAN

        if avg_gain == DECIMAL_ZERO and avg_loss == DECIMAL_ZERO:
            return DECIMAL_FIFTY, IndicatorQualityStatus.FLAT_MARKET
        if avg_loss == DECIMAL_ZERO:
            return DECIMAL_HUNDRED, base_quality
        if avg_gain == DECIMAL_ZERO:
            return DECIMAL_ZERO, base_quality

        # Standard RSI = 100 * avg_gain / (avg_gain + avg_loss)
        rsi = (DECIMAL_HUNDRED * avg_gain) / (avg_gain + avg_loss)
        return rsi, base_quality

    @classmethod
    def calculate_macd(
        cls,
        candles: List[OHLCVCandleDTO],
        fast_period: int = 12,
        slow_period: int = 26,
        signal_period: int = 9,
        closed_only: bool = True
    ) -> MACDSeriesDTO:
        """
        Calculates Moving Average Convergence Divergence (MACD).
        MACD Line = EMA(fast) - EMA(slow)
        Signal Line = EMA(MACD Line, signal_period)
        Histogram = MACD Line - Signal Line
        """
        if fast_period >= slow_period:
            raise ValueError(f"Fast period ({fast_period}) must be strictly less than slow period ({slow_period})")
        if signal_period <= 0:
            raise ValueError(f"Signal period must be positive, got {signal_period}")

        eval_candles = cls._filter_candles(candles, closed_only=closed_only)
        n_candles = len(eval_candles)

        fast_series = cls.calculate_ema(eval_candles, period=fast_period, closed_only=False).points
        slow_series = cls.calculate_ema(eval_candles, period=slow_period, closed_only=False).points

        macd_lines: List[Optional[Decimal]] = []
        for i in range(n_candles):
            fast_pt = fast_series[i].value
            slow_pt = slow_series[i].value
            if fast_pt is not None and slow_pt is not None:
                macd_lines.append(fast_pt - slow_pt)
            else:
                macd_lines.append(None)

        # Calculate Signal Line (EMA of MACD line)
        signal_alpha = Decimal("2") / (Decimal(str(signal_period)) + DECIMAL_ONE)
        one_minus_sig_alpha = DECIMAL_ONE - signal_alpha

        signal_lines: List[Optional[Decimal]] = [None] * n_candles
        histograms: List[Optional[Decimal]] = [None] * n_candles

        # Find first index where slow EMA produced a valid value
        first_valid_macd_idx = slow_period - 1

        if n_candles >= first_valid_macd_idx + signal_period:
            # Seed signal with SMA of first signal_period MACD values
            init_window = macd_lines[first_valid_macd_idx : first_valid_macd_idx + signal_period]
            sig_seed_idx = first_valid_macd_idx + signal_period - 1
            prev_sig = sum(v for v in init_window if v is not None) / Decimal(str(signal_period))
            signal_lines[sig_seed_idx] = prev_sig
            histograms[sig_seed_idx] = macd_lines[sig_seed_idx] - prev_sig

            for i in range(sig_seed_idx + 1, n_candles):
                curr_macd = macd_lines[i]
                if curr_macd is not None:
                    curr_sig = (curr_macd * signal_alpha) + (prev_sig * one_minus_sig_alpha)
                    prev_sig = curr_sig
                    signal_lines[i] = curr_sig
                    histograms[i] = curr_macd - curr_sig

        points: List[MACDPointDTO] = []
        for i in range(n_candles):
            c = eval_candles[i]
            m_val = macd_lines[i]
            s_val = signal_lines[i]
            h_val = histograms[i]

            if m_val is None:
                quality = IndicatorQualityStatus.INSUFFICIENT_HISTORY
            elif s_val is None:
                quality = IndicatorQualityStatus.INSUFFICIENT_HISTORY
            else:
                has_gap = cls._detect_gaps_in_window(eval_candles, i, 2)
                quality = IndicatorQualityStatus.GAPS_IN_WINDOW if has_gap else IndicatorQualityStatus.CLEAN

            points.append(MACDPointDTO(
                timestamp_utc=c.timestamp_utc,
                timestamp_unix=c.timestamp_unix,
                macd_line=quantize_financial(m_val),
                signal_line=quantize_financial(s_val),
                histogram=quantize_financial(h_val),
                quality=quality
            ))

        return MACDSeriesDTO(
            fast_period=fast_period,
            slow_period=slow_period,
            signal_period=signal_period,
            calculation_version=INDICATOR_ENGINE_VERSION,
            points=points
        )

    @classmethod
    def calculate_atr(
        cls,
        candles: List[OHLCVCandleDTO],
        period: int = 14,
        closed_only: bool = True
    ) -> SingleIndicatorSeriesDTO:
        """
        Calculates Average True Range (ATR) with Wilder's smoothing.
        True Range (TR):
          TR_0 = High_0 - Low_0
          TR_t = max(High_t - Low_t, abs(High_t - Close_{t-1}), abs(Low_t - Close_{t-1}))
        Initial ATR_{N-1} = SMA(TR, N)
        ATR_t = (ATR_{t-1} * (N - 1) + TR_t) / N
        """
        if period <= 0:
            raise ValueError(f"ATR period must be positive, got {period}")

        eval_candles = cls._filter_candles(candles, closed_only=closed_only)
        points: List[IndicatorPointDTO] = []
        n_candles = len(eval_candles)
        dec_period = Decimal(str(period))

        if n_candles == 0:
            return SingleIndicatorSeriesDTO(
                indicator_name="ATR",
                period=period,
                calculation_version=INDICATOR_ENGINE_VERSION,
                points=[]
            )

        # Step 1: Compute True Range for each bar
        true_ranges: List[Decimal] = []
        for i in range(n_candles):
            c = eval_candles[i]
            hl = c.high_price - c.low_price
            if i == 0:
                true_ranges.append(hl)
            else:
                prev_close = eval_candles[i - 1].close_price
                hc = abs(c.high_price - prev_close)
                lc = abs(c.low_price - prev_close)
                true_ranges.append(max(hl, hc, lc))

        prev_atr: Optional[Decimal] = None

        for i in range(n_candles):
            c = eval_candles[i]
            if i < period - 1:
                points.append(IndicatorPointDTO(
                    timestamp_utc=c.timestamp_utc,
                    timestamp_unix=c.timestamp_unix,
                    value=None,
                    quality=IndicatorQualityStatus.INSUFFICIENT_HISTORY
                ))
            elif i == period - 1:
                # Initial seed is SMA of first period TRs
                init_sum = sum(true_ranges[0 : period])
                prev_atr = init_sum / dec_period
                has_gap = cls._detect_gaps_in_window(eval_candles, i, period)
                quality = IndicatorQualityStatus.FLAT_MARKET if prev_atr == DECIMAL_ZERO else (
                    IndicatorQualityStatus.GAPS_IN_WINDOW if has_gap else IndicatorQualityStatus.CLEAN
                )
                points.append(IndicatorPointDTO(
                    timestamp_utc=c.timestamp_utc,
                    timestamp_unix=c.timestamp_unix,
                    value=quantize_financial(prev_atr),
                    quality=quality
                ))
            else:
                tr = true_ranges[i]
                curr_atr = ((prev_atr * (dec_period - DECIMAL_ONE)) + tr) / dec_period
                prev_atr = curr_atr
                has_gap = cls._detect_gaps_in_window(eval_candles, i, 2)
                quality = IndicatorQualityStatus.FLAT_MARKET if curr_atr == DECIMAL_ZERO else (
                    IndicatorQualityStatus.GAPS_IN_WINDOW if has_gap else IndicatorQualityStatus.CLEAN
                )
                points.append(IndicatorPointDTO(
                    timestamp_utc=c.timestamp_utc,
                    timestamp_unix=c.timestamp_unix,
                    value=quantize_financial(curr_atr),
                    quality=quality
                ))

        return SingleIndicatorSeriesDTO(
            indicator_name="ATR",
            period=period,
            calculation_version=INDICATOR_ENGINE_VERSION,
            points=points
        )


# Global singleton instance
indicator_engine = IndicatorEngine()
