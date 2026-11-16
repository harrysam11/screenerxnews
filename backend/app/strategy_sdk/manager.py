"""
P1.5-C Strategy Lifecycle Manager.
Coordinates persistence, validation, immutable versioning, and evaluation
of declarative strategies using research_history.db.
Operational database is strictly isolated and inaccessible.
"""

import json
import hashlib
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone

from app.schemas.ohlcv import OHLCVCandleDTO, Timeframe
from app.strategy_sdk.schemas import (
    DeclarativeStrategyDTO,
    StrategyVersionSummaryDTO,
    StrategyEvaluationResponseDTO,
    StrategyValidationResultDTO,
    EvaluationStatus
)
from app.strategy_sdk.evaluator import strategy_evaluator
from app.services.research_analytics_service import research_analytics_service


def compute_definition_checksum(strategy: DeclarativeStrategyDTO) -> str:
    """Computes deterministic SHA-256 hash of the canonical JSON strategy definition."""
    canonical_json = json.dumps(strategy.model_dump(mode="json"), sort_keys=True)
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


class StrategyLifecycleManager:
    """
    Manages declarative strategy registration, immutable versioning,
    status toggles, and safe execution audits.
    """

    @property
    def store(self):
        return research_analytics_service.store

    def register_strategy(
        self,
        strategy: DeclarativeStrategyDTO,
        change_summary: str = "Initial creation"
    ) -> DeclarativeStrategyDTO:
        """
        Validates and registers a new declarative strategy.
        Fails if strategy ID already exists.
        """
        # 1. Validate safety & structure
        val_res = strategy_evaluator.validate_strategy_safety(strategy)
        if not val_res.is_valid:
            raise ValueError(f"Strategy validation failed: {'; '.join(val_res.errors)}")

        checksum = compute_definition_checksum(strategy)
        def_json = strategy.model_dump_json()

        self.store.save_declarative_strategy(
            strategy_id=strategy.strategy.id,
            name=strategy.strategy.name,
            description=strategy.strategy.description,
            version=strategy.strategy.version,
            definition_json=def_json,
            checksum=checksum,
            change_summary=change_summary
        )
        return strategy

    def update_strategy(
        self,
        strategy_id: str,
        new_version: str,
        updated_strategy: DeclarativeStrategyDTO,
        change_summary: str
    ) -> DeclarativeStrategyDTO:
        """
        Updates an existing strategy by creating a new immutable version record.
        """
        if updated_strategy.strategy.id != strategy_id:
            raise ValueError(f"Strategy ID in payload ('{updated_strategy.strategy.id}') does not match URL ('{strategy_id}')")

        # Update version in definition
        updated_strategy.strategy.version = new_version
        val_res = strategy_evaluator.validate_strategy_safety(updated_strategy)
        if not val_res.is_valid:
            raise ValueError(f"Strategy validation failed: {'; '.join(val_res.errors)}")

        checksum = compute_definition_checksum(updated_strategy)
        def_json = updated_strategy.model_dump_json()

        self.store.update_declarative_strategy_version(
            strategy_id=strategy_id,
            new_version=new_version,
            definition_json=def_json,
            checksum=checksum,
            change_summary=change_summary
        )
        return updated_strategy

    def get_strategy(
        self,
        strategy_id: str,
        version: Optional[str] = None
    ) -> Optional[DeclarativeStrategyDTO]:
        """Retrieves a strategy by ID and optional version."""
        row = self.store.get_declarative_strategy(strategy_id, version=version)
        if not row:
            return None
        strat = DeclarativeStrategyDTO.model_validate(row["definition"])
        strat.enabled = (row.get("status") == "ENABLED")
        return strat

    def list_strategies(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Lists declarative strategies."""
        return self.store.list_declarative_strategies(limit=limit)

    def set_strategy_status(self, strategy_id: str, enabled: bool) -> bool:
        """Enables or disables strategy evaluation."""
        return self.store.set_declarative_strategy_status(strategy_id, enabled)

    def get_strategy_versions(self, strategy_id: str) -> List[StrategyVersionSummaryDTO]:
        """Retrieves immutable versions of a strategy."""
        rows = self.store.get_declarative_strategy_versions(strategy_id)
        return [
            StrategyVersionSummaryDTO(
                version_id=r["version_id"],
                strategy_id=r["strategy_id"],
                version=r["version"],
                checksum=r["checksum"],
                created_at=r["created_at"],
                change_summary=r.get("change_summary"),
                is_active=bool(r["is_active"])
            )
            for r in rows
        ]

    def evaluate_strategy(
        self,
        strategy_id: str,
        candles: List[OHLCVCandleDTO],
        version: Optional[str] = None,
        bar_index: int = -1,
        chain_id: Optional[str] = None,
        pool_address: Optional[str] = None,
        timeframe: Optional[Timeframe] = None
    ) -> StrategyEvaluationResponseDTO:
        """Loads registered strategy and evaluates against supplied candles."""
        strategy = self.get_strategy(strategy_id, version=version)
        if not strategy:
            return StrategyEvaluationResponseDTO(
                strategy_id=strategy_id,
                strategy_version=version or "unknown",
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
                data_quality_status="STRATEGY_NOT_FOUND",
                explanation=f"Strategy '{strategy_id}' (version: {version}) not found"
            )

        if not strategy.enabled:
            return StrategyEvaluationResponseDTO(
                strategy_id=strategy_id,
                strategy_version=strategy.strategy.version,
                chain_id=chain_id or (candles[0].chain_id if candles else "unknown"),
                pool_address=pool_address or (candles[0].pool_address if candles else "unknown"),
                timeframe=timeframe or (candles[0].timeframe if candles else Timeframe.M15),
                evaluation_timestamp_utc=datetime.now(timezone.utc).isoformat(),
                evaluation_candle_timestamp_utc="N/A",
                entry_matched=False,
                exit_matched=False,
                overall_status=EvaluationStatus.NO_MATCH,
                condition_details=[],
                features_used={},
                data_quality_status="STRATEGY_DISABLED",
                explanation=f"Strategy '{strategy_id}' is currently disabled"
            )

        return strategy_evaluator.evaluate_strategy(
            strategy=strategy,
            candles=candles,
            bar_index=bar_index,
            chain_id=chain_id,
            pool_address=pool_address,
            timeframe=timeframe
        )

    def evaluate_raw(
        self,
        strategy_def: DeclarativeStrategyDTO,
        candles: List[OHLCVCandleDTO],
        bar_index: int = -1,
        chain_id: Optional[str] = None,
        pool_address: Optional[str] = None,
        timeframe: Optional[Timeframe] = None
    ) -> StrategyEvaluationResponseDTO:
        """Evaluates an unpersisted declarative strategy definition."""
        return strategy_evaluator.evaluate_strategy(
            strategy=strategy_def,
            candles=candles,
            bar_index=bar_index,
            chain_id=chain_id,
            pool_address=pool_address,
            timeframe=timeframe
        )


# Global singleton manager
strategy_manager = StrategyLifecycleManager()
