"""
P1.5-A OHLCV Data Contracts & Integrity Schemas.
Defines versioned, provider-neutral models for multi-timeframe candles,
gap detection intervals, ordering defect records, and series integrity reports.
"""

from enum import Enum
from typing import List, Optional, Dict, Any
from decimal import Decimal
from datetime import datetime, timezone
from pydantic import BaseModel, Field, model_validator


class Timeframe(str, Enum):
    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    H1 = "1h"
    H4 = "4h"
    H12 = "12h"
    D1 = "1d"

    @property
    def seconds(self) -> int:
        mapping = {
            Timeframe.M1: 60,
            Timeframe.M5: 300,
            Timeframe.M15: 900,
            Timeframe.H1: 3600,
            Timeframe.H4: 14400,
            Timeframe.H12: 43200,
            Timeframe.D1: 86400,
        }
        return mapping[self]

    @property
    def gecko_timeframe(self) -> str:
        if self in (Timeframe.M1, Timeframe.M5, Timeframe.M15):
            return "minute"
        elif self in (Timeframe.H1, Timeframe.H4, Timeframe.H12):
            return "hour"
        return "day"

    @property
    def gecko_aggregate(self) -> int:
        if self == Timeframe.M1:
            return 1
        elif self == Timeframe.M5:
            return 5
        elif self == Timeframe.M15:
            return 15
        elif self == Timeframe.H1:
            return 1
        elif self == Timeframe.H4:
            return 4
        elif self == Timeframe.H12:
            return 12
        return 1


class PriceCurrency(str, Enum):
    USD = "USD"
    TOKEN = "TOKEN"


class VolumeCurrency(str, Enum):
    USD = "USD"
    TOKEN = "TOKEN"


class CandleBarStatus(str, Enum):
    CLOSED = "CLOSED"
    INCOMPLETE = "INCOMPLETE"


class CandleRevisionAction(str, Enum):
    NEW_INSERT = "NEW_INSERT"
    IDENTICAL_DUPLICATE = "IDENTICAL_DUPLICATE"
    CONFLICTING_UPDATE = "CONFLICTING_UPDATE"
    REJECTED_BOUNDS = "REJECTED_BOUNDS"


class CandleIntegrityStatus(str, Enum):
    CLEAN = "CLEAN"
    GAPS_DETECTED = "GAPS_DETECTED"
    ORDERING_DEFECTS = "ORDERING_DEFECTS"
    CORRUPTED_BOUNDS = "CORRUPTED_BOUNDS"
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"


class OHLCVCandleDTO(BaseModel):
    """
    Standardized, pool-specific historical OHLCV bar.
    Contains explicit price and volume currencies, base/quote token addresses,
    and provider metadata to prevent identity collisions.
    """
    chain_id: str = Field(..., description="CAIP-2 chain identifier")
    pool_address: str = Field(..., description="Canonical liquidity pool address")
    base_token_address: Optional[str] = Field(None, description="Base asset address")
    quote_token_address: Optional[str] = Field(None, description="Quote asset address")
    timeframe: Timeframe = Field(..., description="Candle interval resolution")
    timestamp_utc: str = Field(..., description="Bar opening timestamp in ISO 8601 UTC")
    timestamp_unix: int = Field(..., description="Bar opening Unix timestamp in seconds")
    end_timestamp_utc: str = Field(..., description="Bar closing timestamp in ISO 8601 UTC")
    open_price: Decimal = Field(..., description="Opening price")
    high_price: Decimal = Field(..., description="Highest price during bar interval")
    low_price: Decimal = Field(..., description="Lowest price during bar interval")
    close_price: Decimal = Field(..., description="Closing price")
    volume: Decimal = Field(..., ge=Decimal("0"), description="Trading volume during bar interval")
    price_currency: PriceCurrency = Field(default=PriceCurrency.USD, description="Price unit denomination")
    volume_currency: VolumeCurrency = Field(default=VolumeCurrency.USD, description="Volume unit denomination")
    bar_status: CandleBarStatus = Field(default=CandleBarStatus.CLOSED, description="CLOSED for finalized bars, INCOMPLETE for active current bar")
    provider: str = Field(default="geckoterminal", description="Data provider identifier")
    provider_version: str = Field(default="v2_20230302", description="API version of provider response")
    revision_count: int = Field(default=0, ge=0, description="Number of conflicting provider revisions applied")

    @model_validator(mode="after")
    def validate_ohlc_relationships(self) -> "OHLCVCandleDTO":
        if self.high_price < self.open_price:
            raise ValueError(f"High price ({self.high_price}) cannot be less than open price ({self.open_price})")
        if self.high_price < self.close_price:
            raise ValueError(f"High price ({self.high_price}) cannot be less than close price ({self.close_price})")
        if self.low_price > self.open_price:
            raise ValueError(f"Low price ({self.low_price}) cannot be greater than open price ({self.open_price})")
        if self.low_price > self.close_price:
            raise ValueError(f"Low price ({self.low_price}) cannot be greater than close price ({self.close_price})")
        if self.low_price < Decimal("0"):
            raise ValueError(f"Low price ({self.low_price}) cannot be negative")
        return self


class CandleGapInterval(BaseModel):
    """Represents a discrete temporal missing interval detected in a series."""
    start_timestamp_utc: str
    end_timestamp_utc: str
    missing_bars_count: int
    duration_seconds: int


class CandleOrderingIssue(BaseModel):
    """Represents a non-monotonic sequence defect in the original raw input."""
    index: int
    current_timestamp_utc: str
    previous_timestamp_utc: str
    issue_type: str = Field(..., description="'NON_MONOTONIC' or 'DUPLICATE'")


class CandleSeriesIntegrityReportDTO(BaseModel):
    """
    Comprehensive mathematical and temporal health audit of an OHLCV series.
    Surfaces gaps, bounds violations, ordering defects, and insufficient history.
    """
    chain_id: str
    pool_address: str
    timeframe: Timeframe
    total_raw_candles: int
    valid_candles_count: int
    rejected_bounds_count: int
    duplicate_candles_count: int
    conflicting_revisions_count: int
    ordering_defects_count: int
    gaps_count: int
    total_missing_bars: int
    incomplete_latest_bar: bool
    bars_available: int
    bars_required: Optional[int] = None
    is_history_sufficient: bool
    integrity_status: CandleIntegrityStatus
    gap_intervals: List[CandleGapInterval] = Field(default_factory=list)
    ordering_issues: List[CandleOrderingIssue] = Field(default_factory=list)
    rejection_reasons: List[str] = Field(default_factory=list)


class CandleListResponse(BaseModel):
    """Response DTO for public read-only candle retrieval."""
    chain_id: str
    pool_address: str
    timeframe: Timeframe
    total_candles: int
    candles: List[OHLCVCandleDTO]
    integrity_report: Optional[CandleSeriesIntegrityReportDTO] = None
    provider_status: str = Field(default="OPERATIONAL", description="'OPERATIONAL', 'CACHED', 'PROVIDER_UNAVAILABLE', or 'OFFLINE'")


class ProtectedCandleFetchRequest(BaseModel):
    """Request DTO for protected candle ingestion from provider."""
    chain_id: str
    pool_address: str
    timeframe: Timeframe = Timeframe.M15
    limit: int = Field(default=100, ge=1, le=1000)
    before_timestamp: Optional[int] = Field(None, description="Unix timestamp for backward pagination")
    base_token_address: Optional[str] = None
    quote_token_address: Optional[str] = None


class ProtectedCandleFetchResponse(BaseModel):
    """Summary DTO for protected candle ingestion."""
    chain_id: str
    pool_address: str
    timeframe: Timeframe
    provider: str
    fetched_count: int
    new_inserts: int
    identical_duplicates: int
    conflicting_updates: int
    rejected_bounds: int
    status: str
