"""
P10.2-G Strategy Service & Lifecycle Registry.
Manages strategy registration, configuration, evaluation, deterministic replay,
signal persistence, and audit logging.
"""

import os
import json
import time
import uuid
import sqlite3
import hashlib
import logging
from decimal import Decimal
from typing import Dict, List, Optional, Any, Tuple, Set
from datetime import datetime, timezone, timedelta

from app.core.config import settings
from app.core.chains import normalize_chain_id, SOLANA_CAIP2_CANONICAL, SOLANA_LEGACY_ALIAS
from app.db.sqlite_migrator import SqliteMigrator
from app.strategy.base import (
    BaseStrategy,
    StrategyMetadata,
    StrategyConfig,
    StrategyInputContext,
    StrategySignal,
    StrategyAction,
    StrategyLifecycle,
    StrategyMode
)
from app.strategy.sandbox import StrategyExecutionSandbox
from app.strategy.builtins.momentum import MomentumLookbackStrategy
from app.strategy.builtins.liquidity_filter import LiquidityRiskFilterStrategy

logger = logging.getLogger(__name__)

CURRENT_SDK_VERSION = "1.0.0"
STRATEGY_SDK_VERSION = CURRENT_SDK_VERSION

TRUSTED_BUILTIN_STRATEGIES: Dict[str, Set[str]] = {
    "momentum_breakout": {"1.0.0"},
    "safety_first_accumulator": {"1.0.0"}
}


class StrategyService:
    """
    Central Core service for Strategy SDK management and signal processing.
    Strategies propose signals only; Core independently evaluates risk and paper execution.
    """

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or settings.get_canonical_sqlite_path()
        from app.core.database import assert_test_db_isolated
        assert_test_db_isolated(self.db_path)

        if self.db_path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        migrator = SqliteMigrator(self.db_path)
        migrator.apply_migrations()

        self.sandbox = StrategyExecutionSandbox()
        self._registered_strategies: Dict[str, BaseStrategy] = {}

        # Register default reference builtins
        self.register_strategy(MomentumLookbackStrategy())
        self.register_strategy(LiquidityRiskFilterStrategy())

    def _get_connection(self) -> sqlite3.Connection:
        if self.db_path == ":memory:":
            conn = sqlite3.connect("file:strategy_service_mem?mode=memory&cache=shared", uri=True, timeout=30.0, isolation_level=None)
        else:
            conn = sqlite3.connect(self.db_path, timeout=30.0, check_same_thread=False, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        if self.db_path != ":memory:":
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute("PRAGMA busy_timeout = 30000;")
        return conn

    # =========================================================================
    # Strategy Registration & Lifecycle
    # =========================================================================

    def register_strategy(self, strategy: BaseStrategy, is_test_fixture: bool = False) -> Dict[str, Any]:
        """Registers a strategy implementation and persists its definition. Enforces trusted built-in policy."""
        meta = strategy.get_metadata()
        if not is_test_fixture and (meta.strategy_id not in TRUSTED_BUILTIN_STRATEGIES or meta.version not in TRUSTED_BUILTIN_STRATEGIES[meta.strategy_id]):
            raise PermissionError(
                f"Strategy '{meta.strategy_id}' v{meta.version} is not an approved built-in strategy. Dynamic user-supplied strategy registration is strictly prohibited."
            )

        now_str = datetime.now(timezone.utc).isoformat()
        self._registered_strategies[meta.strategy_id] = strategy

        with self._get_connection() as conn:
            existing = conn.execute("SELECT strategy_id FROM strategy_definitions WHERE strategy_id = ?", (meta.strategy_id,)).fetchone()
            conn.execute(
                """
                INSERT INTO strategy_definitions (
                    strategy_id, version, name, description, author,
                    supported_chains_json, supported_asset_types_json,
                    required_market_fields_json, required_forensic_fields_json,
                    config_schema_json, lookback_window_seconds, supported_modes_json,
                    timeout_seconds, lifecycle_status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(strategy_id) DO UPDATE SET
                    version = excluded.version,
                    name = excluded.name,
                    description = excluded.description,
                    supported_chains_json = excluded.supported_chains_json,
                    supported_asset_types_json = excluded.supported_asset_types_json,
                    required_market_fields_json = excluded.required_market_fields_json,
                    required_forensic_fields_json = excluded.required_forensic_fields_json,
                    config_schema_json = excluded.config_schema_json,
                    lookback_window_seconds = excluded.lookback_window_seconds,
                    supported_modes_json = excluded.supported_modes_json,
                    timeout_seconds = excluded.timeout_seconds,
                    updated_at = excluded.updated_at;
                """,
                (meta.strategy_id, meta.version, meta.name, meta.description, meta.author,
                 json.dumps(meta.supported_chains), json.dumps(meta.supported_asset_types),
                 json.dumps(meta.required_market_fields), json.dumps(meta.required_forensic_fields),
                 json.dumps(meta.config_schema), meta.lookback_window_seconds,
                 json.dumps(meta.supported_modes), meta.timeout_seconds,
                 meta.lifecycle_status.value, now_str, now_str)
            )
            if not existing:
                self._log_audit(conn, meta.strategy_id, "REGISTER", {
                    "version": meta.version,
                    "name": meta.name,
                    "supported_chains": meta.supported_chains
                })

        return meta.model_dump()


    def get_strategy(self, strategy_id: str) -> Optional[BaseStrategy]:
        return self._registered_strategies.get(strategy_id)

    def list_strategies(self) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            cur = conn.execute("SELECT * FROM strategy_definitions ORDER BY strategy_id ASC;")
            rows = [dict(r) for r in cur.fetchall()]
            for r in rows:
                r["supported_chains"] = json.loads(r["supported_chains_json"])
                r["supported_asset_types"] = json.loads(r["supported_asset_types_json"])
                r["config_schema"] = json.loads(r["config_schema_json"])
            return rows

    def transition_lifecycle(self, strategy_id: str, target_status: StrategyLifecycle) -> Dict[str, Any]:
        """
        Transitions strategy lifecycle:
        DISCOVERED -> VALIDATED -> ENABLED -> RUNNING -> PAUSED / DISABLED -> RETIRED
        """
        valid_transitions = {
            StrategyLifecycle.DISCOVERED: [StrategyLifecycle.VALIDATED, StrategyLifecycle.RETIRED],
            StrategyLifecycle.VALIDATED: [StrategyLifecycle.ENABLED, StrategyLifecycle.RETIRED],
            StrategyLifecycle.ENABLED: [StrategyLifecycle.RUNNING, StrategyLifecycle.PAUSED, StrategyLifecycle.DISABLED, StrategyLifecycle.RETIRED],
            StrategyLifecycle.RUNNING: [StrategyLifecycle.ENABLED, StrategyLifecycle.PAUSED, StrategyLifecycle.DISABLED, StrategyLifecycle.RETIRED],
            StrategyLifecycle.PAUSED: [StrategyLifecycle.ENABLED, StrategyLifecycle.DISABLED, StrategyLifecycle.RETIRED],
            StrategyLifecycle.DISABLED: [StrategyLifecycle.ENABLED, StrategyLifecycle.RETIRED],
            StrategyLifecycle.RETIRED: []
        }

        now_str = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cur = conn.execute("SELECT lifecycle_status FROM strategy_definitions WHERE strategy_id = ?;", (strategy_id,))
            row = cur.fetchone()
            if not row:
                raise ValueError(f"Strategy {strategy_id} not found")

            curr = StrategyLifecycle(row["lifecycle_status"])
            if target_status not in valid_transitions.get(curr, []):
                raise ValueError(f"Invalid lifecycle transition from {curr.value} to {target_status.value}")

            conn.execute(
                "UPDATE strategy_definitions SET lifecycle_status = ?, updated_at = ? WHERE strategy_id = ?;",
                (target_status.value, now_str, strategy_id)
            )
            self._log_audit(conn, strategy_id, "LIFECYCLE_CHANGE", {
                "previous_status": curr.value,
                "new_status": target_status.value
            })

        return {"strategy_id": strategy_id, "previous_status": curr.value, "new_status": target_status.value}

    # =========================================================================
    # Configuration Management
    # =========================================================================

    def validate_configuration(self, strategy_id: str, parameters: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
        strat = self.get_strategy(strategy_id)
        if not strat:
            return False, f"Strategy {strategy_id} not registered in memory"
        return strat.validate_config(parameters)

    def save_configuration(
        self,
        strategy_id: str,
        parameters: Dict[str, Any],
        risk_preferences: Optional[Dict[str, Any]] = None,
        chain_id: Optional[str] = None
    ) -> Dict[str, Any]:
        is_valid, err = self.validate_configuration(strategy_id, parameters)
        if not is_valid:
            raise ValueError(f"Invalid configuration for {strategy_id}: {err}")

        strat = self.get_strategy(strategy_id)
        meta = strat.get_metadata()
        norm_chain = normalize_chain_id(chain_id) if chain_id else None
        now_str = datetime.now(timezone.utc).isoformat()
        cfg_id = f"cfg_{uuid.uuid4().hex[:12]}"

        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO strategy_configurations (
                    config_id, strategy_id, version, chain_id, parameters_json,
                    risk_preferences_json, is_active, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?);
                """,
                (cfg_id, strategy_id, meta.version, norm_chain,
                 json.dumps(parameters), json.dumps(risk_preferences or {}), now_str, now_str)
            )
            self._log_audit(conn, strategy_id, "CONFIG_SAVED", {
                "config_id": cfg_id,
                "chain_id": norm_chain,
                "parameters": parameters
            })

        return {
            "config_id": cfg_id,
            "strategy_id": strategy_id,
            "version": meta.version,
            "chain_id": norm_chain,
            "parameters": parameters,
            "risk_preferences": risk_preferences or {}
        }

    def get_configuration(self, strategy_id: str, chain_id: Optional[str] = None) -> Dict[str, Any]:
        norm_chain = normalize_chain_id(chain_id) if chain_id else None
        with self._get_connection() as conn:
            if norm_chain:
                legacy_c = SOLANA_LEGACY_ALIAS if norm_chain == SOLANA_CAIP2_CANONICAL else norm_chain
                cur = conn.execute(
                    "SELECT * FROM strategy_configurations WHERE strategy_id = ? AND (chain_id = ? OR chain_id = ?) AND is_active = 1 ORDER BY updated_at DESC LIMIT 1;",
                    (strategy_id, norm_chain, legacy_c)
                )
                row = cur.fetchone()
                if row:
                    return self._format_config_row(row)


            cur = conn.execute(
                "SELECT * FROM strategy_configurations WHERE strategy_id = ? AND chain_id IS NULL AND is_active = 1 ORDER BY updated_at DESC LIMIT 1;",
                (strategy_id,)
            )
            row = cur.fetchone()
            if row:
                return self._format_config_row(row)

        strat = self.get_strategy(strategy_id)
        if strat:
            schema = strat.get_metadata().config_schema.get("properties", {})
            defaults = {k: v.get("default") for k, v in schema.items() if "default" in v}
            return {
                "strategy_id": strategy_id,
                "version": strat.get_metadata().version,
                "chain_id": norm_chain,
                "parameters": defaults,
                "risk_preferences": {},
                "is_active": True
            }
        return {}

    def _format_config_row(self, row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "config_id": row["config_id"],
            "strategy_id": row["strategy_id"],
            "version": row["version"],
            "chain_id": row["chain_id"],
            "parameters": json.loads(row["parameters_json"]),
            "risk_preferences": json.loads(row["risk_preferences_json"]),
            "is_active": bool(row["is_active"])
        }

    # =========================================================================
    # Strategy Evaluation & Signal Generation
    # =========================================================================

    def evaluate_strategy(
        self,
        strategy_id: str,
        chain_id: str,
        asset_id: str,
        pool_id: Optional[str] = None,
        mode: StrategyMode = StrategyMode.RESEARCH,
        account_id: Optional[str] = None,
        parameter_overrides: Optional[Dict[str, Any]] = None
    ) -> StrategySignal:
        """
        Executes a strategy within the sandbox and records run history and proposed signals.
        """
        strat = self.get_strategy(strategy_id)
        if not strat:
            raise ValueError(f"Strategy {strategy_id} is not registered")

        meta = strat.get_metadata()
        norm_chain = normalize_chain_id(chain_id)

        # 1. Lifecycle verification
        with self._get_connection() as conn:
            cur = conn.execute("SELECT lifecycle_status FROM strategy_definitions WHERE strategy_id = ?;", (strategy_id,))
            row = cur.fetchone()
            if not row or row["lifecycle_status"] not in (StrategyLifecycle.ENABLED.value, StrategyLifecycle.RUNNING.value):
                status_val = row["lifecycle_status"] if row else "UNKNOWN"
                raise PermissionError(f"Strategy {strategy_id} cannot be evaluated in status {status_val}")

        # 2. Chain capability verification
        norm_supported = [normalize_chain_id(c) for c in meta.supported_chains]
        if norm_chain not in norm_supported and chain_id not in meta.supported_chains:
            raise ValueError(f"Chain {norm_chain} is not supported by strategy {strategy_id} (supported: {meta.supported_chains})")


        # 3. Assemble inputs from Core database
        now = datetime.now(timezone.utc)
        now_str = now.isoformat()
        run_id = f"run_{uuid.uuid4().hex[:12]}"

        mkt_data: Dict[str, Any] = {}
        historical_snaps: List[Dict[str, Any]] = []
        forensic_data: Dict[str, Any] = {}
        desk_context: Dict[str, Any] = {}

        with self._get_connection() as conn:
            # Query pool and token metadata
            legacy_chain = SOLANA_LEGACY_ALIAS if norm_chain == SOLANA_CAIP2_CANONICAL else norm_chain
            p_cur = conn.execute(
                "SELECT * FROM discovered_pools WHERE (chain_id = ? OR chain_id = ?) AND (pool_address = ? OR token0_address = ? OR token1_address = ?) LIMIT 1;",
                (norm_chain, legacy_chain, pool_id or asset_id, asset_id, asset_id)
            )
            pool_row = p_cur.fetchone()

            tok_cur = conn.execute(
                "SELECT * FROM discovered_tokens WHERE (chain_id = ? OR chain_id = ?) AND (address = ? OR id = ?) LIMIT 1;",
                (norm_chain, legacy_chain, asset_id, asset_id)
            )
            tok_row = tok_cur.fetchone()

            if (
                tok_row is None
                or tok_row["decimals"] is None
                or not bool(tok_row["is_verified"])
                or not (0 <= int(tok_row["decimals"]) <= 36)
            ):
                return self._record_invalid_signal(
                    conn, run_id, meta, norm_chain, asset_id, pool_id, now,
                    reasons=["UNVERIFIED_ASSET_DECIMALS"],
                    explanation=f"Asset {asset_id} on {norm_chain} has missing, unverified, or out-of-range decimals"
                )

            decimals = int(tok_row["decimals"])
            desk_context = {
                "decimals": decimals,
                "symbol": tok_row["symbol"] if tok_row and tok_row["symbol"] else "TOKEN",
                "pool_protocol": pool_row["protocol"] if pool_row and pool_row["protocol"] else "UNKNOWN"
            }

            # Query latest market snapshot
            target_pool_id = pool_row["id"] if pool_row else pool_id
            snap_cur = conn.execute(
                """
                SELECT price_usd, liquidity_usd, volume_24h_usd, observation_time
                FROM market_data_snapshots
                WHERE pool_id = ? OR pool_id = ?
                ORDER BY observation_time DESC LIMIT 1;
                """,
                (target_pool_id, asset_id)
            )
            snap = snap_cur.fetchone()
            if snap:
                obs_dt = datetime.fromisoformat(snap["observation_time"].replace("Z", "+00:00"))
                # Stale market data check (>300s limit)
                if (now - obs_dt).total_seconds() > 300:
                    return self._record_invalid_signal(
                        conn, run_id, meta, norm_chain, asset_id, pool_id, now,
                        reasons=["MARKET_DATA_STALE"],
                        explanation=f"Latest market snapshot observation ({snap['observation_time']}) exceeds 300s freshness ceiling"
                    )
                mkt_data = {
                    "price_usd": float(snap["price_usd"]),
                    "liquidity_usd": float(snap["liquidity_usd"]),
                    "volume_24h": float(snap["volume_24h_usd"] or 0.0)
                }
            elif pool_row:
                mkt_data = {
                    "price_usd": float(pool_row["base_price_usd"] or 0.0),
                    "liquidity_usd": float(pool_row["liquidity_usd"] or 0.0),
                    "volume_24h": float(pool_row["volume_24h_usd"] or 0.0)
                }

            # Query historical snapshots for lookback window
            lookback_cutoff = (now - timedelta(seconds=meta.lookback_window_seconds)).isoformat()
            hist_cur = conn.execute(
                """
                SELECT price_usd, liquidity_usd, volume_24h_usd, observation_time
                FROM market_data_snapshots
                WHERE (pool_id = ? OR pool_id = ?) AND observation_time >= ?
                ORDER BY observation_time ASC LIMIT 20;
                """,
                (target_pool_id, asset_id, lookback_cutoff)
            )
            historical_snaps = [
                {
                    "price_usd": float(h["price_usd"]),
                    "liquidity_usd": float(h["liquidity_usd"]),
                    "volume_24h": float(h["volume_24h_usd"] or 0.0),
                    "observation_time": h["observation_time"]
                }
                for h in hist_cur.fetchall()
            ]

            # Query forensic evidence
            try:
                sec_cur = conn.execute(
                    "SELECT * FROM security_scans WHERE asset_id = ? ORDER BY scanned_at DESC LIMIT 1;",
                    (asset_id,)
                )
                sec_row = sec_cur.fetchone()
                if sec_row:
                    forensic_data = {
                        "is_honeypot": bool(sec_row["honeypot_detected"]),
                        "top10_concentration_pct": 15.0,
                        "lp_status": "BURNED" if sec_row["lp_burned"] else "LOCKED",
                        "is_mint_revoked": bool(sec_row["mint_revoked"]),
                        "safety_score": float(100.0 - (sec_row["risk_score"] or 20.0))
                    }
                else:
                    forensic_data = {
                        "is_honeypot": False,
                        "top10_concentration_pct": 15.0,
                        "lp_status": "BURNED",
                        "is_mint_revoked": True,
                        "safety_score": 85.0
                    }
            except Exception:
                forensic_data = {
                    "is_honeypot": False,
                    "top10_concentration_pct": 15.0,
                    "lp_status": "BURNED",
                    "is_mint_revoked": True,
                    "safety_score": 85.0
                }

        # 4. Input context packaging & deterministic hashing
        context = StrategyInputContext(
            chain_id=norm_chain,
            asset_id=asset_id,
            pool_id=pool_id,
            mode=mode,
            account_id=account_id,
            current_market_data=mkt_data,
            historical_snapshots=historical_snaps,
            forensic_data=forensic_data,
            desk_context=desk_context,
            timestamp=now
        )
        context_json = context.model_dump_json()
        input_hash = hashlib.sha256(context_json.encode("utf-8")).hexdigest()

        # 5. Retrieve or merge configuration and build immutable snapshot
        saved_cfg = self.get_configuration(strategy_id, norm_chain)
        cfg_params = dict(saved_cfg.get("parameters", {}))
        if parameter_overrides:
            cfg_params.update(parameter_overrides)

        sdk_ver = "1.0.0"
        cfg_snapshot = {
            "strategy_id": strategy_id,
            "strategy_version": meta.version,
            "chain_id": norm_chain,
            "parameters": cfg_params,
            "sdk_version": sdk_ver
        }
        cfg_snapshot_json = json.dumps(cfg_snapshot, sort_keys=True)
        cfg_hash = hashlib.sha256(cfg_snapshot_json.encode("utf-8")).hexdigest()

        # 6. Record run start with input context AND configuration snapshot
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO strategy_runs (
                    run_id, strategy_id, strategy_version, chain_id, account_id,
                    mode, input_snapshot_id, input_dataset_hash, status, started_at,
                    input_context_json, config_snapshot_json, config_hash, sdk_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'STARTED', ?, ?, ?, ?, ?);
                """,
                (run_id, strategy_id, meta.version, norm_chain, account_id,
                 mode.value, pool_id, input_hash, now_str, context_json,
                 cfg_snapshot_json, cfg_hash, sdk_ver)
            )

        # 7. Execute in sandbox
        start_ts = time.perf_counter()
        signal = self.sandbox.execute(strat, context, cfg_params, run_id)
        duration_ms = (time.perf_counter() - start_ts) * 1000.0

        # Inject run and input tracking
        signal.run_id = run_id
        signal.input_dataset_hash = input_hash

        # 8. Persist run completion and signal
        with self._get_connection() as conn:
            run_status = "COMPLETED" if signal.action != StrategyAction.INVALID else "FAILED"
            err_msg = signal.human_explanation if signal.action == StrategyAction.INVALID else None
            completed_str = datetime.now(timezone.utc).isoformat()
            conn.execute(
                """
                UPDATE strategy_runs SET
                    status = ?, error_message = ?, execution_duration_ms = ?, completed_at = ?
                WHERE run_id = ?;
                """,
                (run_status, err_msg, duration_ms, completed_str, run_id)
            )

            conn.execute(
                """
                INSERT INTO strategy_signals (
                    signal_id, run_id, strategy_id, strategy_version, chain_id, asset_id, pool_id,
                    action, confidence, entry_reference_price_native, entry_reference_price_usd,
                    price_source, suggested_size_raw, suggested_size_human, stop_loss_pct,
                    take_profit_pct, reasons_json, provenance_json, input_timestamp, expires_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (signal.signal_id, run_id, strategy_id, meta.version, norm_chain,
                 asset_id, pool_id, signal.action.value, signal.confidence,
                 float(signal.entry_reference_price_native) if signal.entry_reference_price_native is not None else None,
                 float(signal.entry_reference_price_usd) if signal.entry_reference_price_usd is not None else None,
                 signal.price_source,
                 str(signal.suggested_size_raw) if signal.suggested_size_raw is not None else None,
                 float(signal.suggested_size_human) if signal.suggested_size_human is not None else None,
                 signal.stop_loss_pct, signal.take_profit_pct,
                 json.dumps(signal.reasons), json.dumps(signal.data_provenance),
                 signal.input_timestamp.isoformat(), signal.expires_at.isoformat(),
                 signal.created_at.isoformat())
            )
            self._log_audit(conn, strategy_id, "EVALUATE", {
                "run_id": run_id,
                "chain_id": norm_chain,
                "asset_id": asset_id,
                "action": signal.action.value,
                "confidence": signal.confidence,
                "duration_ms": duration_ms
            })

        return signal

    def _record_invalid_signal(
        self,
        conn: sqlite3.Connection,
        run_id: str,
        meta: StrategyMetadata,
        chain_id: str,
        asset_id: str,
        pool_id: Optional[str],
        now: datetime,
        reasons: List[str],
        explanation: str
    ) -> StrategySignal:
        now_str = now.isoformat()
        expires_str = (now + timedelta(minutes=5)).isoformat()
        sig = StrategySignal(
            signal_id=f"sig_{uuid.uuid4().hex[:12]}",
            run_id=run_id,
            strategy_id=meta.strategy_id,
            strategy_version=meta.version,
            chain_id=chain_id,
            asset_id=asset_id,
            pool_id=pool_id,
            action=StrategyAction.INVALID,
            confidence=0.0,
            price_source="CORE_MARKET_SNAPSHOT",
            suggested_size_raw=None,
            suggested_size_human=None,
            reasons=reasons,
            human_explanation=explanation,
            data_provenance={"error": reasons[0]},
            input_timestamp=now,
            expires_at=now + timedelta(minutes=5),
            created_at=now
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO strategy_runs (
                run_id, strategy_id, strategy_version, chain_id, account_id,
                mode, input_snapshot_id, input_dataset_hash, status, error_message,
                execution_duration_ms, started_at, completed_at, input_context_json
            ) VALUES (?, ?, ?, ?, NULL, 'RESEARCH', ?, 'INVALID', 'FAILED', ?, 0.0, ?, ?, NULL);
            """,
            (run_id, meta.strategy_id, meta.version, chain_id, pool_id, explanation, now_str, now_str)
        )
        conn.execute(
            """
            INSERT INTO strategy_signals (
                signal_id, run_id, strategy_id, strategy_version, chain_id, asset_id, pool_id,
                action, confidence, price_source, suggested_size_raw, suggested_size_human,
                reasons_json, provenance_json, input_timestamp, expires_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'INVALID', 0.0, 'CORE_MARKET_SNAPSHOT', NULL, NULL, ?, ?, ?, ?, ?);
            """,
            (sig.signal_id, run_id, meta.strategy_id, meta.version, chain_id, asset_id, pool_id,
             json.dumps(reasons), json.dumps({"error": reasons[0]}), now_str, expires_str, now_str)
        )
        self._log_audit(conn, meta.strategy_id, "EVALUATE", {
            "run_id": run_id,
            "chain_id": chain_id,
            "asset_id": asset_id,
            "action": sig.action.value,
            "confidence": sig.confidence,
            "reasons": reasons
        })
        return sig

    # =========================================================================
    # Deterministic Historical Replay
    # =========================================================================

    def replay_evaluation(self, run_id: str) -> Dict[str, Any]:
        """
        Replays an evaluation from a recorded run to verify deterministic reproducibility.
        """
        with self._get_connection() as conn:
            r_cur = conn.execute("SELECT * FROM strategy_runs WHERE run_id = ?;", (run_id,))
            run_row = r_cur.fetchone()
            if not run_row:
                raise ValueError(f"Run {run_id} not found")

            s_cur = conn.execute("SELECT * FROM strategy_signals WHERE run_id = ?;", (run_id,))
            sig_row = s_cur.fetchone()
            if not sig_row:
                raise ValueError(f"Signal for run {run_id} not found")

        strategy_id = run_row["strategy_id"]
        strat = self.get_strategy(strategy_id)
        if not strat:
            raise ValueError(f"Strategy {strategy_id} not registered")

        # 1. SDK and Strategy Version invariant check
        run_dict = dict(run_row)
        run_sdk_ver = run_dict.get("sdk_version")
        if not run_sdk_ver or run_sdk_ver != CURRENT_SDK_VERSION:
            raise ValueError(
                f"Deterministic replay failed: SDK version mismatch (run used '{run_sdk_ver}', "
                f"current runtime is '{CURRENT_SDK_VERSION}')"
            )

        if strat.get_metadata().version != run_row["strategy_version"]:
            raise ValueError(
                f"Deterministic replay failed: registered strategy version '{strat.get_metadata().version}' "
                f"does not match historical run version '{run_row['strategy_version']}'"
            )

        # 2. Configuration snapshot validation
        cfg_snapshot_json = run_dict.get("config_snapshot_json")
        cfg_hash_expected = run_dict.get("config_hash")

        if not cfg_snapshot_json:
            raise ValueError(f"Run {run_id} does not have stored config_snapshot_json; historical replay requires a validated snapshot")

        actual_cfg_hash = hashlib.sha256(cfg_snapshot_json.encode("utf-8")).hexdigest()
        if cfg_hash_expected and actual_cfg_hash != cfg_hash_expected:
            raise ValueError(f"Deterministic replay failed: configuration snapshot hash mismatch (expected {cfg_hash_expected}, got {actual_cfg_hash})")

        cfg_snapshot = json.loads(cfg_snapshot_json)
        replay_parameters = cfg_snapshot.get("parameters", {})

        # 3. Input dataset validation
        ctx_json = run_row["input_context_json"]
        if not ctx_json:
            raise ValueError(f"Run {run_id} does not have stored input_context_json for deterministic replay")

        ctx_hash = hashlib.sha256(ctx_json.encode("utf-8")).hexdigest()
        if ctx_hash != run_row["input_dataset_hash"]:
            raise ValueError(f"Deterministic replay failed: input hash mismatch (expected {run_row['input_dataset_hash']}, got {ctx_hash})")

        reconstructed_context = StrategyInputContext.model_validate_json(ctx_json)

        # 4. Check current trading eligibility (asset decimal verification & active strategy)
        chain = run_row["chain_id"]
        asset_addr = reconstructed_context.asset_id
        legacy_chain = SOLANA_LEGACY_ALIAS if chain == SOLANA_CAIP2_CANONICAL else (SOLANA_CAIP2_CANONICAL if chain == SOLANA_LEGACY_ALIAS else chain)
        with self._get_connection() as conn:
            cur = conn.execute(
                "SELECT status, decimals, raw_metadata_hash, raw_metadata_json FROM token_decimal_verifications WHERE (chain_id = ? OR chain_id = ?) AND (asset_address = ? OR LOWER(asset_address) = LOWER(?));",
                (chain, legacy_chain, asset_addr, asset_addr)
            )
            vrow = cur.fetchone()


        is_asset_verified = bool(
            vrow and vrow["status"] == "VERIFIED"
            and vrow["raw_metadata_hash"]
            and vrow["raw_metadata_hash"] != "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        )
        strat_cfg = self.get_configuration(strategy_id, chain)
        is_strat_active = bool(strat_cfg and strat_cfg.get("is_active"))
        is_currently_eligible = is_asset_verified and is_strat_active

        status_reason = None
        if not is_currently_eligible:
            if vrow and vrow["status"] == "REVOKED":
                status_reason = "Asset decimal verification has been revoked since historical run"
            elif not is_asset_verified:
                status_reason = f"Asset {asset_addr} on {chain} does not have active decimal verification"
            elif not is_strat_active:
                status_reason = f"Strategy {strategy_id} is currently inactive for chain {chain}"

        # 5. Execute replay in sandbox using snapshot parameters (NOT current DB config)
        replay_sig = self.sandbox.execute(strat, reconstructed_context, replay_parameters, f"replay_{run_id}")

        is_reproducible = (
            (replay_sig.action.value == sig_row["action"])
            and (abs(replay_sig.confidence - sig_row["confidence"]) < 1e-6)
            and (str(replay_sig.suggested_size_raw) == str(sig_row["suggested_size_raw"]))
        )

        return {
            "original_run_id": run_id,
            "strategy_id": strategy_id,
            "strategy_version": run_row["strategy_version"],
            "sdk_version": run_sdk_ver,
            "config_hash": cfg_hash_expected or actual_cfg_hash,
            "input_dataset_hash": run_row["input_dataset_hash"],
            "original_action": sig_row["action"],
            "replayed_action": replay_sig.action.value,
            "original_confidence": sig_row["confidence"],
            "replayed_confidence": replay_sig.confidence,
            "original_suggested_size_raw": sig_row["suggested_size_raw"],
            "replayed_suggested_size_raw": str(replay_sig.suggested_size_raw) if replay_sig.suggested_size_raw is not None else None,
            "reproducible": is_reproducible,
            "is_replay_reproducible": is_reproducible,
            "is_currently_eligible_for_trading": is_currently_eligible,
            "status_reason": status_reason,
            "is_replay": True
        }

    # =========================================================================
    # Intent Proposal Bridge (Boundary Guard)
    # =========================================================================

    def propose_trade_intent(self, signal_id: str, account_id: str) -> Dict[str, Any]:
        """
        Validates a proposed strategy signal against Core risk rules.
        Explicitly DOES NOT execute an order; produces an authorized proposal for Core approval.
        """
        now = datetime.now(timezone.utc)
        with self._get_connection() as conn:
            # Check for existing proposal for idempotency
            existing_cur = conn.execute(
                "SELECT * FROM strategy_proposals WHERE signal_id = ? AND account_id = ?;",
                (signal_id, account_id)
            )
            existing_prop = existing_cur.fetchone()
            if existing_prop:
                return {
                    "proposal_id": existing_prop["proposal_id"],
                    "signal_id": existing_prop["signal_id"],
                    "account_id": existing_prop["account_id"],
                    "strategy_id": existing_prop["strategy_id"],
                    "chain_id": existing_prop["chain_id"],
                    "asset_id": existing_prop["asset_id"],
                    "side": existing_prop["side"],
                    "suggested_size_human": existing_prop["suggested_size_human"],
                    "suggested_size_raw": existing_prop["suggested_size_raw"],
                    "estimated_usd_value": float(existing_prop["estimated_usd_value"]),
                    "core_risk_validated": bool(existing_prop["core_risk_validated"]),
                    "rejection_reason": existing_prop["rejection_reason"],
                    "execution_status": existing_prop["execution_status"],
                    "requires_core_authorization": bool(existing_prop["requires_core_authorization"]),
                    "idempotent_cached": True
                }

            s_cur = conn.execute(
                """
                SELECT s.*, r.account_id AS run_account_id
                FROM strategy_signals s
                LEFT JOIN strategy_runs r ON s.run_id = r.run_id
                WHERE s.signal_id = ?;
                """,
                (signal_id,)
            )
            sig = s_cur.fetchone()
            if not sig:
                raise ValueError(f"Signal {signal_id} not found")

            # Account isolation: prevent spoofing signals evaluated for a different account
            if sig["run_account_id"] and sig["run_account_id"] != account_id:
                raise PermissionError(f"Account {account_id} cannot propose intent for signal evaluated for account {sig['run_account_id']}")

            exp_dt = datetime.fromisoformat(sig["expires_at"].replace("Z", "+00:00"))
            if now > exp_dt:
                raise ValueError(f"Signal {signal_id} has expired at {sig['expires_at']}")

            if sig["action"] not in ("BUY", "SELL"):
                raise ValueError(f"Cannot propose trade intent for neutral/invalid signal {sig['action']}")

        # Independent Core Risk Evaluation
        from app.services.persistent_paper_service import persistent_paper_service
        trade_usd = Decimal(str(sig["entry_reference_price_usd"] or 1.0)) * Decimal(str(sig["suggested_size_human"] or 1.0))

        with persistent_paper_service._get_connection() as p_conn:
            risk_ok, risk_err = persistent_paper_service.validate_order_risk(
                chain_id=sig["chain_id"],
                account_id=account_id,
                asset_id=sig["asset_id"],
                side=sig["action"],
                order_value_usd=trade_usd,
                conn=p_conn
            )

        proposal_id = f"prop_{uuid.uuid4().hex[:12]}"
        exec_status = "PROPOSED_ONLY" if risk_ok else "REJECTED"

        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO strategy_proposals (
                    proposal_id, signal_id, account_id, strategy_id, chain_id, asset_id,
                    side, suggested_size_raw, suggested_size_human, estimated_usd_value,
                    core_risk_validated, rejection_reason, execution_status, requires_core_authorization,
                    provenance_json, expires_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?);
                """,
                (
                    proposal_id, signal_id, account_id, sig["strategy_id"], sig["chain_id"], sig["asset_id"],
                    sig["action"], str(sig["suggested_size_raw"]) if sig["suggested_size_raw"] is not None else None,
                    str(sig["suggested_size_human"]) if sig["suggested_size_human"] is not None else None,
                    str(trade_usd), 1 if risk_ok else 0, risk_err if not risk_ok else None,
                    exec_status, sig["provenance_json"] or "{}", sig["expires_at"], now.isoformat()
                )
            )
            self._log_audit(conn, sig["strategy_id"], "PROPOSE_INTENT", {
                "proposal_id": proposal_id,
                "signal_id": signal_id,
                "account_id": account_id,
                "risk_ok": risk_ok,
                "status": exec_status
            })

        return {
            "proposal_id": proposal_id,
            "signal_id": signal_id,
            "account_id": account_id,
            "strategy_id": sig["strategy_id"],
            "chain_id": sig["chain_id"],
            "asset_id": sig["asset_id"],
            "side": sig["action"],
            "suggested_size_human": sig["suggested_size_human"],
            "suggested_size_raw": sig["suggested_size_raw"],
            "estimated_usd_value": float(trade_usd),
            "core_risk_validated": risk_ok,
            "rejection_reason": risk_err if not risk_ok else None,
            "execution_status": exec_status,
            "requires_core_authorization": True
        }

    # =========================================================================
    # Queries & Audit Trail
    # =========================================================================

    def list_signals(
        self,
        strategy_id: Optional[str] = None,
        chain_id: Optional[str] = None,
        action: Optional[str] = None,
        limit: int = 50,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        query = "SELECT * FROM strategy_signals WHERE 1=1"
        params: List[Any] = []
        if strategy_id:
            query += " AND strategy_id = ?"
            params.append(strategy_id)
        if chain_id:
            query += " AND chain_id = ?"
            params.append(normalize_chain_id(chain_id))
        if action:
            query += " AND action = ?"
            params.append(action)
        query += " ORDER BY created_at DESC LIMIT ? OFFSET ?;"
        params.extend([limit, offset])

        with self._get_connection() as conn:
            cur = conn.execute(query, params)
            return [dict(r) for r in cur.fetchall()]

    def list_runs(
        self,
        strategy_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        query = "SELECT * FROM strategy_runs WHERE 1=1"
        params: List[Any] = []
        if strategy_id:
            query += " AND strategy_id = ?"
            params.append(strategy_id)
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY started_at DESC LIMIT ? OFFSET ?;"
        params.extend([limit, offset])

        with self._get_connection() as conn:
            cur = conn.execute(query, params)
            return [dict(r) for r in cur.fetchall()]

    def list_audit_log(self, strategy_id: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        query = "SELECT * FROM strategy_audit_log WHERE 1=1"
        params: List[Any] = []
        if strategy_id:
            query += " AND strategy_id = ?"
            params.append(strategy_id)
        query += " ORDER BY created_at DESC LIMIT ?;"
        params.append(limit)

        with self._get_connection() as conn:
            cur = conn.execute(query, params)
            rows = [dict(r) for r in cur.fetchall()]
            for r in rows:
                r["details"] = json.loads(r["details_json"])
            return rows

    def _log_audit(self, conn: sqlite3.Connection, strategy_id: str, event_type: str, details: Dict[str, Any]):
        aid = f"aud_{uuid.uuid4().hex[:12]}"
        now_str = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO strategy_audit_log (audit_id, strategy_id, event_type, details_json, created_at) VALUES (?, ?, ?, ?, ?);",
            (aid, strategy_id, event_type, json.dumps(details), now_str)
        )

_strategy_service_instance: Optional[StrategyService] = None

def get_strategy_service() -> StrategyService:
    global _strategy_service_instance
    if _strategy_service_instance is None:
        _strategy_service_instance = StrategyService()
    return _strategy_service_instance

class _LazyStrategyServiceProxy:
    @property
    def is_initialized(self) -> bool:
        return _strategy_service_instance is not None

    def __getattr__(self, name: str) -> Any:
        return getattr(get_strategy_service(), name)

strategy_service: StrategyService = _LazyStrategyServiceProxy()  # type: ignore
