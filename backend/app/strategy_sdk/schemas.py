"""
P1.5-C StrategySDK Data Contracts & Declarative Schemas.
Defines typed JSON-compatible strategy definition contracts, condition operators,
evaluation result explanations, and security guardrail limits.
"""

from enum import Enum
from typing import List, Optional, Dict, Any, Union
from decimal import Decimal
from pydantic import BaseModel, Field, ConfigDict, model_validator
import re

from app.schemas.ohlcv import Timeframe

STRATEGY_SDK_VERSION = "1.0.0"
STRATEGY_EVALUATOR_VERSION = "1.0.0"

# Security guardrail limits
MAX_CONFIG_SIZE_BYTES = 32768  # 32 KB maximum JSON payload
MAX_EXPRESSION_DEPTH = 5
MAX_TOTAL_CONDITIONS = 30
MAX_STRATEGY_ID_LENGTH = 64


class ConditionOperator(str, Enum):
    """Supported comparison and time-series operators."""
    EQ = "eq"
    NEQ = "neq"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    BETWEEN = "between"
    CROSSES_ABOVE = "crosses_above"
    CROSSES_BELOW = "crosses_below"


class LogicalOperator(str, Enum):
    """Supported boolean combinators."""
    AND = "AND"
    OR = "OR"
    NOT = "NOT"


class EvaluationStatus(str, Enum):
    """Outcome states for strategy and condition evaluation."""
    MATCH = "MATCH"
    NO_MATCH = "NO_MATCH"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    INVALID_STRATEGY = "INVALID_STRATEGY"
    UNSUPPORTED_FEATURE = "UNSUPPORTED_FEATURE"
    DATA_QUALITY_FAILURE = "DATA_QUALITY_FAILURE"


# ============================================================================
# Declarative Condition Models
# ============================================================================

class ConditionLeafDTO(BaseModel):
    """
    Atomic condition comparing a market feature against a scalar value or target feature.
    Strictly forbids unknown fields.
    """
    model_config = ConfigDict(extra="forbid")

    feature: str = Field(..., description="Registered feature identifier (e.g. 'rsi', 'rvol', 'close')")
    feature_params: Dict[str, Any] = Field(default_factory=dict, description="Configurable feature parameters (e.g. {'period': 14})")
    operator: ConditionOperator = Field(..., description="Comparison operator")
    value: Optional[Union[Decimal, float, int, str, bool]] = Field(None, description="Static target threshold value")
    target_feature: Optional[str] = Field(None, description="Dynamic comparison target feature (e.g. compare close against sma)")
    target_feature_params: Dict[str, Any] = Field(default_factory=dict, description="Parameters for target feature")
    min_value: Optional[Union[Decimal, float, int]] = Field(None, description="Lower bound for 'between' operator")
    max_value: Optional[Union[Decimal, float, int]] = Field(None, description="Upper bound for 'between' operator")

    @model_validator(mode="after")
    def validate_leaf_structure(self) -> "ConditionLeafDTO":
        if self.operator == ConditionOperator.BETWEEN:
            if self.min_value is None or self.max_value is None:
                raise ValueError("Operator 'between' requires both 'min_value' and 'max_value'")
            if Decimal(str(self.min_value)) > Decimal(str(self.max_value)):
                raise ValueError(f"'min_value' ({self.min_value}) cannot exceed 'max_value' ({self.max_value})")
        else:
            if self.value is None and self.target_feature is None:
                raise ValueError(f"Operator '{self.operator}' requires either 'value' or 'target_feature'")
        return self


class ConditionGroupDTO(BaseModel):
    """
    Logical grouping of conditions (AND, OR, NOT).
    Supports recursive sub-groups up to MAX_EXPRESSION_DEPTH.
    Strictly forbids unknown fields.
    """
    model_config = ConfigDict(extra="forbid")

    operator: LogicalOperator = Field(..., description="'AND', 'OR', or 'NOT'")
    conditions: List[Union[ConditionLeafDTO, "ConditionGroupDTO"]] = Field(
        ..., min_length=1, description="List of child conditions or sub-groups"
    )

    @model_validator(mode="after")
    def validate_group_structure(self) -> "ConditionGroupDTO":
        if self.operator == LogicalOperator.NOT and len(self.conditions) != 1:
            raise ValueError("Logical operator 'NOT' must contain exactly one child condition")
        return self


# Rebuild self-referencing model
ConditionGroupDTO.model_rebuild()


# ============================================================================
# Strategy Definition Models
# ============================================================================

class StrategyMetadataDTO(BaseModel):
    """Metadata and identification for a declarative strategy."""
    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., description="Unique alphanumeric identifier (e.g. 'vol_mom_001')")
    name: str = Field(..., min_length=1, max_length=128, description="Human-readable strategy name")
    description: Optional[str] = Field(None, max_length=512, description="Strategy summary and intent")
    version: str = Field(default="1.0.0", description="Semantic version string")
    supported_chains: List[str] = Field(default_factory=list, description="Supported CAIP-2 chains (empty means all)")
    supported_timeframes: List[Timeframe] = Field(default_factory=list, description="Supported candle resolutions")
    author: Optional[str] = Field(None, max_length=64, description="Strategy creator or owner")

    @model_validator(mode="after")
    def validate_id_format(self) -> "StrategyMetadataDTO":
        if len(self.id) > MAX_STRATEGY_ID_LENGTH:
            raise ValueError(f"Strategy ID exceeds max length of {MAX_STRATEGY_ID_LENGTH}")
        if not re.match(r"^[a-zA-Z0-9_\-]+$", self.id):
            raise ValueError(f"Strategy ID '{self.id}' must be alphanumeric with underscores or hyphens only")
        return self


class DeclarativeStrategyDTO(BaseModel):
    """
    Complete, validated declarative trading strategy specification.
    JSON-compatible, pure configuration, zero executable code.
    """
    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(default="1.0", description="Declarative format version")
    strategy: StrategyMetadataDTO
    entry: ConditionGroupDTO = Field(..., description="Mandatory entry trigger conditions")
    exit: Optional[ConditionGroupDTO] = Field(None, description="Optional exit / close trigger conditions")
    risk_constraints: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Optional declarative risk limits")
    enabled: bool = Field(default=True, description="Whether strategy is active for evaluation")
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


# ============================================================================
# Evaluation & Validation Result Models
# ============================================================================

class ConditionEvaluationDetailDTO(BaseModel):
    """Diagnostic detail explaining how an individual condition evaluated."""
    condition_path: str = Field(..., description="Path in expression tree (e.g. 'entry.conditions[0]')")
    feature: str
    operator: str
    expected_value: Any
    actual_value: Any
    is_matched: bool
    status: EvaluationStatus
    explanation: str


class StrategyEvaluationResponseDTO(BaseModel):
    """
    Comprehensive, explainable response for strategy evaluation.
    Descriptive only; strictly does not execute trades or create orders.
    """
    strategy_id: str
    strategy_version: str
    chain_id: str
    pool_address: str
    timeframe: Timeframe
    evaluation_timestamp_utc: str
    evaluation_candle_timestamp_utc: str
    entry_matched: bool
    exit_matched: Optional[bool] = None
    overall_status: EvaluationStatus
    condition_details: List[ConditionEvaluationDetailDTO] = Field(default_factory=list)
    features_used: Dict[str, Any] = Field(default_factory=dict)
    data_quality_status: str
    sdk_version: str = STRATEGY_SDK_VERSION
    evaluator_version: str = STRATEGY_EVALUATOR_VERSION
    explanation: str


class StrategyValidationResultDTO(BaseModel):
    """Result of dry-run validation of a declarative strategy."""
    is_valid: bool
    strategy_id: Optional[str] = None
    version: Optional[str] = None
    total_conditions: int = 0
    expression_depth: int = 0
    features_required: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


# ============================================================================
# API Request / Response Containers
# ============================================================================

class StrategyCreateRequest(BaseModel):
    """Request payload for registering a new declarative strategy."""
    model_config = ConfigDict(extra="forbid")
    strategy_definition: DeclarativeStrategyDTO
    change_summary: str = Field(default="Initial strategy creation")


class StrategyUpdateRequest(BaseModel):
    """Request payload for updating an existing strategy, creating a new immutable version."""
    model_config = ConfigDict(extra="forbid")
    new_version: str = Field(..., description="New semantic version (e.g. '1.1.0')")
    strategy_definition: DeclarativeStrategyDTO
    change_summary: str = Field(..., min_length=3, description="Description of changes made")


class StrategyStatusUpdateRequest(BaseModel):
    """Request payload for enabling/disabling a strategy."""
    model_config = ConfigDict(extra="forbid")
    enabled: bool


class StrategyVersionSummaryDTO(BaseModel):
    """Audit summary of an immutable strategy version."""
    version_id: str
    strategy_id: str
    version: str
    checksum: str
    created_at: str
    change_summary: Optional[str] = None
    is_active: bool


class StrategyEvaluateRequest(BaseModel):
    """Request payload to evaluate a registered strategy against historical market data."""
    chain_id: str
    pool_address: str
    timeframe: Timeframe = Timeframe.M15
    limit: int = Field(default=100, ge=10, le=1000)
    include_incomplete: bool = Field(default=False, description="Default closed candles only")


class StrategyEvaluateRawRequest(BaseModel):
    """Request payload to evaluate an unpersisted declarative strategy definition."""
    strategy_definition: DeclarativeStrategyDTO
    chain_id: str
    pool_address: str
    timeframe: Timeframe = Timeframe.M15
    limit: int = Field(default=100, ge=10, le=1000)
    include_incomplete: bool = Field(default=False)
