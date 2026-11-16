"""
StrategySDK package.
Provides safe, declarative, strategy-agnostic modeling, feature extraction,
deterministic evaluation, and versioned lifecycle management.
"""

from app.strategy_sdk.schemas import (
    STRATEGY_SDK_VERSION,
    STRATEGY_EVALUATOR_VERSION,
    ConditionOperator,
    LogicalOperator,
    EvaluationStatus,
    ConditionLeafDTO,
    ConditionGroupDTO,
    StrategyMetadataDTO,
    DeclarativeStrategyDTO,
    ConditionEvaluationDetailDTO,
    StrategyEvaluationResponseDTO,
    StrategyValidationResultDTO
)
from app.strategy_sdk.registry import feature_registry
from app.strategy_sdk.evaluator import strategy_evaluator
from app.strategy_sdk.manager import strategy_manager

__all__ = [
    "STRATEGY_SDK_VERSION",
    "STRATEGY_EVALUATOR_VERSION",
    "ConditionOperator",
    "LogicalOperator",
    "EvaluationStatus",
    "ConditionLeafDTO",
    "ConditionGroupDTO",
    "StrategyMetadataDTO",
    "DeclarativeStrategyDTO",
    "ConditionEvaluationDetailDTO",
    "StrategyEvaluationResponseDTO",
    "StrategyValidationResultDTO",
    "feature_registry",
    "strategy_evaluator",
    "strategy_manager"
]
