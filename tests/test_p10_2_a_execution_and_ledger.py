import os
import sys
import uuid
import shutil
import sqlite3
import tempfile
import threading
import random
import hashlib
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
    # Deterministic EIP-191 personal_sign format
    eip191_prefix = f"\x19Ethereum Signed Message:\n{len(msg_bytes)}".encode("utf-8")
    msghash = keccak256(eip191_prefix + msg_bytes)
    z = int.from_bytes(msghash, 'big')
    k = random.randint(1, _SECP_N - 1)
    R = _point_mul(k, _SECP_G)
    r = R[0] % _SECP_N
    s = (_inv(k, _SECP_N) * (z + r * priv)) % _SECP_N
    recid = 1 if R[1] % 2 != 0 else 0
    if R[0] >= _SECP_N:
        recid += 2
    # EIP-2 low-s malleability protection
    if s > _SECP_N // 2:
        s = _SECP_N - s
        recid ^= 1
    v = 27 + recid
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
    orig_burners = list(settings.BURNER_WALLET_ADDRESSES)
    orig_vaults = list(settings.COLD_VAULT_ADDRESSES)
    settings.BURNER_WALLET_ADDRESSES = ["BurnerWalletMock1111111111111111111"]
    yield store
    settings.BURNER_WALLET_ADDRESSES = orig_burners
    settings.COLD_VAULT_ADDRESSES = orig_vaults
    shutil.rmtree(tmp_dir, ignore_errors=True)

def test_exec_01_policy_rejected_intent_cannot_be_approved(clean_intent_store):
    """
    EXEC-01: An intent that fails risk policy checks is marked POLICY_REJECTED
    and CAN NEVER be approved.
    """
    settings.BURNER_WALLET_ADDRESSES = ["BurnerWalletMock1111111111111111111"]
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
    settings.BURNER_WALLET_ADDRESSES = [authorized_wallet, attacker_wallet]

    canonical_msg = build_canonical_intent_message(
        intent_id=intent_id,
        chain_id="solana:mainnet",
        wallet_address=authorized_wallet,
        input_mint="So11111111111111111111111111111111111111112",
        output_mint="Token1111111111111111111111111111111111111",
        input_amount_raw="100000000",
        max_slippage_bps=150,
        destination_router="default",
        asset_id="",
        pool_id="",
        side="BUY",
        strategy_id="direct",
        strategy_version="1.0",
        nonce="nonce-1",
        expires_at=(now + timedelta(minutes=2)).isoformat()
    )
    digest = compute_intent_digest(canonical_msg)
    tx_hash = hashlib.sha256(digest.encode("utf-8")).hexdigest()

    intent_record = {
        "intent_id": intent_id,
        "chain_id": "solana:mainnet",
        "wallet_address": authorized_wallet,
        "asset_id": "",
        "pool_id": "",
        "side": "BUY",
        "input_mint": "So11111111111111111111111111111111111111112",
        "output_mint": "Token1111111111111111111111111111111111111",
        "input_amount_raw": Decimal("100000000"),
        "max_slippage_bps": 150,
        "destination_router": "default",
        "strategy_id": "direct",
        "strategy_version": "1.0",
        "nonce": "nonce-1",
        "canonical_intent_digest": digest,
        "transaction_message_hash": tx_hash,
        "status": IntentLifecycleState.AWAITING_APPROVAL,
        "policy_checks_passed": True,
        "created_at": now,
        "expires_at": now + timedelta(minutes=2),
        "is_consumed": False
    }
    clean_intent_store.save_intent(intent_record)

    attacker_sig = sign_solana_message(sk_att, digest.encode("utf-8"))

    with pytest.raises(Exception) as exc_info:
        clean_intent_store.atomic_approve_intent(
            intent_id=intent_id,
            signed_by_address=attacker_wallet,
            transaction_message_hash=tx_hash,
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

    settings.BURNER_WALLET_ADDRESSES = [wallet]
    canonical_msg = build_canonical_intent_message(
        intent_id=intent_id,
        chain_id="solana:mainnet",
        wallet_address=wallet,
        input_mint="So11111111111111111111111111111111111111112",
        output_mint="Token1111111111111111111111111111111111111",
        input_amount_raw="100000000",
        max_slippage_bps=150,
        destination_router="default",
        asset_id="",
        pool_id="",
        side="BUY",
        strategy_id="direct",
        strategy_version="1.0",
        nonce="nonce-mand",
        expires_at=(now + timedelta(minutes=2)).isoformat()
    )
    digest = compute_intent_digest(canonical_msg)
    tx_hash = hashlib.sha256(digest.encode("utf-8")).hexdigest()

    intent_record = {
        "intent_id": intent_id,
        "chain_id": "solana:mainnet",
        "wallet_address": wallet,
        "asset_id": "",
        "pool_id": "",
        "side": "BUY",
        "input_mint": "So11111111111111111111111111111111111111112",
        "output_mint": "Token1111111111111111111111111111111111111",
        "input_amount_raw": Decimal("100000000"),
        "max_slippage_bps": 150,
        "destination_router": "default",
        "strategy_id": "direct",
        "strategy_version": "1.0",
        "nonce": "nonce-mand",
        "canonical_intent_digest": digest,
        "transaction_message_hash": tx_hash,
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
            transaction_message_hash=tx_hash,
            signature_hex=None
        )
    assert exc_info_none.value.status_code == 403
    assert "signature is mandatory" in exc_info_none.value.detail.lower()

    # 2. Signature is empty string -> Must fail 403
    with pytest.raises(Exception) as exc_info_empty:
        clean_intent_store.atomic_approve_intent(
            intent_id=intent_id,
            signed_by_address=wallet,
            transaction_message_hash=tx_hash,
            signature_hex="   "
        )
    assert exc_info_empty.value.status_code == 403
    assert "signature is mandatory" in exc_info_empty.value.detail.lower()


def test_exec_02_empty_burner_allowlist_fails_closed(clean_intent_store):
    """
    P10.2-A.3: Empty or missing burner-wallet allowlists MUST fail closed with 403.
    """
    settings.BURNER_WALLET_ADDRESSES = []  # Empty allowlist
    now = datetime.now(timezone.utc)
    intent_id = str(uuid.uuid4())
    sk, wallet = create_solana_keypair()

    canonical_msg = build_canonical_intent_message(
        intent_id=intent_id,
        chain_id="solana:mainnet",
        wallet_address=wallet,
        input_mint="So11111111111111111111111111111111111111112",
        output_mint="Token1111111111111111111111111111111111111",
        input_amount_raw="100000000",
        max_slippage_bps=150,
        destination_router="default",
        asset_id="",
        pool_id="",
        side="BUY",
        strategy_id="direct",
        strategy_version="1.0",
        nonce="nonce-empty-burner",
        expires_at=(now + timedelta(minutes=2)).isoformat()
    )
    digest = compute_intent_digest(canonical_msg)
    tx_hash = hashlib.sha256(digest.encode("utf-8")).hexdigest()
    sig = sign_solana_message(sk, digest.encode("utf-8"))

    intent_record = {
        "intent_id": intent_id,
        "chain_id": "solana:mainnet",
        "wallet_address": wallet,
        "asset_id": "",
        "pool_id": "",
        "side": "BUY",
        "input_mint": "So11111111111111111111111111111111111111112",
        "output_mint": "Token1111111111111111111111111111111111111",
        "input_amount_raw": Decimal("100000000"),
        "max_slippage_bps": 150,
        "destination_router": "default",
        "strategy_id": "direct",
        "strategy_version": "1.0",
        "nonce": "nonce-empty-burner",
        "canonical_intent_digest": digest,
        "transaction_message_hash": tx_hash,
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
            signed_by_address=wallet,
            transaction_message_hash=tx_hash,
            signature_hex=sig
        )
    assert exc_info.value.status_code == 403
    assert "fail-closed policy" in exc_info.value.detail.lower() or "empty or unconfigured" in exc_info.value.detail.lower()


def test_exec_02_evm_address_normalization_and_cold_vault_variants(clean_intent_store):
    """
    P10.2-A.3:
    1. EVM addresses in burner allowlist and wallet are normalized consistently (case-insensitive).
    2. Cold vault check normalizes EVM addresses, blocking mixed-case cold vault addresses.
    """
    now = datetime.now(timezone.utc)
    intent_id = str(uuid.uuid4())
    priv, auth_addr = create_evm_keypair()

    # Configure burner wallet with UPPERCASE
    settings.BURNER_WALLET_ADDRESSES = [auth_addr.upper()]
    # Configure cold vault with MIXED CASE
    cold_vault = "0xDeAdBeEf00000000000000000000000000000001"
    settings.COLD_VAULT_ADDRESSES = [cold_vault]

    canonical_msg = build_canonical_intent_message(
        intent_id=intent_id,
        chain_id="eip155:1",
        wallet_address=auth_addr.lower(),
        input_mint="0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
        output_mint="0x6982508145454Ce325dDbE47a25d4ec3d2311933",
        input_amount_raw="1000000000000000000",
        max_slippage_bps=150,
        destination_router="uniswap_v3",
        asset_id="asset-eth",
        pool_id="pool-eth",
        side="BUY",
        strategy_id="direct",
        strategy_version="1.0",
        nonce="nonce-norm",
        expires_at=(now + timedelta(minutes=2)).isoformat()
    )
    digest = compute_intent_digest(canonical_msg)
    tx_hash = hashlib.sha256(digest.encode("utf-8")).hexdigest()
    sig = sign_evm_message(priv, digest.encode("utf-8"))

    intent_record = {
        "intent_id": intent_id,
        "chain_id": "eip155:1",
        "wallet_address": auth_addr.lower(),
        "asset_id": "asset-eth",
        "pool_id": "pool-eth",
        "side": "BUY",
        "input_mint": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
        "output_mint": "0x6982508145454Ce325dDbE47a25d4ec3d2311933",
        "input_amount_raw": Decimal("1000000000000000000"),
        "max_slippage_bps": 150,
        "destination_router": "uniswap_v3",
        "strategy_id": "direct",
        "strategy_version": "1.0",
        "nonce": "nonce-norm",
        "canonical_intent_digest": digest,
        "transaction_message_hash": tx_hash,
        "status": IntentLifecycleState.AWAITING_APPROVAL,
        "policy_checks_passed": True,
        "created_at": now,
        "expires_at": now + timedelta(minutes=2),
        "is_consumed": False
    }
    clean_intent_store.save_intent(intent_record)

    # 1. Signer with mixed-case matches uppercase burner allowlist and lowercase intent wallet
    res = clean_intent_store.atomic_approve_intent(
        intent_id=intent_id,
        signed_by_address=auth_addr,
        transaction_message_hash=tx_hash,
        signature_hex=sig
    )
    assert res["status"] == "APPROVED"

    # 2. Cold vault signed with lowercase must still be blocked by mixed-case COLD_VAULT_ADDRESSES
    intent_id_cv = str(uuid.uuid4())
    intent_record["intent_id"] = intent_id_cv
    intent_record["wallet_address"] = cold_vault.lower()
    intent_record["is_consumed"] = False
    intent_record["status"] = IntentLifecycleState.AWAITING_APPROVAL
    clean_intent_store.save_intent(intent_record)

    with pytest.raises(Exception) as exc_cv:
        clean_intent_store.atomic_approve_intent(
            intent_id=intent_id_cv,
            signed_by_address=cold_vault.lower(),
            transaction_message_hash=tx_hash,
            signature_hex="deadbeef" * 8
        )
    assert exc_cv.value.status_code == 403
    assert "cold vault" in exc_cv.value.detail.lower()


def test_exec_02_solana_case_sensitivity(clean_intent_store):
    """
    P10.2-A.3: Solana Base58 addresses are case-sensitive. Case alteration must be rejected.
    """
    now = datetime.now(timezone.utc)
    intent_id = str(uuid.uuid4())
    sk, wallet = create_solana_keypair()
    settings.BURNER_WALLET_ADDRESSES = [wallet]

    canonical_msg = build_canonical_intent_message(
        intent_id=intent_id,
        chain_id="solana:mainnet",
        wallet_address=wallet,
        input_mint="So11111111111111111111111111111111111111112",
        output_mint="Token1111111111111111111111111111111111111",
        input_amount_raw="100000000",
        max_slippage_bps=150,
        destination_router="default",
        asset_id="",
        pool_id="",
        side="BUY",
        strategy_id="direct",
        strategy_version="1.0",
        nonce="nonce-case",
        expires_at=(now + timedelta(minutes=2)).isoformat()
    )
    digest = compute_intent_digest(canonical_msg)
    tx_hash = hashlib.sha256(digest.encode("utf-8")).hexdigest()
    sig = sign_solana_message(sk, digest.encode("utf-8"))

    intent_record = {
        "intent_id": intent_id,
        "chain_id": "solana:mainnet",
        "wallet_address": wallet,
        "asset_id": "",
        "pool_id": "",
        "side": "BUY",
        "input_mint": "So11111111111111111111111111111111111111112",
        "output_mint": "Token1111111111111111111111111111111111111",
        "input_amount_raw": Decimal("100000000"),
        "max_slippage_bps": 150,
        "destination_router": "default",
        "strategy_id": "direct",
        "strategy_version": "1.0",
        "nonce": "nonce-case",
        "canonical_intent_digest": digest,
        "transaction_message_hash": tx_hash,
        "status": IntentLifecycleState.AWAITING_APPROVAL,
        "policy_checks_passed": True,
        "created_at": now,
        "expires_at": now + timedelta(minutes=2),
        "is_consumed": False
    }
    clean_intent_store.save_intent(intent_record)

    # Invert case of the first alphabetic character of wallet
    idx = next(i for i, c in enumerate(wallet) if c.isalpha())
    flipped = wallet[idx].lower() if wallet[idx].isupper() else wallet[idx].upper()
    altered_wallet = wallet[:idx] + flipped + wallet[idx+1:]

    with pytest.raises(Exception) as exc_info:
        clean_intent_store.atomic_approve_intent(
            intent_id=intent_id,
            signed_by_address=altered_wallet,
            transaction_message_hash=tx_hash,
            signature_hex=sig
        )
    assert exc_info.value.status_code == 403


def test_exec_03_transaction_message_digest_verification(clean_intent_store):
    """
    EXEC-03: Material change in transaction message hash / payload invalidates approval (400).
    """
    now = datetime.now(timezone.utc)
    intent_id = str(uuid.uuid4())
    sk, wallet = create_solana_keypair()
    settings.BURNER_WALLET_ADDRESSES = [wallet]

    canonical_msg = build_canonical_intent_message(
        intent_id=intent_id,
        chain_id="solana:mainnet",
        wallet_address=wallet,
        input_mint="So11111111111111111111111111111111111111112",
        output_mint="Token1111111111111111111111111111111111111",
        input_amount_raw="100000000",
        max_slippage_bps=150,
        destination_router="default",
        asset_id="",
        pool_id="",
        side="BUY",
        strategy_id="direct",
        strategy_version="1.0",
        nonce="nonce-hash",
        expires_at=(now + timedelta(minutes=2)).isoformat()
    )
    digest = compute_intent_digest(canonical_msg)
    tx_hash = hashlib.sha256(digest.encode("utf-8")).hexdigest()

    intent_record = {
        "intent_id": intent_id,
        "chain_id": "solana:mainnet",
        "wallet_address": wallet,
        "asset_id": "",
        "pool_id": "",
        "side": "BUY",
        "input_mint": "So11111111111111111111111111111111111111112",
        "output_mint": "Token1111111111111111111111111111111111111",
        "input_amount_raw": Decimal("100000000"),
        "max_slippage_bps": 150,
        "destination_router": "default",
        "strategy_id": "direct",
        "strategy_version": "1.0",
        "nonce": "nonce-hash",
        "canonical_intent_digest": digest,
        "transaction_message_hash": tx_hash,
        "status": IntentLifecycleState.AWAITING_APPROVAL,
        "policy_checks_passed": True,
        "created_at": now,
        "expires_at": now + timedelta(minutes=2),
        "is_consumed": False
    }
    clean_intent_store.save_intent(intent_record)

    sig = sign_solana_message(sk, digest.encode("utf-8"))

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
    EXEC-03 / P10.2-A.2:
    Genuine EVM Secp256k1 recovery against single explicit EIP-191 personal_sign specification:
    1. Valid Secp256k1 signature from intent wallet approves the intent.
    2. Signature from a different EVM address is rejected.
    3. Standard established Ethereum test vector verified.
    4. Malleable high-s signature is rejected (s > N // 2).
    """
    now = datetime.now(timezone.utc)
    intent_id = str(uuid.uuid4())
    priv_auth, auth_evm_addr = create_evm_keypair()
    priv_other, other_evm_addr = create_evm_keypair()
    settings.BURNER_WALLET_ADDRESSES = [auth_evm_addr, other_evm_addr]

    canonical_msg = build_canonical_intent_message(
        intent_id=intent_id,
        chain_id="eip155:1",
        wallet_address=auth_evm_addr,
        input_mint="0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
        output_mint="0x6982508145454Ce325dDbE47a25d4ec3d2311933",
        input_amount_raw="1000000000000000000",
        max_slippage_bps=150,
        destination_router="uniswap_v3",
        asset_id="asset-eth",
        pool_id="pool-eth",
        side="BUY",
        strategy_id="direct",
        strategy_version="1.0",
        nonce="nonce-evm-1",
        expires_at=(now + timedelta(minutes=2)).isoformat()
    )
    canonical_digest = compute_intent_digest(canonical_msg)
    tx_hash = hashlib.sha256(canonical_digest.encode("utf-8")).hexdigest()

    intent_record = {
        "intent_id": intent_id,
        "chain_id": "eip155:1",
        "wallet_address": auth_evm_addr,
        "asset_id": "asset-eth",
        "pool_id": "pool-eth",
        "side": "BUY",
        "input_mint": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
        "output_mint": "0x6982508145454Ce325dDbE47a25d4ec3d2311933",
        "input_amount_raw": Decimal("1000000000000000000"),
        "max_slippage_bps": 150,
        "destination_router": "uniswap_v3",
        "strategy_id": "direct",
        "strategy_version": "1.0",
        "nonce": "nonce-evm-1",
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

    # 2. Signature from other EVM key is rejected
    intent_id_2 = str(uuid.uuid4())
    canonical_msg_2 = build_canonical_intent_message(
        intent_id=intent_id_2,
        chain_id="eip155:1",
        wallet_address=auth_evm_addr,
        input_mint="0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
        output_mint="0x6982508145454Ce325dDbE47a25d4ec3d2311933",
        input_amount_raw="1000000000000000000",
        max_slippage_bps=150,
        destination_router="uniswap_v3",
        asset_id="asset-eth",
        pool_id="pool-eth",
        side="BUY",
        strategy_id="direct",
        strategy_version="1.0",
        nonce="nonce-evm-2",
        expires_at=(now + timedelta(minutes=2)).isoformat()
    )
    canonical_digest_2 = compute_intent_digest(canonical_msg_2)
    tx_hash_2 = hashlib.sha256(canonical_digest_2.encode("utf-8")).hexdigest()

    intent_record_2 = dict(intent_record)
    intent_record_2["intent_id"] = intent_id_2
    intent_record_2["nonce"] = "nonce-evm-2"
    intent_record_2["canonical_intent_digest"] = canonical_digest_2
    intent_record_2["transaction_message_hash"] = tx_hash_2
    intent_record_2["is_consumed"] = False
    intent_record_2["status"] = IntentLifecycleState.AWAITING_APPROVAL
    clean_intent_store.save_intent(intent_record_2)

    other_evm_sig = sign_evm_message(priv_other, canonical_digest_2.encode("utf-8"))
    with pytest.raises(Exception) as exc_info:
        clean_intent_store.atomic_approve_intent(
            intent_id=intent_id_2,
            signed_by_address=auth_evm_addr,
            transaction_message_hash=tx_hash_2,
            signature_hex=other_evm_sig
        )
    assert exc_info.value.status_code == 400
    assert "Cryptographic signature verification failed" in exc_info.value.detail

    # 3. Established cryptographic test vector verification
    verifier = ChainSignatureVerifier()
    std_addr = "0x2c7536e3605d9c16a7a3d7b1898e529396a65c23"
    std_msg = b"Hello World"
    std_sig = "6f267aa61dda3067280942299172e2b9f6b4252b492e7c0343ecce6e7edfe0b24110a805ba6777e4303501ea9dfae133855f4ee2e34fd0444bb23d11f296eef31c"
    assert verifier.verify("eip155:1", std_addr, std_msg, bytes.fromhex(std_sig)) is True

    # 4. Strict low-s malleability enforcement
    sig_raw = bytes.fromhex(std_sig)
    r = int.from_bytes(sig_raw[:32], 'big')
    s = int.from_bytes(sig_raw[32:64], 'big')
    v = sig_raw[64]
    high_s = _SECP_N - s
    recid_flip = (v - 27) ^ 1
    malleable_sig = r.to_bytes(32, 'big') + high_s.to_bytes(32, 'big') + bytes([27 + recid_flip])
    assert verifier.verify("eip155:1", std_addr, std_msg, malleable_sig) is False


def test_exec_03_evm_secp256k1_negative_cases():
    """
    P10.2-A.3:
    1. Rejects recovery candidates where (y * y) % P != y_sq (non-residue).
    2. Rejects invalid recovery IDs (v < 27, v > 38, or invalid recid).
    3. Rejects out-of-range r values (r = 0, r >= N).
    4. Rejects out-of-range s values (s = 0, s > N // 2).
    5. Rejects malformed signature lengths (not exactly 65 bytes).
    """
    from app.services.execution_interfaces import ec_recover_evm_address
    verifier = ChainSignatureVerifier()
    msghash = b"\x11" * 32

    # 1. Quadratic non-residue: on secp256k1, x=5 has y_sq = 5^3 + 7 = 132 which has no square root mod P
    assert ec_recover_evm_address(msghash, r=5, s=100, v=27) is None

    # 2. Invalid recovery IDs
    assert ec_recover_evm_address(msghash, r=100, s=100, v=26) is None  # Below 27
    assert ec_recover_evm_address(msghash, r=100, s=100, v=39) is None  # Invalid EIP-155 parity
    assert ec_recover_evm_address(msghash, r=100, s=100, v=4) is None   # Invalid raw recid > 3

    # 3. Invalid r range
    assert ec_recover_evm_address(msghash, r=0, s=100, v=27) is None
    assert ec_recover_evm_address(msghash, r=_SECP_N, s=100, v=27) is None
    assert ec_recover_evm_address(msghash, r=_SECP_N + 5, s=100, v=27) is None

    # 4. Invalid s range (zero or high-s violating EIP-2)
    assert ec_recover_evm_address(msghash, r=100, s=0, v=27) is None
    assert ec_recover_evm_address(msghash, r=100, s=_SECP_N // 2 + 1, v=27) is None

    # 5. Malformed signature length
    assert verifier.verify("eip155:1", "0x2c7536e3605d9c16a7a3d7b1898e529396a65c23", b"test", b"\x00" * 64) is False
    assert verifier.verify("eip155:1", "0x2c7536e3605d9c16a7a3d7b1898e529396a65c23", b"test", b"\x00" * 66) is False


def test_exec_03_second_known_ethereum_test_vector():
    """
    P10.2-A.3: Verify against independent known RFC 6979 / EIP-191 Ethereum test vector.
    """
    verifier = ChainSignatureVerifier()
    addr = "0x2c7536e3605d9c16a7a3d7b1898e529396a65c23"
    msg = b"Hello Ethereum"
    sig_hex = "bd14a51caf4881e4f536efd608c2bbd6b9e7c02460bcda67d23a4f02e9b8b82702f0d0c911adfc7b3768f6d8eeaa161f4ad688fc7313f7cc03d5522d1832e8771b"
    assert verifier.verify("eip155:1", addr, msg, bytes.fromhex(sig_hex)) is True


def test_exec_03_canonical_digest_covers_all_authorization_fields():
    """
    EXEC-03 / P10.2-A.1: The canonical intent message must cover all 15 parameters.
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


def test_exec_03_independent_canonical_reconstruction_detects_db_tampering(clean_intent_store):
    """
    EXEC-03 / P10.2-A.2:
    `atomic_approve_intent()` must independently reconstruct the canonical message from
    stored fields. If a direct SQL update silently modifies a field in the database,
    approval MUST be rejected with 400.
    """
    now = datetime.now(timezone.utc)
    intent_id = str(uuid.uuid4())
    sk, wallet = create_solana_keypair()
    settings.BURNER_WALLET_ADDRESSES = [wallet]

    canonical_msg = build_canonical_intent_message(
        intent_id=intent_id,
        chain_id="solana:mainnet",
        wallet_address=wallet,
        input_mint="So11111111111111111111111111111111111111112",
        output_mint="Token1111111111111111111111111111111111111",
        input_amount_raw="100000000",
        max_slippage_bps=150,
        destination_router="default",
        asset_id="asset-recon",
        pool_id="pool-recon",
        side="BUY",
        strategy_id="direct",
        strategy_version="1.0",
        nonce="nonce-recon-1",
        expires_at=(now + timedelta(minutes=2)).isoformat()
    )
    digest = compute_intent_digest(canonical_msg)
    tx_hash = hashlib.sha256(digest.encode("utf-8")).hexdigest()

    intent_record = {
        "intent_id": intent_id,
        "chain_id": "solana:mainnet",
        "wallet_address": wallet,
        "asset_id": "asset-recon",
        "pool_id": "pool-recon",
        "side": "BUY",
        "input_mint": "So11111111111111111111111111111111111111112",
        "output_mint": "Token1111111111111111111111111111111111111",
        "input_amount_raw": Decimal("100000000"),
        "max_slippage_bps": 150,
        "destination_router": "default",
        "strategy_id": "direct",
        "strategy_version": "1.0",
        "nonce": "nonce-recon-1",
        "canonical_intent_digest": digest,
        "transaction_message_hash": tx_hash,
        "status": IntentLifecycleState.AWAITING_APPROVAL,
        "policy_checks_passed": True,
        "created_at": now,
        "expires_at": now + timedelta(minutes=2),
        "is_consumed": False
    }
    clean_intent_store.save_intent(intent_record)

    sig = sign_solana_message(sk, digest.encode("utf-8"))

    # Direct SQL tampering: mutate input_amount_raw in DB
    conn = clean_intent_store._get_connection()
    try:
        conn.execute("UPDATE execution_intents SET input_amount_raw = '999999999' WHERE intent_id = ?", (intent_id,))
    finally:
        conn.close()

    # Approval must detect the tampering through independent reconstruction
    with pytest.raises(Exception) as exc_info:
        clean_intent_store.atomic_approve_intent(
            intent_id=intent_id,
            signed_by_address=wallet,
            transaction_message_hash=tx_hash,
            signature_hex=sig
        )
    assert exc_info.value.status_code == 400
    assert "Stored intent digest does not match reconstructed canonical intent fields" in exc_info.value.detail


def test_exec_04_expired_intents_cannot_be_approved(clean_intent_store):
    """
    EXEC-04: Approval submitted after TTL expiration is rejected with 410.
    """
    past = datetime.now(timezone.utc) - timedelta(minutes=5)
    intent_id = str(uuid.uuid4())
    sk, wallet = create_solana_keypair()
    settings.BURNER_WALLET_ADDRESSES = [wallet]

    canonical_msg = build_canonical_intent_message(
        intent_id=intent_id,
        chain_id="solana:mainnet",
        wallet_address=wallet,
        input_mint="So11111111111111111111111111111111111111112",
        output_mint="Token1111111111111111111111111111111111111",
        input_amount_raw="100000000",
        max_slippage_bps=150,
        destination_router="default",
        asset_id="",
        pool_id="",
        side="BUY",
        strategy_id="direct",
        strategy_version="1.0",
        nonce="nonce-exp",
        expires_at=past.isoformat()
    )
    digest = compute_intent_digest(canonical_msg)
    tx_hash = hashlib.sha256(digest.encode("utf-8")).hexdigest()

    intent_record = {
        "intent_id": intent_id,
        "chain_id": "solana:mainnet",
        "wallet_address": wallet,
        "asset_id": "",
        "pool_id": "",
        "side": "BUY",
        "input_mint": "So11111111111111111111111111111111111111112",
        "output_mint": "Token1111111111111111111111111111111111111",
        "input_amount_raw": Decimal("100000000"),
        "max_slippage_bps": 150,
        "destination_router": "default",
        "strategy_id": "direct",
        "strategy_version": "1.0",
        "nonce": "nonce-exp",
        "canonical_intent_digest": digest,
        "transaction_message_hash": tx_hash,
        "status": IntentLifecycleState.AWAITING_APPROVAL,
        "policy_checks_passed": True,
        "created_at": past - timedelta(minutes=2),
        "expires_at": past,
        "is_consumed": False
    }
    clean_intent_store.save_intent(intent_record)

    sig = sign_solana_message(sk, digest.encode("utf-8"))

    with pytest.raises(Exception) as exc_info:
        clean_intent_store.atomic_approve_intent(
            intent_id=intent_id,
            signed_by_address=wallet,
            transaction_message_hash=tx_hash,
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
    settings.BURNER_WALLET_ADDRESSES = [wallet]

    canonical_msg = build_canonical_intent_message(
        intent_id=intent_id,
        chain_id="solana:mainnet",
        wallet_address=wallet,
        input_mint="So11111111111111111111111111111111111111112",
        output_mint="Token1111111111111111111111111111111111111",
        input_amount_raw="100000000",
        max_slippage_bps=150,
        destination_router="default",
        asset_id="",
        pool_id="",
        side="BUY",
        strategy_id="direct",
        strategy_version="1.0",
        nonce="nonce-dup",
        expires_at=(now + timedelta(minutes=2)).isoformat()
    )
    digest = compute_intent_digest(canonical_msg)
    tx_hash = hashlib.sha256(digest.encode("utf-8")).hexdigest()

    intent_record = {
        "intent_id": intent_id,
        "chain_id": "solana:mainnet",
        "wallet_address": wallet,
        "asset_id": "",
        "pool_id": "",
        "side": "BUY",
        "input_mint": "So11111111111111111111111111111111111111112",
        "output_mint": "Token1111111111111111111111111111111111111",
        "input_amount_raw": Decimal("100000000"),
        "max_slippage_bps": 150,
        "destination_router": "default",
        "strategy_id": "direct",
        "strategy_version": "1.0",
        "nonce": "nonce-dup",
        "canonical_intent_digest": digest,
        "transaction_message_hash": tx_hash,
        "status": IntentLifecycleState.AWAITING_APPROVAL,
        "policy_checks_passed": True,
        "created_at": now,
        "expires_at": now + timedelta(minutes=2),
        "is_consumed": False
    }
    clean_intent_store.save_intent(intent_record)

    sig = sign_solana_message(sk, digest.encode("utf-8"))

    # 1. First approval succeeds
    res1 = clean_intent_store.atomic_approve_intent(
        intent_id=intent_id,
        signed_by_address=wallet,
        transaction_message_hash=tx_hash,
        signature_hex=sig
    )
    assert res1["status"] == "APPROVED"
    assert res1["is_consumed"] is True

    # 2. Second approval must fail with 409
    with pytest.raises(Exception) as exc_info:
        clean_intent_store.atomic_approve_intent(
            intent_id=intent_id,
            signed_by_address=wallet,
            transaction_message_hash=tx_hash,
            signature_hex=sig
        )
    assert exc_info.value.status_code == 409
    assert "already consumed" in exc_info.value.detail


def test_exec_05_cross_process_and_multi_connection_approval_isolation(clean_intent_store):
    """
    P10.2-A.3:
    Verifies SQLite BEGIN IMMEDIATE transaction reservation prevents concurrent cross-connection
    races: when connection A holds the write lock, connection B attempting to write is blocked.
    """
    now = datetime.now(timezone.utc)
    intent_id = str(uuid.uuid4())
    sk, wallet = create_solana_keypair()
    settings.BURNER_WALLET_ADDRESSES = [wallet]

    canonical_msg = build_canonical_intent_message(
        intent_id=intent_id,
        chain_id="solana:mainnet",
        wallet_address=wallet,
        input_mint="So11111111111111111111111111111111111111112",
        output_mint="Token1111111111111111111111111111111111111",
        input_amount_raw="100000000",
        max_slippage_bps=150,
        destination_router="default",
        asset_id="",
        pool_id="",
        side="BUY",
        strategy_id="direct",
        strategy_version="1.0",
        nonce="nonce-iso",
        expires_at=(now + timedelta(minutes=2)).isoformat()
    )
    digest = compute_intent_digest(canonical_msg)
    tx_hash = hashlib.sha256(digest.encode("utf-8")).hexdigest()
    sig = sign_solana_message(sk, digest.encode("utf-8"))

    clean_intent_store.save_intent({
        "intent_id": intent_id,
        "chain_id": "solana:mainnet",
        "wallet_address": wallet,
        "asset_id": "",
        "pool_id": "",
        "side": "BUY",
        "input_mint": "So11111111111111111111111111111111111111112",
        "output_mint": "Token1111111111111111111111111111111111111",
        "input_amount_raw": Decimal("100000000"),
        "max_slippage_bps": 150,
        "destination_router": "default",
        "strategy_id": "direct",
        "strategy_version": "1.0",
        "nonce": "nonce-iso",
        "canonical_intent_digest": digest,
        "transaction_message_hash": tx_hash,
        "status": IntentLifecycleState.AWAITING_APPROVAL,
        "policy_checks_passed": True,
        "created_at": now,
        "expires_at": now + timedelta(minutes=2),
        "is_consumed": False
    })

    # External connection acquires BEGIN IMMEDIATE
    ext_conn = sqlite3.connect(clean_intent_store.db_path, timeout=0.1, isolation_level=None)
    ext_conn.execute("BEGIN IMMEDIATE;")
    try:
        # clean_intent_store attempting to acquire lock should fail immediately with operational error/locked
        # Note: clean_intent_store timeout is 10.0, but we can verify timeout occurs or lock is held
        with sqlite3.connect(clean_intent_store.db_path, timeout=0.01, isolation_level=None) as second_conn:
            with pytest.raises(sqlite3.OperationalError) as lock_exc:
                second_conn.execute("BEGIN IMMEDIATE;")
            assert "locked" in str(lock_exc.value).lower()
    finally:
        ext_conn.execute("ROLLBACK;")
        ext_conn.close()

    # Now approval proceeds without issue
    res = clean_intent_store.atomic_approve_intent(
        intent_id=intent_id,
        signed_by_address=wallet,
        transaction_message_hash=tx_hash,
        signature_hex=sig
    )
    assert res["status"] == "APPROVED"


def test_exec_05_intent_mutation_race_condition_prevented(clean_intent_store):
    """
    P10.2-A.3:
    Proves that modifications to authorization-critical columns (e.g. input_amount_raw)
    cannot silently pass approval: the server reconstructs the canonical message from
    persisted database fields within the IMMEDIATE transaction and rejects mismatched hashes.
    """
    now = datetime.now(timezone.utc)
    intent_id = str(uuid.uuid4())
    sk, wallet = create_solana_keypair()
    settings.BURNER_WALLET_ADDRESSES = [wallet]

    canonical_msg = build_canonical_intent_message(
        intent_id=intent_id,
        chain_id="solana:mainnet",
        wallet_address=wallet,
        input_mint="So11111111111111111111111111111111111111112",
        output_mint="Token1111111111111111111111111111111111111",
        input_amount_raw="100000000",
        max_slippage_bps=150,
        destination_router="default",
        asset_id="",
        pool_id="",
        side="BUY",
        strategy_id="direct",
        strategy_version="1.0",
        nonce="nonce-race",
        expires_at=(now + timedelta(minutes=2)).isoformat()
    )
    digest = compute_intent_digest(canonical_msg)
    tx_hash = hashlib.sha256(digest.encode("utf-8")).hexdigest()
    sig = sign_solana_message(sk, digest.encode("utf-8"))

    clean_intent_store.save_intent({
        "intent_id": intent_id,
        "chain_id": "solana:mainnet",
        "wallet_address": wallet,
        "asset_id": "",
        "pool_id": "",
        "side": "BUY",
        "input_mint": "So11111111111111111111111111111111111111112",
        "output_mint": "Token1111111111111111111111111111111111111",
        "input_amount_raw": Decimal("100000000"),
        "max_slippage_bps": 150,
        "destination_router": "default",
        "strategy_id": "direct",
        "strategy_version": "1.0",
        "nonce": "nonce-race",
        "canonical_intent_digest": digest,
        "transaction_message_hash": tx_hash,
        "status": IntentLifecycleState.AWAITING_APPROVAL,
        "policy_checks_passed": True,
        "created_at": now,
        "expires_at": now + timedelta(minutes=2),
        "is_consumed": False
    })

    # Tamper with the raw amount in the database
    conn = clean_intent_store._get_connection()
    try:
        conn.execute("UPDATE execution_intents SET input_amount_raw = '500000000' WHERE intent_id = ?", (intent_id,))
    finally:
        conn.close()

    with pytest.raises(Exception) as exc_info:
        clean_intent_store.atomic_approve_intent(
            intent_id=intent_id,
            signed_by_address=wallet,
            transaction_message_hash=tx_hash,
            signature_hex=sig
        )
    assert exc_info.value.status_code == 400
    assert "Stored intent digest does not match reconstructed canonical intent fields" in exc_info.value.detail


def test_exec_06_atomic_state_transitions(clean_intent_store):
    """
    EXEC-06: Concurrent threads racing to approve the same intent result in exactly 1 success
    and all others failing with 409 (no race conditions).
    """
    now = datetime.now(timezone.utc)
    intent_id = str(uuid.uuid4())
    sk, wallet = create_solana_keypair()
    settings.BURNER_WALLET_ADDRESSES = [wallet]

    canonical_msg = build_canonical_intent_message(
        intent_id=intent_id,
        chain_id="solana:mainnet",
        wallet_address=wallet,
        input_mint="So11111111111111111111111111111111111111112",
        output_mint="Token1111111111111111111111111111111111111",
        input_amount_raw="100000000",
        max_slippage_bps=150,
        destination_router="default",
        asset_id="",
        pool_id="",
        side="BUY",
        strategy_id="direct",
        strategy_version="1.0",
        nonce="nonce-race",
        expires_at=(now + timedelta(minutes=2)).isoformat()
    )
    digest = compute_intent_digest(canonical_msg)
    tx_hash = hashlib.sha256(digest.encode("utf-8")).hexdigest()

    intent_record = {
        "intent_id": intent_id,
        "chain_id": "solana:mainnet",
        "wallet_address": wallet,
        "asset_id": "",
        "pool_id": "",
        "side": "BUY",
        "input_mint": "So11111111111111111111111111111111111111112",
        "output_mint": "Token1111111111111111111111111111111111111",
        "input_amount_raw": Decimal("100000000"),
        "max_slippage_bps": 150,
        "destination_router": "default",
        "strategy_id": "direct",
        "strategy_version": "1.0",
        "nonce": "nonce-race",
        "canonical_intent_digest": digest,
        "transaction_message_hash": tx_hash,
        "status": IntentLifecycleState.AWAITING_APPROVAL,
        "policy_checks_passed": True,
        "created_at": now,
        "expires_at": now + timedelta(minutes=2),
        "is_consumed": False
    }
    clean_intent_store.save_intent(intent_record)

    sig = sign_solana_message(sk, digest.encode("utf-8"))

    results = []
    errors = []

    def approve_worker():
        try:
            res = clean_intent_store.atomic_approve_intent(
                intent_id=intent_id,
                signed_by_address=wallet,
                transaction_message_hash=tx_hash,
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
# 3. Direct SQLite Database-Level Invariant Enforcement Tests (P10.2-A.2)
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


def test_ledger_10_direct_sql_status_rollback_blocked_by_db_trigger(clean_ledger_service):
    """
    P10.2-A.2 / Direct SQL Audit:
    Attempting to rollback a POSTED journal to DRAFT status via raw SQL
    MUST be aborted by `trg_enforce_valid_status_transition` and `trg_prevent_posted_journal_update`.
    """
    journal_id = str(uuid.uuid4())
    lines = [
        {"account_code": "WALLET_SOL", "wallet_address": "W1", "mint_address": "SOL", "debit_amount_raw": 0, "credit_amount_raw": Decimal("100"), "usd_rate": Decimal("1.0")},
        {"account_code": "WALLET_TOKEN", "wallet_address": "W1", "mint_address": "TOK", "debit_amount_raw": Decimal("100"), "credit_amount_raw": 0, "usd_rate": Decimal("1.0")}
    ]
    clean_ledger_service.post_journal(
        journal_id=journal_id,
        idempotency_key="idem-rb-test",
        journal_type="TRANSFER",
        description="Rollback Test",
        lines=lines
    )

    conn = sqlite3.connect(clean_ledger_service.db_path)
    try:
        with pytest.raises(sqlite3.IntegrityError) as exc_info:
            conn.execute(f"UPDATE ledger_journals SET status = 'DRAFT' WHERE id = '{journal_id}'")
        assert "FINANCIAL INTEGRITY VIOLATION" in str(exc_info.value)
    finally:
        conn.close()


def test_ledger_11_direct_sql_exact_integer_balance_and_boundary_precision(clean_ledger_service):
    """
    P10.2-A.2 / Direct SQL Audit:
    Verifies exact integer balance arithmetic:
    1. Single line in draft fails post check (< 2 lines).
    2. Fractional boundary imbalance (1 unit off in 10^8 integer scale, 0.00000001 USD)
       MUST be aborted with zero floating-point epsilon tolerance.
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
                journal_id, account_code, wallet_address, mint_address,
                debit_amount_raw, credit_amount_raw, usd_rate,
                debit_usd_units, credit_usd_units
            ) VALUES ('direct_j2', 'WALLET_SOL', 'W1', 'SOL', '100', '0', '1.0', 10000000000, 0)
        """)
        with pytest.raises(sqlite3.IntegrityError) as exc_info_lines:
            conn.execute("UPDATE ledger_journals SET status = 'POSTED' WHERE id = 'direct_j2'")
        assert "Journal has fewer than 2 lines" in str(exc_info_lines.value)

        # Case 2: Boundary precision attack (1 integer unit imbalance: 100.00000000 USD vs 99.99999999 USD)
        conn.execute("""
            INSERT INTO ledger_lines (
                journal_id, account_code, wallet_address, mint_address,
                debit_amount_raw, credit_amount_raw, usd_rate,
                debit_usd_units, credit_usd_units
            ) VALUES ('direct_j2', 'WALLET_TOKEN', 'W1', 'BONK', '0', '99.99999999', '1.0', 0, 9999999999)
        """)
        with pytest.raises(sqlite3.IntegrityError) as exc_info_balance:
            conn.execute("UPDATE ledger_journals SET status = 'POSTED' WHERE id = 'direct_j2'")
        assert "Journal debits and credits do not balance in exact integer units" in str(exc_info_balance.value)
    finally:
        conn.close()


def test_ledger_12_direct_sql_line_insert_on_posted_blocked_by_db_trigger(clean_ledger_service):
    """
    P10.2-A.1 / Direct SQL Audit:
    Attempting to insert new ledger lines into an already POSTED journal via raw SQL
    MUST be aborted by SQLite trigger `trg_prevent_line_insert_on_posted`.
    """
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

    conn = sqlite3.connect(clean_ledger_service.db_path)
    try:
        with pytest.raises(sqlite3.IntegrityError) as exc_info:
            conn.execute(f"""
                INSERT INTO ledger_lines (
                    journal_id, account_code, wallet_address, mint_address,
                    debit_amount_raw, credit_amount_raw, usd_rate,
                    debit_usd_units, credit_usd_units
                ) VALUES ('{journal_id}', 'EXPENSE_NETWORK_FEE', 'W1', 'SOL', '5', '0', '1.0', 500000000, 0)
            """)
        assert "Cannot insert lines into an already POSTED journal" in str(exc_info.value)
    finally:
        conn.close()


def test_ledger_13_direct_sql_inconsistent_raw_amounts_and_rates_blocked_by_db_trigger(clean_ledger_service):
    """
    P10.2-A.3:
    Direct SQL attempt to insert fabricated integer units that balance (e.g. 100 units debits == 100 units credits)
    but do NOT match the source raw amounts and USD rates MUST be rejected by SQLite trigger.
    """
    conn = sqlite3.connect(clean_ledger_service.db_path)
    try:
        conn.execute("""
            INSERT INTO ledger_journals (
                id, transaction_signature, order_id, idempotency_key, journal_type, description, status, posted_at
            ) VALUES ('direct_inconsistent', NULL, NULL, 'idem_inconsistent', 'SWAP', 'Inconsistent units', 'DRAFT', 'now')
        """)
        # Fabricated units: both lines have 100 units (balance sums to 0), but raw amounts are 500 USD with rate 1.0!
        conn.execute("""
            INSERT INTO ledger_lines (
                journal_id, account_code, wallet_address, mint_address,
                debit_amount_raw, credit_amount_raw, usd_rate,
                debit_usd_units, credit_usd_units
            ) VALUES ('direct_inconsistent', 'WALLET_SOL', 'W1', 'SOL', '500', '0', '1.0', 100, 0)
        """)
        conn.execute("""
            INSERT INTO ledger_lines (
                journal_id, account_code, wallet_address, mint_address,
                debit_amount_raw, credit_amount_raw, usd_rate,
                debit_usd_units, credit_usd_units
            ) VALUES ('direct_inconsistent', 'WALLET_TOKEN', 'W1', 'BONK', '0', '500', '1.0', 0, 100)
        """)
        with pytest.raises(sqlite3.IntegrityError) as exc_info:
            conn.execute("UPDATE ledger_journals SET status = 'POSTED' WHERE id = 'direct_inconsistent'")
        assert "Line fixed-point USD units do not match source raw amount and USD rate" in str(exc_info.value)
    finally:
        conn.close()


def test_ledger_14_exact_fixed_point_round_half_up_and_boundary_overflow(clean_ledger_service):
    """
    P10.2-A.3:
    Verifies ROUND_HALF_UP rounding policy on exact decimal boundary conditions
    and ensures high-precision fractional amounts balance consistently down to 10^-8 USD.
    """
    journal_id = str(uuid.uuid4())
    # 0.000000005 rounds up to 1 unit (0.00000001)
    # 0.000000004 rounds down to 0 units
    lines = [
        {"account_code": "WALLET_SOL", "wallet_address": "W1", "mint_address": "SOL", "debit_amount_raw": 0, "credit_amount_raw": Decimal("100.000000005"), "usd_rate": Decimal("1.0")},
        {"account_code": "WALLET_TOKEN", "wallet_address": "W1", "mint_address": "TOK", "debit_amount_raw": Decimal("100.000000005"), "credit_amount_raw": 0, "usd_rate": Decimal("1.0")}
    ]
    res = clean_ledger_service.post_journal(
        journal_id=journal_id,
        idempotency_key="idem-round-test",
        journal_type="TRANSFER",
        description="Round half up boundary test",
        lines=lines
    )
    assert res["status"] == "POSTED"
    # Stored integer units must be 100 * 10^8 + 1 = 10000000001
    conn = sqlite3.connect(clean_ledger_service.db_path)
    try:
        rows = conn.execute("SELECT debit_usd_units, credit_usd_units FROM ledger_lines WHERE journal_id = ?", (journal_id,)).fetchall()
        assert rows[0][1] == 10000000001
        assert rows[1][0] == 10000000001
    finally:
        conn.close()


# =====================================================================
# 4. PostgreSQL Integration Tests (With real test implementation)
# =====================================================================

has_postgres_binaries = shutil.which("psql") is not None and shutil.which("initdb") is not None

@pytest.mark.skipif(
    not has_postgres_binaries,
    reason="PostgreSQL binaries (initdb/psql) not installed on host. Acceptance criterion: PostgreSQL integration remains explicitly UNVERIFIED pending environment."
)
def test_pg_ledger_deferred_trigger_integration():
    """
    PG-LEDGER-01: Full integration test for PostgreSQL Migration 004 triggers matching exact production schema:
    1. Executes migration DDL on PostgreSQL matching migrations/001_initial_schema.sql and migrations/004_p10_1_ledger_hardening.sql.
    2. Verifies trg_prevent_journal_truncate blocks TRUNCATE with append-only exception.
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

        # Schema matching migrations/001_initial_schema.sql
        cur.execute("""
            CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

            CREATE TABLE IF NOT EXISTS chain_transactions (
                signature TEXT PRIMARY KEY
            );

            CREATE TABLE IF NOT EXISTS live_orders (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid()
            );

            CREATE TABLE IF NOT EXISTS live_ledger_journals (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                transaction_signature TEXT NOT NULL UNIQUE REFERENCES chain_transactions(signature),
                order_id UUID REFERENCES live_orders(id),
                idempotency_key TEXT NOT NULL UNIQUE,
                journal_type TEXT NOT NULL CHECK (journal_type IN ('TRADE_BUY', 'TRADE_SELL', 'FEE_EXPENSE', 'TRANSFER')),
                description TEXT NOT NULL,
                posted_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );

            CREATE TABLE IF NOT EXISTS live_ledger_lines (
                id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                journal_id UUID NOT NULL REFERENCES live_ledger_journals(id) ON DELETE CASCADE,
                account_code TEXT NOT NULL CHECK (account_code IN (
                    'WALLET_SOL', 'WALLET_TOKEN', 'EXPENSE_NETWORK_FEE', 'EXPENSE_DEX_FEE', 'REALIZED_PNL'
                )),
                wallet_address TEXT NOT NULL,
                mint_address TEXT NOT NULL,
                debit_amount_raw NUMERIC(40,0) NOT NULL DEFAULT 0,
                credit_amount_raw NUMERIC(40,0) NOT NULL DEFAULT 0,
                usd_rate NUMERIC(30,12) NOT NULL,
                CONSTRAINT chk_debit_credit_positive CHECK (debit_amount_raw >= 0 AND credit_amount_raw >= 0),
                CONSTRAINT chk_at_least_one_side CHECK (debit_amount_raw > 0 OR credit_amount_raw > 0)
            );
        """)

        migration_path = os.path.abspath("./migrations/004_p10_1_ledger_hardening.sql")
        with open(migration_path, "r", encoding="utf-8") as f:
            cur.execute(f.read())

        # 1. TRUNCATE protection
        with pytest.raises(psycopg2.DatabaseError) as exc_info:
            cur.execute("TRUNCATE live_ledger_journals;")
        assert "strictly append-only" in str(exc_info.value)

        # 2. Line count protection (< 2 lines)
        conn.autocommit = False
        j_id = str(uuid.uuid4())
        cur.execute("INSERT INTO chain_transactions (signature) VALUES (%s) ON CONFLICT DO NOTHING;", ("sig-pg-1",))
        cur.execute(
            "INSERT INTO live_ledger_journals (id, transaction_signature, idempotency_key, journal_type, description) VALUES (%s, %s, %s, %s, %s);",
            (j_id, "sig-pg-1", "idem-pg-1", "TRADE_BUY", "test journal 1")
        )
        cur.execute(
            "INSERT INTO live_ledger_lines (journal_id, account_code, wallet_address, mint_address, debit_amount_raw, credit_amount_raw, usd_rate) VALUES (%s, %s, %s, %s, %s, %s, %s);",
            (j_id, "WALLET_SOL", "addr1", "mint1", 100, 0, 1.0)
        )
        with pytest.raises(psycopg2.DatabaseError) as exc_info:
            conn.commit()
        assert "must contain at least 2 lines" in str(exc_info.value)
        conn.rollback()

        # 3. Double-entry balance protection (debits != credits)
        j_id2 = str(uuid.uuid4())
        cur.execute("INSERT INTO chain_transactions (signature) VALUES (%s) ON CONFLICT DO NOTHING;", ("sig-pg-2",))
        cur.execute(
            "INSERT INTO live_ledger_journals (id, transaction_signature, idempotency_key, journal_type, description) VALUES (%s, %s, %s, %s, %s);",
            (j_id2, "sig-pg-2", "idem-pg-2", "TRADE_BUY", "test journal 2")
        )
        cur.execute(
            "INSERT INTO live_ledger_lines (journal_id, account_code, wallet_address, mint_address, debit_amount_raw, credit_amount_raw, usd_rate) VALUES (%s, %s, %s, %s, %s, %s, %s);",
            (j_id2, "WALLET_SOL", "addr1", "mint1", 100, 0, 1.0)
        )
        cur.execute(
            "INSERT INTO live_ledger_lines (journal_id, account_code, wallet_address, mint_address, debit_amount_raw, credit_amount_raw, usd_rate) VALUES (%s, %s, %s, %s, %s, %s, %s);",
            (j_id2, "WALLET_TOKEN", "addr1", "mint2", 0, 80, 1.0)
        )
        with pytest.raises(psycopg2.DatabaseError) as exc_info:
            conn.commit()
        assert "is unbalanced" in str(exc_info.value)
        conn.rollback()

        # 4. Balanced journal cleanly commits
        j_id3 = str(uuid.uuid4())
        cur.execute("INSERT INTO chain_transactions (signature) VALUES (%s) ON CONFLICT DO NOTHING;", ("sig-pg-3",))
        cur.execute(
            "INSERT INTO live_ledger_journals (id, transaction_signature, idempotency_key, journal_type, description) VALUES (%s, %s, %s, %s, %s);",
            (j_id3, "sig-pg-3", "idem-pg-3", "TRADE_BUY", "test journal 3")
        )
        cur.execute(
            "INSERT INTO live_ledger_lines (journal_id, account_code, wallet_address, mint_address, debit_amount_raw, credit_amount_raw, usd_rate) VALUES (%s, %s, %s, %s, %s, %s, %s);",
            (j_id3, "WALLET_SOL", "addr1", "mint1", 100, 0, 1.0)
        )
        cur.execute(
            "INSERT INTO live_ledger_lines (journal_id, account_code, wallet_address, mint_address, debit_amount_raw, credit_amount_raw, usd_rate) VALUES (%s, %s, %s, %s, %s, %s, %s);",
            (j_id3, "WALLET_TOKEN", "addr1", "mint2", 0, 100, 1.0)
        )
        conn.commit()

        cur.close()
        conn.close()
