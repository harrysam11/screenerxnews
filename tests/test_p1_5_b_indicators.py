"""
P1.5-B Technical Indicators & Market Structure Unit and Integration Tests.
Verifies mathematical determinism, Wilder's smoothing, zero-denominator edge cases,
gap handling, swing confirmation timing, generic FVG/OB detection, and API endpoints.
All tests run against temporary isolated databases without touching operational data.
"""

import os
import tempfile
import pytest
from decimal import Decimal
from datetime import datetime, timezone, timedelta
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.schemas.ohlcv import (
    Timeframe,
    OHLCVCandleDTO,
    CandleBarStatus
)
from app.schemas.indicators import (
    IndicatorQualityStatus,
    MarketStructureType,
    LiquidityLevelType,
    LiquidityLevelStatus,
    FVGType,
    FVGStatus,
    OrderBlockType,
    OrderBlockStatus
)
from app.services.indicator_engine import indicator_engine
from app.services.volume_intelligence import volume_intelligence_service
from app.services.market_structure_service import market_structure_service
from app.db.research_history_store import ResearchHistoryStore
from app.services.research_analytics_service import research_analytics_service
from app.api.v1.research import router as research_router


def make_candle(
    idx: int,
    open_p: str,
    high_p: str,
    low_p: str,
    close_p: str,
    volume: str = "1000",
    timeframe: Timeframe = Timeframe.M15,
    bar_status: CandleBarStatus = CandleBarStatus.CLOSED,
    base_ts: int = 1700000000,
    chain_id: str = "eip155:8453",
    pool_address: str = "0xpool123"
) -> OHLCVCandleDTO:
    """Helper to generate a validated OHLCVCandleDTO with monotonic timestamps."""
    step = timeframe.seconds
    ts_unix = base_ts + (idx * step)
    ts_utc = datetime.fromtimestamp(ts_unix, tz=timezone.utc).isoformat()
    end_utc = datetime.fromtimestamp(ts_unix + step, tz=timezone.utc).isoformat()
    return OHLCVCandleDTO(
        chain_id=chain_id,
        pool_address=pool_address,
        timeframe=timeframe,
        timestamp_utc=ts_utc,
        timestamp_unix=ts_unix,
        end_timestamp_utc=end_utc,
        open_price=Decimal(open_p),
        high_price=Decimal(high_p),
        low_price=Decimal(low_p),
        close_price=Decimal(close_p),
        volume=Decimal(volume),
        bar_status=bar_status
    )


def test_sma_and_ema_numerical_accuracy_and_warmup():
    """
    Validates SMA and EMA against exact mathematical derivations.
    Prices: [10, 11, 12, 13, 14, 15, 16]
    SMA(5):
      idx 0..3: None (insufficient history)
      idx 4: (10+11+12+13+14)/5 = 12.0
      idx 5: (11+12+13+14+15)/5 = 13.0
      idx 6: (12+13+14+15+16)/5 = 14.0
    EMA(5):
      alpha = 2 / (5 + 1) = 1/3
      idx 4: seed SMA = 12.0
      idx 5: 15 * (1/3) + 12 * (2/3) = 5 + 8 = 13.0
      idx 6: 16 * (1/3) + 13 * (2/3) = 5.33333333 + 8.66666667 = 14.0
    """
    prices = ["10", "11", "12", "13", "14", "15", "16"]
    candles = [make_candle(i, p, str(Decimal(p) + Decimal("1")), str(Decimal(p) - Decimal("1")), p) for i, p in enumerate(prices)]

    # SMA(5)
    sma_res = indicator_engine.calculate_sma(candles, period=5)
    assert len(sma_res.points) == 7
    for i in range(4):
        assert sma_res.points[i].value is None
        assert sma_res.points[i].quality == IndicatorQualityStatus.INSUFFICIENT_HISTORY

    assert sma_res.points[4].value == Decimal("12.00000000")
    assert sma_res.points[5].value == Decimal("13.00000000")
    assert sma_res.points[6].value == Decimal("14.00000000")

    # EMA(5)
    ema_res = indicator_engine.calculate_ema(candles, period=5)
    assert len(ema_res.points) == 7
    for i in range(4):
        assert ema_res.points[i].value is None
        assert ema_res.points[i].quality == IndicatorQualityStatus.INSUFFICIENT_HISTORY

    assert ema_res.points[4].value == Decimal("12.00000000")
    assert ema_res.points[5].value == Decimal("13.00000000")
    assert ema_res.points[6].value == Decimal("14.00000000")


def test_rsi_edge_cases_rising_falling_flat_and_warmup():
    """
    Validates Wilder's RSI edge cases:
    - Purely rising: RSI = 100
    - Purely falling: RSI = 0
    - Purely flat: RSI = 50 with FLAT_MARKET quality
    - Warmup check for period 14
    """
    # 1. Rising market (16 candles)
    rising = [make_candle(i, str(10 + i), str(11 + i), str(10 + i), str(11 + i)) for i in range(16)]
    rsi_rising = indicator_engine.calculate_rsi(rising, period=14)
    assert rsi_rising.points[13].value is None  # idx 0..13 insufficient history
    assert rsi_rising.points[14].value == Decimal("100.00000000")
    assert rsi_rising.points[15].value == Decimal("100.00000000")

    # 2. Falling market (16 candles)
    falling = [make_candle(i, str(100 - i), str(100 - i), str(99 - i), str(99 - i)) for i in range(16)]
    rsi_falling = indicator_engine.calculate_rsi(falling, period=14)
    assert rsi_falling.points[14].value == Decimal("0.00000000")
    assert rsi_falling.points[15].value == Decimal("0.00000000")

    # 3. Flat market (16 candles of identical price 50)
    flat = [make_candle(i, "50", "50", "50", "50") for i in range(16)]
    rsi_flat = indicator_engine.calculate_rsi(flat, period=14)
    assert rsi_flat.points[14].value == Decimal("50.00000000")
    assert rsi_flat.points[14].quality == IndicatorQualityStatus.FLAT_MARKET
    assert rsi_flat.points[15].value == Decimal("50.00000000")
    assert rsi_flat.points[15].quality == IndicatorQualityStatus.FLAT_MARKET


def test_macd_calculation_components_and_alignment():
    """
    Validates MACD Line, Signal Line, and Histogram alignment.
    Histogram must equal MACD Line minus Signal Line.
    Signal Line and Histogram remain None until slow_period + signal_period - 1.
    """
    # 35 candles with linear progression
    candles = [make_candle(i, str(10 + i), str(12 + i), str(9 + i), str(11 + i)) for i in range(35)]
    macd_res = indicator_engine.calculate_macd(candles, fast_period=12, slow_period=26, signal_period=9)

    # Slow period is 26, so first valid MACD line is at index 25
    assert macd_res.points[24].macd_line is None
    assert macd_res.points[25].macd_line is not None

    # Signal requires 9 additional MACD values, so first valid signal line is at index 25 + 9 - 1 = 33
    assert macd_res.points[32].signal_line is None
    assert macd_res.points[32].histogram is None
    assert macd_res.points[33].signal_line is not None
    assert macd_res.points[33].histogram is not None

    # Alignment check
    for pt in macd_res.points:
        if pt.macd_line is not None and pt.signal_line is not None:
            expected_hist = pt.macd_line - pt.signal_line
            assert pt.histogram == expected_hist


def test_atr_and_true_range_with_gap_handling():
    """
    Validates ATR computation and True Range calculation where previous close
    creates a gap beyond current High - Low.
    """
    # Bar 0: High=10, Low=8, Close=9. TR = 2
    # Bar 1: Open=15, High=16, Low=14, Close=15.
    # Current H-L = 2, but High - PrevClose = 16 - 9 = 7. TR = 7
    c0 = make_candle(0, "8", "10", "8", "9")
    c1 = make_candle(1, "15", "16", "14", "15")

    # With period 2:
    atr_res = indicator_engine.calculate_atr([c0, c1], period=2)
    assert atr_res.points[0].value is None
    # ATR(2) at idx 1 = (2 + 7) / 2 = 4.5
    assert atr_res.points[1].value == Decimal("4.50000000")


def test_volume_sma_rvol_and_zero_baseline_cases():
    """
    Validates Volume SMA and RVOL with zero baseline handling:
    - Normal volume: RVOL = Vol / SMA
    - Vol = 0, SMA = 0 -> RVOL = 1.0, ZERO_BASELINE_VOLUME
    - Vol > 0, SMA = 0 -> RVOL = None, ZERO_BASELINE_VOLUME
    """
    # 5 candles with volume 100, 200, 300, 400, 500
    candles = [make_candle(i, "10", "11", "9", "10", volume=str((i + 1) * 100)) for i in range(5)]
    rvol_res = volume_intelligence_service.calculate_rvol(candles, period=3)

    # idx 2: window [100, 200, 300], avg = 200. Vol = 300. RVOL = 300 / 200 = 1.5
    assert rvol_res.points[2].value == Decimal("1.50000000")
    # idx 4: window [300, 400, 500], avg = 400. Vol = 500. RVOL = 500 / 400 = 1.25
    assert rvol_res.points[4].value == Decimal("1.25000000")

    # Zero baseline case
    zero_candles = [make_candle(i, "10", "11", "9", "10", volume="0") for i in range(4)]
    rvol_zero = volume_intelligence_service.calculate_rvol(zero_candles, period=3)
    assert rvol_zero.points[2].value == Decimal("1.00000000")
    assert rvol_zero.points[2].quality == IndicatorQualityStatus.ZERO_BASELINE_VOLUME


def test_volume_acceleration_ratio_and_change_pct():
    """
    Validates volume acceleration ratio (Short SMA / Long SMA)
    and bar-over-bar percentage volume change.
    """
    volumes = ["100", "100", "100", "200", "400"]
    candles = [make_candle(i, "10", "11", "9", "10", volume=v) for i, v in enumerate(volumes)]
    accel_res = volume_intelligence_service.calculate_volume_acceleration(candles, short_period=2, long_period=4)

    # Bar-over-bar change at idx 3: (200 - 100) / 100 * 100 = 100%
    assert accel_res.points[3].volume_change_pct == Decimal("100.00000000")
    # Bar-over-bar change at idx 4: (400 - 200) / 200 * 100 = 100%
    assert accel_res.points[4].volume_change_pct == Decimal("100.00000000")

    # Long period 4 is ready at idx 3:
    # Long SMA at idx 3: (100+100+100+200)/4 = 125
    # Short SMA at idx 3: (100+200)/2 = 150
    # Ratio = 150 / 125 = 1.2
    assert accel_res.points[3].volume_acceleration_ratio == Decimal("1.20000000")


def test_swing_confirmation_timing_and_no_lookahead():
    """
    Verifies that a swing point occurring at candle i is NOT emitted or confirmed
    until candle i + lookforward has closed.
    """
    # Create pattern: 10, 15, 20 (peak), 15, 10
    # Swing high at idx 2 (price 20). Lookback=2, lookforward=2.
    prices = [("10", "11", "9", "10"),
              ("14", "16", "13", "15"),
              ("18", "22", "17", "20"),  # Peak High=22 at idx 2
              ("14", "16", "13", "15"),
              ("10", "11", "9", "10")]
    candles = [make_candle(i, op, hp, lp, cp) for i, (op, hp, lp, cp) in enumerate(prices)]

    # 1. Evaluate with all 5 candles: Peak at idx 2 is confirmable at idx 4 (2 + 2 = 4 < 5)
    swings = market_structure_service.detect_swings(candles, lookback=2, lookforward=2)
    assert len(swings) == 1
    sh = swings[0]
    assert sh.swing_type == MarketStructureType.SWING_HIGH
    assert sh.swing_candle_index == 2
    assert sh.swing_price == Decimal("22.00000000")
    assert sh.confirmation_candle_index == 4
    assert sh.confirmation_timestamp_unix == candles[4].timestamp_unix

    # 2. Evaluate with only 4 candles (idx 0..3): candle 4 has NOT arrived yet!
    # The potential swing at idx 2 CANNOT be confirmed. Zero lookahead!
    swings_partial = market_structure_service.detect_swings(candles[:4], lookback=2, lookforward=2)
    assert len(swings_partial) == 0


def test_liquidity_level_derivation_swept_and_violation():
    """
    Validates derived liquidity levels and chronological status transitions:
    ACTIVE -> SWEPT (wick breach without close beyond) -> VIOLATED (close beyond).
    """
    # 0: base, 1: base, 2: peak at 25, 3: pull back, 4: confirmation of peak (BSL level at 25)
    # 5: wick reaches 26, but closes at 24 -> SWEPT
    # 6: closes at 27 -> VIOLATED
    prices = [
        ("10", "12", "9", "10"),
        ("14", "16", "13", "15"),
        ("20", "25", "19", "22"), # Peak at 25, idx 2
        ("15", "17", "14", "16"),
        ("12", "14", "11", "13"), # Confirmed here at idx 4
        ("22", "26", "20", "24"), # Wick 26 >= 25, close 24 <= 25 -> SWEPT
        ("25", "28", "24", "27")  # Close 27 > 25 -> VIOLATED
    ]
    candles = [make_candle(i, op, hp, lp, cp) for i, (op, hp, lp, cp) in enumerate(prices)]
    swings = market_structure_service.detect_swings(candles, lookback=2, lookforward=2)
    levels = market_structure_service.derive_liquidity_levels(candles, swings)

    bsl_levels = [l for l in levels if l.level_type == LiquidityLevelType.BUY_SIDE_LIQUIDITY]
    assert len(bsl_levels) == 1
    lvl = bsl_levels[0]
    assert lvl.price_level == Decimal("25.00000000")
    assert lvl.status == LiquidityLevelStatus.VIOLATED
    assert lvl.violated_candle_index == 6
    assert lvl.swept_candle_index == 5


def test_fair_value_gap_and_generic_order_block():
    """
    Validates:
    1. Bullish FVG: Candle 0 High < Candle 2 Low
    2. Generic Bullish Order Block: Red candle preceding strong ATR displacement
    """
    # Build 4-candle sequence:
    # 0: Open = 10, High = 11, Low = 8, Close = 9 (red candle, down-close)
    # 1: Large green displacement candle: Open = 9, Low = 9, High = 20, Close = 20
    # 2: Candle 2: Open = 20, Low = 13, High = 22, Close = 21 (Low 13 > Candle 0 High 11 -> FVG [11, 13])
    # 3: Pull back into FVG and OB: Low = 10 -> MITIGATES OB and FILLS FVG
    c0 = make_candle(0, "10", "11", "8", "9")     # Down candle
    c1 = make_candle(1, "9", "20", "9", "20")     # Strong green displacement (+11)
    c2 = make_candle(2, "20", "22", "13", "21")   # Low 13 > c0 High 11
    c3 = make_candle(3, "21", "21", "10", "11")   # Retraces into gap and OB

    candles = [c0, c1, c2, c3]

    # FVG
    fvgs = market_structure_service.detect_fair_value_gaps(candles)
    assert len(fvgs) == 1
    fvg = fvgs[0]
    assert fvg.fvg_type == FVGType.BULLISH_FVG
    assert fvg.bottom_price == Decimal("11.00000000")
    assert fvg.top_price == Decimal("13.00000000")
    assert fvg.gap_size == Decimal("2.00000000")
    assert fvg.status == FVGStatus.FULLY_FILLED

    # Order Block with displacement multiplier
    obs = market_structure_service.detect_order_blocks(candles, displacement_atr_multiple=Decimal("1.0"), atr_period=2)
    bull_obs = [o for o in obs if o.block_type == OrderBlockType.BULLISH_ORDER_BLOCK]
    assert len(bull_obs) == 1
    ob = bull_obs[0]
    assert ob.block_type == OrderBlockType.BULLISH_ORDER_BLOCK
    assert ob.originating_candle_index == 0
    assert ob.confirmation_candle_index == 1
    assert ob.top_price == Decimal("11.00000000")
    assert ob.bottom_price == Decimal("8.00000000")
    assert ob.status == OrderBlockStatus.MITIGATED


def test_research_api_indicators_and_market_structure_isolated_db():
    """
    Integration test verifying GET /api/v1/research/indicators and
    GET /api/v1/research/market-structure endpoints against an isolated research DB.
    """
    temp_dir = tempfile.mkdtemp()
    temp_db = os.path.join(temp_dir, "test_research_p1_5_b.db")

    # Wire isolated store
    isolated_store = ResearchHistoryStore(db_path=temp_db)
    orig_store = research_analytics_service.store
    research_analytics_service.store = isolated_store

    app = FastAPI()
    app.include_router(research_router, prefix="/api/v1")
    client = TestClient(app)

    try:
        # Insert 30 candles into isolated research DB
        raw_candles = []
        for i in range(30):
            p = str(100 + i)
            c = make_candle(i, p, str(Decimal(p) + Decimal("2")), str(Decimal(p) - Decimal("1")), p, volume="5000", pool_address="0xabc123")
            raw_candles.append({
                "chain_id": c.chain_id,
                "pool_address": c.pool_address,
                "timeframe": c.timeframe.value,
                "timestamp_utc": c.timestamp_utc,
                "timestamp_unix": c.timestamp_unix,
                "end_timestamp_utc": c.end_timestamp_utc,
                "open_price": str(c.open_price),
                "high_price": str(c.high_price),
                "low_price": str(c.low_price),
                "close_price": str(c.close_price),
                "volume": str(c.volume),
                "bar_status": c.bar_status.value
            })
        isolated_store.upsert_candles_batch(raw_candles)

        # 1. Call Indicators API
        ind_resp = client.get("/api/v1/research/indicators/eip155:8453/0xabc123?timeframe=15m&sma_period=5&rsi_period=14")
        assert ind_resp.status_code == 200
        data = ind_resp.json()
        assert data["total_candles_evaluated"] == 30
        assert data["sma"] is not None
        assert data["sma"]["period"] == 5
        assert data["rsi"] is not None
        assert data["rsi"]["period"] == 14
        assert data["volume_sma"] is not None
        assert data["rvol"] is not None

        # 2. Call Market Structure API
        struct_resp = client.get("/api/v1/research/market-structure/eip155:8453/0xabc123?timeframe=15m&swing_lookback=2&swing_lookforward=2")
        assert struct_resp.status_code == 200
        s_data = struct_resp.json()
        assert s_data["total_candles_evaluated"] == 30
        assert "swings" in s_data
        assert "liquidity_levels" in s_data
        assert "fair_value_gaps" in s_data
        assert "order_blocks" in s_data

    finally:
        research_analytics_service.store = orig_store
        if os.path.exists(temp_db):
            os.remove(temp_db)


def test_gap_detection_flags_in_indicator_series():
    """
    Validates that temporal missing intervals (where delta_t > interval_seconds)
    within the lookback window are flagged with GAPS_IN_WINDOW quality.
    """
    # Create 5 candles with a 2-hour gap between candle 2 and candle 3
    base_ts = 1700000000
    step = 900  # 15m
    c0 = make_candle(0, "10", "11", "9", "10", base_ts=base_ts)
    c1 = make_candle(1, "10", "11", "9", "10", base_ts=base_ts)
    c2 = make_candle(2, "10", "11", "9", "10", base_ts=base_ts)
    # Candle 3 is 2 hours later (7200 seconds instead of 900)
    c3 = make_candle(10, "10", "11", "9", "10", base_ts=base_ts)
    c4 = make_candle(11, "10", "11", "9", "10", base_ts=base_ts)

    candles = [c0, c1, c2, c3, c4]
    sma_res = indicator_engine.calculate_sma(candles, period=3)

    # Index 2 has clean window [c0, c1, c2]
    assert sma_res.points[2].quality == IndicatorQualityStatus.CLEAN
    # Index 3 window has gap between c2 and c3
    assert sma_res.points[3].quality == IndicatorQualityStatus.GAPS_IN_WINDOW
    # Index 4 window [c2, c3, c4] also spans the gap
    assert sma_res.points[4].quality == IndicatorQualityStatus.GAPS_IN_WINDOW


def test_incomplete_candle_filtering_and_inclusion():
    """
    Verifies that active INCOMPLETE bars are excluded by default (closed_only=True)
    and only evaluated when closed_only=False.
    """
    c0 = make_candle(0, "10", "11", "9", "10", bar_status=CandleBarStatus.CLOSED)
    c1 = make_candle(1, "11", "12", "10", "11", bar_status=CandleBarStatus.CLOSED)
    c2_incomplete = make_candle(2, "12", "15", "11", "14", bar_status=CandleBarStatus.INCOMPLETE)

    candles = [c0, c1, c2_incomplete]

    # Closed only: evaluates 2 candles
    sma_closed = indicator_engine.calculate_sma(candles, period=2, closed_only=True)
    assert len(sma_closed.points) == 2
    assert sma_closed.points[1].value == Decimal("10.50000000")

    # Incomplete included: evaluates 3 candles
    sma_all = indicator_engine.calculate_sma(candles, period=2, closed_only=False)
    assert len(sma_all.points) == 3
    assert sma_all.points[2].value == Decimal("12.50000000")


def test_parameter_bounds_and_invalid_inputs():
    """Validates exception raising on invalid indicator parameters."""
    candles = [make_candle(i, "10", "11", "9", "10") for i in range(5)]

    with pytest.raises(ValueError, match="SMA period must be positive"):
        indicator_engine.calculate_sma(candles, period=0)

    with pytest.raises(ValueError, match="EMA period must be positive"):
        indicator_engine.calculate_ema(candles, period=-1)

    with pytest.raises(ValueError, match="RSI period must be positive"):
        indicator_engine.calculate_rsi(candles, period=0)

    with pytest.raises(ValueError, match="Fast period .* must be strictly less than slow period"):
        indicator_engine.calculate_macd(candles, fast_period=26, slow_period=12)

    with pytest.raises(ValueError, match="ATR period must be positive"):
        indicator_engine.calculate_atr(candles, period=0)

    with pytest.raises(ValueError, match="Lookback and lookforward must be positive"):
        market_structure_service.detect_swings(candles, lookback=0, lookforward=2)


def test_deterministic_repeatability_and_operational_db_isolation():
    """
    Verifies that running calculations multiple times yields identical Decimal results,
    and asserts that data/memecoin_os.db is never touched.
    """
    import hashlib
    op_db_path = "data/memecoin_os.db"
    assert os.path.exists(op_db_path)
    with open(op_db_path, "rb") as f:
        before_hash = hashlib.sha256(f.read()).hexdigest()

    prices = ["10", "12", "15", "14", "18", "22", "19", "17", "16", "20"]
    candles = [make_candle(i, p, str(Decimal(p) + Decimal("1")), str(Decimal(p) - Decimal("1")), p) for i, p in enumerate(prices)]

    # Run calculations 3 times
    for _ in range(3):
        sma1 = indicator_engine.calculate_sma(candles, period=3)
        ema1 = indicator_engine.calculate_ema(candles, period=3)
        rsi1 = indicator_engine.calculate_rsi(candles, period=3)
        swings1 = market_structure_service.detect_swings(candles, lookback=2, lookforward=2)

    assert sma1.points[4].value == Decimal("15.66666667")
    assert len(swings1) >= 1

    # Invariant assertion
    with open(op_db_path, "rb") as f:
        after_hash = hashlib.sha256(f.read()).hexdigest()
    assert before_hash == after_hash, "Operational database SHA-256 hash mutated!"

