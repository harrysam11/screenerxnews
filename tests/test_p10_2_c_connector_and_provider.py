"""
Phase P10.2-C: Connector and Provider Manager Test Suite
Verifies acceptance criteria:
- API-01: User can configure and test custom RPC
- API-02: Quota exhaustion never causes paid fallback
- API-03: Credentials never appear in logs or exports
- OFFLINE-01: Global offline mode switch blocks outbound traffic fail-closed
"""

import os
import sys
import tempfile
import sqlite3
import pytest
from decimal import Decimal
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath("./backend"))
from app.main import app
from app.services.provider_manager import (
    ProviderManager, EndpointConfig, OfflineModeException, QuotaExhaustedException
)
from app.services.credential_vault import CredentialVault

client = TestClient(app)

# ==============================================================================
# API-01: User can configure and test custom RPC
# ==============================================================================

def test_api_01_user_can_configure_and_test_custom_rpc():
    """
    API-01: Verifies that a user can add a custom RPC endpoint with custom priority
    and test connectivity with latency and health status recording.
    """
    with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
        pm = ProviderManager(db_path=tmp.name)

        # 1. Add user custom RPC for Solana with priority 1 (overriding default)
        custom_endpoint = EndpointConfig(
            endpoint_id="user-solana-custom-01",
            chain_id="solana:mainnet",
            url="https://api.mainnet-beta.solana.com",
            provider_name="My Private Solana Node",
            priority=1,
            timeout_ms=3000,
            rate_limit_rpm=300,
            budget_tier="custom",
            daily_quota_limit=50000
        )
        added = pm.add_endpoint(custom_endpoint)
        assert added.endpoint_id == "user-solana-custom-01"

        # Verify endpoint persists in SQLite
        endpoints = pm.list_endpoints(chain_id="solana:mainnet")
        endpoint_ids = [e.endpoint_id for e in endpoints]
        assert "user-solana-custom-01" in endpoint_ids

        # 2. Test live connectivity probe
        import asyncio
        test_result = asyncio.run(pm.test_endpoint("user-solana-custom-01"))
        assert test_result["endpoint_id"] == "user-solana-custom-01"
        assert test_result["status"] in ["healthy", "error"]  # Network probe executed
        assert "latency_ms" in test_result

        # 3. Test via FastAPI HTTP endpoint
        add_payload = {
            "endpoint_id": "api-test-base-01",
            "chain_id": "eip155:8453",
            "url": "https://mainnet.base.org",
            "provider_name": "My Custom Base RPC",
            "priority": 1,
            "weight": 100,
            "timeout_ms": 4000,
            "rate_limit_rpm": 200,
            "budget_tier": "custom",
            "daily_quota_limit": 25000
        }
        res = client.post("/api/v1/providers/endpoint", json=add_payload)
        assert res.status_code == 200
        data = res.json()
        assert data["endpoint_id"] == "api-test-base-01"
        assert data["chain_id"] == "eip155:8453"

        # Probe via HTTP test endpoint
        probe_res = client.post("/api/v1/providers/test/api-test-base-01")
        assert probe_res.status_code == 200
        probe_data = probe_res.json()
        assert probe_data["endpoint_id"] == "api-test-base-01"
        assert "latency_ms" in probe_data

# ==============================================================================
# API-02: Quota exhaustion never causes paid fallback
# ==============================================================================

def test_api_02_quota_exhaustion_never_causes_paid_fallback():
    """
    API-02: Verifies that when a free or custom endpoint's quota is exhausted,
    the system strictly fails closed and NEVER silently routes traffic to a paid provider.
    """
    with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
        pm = ProviderManager(db_path=tmp.name)

        # Clear existing default endpoints for eip155:1 to test isolated fallback policy
        with pm._get_connection() as conn:
            conn.execute("UPDATE provider_endpoints SET is_active = 0 WHERE chain_id = 'eip155:1';")

        # Register a free endpoint with a tiny quota limit of 2 requests
        pm.add_endpoint(EndpointConfig(
            endpoint_id="limited-free-eth",
            chain_id="eip155:1",
            url="https://eth.llamarpc.com",
            provider_name="Free Llama Tier",
            priority=1,
            budget_tier="free",
            daily_quota_limit=2,
            requests_today=0,
            is_active=True
        ))

        # Register an active paid endpoint (which must NEVER be automatically invoked as fallback)
        pm.add_endpoint(EndpointConfig(
            endpoint_id="expensive-paid-eth",
            chain_id="eip155:1",
            url="https://expensive-paid-provider.io/rpc",
            provider_name="Paid Enterprise Tier",
            priority=2,
            budget_tier="paid",
            daily_quota_limit=1000000,
            requests_today=0,
            is_active=True
        ))

        # Request 1: Quota available
        ep1 = pm.get_active_endpoint("eip155:1")
        assert ep1.endpoint_id == "limited-free-eth"
        pm.record_usage("limited-free-eth", success=True)

        # Request 2: Quota available
        ep2 = pm.get_active_endpoint("eip155:1")
        assert ep2.endpoint_id == "limited-free-eth"
        pm.record_usage("limited-free-eth", success=True)

        # Request 3: Free quota exhausted! Even though 'expensive-paid-eth' is active,
        # system MUST NOT route to paid tier. It must raise QuotaExhaustedException fail-closed!
        with pytest.raises(QuotaExhaustedException) as exc_info:
            pm.get_active_endpoint("eip155:1")

        assert "SAFETY POLICY ENFORCEMENT (API-02)" in str(exc_info.value)
        assert "Paid provider fallback is permanently prohibited" in str(exc_info.value)

# ==============================================================================
# API-03: Credentials never appear in logs or exports
# ==============================================================================

def test_api_03_credentials_never_appear_in_logs_or_exports():
    """
    API-03: Verifies that sensitive credentials:
    1. Are encrypted with AES-256-GCM in storage (never plain text)
    2. Are masked in metadata listings
    3. Are completely stripped from configuration exports
    4. Are redacted from system logs
    """
    with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
        vault = CredentialVault(db_path=tmp.name, secret_key="test_master_secret_key_1234567890")

        secret_token = "sk_live_super_secret_helius_token_987654321"

        # 1. Store secret in vault
        meta = vault.store_credential(
            credential_id="cred-helius-01",
            provider_name="Helius Solana",
            category="rpc",
            secret_value=secret_token
        )
        assert meta["masked_hint"] == "sk_l...4321"
        assert secret_token not in meta["masked_hint"]

        # 2. Verify database storage contains ONLY ciphertext, never the raw secret
        conn = sqlite3.connect(tmp.name)
        cursor = conn.execute("SELECT encrypted_data, nonce, masked_hint FROM encrypted_credentials WHERE credential_id = 'cred-helius-01';")
        row = cursor.fetchone()
        assert row is not None
        assert secret_token not in row[0], "Raw secret MUST NOT appear in database storage"
        assert secret_token not in row[1]
        assert row[2] == "sk_l...4321"
        conn.close()

        # 3. Verify internal decryption works for authorized services
        decrypted = vault.get_credential("cred-helius-01")
        assert decrypted == secret_token

        # 4. Verify export configuration strips all credentials
        pm = ProviderManager(db_path=tmp.name)
        pm.add_endpoint(EndpointConfig(
            endpoint_id="ep-with-query-token",
            chain_id="solana:mainnet",
            url=f"https://mainnet.helius-rpc.com/?api-key={secret_token}",
            provider_name="Helius Dedicated",
            priority=1
        ))

        export_data = pm.export_safe_configuration()
        export_str = str(export_data)

        # Raw secret MUST NOT appear anywhere in the export
        assert secret_token not in export_str, "CRITICAL SECURITY LEAK: Secret found in configuration export!"
        assert "[REDACTED_QUERY_PARAMS]" in export_str
        assert export_data["security_policy"] == "STRICT_CREDENTIAL_ISOLATION_VERIFIED"

        # 5. Verify log sanitization
        sample_log = f"Error communicating with provider using token {secret_token} on port 443"
        sanitized = CredentialVault.sanitize_log_message(sample_log, known_secrets=[secret_token])
        assert secret_token not in sanitized
        assert "[REDACTED_CREDENTIAL]" in sanitized

# ==============================================================================
# OFFLINE-01: Global offline mode switch
# ==============================================================================

def test_offline_01_global_offline_mode_switch():
    """
    OFFLINE-01: Verifies that toggling global offline mode blocks all outbound
    network requests fail-closed without network egress.
    """
    with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
        pm = ProviderManager(db_path=tmp.name)

        # Ensure online
        pm.set_offline_mode(False)
        assert pm.is_offline() is False

        # Switch to offline
        pm.set_offline_mode(True)
        assert pm.is_offline() is True

        # Outbound endpoint request must be blocked fail-closed
        with pytest.raises(OfflineModeException) as exc_info:
            pm.get_active_endpoint("solana:mainnet")
        assert "Global offline mode active" in str(exc_info.value)

        # HTTP connectivity probe must report blocked
        import asyncio
        probe_res = asyncio.run(pm.test_endpoint("sol-public-01"))
        assert probe_res["status"] == "blocked"
        assert "Offline mode active" in probe_res["message"]

        # Restore online
        pm.set_offline_mode(False)
        assert pm.is_offline() is False
        ep = pm.get_active_endpoint("solana:mainnet")
        assert ep is not None
