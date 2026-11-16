"""
P10.2-G Strategy Execution Sandbox & Boundary Isolation.

Isolation Model & Limitations (Honest Disclosure):
1. In-process boundary enforcement:
   - Evaluates strategies in restricted worker threads with hard timeouts.
   - Restricts inputs to immutable, serialized `StrategyInputContext` instances.
   - Denies passing of database connections, ledger journals, signing keys, or provider vault handles.
   - Traps all strategy exceptions, containing faults and returning INVALID signals without crashing Core.
   - Validates and sanitizes input/output payload sizes to prevent memory exhaustion on low-spec devices.
2. Termux & Lightweight Compatibility Limitation:
   - On Android/Termux and minimal Linux systems lacking root, cgroups, or kernel seccomp filters,
     full operating-system containerization is unavailable.
   - This sandbox provides defensive software-level fault isolation, input validation, and authorization
     guards; it does NOT claim to provide hardware-level multi-tenant hypervisor isolation.
   - Arbitrary external plugin loading is disabled in production mode.
"""

import sys
import uuid
import time
import copy
import threading
import logging
from typing import Dict, Any, Optional, Set
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, TimeoutError

from app.strategy.base import (
    BaseStrategy,
    StrategyInputContext,
    StrategySignal,
    StrategyAction
)

logger = logging.getLogger(__name__)

# Maximum serialized payload size limits for Termux / memory-constrained hosts
MAX_INPUT_PAYLOAD_BYTES = 5 * 1024 * 1024   # 5 MB
MAX_OUTPUT_PAYLOAD_BYTES = 256 * 1024       # 256 KB


class StrategySandboxViolation(Exception):
    """Raised when a strategy attempts unauthorized system access or violates boundary constraints."""
    pass


class StrategyExecutionSandbox:
    """
    Executes strategy evaluations within defensive isolation boundaries.
    Guarantees that strategy failures fail closed without compromising Core trading operations.

    Note on In-Process Isolation Limitations:
    - This sandbox provides responsiveness guards and exception containment in worker threads.
    - Python threads cannot be preemptively terminated; timed-out threads continue in the background.
    - To prevent thread pool starvation, admission is bounded (max_workers + max_queue_size).
    - Late completions from abandoned timed-out threads are tracked and dropped.
    - Dynamic loading of untrusted third-party code is strictly prohibited.
    """

    def __init__(self, max_workers: int = 4, max_queue_size: int = 4):
        self._max_workers = max_workers
        self._max_queue_size = max_queue_size
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="strat_sandbox")
        self._active_runs: Set[str] = set()
        self._timed_out_runs: Set[str] = set()
        self._in_flight = 0
        self._lock = threading.Lock()

    def shutdown(self, wait: bool = False):
        """Shuts down the executor without blocking on abandoned threads if wait=False."""
        self._executor.shutdown(wait=wait, cancel_futures=True)

    def execute(
        self,
        strategy: BaseStrategy,
        context: StrategyInputContext,
        config: Dict[str, Any],
        run_id: str,
        timeout_seconds: Optional[float] = None
    ) -> StrategySignal:
        """
        Executes a strategy within boundary constraints and timeout protection.
        """
        meta = strategy.get_metadata()
        effective_timeout = timeout_seconds if timeout_seconds is not None else meta.timeout_seconds
        now = datetime.now(timezone.utc)

        # 1. Bounded admission & capacity guard (prevent unbounded starvation)
        with self._lock:
            if self._in_flight >= (self._max_workers + self._max_queue_size):
                logger.warning(
                    "Worker pool exhausted (in_flight=%d, max_capacity=%d); rejecting strategy %s",
                    self._in_flight, self._max_workers + self._max_queue_size, meta.strategy_id
                )
                return StrategySignal(
                    signal_id=f"sig_{uuid.uuid4().hex[:12]}",
                    run_id=run_id,
                    strategy_id=meta.strategy_id,
                    strategy_version=meta.version,
                    chain_id=context.chain_id,
                    asset_id=context.asset_id,
                    pool_id=context.pool_id,
                    action=StrategyAction.INVALID,
                    confidence=0.0,
                    price_source="CORE_MARKET_SNAPSHOT",
                    suggested_size_raw=None,
                    suggested_size_human=None,
                    reasons=["WORKER_POOL_EXHAUSTED", "BUSY"],
                    human_explanation=f"Strategy execution rejected: worker pool capacity limit ({self._max_workers + self._max_queue_size}) reached",
                    data_provenance={"error": "QUEUE_OVERFLOW", "in_flight": self._in_flight},
                    input_timestamp=context.timestamp,
                    expires_at=now + timedelta(minutes=5),
                    created_at=now
                )
            self._in_flight += 1
            self._active_runs.add(run_id)

        submitted = False
        try:
            # 2. Payload size guard
            raw_ctx_len = len(context.model_dump_json().encode("utf-8"))
            if raw_ctx_len > MAX_INPUT_PAYLOAD_BYTES:
                return StrategySignal(
                    signal_id=f"sig_{uuid.uuid4().hex[:12]}",
                    run_id=run_id,
                    strategy_id=meta.strategy_id,
                    strategy_version=meta.version,
                    chain_id=context.chain_id,
                    asset_id=context.asset_id,
                    pool_id=context.pool_id,
                    action=StrategyAction.INVALID,
                    confidence=0.0,
                    price_source="CORE_MARKET_SNAPSHOT",
                    suggested_size_raw=None,
                    suggested_size_human=None,
                    reasons=["PAYLOAD_LIMIT_EXCEEDED"],
                    human_explanation=f"Input context size ({raw_ctx_len} bytes) exceeded ceiling ({MAX_INPUT_PAYLOAD_BYTES} bytes)",
                    data_provenance={"error": "PAYLOAD_TOO_LARGE"},
                    input_timestamp=context.timestamp,
                    expires_at=now + timedelta(minutes=5),
                    created_at=now
                )

            # 3. Deeply freeze context and copy config to prevent nested mutation
            isolated_ctx = context.to_deeply_immutable()
            isolated_cfg = copy.deepcopy(config)

            # 4. Worker execution with timeout containment
            start_time = time.perf_counter()

            def _isolated_worker() -> Optional[StrategySignal]:
                try:
                    # Evaluate strategy with deeply immutable context
                    sig = strategy.evaluate(isolated_ctx, isolated_cfg)

                    # Check if this worker completed late after caller timed out
                    with self._lock:
                        if run_id in self._timed_out_runs:
                            logger.warning(
                                "Strategy %s worker completed late for timed-out run %s; result dropped",
                                meta.strategy_id, run_id
                            )
                            return None

                    # Ensure required metadata is tagged
                    if not isinstance(sig, StrategySignal):
                        raise ValueError(f"Strategy must return StrategySignal instance, got {type(sig)}")
                    return sig
                finally:
                    with self._lock:
                        self._in_flight = max(0, self._in_flight - 1)
                        self._active_runs.discard(run_id)
                        self._timed_out_runs.discard(run_id)

            future = self._executor.submit(_isolated_worker)
            submitted = True
            try:
                signal = future.result(timeout=effective_timeout)
                if signal is None:
                    # Late completion already handled
                    raise TimeoutError()
            except TimeoutError:
                duration_ms = (time.perf_counter() - start_time) * 1000.0
                with self._lock:
                    self._timed_out_runs.add(run_id)
                logger.warning(
                    "Strategy %s timed out after %.2f ms (limit %.2f s); thread execution abandoned in background",
                    meta.strategy_id, duration_ms, effective_timeout
                )
                return StrategySignal(
                    signal_id=f"sig_{uuid.uuid4().hex[:12]}",
                    run_id=run_id,
                    strategy_id=meta.strategy_id,
                    strategy_version=meta.version,
                    chain_id=context.chain_id,
                    asset_id=context.asset_id,
                    pool_id=context.pool_id,
                    action=StrategyAction.INVALID,
                    confidence=0.0,
                    price_source="CORE_MARKET_SNAPSHOT",
                    suggested_size_raw=None,
                    suggested_size_human=None,
                    reasons=["EXECUTION_TIMEOUT"],
                    human_explanation=f"Strategy execution exceeded {effective_timeout}s timeout ceiling; thread execution abandoned (thread continues in background until completion; late results dropped)",
                    data_provenance={"timeout_seconds": effective_timeout, "duration_ms": duration_ms},
                    input_timestamp=context.timestamp,
                    expires_at=now + timedelta(minutes=5),
                    created_at=now
                )
            except Exception as exc:
                duration_ms = (time.perf_counter() - start_time) * 1000.0
                logger.error("Strategy %s evaluation exception: %s", meta.strategy_id, exc, exc_info=True)
                return StrategySignal(
                    signal_id=f"sig_{uuid.uuid4().hex[:12]}",
                    run_id=run_id,
                    strategy_id=meta.strategy_id,
                    strategy_version=meta.version,
                    chain_id=context.chain_id,
                    asset_id=context.asset_id,
                    pool_id=context.pool_id,
                    action=StrategyAction.INVALID,
                    confidence=0.0,
                    price_source="CORE_MARKET_SNAPSHOT",
                    suggested_size_raw=None,
                    suggested_size_human=None,
                    reasons=["STRATEGY_EXCEPTION", type(exc).__name__],
                    human_explanation=f"Strategy threw unhandled exception: {str(exc)}",
                    data_provenance={"exception_type": type(exc).__name__, "message": str(exc)},
                    input_timestamp=context.timestamp,
                    expires_at=now + timedelta(minutes=5),
                    created_at=now
                )
        finally:
            if not submitted:
                with self._lock:
                    self._in_flight = max(0, self._in_flight - 1)
                    self._active_runs.discard(run_id)

        # 3. Output size guard
        sig_bytes = len(signal.model_dump_json().encode("utf-8"))
        if sig_bytes > MAX_OUTPUT_PAYLOAD_BYTES:
            return StrategySignal(
                signal_id=f"sig_{uuid.uuid4().hex[:12]}",
                run_id=run_id,
                strategy_id=meta.strategy_id,
                strategy_version=meta.version,
                chain_id=context.chain_id,
                asset_id=context.asset_id,
                pool_id=context.pool_id,
                action=StrategyAction.INVALID,
                confidence=0.0,
                price_source="CORE_MARKET_SNAPSHOT",
                reasons=["OUTPUT_PAYLOAD_LIMIT_EXCEEDED"],
                human_explanation=f"Strategy output size ({sig_bytes} bytes) exceeded ceiling",
                data_provenance={"error": "OUTPUT_TOO_LARGE"},
                input_timestamp=context.timestamp,
                expires_at=now + timedelta(minutes=5),
                created_at=now
            )

        return signal
