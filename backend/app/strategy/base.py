"""
P10.2-G Strategy SDK Contract and Base Types.
Defines versioned, provider-neutral interfaces for strategy plugins.
Strategies propose signals/intents only and NEVER execute orders or alter Core state.
"""

from enum import Enum
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Any, Tuple
from decimal import Decimal
from datetime import datetime, timezone
from pydantic import BaseModel, Field


class StrategyAction(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"
    INVALID = "INVALID"


class StrategyLifecycle(str, Enum):
    DISCOVERED = "DISCOVERED"
    VALIDATED = "VALIDATED"
    ENABLED = "ENABLED"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    DISABLED = "DISABLED"
    RETIRED = "RETIRED"


class StrategyMode(str, Enum):
    RESEARCH = "RESEARCH"
    BACKTEST = "BACKTEST"
    PAPER = "PAPER"


class StrategyMetadata(BaseModel):
    strategy_id: str = Field(..., description="Unique strategy identifier, e.g. momentum_breakout")
    version: str = Field(..., description="Semantic version string, e.g. 1.0.0")
    name: str = Field(..., description="Display name for dashboard and reports")
    description: str = Field(default="", description="Detailed human-readable strategy rationale")
    author: str = Field(default="SYSTEM", description="Strategy author or provider")
    supported_chains: List[str] = Field(..., description="List of supported CAIP-2 chain identifiers")
    supported_asset_types: List[str] = Field(default=["MEMECOIN", "TOKEN"], description="Supported asset classes")
    required_market_fields: List[str] = Field(default=["price_usd", "liquidity_usd"], description="Mandatory market snapshot attributes")
    required_forensic_fields: List[str] = Field(default=[], description="Mandatory security forensic scan attributes")
    config_schema: Dict[str, Any] = Field(default_factory=dict, description="JSON Schema for strategy parameter validation")
    lookback_window_seconds: int = Field(default=3600, ge=60, le=86400 * 7, description="Required historical market data depth")
    supported_modes: List[str] = Field(default=["RESEARCH", "BACKTEST", "PAPER"], description="Allowed execution modes")
    timeout_seconds: float = Field(default=5.0, ge=0.5, le=30.0, description="Evaluation execution timeout ceiling")
    lifecycle_status: StrategyLifecycle = Field(default=StrategyLifecycle.DISCOVERED, description="Current lifecycle state")


class StrategyConfig(BaseModel):
    config_id: Optional[str] = None
    strategy_id: str
    version: str
    chain_id: Optional[str] = None
    parameters: Dict[str, Any] = Field(default_factory=dict)
    risk_preferences: Dict[str, Any] = Field(default_factory=dict, description="Strategy-specific preferences; cannot override Core risk limits")
    is_active: bool = True


class StrategyInputContext(BaseModel):
    """
    Read-only input context provided to a strategy during evaluation.
    Contains verified Core market data, historical lookback snapshots, and forensic evidence.
    Does NOT contain signing keys, database handles, or ledger access.
    """
    chain_id: str
    asset_id: str
    pool_id: Optional[str] = None
    mode: StrategyMode = StrategyMode.RESEARCH
    account_id: Optional[str] = None
    current_market_data: Dict[str, Any] = Field(default_factory=dict)
    historical_snapshots: List[Dict[str, Any]] = Field(default_factory=list)
    forensic_data: Dict[str, Any] = Field(default_factory=dict)
    desk_context: Dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    model_config = {"frozen": True, "arbitrary_types_allowed": True}

    def to_deeply_immutable(self) -> "StrategyInputContext":
        """
        Creates a deeply frozen instance of this context where all nested dicts are
        wrapped in MappingProxyType and lists in tuples, preventing in-place mutation by strategy code.
        """
        from types import MappingProxyType

        def _freeze(val: Any) -> Any:
            if isinstance(val, dict):
                return MappingProxyType({k: _freeze(v) for k, v in val.items()})
            elif isinstance(val, (list, tuple)):
                return tuple(_freeze(v) for v in val)
            return val

        return StrategyInputContext.model_construct(
            chain_id=self.chain_id,
            asset_id=self.asset_id,
            pool_id=self.pool_id,
            mode=self.mode,
            account_id=self.account_id,
            current_market_data=_freeze(self.current_market_data),
            historical_snapshots=_freeze(self.historical_snapshots),
            forensic_data=_freeze(self.forensic_data),
            desk_context=_freeze(self.desk_context),
            timestamp=self.timestamp
        )


class StrategySignal(BaseModel):
    """
    Typed, versioned output contract of a strategy evaluation.
    This is an analytical proposal ONLY and not an executable transaction or order.
    """
    signal_id: str
    run_id: str
    strategy_id: str
    strategy_version: str
    chain_id: str
    asset_id: str
    pool_id: Optional[str] = None
    action: StrategyAction
    confidence: float = Field(..., ge=0.0, le=1.0, description="Confidence score from 0.0 to 1.0")
    entry_reference_price_native: Optional[Decimal] = None
    entry_reference_price_usd: Optional[Decimal] = None
    price_source: str = "CORE_MARKET_SNAPSHOT"
    suggested_size_raw: Optional[Decimal] = Field(None, description="Suggested position size in exact raw atomic integer units")
    suggested_size_human: Optional[Decimal] = Field(None, description="Suggested position size in human normalized token units")
    stop_loss_pct: Optional[float] = None
    take_profit_pct: Optional[float] = None
    reasons: List[str] = Field(default_factory=list, description="Machine-readable reason codes")
    human_explanation: str = Field(default="", description="Human-readable explanation of signal rationale")
    data_provenance: Dict[str, Any] = Field(default_factory=dict, description="Metadata trace of input sources and freshness")
    input_dataset_hash: str = Field(default="", description="Deterministic SHA-256 hash of input context for replay validation")
    input_timestamp: datetime
    expires_at: datetime
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class BaseStrategy(ABC):
    """
    Abstract Base Class for all ScreenerXNews strategy plugins.
    Strategies must be pure evaluation functions producing signals from input contexts.
    """

    @abstractmethod
    def get_metadata(self) -> StrategyMetadata:
        """Returns static capability metadata, lifecycle status, and config schema."""
        pass

    @abstractmethod
    def validate_config(self, config: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
        """Validates strategy configuration against parameter rules."""
        pass

    @abstractmethod
    def evaluate(self, context: StrategyInputContext, config: Dict[str, Any]) -> StrategySignal:
        """
        Evaluates input context and returns a proposed trade signal.
        Must NEVER execute orders, access external networks, or call execution APIs directly.
        """
        pass
