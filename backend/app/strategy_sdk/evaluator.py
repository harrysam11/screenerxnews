"""
P1.5-C Safe Strategy Evaluator & Sandboxing Engine.
Interprets pure declarative JSON strategy definitions.
Zero code execution, zero eval/exec, zero dynamic imports, strictly sandboxed.
Enforces recursion depth, condition count, and computational bounds.
Provides deterministic, explainable evaluation results.
"""

import json
from typing import List, Dict, Any, Tuple, Optional, Union
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone

from app.schemas.ohlcv import OHLCVCandleDTO, CandleBarStatus, Timeframe
from app.schemas.indicators import IndicatorQualityStatus
from app.strategy_sdk.schemas import (
    STRATEGY_SDK_VERSION,
    STRATEGY_EVALUATOR_VERSION,
    MAX_CONFIG_SIZE_BYTES,
    MAX_EXPRESSION_DEPTH,
    MAX_TOTAL_CONDITIONS,
    ConditionOperator,
    LogicalOperator,
    EvaluationStatus,
    ConditionLeafDTO,
    ConditionGroupDTO,
    DeclarativeStrategyDTO,
    ConditionEvaluationDetailDTO,
    StrategyEvaluationResponseDTO,
    StrategyValidationResultDTO
)
from app.strategy_sdk.registry import feature_registry


FORBIDDEN_SECURITY_STRINGS = [
    "__import__", "eval(", "exec(", "os.", "sys.", "subprocess",
    "open(", "getattr(", "setattr(", "globals(", "locals(",
    "__class__", "__subclasses__", "__bases__", "__globals__"
]


class SafeStrategyEvaluator:
    """
    Deterministic declarative strategy validator and evaluator.
    Strictly isolated: does not access external network, filesystem, or database.
    """

    # -------------------------------------------------------------------------
    # Validation & Security Sandboxing
    # -------------------------------------------------------------------------
    @classmethod
    def validate_strategy_safety(cls, strategy: DeclarativeStrategyDTO) -> StrategyValidationResultDTO:
        """
        Validates declarative configuration for safety, complexity, and feature availability.
        Rejects arbitrary injection, recursion, oversized payloads, and invalid operators.
        """
        errors: List[str] = []
        warnings: List[str] = []

        # 1. Payload size check
        raw_json = strategy.model_dump_json()
        if len(raw_json.encode("utf-8")) > MAX_CONFIG_SIZE_BYTES:
            errors.append(f"Strategy payload size ({len(raw_json)} bytes) exceeds limit of {MAX_CONFIG_SIZE_BYTES} bytes")

        # 2. String security inspection
        raw_lower = raw_json.lower()
        for s in FORBIDDEN_SECURITY_STRINGS:
            if s.lower() in raw_lower:
                errors.append(f"Security violation: Forbidden pattern '{s}' detected in declarative specification")

        # 3. Structural limits & feature checks
        features_required: List[str] = []
        total_conditions = 0
        max_depth = 0

        def inspect_node(node: Union[ConditionLeafDTO, ConditionGroupDTO], current_depth: int, path: str):
            nonlocal total_conditions, max_depth
            max_depth = max(max_depth, current_depth)

            if current_depth > MAX_EXPRESSION_DEPTH:
                errors.append(f"Expression depth exceeded at '{path}': max depth is {MAX_EXPRESSION_DEPTH}")
                return

            if isinstance(node, ConditionLeafDTO):
                total_conditions += 1
                if total_conditions > MAX_TOTAL_CONDITIONS:
                    errors.append(f"Total condition count exceeded: max allowed is {MAX_TOTAL_CONDITIONS}")
                    return

                # Validate feature in registry
                feat = feature_registry.get_feature(node.feature)
                if not feat:
                    errors.append(f"Unsupported feature '{node.feature}' at '{path}'")
                else:
                    if node.feature not in features_required:
                        features_required.append(node.feature)
                    # Validate operator compatibility
                    if node.operator not in feat.supported_operators:
                        errors.append(
                            f"Operator '{node.operator.value}' is incompatible with {feat.data_type} feature '{node.feature}' at '{path}'"
                        )

                # Validate target feature if specified
                if node.target_feature:
                    t_feat = feature_registry.get_feature(node.target_feature)
                    if not t_feat:
                        errors.append(f"Unsupported target feature '{node.target_feature}' at '{path}'")
                    elif feat and t_feat.data_type != feat.data_type:
                        errors.append(
                            f"Type mismatch at '{path}': feature '{node.feature}' ({feat.data_type}) vs target '{node.target_feature}' ({t_feat.data_type})"
                        )

            elif isinstance(node, ConditionGroupDTO):
                for idx, child in enumerate(node.conditions):
                    inspect_node(child, current_depth + 1, f"{path}.conditions[{idx}]")

        # Inspect entry
        inspect_node(strategy.entry, 1, "entry")
        # Inspect exit if present
        if strategy.exit:
            inspect_node(strategy.exit, 1, "exit")

        return StrategyValidationResultDTO(
            is_valid=(len(errors) == 0),
            strategy_id=strategy.strategy.id,
            version=strategy.strategy.version,
            total_conditions=total_conditions,
            expression_depth=max_depth,
            features_required=features_required,
            errors=errors,
            warnings=warnings
        )

    # -------------------------------------------------------------------------
    # Evaluation Execution
    # -------------------------------------------------------------------------
    @classmethod
    def evaluate_strategy(
        cls,
        strategy: DeclarativeStrategyDTO,
        candles: List[OHLCVCandleDTO],
        bar_index: int = -1,
        chain_id: Optional[str] = None,
        pool_address: Optional[str] = None,
        timeframe: Optional[Timeframe] = None
    ) -> StrategyEvaluationResponseDTO:
        """
        Deterministically evaluates a strategy against historical candles at bar_index.
        Default bar_index = -1 evaluates latest available closed bar.
        Returns explainable results without generating orders or trade intents.
        """
        # Validate strategy first
        val_result = cls.validate_strategy_safety(strategy)
        if not val_result.is_valid:
            return StrategyEvaluationResponseDTO(
                strategy_id=strategy.strategy.id,
                strategy_version=strategy.strategy.version,
                chain_id=chain_id or (candles[0].chain_id if candles else "unknown"),
                pool_address=pool_address or (candles[0].pool_address if candles else "unknown"),
                timeframe=timeframe or (candles[0].timeframe if candles else Timeframe.M15),
                evaluation_timestamp_utc=datetime.now(timezone.utc).isoformat(),
                evaluation_candle_timestamp_utc="N/A",
                entry_matched=False,
                exit_matched=False,
                overall_status=EvaluationStatus.INVALID_STRATEGY,
                condition_details=[],
                features_used={},
                data_quality_status="INVALID_STRATEGY",
                explanation=f"Strategy validation failed: {'; '.join(val_result.errors)}"
            )

        if not candles:
            return StrategyEvaluationResponseDTO(
                strategy_id=strategy.strategy.id,
                strategy_version=strategy.strategy.version,
                chain_id=chain_id or "unknown",
                pool_address=pool_address or "unknown",
                timeframe=timeframe or Timeframe.M15,
                evaluation_timestamp_utc=datetime.now(timezone.utc).isoformat(),
                evaluation_candle_timestamp_utc="N/A",
                entry_matched=False,
                exit_matched=False,
                overall_status=EvaluationStatus.INSUFFICIENT_DATA,
                condition_details=[],
                features_used={},
                data_quality_status="NO_CANDLES_PROVIDED",
                explanation="No historical candles supplied for evaluation"
            )

        # Resolve candle index
        n = len(candles)
        eval_idx = bar_index if bar_index >= 0 else (n + bar_index)
        if eval_idx < 0 or eval_idx >= n:
            eval_idx = n - 1

        eval_candle = candles[eval_idx]
        eff_chain_id = chain_id or eval_candle.chain_id
        eff_pool = pool_address or eval_candle.pool_address
        eff_tf = timeframe or eval_candle.timeframe

        condition_details: List[ConditionEvaluationDetailDTO] = []
        features_used: Dict[str, Any] = {}
        had_insufficient_data = False

        # Evaluate entry group
        entry_matched = cls._evaluate_group(
            strategy.entry,
            candles,
            eval_idx,
            "entry",
            condition_details,
            features_used
        )

        # Evaluate exit group if present
        exit_matched: Optional[bool] = None
        if strategy.exit:
            exit_matched = cls._evaluate_group(
                strategy.exit,
                candles,
                eval_idx,
                "exit",
                condition_details,
                features_used
            )

        # Check for insufficient data in any condition
        for d in condition_details:
            if d.status == EvaluationStatus.INSUFFICIENT_DATA:
                had_insufficient_data = True
                break

        if had_insufficient_data and not entry_matched:
            overall_status = EvaluationStatus.INSUFFICIENT_DATA
            explanation = "One or more conditions could not be evaluated due to insufficient data or missing history."
        elif entry_matched:
            overall_status = EvaluationStatus.MATCH
            explanation = "Entry trigger criteria satisfied."
        else:
            overall_status = EvaluationStatus.NO_MATCH
            explanation = "Entry trigger criteria not satisfied."

        return StrategyEvaluationResponseDTO(
            strategy_id=strategy.strategy.id,
            strategy_version=strategy.strategy.version,
            chain_id=eff_chain_id,
            pool_address=eff_pool,
            timeframe=eff_tf,
            evaluation_timestamp_utc=datetime.now(timezone.utc).isoformat(),
            evaluation_candle_timestamp_utc=eval_candle.timestamp_utc,
            entry_matched=entry_matched,
            exit_matched=exit_matched,
            overall_status=overall_status,
            condition_details=condition_details,
            features_used=features_used,
            data_quality_status="CLEAN" if not had_insufficient_data else "INSUFFICIENT_HISTORY",
            explanation=explanation
        )

    # -------------------------------------------------------------------------
    # Internal Recursive Node Evaluators
    # -------------------------------------------------------------------------
    @classmethod
    def _evaluate_group(
        cls,
        group: ConditionGroupDTO,
        candles: List[OHLCVCandleDTO],
        bar_idx: int,
        path: str,
        details: List[ConditionEvaluationDetailDTO],
        features_used: Dict[str, Any]
    ) -> bool:
        """Evaluates a logical condition group (AND, OR, NOT)."""
        child_results: List[bool] = []

        for idx, child in enumerate(group.conditions):
            child_path = f"{path}.conditions[{idx}]"
            if isinstance(child, ConditionLeafDTO):
                res = cls._evaluate_leaf(child, candles, bar_idx, child_path, details, features_used)
                child_results.append(res)
            elif isinstance(child, ConditionGroupDTO):
                res = cls._evaluate_group(child, candles, bar_idx, child_path, details, features_used)
                child_results.append(res)

        if group.operator == LogicalOperator.AND:
            return all(child_results)
        elif group.operator == LogicalOperator.OR:
            return any(child_results)
        elif group.operator == LogicalOperator.NOT:
            return not child_results[0]
        return False

    @classmethod
    def _evaluate_leaf(
        cls,
        leaf: ConditionLeafDTO,
        candles: List[OHLCVCandleDTO],
        bar_idx: int,
        path: str,
        details: List[ConditionEvaluationDetailDTO],
        features_used: Dict[str, Any]
    ) -> bool:
        """Evaluates an atomic condition leaf comparing actual feature value to target."""
        feat_id = leaf.feature.lower()
        actual_val, quality = feature_registry.extract_value(
            feat_id, candles, bar_index=bar_idx, params=leaf.feature_params
        )

        features_used[feat_id] = str(actual_val) if actual_val is not None else None

        # Fail-closed if actual value is unavailable
        if actual_val is None or quality in (IndicatorQualityStatus.INSUFFICIENT_HISTORY, IndicatorQualityStatus.UNAVAILABLE):
            details.append(ConditionEvaluationDetailDTO(
                condition_path=path,
                feature=leaf.feature,
                operator=leaf.operator.value,
                expected_value=str(leaf.value) if leaf.value is not None else leaf.target_feature,
                actual_value=None,
                is_matched=False,
                status=EvaluationStatus.INSUFFICIENT_DATA,
                explanation=f"Feature '{leaf.feature}' unavailable ({quality.value})"
            ))
            return False

        # Resolve expected comparison target
        expected_val: Any = None
        if leaf.target_feature:
            t_feat_id = leaf.target_feature.lower()
            t_val, t_qual = feature_registry.extract_value(
                t_feat_id, candles, bar_index=bar_idx, params=leaf.target_feature_params
            )
            features_used[t_feat_id] = str(t_val) if t_val is not None else None
            if t_val is None:
                details.append(ConditionEvaluationDetailDTO(
                    condition_path=path,
                    feature=leaf.feature,
                    operator=leaf.operator.value,
                    expected_value=f"target_feature:{leaf.target_feature}",
                    actual_value=str(actual_val),
                    is_matched=False,
                    status=EvaluationStatus.INSUFFICIENT_DATA,
                    explanation=f"Target feature '{leaf.target_feature}' unavailable ({t_qual.value})"
                ))
                return False
            expected_val = t_val
        elif leaf.operator == ConditionOperator.BETWEEN:
            expected_val = (leaf.min_value, leaf.max_value)
        else:
            expected_val = leaf.value

        # Execute comparison
        is_matched, explanation = cls._compare_values(
            leaf, actual_val, expected_val, candles, bar_idx
        )

        details.append(ConditionEvaluationDetailDTO(
            condition_path=path,
            feature=leaf.feature,
            operator=leaf.operator.value,
            expected_value=str(expected_val),
            actual_value=str(actual_val),
            is_matched=is_matched,
            status=EvaluationStatus.MATCH if is_matched else EvaluationStatus.NO_MATCH,
            explanation=explanation
        ))
        return is_matched

    @classmethod
    def _compare_values(
        cls,
        leaf: ConditionLeafDTO,
        actual: Any,
        expected: Any,
        candles: List[OHLCVCandleDTO],
        bar_idx: int
    ) -> Tuple[bool, str]:
        """Performs typed, deterministic comparison between actual and expected."""
        op = leaf.operator

        # Normalize numeric Decimals where applicable
        if isinstance(actual, (Decimal, int, float)) or (isinstance(actual, str) and actual.replace(".", "", 1).isdigit()):
            try:
                dec_actual = Decimal(str(actual))
            except InvalidOperation:
                dec_actual = None
        else:
            dec_actual = None

        if op in (ConditionOperator.EQ, ConditionOperator.NEQ):
            if dec_actual is not None and expected is not None:
                try:
                    dec_expected = Decimal(str(expected))
                    matched = (dec_actual == dec_expected) if op == ConditionOperator.EQ else (dec_actual != dec_expected)
                    return matched, f"{dec_actual} {'==' if op == ConditionOperator.EQ else '!='} {dec_expected}"
                except (InvalidOperation, TypeError):
                    pass
            # String / boolean equality
            matched = (str(actual).lower() == str(expected).lower()) if op == ConditionOperator.EQ else (str(actual).lower() != str(expected).lower())
            return matched, f"'{actual}' {'==' if op == ConditionOperator.EQ else '!='} '{expected}'"

        elif op in (ConditionOperator.GT, ConditionOperator.GTE, ConditionOperator.LT, ConditionOperator.LTE):
            if dec_actual is None:
                return False, f"Cannot perform numerical comparison on non-numeric value: {actual!r}"
            try:
                dec_expected = Decimal(str(expected))
            except (InvalidOperation, TypeError):
                return False, f"Invalid numeric target value: {expected!r}"

            if op == ConditionOperator.GT:
                matched = dec_actual > dec_expected
                return matched, f"{dec_actual} > {dec_expected}"
            elif op == ConditionOperator.GTE:
                matched = dec_actual >= dec_expected
                return matched, f"{dec_actual} >= {dec_expected}"
            elif op == ConditionOperator.LT:
                matched = dec_actual < dec_expected
                return matched, f"{dec_actual} < {dec_expected}"
            elif op == ConditionOperator.LTE:
                matched = dec_actual <= dec_expected
                return matched, f"{dec_actual} <= {dec_expected}"

        elif op == ConditionOperator.BETWEEN:
            if dec_actual is None:
                return False, f"Cannot perform between comparison on non-numeric value: {actual!r}"
            min_val = Decimal(str(leaf.min_value))
            max_val = Decimal(str(leaf.max_value))
            matched = (min_val <= dec_actual <= max_val)
            return matched, f"{min_val} <= {dec_actual} <= {max_val}"

        elif op in (ConditionOperator.CROSSES_ABOVE, ConditionOperator.CROSSES_BELOW):
            # Requires previous bar index
            if bar_idx < 1:
                return False, f"Operator '{op.value}' requires prior bar for crossing comparison"

            prev_actual, prev_qual = feature_registry.extract_value(
                leaf.feature.lower(), candles, bar_index=bar_idx - 1, params=leaf.feature_params
            )
            if prev_actual is None:
                return False, "Prior bar feature value unavailable for crossing check"

            try:
                dec_prev_actual = Decimal(str(prev_actual))
            except InvalidOperation:
                return False, f"Non-numeric prior value: {prev_actual!r}"

            if leaf.target_feature:
                prev_expected, _ = feature_registry.extract_value(
                    leaf.target_feature.lower(), candles, bar_index=bar_idx - 1, params=leaf.target_feature_params
                )
                curr_expected = expected
            else:
                prev_expected = expected
                curr_expected = expected

            try:
                dec_prev_expected = Decimal(str(prev_expected))
                dec_curr_expected = Decimal(str(curr_expected))
            except (InvalidOperation, TypeError):
                return False, "Non-numeric expected values for crossing check"

            if op == ConditionOperator.CROSSES_ABOVE:
                matched = (dec_prev_actual <= dec_prev_expected) and (dec_actual > dec_curr_expected)
                return matched, f"Prev: {dec_prev_actual} <= {dec_prev_expected}, Curr: {dec_actual} > {dec_curr_expected}"
            else:  # CROSSES_BELOW
                matched = (dec_prev_actual >= dec_prev_expected) and (dec_actual < dec_curr_expected)
                return matched, f"Prev: {dec_prev_actual} >= {dec_prev_expected}, Curr: {dec_actual} < {dec_curr_expected}"

        return False, f"Unsupported operator '{op}'"


# Global singleton evaluator
strategy_evaluator = SafeStrategyEvaluator()
