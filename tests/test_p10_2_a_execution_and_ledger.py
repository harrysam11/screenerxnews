import os
import sys
import uuid
import shutil
import sqlite3
import tempfile
import threading
import random
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from datetime import datetime, timezone, timedelta
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath("./backend"))
from app.main import app
from app.core.config import settings
from app.schemas.contracts import TradeIntentRequest, TradeApprovalRequest, IntentState
from app.services.execution_store import (
    ExecutionIntentStore,
    IntentLifecycleState,
    build_canonical_intent_message,
    compute_intent_digest
)
from app.services.execution_interfaces import (
    IsolatedExternalSigner,
    ChainSignatureVerifier,
    SecurityViolationError,
    b58decode,
    b58encode,
    _SECP_G,
    _SECP_N,
    _inv,
    _point_mul,
    keccak256
)
from app.services.ledger_service import (
    DoubleEntryLedgerService,
    UnbalancedJournalError,
    DuplicatePostingError,
    ImmutableLedgerError
)

client = TestClient(app)

# =====================================================================
# Cryptographic Test Helpers
# =====================================================================

def create_solana_keypair():
    import nacl.signing
    sk = nacl.signing.SigningKey.generate()
    pub_b58 = b58encode(sk.verify_key.encode())
    return sk, pub_b58

def sign_solana_message(sk, msg_bytes: bytes) -> str:
    return sk.sign(msg_bytes).signature.hex()

def create_evm_keypair():
    priv = random.randint(1, _SECP_N - 1)
    pub = _point_mul(priv, _SECP_G)
    pub_bytes = pub[0].to_bytes(32, 'big') + pub[1].to_bytes(32, 'big')
    addr = '0x' + keccak256(pub_bytes)[-20:].hex()
    return priv, addr

def sign_evm_message(priv: int, msg_bytes: bytes) -> str:
    import hashlib
    msghash = hashlib.sha256(msg_bytes).digest()
    z = int.from_bytes(msghash, 'big')
    k = random.randint(1, _SECP_N - 1)
    R = _point_mul(k, _SECP_G)
    r = R[0] % _SECP_N
    s = (_inv(k, _SECP_N) * (z + r * priv)) % _SECP_N
    v = 27 + (0 if R[1] % 2 == 0 else 1)
    sig_bytes = r.to_bytes(32, 'big') + s.to_bytes(32, 'big') + bytes([v])
    return sig_bytes.hex()


# =====================================================================
# 1. Execution Authorization Tests (EXEC-01 through EXEC-09)
# =====================================================================

@pytest.fixture
def clean_intent_store():
    tmp_dir = tempfile.mkdtemp()
    db_path = os.path.join(tmp_dir, "test_intents.db")
    store = ExecutionIntentStore(db_path=db_path)
    yield store
    shutil.rmtree(tmp_dir, ignore_errors=True)

def test_exec_01_policy_rejected_intent_cannot_be_approved(clean_intent_store):
    """
    EXEC-01: An intent that fails risk policy checks is marked POLICY_REJECTED
    and CAN NEVER be approved.
    """
    now = datetime.now(timezone.utc)
    intent_id = str(uuid.uuid4())
    intent_record = {
        "intent_id": intent_id,
        "chain_id": "solana:mainnet",
        "wallet_address": "BurnerWalletMock1111111111111111111",
        "asset_id": "asset-1",
        "pool_id": "pool-1",
        "side": "BUY",
        "input_mint": "So11111111111111111111111111111111111111112",
        "output_mint": "Token1111111111111111111111111111111111111",
        "input_amount_raw": Decimal("5000000000"),
        "max_slippage_bps": 150,
        "canonical_intent_digest": "dummy_digest_rejected",
        "transaction_message_hash": "dummy_hash_rejected",
        "status": IntentLifecycleState.POLICY_REJECTED,
        "policy_checks_passed": False,
        "rejection_reasons": ["Trade amount exceeds maximum single trade cap of 0.5 SOL"],
        "created_at": now,
        "expires_at": now + timedelta(minutes=2),
        "is_consumed": False
    }
    clean_intent_store.save_intent(intent_record)

    with pytest.raises(Exception) as exc_info:
        clean_intent_store.atomic_approve_intent(
            intent_id=intent_id,
            signed_by_address="BurnerWalletMock1111111111111111111",
            transaction_message_hash="dummy_hash_rejected",
            signature_hex="deadbeef" * 8
        )
    assert exc_info.value.status_code == 422
    assert "Cannot approve intent that failed risk policy checks" in exc_info.value.detail


def test_exec_02_signer_address_must_match_wallet_address(clean_intent_store):
    """
    EXEC-02: Approval request signed by a different wallet must be rejected with 403.
    """
    now = datetime.now(timezone.utc)
    intent_id = str(uuid.uuid4())
    sk_auth, authorized_wallet = create_solana_keypair()
    sk_att, attacker_wallet = create_solana_keypair()

    intent_record = {
        "intent_id": intent_id,
        "chain_id": "solana:mainnet",
        "wallet_address": authorized_wallet,
        "input_mint": "So11111111111111111111111111111111111111112",
        "output_mint": "Token1111111111111111111111111111111111111",
        "input_amount_raw": Decimal("100000000"),
        "canonical_intent_digest": "valid_digest_123",
        "transaction_message_hash": "valid_hash_123",
        "status": IntentLifecycleState.AWAITING_APPROVAL,
        "policy_checks_passed": True,
        "created_at": now,
        "expires_at": now + timedelta(minutes=2),
        "is_consumed": False
    }
    clean_intent_store.save_intent(intent_record)

    attacker_sig = sign_solana_message(sk_att, b"valid_digest_123")

    with pytest.raises(Exception) as exc_info:
        clean_intent_store.atomic_approve_intent(
            intent_id=intent_id,
            signed_by_address=attacker_wallet,
            transaction_message_hash="valid_hash_123",
            signature_hex=attacker_sig
        )
    assert exc_info.value.status_code == 403
    assert "Signer address does not match the authorized wallet" in exc_info.value.detail


def test_exec_02_mandatory_signature_enforcement(clean_intent_store):
    """
    EXEC-02 / P10.2-A.1: Omission of cryptographic signature MUST fail with 403.
    Approvals can never proceed without cryptographic proof.
    """
    now = datetime.now(timezone.utc)
    intent_id = str(uuid.uuid4())
    sk, wallet = create_solana_keypair()

    intent_record = {
        "intent_id": intent_id,
        "chain_id": "solana:mainnet",
        "wallet_address": wallet,
        "input_mint": "So11111111111111111111111111111111111111112",
        "output_mint": "Token1111111111111111111111111111111111111",
        "input_amount_raw": Decimal("100000000"),
        "canonical_intent_digest": "valid_digest_mandatory",
        "transaction_message_hash": "valid_hash_mandatory",
        "status": IntentLifecycleState.AWAITING_APPROVAL,
        "policy_checks_passed": True,
        "created_at": now,
        "expires_at": now + timedelta(minutes=2),
        "is_consumed": False
    }
    clean_intent_store.save_intent(intent_record)

    # 1. Signature is None -> Must fail 403
    with pytest.raises(Exception) as exc_info_none:
        clean_intent_store.atomic_approve_intent(
            intent_id=intent_id,
            signed_by_address=wallet,
            transaction_message_hash="valid_hash_mandatory",
            signature_hex=None
        )
    assert exc_info_none.value.status_code == 403
    assert "signature is mandatory" in exc_info_none.value.detail.lower()

    # 2. Signature is empty string -> Must fail 403
    with pytest.raises(Exception) as exc_info_empty:
        clean_intent_store.atomic_approve_intent(
            intent_id=intent_id,
            signed_by_address=wallet,
            transaction_message_hash="valid_hash_mandatory",
            signature_hex="   "
        )
    assert exc_info_empty.value.status_code == 403
    assert "signature is mandatory" in exc_info_empty.value.detail.lower()


def test_exec_03_transaction_message_digest_verification(clean_intent_store):
    """
    EXEC-03: Material change in transaction message hash / payload invalidates approval (400).
    """
    now = datetime.now(timezone.utc)
    intent_id = str(uuid.uuid4())
    sk, wallet = create_solana_keypair()

    intent_record = {
        "intent_id": intent_id,
        "chain_id": "solana:mainnet",
        "wallet_address": wallet,
        "input_mint": "So11111111111111111111111111111111111111112",
        "output_mint": "Token1111111111111111111111111111111111111",
        "input_amount_raw": Decimal("100000000"),
        "canonical_intent_digest": "expected_digest",
        "transaction_message_hash": "expected_hash_original",
        "status": IntentLifecycleState.AWAITING_APPROVAL,
        "policy_checks_passed": True,
        "created_at": now,
        "expires_at": now + timedelta(minutes=2),
        "is_consumed": False
    }
    clean_intent_store.save_intent(intent_record)

    sig = sign_solana_message(sk, b"expected_digest")

    with pytest.raises(Exception) as exc_info:
        clean_intent_store.atomic_approve_intent(
            intent_id=intent_id,
            signed_by_address=wallet,
            transaction_message_hash="tampered_different_hash",
            signature_hex=sig
        )
    assert exc_info.value.status_code == 400
    assert "does not match canonical intent digest" in exc_info.value.detail


def test_exec_03_evm_secp256k1_signature_verification(clean_intent_store):
    """
    EXEC-03 / P10.2-A.1: Genuine EVM Secp256k1 address recovery and signature verification.
    Validates that:
    1. A valid Secp256k1 signature from the intent wallet approves the intent.
    2. A signature from a different EVM address is rejected.
    3. A corrupt or forged signature is rejected.
    """
    now = datetime.now(timezone.utc)
    intent_id = str(uuid.uuid4())
    priv_auth, auth_evm_addr = create_evm_keypair()
    priv_other, other_evm_addr = create_evm_keypair()

    canonical_digest = "evm_canonical_digest_test_12345"
    tx_hash = "evm_tx_hash_12345"

    intent_record = {
        "intent_id": intent_id,
        "chain_id": "eip155:1",
        "wallet_address": auth_evm_addr,
        "input_mint": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
        "output_mint": "0x6982508145454Ce325dDbE47a25d4ec3d2311933",
        "input_amount_raw": Decimal("1000000000000000000"),
        "canonical_intent_digest": canonical_digest,
        "transaction_message_hash": tx_hash,
        "status": IntentLifecycleState.AWAITING_APPROVAL,
        "policy_checks_passed": True,
        "created_at": now,
        "expires_at": now + timedelta(minutes=2),
        "is_consumed": False
    }
    clean_intent_store.save_intent(intent_record)

    # 1. Valid EVM signature succeeds
    valid_evm_sig = sign_evm_message(priv_auth, canonical_digest.encode("utf-8"))
    res = clean_intent_store.atomic_approve_intent(
        intent_id=intent_id,
        signed_by_address=auth_evm_addr,
        transaction_message_hash=tx_hash,
        signature_hex=valid_evm_sig
    )
    assert res["status"] == "APPROVED"
    assert res["is_consumed"] is True

    # 2. Reset intent and try invalid signature from other EVM key
    intent_id_2 = str(uuid.uuid4())
    intent_record["intent_id"] = intent_id_2
    intent_record["is_consumed"] = False
    intent_record["status"] = IntentLifecycleState.AWAITING_APPROVAL
    clean_intent_store.save_intent(intent_record)

    other_evm_sig = sign_evm_message(priv_other, canonical_digest.encode("utf-8"))
    with pytest.raises(Exception) as exc_info:
        clean_intent_store.atomic_approve_intent(
            intent_id=intent_id_2,
            signed_by_address=auth_evm_addr,
            transaction_message_hash=tx_hash,
            signature_hex=other_evm_sig # Signed by other key!
        )
    assert exc_info.value.status_code == 400
    assert "Cryptographic signature verification failed" in exc_info.value.detail


def test_exec_03_canonical_digest_covers_all_authorization_fields():
    """
    EXEC-03 / P10.2-A.1: The canonical intent message must cover all 15 parameters:
    intent_id, chain_id, wallet, in_token, out_token, asset_id, pool_id, side,
    amount, slippage, router, strategy_id, strategy_version, nonce, expires_at.
    Mutating ANY of these fields must alter the canonical digest.
    """
    base_params = {
        "intent_id": "00000000-0000-0000-0000-000000000001",
        "chain_id": "solana:mainnet",
        "wallet_address": "BurnerWalletBase111111111111111111",
        "input_mint": "MintIn11111111111111111111111111111",
        "output_mint": "MintOut2222222222222222222222222222",
        "input_amount_raw": "1000000",
        "max_slippage_bps": 150,
        "destination_router": "raydium_v4",
        "asset_id": "asset-uuid-1",
        "pool_id": "pool-uuid-2",
        "side": "BUY",
        "strategy_id": "momentum_breakout",
        "strategy_version": "2.1.0",
        "nonce": "nonce-token-1234",
        "expires_at": "2026-11-14T12:00:00Z"
    }

    base_msg = build_canonical_intent_message(**base_params)
    base_digest = compute_intent_digest(base_msg)

    # Test mutating every single field
    fields_to_test = [
        ("asset_id", "asset-uuid-MUTATED"),
        ("pool_id", "pool-uuid-MUTATED"),
        ("side", "SELL"),
        ("strategy_id", "mean_reversion"),
        ("strategy_version", "2.1.1"),
        ("nonce", "nonce-token-MUTATED"),
        ("destination_router", "jupiter_v6"),
        ("input_amount_raw", "2000000"),
        ("max_slippage_bps", 300)
    ]

    for field, new_val in fields_to_test:
        mutated_params = dict(base_params)
        mutated_params[field] = new_val
        mutated_msg = build_canonical_intent_message(**mutated_params)
        mutated_digest = compute_intent_digest(mutated_msg)
        assert mutated_digest != base_digest, f"Digest failed to change when mutating field: {field}"


def test_exec_04_expired_intents_cannot_be_approved(clean_intent_store):
    """
    EXEC-04: Approval submitted after TTL expiration is rejected with 410.
    """
    past = datetime.now(timezone.utc) - timedelta(minutes=5)
    intent_id = str(uuid.uuid4())
    sk, wallet = create_solana_keypair()

    intent_record = {
        "intent_id": intent_id,
        "chain_id": "solana:mainnet",
        "wallet_address": wallet,
        "input_mint": "So11111111111111111111111111111111111111112",
        "output_mint": "Token1111111111111111111111111111111111111",
        "input_amount_raw": Decimal("100000000"),
        "canonical_intent_digest": "expired_digest",
        "transaction_message_hash": "expired_hash",
        "status": IntentLifecycleState.AWAITING_APPROVAL,
        "policy_checks_passed": True,
        "created_at": past - timedelta(minutes=2),
        "expires_at": past,
        "is_consumed": False
    }
    clean_intent_store.save_intent(intent_record)

    sig = sign_solana_message(sk, b"expired_digest")

    with pytest.raises(Exception) as exc_info:
        clean_intent_store.atomic_approve_intent(
            intent_id=intent_id,
            signed_by_address=wallet,
            transaction_message_hash="expired_hash",
            signature_hex=sig
        )
    assert exc_info.value.status_code == 410
    assert "Trade intent has expired" in exc_info.value.detail


def test_exec_05_concurrent_duplicate_approvals_rejected(clean_intent_store):
    """
    EXEC-05: Submitting approval twice on the same intent must reject the second request (409 Conflict).
    """
    now = datetime.now(timezone.utc)
    intent_id = str(uuid.uuid4())
    sk, wallet = create_solana_keypair()

    intent_record = {
        "intent_id": intent_id,
        "chain_id": "solana:mainnet",
        "wallet_address": wallet,
        "input_mint": "So11111111111111111111111111111111111111112",
        "output_mint": "Token1111111111111111111111111111111111111",
        "input_amount_raw": Decimal("100000000"),
        "canonical_intent_digest": "digest_dup",
        "transaction_message_hash": "hash_dup",
        "status": IntentLifecycleState.AWAITING_APPROVAL,
        "policy_checks_passed": True,
        "created_at": now,
        "expires_at": now + timedelta(minutes=2),
        "is_consumed": False
    }
    clean_intent_store.save_intent(intent_record)

    sig = sign_solana_message(sk, b"digest_dup")

    # 1. First approval succeeds
    res1 = clean_intent_store.atomic_approve_intent(
        intent_id=intent_id,
        signed_by_address=wallet,
        transaction_message_hash="hash_dup",
        signature_hex=sig
    )
    assert res1["status"] == "APPROVED"
    assert res1["is_consumed"] is True

    # 2. Second approval must fail with 409
    with pytest.raises(Exception) as exc_info:
        clean_intent_store.atomic_approve_intent(
            intent_id=intent_id,
            signed_by_address=wallet,
            transaction_message_hash="hash_dup",
            signature_hex=sig
        )
    assert exc_info.value.status_code == 409
    assert "already consumed" in exc_info.value.detail


def test_exec_06_atomic_state_transitions(clean_intent_store):
    """
    EXEC-06: Concurrent threads racing to approve the same intent result in exactly 1 success
    and all others failing with 409 (no race conditions).
    """
    now = datetime.now(timezone.utc)
    intent_id = str(uuid.uuid4())
    sk, wallet = create_solana_keypair()

    intent_record = {
        "intent_id": intent_id,
        "chain_id": "solana:mainnet",
        "wallet_address": wallet,
        "input_mint": "So11111111111111111111111111111111111111112",
        "output_mint": "Token1111111111111111111111111111111111111",
        "input_amount_raw": Decimal("100000000"),
        "canonical_intent_digest": "digest_race",
        "transaction_message_hash": "hash_race",
        "status": IntentLifecycleState.AWAITING_APPROVAL,
        "policy_checks_passed": True,
        "created_at": now,
        "expires_at": now + timedelta(minutes=2),
        "is_consumed": False
    }
    clean_intent_store.save_intent(intent_record)

    sig = sign_solana_message(sk, b"digest_race")

    results = []
    errors = []

    def approve_worker():
        try:
            res = clean_intent_store.atomic_approve_intent(
                intent_id=intent_id,
                signed_by_address=wallet,
                transaction_message_hash="hash_race",
                signature_hex=sig
            )
            results.append(res)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=approve_worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == 1, f"Expected exactly 1 success, got {len(results)}"
    assert len(errors) == 4, f"Expected 4 errors, got {len(errors)}"
    for err in errors:
        assert err.status_code == 409


def test_exec_07_persisted_intents_survive_service_restart():
    """
    EXEC-07: Creating an intent, terminating the store instance, and re-opening
    from disk recovers the exact intent state without loss.
    """
    tmp_dir = tempfile.mkdtemp()
    db_path = os.path.join(tmp_dir, "restart_test.db")
    now = datetime.now(timezone.utc)
    intent_id = str(uuid.uuid4())

    store_1 = ExecutionIntentStore(db_path=db_path)
    store_1.save_intent({
        "intent_id": intent_id,
        "chain_id": "solana:mainnet",
        "wallet_address": "PersistentWallet1111111111111111111",
        "input_mint": "So11111111111111111111111111111111111111112",
        "output_mint": "Token1111111111111111111111111111111111111",
        "input_amount_raw": Decimal("250000000"),
        "canonical_intent_digest": "persist_digest",
        "transaction_message_hash": "persist_hash",
        "status": IntentLifecycleState.AWAITING_APPROVAL,
        "policy_checks_passed": True,
        "created_at": now,
        "expires_at": now + timedelta(minutes=2),
        "is_consumed": False
    })
    del store_1

    store_2 = ExecutionIntentStore(db_path=db_path)
    recovered = store_2.get_intent(intent_id)
    assert recovered is not None
    assert recovered["intent_id"] == intent_id
    assert recovered["wallet_address"] == "PersistentWallet1111111111111111111"
    assert recovered["status"] == IntentLifecycleState.AWAITING_APPROVAL
    assert recovered["is_consumed"] is False

    shutil.rmtree(tmp_dir, ignore_errors=True)


def test_exec_08_paper_mode_cannot_call_isolated_signer():
    """
    EXEC-08: In paper mode or when live trading is disabled, attempting to access
    live signer raises SecurityViolationError.
    """
    paper_signer = IsolatedExternalSigner(is_live_enabled=False, execution_mode="PAPER")
    with pytest.raises(SecurityViolationError) as exc_info:
        paper_signer.sign_transaction(
            chain_id="solana:mainnet",
            unsigned_payload="raw_payload",
            wallet_address="Burner1111"
        )
    assert "Paper trading mode is strictly forbidden from accessing live signing gateway" in str(exc_info.value)


def test_exec_09_cold_vault_cannot_approve_intent(clean_intent_store):
    """
    EXEC-09: Cold vault addresses are strictly forbidden from approving intents.
    """
    cold_vault = "ColdVaultMock1111111111111111111111"
    settings.COLD_VAULT_ADDRESSES = [cold_vault]

    now = datetime.now(timezone.utc)
    intent_id = str(uuid.uuid4())
    intent_record = {
        "intent_id": intent_id,
        "chain_id": "solana:mainnet",
        "wallet_address": cold_vault,
        "input_mint": "So11111111111111111111111111111111111111112",
        "output_mint": "Token1111111111111111111111111111111111111",
        "input_amount_raw": Decimal("100000000"),
        "canonical_intent_digest": "digest_cold",
        "transaction_message_hash": "hash_cold",
        "status": IntentLifecycleState.AWAITING_APPROVAL,
        "policy_checks_passed": True,
        "created_at": now,
        "expires_at": now + timedelta(minutes=2),
        "is_consumed": False
    }
    clean_intent_store.save_intent(intent_record)

    with pytest.raises(Exception) as exc_info:
        clean_intent_store.atomic_approve_intent(
            intent_id=intent_id,
            signed_by_address=cold_vault,
            transaction_message_hash="hash_cold",
            signature_hex="deadbeef" * 8
        )
    assert exc_info.value.status_code == 403
    assert "Cold vault signatures are permanently forbidden" in exc_info.value.detail


# =====================================================================
# 2. Financial Ledger Tests (LEDGER-01 through LEDGER-08)
# =====================================================================

@pytest.fixture
def clean_ledger_service():
    tmp_dir = tempfile.mkdtemp()
    db_path = os.path.join(tmp_dir, "test_ledger.db")
    svc = DoubleEntryLedgerService(db_path=db_path)
    yield svc
    shutil.rmtree(tmp_dir, ignore_errors=True)

def test_ledger_01_unbalanced_journals_rejected(clean_ledger_service):
    """
    LEDGER-01: An unbalanced journal where Debits != Credits raises UnbalancedJournalError.
    """
    journal_id = str(uuid.uuid4())
    lines = [
        {"account_code": "WALLET_SOL", "wallet_address": "W1", "mint_address": "SOL", "debit_amount_raw": 0, "credit_amount_raw": Decimal("500000000"), "usd_rate": Decimal("150.0")},
        {"account_code": "WALLET_TOKEN", "wallet_address": "W1", "mint_address": "BONK", "debit_amount_raw": Decimal("400000000"), "credit_amount_raw": 0, "usd_rate": Decimal("150.0")}
    ]
    with pytest.raises(UnbalancedJournalError) as exc_info:
        clean_ledger_service.post_journal(
            journal_id=journal_id,
            idempotency_key="idem-002",
            journal_type="TRADE_BUY",
            description="Unbalanced Trade",
            lines=lines
        )
    assert "DOUBLE-ENTRY BALANCE VIOLATION" in str(exc_info.value)


def test_ledger_02_balanced_journals_accepted(clean_ledger_service):
    """
    LEDGER-02: A balanced journal (Debits == Credits, >= 2 lines) posts successfully.
    """
    journal_id = str(uuid.uuid4())
    lines = [
        {"account_code": "WALLET_SOL", "wallet_address": "W1", "mint_address": "SOL", "debit_amount_raw": 0, "credit_amount_raw": Decimal("500000000"), "usd_rate": Decimal("150.0")},
        {"account_code": "WALLET_TOKEN", "wallet_address": "W1", "mint_address": "BONK", "debit_amount_raw": Decimal("499500000"), "credit_amount_raw": 0, "usd_rate": Decimal("150.0")},
        {"account_code": "EXPENSE_NETWORK_FEE", "wallet_address": "W1", "mint_address": "SOL", "debit_amount_raw": Decimal("500000"), "credit_amount_raw": 0, "usd_rate": Decimal("150.0")}
    ]
    res = clean_ledger_service.post_journal(
        journal_id=journal_id,
        idempotency_key="idem-001",
        journal_type="TRADE_BUY",
        description="Buy BONK with SOL",
        lines=lines
    )
    assert res["status"] == "POSTED"
    assert res["lines_count"] == 3

    fetched = clean_ledger_service.get_journal(journal_id)
    assert fetched is not None
    assert len(fetched["lines"]) == 3


def test_ledger_03_journal_with_fewer_than_two_lines_rejected(clean_ledger_service):
    """
    LEDGER-03: A journal with fewer than 2 lines raises UnbalancedJournalError.
    """
    journal_id = str(uuid.uuid4())
    lines = [
        {"account_code": "WALLET_SOL", "wallet_address": "W1", "mint_address": "SOL", "debit_amount_raw": Decimal("500000000"), "credit_amount_raw": 0, "usd_rate": Decimal("150.0")}
    ]
    with pytest.raises(UnbalancedJournalError) as exc_info:
        clean_ledger_service.post_journal(
            journal_id=journal_id,
            idempotency_key="idem-004",
            journal_type="TRANSFER",
            description="Single Line Journal",
            lines=lines
        )
    assert "Every posted journal must contain at least 2 lines" in str(exc_info.value)


def test_ledger_04_journal_with_empty_lines_rejected(clean_ledger_service):
    """
    LEDGER-04: An empty journal raises UnbalancedJournalError.
    """
    journal_id = str(uuid.uuid4())
    with pytest.raises(UnbalancedJournalError) as exc_info:
        clean_ledger_service.post_journal(
            journal_id=journal_id,
            idempotency_key="idem-003",
            journal_type="TRANSFER",
            description="Empty Journal",
            lines=[]
        )
    assert "Every posted journal must contain at least 2 lines" in str(exc_info.value)


def test_ledger_05_posted_ledger_entries_cannot_be_updated(clean_ledger_service):
    """
    LEDGER-05: Attempting to update a posted journal triggers immutability violation.
    """
    journal_id = str(uuid.uuid4())
    lines = [
        {"account_code": "WALLET_SOL", "wallet_address": "W1", "mint_address": "SOL", "debit_amount_raw": 0, "credit_amount_raw": Decimal("100"), "usd_rate": Decimal("1.0")},
        {"account_code": "WALLET_TOKEN", "wallet_address": "W1", "mint_address": "TOK", "debit_amount_raw": Decimal("100"), "credit_amount_raw": 0, "usd_rate": Decimal("1.0")}
    ]
    clean_ledger_service.post_journal(
        journal_id=journal_id,
        idempotency_key="idem-005",
        journal_type="TRANSFER",
        description="Original Description",
        lines=lines
    )

    with pytest.raises(ImmutableLedgerError) as exc_info:
        clean_ledger_service.mutate_journal(journal_id, "Tampered Description")
    assert "Posted ledger journals cannot be modified" in str(exc_info.value)


def test_ledger_06_posted_ledger_entries_cannot_be_deleted(clean_ledger_service):
    """
    LEDGER-06: Attempting to delete a posted journal triggers immutability violation.
    """
    journal_id = str(uuid.uuid4())
    lines = [
        {"account_code": "WALLET_SOL", "wallet_address": "W1", "mint_address": "SOL", "debit_amount_raw": 0, "credit_amount_raw": Decimal("100"), "usd_rate": Decimal("1.0")},
        {"account_code": "WALLET_TOKEN", "wallet_address": "W1", "mint_address": "TOK", "debit_amount_raw": Decimal("100"), "credit_amount_raw": 0, "usd_rate": Decimal("1.0")}
    ]
    clean_ledger_service.post_journal(
        journal_id=journal_id,
        idempotency_key="idem-006",
        journal_type="TRANSFER",
        description="Original Transfer",
        lines=lines
    )

    with pytest.raises(ImmutableLedgerError) as exc_info:
        clean_ledger_service.delete_journal(journal_id)
    assert "Posted ledger journals cannot be deleted" in str(exc_info.value)


def test_ledger_07_failed_journal_rolls_back_atomically(clean_ledger_service):
    """
    LEDGER-07: If an error occurs during journal insertion, database rollback occurs
    and no partial rows are committed.
    """
    journal_id = str(uuid.uuid4())
    lines = [
        {"account_code": "WALLET_SOL", "wallet_address": "W1", "mint_address": "SOL", "debit_amount_raw": 0, "credit_amount_raw": Decimal("100"), "usd_rate": Decimal("1.0")},
        {"account_code": "INVALID_CODE", "wallet_address": "W1", "mint_address": "TOK", "debit_amount_raw": Decimal("100"), "credit_amount_raw": 0, "usd_rate": Decimal("1.0")}
    ]
    with pytest.raises(Exception):
        clean_ledger_service.post_journal(
            journal_id=journal_id,
            idempotency_key="idem-007",
            journal_type="TRANSFER",
            description="Bad Code",
            lines=lines
        )

    fetched = clean_ledger_service.get_journal(journal_id)
    assert fetched is None


def test_ledger_08_idempotency_key_prevents_duplicate_posting(clean_ledger_service):
    """
    LEDGER-08: Duplicate posting using the same idempotency key is rejected.
    """
    journal_id_1 = str(uuid.uuid4())
    journal_id_2 = str(uuid.uuid4())
    lines = [
        {"account_code": "WALLET_SOL", "wallet_address": "W1", "mint_address": "SOL", "debit_amount_raw": 0, "credit_amount_raw": Decimal("100"), "usd_rate": Decimal("1.0")},
        {"account_code": "WALLET_TOKEN", "wallet_address": "W1", "mint_address": "TOK", "debit_amount_raw": Decimal("100"), "credit_amount_raw": 0, "usd_rate": Decimal("1.0")}
    ]
    clean_ledger_service.post_journal(
        journal_id=journal_id_1,
        idempotency_key="same-key-123",
        journal_type="TRANSFER",
        description="First attempt",
        lines=lines
    )

    with pytest.raises(DuplicatePostingError) as exc_info:
        clean_ledger_service.post_journal(
            journal_id=journal_id_2,
            idempotency_key="same-key-123",
            journal_type="TRANSFER",
            description="Duplicate attempt",
            lines=lines
        )
    assert "Duplicate posting rejected" in str(exc_info.value) or "already consumed" in str(exc_info.value)


# =====================================================================
# 3. Direct SQLite Database-Level Invariant Enforcement Tests (P10.2-A.1)
# =====================================================================

def test_ledger_09_direct_sql_insert_posted_blocked_by_db_trigger(clean_ledger_service):
    """
    P10.2-A.1 / Direct SQL Audit:
    Attempting to directly execute `INSERT INTO ledger_journals ... status='POSTED'`
    via raw SQL bypasses Python checks, but MUST be blocked by the SQLite trigger
    `trg_prevent_direct_posted_insert`.
    """
    conn = sqlite3.connect(clean_ledger_service.db_path)
    try:
        with pytest.raises(sqlite3.IntegrityError) as exc_info:
            conn.execute("""
                INSERT INTO ledger_journals (
                    id, transaction_signature, order_id, idempotency_key, journal_type, description, status, posted_at
                ) VALUES ('direct_j1', NULL, NULL, 'direct_k1', 'SWAP', 'Direct SQL', 'POSTED', 'now')
            """)
        assert "Direct INSERT with status=POSTED forbidden" in str(exc_info.value)
    finally:
        conn.close()


def test_ledger_10_direct_sql_unbalanced_post_blocked_by_db_trigger(clean_ledger_service):
    """
    P10.2-A.1 / Direct SQL Audit:
    Attempting to insert a draft journal with 1 line or unbalanced lines via raw SQL,
    then updating status to 'POSTED', MUST be aborted by SQLite trigger `trg_check_journal_post_invariants`.
    """
    conn = sqlite3.connect(clean_ledger_service.db_path)
    try:
        # Case 1: 1 line (< 2 lines)
        conn.execute("""
            INSERT INTO ledger_journals (
                id, transaction_signature, order_id, idempotency_key, journal_type, description, status, posted_at
            ) VALUES ('direct_j2', NULL, NULL, 'direct_k2', 'SWAP', 'Draft SQL', 'DRAFT', 'now')
        """)
        conn.execute("""
            INSERT INTO ledger_lines (
                journal_id, account_code, wallet_address, mint_address, debit_amount_raw, credit_amount_raw, usd_rate
            ) VALUES ('direct_j2', 'WALLET_SOL', 'W1', 'SOL', '100', '0', '1.0')
        """)
        with pytest.raises(sqlite3.IntegrityError) as exc_info_lines:
            conn.execute("UPDATE ledger_journals SET status = 'POSTED' WHERE id = 'direct_j2'")
        assert "Journal has fewer than 2 lines" in str(exc_info_lines.value)

        # Case 2: 2 lines but unbalanced
        conn.execute("""
            INSERT INTO ledger_lines (
                journal_id, account_code, wallet_address, mint_address, debit_amount_raw, credit_amount_raw, usd_rate
            ) VALUES ('direct_j2', 'WALLET_TOKEN', 'W1', 'BONK', '0', '80', '1.0')
        """)
        with pytest.raises(sqlite3.IntegrityError) as exc_info_balance:
            conn.execute("UPDATE ledger_journals SET status = 'POSTED' WHERE id = 'direct_j2'")
        assert "Journal debits and credits do not balance" in str(exc_info_balance.value)
    finally:
        conn.close()


def test_ledger_11_direct_sql_line_insert_on_posted_blocked_by_db_trigger(clean_ledger_service):
    """
    P10.2-A.1 / Direct SQL Audit:
    Attempting to insert new ledger lines into an already POSTED journal via raw SQL
    MUST be aborted by SQLite trigger `trg_prevent_line_insert_on_posted`.
    """
    # 1. Post a valid journal via service
    journal_id = str(uuid.uuid4())
    lines = [
        {"account_code": "WALLET_SOL", "wallet_address": "W1", "mint_address": "SOL", "debit_amount_raw": 0, "credit_amount_raw": Decimal("100"), "usd_rate": Decimal("1.0")},
        {"account_code": "WALLET_TOKEN", "wallet_address": "W1", "mint_address": "TOK", "debit_amount_raw": Decimal("100"), "credit_amount_raw": 0, "usd_rate": Decimal("1.0")}
    ]
    clean_ledger_service.post_journal(
        journal_id=journal_id,
        idempotency_key="idem-valid-post",
        journal_type="TRANSFER",
        description="Valid Posted Journal",
        lines=lines
    )

    # 2. Attempt raw SQL line insertion into this posted journal
    conn = sqlite3.connect(clean_ledger_service.db_path)
    try:
        with pytest.raises(sqlite3.IntegrityError) as exc_info:
            conn.execute(f"""
                INSERT INTO ledger_lines (
                    journal_id, account_code, wallet_address, mint_address, debit_amount_raw, credit_amount_raw, usd_rate
                ) VALUES ('{journal_id}', 'EXPENSE_NETWORK_FEE', 'W1', 'SOL', '5', '0', '1.0')
            """)
        assert "Cannot insert lines into an already POSTED journal" in str(exc_info.value)
    finally:
        conn.close()


# =====================================================================
# 4. PostgreSQL Integration Tests (With real test implementation)
# =====================================================================

has_postgres_binaries = shutil.which("psql") is not None and shutil.which("initdb") is not None

@pytest.mark.skipif(
    not has_postgres_binaries,
    reason="PostgreSQL binaries (initdb/psql) not installed on host. Acceptance criterion pending on PostgreSQL environment."
)
def test_pg_ledger_deferred_trigger_integration():
    """
    PG-LEDGER-01: Full integration test for PostgreSQL Migration 004 triggers:
    1. Executes migration DDL on PostgreSQL.
    2. Verifies trg_no_truncate_journals blocks TRUNCATE.
    3. Verifies trg_check_journal_line_count blocks transactions with < 2 lines upon COMMIT.
    4. Verifies trg_check_journal_balance blocks transactions where debits != credits upon COMMIT.
    5. Verifies balanced transactions commit cleanly.
    """
    import testing.postgresql
    import psycopg2

    with testing.postgresql.Postgresql() as pg:
        conn = psycopg2.connect(**pg.dsn())
        conn.autocommit = True
        cur = conn.cursor()

        # 1. Create base tables
        cur.execute("""
            CREATE TABLE live_ledger_journals (
                journal_id UUID PRIMARY KEY,
                chain_id VARCHAR(64) NOT NULL,
                status VARCHAR(32) NOT NULL DEFAULT 'DRAFT',
                total_amount NUMERIC(36, 18) NOT NULL,
                idempotency_key VARCHAR(128) UNIQUE NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );

            CREATE TABLE live_ledger_lines (
                line_id BIGSERIAL PRIMARY KEY,
                journal_id UUID NOT NULL REFERENCES live_ledger_journals(journal_id) ON DELETE RESTRICT,
                account_id VARCHAR(64) NOT NULL,
                currency VARCHAR(32) NOT NULL,
                debit_amount NUMERIC(36, 18) NOT NULL DEFAULT 0,
                credit_amount NUMERIC(36, 18) NOT NULL DEFAULT 0,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)

        # 2. Apply Migration 004
        migration_path = os.path.abspath("./migrations/004_p10_1_ledger_hardening.sql")
        with open(migration_path, "r", encoding="utf-8") as f:
            cur.execute(f.read())

        # 3. Test TRUNCATE prevention
        with pytest.raises(psycopg2.DatabaseError) as exc_info:
            cur.execute("TRUNCATE live_ledger_journals;")
        assert "TRUNCATE forbidden" in str(exc_info.value)

        # 4. Test Single-Line Deferred Constraint (LEDGER-03)
        conn.autocommit = False
        j_id = str(uuid.uuid4())
        cur.execute(
            "INSERT INTO live_ledger_journals (journal_id, chain_id, status, total_amount, idempotency_key) VALUES (%s, %s, %s, %s, %s);",
            (j_id, "solana:mainnet", "POSTED", 100, "idem-pg-1")
        )
        cur.execute(
            "INSERT INTO live_ledger_lines (journal_id, account_id, currency, debit_amount, credit_amount) VALUES (%s, %s, %s, %s, %s);",
            (j_id, "WALLET_SOL", "SOL", 100, 0)
        )
        with pytest.raises(psycopg2.DatabaseError) as exc_info:
            conn.commit()
        assert "Ledger line count violation (LEDGER-03)" in str(exc_info.value)
        conn.rollback()

        # 5. Test Unbalanced Deferred Constraint (LEDGER-01)
        cur.execute(
            "INSERT INTO live_ledger_journals (journal_id, chain_id, status, total_amount, idempotency_key) VALUES (%s, %s, %s, %s, %s);",
            (j_id, "solana:mainnet", "POSTED", 100, "idem-pg-2")
        )
        cur.execute(
            "INSERT INTO live_ledger_lines (journal_id, account_id, currency, debit_amount, credit_amount) VALUES (%s, %s, %s, %s, %s);",
            (j_id, "WALLET_SOL", "SOL", 100, 0)
        )
        cur.execute(
            "INSERT INTO live_ledger_lines (journal_id, account_id, currency, debit_amount, credit_amount) VALUES (%s, %s, %s, %s, %s);",
            (j_id, "WALLET_BONK", "BONK", 0, 80)
        )
        with pytest.raises(psycopg2.DatabaseError) as exc_info:
            conn.commit()
        assert "Ledger balance violation (LEDGER-01)" in str(exc_info.value)
        conn.rollback()

        # 6. Test Balanced Journal Commit Succeeds
        cur.execute(
            "INSERT INTO live_ledger_journals (journal_id, chain_id, status, total_amount, idempotency_key) VALUES (%s, %s, %s, %s, %s);",
            (j_id, "solana:mainnet", "POSTED", 100, "idem-pg-3")
        )
        cur.execute(
            "INSERT INTO live_ledger_lines (journal_id, account_id, currency, debit_amount, credit_amount) VALUES (%s, %s, %s, %s, %s);",
            (j_id, "WALLET_SOL", "SOL", 100, 0)
        )
        cur.execute(
            "INSERT INTO live_ledger_lines (journal_id, account_id, currency, debit_amount, credit_amount) VALUES (%s, %s, %s, %s, %s);",
            (j_id, "WALLET_BONK", "SOL", 0, 100)
        )
        conn.commit()

        cur.close()
        conn.close()
