"""
P1.5-A Candle Integrity & Gap Detection Engine.
Performs deterministic mathematical, temporal, and sequence audits on OHLCV series.
Never fabricates synthetic candles, explicitly identifies missing intervals,
verifies candle closure using end timestamps, and preserves raw ordering defect evidence.
"""

from typing import List, Dict, Any, Tuple, Optional
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone, timedelta

from app.schemas.ohlcv import (
    Timeframe,
    CandleBarStatus,
    CandleIntegrityStatus,
    CandleGapInterval,
    CandleOrderingIssue,
    CandleSeriesIntegrityReportDTO,
    OHLCVCandleDTO
)


class CandleIntegrityValidator:
    """
    Independent, strategy-agnostic mathematical validator for candlestick data.
    """

    @staticmethod
    def validate_single_candle_bounds(
        open_price: Decimal,
        high_price: Decimal,
        low_price: Decimal,
        close_price: Decimal,
        volume: Decimal
    ) -> Tuple[bool, Optional[str]]:
        """
        Validates mathematical OHLC bounds and non-negativity.
        Returns (is_valid, error_reason).
        """
        if low_price < Decimal("0"):
            return False, f"Low price ({low_price}) cannot be negative"
        if open_price < Decimal("0") or close_price < Decimal("0"):
            return False, "Open and close prices must be non-negative"
        if volume < Decimal("0"):
            return False, f"Volume ({volume}) cannot be negative"
        if high_price < open_price:
            return False, f"High price ({high_price}) cannot be less than open price ({open_price})"
        if high_price < close_price:
            return False, f"High price ({high_price}) cannot be less than close price ({close_price})"
        if low_price > open_price:
            return False, f"Low price ({low_price}) cannot be greater than open price ({open_price})"
        if low_price > close_price:
            return False, f"Low price ({low_price}) cannot be greater than close price ({close_price})"
        if high_price < low_price:
            return False, f"High price ({high_price}) cannot be less than low price ({low_price})"
        return True, None

    @classmethod
    def evaluate_series_integrity(
        cls,
        raw_candles: List[Dict[str, Any]],
        chain_id: str,
        pool_address: str,
        timeframe: Timeframe,
        min_bars_required: Optional[int] = None,
        reference_now: Optional[datetime] = None
    ) -> Tuple[List[OHLCVCandleDTO], CandleSeriesIntegrityReportDTO]:
        """
        Evaluates a raw sequence of candles without silently sorting first.
        Detects:
        1. Invalid mathematical bounds.
        2. Sequence defects in original input order (non-monotonic or duplicates).
        3. End-timestamp closure status.
        4. Discrete missing temporal gaps.
        5. Strategy-dependent insufficient history.
        """
        now = reference_now or datetime.now(timezone.utc)
        step_seconds = timeframe.seconds

        valid_candles: List[OHLCVCandleDTO] = []
        ordering_issues: List[CandleOrderingIssue] = []
        rejection_reasons: List[str] = []

        total_raw = len(raw_candles)
        rejected_bounds = 0
        duplicate_count = 0
        incomplete_latest_bar = False

        # Phase 1: Inspect raw sequence ordering and mathematical validity
        prev_ts: Optional[datetime] = None
        seen_timestamps = set()

        for idx, item in enumerate(raw_candles):
            # Parse timestamp
            ts_val = item.get("timestamp_utc") or item.get("timestamp")
            if isinstance(ts_val, (int, float)):
                ts_dt = datetime.fromtimestamp(ts_val, tz=timezone.utc)
            elif isinstance(ts_val, str):
                ts_dt = datetime.fromisoformat(ts_val.replace("Z", "+00:00"))
            elif isinstance(ts_val, datetime):
                ts_dt = ts_val if ts_val.tzinfo else ts_val.replace(tzinfo=timezone.utc)
            else:
                rejected_bounds += 1
                rejection_reasons.append(f"Row {idx}: Missing or invalid timestamp '{ts_val}'")
                continue

            ts_iso = ts_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

            # Check sequence ordering in original input
            if prev_ts is not None:
                if ts_dt < prev_ts:
                    ordering_issues.append(CandleOrderingIssue(
                        index=idx,
                        current_timestamp_utc=ts_iso,
                        previous_timestamp_utc=prev_ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        issue_type="NON_MONOTONIC"
                    ))
                elif ts_dt == prev_ts:
                    duplicate_count += 1
                    ordering_issues.append(CandleOrderingIssue(
                        index=idx,
                        current_timestamp_utc=ts_iso,
                        previous_timestamp_utc=prev_ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        issue_type="DUPLICATE"
                    ))
            prev_ts = ts_dt

            # Mathematical bounds check
            try:
                op = Decimal(str(item.get("open_price", item.get("open", "0"))))
                hp = Decimal(str(item.get("high_price", item.get("high", "0"))))
                lp = Decimal(str(item.get("low_price", item.get("low", "0"))))
                cp = Decimal(str(item.get("close_price", item.get("close", "0"))))
                vol = Decimal(str(item.get("volume", "0")))
            except (InvalidOperation, TypeError, ValueError) as err:
                rejected_bounds += 1
                rejection_reasons.append(f"Row {idx} ({ts_iso}): Numeric parse error: {err}")
                continue

            is_valid, err_msg = cls.validate_single_candle_bounds(op, hp, lp, cp, vol)
            if not is_valid:
                rejected_bounds += 1
                rejection_reasons.append(f"Row {idx} ({ts_iso}): Bounds violation: {err_msg}")
                continue

            # End timestamp closure check: t_end = t_start + timeframe_seconds
            end_dt = ts_dt + timedelta(seconds=step_seconds)
            end_iso = end_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

            # A candle is incomplete if the reference time is before its scheduled close
            bar_status = CandleBarStatus.CLOSED
            if now < end_dt:
                bar_status = CandleBarStatus.INCOMPLETE
                incomplete_latest_bar = True

            candle_dto = OHLCVCandleDTO(
                chain_id=chain_id,
                pool_address=pool_address,
                base_token_address=item.get("base_token_address"),
                quote_token_address=item.get("quote_token_address"),
                timeframe=timeframe,
                timestamp_utc=ts_iso,
                timestamp_unix=int(ts_dt.timestamp()),
                end_timestamp_utc=end_iso,
                open_price=op,
                high_price=hp,
                low_price=lp,
                close_price=cp,
                volume=vol,
                price_currency=item.get("price_currency", "USD"),
                volume_currency=item.get("volume_currency", "USD"),
                bar_status=bar_status,
                provider=item.get("provider", "geckoterminal"),
                provider_version=item.get("provider_version", "v2_20230302"),
                revision_count=int(item.get("revision_count", 0))
            )
            valid_candles.append(candle_dto)

        # Phase 2: Deduplicate and sort valid candles chronologically for gap analysis
        unique_by_ts: Dict[int, OHLCVCandleDTO] = {}
        conflicting_revisions = 0
        for c in valid_candles:
            if c.timestamp_unix in unique_by_ts:
                existing = unique_by_ts[c.timestamp_unix]
                # Compare OHLCV values to determine if revision is conflicting
                is_identical = (
                    existing.open_price == c.open_price and
                    existing.high_price == c.high_price and
                    existing.low_price == c.low_price and
                    existing.close_price == c.close_price and
                    existing.volume == c.volume
                )
                if not is_identical:
                    conflicting_revisions += 1
            unique_by_ts[c.timestamp_unix] = c

        sorted_valid = sorted(unique_by_ts.values(), key=lambda x: x.timestamp_unix)

        # Phase 3: Discrete Temporal Gap Detection
        gap_intervals: List[CandleGapInterval] = []
        total_missing = 0

        for i in range(len(sorted_valid) - 1):
            curr_c = sorted_valid[i]
            next_c = sorted_valid[i + 1]
            delta_sec = next_c.timestamp_unix - curr_c.timestamp_unix

            if delta_sec > step_seconds:
                # Interval gap detected
                missing_bars = int(round(delta_sec / step_seconds)) - 1
                if missing_bars > 0:
                    gap_start_dt = datetime.fromtimestamp(curr_c.timestamp_unix + step_seconds, tz=timezone.utc)
                    gap_end_dt = datetime.fromtimestamp(next_c.timestamp_unix, tz=timezone.utc)
                    gap_intervals.append(CandleGapInterval(
                        start_timestamp_utc=gap_start_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        end_timestamp_utc=gap_end_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        missing_bars_count=missing_bars,
                        duration_seconds=delta_sec - step_seconds
                    ))
                    total_missing += missing_bars

        # Phase 4: Strategy-dependent history sufficiency evaluation
        # Closed bars only count towards usable strategy history
        closed_bars_count = sum(1 for c in sorted_valid if c.bar_status == CandleBarStatus.CLOSED)
        is_sufficient = True
        if min_bars_required is not None:
            is_sufficient = (closed_bars_count >= min_bars_required)

        # Phase 5: Determine overall integrity status
        if rejected_bounds > 0:
            integrity_status = CandleIntegrityStatus.CORRUPTED_BOUNDS
        elif len(ordering_issues) > 0:
            integrity_status = CandleIntegrityStatus.ORDERING_DEFECTS
        elif len(gap_intervals) > 0:
            integrity_status = CandleIntegrityStatus.GAPS_DETECTED
        elif not is_sufficient:
            integrity_status = CandleIntegrityStatus.INSUFFICIENT_HISTORY
        else:
            integrity_status = CandleIntegrityStatus.CLEAN

        report = CandleSeriesIntegrityReportDTO(
            chain_id=chain_id,
            pool_address=pool_address,
            timeframe=timeframe,
            total_raw_candles=total_raw,
            valid_candles_count=len(sorted_valid),
            rejected_bounds_count=rejected_bounds,
            duplicate_candles_count=duplicate_count,
            conflicting_revisions_count=conflicting_revisions,
            ordering_defects_count=len(ordering_issues),
            gaps_count=len(gap_intervals),
            total_missing_bars=total_missing,
            incomplete_latest_bar=incomplete_latest_bar,
            bars_available=closed_bars_count,
            bars_required=min_bars_required,
            is_history_sufficient=is_sufficient,
            integrity_status=integrity_status,
            gap_intervals=gap_intervals,
            ordering_issues=ordering_issues,
            rejection_reasons=rejection_reasons
        )

        return sorted_valid, report


candle_integrity_validator = CandleIntegrityValidator()
