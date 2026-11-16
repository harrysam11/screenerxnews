import os
import json
import sqlite3
import uuid
import time
import asyncio
from enum import Enum
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List, Tuple, Callable
from app.core.config import settings
from app.core.chains import SOLANA_CAIP2_CANONICAL, SOLANA_LEGACY_ALIAS, normalize_chain_id, to_caip2_chain_id
from app.core.network import OfflineModeBlockedException
from app.services.credential_vault import credential_vault, CredentialVaultService

class ProviderCapability(str, Enum):
    CHAIN_RPC = "CHAIN_RPC"
    MARKET_DATA = "MARKET_DATA"
    PRICE_LIQUIDITY = "PRICE_LIQUIDITY"
    TOKEN_METADATA = "TOKEN_METADATA"
    SECURITY_FORENSICS = "SECURITY_FORENSICS"
    MARKET_DISCOVERY = "MARKET_DISCOVERY"

class CircuitState(str, Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"

class ResponseClassification(str, Enum):
    SUCCESS = "SUCCESS"
    RATE_LIMIT = "RATE_LIMIT"
    SERVER_ERROR = "SERVER_ERROR"
    TIMEOUT = "TIMEOUT"
    AUTH_ERROR = "AUTH_ERROR"
    UNKNOWN_FAILURE = "UNKNOWN_FAILURE"

class ReservationStatus(str, Enum):
    RESERVED = "RESERVED"
    COMMITTED = "COMMITTED"
    RELEASED = "RELEASED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"

ALL_12_CHAINS = [
    "solana:mainnet",
    "eip155:1",
    "eip155:8453",
    "eip155:56",
    "eip155:42161",
    "eip155:10",
    "eip155:137",
    "eip155:43114",
    "sui:mainnet",
    "aptos:mainnet",
    "ton:mainnet",
    "tron:mainnet"
]

class ProviderManager:
    """
    User-Controlled Provider, RPC & Rate Limit Manager (Phase P10.2-C).
    Provides complete user sovereignty over external APIs, RPC endpoints across
    all 12 chains, request budgets, offline isolation, and explicitly authorized fallback.
    """

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or settings.get_canonical_sqlite_path()
        self.vault = credential_vault

    def _get_connection(self) -> sqlite3.Connection:
        if self.db_path == ":memory:":
            conn = sqlite3.connect("file:memecoin_prov_mem?mode=memory&cache=shared", uri=True, timeout=30.0, isolation_level=None)
        else:
            conn = sqlite3.connect(self.db_path, timeout=30.0, check_same_thread=False, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        if self.db_path != ":memory:":
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute("PRAGMA busy_timeout = 30000;")
        return conn

    # =========================================================================
    # 1. Operating Mode & Offline Isolation
    # =========================================================================

    def is_offline_mode(self) -> bool:
        """
        Determines whether the system is operating in strict offline mode.
        """
        return settings.is_offline or settings.OPERATING_MODE == "offline_research"

    def set_operating_mode(self, mode: str) -> str:
        """
        Dynamically changes operating mode (e.g. 'offline_research', 'online_research', 'paper_trading').
        """
        allowed = {"offline_research", "online_research", "paper_trading", "live_trading"}
        if mode not in allowed:
            raise ValueError(f"Invalid operating mode: {mode}. Must be one of {allowed}")
        if mode == "live_trading" and not settings.ALLOW_LIVE_TRADING:
            raise PermissionError("Live trading mode is prohibited when ALLOW_LIVE_TRADING=False")
        settings.OPERATING_MODE = mode
        return settings.OPERATING_MODE

    # =========================================================================
    # 2. Provider Management (Registration, Enable/Disable, Inspection)
    # =========================================================================

    def register_provider(
        self,
        provider_id: str,
        name: str,
        provider_type: str,
        tier: str = "free",
        is_enabled: bool = False,
        is_paid: bool = False,
        priority: int = 100,
        capabilities: Optional[List[str]] = None,
        chains: Optional[List[str]] = None,
        auth_type: str = "none",
        rate_limit_rpm: int = 60,
        daily_request_budget: int = 1000,
        daily_spend_budget_usd: float = 0.0,
        timeout_seconds: float = 10.0,
        max_retries: int = 3
    ) -> Dict[str, Any]:
        """
        Registers or updates a user-configured data or RPC provider.
        """
        caps = json.dumps(capabilities or [])
        chns = json.dumps(chains or ALL_12_CHAINS)
        now_str = datetime.now(timezone.utc).isoformat()

        with self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            try:
                conn.execute(
                    """
                    INSERT INTO providers (
                        id, name, provider_type, tier, is_enabled, is_paid, priority,
                        capabilities_json, chains_json, auth_type, rate_limit_rpm,
                        daily_request_budget, daily_spend_budget_usd, timeout_seconds,
                        max_retries, health_status, consecutive_failures, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'unknown', 0, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        name = excluded.name,
                        provider_type = excluded.provider_type,
                        tier = excluded.tier,
                        is_enabled = excluded.is_enabled,
                        is_paid = excluded.is_paid,
                        priority = excluded.priority,
                        capabilities_json = excluded.capabilities_json,
                        chains_json = excluded.chains_json,
                        auth_type = excluded.auth_type,
                        rate_limit_rpm = excluded.rate_limit_rpm,
                        daily_request_budget = excluded.daily_request_budget,
                        daily_spend_budget_usd = excluded.daily_spend_budget_usd,
                        timeout_seconds = excluded.timeout_seconds,
                        max_retries = excluded.max_retries,
                        updated_at = excluded.updated_at;
                    """,
                    (
                        provider_id, name, provider_type, tier, 1 if is_enabled else 0,
                        1 if is_paid else 0, priority, caps, chns, auth_type,
                        rate_limit_rpm, daily_request_budget, daily_spend_budget_usd,
                        timeout_seconds, max_retries, now_str, now_str
                    )
                )
                conn.execute("COMMIT;")
            except Exception as e:
                conn.execute("ROLLBACK;")
                raise e

        return self.get_provider(provider_id)  # type: ignore

    def get_provider(self, provider_id: str) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT * FROM providers WHERE id = ?;", (provider_id,))
            row = cursor.fetchone()
            if not row:
                return None
            res = dict(row)
            res["capabilities"] = json.loads(res.get("capabilities_json", "[]"))
            res["chains"] = json.loads(res.get("chains_json", "[]"))
            return res

    def list_providers(
        self,
        capability: Optional[str] = None,
        chain_id: Optional[str] = None,
        enabled_only: bool = False
    ) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            query = "SELECT * FROM providers WHERE 1=1"
            params: List[Any] = []
            if enabled_only:
                query += " AND is_enabled = 1"
            query += " ORDER BY priority ASC, name ASC;"

            cursor = conn.execute(query, params)
            providers = []
            for row in cursor.fetchall():
                p = dict(row)
                caps = json.loads(p.get("capabilities_json", "[]"))
                chns = json.loads(p.get("chains_json", "[]"))
                p["capabilities"] = caps
                p["chains"] = chns

                if capability and capability not in caps:
                    continue
                if chain_id:
                    norm = normalize_chain_id(chain_id)
                    caip2 = to_caip2_chain_id(chain_id)
                    norm_chns = [normalize_chain_id(c) for c in chns]
                    if chain_id not in chns and norm not in chns and caip2 not in chns and norm not in norm_chns:
                        continue
                providers.append(p)

            return providers

    def enable_provider(self, provider_id: str) -> bool:
        now_str = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cursor = conn.execute(
                "UPDATE providers SET is_enabled = 1, updated_at = ? WHERE id = ?;",
                (now_str, provider_id)
            )
            return cursor.rowcount > 0

    def disable_provider(self, provider_id: str) -> bool:
        now_str = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            try:
                cursor = conn.execute(
                    "UPDATE providers SET is_enabled = 0, updated_at = ? WHERE id = ?;",
                    (now_str, provider_id)
                )
                if cursor.rowcount > 0:
                    audit_id = f"audit-{uuid.uuid4().hex[:12]}"
                    conn.execute(
                        """
                        INSERT INTO provider_audit_log (id, event_type, provider_id, details_json, created_at)
                        VALUES (?, 'PROVIDER_DISABLED', ?, '{}', ?);
                        """,
                        (audit_id, provider_id, now_str)
                    )
                conn.execute("COMMIT;")
                return cursor.rowcount > 0
            except Exception as e:
                conn.execute("ROLLBACK;")
                raise e

    # =========================================================================
    # 3. RPC Endpoint Management (All 12 Chains)
    # =========================================================================

    def register_rpc_endpoint(
        self,
        endpoint_id: str,
        chain_id: str,
        name: str,
        endpoint_url: str,
        provider_id: Optional[str] = None,
        ws_url: Optional[str] = None,
        priority: int = 100,
        is_enabled: bool = False,
        is_paid: bool = False,
        daily_budget: int = 50000
    ) -> Dict[str, Any]:
        """
        Registers an RPC endpoint for any of the 12 supported chains.
        Sanitizes endpoint_url before saving.
        """
        canonical_chain_id = normalize_chain_id(chain_id)
        valid_chains = set(ALL_12_CHAINS) | {SOLANA_CAIP2_CANONICAL, "solana:mainnet"}
        if canonical_chain_id not in valid_chains and chain_id not in valid_chains:
            raise ValueError(f"Unknown chain_id: {chain_id}. Supported chains: {ALL_12_CHAINS}")


        sanitized_url = CredentialVaultService.sanitize_url(endpoint_url.strip())
        sanitized_ws = CredentialVaultService.sanitize_url(ws_url.strip()) if ws_url else None
        now_str = datetime.now(timezone.utc).isoformat()

        with self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            try:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO chain_registries (chain_id, chain_name, network_type, group_tier, is_active, rpc_url, native_symbol, decimals, created_at)
                    VALUES (?, ?, 'evm', 'B', 1, ?, 'NATIVE', 18, ?);
                    """,
                    (canonical_chain_id, name, sanitized_url, now_str)
                )
                conn.execute(
                    """
                    INSERT INTO chain_rpc_endpoints (
                        id, chain_id, provider_id, name, endpoint_url, ws_url,
                        priority, is_enabled, is_paid, daily_budget, health_status,
                        consecutive_failures, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'unknown', 0, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        chain_id = excluded.chain_id,
                        provider_id = excluded.provider_id,
                        name = excluded.name,
                        endpoint_url = excluded.endpoint_url,
                        ws_url = excluded.ws_url,
                        priority = excluded.priority,
                        is_enabled = excluded.is_enabled,
                        is_paid = excluded.is_paid,
                        daily_budget = excluded.daily_budget,
                        updated_at = excluded.updated_at;
                    """,
                    (
                        endpoint_id, canonical_chain_id, provider_id, name, sanitized_url,
                        sanitized_ws, priority, 1 if is_enabled else 0,
                        1 if is_paid else 0, daily_budget, now_str, now_str
                    )
                )
                conn.execute("COMMIT;")
            except Exception as e:
                conn.execute("ROLLBACK;")
                raise e

        return self.get_rpc_endpoint(endpoint_id)  # type: ignore

    def get_rpc_endpoint(self, endpoint_id: str) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT * FROM chain_rpc_endpoints WHERE id = ?;", (endpoint_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def list_rpc_endpoints(self, chain_id: Optional[str] = None, enabled_only: bool = False) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            query = "SELECT * FROM chain_rpc_endpoints WHERE 1=1"
            params: List[Any] = []
            if chain_id:
                norm_id = normalize_chain_id(chain_id)
                query += " AND (chain_id = ? OR chain_id = ?)"
                params.extend([norm_id, chain_id])
            if enabled_only:
                query += " AND is_enabled = 1"
            query += " ORDER BY chain_id ASC, priority ASC, name ASC;"

            cursor = conn.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]

    def enable_rpc_endpoint(self, endpoint_id: str) -> bool:
        now_str = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cursor = conn.execute(
                "UPDATE chain_rpc_endpoints SET is_enabled = 1, updated_at = ? WHERE id = ?;",
                (now_str, endpoint_id)
            )
            return cursor.rowcount > 0

    def disable_rpc_endpoint(self, endpoint_id: str) -> bool:
        now_str = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cursor = conn.execute(
                "UPDATE chain_rpc_endpoints SET is_enabled = 0, updated_at = ? WHERE id = ?;",
                (now_str, endpoint_id)
            )
            return cursor.rowcount > 0

    def get_active_rpc_endpoint(self, chain_id: str) -> Optional[str]:
        """
        Selects the highest-priority enabled, non-cooling RPC URL for the chain.
        Supports canonical CAIP-2 identifiers and legacy aliases.
        """
        norm_id = normalize_chain_id(chain_id)
        now_str = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT endpoint_url FROM chain_rpc_endpoints
                WHERE (chain_id = ? OR chain_id = ?) AND is_enabled = 1
                  AND (cooldown_until IS NULL OR cooldown_until <= ?)
                ORDER BY priority ASC, consecutive_failures ASC
                LIMIT 1;
                """,
                (norm_id, chain_id, now_str)
            )
            row = cursor.fetchone()
            return row["endpoint_url"] if row else None

    # =========================================================================
    # 4. Quotas, Daily Budgets & Rolling Usage Records
    # =========================================================================

    def check_budget_and_quota(
        self,
        provider_id: str,
        estimated_cost_usd: float = 0.0
    ) -> Tuple[bool, Optional[str]]:
        """
        Verifies that daily request and monetary spend budgets are not exceeded.
        """
        provider = self.get_provider(provider_id)
        if not provider:
            return False, "PROVIDER_NOT_FOUND"

        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT SUM(request_count) as total_requests, SUM(estimated_cost_usd) as total_spend
                FROM provider_usage_records
                WHERE provider_id = ? AND day_date = ?;
                """,
                (provider_id, today_str)
            )
            row = cursor.fetchone()
            total_requests = row["total_requests"] or 0
            total_spend = row["total_spend"] or 0.0

            # Sum committed requests and spend
            daily_req_budget = provider.get("daily_request_budget", 1000)
            daily_spend_budget = provider.get("daily_spend_budget_usd", 0.0)

            # Sum active reservations
            cur_res = conn.execute(
                """
                SELECT COUNT(*) as reserved_count, COALESCE(SUM(cost_usd), 0.0) as reserved_spend
                FROM provider_reservations
                WHERE provider_id = ? AND status = 'RESERVED' AND reserved_at >= ?;
                """,
                (provider_id, today_str)
            )
            res_row = cur_res.fetchone()
            reserved_reqs = res_row["reserved_count"] if res_row else 0
            reserved_spend = res_row["reserved_spend"] if res_row else 0.0

            if daily_req_budget > 0 and (total_requests + reserved_reqs) >= daily_req_budget:
                self._record_audit_event(
                    "BUDGET_EXCEEDED",
                    provider_id=provider_id,
                    details={"reason": "daily_request_budget", "used": total_requests + reserved_reqs, "budget": daily_req_budget}
                )
                return False, "DAILY_REQUEST_BUDGET_EXCEEDED"

            if daily_spend_budget > 0.0 and (total_spend + reserved_spend + estimated_cost_usd) > daily_spend_budget:
                self._record_audit_event(
                    "BUDGET_EXCEEDED",
                    provider_id=provider_id,
                    details={"reason": "daily_spend_budget", "used": total_spend + reserved_spend, "budget": daily_spend_budget}
                )
                return False, "DAILY_SPEND_BUDGET_EXCEEDED"

        return True, None

    def reserve_quota_and_budget(
        self,
        provider_id: str,
        capability: str,
        cost_usd: float = 0.0,
        chain_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        timeout_seconds: float = 300.0
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Atomic Quota & Spending Reservation (Phase P10.2-C.2 SEC-01).
        Guarantees that concurrent requests cannot exceed daily request quotas or monetary budgets.
        Executes under SQLite BEGIN IMMEDIATE; to serialize reservation attempts across coroutines/threads.
        Returns: (success: bool, reservation_id: Optional[str], error_code: Optional[str])
        """
        now = datetime.now(timezone.utc)
        now_str = now.isoformat()
        today_str = now.strftime("%Y-%m-%d")
        cutoff_str = (now - timedelta(seconds=timeout_seconds)).isoformat()
        res_id = f"res-{uuid.uuid4().hex[:12]}"

        with self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            try:
                # 1. Idempotency check
                if idempotency_key:
                    cur = conn.execute(
                        "SELECT id, status FROM provider_reservations WHERE idempotency_key = ?;",
                        (idempotency_key,)
                    )
                    existing = cur.fetchone()
                    if existing:
                        st = existing["status"]
                        if st in ("RESERVED", "COMMITTED"):
                            conn.execute("COMMIT;")
                            return True, existing["id"], None

                # 2. Expire stale pending reservations
                conn.execute(
                    """
                    UPDATE provider_reservations
                    SET status = 'EXPIRED', updated_at = ?
                    WHERE provider_id = ? AND status = 'RESERVED' AND reserved_at < ?;
                    """,
                    (now_str, provider_id, cutoff_str)
                )

                # 3. Read provider limits
                cur = conn.execute(
                    "SELECT daily_request_budget, daily_spend_budget_usd FROM providers WHERE id = ?;",
                    (provider_id,)
                )
                prov_row = cur.fetchone()
                if not prov_row:
                    conn.execute("ROLLBACK;")
                    return False, None, "PROVIDER_NOT_FOUND"

                daily_req_budget = prov_row["daily_request_budget"] or 1000
                daily_spend_budget = prov_row["daily_spend_budget_usd"] or 0.0

                # 4. Sum committed usage for today
                cur = conn.execute(
                    """
                    SELECT COALESCE(SUM(request_count), 0) as committed_requests,
                           COALESCE(SUM(estimated_cost_usd), 0.0) as committed_spend
                    FROM provider_usage_records
                    WHERE provider_id = ? AND day_date = ?;
                    """,
                    (provider_id, today_str)
                )
                usage_row = cur.fetchone()
                committed_reqs = usage_row["committed_requests"] if usage_row else 0
                committed_spend = usage_row["committed_spend"] if usage_row else 0.0

                # 5. Sum currently active in-flight reservations
                cur = conn.execute(
                    """
                    SELECT COUNT(*) as reserved_count,
                           COALESCE(SUM(cost_usd), 0.0) as reserved_spend
                    FROM provider_reservations
                    WHERE provider_id = ? AND status = 'RESERVED' AND reserved_at >= ?;
                    """,
                    (provider_id, today_str)
                )
                res_row = cur.fetchone()
                reserved_reqs = res_row["reserved_count"] if res_row else 0
                reserved_spend = res_row["reserved_spend"] if res_row else 0.0

                # Check request count constraint
                if daily_req_budget > 0 and (committed_reqs + reserved_reqs + 1) > daily_req_budget:
                    self._record_audit_event(
                        "BUDGET_EXCEEDED",
                        provider_id=provider_id,
                        chain_id=chain_id,
                        details={
                            "reason": "daily_request_budget",
                            "committed": committed_reqs,
                            "reserved": reserved_reqs,
                            "budget": daily_req_budget
                        },
                        conn=conn
                    )
                    conn.execute("COMMIT;")
                    return False, None, "DAILY_REQUEST_BUDGET_EXCEEDED"

                # Check monetary spend constraint
                if daily_spend_budget > 0.0 and (committed_spend + reserved_spend + cost_usd) > daily_spend_budget:
                    self._record_audit_event(
                        "BUDGET_EXCEEDED",
                        provider_id=provider_id,
                        chain_id=chain_id,
                        details={
                            "reason": "daily_spend_budget",
                            "committed": committed_spend,
                            "reserved": reserved_spend,
                            "attempted": cost_usd,
                            "budget": daily_spend_budget
                        },
                        conn=conn
                    )
                    conn.execute("COMMIT;")
                    return False, None, "DAILY_SPEND_BUDGET_EXCEEDED"

                # 6. Insert new reservation
                conn.execute(
                    """
                    INSERT INTO provider_reservations (
                        id, provider_id, capability, chain_id, cost_usd, status,
                        idempotency_key, reserved_at, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 'RESERVED', ?, ?, ?, ?);
                    """,
                    (res_id, provider_id, capability, chain_id, cost_usd, idempotency_key, now_str, now_str, now_str)
                )
                conn.execute("COMMIT;")
                return True, res_id, None
            except Exception as e:
                conn.execute("ROLLBACK;")
                raise e

    def commit_reservation(
        self,
        reservation_id: str,
        actual_cost_usd: Optional[float] = None
    ) -> None:
        """
        Transitions a reservation to COMMITTED and updates provider_usage_records under BEGIN IMMEDIATE;
        """
        now = datetime.now(timezone.utc)
        now_str = now.isoformat()
        today_str = now.strftime("%Y-%m-%d")

        with self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            try:
                cur = conn.execute("SELECT * FROM provider_reservations WHERE id = ?;", (reservation_id,))
                row = cur.fetchone()
                if not row:
                    conn.execute("ROLLBACK;")
                    return

                provider_id = row["provider_id"]
                capability = row["capability"]
                chain_key = row["chain_id"] or "global"
                final_cost = actual_cost_usd if actual_cost_usd is not None else row["cost_usd"]

                conn.execute(
                    """
                    UPDATE provider_reservations
                    SET status = 'COMMITTED', committed_at = ?, cost_usd = ?, updated_at = ?
                    WHERE id = ?;
                    """,
                    (now_str, final_cost, now_str, reservation_id)
                )

                rec_id = f"usg-{uuid.uuid4().hex[:12]}"
                conn.execute(
                    """
                    INSERT INTO provider_usage_records (
                        id, provider_id, chain_id, capability, day_date,
                        request_count, failed_count, estimated_cost_usd, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 1, 0, ?, ?, ?)
                    ON CONFLICT(provider_id, chain_id, capability, day_date) DO UPDATE SET
                        request_count = request_count + 1,
                        estimated_cost_usd = estimated_cost_usd + excluded.estimated_cost_usd,
                        updated_at = excluded.updated_at;
                    """,
                    (rec_id, provider_id, chain_key, capability, today_str, final_cost, now_str, now_str)
                )
                conn.execute("COMMIT;")
            except Exception as e:
                conn.execute("ROLLBACK;")
                raise e

    def release_reservation(self, reservation_id: str) -> None:
        """
        Transitions reservation to RELEASED without consuming request or spend quota.
        """
        now_str = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            try:
                conn.execute(
                    """
                    UPDATE provider_reservations
                    SET status = 'RELEASED', released_at = ?, updated_at = ?
                    WHERE id = ?;
                    """,
                    (now_str, now_str, reservation_id)
                )
                conn.execute("COMMIT;")
            except Exception as e:
                conn.execute("ROLLBACK;")
                raise e

    def fail_reservation(
        self,
        reservation_id: str,
        error_message: str,
        external_billable: bool = False,
        actual_cost_usd: Optional[float] = None
    ) -> None:
        """
        Transitions reservation to FAILED.
        If external_billable is True (conservative reconciliation), records usage in provider_usage_records.
        """
        now = datetime.now(timezone.utc)
        now_str = now.isoformat()
        today_str = now.strftime("%Y-%m-%d")

        with self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            try:
                cur = conn.execute("SELECT * FROM provider_reservations WHERE id = ?;", (reservation_id,))
                row = cur.fetchone()
                if not row:
                    conn.execute("ROLLBACK;")
                    return

                provider_id = row["provider_id"]
                capability = row["capability"]
                chain_key = row["chain_id"] or "global"
                final_cost = actual_cost_usd if actual_cost_usd is not None else row["cost_usd"]

                conn.execute(
                    """
                    UPDATE provider_reservations
                    SET status = 'FAILED', failed_at = ?, error_message = ?, updated_at = ?
                    WHERE id = ?;
                    """,
                    (now_str, error_message, now_str, reservation_id)
                )

                if external_billable:
                    rec_id = f"usg-{uuid.uuid4().hex[:12]}"
                    conn.execute(
                        """
                        INSERT INTO provider_usage_records (
                            id, provider_id, chain_id, capability, day_date,
                            request_count, failed_count, estimated_cost_usd, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, 1, 1, ?, ?, ?)
                        ON CONFLICT(provider_id, chain_id, capability, day_date) DO UPDATE SET
                            request_count = request_count + 1,
                            failed_count = failed_count + 1,
                            estimated_cost_usd = estimated_cost_usd + excluded.estimated_cost_usd,
                            updated_at = excluded.updated_at;
                        """,
                        (rec_id, provider_id, chain_key, capability, today_str, final_cost, now_str, now_str)
                    )

                conn.execute("COMMIT;")
            except Exception as e:
                conn.execute("ROLLBACK;")
                raise e

    def get_reservation(self, reservation_id: str) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            cur = conn.execute("SELECT * FROM provider_reservations WHERE id = ?;", (reservation_id,))
            row = cur.fetchone()
            return dict(row) if row else None

    def record_usage(
        self,
        provider_id: str,
        capability: str,
        chain_id: Optional[str] = None,
        success: bool = True,
        cost_usd: float = 0.0
    ):
        """
        Persists rolling request counts, failure counts, and spend into SQLite.
        """
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        now_str = datetime.now(timezone.utc).isoformat()
        rec_id = f"usg-{uuid.uuid4().hex[:12]}"
        chain_key = chain_id or "global"

        with self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            try:
                conn.execute(
                    """
                    INSERT INTO provider_usage_records (
                        id, provider_id, chain_id, capability, day_date,
                        request_count, failed_count, estimated_cost_usd, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?)
                    ON CONFLICT(provider_id, chain_id, capability, day_date) DO UPDATE SET
                        request_count = request_count + 1,
                        failed_count = failed_count + excluded.failed_count,
                        estimated_cost_usd = estimated_cost_usd + excluded.estimated_cost_usd,
                        updated_at = excluded.updated_at;
                    """,
                    (rec_id, provider_id, chain_key, capability, today_str, 0 if success else 1, cost_usd, now_str, now_str)
                )
                conn.execute("COMMIT;")
            except Exception as e:
                conn.execute("ROLLBACK;")
                raise e

    # =========================================================================
    # 5. Circuit Breakers, Consecutive Failures & Cooldowns
    # =========================================================================

    def record_failure(
        self,
        provider_id: str,
        error_message: str,
        is_rate_limit: bool = False,
        latency_ms: Optional[float] = None,
        response_type: Optional[str] = None
    ):
        """
        Increments consecutive failures and engages circuit breaker cooldown if threshold exceeded.
        Calculates bounded exponential backoff.
        """
        now = datetime.now(timezone.utc)
        now_str = now.isoformat()
        threshold = settings.PROVIDER_CIRCUIT_BREAKER_FAILURES

        with self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            try:
                cursor = conn.execute(
                    "SELECT consecutive_failures, circuit_state, cooldown_until FROM providers WHERE id = ?;",
                    (provider_id,)
                )
                row = cursor.fetchone()
                current_failures = (row["consecutive_failures"] if row else 0) + 1
                prev_circuit = row["circuit_state"] if row and "circuit_state" in row.keys() else "CLOSED"
                cooldown_val = row["cooldown_until"] if row and "cooldown_until" in row.keys() else None
                was_half_open = (prev_circuit == CircuitState.HALF_OPEN.value or (prev_circuit == CircuitState.OPEN.value and cooldown_val and now_str >= cooldown_val))

                base_cooldown = float(settings.PROVIDER_COOLDOWN_SECONDS)
                # Bounded exponential backoff: base * 2**(failures - 1), capped at 1800.0s (30m)
                exp_factor = 2 ** min(max(0, current_failures - 1), 6)
                cooldown_sec = min(1800.0, base_cooldown * exp_factor * (2.0 if is_rate_limit else 1.0))
                cooldown_until = (now + timedelta(seconds=cooldown_sec)).isoformat()

                if current_failures >= threshold or is_rate_limit or was_half_open:
                    conn.execute(
                        """
                        UPDATE providers
                        SET consecutive_failures = ?,
                            health_status = 'cooldown',
                            circuit_state = 'OPEN',
                            cooldown_until = ?,
                            last_failure_at = ?,
                            last_latency_ms = COALESCE(?, last_latency_ms),
                            updated_at = ?
                        WHERE id = ?;
                        """,
                        (current_failures, cooldown_until, now_str, latency_ms, now_str, provider_id)
                    )
                    self._record_audit_event(
                        "COOLDOWN_ACTIVATED",
                        provider_id=provider_id,
                        details={
                            "consecutive_failures": current_failures,
                            "is_rate_limit": is_rate_limit,
                            "cooldown_until": cooldown_until,
                            "cooldown_seconds": cooldown_sec,
                            "response_type": response_type,
                            "error": error_message
                        },
                        conn=conn
                    )
                else:
                    conn.execute(
                        """
                        UPDATE providers
                        SET consecutive_failures = ?,
                            health_status = 'degraded',
                            circuit_state = 'CLOSED',
                            last_failure_at = ?,
                            last_latency_ms = COALESCE(?, last_latency_ms),
                            updated_at = ?
                        WHERE id = ?;
                        """,
                        (current_failures, now_str, latency_ms, now_str, provider_id)
                    )
                conn.execute("COMMIT;")
            except Exception as e:
                conn.execute("ROLLBACK;")
                raise e

    def record_success(self, provider_id: str, latency_ms: Optional[float] = None):
        now_str = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            conn.execute(
                """
                UPDATE providers
                SET consecutive_failures = 0,
                    health_status = 'healthy',
                    circuit_state = 'CLOSED',
                    cooldown_until = NULL,
                    last_success_at = ?,
                    last_latency_ms = COALESCE(?, last_latency_ms),
                    updated_at = ?
                WHERE id = ?;
                """,
                (now_str, latency_ms, now_str, provider_id)
            )

    def get_circuit_state(self, provider_id: str) -> str:
        """
        Returns the effective circuit breaker state for a provider:
        - CLOSED: healthy, normal operations permitted
        - OPEN: tripping in progress, cooldown active, calls blocked
        - HALF_OPEN: cooldown expired, single probe request permitted
        """
        now_str = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cur = conn.execute(
                "SELECT circuit_state, cooldown_until FROM providers WHERE id = ?;",
                (provider_id,)
            )
            row = cur.fetchone()
            if not row:
                return CircuitState.CLOSED.value
            state = row["circuit_state"] or CircuitState.CLOSED.value
            cooldown = row["cooldown_until"]
            if state == CircuitState.OPEN.value:
                if cooldown and now_str >= cooldown:
                    return CircuitState.HALF_OPEN.value
                return CircuitState.OPEN.value
            elif state == CircuitState.HALF_OPEN.value:
                return CircuitState.HALF_OPEN.value
            return CircuitState.CLOSED.value

    def rotate_provider(
        self,
        capability: str,
        chain_id: Optional[str] = None,
        exclude_provider_ids: Optional[List[str]] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Finds the next best available, compatible provider for a capability/chain:
        - Must support capability and chain
        - Must be enabled
        - Excludes any in exclude_provider_ids
        - Excludes any whose effective circuit state is OPEN
        - Must have available daily request and spend quota
        """
        norm_chain = normalize_chain_id(chain_id) if chain_id else None
        excludes = set(exclude_provider_ids or [])
        candidates = self.list_providers(capability=capability, chain_id=norm_chain, enabled_only=True)

        for candidate in candidates:
            pid = candidate["id"]
            if pid in excludes:
                continue
            circuit = self.get_circuit_state(pid)
            if circuit == CircuitState.OPEN.value:
                continue
            within_budget, _ = self.check_budget_and_quota(pid, 0.0)
            if not within_budget:
                continue
            return candidate

        return None

    def assert_online(self, provider_id: Optional[str] = None) -> None:
        """
        Enforces strict offline isolation. Raises OfflineModeBlockedException
        if system is operating in offline mode.
        """
        if self.is_offline_mode():
            self._record_audit_event(
                "OFFLINE_BLOCKED",
                provider_id=provider_id,
                details={"message": "Network call blocked by strict offline mode"}
            )
            raise OfflineModeBlockedException("Outbound network calls are disabled in offline mode.")

    # =========================================================================
    # 6. Explicitly Authorized Fallback Rules
    # =========================================================================

    def add_fallback_rule(
        self,
        capability: str,
        primary_provider_id: str,
        fallback_provider_id: str,
        chain_id: Optional[str] = None,
        is_authorized: bool = True,
        priority: int = 10
    ) -> Dict[str, Any]:
        """
        Defines an authorized fallback route between two user-configured providers.
        """
        rule_id = f"rule-{uuid.uuid4().hex[:12]}"
        now_str = datetime.now(timezone.utc).isoformat()

        with self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            try:
                conn.execute(
                    """
                    INSERT INTO provider_fallback_rules (
                        id, capability, chain_id, primary_provider_id,
                        fallback_provider_id, is_authorized, priority, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (rule_id, capability, chain_id, primary_provider_id, fallback_provider_id, 1 if is_authorized else 0, priority, now_str)
                )
                conn.execute("COMMIT;")
            except Exception as e:
                conn.execute("ROLLBACK;")
                raise e

        return {
            "id": rule_id,
            "capability": capability,
            "chain_id": chain_id,
            "primary_provider_id": primary_provider_id,
            "fallback_provider_id": fallback_provider_id,
            "is_authorized": 1 if is_authorized else 0,
            "priority": priority,
            "created_at": now_str
        }

    def list_fallback_rules(self, capability: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            query = "SELECT * FROM provider_fallback_rules WHERE 1=1"
            params: List[Any] = []
            if capability:
                query += " AND capability = ?"
                params.append(capability)
            query += " ORDER BY priority ASC, created_at ASC;"
            cursor = conn.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]

    def get_authorized_fallback(
        self,
        primary_provider_id: str,
        capability: str,
        chain_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Finds the highest-priority authorized fallback provider for a failed or exhausted primary provider.
        Enforces strict rules:
        - Must be authorized (is_authorized = 1)
        - Fallback provider must be enabled (is_enabled = 1)
        - Fallback provider must support the capability & chain
        - Fallback provider must NOT be in active cooldown
        - Fallback provider must NOT exceed its budget
        - Budget exhaustion on primary NEVER silently invokes an unapproved paid fallback!
        """
        now_str = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT f.fallback_provider_id, p.*
                FROM provider_fallback_rules f
                JOIN providers p ON f.fallback_provider_id = p.id
                WHERE f.primary_provider_id = ?
                  AND f.capability = ?
                  AND (f.chain_id IS NULL OR f.chain_id = ?)
                  AND f.is_authorized = 1
                  AND p.is_enabled = 1
                  AND (p.cooldown_until IS NULL OR p.cooldown_until <= ?)
                ORDER BY f.priority ASC, p.priority ASC;
                """,
                (primary_provider_id, capability, chain_id or "", now_str)
            )
            for row in cursor.fetchall():
                p = dict(row)
                caps = json.loads(p.get("capabilities_json", "[]"))
                chns = json.loads(p.get("chains_json", "[]"))
                if capability not in caps:
                    continue
                if chain_id:
                    norm = normalize_chain_id(chain_id)
                    caip2 = to_caip2_chain_id(chain_id)
                    if chain_id not in chns and norm not in chns and caip2 not in chns:
                        continue

                # Verify budget on fallback candidate
                within_budget, _ = self.check_budget_and_quota(p["id"], 0.0)
                if not within_budget:
                    continue

                p["capabilities"] = caps
                p["chains"] = chns
                return p

        return None

    # =========================================================================
    # 7. Unified Dispatcher with Offline, Quota, Circuit Breaker & Fallback
    # =========================================================================

    async def execute_request(
        self,
        capability: str,
        chain_id: Optional[str] = None,
        preferred_provider_id: Optional[str] = None,
        handler_fn: Optional[Callable] = None,
        cost_usd: float = 0.0,
        idempotency_key: Optional[str] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Unified dispatch pipeline executing provider requests with atomic reservation safety (SEC-01):
        1. Strict offline isolation check (SEC-02)
        2. Canonical CAIP-2 chain identity normalization (SEC-03)
        3. Capability validation
        4. Provider eligibility & circuit-breaker cooldown check
        5. Atomic reservation of quota and budget under BEGIN IMMEDIATE;
        6. Authorized fallback with reservation transfer on cooldown/exhaustion
        7. Conservative failure reconciliation on unexpected network exceptions
        """
        canonical_chain_id = normalize_chain_id(chain_id) if chain_id else None

        # 1. Strict offline isolation check
        if self.is_offline_mode():
            self._record_audit_event(
                "OFFLINE_BLOCKED",
                chain_id=canonical_chain_id,
                details={"capability": capability, "message": "Outbound network blocked in offline mode"}
            )
            return {
                "status": "OFFLINE_BLOCKED",
                "success": False,
                "message": "Outbound network calls are disabled in offline mode.",
                "data": None
            }

        # 2. Capability validation
        valid_caps = {c.value for c in ProviderCapability}
        if capability not in valid_caps:
            return {
                "status": "UNSUPPORTED",
                "success": False,
                "message": f"Unsupported provider capability: {capability}",
                "data": None
            }

        # 3. Select primary provider candidate
        candidates = self.list_providers(capability=capability, chain_id=canonical_chain_id, enabled_only=True)
        primary: Optional[Dict[str, Any]] = None

        if preferred_provider_id:
            for c in candidates:
                if c["id"] == preferred_provider_id:
                    primary = c
                    break

        if not primary and candidates:
            primary = candidates[0]

        if not primary:
            return {
                "status": "NO_ELIGIBLE_PROVIDER",
                "success": False,
                "message": f"No enabled provider found supporting {capability} on {canonical_chain_id or 'any chain'}",
                "data": None
            }

        # 4. Check primary health and circuit breaker
        circuit_state = self.get_circuit_state(primary["id"])
        in_cooldown = (circuit_state == CircuitState.OPEN.value)

        # 5. Atomic reservation attempt on primary
        reserved = False
        res_id: Optional[str] = None
        budget_err: Optional[str] = None

        if not in_cooldown:
            reserved, res_id, budget_err = self.reserve_quota_and_budget(
                primary["id"], capability, cost_usd=cost_usd,
                chain_id=canonical_chain_id, idempotency_key=idempotency_key
            )

        # If primary cannot execute (cooldown or quota/budget exceeded), attempt authorized fallback
        if in_cooldown or not reserved:
            fallback = self.get_authorized_fallback(primary["id"], capability, canonical_chain_id)
            if not fallback:
                reason = "COOLDOWN_ACTIVE" if in_cooldown else budget_err
                return {
                    "status": "BUDGET_OR_COOLDOWN_BLOCKED",
                    "success": False,
                    "provider_id": primary["id"],
                    "message": f"Primary provider unavailable ({reason}) and no authorized fallback available.",
                    "data": None
                }

            # Attempt atomic reservation on fallback provider
            fb_reserved, fb_res_id, fb_err = self.reserve_quota_and_budget(
                fallback["id"], capability, cost_usd=cost_usd,
                chain_id=canonical_chain_id, idempotency_key=idempotency_key
            )
            if not fb_reserved:
                return {
                    "status": "BUDGET_OR_COOLDOWN_BLOCKED",
                    "success": False,
                    "provider_id": fallback["id"],
                    "message": f"Fallback provider unavailable ({fb_err}).",
                    "data": None
                }

            self._record_audit_event(
                "FALLBACK_TRIGGERED",
                provider_id=fallback["id"],
                chain_id=canonical_chain_id,
                details={
                    "primary_provider": primary["id"],
                    "fallback_provider": fallback["id"],
                    "capability": capability,
                    "reason": "primary_unavailable"
                }
            )
            return await self._dispatch_with_provider(
                fallback, capability, canonical_chain_id, handler_fn, cost_usd, fb_res_id, is_fallback=True, **kwargs
            )

        # Dispatch with primary provider under active atomic reservation
        return await self._dispatch_with_provider(
            primary, capability, canonical_chain_id, handler_fn, cost_usd, res_id, is_fallback=False, **kwargs
        )

    async def _dispatch_with_provider(
        self,
        provider: Dict[str, Any],
        capability: str,
        chain_id: Optional[str],
        handler_fn: Optional[Callable],
        cost_usd: float,
        reservation_id: Optional[str],
        is_fallback: bool,
        **kwargs
    ) -> Dict[str, Any]:
        provider_id = provider["id"]
        secret = self.vault.get_credential_secret(provider_id)

        # Resolve RPC endpoint if relevant
        rpc_url = None
        if chain_id:
            rpc_url = self.get_active_rpc_endpoint(chain_id)

        start_t = time.perf_counter()
        try:
            if handler_fn:
                if asyncio.iscoroutinefunction(handler_fn):
                    res = await handler_fn(provider_id=provider_id, secret=secret, rpc_url=rpc_url, **kwargs)
                else:
                    res = handler_fn(provider_id=provider_id, secret=secret, rpc_url=rpc_url, **kwargs)
            else:
                # Default simulated ping response
                res = {"ping": "pong", "provider": provider_id, "capability": capability, "chain_id": chain_id}

            latency_ms = (time.perf_counter() - start_t) * 1000.0
            if reservation_id:
                self.commit_reservation(reservation_id, actual_cost_usd=cost_usd)
            self.record_success(provider_id, latency_ms=latency_ms)

            return {
                "status": "FALLBACK_SUCCESS" if is_fallback else "SUCCESS",
                "success": True,
                "provider_id": provider_id,
                "reservation_id": reservation_id,
                "fallback_used": is_fallback,
                "latency_ms": latency_ms,
                "data": res
            }
        except OfflineModeBlockedException:
            if reservation_id:
                self.release_reservation(reservation_id)
            raise
        except Exception as primary_exc:
            latency_ms = (time.perf_counter() - start_t) * 1000.0
            is_billable = not isinstance(primary_exc, (ValueError, KeyError, PermissionError))
            if reservation_id:
                self.fail_reservation(reservation_id, error_message=str(primary_exc), external_billable=is_billable, actual_cost_usd=cost_usd)
            self.record_failure(provider_id, str(primary_exc), latency_ms=latency_ms)

            # Attempt authorized fallback after runtime failure
            if not is_fallback:
                fallback = self.get_authorized_fallback(provider_id, capability, chain_id)
                if fallback:
                    fb_reserved, fb_res_id, _ = self.reserve_quota_and_budget(
                        fallback["id"], capability, cost_usd=cost_usd, chain_id=chain_id
                    )
                    if fb_reserved:
                        self._record_audit_event(
                            "FALLBACK_TRIGGERED",
                            provider_id=fallback["id"],
                            chain_id=chain_id,
                            details={
                                "primary_provider": provider_id,
                                "fallback_provider": fallback["id"],
                                "capability": capability,
                                "reason": f"primary_failed: {str(primary_exc)}"
                            }
                        )
                        return await self._dispatch_with_provider(
                            fallback, capability, chain_id, handler_fn, cost_usd, fb_res_id, is_fallback=True, **kwargs
                        )

            return {
                "status": "FAILED",
                "success": False,
                "provider_id": provider_id,
                "reservation_id": reservation_id,
                "latency_ms": latency_ms,
                "message": f"Provider failed: {str(primary_exc)} and no authorized fallback configured.",
                "data": None
            }

    # =========================================================================
    # 8. Audit Log & Usage Queries
    # =========================================================================

    def _record_audit_event(
        self,
        event_type: str,
        provider_id: Optional[str] = None,
        chain_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        conn: Optional[sqlite3.Connection] = None
    ):
        now_str = datetime.now(timezone.utc).isoformat()
        audit_id = f"audit-{uuid.uuid4().hex[:12]}"
        details_str = json.dumps(CredentialVaultService.redact_dict(details or {}))

        if conn is not None:
            conn.execute(
                """
                INSERT INTO provider_audit_log (id, event_type, provider_id, chain_id, details_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?);
                """,
                (audit_id, event_type, provider_id, chain_id, details_str, now_str)
            )
        else:
            with self._get_connection() as active_conn:
                active_conn.execute(
                    """
                    INSERT INTO provider_audit_log (id, event_type, provider_id, chain_id, details_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?);
                    """,
                    (audit_id, event_type, provider_id, chain_id, details_str, now_str)
                )

    def get_audit_log(self, event_type: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            query = "SELECT * FROM provider_audit_log WHERE 1=1"
            params: List[Any] = []
            if event_type:
                query += " AND event_type = ?"
                params.append(event_type)
            query += " ORDER BY created_at DESC LIMIT ?;"
            params.append(limit)

            cursor = conn.execute(query, params)
            items = []
            for row in cursor.fetchall():
                it = dict(row)
                try:
                    it["details"] = json.loads(it.get("details_json", "{}"))
                except Exception:
                    it["details"] = {}
                items.append(it)
            return items

    def get_usage_summary(
        self,
        provider_id: Optional[str] = None,
        day_date: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            query = "SELECT * FROM provider_usage_records WHERE 1=1"
            params: List[Any] = []
            if provider_id:
                query += " AND provider_id = ?"
                params.append(provider_id)
            if day_date:
                query += " AND day_date = ?"
                params.append(day_date)
            query += " ORDER BY day_date DESC, provider_id ASC;"

            cursor = conn.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]

provider_manager = ProviderManager()
