"""
Phase P10.2-C: User-Controlled API, RPC & Provider Manager Test Suite.
Verifies acceptance criteria:
- PROV-01: User registration and prioritization of providers across capabilities.
- PROV-02: Multi-chain RPC management across all 12 registered blockchains.
- PROV-03: Local encrypted credential vault (zero plaintext in DB, secret redaction, URL sanitization).
- PROV-04: Daily request budget, spend ceiling, and rolling usage persistence.
- PROV-05: Circuit breaker and cooldown state transitions on consecutive failures.
- PROV-06: Explicitly authorized fallback with audit logging; unapproved paid fallback blocked.
- PROV-07: Strict offline isolation blocking outbound requests and logging audit events.
- PROV-08: SQLite restart persistence across separate connection sessions.
- PROV-09: REST API endpoints integration and mode control.
"""

import os
import tempfile
import sqlite3
import pytest
import asyncio
from datetime import datetime, timezone
from fastapi.testclient import TestClient

from app.main import app
from app.core.config import settings
from app.core.network import OfflineModeBlockedException
from app.core.chains import SOLANA_CAIP2_CANONICAL, SOLANA_LEGACY_ALIAS
from app.db.sqlite_migrator import SqliteMigrator
from app.db.sqlite_backup import SQLiteBackupService
from app.services.credential_vault import CredentialVaultService
from app.services.provider_manager import (
    ProviderManager,
    ProviderCapability,
    ReservationStatus,
    ALL_12_CHAINS
)
from app.services.dexscreener import dex_screener_service
from app.services.goplus import goplus_service
from app.services.rugcheck import rugcheck_service
from app.services.evm_rpc import EvmRpcClient
from app.services.solana_rpc import solana_rpc_service
from app.adapters.evm import EvmChainAdapter
from app.adapters.sui import SuiChainAdapter
from app.adapters.aptos import AptosChainAdapter
from app.adapters.ton import TonChainAdapter
from app.adapters.tron import TronChainAdapter

client = TestClient(app)

@pytest.fixture
def temp_migrated_db():
    """Provides a temporary, freshly-migrated SQLite database file."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name

    migrator = SqliteMigrator(db_path)
    migrator.apply_migrations()
    yield db_path

    if os.path.exists(db_path):
        os.remove(db_path)

# =============================================================================
# PROV-01: Provider Registration & Capability Matrix
# =============================================================================

def test_prov_01_provider_registration_and_capabilities(temp_migrated_db):
    pm = ProviderManager(temp_migrated_db)

    # Register custom provider
    created = pm.register_provider(
        provider_id="prov-test-custom",
        name="Custom Test Provider",
        provider_type="market_data",
        tier="free",
        is_enabled=True,
        is_paid=False,
        priority=5,
        capabilities=["MARKET_DATA", "PRICE_LIQUIDITY"],
        chains=["solana:mainnet", "eip155:1"],
        rate_limit_rpm=120,
        daily_request_budget=5000
    )

    assert created["id"] == "prov-test-custom"
    assert created["is_enabled"] == 1
    assert "MARKET_DATA" in created["capabilities"]
    assert "solana:mainnet" in created["chains"]

    # Filter by capability
    market_provs = pm.list_providers(capability="MARKET_DATA")
    assert any(p["id"] == "prov-test-custom" for p in market_provs)

    # Filter by chain
    eth_provs = pm.list_providers(chain_id="eip155:1")
    assert any(p["id"] == "prov-test-custom" for p in eth_provs)

    # Unsupported capability request returns explicit status
    async def run_unsupported():
        return await pm.execute_request(capability="UNKNOWN_CAPABILITY_XYZ")

    res = asyncio.run(run_unsupported())
    assert res["status"] == "UNSUPPORTED"
    assert res["success"] is False

# =============================================================================
# PROV-02: 12-Chain RPC Endpoints Coverage & Priority
# =============================================================================

def test_prov_02_all_12_chains_rpc_endpoints_and_priority(temp_migrated_db):
    pm = ProviderManager(temp_migrated_db)

    endpoints = pm.list_rpc_endpoints()
    registered_chains = {ep["chain_id"] for ep in endpoints}

    # Verify all 12 chains are represented in seeded migration
    for chain_id in ALL_12_CHAINS:
        assert chain_id in registered_chains, f"Chain {chain_id} missing from RPC endpoints"

    # Register higher-priority custom endpoint for Ethereum
    pm.register_rpc_endpoint(
        endpoint_id="rpc-eth-high-pri",
        chain_id="eip155:1",
        name="High Priority Custom Node",
        endpoint_url="https://eth-node.example.org",
        priority=1,
        is_enabled=True
    )

    pm.register_rpc_endpoint(
        endpoint_id="rpc-eth-low-pri",
        chain_id="eip155:1",
        name="Low Priority Backup Node",
        endpoint_url="https://eth-backup.example.org",
        priority=10,
        is_enabled=True
    )

    active_rpc = pm.get_active_rpc_endpoint("eip155:1")
    assert active_rpc == "https://eth-node.example.org"

# =============================================================================
# PROV-03: Credential Vault Encryption, Zero-Plaintext & Redaction
# =============================================================================

def test_prov_03_credential_vault_security(temp_migrated_db):
    pm = ProviderManager(temp_migrated_db)
    pm.register_provider(
        provider_id="prov-vault-test",
        name="Vault Test Provider",
        provider_type="market_data",
        capabilities=["MARKET_DATA"]
    )
    vault = CredentialVaultService(db_path=temp_migrated_db)

    raw_secret = "sk-live-supersecrettoken1234567890abcdef"
    stored = vault.store_credential("prov-vault-test", raw_secret, "api_key")

    assert stored["provider_id"] == "prov-vault-test"
    assert stored["redacted_preview"] == "sk-l...cdef"
    assert "supersecret" not in stored["redacted_preview"]

    # Verify zero plaintext in database file
    conn = sqlite3.connect(temp_migrated_db)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM provider_credentials WHERE id = ?;", (stored["id"],)).fetchone()
    conn.close()

    assert raw_secret not in row["encrypted_secret"]
    assert row["encrypted_secret"] != raw_secret
    assert row["redacted_preview"] == "sk-l...cdef"

    # Decrypt with authorized vault service
    decrypted = vault.get_credential_secret("prov-vault-test")
    assert decrypted == raw_secret

    # Verify URL sanitization
    dirty_url = "https://eth-mainnet.alchemy.com/v2/abcdef1234567890abcdef?key=secrettoken123"
    clean_url = vault.sanitize_url(dirty_url)
    assert "secrettoken123" not in clean_url
    assert "abcdef1234567890abcdef" not in clean_url
    assert clean_url == "https://eth-mainnet.alchemy.com/v2/[REDACTED]?key=[REDACTED]"

    # Revoke credential
    revoked = vault.revoke_credential(stored["id"])
    assert revoked is True
    assert vault.get_credential_secret("prov-vault-test") is None

# =============================================================================
# PROV-04: Daily Quotas, Spend Limits & Usage Tracking
# =============================================================================

def test_prov_04_daily_quotas_and_spend_limits(temp_migrated_db):
    pm = ProviderManager(temp_migrated_db)

    pm.register_provider(
        provider_id="prov-budget-limited",
        name="Budget Limited Provider",
        provider_type="market_data",
        tier="paid",
        is_enabled=True,
        is_paid=True,
        priority=1,
        capabilities=["MARKET_DATA"],
        daily_request_budget=2,
        daily_spend_budget_usd=1.00
    )

    # 1. First request succeeds
    res1 = asyncio.run(pm.execute_request("MARKET_DATA", preferred_provider_id="prov-budget-limited", cost_usd=0.40))
    assert res1["status"] == "SUCCESS"

    # 2. Second request succeeds
    res2 = asyncio.run(pm.execute_request("MARKET_DATA", preferred_provider_id="prov-budget-limited", cost_usd=0.40))
    assert res2["status"] == "SUCCESS"

    # 3. Third request blocked by daily request budget
    res3 = asyncio.run(pm.execute_request("MARKET_DATA", preferred_provider_id="prov-budget-limited", cost_usd=0.10))
    assert res3["status"] == "BUDGET_OR_COOLDOWN_BLOCKED"
    assert res3["success"] is False

    # Check audit log contains BUDGET_EXCEEDED event
    audits = pm.get_audit_log(event_type="BUDGET_EXCEEDED")
    assert len(audits) >= 1
    assert audits[0]["provider_id"] == "prov-budget-limited"

    # Verify usage records persisted
    summary = pm.get_usage_summary(provider_id="prov-budget-limited")
    assert len(summary) >= 1
    assert summary[0]["request_count"] == 2
    assert summary[0]["estimated_cost_usd"] == pytest.approx(0.80, 0.01)

# =============================================================================
# PROV-05: Circuit Breaker & Consecutive Failures Cooldown
# =============================================================================

def test_prov_05_circuit_breaker_and_cooldown(temp_migrated_db):
    pm = ProviderManager(temp_migrated_db)

    pm.register_provider(
        provider_id="prov-failing-node",
        name="Unstable Provider",
        provider_type="market_data",
        is_enabled=True,
        priority=1,
        capabilities=["MARKET_DATA"]
    )

    # Consecutive failures below threshold -> degraded
    pm.record_failure("prov-failing-node", "Network timeout 1")
    p = pm.get_provider("prov-failing-node")
    assert p["health_status"] == "degraded"
    assert p["consecutive_failures"] == 1

    pm.record_failure("prov-failing-node", "Network timeout 2")
    p = pm.get_provider("prov-failing-node")
    assert p["health_status"] == "degraded"
    assert p["consecutive_failures"] == 2

    # 3rd failure reaches threshold -> cooldown
    pm.record_failure("prov-failing-node", "Network timeout 3")
    p = pm.get_provider("prov-failing-node")
    assert p["health_status"] == "cooldown"
    assert p["consecutive_failures"] == 3
    assert p["cooldown_until"] is not None

    # Verify audit event
    cooldown_audits = pm.get_audit_log(event_type="COOLDOWN_ACTIVATED")
    assert len(cooldown_audits) >= 1
    assert cooldown_audits[0]["provider_id"] == "prov-failing-node"

    # Recovery on success resets circuit breaker
    pm.record_success("prov-failing-node")
    p_recovered = pm.get_provider("prov-failing-node")
    assert p_recovered["health_status"] == "healthy"
    assert p_recovered["consecutive_failures"] == 0
    assert p_recovered["cooldown_until"] is None

# =============================================================================
# PROV-06: Authorized Fallback & Unapproved Paid Fallback Blocked
# =============================================================================

def test_prov_06_authorized_fallback_dispatch(temp_migrated_db):
    pm = ProviderManager(temp_migrated_db)

    pm.register_provider(
        provider_id="prov-primary-free",
        name="Primary Free Data",
        provider_type="market_data",
        tier="free",
        is_enabled=True,
        priority=1,
        capabilities=["MARKET_DATA"]
    )

    pm.register_provider(
        provider_id="prov-backup-free",
        name="Backup Free Data",
        provider_type="market_data",
        tier="free",
        is_enabled=True,
        priority=10,
        capabilities=["MARKET_DATA"]
    )

    # Authorize fallback from primary to backup
    pm.add_fallback_rule(
        capability="MARKET_DATA",
        primary_provider_id="prov-primary-free",
        fallback_provider_id="prov-backup-free",
        is_authorized=True,
        priority=1
    )

    # Handler function that fails on primary and succeeds on backup
    async def handler(provider_id: str, **kwargs):
        if provider_id == "prov-primary-free":
            raise RuntimeError("Primary node connection dropped")
        return {"data": "backup_response"}

    res = asyncio.run(pm.execute_request(
        capability="MARKET_DATA",
        preferred_provider_id="prov-primary-free",
        handler_fn=handler
    ))

    assert res["status"] == "FALLBACK_SUCCESS"
    assert res["success"] is True
    assert res["provider_id"] == "prov-backup-free"
    assert res["fallback_used"] is True
    assert res["data"] == {"data": "backup_response"}

    # Audit log records fallback event
    fallbacks = pm.get_audit_log(event_type="FALLBACK_TRIGGERED")
    assert len(fallbacks) >= 1
    assert fallbacks[0]["provider_id"] == "prov-backup-free"

def test_prov_06_unapproved_paid_fallback_blocked(temp_migrated_db):
    pm = ProviderManager(temp_migrated_db)

    pm.register_provider(
        provider_id="prov-primary-failing",
        name="Primary Failing Provider",
        provider_type="market_data",
        tier="free",
        is_enabled=True,
        daily_request_budget=1,
        capabilities=["MARKET_DATA"]
    )

    pm.register_provider(
        provider_id="prov-expensive-paid",
        name="Expensive Paid API",
        provider_type="market_data",
        tier="paid",
        is_enabled=True,
        is_paid=True,
        capabilities=["MARKET_DATA"]
    )

    # Exhaust budget on primary
    pm.record_usage("prov-primary-failing", "MARKET_DATA", success=True)

    # Without explicit fallback authorization to the paid provider, fallback MUST NOT happen
    res = asyncio.run(pm.execute_request("MARKET_DATA", preferred_provider_id="prov-primary-failing"))
    assert res["status"] == "BUDGET_OR_COOLDOWN_BLOCKED"
    assert res["success"] is False

# =============================================================================
# PROV-07: Strict Offline Mode Network Isolation
# =============================================================================

def test_prov_07_offline_mode_isolation(temp_migrated_db):
    pm = ProviderManager(temp_migrated_db)

    # Put system in offline research mode
    pm.set_operating_mode("offline_research")
    assert pm.is_offline_mode() is True

    # Outbound request must be blocked immediately
    res = asyncio.run(pm.execute_request("MARKET_DATA"))
    assert res["status"] == "OFFLINE_BLOCKED"
    assert res["success"] is False

    # Check OFFLINE_BLOCKED audit log
    offline_audits = pm.get_audit_log(event_type="OFFLINE_BLOCKED")
    assert len(offline_audits) >= 1

    # Restore online mode
    pm.set_operating_mode("online_research")
    assert pm.is_offline_mode() is False

# =============================================================================
# PROV-08: Restart Persistence across SQLite Sessions
# =============================================================================

def test_prov_08_restart_persistence(temp_migrated_db):
    # Session 1: configure provider and credential
    pm1 = ProviderManager(temp_migrated_db)
    v1 = CredentialVaultService(db_path=temp_migrated_db)

    pm1.register_provider(
        provider_id="prov-persistent-01",
        name="Persistent Test Node",
        provider_type="rpc",
        tier="free",
        is_enabled=True,
        capabilities=["CHAIN_RPC"],
        chains=["solana:mainnet"]
    )
    v1.store_credential("prov-persistent-01", "my_persistent_rpc_token_12345", "token")
    pm1.record_usage("prov-persistent-01", "CHAIN_RPC", "solana:mainnet", success=True, cost_usd=0.05)

    del pm1
    del v1

    # Session 2: Fresh instances pointing to the same file
    pm2 = ProviderManager(temp_migrated_db)
    v2 = CredentialVaultService(db_path=temp_migrated_db)

    p = pm2.get_provider("prov-persistent-01")
    assert p is not None
    assert p["name"] == "Persistent Test Node"
    assert p["is_enabled"] == 1

    token = v2.get_credential_secret("prov-persistent-01", "token")
    assert token == "my_persistent_rpc_token_12345"

    usage = pm2.get_usage_summary(provider_id="prov-persistent-01")
    assert len(usage) == 1
    assert usage[0]["request_count"] == 1
    assert usage[0]["estimated_cost_usd"] == pytest.approx(0.05, 0.001)

# =============================================================================
# PROV-09: REST API Endpoints Integration
# =============================================================================

def test_prov_09_api_endpoints():
    # 1. List seeded providers
    resp = client.get("/api/v1/providers")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "SUCCESS"
    assert "providers" in data

    # 2. Register provider via API
    prov_payload = {
        "id": "prov-api-test-1",
        "name": "API Test Provider",
        "provider_type": "market_data",
        "tier": "free",
        "is_enabled": False,
        "is_paid": False,
        "priority": 50,
        "capabilities": ["MARKET_DATA", "MARKET_DISCOVERY"],
        "chains": ["solana:mainnet", "eip155:1"],
        "rate_limit_rpm": 60,
        "daily_request_budget": 1000
    }
    create_resp = client.post("/api/v1/providers", json=prov_payload)
    assert create_resp.status_code == 201
    assert create_resp.json()["provider"]["id"] == "prov-api-test-1"

    # 3. Enable provider
    en_resp = client.post("/api/v1/providers/prov-api-test-1/enable")
    assert en_resp.status_code == 200
    assert en_resp.json()["is_enabled"] is True

    # 4. Store encrypted credential via API
    cred_payload = {"secret": "sk-live-api-key-test-9999", "credential_type": "api_key"}
    cred_resp = client.post("/api/v1/providers/prov-api-test-1/credentials", json=cred_payload)
    assert cred_resp.status_code == 201
    cred_data = cred_resp.json()["credential"]
    assert "9999" in cred_data["redacted_preview"]
    assert "api-key-test" not in cred_data["redacted_preview"]

    # 5. List RPC endpoints
    rpc_resp = client.get("/api/v1/providers/rpc-endpoints/list")
    assert rpc_resp.status_code == 200
    assert len(rpc_resp.json()["endpoints"]) >= 12

    # 6. Mode status
    mode_resp = client.get("/api/v1/providers/mode/status")
    assert mode_resp.status_code == 200
    assert mode_resp.json()["status"] == "SUCCESS"

    # 7. Disallow live trading activation via API
    bad_mode = client.post("/api/v1/providers/mode/set", json={"mode": "live_trading"})
    assert bad_mode.status_code == 400

# =============================================================================
# SEC-01: Atomic Quota & Spending Reservation (Concurrency & Reconciliation)
# =============================================================================

def test_sec_01_atomic_quota_and_budget_reservation_concurrency(temp_migrated_db):
    pm = ProviderManager(temp_migrated_db)

    # Register provider with strict quota: exactly 5 requests allowed
    pm.register_provider(
        provider_id="prov-atomic-quota",
        name="Atomic Quota Provider",
        provider_type="market_data",
        tier="free",
        is_enabled=True,
        is_paid=False,
        priority=1,
        capabilities=["MARKET_DATA"],
        chains=["solana:mainnet"],
        daily_request_budget=5,
        daily_spend_budget_usd=100.0
    )

    async def hit_request(req_idx: int):
        await asyncio.sleep(0.005 * (req_idx % 3))
        return await pm.execute_request(
            capability="MARKET_DATA",
            chain_id="solana:mainnet",
            preferred_provider_id="prov-atomic-quota"
        )

    # Launch 10 concurrent requests simultaneously
    async def run_concurrent():
        tasks = [hit_request(i) for i in range(10)]
        return await asyncio.gather(*tasks)

    results = asyncio.run(run_concurrent())

    successful = [r for r in results if r.get("status") == "SUCCESS"]
    blocked = [r for r in results if r.get("status") == "BUDGET_OR_COOLDOWN_BLOCKED"]

    # Exactly 5 must succeed and exactly 5 must be blocked under race conditions
    assert len(successful) == 5
    assert len(blocked) == 5

    # Check committed usage in SQLite
    summary = pm.get_usage_summary(provider_id="prov-atomic-quota")
    assert len(summary) >= 1
    total_reqs = sum(s["request_count"] for s in summary)
    assert total_reqs == 5

    # Test concurrent monetary spending budget limit
    pm.register_provider(
        provider_id="prov-atomic-spend",
        name="Atomic Spend Provider",
        provider_type="market_data",
        tier="paid",
        is_enabled=True,
        is_paid=True,
        priority=1,
        capabilities=["MARKET_DATA"],
        chains=["solana:mainnet"],
        daily_request_budget=100,
        daily_spend_budget_usd=5.0
    )

    async def hit_spend(cost: float):
        return await pm.execute_request(
            capability="MARKET_DATA",
            chain_id="solana:mainnet",
            preferred_provider_id="prov-atomic-spend",
            cost_usd=cost
        )

    async def run_concurrent_spend():
        # 3 concurrent requests costing $3.0 each against a $5.0 ceiling
        tasks = [hit_spend(3.0) for _ in range(3)]
        return await asyncio.gather(*tasks)

    spend_results = asyncio.run(run_concurrent_spend())
    spend_success = [r for r in spend_results if r.get("status") == "SUCCESS"]
    spend_blocked = [r for r in spend_results if r.get("status") == "BUDGET_OR_COOLDOWN_BLOCKED"]

    # At most 1 request of $3.0 can fit into $5.0 budget
    assert len(spend_success) == 1
    assert len(spend_blocked) == 2

def test_sec_01_reservation_state_transitions_and_reconciliation(temp_migrated_db):
    pm = ProviderManager(temp_migrated_db)
    pm.register_provider(
        provider_id="prov-state-test",
        name="State Transition Provider",
        provider_type="market_data",
        tier="free",
        is_enabled=True,
        is_paid=False,
        priority=1,
        capabilities=["MARKET_DATA"],
        chains=["solana:mainnet"],
        daily_request_budget=10,
        daily_spend_budget_usd=50.0
    )

    # 1. Successful reservation
    ok, res_id, err = pm.reserve_quota_and_budget("prov-state-test", "MARKET_DATA", cost_usd=2.0)
    assert ok is True
    assert res_id is not None
    res = pm.get_reservation(res_id)
    assert res["status"] == "RESERVED"

    # 2. Idempotency key deduplication
    ok_idem, res_id_idem, _ = pm.reserve_quota_and_budget(
        "prov-state-test", "MARKET_DATA", cost_usd=2.0, idempotency_key="unique-key-123"
    )
    assert ok_idem is True
    ok_idem_dup, res_id_dup, _ = pm.reserve_quota_and_budget(
        "prov-state-test", "MARKET_DATA", cost_usd=2.0, idempotency_key="unique-key-123"
    )
    assert res_id_dup == res_id_idem

    # 3. Commit reservation
    pm.commit_reservation(res_id, actual_cost_usd=2.5)
    res_committed = pm.get_reservation(res_id)
    assert res_committed["status"] == "COMMITTED"
    assert res_committed["cost_usd"] == 2.5

    # 4. Release reservation (no quota consumed)
    ok_rel, res_rel_id, _ = pm.reserve_quota_and_budget("prov-state-test", "MARKET_DATA", cost_usd=1.0)
    pm.release_reservation(res_rel_id)
    res_released = pm.get_reservation(res_rel_id)
    assert res_released["status"] == "RELEASED"

    # 5. Failed reservation with conservative failure reconciliation (billable external failure)
    ok_fail, res_fail_id, _ = pm.reserve_quota_and_budget("prov-state-test", "MARKET_DATA", cost_usd=1.5)
    pm.fail_reservation(res_fail_id, error_message="Upstream 504 Gateway Timeout", external_billable=True, actual_cost_usd=1.5)
    res_failed = pm.get_reservation(res_fail_id)
    assert res_failed["status"] == "FAILED"
    assert "Timeout" in res_failed["error_message"]

# =============================================================================
# SEC-02: Global Offline Network Enforcement Across 100% of Outbound Paths
# =============================================================================

def test_sec_02_global_offline_mode_blocking_across_all_services():
    old_mode = settings.OPERATING_MODE
    try:
        settings.OPERATING_MODE = "offline_research"
        assert settings.is_offline is True

        # 1. DexScreener client blocked
        with pytest.raises(OfflineModeBlockedException):
            asyncio.run(dex_screener_service.get_tokens_batch(["DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"]))

        # 2. GoPlus security client blocked
        with pytest.raises(OfflineModeBlockedException):
            asyncio.run(goplus_service.get_token_security("DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"))

        # 3. RugCheck client blocked
        with pytest.raises(OfflineModeBlockedException):
            asyncio.run(rugcheck_service.get_token_report("DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"))

        # 4. EVM RPC client blocked
        with pytest.raises(OfflineModeBlockedException):
            asyncio.run(EvmRpcClient("eip155:1").call_rpc("eth_blockNumber", []))

        # 5. Solana RPC client blocked
        with pytest.raises(OfflineModeBlockedException):
            asyncio.run(solana_rpc_service.get_parsed_mint_info("DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"))

        # 6. Chain Adapters blocked
        adapters = [
            EvmChainAdapter("eip155:1", ["https://eth.llamarpc.com"]),
            SuiChainAdapter(),
            AptosChainAdapter(),
            TonChainAdapter(),
            TronChainAdapter()
        ]
        for adapter in adapters:
            with pytest.raises(OfflineModeBlockedException):
                asyncio.run(adapter.get_latest_block())

        # 7. Endpoint test API returns OFFLINE_BLOCKED without socket creation
        rpc_test_resp = client.post("/api/v1/providers/rpc-endpoints/rpc-solana-public/test")
        assert rpc_test_resp.status_code == 200
        assert rpc_test_resp.json()["status"] == "OFFLINE_BLOCKED"

    finally:
        settings.OPERATING_MODE = old_mode

# =============================================================================
# SEC-03: Canonical CAIP-2 Solana Identity & Compatibility Alias
# =============================================================================

def test_sec_03_canonical_caip2_solana_identity(temp_migrated_db):
    pm = ProviderManager(temp_migrated_db)

    # 1. Canonical constant value
    assert SOLANA_CAIP2_CANONICAL == "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"

    # 2. Register RPC endpoint with canonical CAIP-2 identifier
    ep = pm.register_rpc_endpoint(
        endpoint_id="rpc-sol-canonical",
        chain_id=SOLANA_CAIP2_CANONICAL,
        name="Solana Canonical CAIP-2 Endpoint",
        endpoint_url="https://api.mainnet-beta.solana.com",
        is_enabled=True,
        priority=1
    )
    assert ep["id"] == "rpc-sol-canonical"

    # 3. Resolve active endpoint using canonical identifier
    active_caip2 = pm.get_active_rpc_endpoint(SOLANA_CAIP2_CANONICAL)
    assert active_caip2 == "https://api.mainnet-beta.solana.com"

    # 4. Resolve active endpoint using legacy alias
    active_legacy = pm.get_active_rpc_endpoint(SOLANA_LEGACY_ALIAS)
    assert active_legacy == "https://api.mainnet-beta.solana.com"

    # 5. List RPC endpoints filtering by canonical identifier
    eps = pm.list_rpc_endpoints(chain_id=SOLANA_CAIP2_CANONICAL)
    assert any(e["id"] == "rpc-sol-canonical" for e in eps)

    # 6. Execute request using canonical identifier
    pm.register_provider(
        provider_id="prov-sol-caip2",
        name="Solana CAIP-2 Provider",
        provider_type="market_data",
        tier="free",
        is_enabled=True,
        capabilities=["MARKET_DATA"],
        chains=[SOLANA_CAIP2_CANONICAL]
    )
    res = asyncio.run(pm.execute_request(
        capability="MARKET_DATA",
        chain_id=SOLANA_CAIP2_CANONICAL,
        preferred_provider_id="prov-sol-caip2"
    ))
    assert res["status"] == "SUCCESS"

# =============================================================================
# SEC-04: Unique Persistent Cryptographic Salt & Legacy Migration
# =============================================================================

def test_sec_04_unique_persistent_salt_and_legacy_migration():
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = os.path.join(tmp_dir, "vault_test.db")
        salt_file = os.path.join(tmp_dir, ".vault_salt")
        meta_file = os.path.join(tmp_dir, ".vault_meta.json")

        # 1. Initialize vault and verify unique salt generation
        v1 = CredentialVaultService(db_path=db_path)
        assert os.path.exists(salt_file)
        assert os.path.exists(meta_file)

        # Permissions must be 0600
        file_mode = oct(os.stat(salt_file).st_mode & 0o777)
        assert file_mode == "0o600"

        with open(salt_file, "rb") as f:
            salt_bytes = f.read()
        assert len(salt_bytes) >= 16

        # Encrypt with new unique salt
        secret = "secret-super-classified-token-xyz"
        ciphertext = v1.encrypt(secret)
        assert v1.decrypt(ciphertext) == secret

        # 2. Restart vault in same directory - must reuse persistent salt
        v2 = CredentialVaultService(db_path=db_path)
        assert v2._salt == salt_bytes
        assert v2.decrypt(ciphertext) == secret

        # 3. Test backward-compatible decryption of legacy credential encrypted with default salt
        legacy_vault = CredentialVaultService(
            key=v1._raw_key,
            db_path=":memory:",
            salt=settings.CREDENTIAL_VAULT_SALT.encode("utf-8")
        )
        legacy_ciphertext = legacy_vault.encrypt("legacy-api-key-from-p10_2_c")

        # V1 (using new random salt) transparently decrypts legacy ciphertext via dual-salt fallback
        decrypted_legacy = v1.decrypt(legacy_ciphertext)
        assert decrypted_legacy == "legacy-api-key-from-p10_2_c"

# =============================================================================
# SEC-05: Vault Backup & Encrypted Recovery Package Procedure
# =============================================================================

def test_sec_05_vault_backup_and_recovery_package():
    with tempfile.TemporaryDirectory() as src_dir, tempfile.TemporaryDirectory() as restore_dir:
        db_path = os.path.join(src_dir, "source.db")
        pkg_path = os.path.join(src_dir, "recovery.vaultpkg")

        # Initialize vault in src_dir
        v = CredentialVaultService(db_path=db_path)
        ciphertext = v.encrypt("critical-production-private-key")

        # 1. Export encrypted recovery package
        passphrase = "StrictPassphrase!2026#Secure"
        exp_res = SQLiteBackupService.export_vault_recovery_package(
            passphrase=passphrase,
            export_path=pkg_path,
            vault_dir=src_dir
        )
        assert exp_res["status"] == "EXPORTED"
        assert os.path.exists(pkg_path)
        assert oct(os.stat(pkg_path).st_mode & 0o777) == "0o600"

        # 2. Decrypt and import into new target directory
        imp_res = SQLiteBackupService.import_vault_recovery_package(
            passphrase=passphrase,
            import_path=pkg_path,
            target_vault_dir=restore_dir
        )
        assert imp_res["status"] == "RESTORED"

        restored_key = os.path.join(restore_dir, ".vault_key")
        restored_salt = os.path.join(restore_dir, ".vault_salt")
        assert os.path.exists(restored_key)
        assert os.path.exists(restored_salt)
        assert oct(os.stat(restored_key).st_mode & 0o777) == "0o600"
        assert oct(os.stat(restored_salt).st_mode & 0o777) == "0o600"

        # 3. New vault instance in restored directory can decrypt original secret
        restored_db_path = os.path.join(restore_dir, "restored.db")
        v_restored = CredentialVaultService(db_path=restored_db_path)
        assert v_restored.decrypt(ciphertext) == "critical-production-private-key"

        # 4. Incorrect passphrase fails to decrypt recovery package
        with pytest.raises(Exception):
            SQLiteBackupService.import_vault_recovery_package(
                passphrase="WrongPassword!",
                import_path=pkg_path,
                target_vault_dir=tempfile.mkdtemp()
            )

# =============================================================================
# SEC-06: API Access Protection & Admin Token Enforcement
# =============================================================================

def test_sec_06_api_access_protection_and_token_enforcement(temp_migrated_db):
    old_token = settings.ADMIN_API_TOKEN
    try:
        # 1. When ADMIN_API_TOKEN is set, unauthenticated mutating requests must be rejected with 401
        settings.ADMIN_API_TOKEN = "super-secret-admin-token-999"

        # Without token
        unauth_resp = client.post("/api/v1/providers/prov-api-test-1/enable")
        assert unauth_resp.status_code == 401
        assert "Invalid or missing" in unauth_resp.json()["detail"]

        # With invalid token
        bad_token_resp = client.post(
            "/api/v1/providers/prov-api-test-1/enable",
            headers={"X-API-Key": "wrong-token"}
        )
        assert bad_token_resp.status_code == 401

        # With valid X-API-Key header
        auth_resp = client.post(
            "/api/v1/providers/prov-api-test-1/enable",
            headers={"X-API-Key": "super-secret-admin-token-999"}
        )
        assert auth_resp.status_code == 200

        # With valid Bearer Authorization header
        bearer_resp = client.post(
            "/api/v1/providers/prov-api-test-1/disable",
            headers={"Authorization": "Bearer super-secret-admin-token-999"}
        )
        assert bearer_resp.status_code == 200

    finally:
        settings.ADMIN_API_TOKEN = old_token
