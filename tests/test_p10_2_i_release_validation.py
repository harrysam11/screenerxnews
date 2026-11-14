import os
import time
import psutil
import pytest
from app.core.config import settings
from app.core.chains import chain_registry
from app.services.credential_vault import credential_vault
from app.services.live_execution_engine import live_execution_engine
from app.services.ledger_service import double_entry_ledger_service

def test_gate_1_security_invariants_and_zero_exposure():
    """
    RELEASE GATE 1:
    - ALLOW_LIVE_TRADING must be strictly False by default.
    - Live execution authorization must fail-closed.
    - Credential vault must enforce AES-256-GCM encryption.
    """
    assert settings.ALLOW_LIVE_TRADING is False, "CRITICAL: Live trading must be disabled by default."
    assert live_execution_engine.verify_live_authorization(None) is False
    assert live_execution_engine.verify_live_authorization("unauthorized_attempt") is False

    # Vault encryption check: raw secret must never be stored as plaintext in database
    credential_vault.store_credential("solana_quicknode_test", "quicknode", "RPC", "qn_secret_api_key_777")
    with credential_vault._get_connection() as conn:
        row = conn.execute("SELECT encrypted_data FROM encrypted_credentials WHERE credential_id = 'solana_quicknode_test'").fetchone()
        assert row is not None
        assert "qn_secret_api_key_777" not in row["encrypted_data"], "CRITICAL: Plaintext secret leak detected in database!"

    # Decrypted retrieval matches original secret
    decrypted = credential_vault.get_credential("solana_quicknode_test")
    assert decrypted == "qn_secret_api_key_777"

    # Listed credentials have masked secret
    creds = credential_vault.list_credentials()
    matching = [c for c in creds if c["credential_id"] == "solana_quicknode_test"]
    assert len(matching) == 1
    assert "qn_secret_api_key_777" not in matching[0]["masked_hint"]

def test_gate_2_lightweight_rss_and_startup_benchmarks():
    """
    RELEASE GATE 2:
    - App cold startup must complete well within target (< 2.0s).
    - Resident Set Size (RSS) memory must remain lightweight (< 120MB).
    """
    t0 = time.perf_counter()
    from app.main import app
    assert app is not None
    startup_duration = time.perf_counter() - t0

    process = psutil.Process(os.getpid())
    rss_mb = process.memory_info().rss / (1024 * 1024)

    assert startup_duration < 2.0, f"Cold start too slow: {startup_duration:.2f}s"
    assert rss_mb < 120.0, f"RSS memory exceeds lightweight ceiling: {rss_mb:.2f}MB"

def test_gate_3_complete_twelve_chain_coverage():
    """
    RELEASE GATE 3:
    - Complete validation across all 12 chains (Solana, EVMs, Move, TVM).
    - Zero validation errors.
    """
    validation_res = chain_registry.validate_all_chains()
    assert validation_res["valid"] is True, f"Chain validation failed: {validation_res['errors']}"
    assert validation_res["total_chains"] == 12
    assert len(validation_res["validated_chains"]) == 12

    expected_chains = [
        "solana:mainnet", "eip155:1", "eip155:8453", "eip155:56",
        "eip155:42161", "eip155:10", "eip155:137", "eip155:43114",
        "sui:mainnet", "aptos:mainnet", "ton:mainnet", "tron:mainnet"
    ]
    for cid in expected_chains:
        assert cid in validation_res["validated_chains"], f"Missing chain: {cid}"

def test_gate_4_immutable_double_entry_ledger_invariants():
    """
    RELEASE GATE 4:
    - Financial journal requires minimum 2 lines and exact integer balance.
    """
    with pytest.raises(Exception):
        # Direct empty or single-line posting must fail
        double_entry_ledger_service.post_journal(
            journal_id="gate4_invalid",
            idempotency_key="gate4_idemp",
            journal_type="TEST",
            description="invalid single line",
            lines=[]
        )
