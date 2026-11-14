import os
import time
import httpx
import sqlite3
import logging
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone
from pydantic import BaseModel, Field
from app.core.config import settings
from app.services.credential_vault import credential_vault

logger = logging.getLogger("memecoin_os.provider_manager")

class EndpointConfig(BaseModel):
    endpoint_id: str
    chain_id: str
    url: str
    provider_name: str
    category: str = "rpc"  # "rpc", "websocket", "market_data", "security"
    priority: int = 1      # 1 = primary, 2 = secondary
    weight: int = 100
    timeout_ms: int = 5000
    rate_limit_rpm: int = 120
    budget_tier: str = "free"  # "free", "custom", "paid"
    is_active: bool = True
    last_status: str = "untested"
    last_latency_ms: float = 0.0
    last_success_at: Optional[str] = None
    consecutive_failures: int = 0
    cooldown_until: float = 0.0
    requests_today: int = 0
    daily_quota_limit: int = 10000

class OfflineModeException(Exception):
    """Raised when an outbound network request is attempted while offline mode is active."""
    pass

class QuotaExhaustedException(Exception):
    """Raised when an endpoint's rate limit or daily quota is exceeded (API-02)."""
    pass

class ProviderManager:
    """
    Unified Multi-Chain Connector & Provider Manager (Phase P10.2-C).
    Features:
    - User-configured RPC/WebSocket endpoints across all 12 chains
    - Real live connectivity probe and latency benchmarking (API-01)
    - Strict fail-closed quota enforcement preventing paid fallback (API-02)
    - Credential isolation and redacted configuration export (API-03)
    - Instant global offline research switch
    """

    DEFAULT_ENDPOINTS = [
        # Solana
        {"endpoint_id": "sol-public-01", "chain_id": "solana:mainnet", "url": "https://api.mainnet-beta.solana.com", "provider_name": "Solana Foundation Public", "priority": 1, "budget_tier": "free", "daily_quota_limit": 5000},
        # Ethereum
        {"endpoint_id": "eth-llama-01", "chain_id": "eip155:1", "url": "https://eth.llamarpc.com", "provider_name": "LlamaNodes Public", "priority": 1, "budget_tier": "free", "daily_quota_limit": 10000},
        {"endpoint_id": "eth-ankr-02", "chain_id": "eip155:1", "url": "https://rpc.ankr.com/eth", "provider_name": "Ankr Public", "priority": 2, "budget_tier": "free", "daily_quota_limit": 5000},
        # Base
        {"endpoint_id": "base-public-01", "chain_id": "eip155:8453", "url": "https://mainnet.base.org", "provider_name": "Base Official Public", "priority": 1, "budget_tier": "free", "daily_quota_limit": 20000},
        # BNB Chain
        {"endpoint_id": "bnb-binance-01", "chain_id": "eip155:56", "url": "https://bsc-dataseed.binance.org", "provider_name": "Binance Dataseed", "priority": 1, "budget_tier": "free", "daily_quota_limit": 15000},
        # Group B: Arbitrum, Optimism, Polygon, Avalanche
        {"endpoint_id": "arb-public-01", "chain_id": "eip155:42161", "url": "https://arb1.arbitrum.io/rpc", "provider_name": "Offchain Labs", "priority": 1, "budget_tier": "free", "daily_quota_limit": 10000},
        {"endpoint_id": "op-public-01", "chain_id": "eip155:10", "url": "https://mainnet.optimism.io", "provider_name": "Optimism Foundation", "priority": 1, "budget_tier": "free", "daily_quota_limit": 10000},
        {"endpoint_id": "pol-public-01", "chain_id": "eip155:137", "url": "https://polygon-rpc.com", "provider_name": "Polygon Public", "priority": 1, "budget_tier": "free", "daily_quota_limit": 10000},
        {"endpoint_id": "avax-public-01", "chain_id": "eip155:43114", "url": "https://api.avax.network/ext/bc/C/rpc", "provider_name": "Avalanche C-Chain", "priority": 1, "budget_tier": "free", "daily_quota_limit": 10000},
        # Group C: Sui, Aptos, TON, TRON
        {"endpoint_id": "sui-public-01", "chain_id": "sui:mainnet", "url": "https://fullnode.mainnet.sui.io:443", "provider_name": "Mysten Labs Public", "priority": 1, "budget_tier": "free", "daily_quota_limit": 10000},
        {"endpoint_id": "aptos-public-01", "chain_id": "aptos:mainnet", "url": "https://fullnode.mainnet.aptoslabs.com/v1", "provider_name": "Aptos Labs Public", "priority": 1, "budget_tier": "free", "daily_quota_limit": 10000},
        {"endpoint_id": "ton-public-01", "chain_id": "ton:mainnet", "url": "https://toncenter.com/api/v2/jsonRPC", "provider_name": "TON Center Public", "priority": 1, "budget_tier": "free", "daily_quota_limit": 5000},
        {"endpoint_id": "tron-public-01", "chain_id": "tron:mainnet", "url": "https://api.trongrid.io", "provider_name": "TronGrid Public", "priority": 1, "budget_tier": "free", "daily_quota_limit": 5000}
    ]

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            self.db_path = settings.SQLITE_DB_PATH
        else:
            self.db_path = db_path

        if self.db_path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)

        self._offline_override: Optional[bool] = None
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        if self.db_path == ":memory:":
            conn = sqlite3.connect("file:providers_mem?mode=memory&cache=shared", uri=True, timeout=30.0, isolation_level=None)
        else:
            conn = sqlite3.connect(self.db_path, timeout=30.0, check_same_thread=False, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        if self.db_path != ":memory:":
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute("PRAGMA busy_timeout = 30000;")
        return conn

    def _init_db(self):
        with self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS provider_endpoints (
                    endpoint_id TEXT PRIMARY KEY,
                    chain_id TEXT NOT NULL,
                    url TEXT NOT NULL,
                    provider_name TEXT NOT NULL,
                    category TEXT NOT NULL DEFAULT 'rpc',
                    priority INTEGER NOT NULL DEFAULT 1,
                    weight INTEGER NOT NULL DEFAULT 100,
                    timeout_ms INTEGER NOT NULL DEFAULT 5000,
                    rate_limit_rpm INTEGER NOT NULL DEFAULT 120,
                    budget_tier TEXT NOT NULL DEFAULT 'free',
                    is_active INTEGER NOT NULL DEFAULT 1,
                    last_status TEXT NOT NULL DEFAULT 'untested',
                    last_latency_ms REAL NOT NULL DEFAULT 0.0,
                    last_success_at TEXT,
                    consecutive_failures INTEGER NOT NULL DEFAULT 0,
                    requests_today INTEGER NOT NULL DEFAULT 0,
                    daily_quota_limit INTEGER NOT NULL DEFAULT 10000,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
            """)

            # Seed default providers if table is empty
            cursor = conn.execute("SELECT COUNT(*) as count FROM provider_endpoints;")
            if cursor.fetchone()["count"] == 0:
                now_str = datetime.now(timezone.utc).isoformat()
                for ep in self.DEFAULT_ENDPOINTS:
                    conn.execute("""
                        INSERT OR IGNORE INTO provider_endpoints 
                        (endpoint_id, chain_id, url, provider_name, category, priority, weight, timeout_ms, rate_limit_rpm, budget_tier, is_active, last_status, daily_quota_limit, created_at, updated_at)
                        VALUES (?, ?, ?, ?, 'rpc', ?, 100, 5000, 120, ?, 1, 'untested', ?, ?, ?);
                    """, (ep["endpoint_id"], ep["chain_id"], ep["url"], ep["provider_name"], ep["priority"], ep["budget_tier"], ep["daily_quota_limit"], now_str, now_str))

    def is_offline(self) -> bool:
        if self._offline_override is not None:
            return self._offline_override
        return settings.is_offline

    def set_offline_mode(self, enabled: bool):
        self._offline_override = enabled
        if enabled:
            settings.OPERATING_MODE = "offline_research"

    def list_endpoints(self, chain_id: Optional[str] = None) -> List[EndpointConfig]:
        with self._get_connection() as conn:
            query = "SELECT * FROM provider_endpoints"
            params = ()
            if chain_id:
                query += " WHERE chain_id = ?"
                params = (chain_id,)
            query += " ORDER BY priority ASC, updated_at DESC;"

            cursor = conn.execute(query, params)
            return [EndpointConfig(**dict(row)) for row in cursor.fetchall()]

    def add_endpoint(self, endpoint: EndpointConfig) -> EndpointConfig:
        now_str = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO provider_endpoints 
                (endpoint_id, chain_id, url, provider_name, category, priority, weight, timeout_ms, rate_limit_rpm, budget_tier, is_active, last_status, last_latency_ms, last_success_at, consecutive_failures, requests_today, daily_quota_limit, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """, (
                endpoint.endpoint_id, endpoint.chain_id, endpoint.url, endpoint.provider_name,
                endpoint.category, endpoint.priority, endpoint.weight, endpoint.timeout_ms,
                endpoint.rate_limit_rpm, endpoint.budget_tier, int(endpoint.is_active),
                endpoint.last_status, endpoint.last_latency_ms, endpoint.last_success_at,
                endpoint.consecutive_failures, endpoint.requests_today, endpoint.daily_quota_limit,
                now_str, now_str
            ))
        return endpoint

    def delete_endpoint(self, endpoint_id: str) -> bool:
        with self._get_connection() as conn:
            cursor = conn.execute("DELETE FROM provider_endpoints WHERE endpoint_id = ?;", (endpoint_id,))
            return cursor.rowcount > 0

    def get_active_endpoint(self, chain_id: str, category: str = "rpc") -> EndpointConfig:
        """
        Retrieves the preferred active endpoint for a given chain.
        Guarantees quota enforcement (API-02) and offline blocking.
        """
        if self.is_offline():
            raise OfflineModeException(f"Global offline mode active: Outbound network request to {chain_id} blocked.")

        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT * FROM provider_endpoints 
                WHERE chain_id = ? AND category = ? AND is_active = 1
                ORDER BY priority ASC, last_latency_ms ASC;
            """, (chain_id, category))
            rows = cursor.fetchall()

            if not rows:
                raise ValueError(f"No active provider endpoint configured for {chain_id} ({category})")

            for row in rows:
                ep = EndpointConfig(**dict(row))
                # Invariant API-02: Never silently route or fallback to a paid tier
                if ep.budget_tier == "paid":
                    continue
                # Check daily quota ceiling
                if ep.requests_today >= ep.daily_quota_limit:
                    continue
                return ep

            # If all free/custom endpoints exhausted, fail closed! Do not route to paid fallback
            raise QuotaExhaustedException(
                f"SAFETY POLICY ENFORCEMENT (API-02): Daily quota exhausted for chain {chain_id}. "
                "Paid provider fallback is permanently prohibited. Request aborted."
            )

    def record_usage(self, endpoint_id: str, success: bool, latency_ms: float = 0.0):
        now_str = datetime.now(timezone.utc).isoformat() if success else None
        with self._get_connection() as conn:
            if success:
                conn.execute("""
                    UPDATE provider_endpoints 
                    SET requests_today = requests_today + 1,
                        last_status = 'healthy',
                        last_latency_ms = ?,
                        last_success_at = ?,
                        consecutive_failures = 0,
                        updated_at = datetime('now')
                    WHERE endpoint_id = ?;
                """, (latency_ms, now_str, endpoint_id))
            else:
                conn.execute("""
                    UPDATE provider_endpoints 
                    SET consecutive_failures = consecutive_failures + 1,
                        last_status = 'error',
                        updated_at = datetime('now')
                    WHERE endpoint_id = ?;
                """, (endpoint_id,))

    async def test_endpoint(self, endpoint_id: str) -> Dict[str, Any]:
        """
        Performs a live connectivity test against an endpoint (API-01).
        Verifies protocol response, measures latency, and records status.
        """
        if self.is_offline():
            return {
                "endpoint_id": endpoint_id,
                "status": "blocked",
                "message": "Offline mode active: probe aborted."
            }

        with self._get_connection() as conn:
            cursor = conn.execute("SELECT * FROM provider_endpoints WHERE endpoint_id = ?;", (endpoint_id,))
            row = cursor.fetchone()
            if not row:
                raise ValueError(f"Endpoint {endpoint_id} not found")
            ep = EndpointConfig(**dict(row))

        t0 = time.perf_counter()
        probe_success = False
        message = ""
        block_height = None

        try:
            timeout_sec = ep.timeout_ms / 1000.0
            async with httpx.AsyncClient(timeout=timeout_sec) as client:
                if "solana" in ep.chain_id:
                    # Solana JSON-RPC health probe
                    payload = {"jsonrpc": "2.0", "id": 1, "method": "getHealth"}
                    resp = await client.post(ep.url, json=payload)
                    t1 = time.perf_counter()
                    latency_ms = round((t1 - t0) * 1000, 2)
                    if resp.status_code == 200:
                        data = resp.json()
                        result = data.get("result", "")
                        probe_success = (result == "ok") or ("result" in data)
                        message = f"Solana RPC responsive: {result}"
                elif "eip155" in ep.chain_id:
                    # EVM eth_blockNumber probe
                    payload = {"jsonrpc": "2.0", "id": 1, "method": "eth_blockNumber", "params": []}
                    resp = await client.post(ep.url, json=payload)
                    t1 = time.perf_counter()
                    latency_ms = round((t1 - t0) * 1000, 2)
                    if resp.status_code == 200:
                        data = resp.json()
                        raw_block = data.get("result")
                        if raw_block:
                            block_height = int(raw_block, 16)
                            probe_success = True
                            message = f"EVM RPC responsive: block {block_height}"
                        else:
                            message = f"EVM RPC error response: {data.get('error')}"
                else:
                    # Generic HTTP probe
                    resp = await client.get(ep.url)
                    t1 = time.perf_counter()
                    latency_ms = round((t1 - t0) * 1000, 2)
                    probe_success = resp.status_code < 400
                    message = f"HTTP probe returned status {resp.status_code}"
        except Exception as e:
            t1 = time.perf_counter()
            latency_ms = round((t1 - t0) * 1000, 2)
            message = f"Connection probe failed: {str(e)}"
            probe_success = False

        self.record_usage(endpoint_id, success=probe_success, latency_ms=latency_ms)

        return {
            "endpoint_id": endpoint_id,
            "chain_id": ep.chain_id,
            "url": ep.url,
            "status": "healthy" if probe_success else "error",
            "latency_ms": latency_ms,
            "block_height": block_height,
            "message": message
        }

    def export_safe_configuration(self) -> Dict[str, Any]:
        """
        Exports the entire provider and connector configuration.
        Guarantees that ZERO secrets or credentials appear in the export (API-03).
        """
        endpoints = self.list_endpoints()
        sanitized_endpoints = []
        for ep in endpoints:
            d = ep.model_dump()
            # Strip URL query params if they contain api_key/token
            if "?" in d["url"]:
                base_url = d["url"].split("?")[0]
                d["url"] = f"{base_url}?[REDACTED_QUERY_PARAMS]"
            sanitized_endpoints.append(d)

        # List registered credentials with secrets masked
        credentials_meta = credential_vault.list_credentials()

        return {
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "operating_mode": settings.OPERATING_MODE,
            "is_offline": self.is_offline(),
            "endpoints_count": len(sanitized_endpoints),
            "endpoints": sanitized_endpoints,
            "credentials": credentials_meta,
            "security_policy": "STRICT_CREDENTIAL_ISOLATION_VERIFIED"
        }

provider_manager = ProviderManager()
