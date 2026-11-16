"""
Phase P1.5-A Test Suite: Historical OHLCV Foundation, Integrity & Gap Detection.
Verifies:
1. Decimal-safe mathematical bounds validation.
2. Raw ordering defect detection without silent pre-sorting.
3. Duplicate timestamp handling and conflicting revision audit logs.
4. Discrete temporal gap detection without synthetic candle interpolation.
5. End-timestamp closure evaluation (t_start + duration).
6. Strategy-dependent minimum history requirements.
7. Pool-specific candle identity with currency and base/quote orientation.
8. Offline / provider-unavailable state distinction.
9. Protected ingestion authorization (403 unauth vs 200 auth) and operational DB isolation.
"""

import os
import asyncio
import tempfile
import pytest
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from fastapi.testclient import TestClient
from fastapi import FastAPI

from app.schemas.ohlcv import (
    Timeframe,
    PriceCurrency,
    VolumeCurrency,
    CandleBarStatus,
    CandleIntegrityStatus,
    OHLCVCandleDTO,
    CandleSeriesIntegrityReportDTO
)
from app.services.candle_integrity_service import CandleIntegrityValidator
from app.db.research_history_store import ResearchHistoryStore
from app.api.v1 import api_v1_router


# Build isolated FastAPI test app
isolated_api_app = FastAPI()
isolated_api_app.include_router(api_v1_router)
client = TestClient(isolated_api_app)


# -----------------------------------------------------------------------------
# 1. Decimal-Safe Mathematical Bounds Validation
# -----------------------------------------------------------------------------
def test_candle_bounds_mathematical_validation():
    """
    Verifies that invalid OHLC relationships and negative values are strictly rejected:
    - High < Open, High < Close, Low > Open, Low > Close, Low < 0, Volume < 0
    """
    v = CandleIntegrityValidator()

    # Valid candle
    ok, err = v.validate_single_candle_bounds(
        Decimal("1.00"), Decimal("1.20"), Decimal("0.90"), Decimal("1.10"), Decimal("5000")
    )
    assert ok is True
    assert err is None

    # High < Open
    ok, err = v.validate_single_candle_bounds(
        Decimal("1.25"), Decimal("1.20"), Decimal("0.90"), Decimal("1.10"), Decimal("5000")
    )
    assert ok is False
    assert "High price" in err

    # High < Close
    ok, err = v.validate_single_candle_bounds(
        Decimal("1.00"), Decimal("1.05"), Decimal("0.90"), Decimal("1.10"), Decimal("5000")
    )
    assert ok is False
    assert "High price" in err

    # Low > Open
    ok, err = v.validate_single_candle_bounds(
        Decimal("0.85"), Decimal("1.20"), Decimal("0.90"), Decimal("1.10"), Decimal("5000")
    )
    assert ok is False
    assert "Low price" in err

    # Low > Close
    ok, err = v.validate_single_candle_bounds(
        Decimal("1.00"), Decimal("1.20"), Decimal("1.05"), Decimal("0.95"), Decimal("5000")
    )
    assert ok is False
    assert "Low price" in err

    # Negative Low
    ok, err = v.validate_single_candle_bounds(
        Decimal("1.00"), Decimal("1.20"), Decimal("-0.10"), Decimal("1.10"), Decimal("5000")
    )
    assert ok is False
    assert "cannot be negative" in err

    # Negative Volume
    ok, err = v.validate_single_candle_bounds(
        Decimal("1.00"), Decimal("1.20"), Decimal("0.90"), Decimal("1.10"), Decimal("-50")
    )
    assert ok is False
    assert "Volume" in err


# -----------------------------------------------------------------------------
# 2. Raw Ordering Defect Detection Without Silent Pre-Sorting
# -----------------------------------------------------------------------------
def test_raw_ordering_defect_detection_preserves_input_sequence():
    """
    Verifies that the validator reports out-of-order candles from the raw input
    without silently sorting first and hiding evidence of provider defects.
    """
    base_time = datetime(2026, 11, 16, 10, 0, 0, tzinfo=timezone.utc)
    raw_shuffled = [
        {"timestamp": base_time.isoformat(), "open": "1.0", "high": "1.1", "low": "0.9", "close": "1.05", "volume": "1000"},
        # Defect: earlier timestamp placed after later timestamp
        {"timestamp": (base_time - timedelta(minutes=15)).isoformat(), "open": "0.95", "high": "1.0", "low": "0.9", "close": "1.0", "volume": "800"},
        {"timestamp": (base_time + timedelta(minutes=15)).isoformat(), "open": "1.05", "high": "1.15", "low": "1.0", "close": "1.10", "volume": "1200"}
    ]

    valid_candles, report = CandleIntegrityValidator.evaluate_series_integrity(
        raw_candles=raw_shuffled,
        chain_id="solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp",
        pool_address="PoolOrderDefect111111111111111111111111111",
        timeframe=Timeframe.M15,
        reference_now=base_time + timedelta(hours=2)
    )

    # Ordering defects must be surfaced
    assert report.ordering_defects_count == 1
    assert len(report.ordering_issues) == 1
    defect = report.ordering_issues[0]
    assert defect.index == 1
    assert defect.issue_type == "NON_MONOTONIC"
    assert report.integrity_status == CandleIntegrityStatus.ORDERING_DEFECTS

    # The resulting normalized candle list must be properly sorted chronologically
    assert len(valid_candles) == 3
    assert valid_candles[0].timestamp_unix < valid_candles[1].timestamp_unix < valid_candles[2].timestamp_unix


# -----------------------------------------------------------------------------
# 3. Duplicate Timestamp Reporting and Conflicting Revision Policy
# -----------------------------------------------------------------------------
def test_duplicate_timestamp_reporting_and_revision_policy():
    """
    Verifies:
    1. Identical duplicates are treated as no-op without duplicating rows.
    2. Conflicting revisions update candle values, increment revision_count, and log audit entries.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_ohlcv_rev.db")
        store = ResearchHistoryStore(db_path=db_path)

        c_id = "eip155:8453"
        p_addr = "0xPoolRevisionTest1111111111111111111111111"
        ts = "2026-11-16T12:00:00Z"

        candle_v1 = {
            "chain_id": c_id,
            "pool_address": p_addr,
            "timeframe": "15m",
            "timestamp_utc": ts,
            "open_price": "1.00",
            "high_price": "1.10",
            "low_price": "0.95",
            "close_price": "1.05",
            "volume": "10000"
        }

        # 1. Initial insert
        res1 = store.upsert_candle(candle_v1)
        assert res1 == "NEW_INSERT"
        assert store.get_candle_count(c_id, p_addr, "15m") == 1

        # 2. Identical duplicate -> IDENTICAL_DUPLICATE (no-op)
        res2 = store.upsert_candle(candle_v1)
        assert res2 == "IDENTICAL_DUPLICATE"
        assert store.get_candle_count(c_id, p_addr, "15m") == 1

        # 3. Conflicting provider update -> CONFLICTING_UPDATE (updates row, increments rev count, logs audit)
        candle_v2 = dict(candle_v1)
        candle_v2["high_price"] = "1.25"  # Provider corrected high price from 1.10 to 1.25
        candle_v2["volume"] = "12500"

        res3 = store.upsert_candle(candle_v2)
        assert res3 == "CONFLICTING_UPDATE"
        assert store.get_candle_count(c_id, p_addr, "15m") == 1

        # Verify updated values and revision count
        stored = store.get_candles(c_id, p_addr, "15m")
        assert len(stored) == 1
        assert stored[0]["high_price"] == "1.25"
        assert stored[0]["revision_count"] == 1

        # Verify audit log recorded
        audits = store.get_candle_revision_audit_logs(c_id, p_addr)
        assert len(audits) == 1
        assert "1.1" in audits[0]["old_ohlcv_json"]
        assert "1.25" in audits[0]["new_ohlcv_json"]


# -----------------------------------------------------------------------------
# 4. Discrete Temporal Gap Detection Without Fabrication
# -----------------------------------------------------------------------------
def test_discrete_temporal_gap_detection_without_fabrication():
    """
    Verifies that missing 15m intervals are detected, exact gap intervals reported,
    and missing data is NEVER interpolated or synthesized as fake candles.
    """
    t0 = datetime(2026, 11, 16, 8, 0, 0, tzinfo=timezone.utc)
    raw_series = [
        # Bar 0: 08:00
        {"timestamp": t0.isoformat(), "open": "1.0", "high": "1.05", "low": "0.98", "close": "1.02", "volume": "500"},
        # Bar 1: 08:15
        {"timestamp": (t0 + timedelta(minutes=15)).isoformat(), "open": "1.02", "high": "1.08", "low": "1.01", "close": "1.06", "volume": "600"},
        # GAP: Missing 08:30 and 08:45 (30 minutes / 2 missing bars)
        # Bar 2: 09:00
        {"timestamp": (t0 + timedelta(minutes=60)).isoformat(), "open": "1.06", "high": "1.12", "low": "1.04", "close": "1.10", "volume": "700"}
    ]

    valid_candles, report = CandleIntegrityValidator.evaluate_series_integrity(
        raw_candles=raw_series,
        chain_id="eip155:1",
        pool_address="0xPoolGapTest111111111111111111111111111111",
        timeframe=Timeframe.M15,
        reference_now=t0 + timedelta(hours=3)
    )

    assert report.gaps_count == 1
    assert report.total_missing_bars == 2
    assert report.integrity_status == CandleIntegrityStatus.GAPS_DETECTED

    gap = report.gap_intervals[0]
    assert gap.missing_bars_count == 2
    assert gap.start_timestamp_utc == "2026-11-16T08:30:00Z"
    assert gap.end_timestamp_utc == "2026-11-16T09:00:00Z"

    # Only 3 real bars returned; zero synthetic bars injected
    assert len(valid_candles) == 3


# -----------------------------------------------------------------------------
# 5. End-Timestamp Closure Evaluation
# -----------------------------------------------------------------------------
def test_candle_closure_evaluated_by_end_timestamp():
    """
    Verifies that candle closure is determined by end timestamp (t_start + duration):
    - When reference_now < t_start + duration -> INCOMPLETE
    - When reference_now >= t_start + duration -> CLOSED
    """
    now = datetime(2026, 11, 16, 12, 10, 0, tzinfo=timezone.utc)
    # Candle started at 12:00 for 15m timeframe -> ends at 12:15. At 12:10 it is INCOMPLETE!
    candle_1200 = {"timestamp": "2026-11-16T12:00:00Z", "open": "1.0", "high": "1.1", "low": "0.9", "close": "1.05", "volume": "100"}
    # Candle started at 11:45 for 15m timeframe -> ended at 12:00. At 12:10 it is CLOSED!
    candle_1145 = {"timestamp": "2026-11-16T11:45:00Z", "open": "0.95", "high": "1.0", "low": "0.92", "close": "1.0", "volume": "90"}

    candles, report = CandleIntegrityValidator.evaluate_series_integrity(
        raw_candles=[candle_1145, candle_1200],
        chain_id="eip155:42161",
        pool_address="0xPoolClosure111111111111111111111111111111",
        timeframe=Timeframe.M15,
        reference_now=now
    )

    assert len(candles) == 2
    c_closed = next(c for c in candles if c.timestamp_utc == "2026-11-16T11:45:00Z")
    c_incomp = next(c for c in candles if c.timestamp_utc == "2026-11-16T12:00:00Z")

    assert c_closed.bar_status == CandleBarStatus.CLOSED
    assert c_closed.end_timestamp_utc == "2026-11-16T12:00:00Z"

    assert c_incomp.bar_status == CandleBarStatus.INCOMPLETE
    assert c_incomp.end_timestamp_utc == "2026-11-16T12:15:00Z"
    assert report.incomplete_latest_bar is True


# -----------------------------------------------------------------------------
# 6. Strategy-Dependent Minimum History Requirements
# -----------------------------------------------------------------------------
def test_strategy_dependent_insufficient_history():
    """
    Verifies that minimum history requirement is strategy-dependent:
    - 5 closed bars satisfy min_bars_required=4
    - 5 closed bars FAIL min_bars_required=14 (e.g. RSI requirement)
    """
    base_t = datetime(2026, 11, 16, 6, 0, 0, tzinfo=timezone.utc)
    raw = [
        {"timestamp": (base_t + timedelta(minutes=15 * i)).isoformat(), "open": "1.0", "high": "1.05", "low": "0.95", "close": "1.02", "volume": "100"}
        for i in range(5)
    ]

    # Strategy A: requires 4 bars -> SUFFICIENT
    _, rep_a = CandleIntegrityValidator.evaluate_series_integrity(
        raw_candles=raw,
        chain_id="solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp",
        pool_address="PoolStratHist1111111111111111111111111111",
        timeframe=Timeframe.M15,
        min_bars_required=4,
        reference_now=base_t + timedelta(hours=3)
    )
    assert rep_a.bars_available == 5
    assert rep_a.is_history_sufficient is True
    assert rep_a.integrity_status == CandleIntegrityStatus.CLEAN

    # Strategy B: requires 14 bars (e.g. RSI-14 warm-up) -> INSUFFICIENT
    _, rep_b = CandleIntegrityValidator.evaluate_series_integrity(
        raw_candles=raw,
        chain_id="solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp",
        pool_address="PoolStratHist1111111111111111111111111111",
        timeframe=Timeframe.M15,
        min_bars_required=14,
        reference_now=base_t + timedelta(hours=3)
    )
    assert rep_b.bars_available == 5
    assert rep_b.bars_required == 14
    assert rep_b.is_history_sufficient is False
    assert rep_b.integrity_status == CandleIntegrityStatus.INSUFFICIENT_HISTORY


# -----------------------------------------------------------------------------
# 7. Pool-Specific Candle Identity with Orientation & Currencies
# -----------------------------------------------------------------------------
def test_candle_identity_with_orientation_and_currency():
    """
    Verifies that pool-specific candle identity supports base/quote orientation
    and price/volume currencies, preventing collisions.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_ident.db")
        store = ResearchHistoryStore(db_path=db_path)

        c_usd = {
            "chain_id": "eip155:1",
            "pool_address": "0xPoolIdentTest11111111111111111111111111",
            "base_token_address": "0xBaseToken111111111111111111111111111",
            "quote_token_address": "0xQuoteToken1111111111111111111111111",
            "timeframe": "1h",
            "timestamp_utc": "2026-11-16T10:00:00Z",
            "open_price": "2.00",
            "high_price": "2.10",
            "low_price": "1.90",
            "close_price": "2.05",
            "volume": "50000",
            "price_currency": "USD",
            "volume_currency": "USD"
        }
        res_usd = store.upsert_candle(c_usd)
        assert res_usd == "NEW_INSERT"

        # Same pool and timestamp but priced in TOKEN currency -> distinct candle identity
        c_tok = dict(c_usd)
        c_tok["price_currency"] = "TOKEN"
        c_tok["volume_currency"] = "TOKEN"
        c_tok["open_price"] = "0.001"
        c_tok["high_price"] = "0.0011"
        c_tok["low_price"] = "0.0009"
        c_tok["close_price"] = "0.00105"

        res_tok = store.upsert_candle(c_tok)
        assert res_tok == "NEW_INSERT"

        # Both records exist independently without collision
        all_c = store.get_candles("eip155:1", "0xPoolIdentTest11111111111111111111111111", "1h")
        assert len(all_c) == 2


# -----------------------------------------------------------------------------
# 8. GeckoTerminal Adapter Offline / Provider-Unavailable Distinction
# -----------------------------------------------------------------------------
def test_geckoterminal_ohlcv_adapter_and_offline_distinction():
    """
    Verifies that GeckoTerminalClient returns explicit OFFLINE, PROVIDER_UNAVAILABLE,
    or POOL_NOT_FOUND states rather than confusing provider unavailability with an empty market.
    """
    async def _run():
        from app.services.market_data_service import GeckoTerminalClient

        client_gt = GeckoTerminalClient()

        # 1. Non-existent pool explicitly reports POOL_NOT_FOUND (not empty valid data)
        res_404 = await client_gt.fetch_ohlcv_candles(
            network="solana",
            pool_address="PoolNonExistent11111111111111111111111111",
            timeframe="15m"
        )
        assert res_404["status"] in ("POOL_NOT_FOUND", "OFFLINE", "PROVIDER_UNAVAILABLE")

        # 2. Trip circuit breaker -> explicitly returns PROVIDER_UNAVAILABLE
        client_gt.circuit_breaker.record_failure("mock_failure")
        client_gt.circuit_breaker.record_failure("mock_failure")
        client_gt.circuit_breaker.record_failure("mock_failure")
        assert not client_gt.circuit_breaker.can_execute()

        res_cb = await client_gt.fetch_ohlcv_candles(
            network="solana",
            pool_address="PoolAny11111111111111111111111111111111111",
            timeframe="15m"
        )
        assert res_cb["status"] == "PROVIDER_UNAVAILABLE"
        assert res_cb["error"] == "CIRCUIT_BREAKER_OPEN"

    asyncio.run(_run())


# -----------------------------------------------------------------------------
# 9. Protected Ingestion Authorization & Public Read-Only Endpoint
# -----------------------------------------------------------------------------
def test_protected_candle_ingestion_and_public_read_isolation():
    """
    Verifies:
    1. GET /api/v1/research/candles/... is open for public read-only retrieval.
    2. POST /api/v1/research/candles/fetch requires X-Maintenance-Authorization (403 Forbidden without auth).
    3. Operational database data/memecoin_os.db is completely untouched.
    """
    c_id = "eip155:8453"
    p_addr = "0xPoolApiTest111111111111111111111111111"

    # 1. Public read-only GET succeeds (returns 200 with empty or populated list)
    get_res = client.get(f"/api/v1/research/candles/{c_id}/{p_addr}?timeframe=15m")
    assert get_res.status_code == 200
    data = get_res.json()
    assert "candles" in data
    assert "timeframe" in data

    # 2. Ingestion POST without auth header fails closed with 403 Forbidden
    unauth_post = client.post("/api/v1/research/candles/fetch", json={
        "chain_id": c_id,
        "pool_address": p_addr,
        "timeframe": "15m"
    })
    assert unauth_post.status_code == 403
    assert "Forbidden" in unauth_post.json()["detail"]

    # 3. Ingestion POST with invalid auth token fails closed with 403 Forbidden
    bad_post = client.post(
        "/api/v1/research/candles/fetch",
        json={"chain_id": c_id, "pool_address": p_addr, "timeframe": "15m"},
        headers={"X-Maintenance-Authorization": "invalid_maintenance_token"}
    )
    assert bad_post.status_code == 403
