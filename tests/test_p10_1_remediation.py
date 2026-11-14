import sys
import os
import hashlib
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath("./backend"))
from app.main import app
from app.core.chains import chain_registry
from app.core.config import settings
from app.services.multichain_scorer import multichain_scorer
from app.schemas.contracts import TriState, EvidenceStatus
from app.services.execution_store import execution_intent_store

client = TestClient(app)


def test_canonical_caip2_solana_lookup():
    """
    Remediation DEF-03:
    Verifies that Solana is accessible by both its canonical CAIP-2 identifier
    ('solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp') and its legacy alias ('solana:mainnet').
    """
    canonical_chain = chain_registry.get_chain("solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp")
    assert canonical_chain is not None, "Failed to resolve canonical CAIP-2 Solana identifier"
    assert canonical_chain.name == "Solana"
    assert canonical_chain.native_asset_symbol == "SOL"

    legacy_chain = chain_registry.get_chain("solana:mainnet")
    assert legacy_chain is not None
    assert legacy_chain.id == canonical_chain.id


def test_multichain_scorer_missing_evidence_not_rewarded():
    """
    Remediation DEF-04:
    Verifies that missing sell tax evidence (sell_tax_bps is None) does NOT receive
    the +10 safety score bonus awarded to explicitly verified low-tax tokens (<= 5%).
    """
    # 1. Missing evidence (sell_tax_bps=None)
    eval_missing = multichain_scorer.evaluate_security_and_safety(
        security_report=None,
        is_honeypot=False,
        sell_tax_bps=None,
        is_mint_revoked=TriState.TRUE,
        is_freeze_revoked=TriState.TRUE,
        lp_status="BURNED"
    )

    # 2. Verified low tax (sell_tax_bps=100)
    eval_verified_low = multichain_scorer.evaluate_security_and_safety(
        security_report=None,
        is_honeypot=False,
        sell_tax_bps=100,
        is_mint_revoked=TriState.TRUE,
        is_freeze_revoked=TriState.TRUE,
        lp_status="BURNED"
    )

    # Missing evidence must have lower safety score than verified low tax (+10 pts difference)
    assert eval_missing["security_score"] == Decimal("90.0")
    assert eval_verified_low["security_score"] == Decimal("100.0")
    assert eval_missing["security_score"] < eval_verified_low["security_score"]


def test_execution_intent_zero_trust_approvals():
    """
    Remediation DEF-02:
    Verifies zero-trust execution approval gates:
    - 404 on unissued/unknown intent_id
    - 400 on transaction message hash mismatch
    - 409 on replayed approval (already consumed)
    - 410 on expired intent
    - 403 on cold vault signer
    """
    import nacl.signing
    from app.services.execution_interfaces import b58encode

    sk = nacl.signing.SigningKey.generate()
    burner_wallet = b58encode(sk.verify_key.encode())
    cold_vault = "ColdVaultRemediation111111111111111111111"
    settings.COLD_VAULT_ADDRESSES = [cold_vault]

    # 1. Unknown intent rejection
    res_404 = client.post("/api/v1/execution/approve/non-existent-uuid-1234", json={
        "transaction_message_hash": "dummyhash",
        "signed_by_address": burner_wallet,
        "signature": "abcd" * 16
    })
    assert res_404.status_code == 404
    assert "not found" in res_404.json()["detail"].lower()

    # 2. Create valid intent
    intent_payload = {
        "asset_id": "00000000-0000-0000-0000-000000000001",
        "pool_id": "00000000-0000-0000-0000-000000000002",
        "wallet_address": burner_wallet,
        "side": "BUY",
        "input_mint": "So11111111111111111111111111111111111111112",
        "output_mint": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
        "input_amount_raw": 100000000,
        "max_slippage_bps": 150
    }
    create_res = client.post("/api/v1/execution/intent", json=intent_payload)
    assert create_res.status_code == 200
    intent_data = create_res.json()
    intent_id = intent_data["intent_id"]
    correct_hash = intent_data["transaction_message_hash"]
    canonical_digest = intent_data["canonical_intent_digest"]

    # Compute genuine signature for canonical digest
    valid_sig = sk.sign(canonical_digest.encode("utf-8")).signature.hex()

    # 3. Hash mismatch rejection
    res_400 = client.post(f"/api/v1/execution/approve/{intent_id}", json={
        "transaction_message_hash": "tampered_wrong_hash_value",
        "signed_by_address": burner_wallet,
        "signature": valid_sig
    })
    assert res_400.status_code == 400
    assert "does not match" in res_400.json()["detail"].lower()

    # 4. Cold vault signer rejection
    res_403 = client.post(f"/api/v1/execution/approve/{intent_id}", json={
        "transaction_message_hash": correct_hash,
        "signed_by_address": cold_vault,
        "signature": valid_sig
    })
    assert res_403.status_code == 403
    assert "cold vault" in res_403.json()["detail"].lower()

    # 5. Missing signature rejection (mandatory signature enforcement)
    res_no_sig = client.post(f"/api/v1/execution/approve/{intent_id}", json={
        "transaction_message_hash": correct_hash,
        "signed_by_address": burner_wallet
    })
    assert res_no_sig.status_code == 403
    assert "signature is mandatory" in res_no_sig.json()["detail"].lower()

    # 6. Successful first consumption
    res_200 = client.post(f"/api/v1/execution/approve/{intent_id}", json={
        "transaction_message_hash": correct_hash,
        "signed_by_address": burner_wallet,
        "signature": valid_sig
    })
    assert res_200.status_code == 200
    assert res_200.json().get("signature_verified") is True

    # 7. Replay attack rejection (second attempt on consumed intent)
    res_409 = client.post(f"/api/v1/execution/approve/{intent_id}", json={
        "transaction_message_hash": correct_hash,
        "signed_by_address": burner_wallet,
        "signature": valid_sig
    })
    assert res_409.status_code == 409
    assert "already consumed" in res_409.json()["detail"].lower()

    # 8. Expired intent rejection
    create_expired = client.post("/api/v1/execution/intent", json=intent_payload)
    expired_id = create_expired.json()["intent_id"]
    expired_hash = create_expired.json()["transaction_message_hash"]
    expired_digest = create_expired.json()["canonical_intent_digest"]
    expired_sig = sk.sign(expired_digest.encode("utf-8")).signature.hex()

    # Artificially expire the intent TTL in store
    with execution_intent_store._get_connection() as conn:
        conn.execute("UPDATE execution_intents SET expires_at = ? WHERE intent_id = ?",
                     ((datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat(), expired_id))
        conn.commit()

    res_410 = client.post(f"/api/v1/execution/approve/{expired_id}", json={
        "transaction_message_hash": expired_hash,
        "signed_by_address": burner_wallet,
        "signature": expired_sig
    })
    assert res_410.status_code == 410
    assert "expired" in res_410.json()["detail"].lower()


def test_migration_004_ddl_and_invariants():
    """
    Remediation DEF-01:
    Verifies that migration 004 defines append-only rules, RESTRICT foreign keys,
    and deferred double-entry balancing triggers.
    """
    migration_path = os.path.abspath("./migrations/004_p10_1_ledger_hardening.sql")
    assert os.path.exists(migration_path)

    with open(migration_path, "r", encoding="utf-8") as f:
        sql = f.read()

    assert "ON DELETE RESTRICT" in sql
    assert "trg_enforce_append_only_ledger" in sql
    assert "trg_verify_journal_balance" in sql
    assert "DEFERRABLE INITIALLY DEFERRED" in sql
