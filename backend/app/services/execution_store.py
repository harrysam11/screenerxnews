import os
import json
import sqlite3
import threading
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import hashlib
from fastapi import HTTPException
from app.core.config import settings
from app.services.execution_interfaces import ChainSignatureVerifier

def build_canonical_intent_message(
    intent_id: str,
    chain_id: str,
    wallet_address: str,
    input_mint: str,
    output_mint: str,
    input_amount_raw: Any,
    max_slippage_bps: int,
    destination_router: str,
    asset_id: str,
    pool_id: str,
    side: str,
    strategy_id: str,
    strategy_version: str,
    nonce: str,
    expires_at: str
) -> str:
    """
    Constructs a deterministic canonical string containing every authorization-critical field.
    Binds intent_id, chain_id, wallet, mints, asset_id, pool_id, side, amount, slippage,
    router, strategy_id, strategy_version, nonce, and expiration.
    """
    wallet_norm = wallet_address.lower().strip() if chain_id.startswith("eip155:") else wallet_address.strip()
    return (
        f"intent_id={intent_id}&"
        f"chain_id={chain_id}&"
        f"wallet={wallet_norm}&"
        f"in_token={input_mint}&"
        f"out_token={output_mint}&"
        f"amount={input_amount_raw}&"
        f"slippage={max_slippage_bps}&"
        f"router={destination_router}&"
        f"asset_id={asset_id}&"
        f"pool_id={pool_id}&"
        f"side={side}&"
        f"strategy_id={strategy_id}&"
        f"strategy_version={strategy_version}&"
        f"nonce={nonce}&"
        f"expires_at={expires_at}"
    )

def compute_intent_digest(canonical_message: str) -> str:
    return hashlib.sha256(canonical_message.encode("utf-8")).hexdigest()

class IntentLifecycleState:
    CREATED = "CREATED"
    POLICY_REJECTED = "POLICY_REJECTED"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    APPROVED = "APPROVED"
    SIGNED = "SIGNED"
    BROADCAST = "BROADCAST"
    CONFIRMED = "CONFIRMED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"

class ExecutionIntentStore:
    """
    Persistent, thread-safe execution intent store with atomic consumption,
    state transition verification, and zero private-key storage.
    """
    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            self.db_path = settings.get_canonical_sqlite_path()
        else:
            self.db_path = db_path
        if self.db_path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)

        self._lock = threading.Lock()
        self._verifier = ChainSignatureVerifier()
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10.0, check_same_thread=False, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    def _init_db(self):
        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute("BEGIN IMMEDIATE;")
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS execution_intents (
                        intent_id TEXT PRIMARY KEY,
                        chain_id TEXT NOT NULL,
                        wallet_address TEXT NOT NULL,
                        asset_id TEXT NOT NULL,
                        pool_id TEXT NOT NULL,
                        side TEXT NOT NULL,
                        input_mint TEXT NOT NULL,
                        output_mint TEXT NOT NULL,
                        input_amount_raw TEXT NOT NULL,
                        max_slippage_bps INTEGER NOT NULL,
                        destination_router TEXT,
                        strategy_id TEXT,
                        strategy_version TEXT,
                        nonce TEXT,
                        canonical_intent_digest TEXT NOT NULL,
                        transaction_message_hash TEXT NOT NULL,
                        status TEXT NOT NULL,
                        policy_checks_passed INTEGER NOT NULL,
                        rejection_reasons TEXT,
                        created_at TEXT NOT NULL,
                        expires_at TEXT NOT NULL,
                        is_consumed INTEGER NOT NULL DEFAULT 0,
                        consumed_at TEXT
                    );
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS execution_intent_outbox (
                        intent_id TEXT PRIMARY KEY,
                        idempotency_key TEXT NOT NULL UNIQUE,
                        payload_json TEXT NOT NULL,
                        status TEXT NOT NULL,
                        attempts INTEGER NOT NULL DEFAULT 0,
                        last_error TEXT,
                        created_at TEXT NOT NULL,
                        posted_at TEXT
                    );
                """)
                conn.execute("COMMIT;")
            except Exception:
                try:
                    conn.execute("ROLLBACK;")
                except Exception:
                    pass
                raise
            finally:
                conn.close()

    def save_intent(self, intent_data: Dict[str, Any]) -> None:
        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute("BEGIN IMMEDIATE;")
                reasons_str = ",".join(intent_data.get("rejection_reasons", []))
                conn.execute("""
                    INSERT INTO execution_intents (
                        intent_id, chain_id, wallet_address, asset_id, pool_id, side,
                        input_mint, output_mint, input_amount_raw, max_slippage_bps,
                        destination_router, strategy_id, strategy_version, nonce,
                        canonical_intent_digest, transaction_message_hash, status,
                        policy_checks_passed, rejection_reasons, created_at, expires_at,
                        is_consumed, consumed_at
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?,
                        ?, ?, ?, ?,
                        ?, ?, ?,
                        ?, ?, ?, ?,
                        ?, ?
                    )
                """, (
                    intent_data["intent_id"],
                    intent_data.get("chain_id", "solana:mainnet"),
                    intent_data["wallet_address"],
                    intent_data.get("asset_id", ""),
                    intent_data.get("pool_id", ""),
                    intent_data.get("side", "BUY"),
                    intent_data["input_mint"],
                    intent_data["output_mint"],
                    str(intent_data["input_amount_raw"]),
                    intent_data.get("max_slippage_bps", 150),
                    intent_data.get("destination_router", "default"),
                    intent_data.get("strategy_id", "direct"),
                    intent_data.get("strategy_version", "1.0"),
                    intent_data.get("nonce", ""),
                    intent_data["canonical_intent_digest"],
                    intent_data["transaction_message_hash"],
                    intent_data["status"],
                    1 if intent_data.get("policy_checks_passed", True) else 0,
                    reasons_str,
                    intent_data["created_at"].isoformat() if isinstance(intent_data["created_at"], datetime) else intent_data["created_at"],
                    intent_data["expires_at"].isoformat() if isinstance(intent_data["expires_at"], datetime) else intent_data["expires_at"],
                    1 if intent_data.get("is_consumed", False) else 0,
                    intent_data.get("consumed_at")
                ))
                conn.execute("COMMIT;")
            except Exception:
                try:
                    conn.execute("ROLLBACK;")
                except Exception:
                    pass
                raise
            finally:
                conn.close()

    def get_intent(self, intent_id: str) -> Optional[Dict[str, Any]]:
        conn = self._get_connection()
        try:
            cur = conn.execute("SELECT * FROM execution_intents WHERE intent_id = ?", (intent_id,))
            row = cur.fetchone()
            if not row:
                return None
            data = dict(row)
            data["policy_checks_passed"] = bool(data["policy_checks_passed"])
            data["is_consumed"] = bool(data["is_consumed"])
            data["rejection_reasons"] = data["rejection_reasons"].split(",") if data["rejection_reasons"] else []
            return data
        finally:
            conn.close()

    def atomic_approve_intent(
        self,
        intent_id: str,
        signed_by_address: str,
        transaction_message_hash: str,
        signature_hex: Optional[str] = None,
        journal_payload: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Executes atomic intent approval with full zero-trust validation:
        - Transaction Isolation: Starts SQLite BEGIN IMMEDIATE before reading intent to lock out concurrent writers.
        - Fail-Closed Policy: Empty or missing burner wallet allowlist fails closed.
        - EVM Normalization: Consistent lowercasing for EVM addresses, strict case sensitivity for Solana.
        - Canonical Reconstruction: Rebuilds canonical message from database fields and verifies digest.
        - Cryptographic Verification: Validates single EIP-191 personal sign format for EVM or Ed25519 for Solana.
        - Durable Outbox: If journal_payload provided, inserts into execution_intent_outbox in the same transaction.
        """
        with self._lock:
            conn = self._get_connection()
            try:
                # 0. Acquire SQLite write reservation BEFORE reading to prevent cross-process race conditions
                conn.execute("BEGIN IMMEDIATE;")
                cur = conn.execute("SELECT * FROM execution_intents WHERE intent_id = ?", (intent_id,))
                row = cur.fetchone()
                if not row:
                    raise HTTPException(
                        status_code=404,
                        detail="SECURITY VIOLATION: Trade intent not found or never issued."
                    )

                intent = dict(row)

                # 1. EXEC-01: Rejected intent can NEVER become approved
                if intent["status"] == IntentLifecycleState.POLICY_REJECTED or intent["policy_checks_passed"] == 0:
                    raise HTTPException(
                        status_code=422,
                        detail="SECURITY VIOLATION (EXEC-01): Cannot approve intent that failed risk policy checks."
                    )

                # 2. Check if intent is in valid state for approval
                if intent["status"] != IntentLifecycleState.AWAITING_APPROVAL:
                    if intent["is_consumed"] == 1 or intent["status"] == IntentLifecycleState.APPROVED:
                        raise HTTPException(
                            status_code=409,
                            detail="SECURITY VIOLATION (EXEC-05): Trade approval already consumed (Replay attack prevented)."
                        )
                    raise HTTPException(
                        status_code=400,
                        detail=f"SECURITY VIOLATION: Intent in invalid state for approval: {intent['status']}."
                    )

                # 3. EXEC-04: Expired intent rejection (permanently marked consumed and expired)
                expires_at = datetime.fromisoformat(intent["expires_at"])
                now = datetime.now(timezone.utc)
                if now > expires_at:
                    conn.execute("UPDATE execution_intents SET status = ?, is_consumed = 1 WHERE intent_id = ?", (IntentLifecycleState.EXPIRED, intent_id))
                    conn.execute("COMMIT;")
                    raise HTTPException(
                        status_code=410,
                        detail="SECURITY VIOLATION (EXEC-04): Trade intent has expired. Re-approval rejected."
                    )

                # 4. Wallet address normalization & policy checks
                is_evm = intent["chain_id"].startswith("eip155:")
                norm_signer = signed_by_address.lower().strip() if is_evm else signed_by_address.strip()
                norm_intent_wallet = intent["wallet_address"].lower().strip() if is_evm else intent["wallet_address"].strip()

                # Cold Vault Hard Reject (fail closed)
                cold_vaults = [
                    (cv.lower().strip() if is_evm else cv.strip())
                    for cv in settings.COLD_VAULT_ADDRESSES
                ]
                if norm_signer in cold_vaults:
                    raise HTTPException(
                        status_code=403,
                        detail="SECURITY VIOLATION: Cold vault signatures are permanently forbidden."
                    )

                # EXEC-02: Signer must match originally authorized wallet
                if norm_signer != norm_intent_wallet:
                    raise HTTPException(
                        status_code=403,
                        detail="SECURITY VIOLATION (EXEC-02): Signer address does not match the authorized wallet."
                    )

                # Burner Wallet Whitelist Verification (FAIL-CLOSED)
                if not settings.BURNER_WALLET_ADDRESSES:
                    raise HTTPException(
                        status_code=403,
                        detail="SECURITY VIOLATION: Burner wallet allowlist is empty or unconfigured (fail-closed policy)."
                    )

                burner_allowlist = [
                    (bw.lower().strip() if is_evm else bw.strip())
                    for bw in settings.BURNER_WALLET_ADDRESSES
                ]
                if norm_signer not in burner_allowlist:
                    raise HTTPException(
                        status_code=403,
                        detail="SECURITY VIOLATION: Signer is not an authorized burner wallet address."
                    )

                # 5. Cryptographic Signature Verification (MANDATORY presence check)
                if not signature_hex or not signature_hex.strip():
                    raise HTTPException(
                        status_code=403,
                        detail="SECURITY VIOLATION (EXEC-02): Cryptographic signature is mandatory for intent approval."
                    )

                # 6. Independent Canonical Message Reconstruction (EXEC-03 / P10.2-A.2)
                reconstructed_msg = build_canonical_intent_message(
                    intent_id=intent["intent_id"],
                    chain_id=intent["chain_id"],
                    wallet_address=intent["wallet_address"],
                    input_mint=intent["input_mint"],
                    output_mint=intent["output_mint"],
                    input_amount_raw=intent["input_amount_raw"],
                    max_slippage_bps=intent["max_slippage_bps"],
                    destination_router=intent["destination_router"],
                    asset_id=intent["asset_id"],
                    pool_id=intent["pool_id"],
                    side=intent["side"],
                    strategy_id=intent["strategy_id"],
                    strategy_version=intent["strategy_version"],
                    nonce=intent["nonce"],
                    expires_at=intent["expires_at"]
                )
                recomputed_digest = compute_intent_digest(reconstructed_msg)

                if recomputed_digest != intent["canonical_intent_digest"]:
                    raise HTTPException(
                        status_code=400,
                        detail="SECURITY VIOLATION: Stored intent digest does not match reconstructed canonical intent fields."
                    )

                expected_tx_hash = hashlib.sha256(recomputed_digest.encode("utf-8")).hexdigest()
                if transaction_message_hash != expected_tx_hash or transaction_message_hash != intent["transaction_message_hash"]:
                    raise HTTPException(
                        status_code=400,
                        detail="SECURITY VIOLATION (EXEC-03): Transaction message hash does not match canonical intent digest."
                    )

                # 7. Cryptographic Signature Verification
                clean_sig = signature_hex.strip()
                if clean_sig.startswith("0x"):
                    clean_sig = clean_sig[2:]

                try:
                    sig_bytes = bytes.fromhex(clean_sig)
                except Exception:
                    raise HTTPException(
                        status_code=400,
                        detail="SECURITY VIOLATION: Invalid hexadecimal signature encoding."
                    )

                msg_bytes = recomputed_digest.encode("utf-8")
                is_valid = self._verifier.verify(
                    chain_id=intent["chain_id"],
                    signer_address=signed_by_address,
                    message_bytes=msg_bytes,
                    signature_bytes=sig_bytes
                )
                if not is_valid:
                    raise HTTPException(
                        status_code=400,
                        detail="SECURITY VIOLATION (EXEC-03): Cryptographic signature verification failed or signer address mismatch."
                    )

                # 8. EXEC-05 & EXEC-06: Atomic state transition within BEGIN IMMEDIATE transaction
                update_cur = conn.execute("""
                    UPDATE execution_intents
                    SET status = ?, is_consumed = 1, consumed_at = ?
                    WHERE intent_id = ? AND status = ? AND is_consumed = 0
                """, (
                    IntentLifecycleState.APPROVED,
                    now.isoformat(),
                    intent_id,
                    IntentLifecycleState.AWAITING_APPROVAL
                ))

                if update_cur.rowcount == 0:
                    raise HTTPException(
                        status_code=409,
                        detail="SECURITY VIOLATION (EXEC-06): Trade approval race condition detected. Already consumed."
                    )

                outbox_recorded = False
                if journal_payload:
                    idempotency_key = journal_payload.get("idempotency_key") or f"outbox_{intent_id}"
                    payload_json = json.dumps(journal_payload)
                    conn.execute("""
                        INSERT INTO execution_intent_outbox (
                            intent_id, idempotency_key, payload_json, status, attempts, last_error, created_at
                        ) VALUES (?, ?, ?, 'PENDING', 0, NULL, ?);
                    """, (intent_id, idempotency_key, payload_json, now.isoformat()))
                    outbox_recorded = True

                conn.execute("COMMIT;")
                resp = {
                    "intent_id": intent_id,
                    "status": "APPROVED",
                    "is_consumed": True,
                    "consumed_at": now.isoformat()
                }
                if outbox_recorded:
                    resp["outbox_status"] = "PENDING"
                return resp
            except Exception:
                try:
                    conn.execute("ROLLBACK;")
                except Exception:
                    pass
                raise
            finally:
                conn.close()

    def atomic_approve_and_post_journal(
        self,
        intent_id: str,
        signed_by_address: str,
        transaction_message_hash: str,
        signature_hex: Optional[str] = None,
        journal_payload: Optional[Dict[str, Any]] = None,
        ledger_service: Optional[Any] = None
    ) -> Dict[str, Any]:
        """
        Executes intent approval AND double-entry financial ledger journal posting
        within a single, unified SQLite transaction on a single connection.
        Guarantees absolute atomicity: either both succeed or both rollback together.
        """
        if ledger_service is None:
            from app.services.ledger_service import double_entry_ledger_service
            ledger_service = double_entry_ledger_service

        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute("BEGIN IMMEDIATE;")
                cur = conn.execute("SELECT * FROM execution_intents WHERE intent_id = ?", (intent_id,))
                row = cur.fetchone()
                if not row:
                    raise HTTPException(
                        status_code=404,
                        detail="SECURITY VIOLATION: Trade intent not found or never issued."
                    )

                intent = dict(row)

                if intent["status"] == IntentLifecycleState.POLICY_REJECTED or intent["policy_checks_passed"] == 0:
                    raise HTTPException(
                        status_code=422,
                        detail="SECURITY VIOLATION (EXEC-01): Cannot approve intent that failed risk policy checks."
                    )

                if intent["status"] != IntentLifecycleState.AWAITING_APPROVAL:
                    if intent["is_consumed"] == 1 or intent["status"] == IntentLifecycleState.APPROVED:
                        raise HTTPException(
                            status_code=409,
                            detail="SECURITY VIOLATION (EXEC-05): Trade approval already consumed (Replay attack prevented)."
                        )
                    raise HTTPException(
                        status_code=400,
                        detail=f"SECURITY VIOLATION: Intent in invalid state for approval: {intent['status']}."
                    )

                expires_at = datetime.fromisoformat(intent["expires_at"])
                now = datetime.now(timezone.utc)
                if now > expires_at:
                    conn.execute("UPDATE execution_intents SET status = ?, is_consumed = 1 WHERE intent_id = ?", (IntentLifecycleState.EXPIRED, intent_id))
                    conn.execute("COMMIT;")
                    raise HTTPException(
                        status_code=410,
                        detail="SECURITY VIOLATION (EXEC-04): Trade intent has expired. Re-approval rejected."
                    )

                is_evm = intent["chain_id"].startswith("eip155:")
                norm_signer = signed_by_address.lower().strip() if is_evm else signed_by_address.strip()
                norm_intent_wallet = intent["wallet_address"].lower().strip() if is_evm else intent["wallet_address"].strip()

                cold_vaults = [
                    (cv.lower().strip() if is_evm else cv.strip())
                    for cv in settings.COLD_VAULT_ADDRESSES
                ]
                if norm_signer in cold_vaults:
                    raise HTTPException(
                        status_code=403,
                        detail="SECURITY VIOLATION: Cold vault signatures are permanently forbidden."
                    )

                if norm_signer != norm_intent_wallet:
                    raise HTTPException(
                        status_code=403,
                        detail="SECURITY VIOLATION (EXEC-02): Signer address does not match the authorized wallet."
                    )

                if not settings.BURNER_WALLET_ADDRESSES:
                    raise HTTPException(
                        status_code=403,
                        detail="SECURITY VIOLATION: Burner wallet allowlist is empty or unconfigured (fail-closed policy)."
                    )

                burner_allowlist = [
                    (bw.lower().strip() if is_evm else bw.strip())
                    for bw in settings.BURNER_WALLET_ADDRESSES
                ]
                if norm_signer not in burner_allowlist:
                    raise HTTPException(
                        status_code=403,
                        detail="SECURITY VIOLATION: Signer is not an authorized burner wallet address."
                    )

                if not signature_hex or not signature_hex.strip():
                    raise HTTPException(
                        status_code=403,
                        detail="SECURITY VIOLATION (EXEC-02): Cryptographic signature is mandatory for intent approval."
                    )

                reconstructed_msg = build_canonical_intent_message(
                    intent_id=intent["intent_id"],
                    chain_id=intent["chain_id"],
                    wallet_address=intent["wallet_address"],
                    input_mint=intent["input_mint"],
                    output_mint=intent["output_mint"],
                    input_amount_raw=intent["input_amount_raw"],
                    max_slippage_bps=intent["max_slippage_bps"],
                    destination_router=intent["destination_router"],
                    asset_id=intent["asset_id"],
                    pool_id=intent["pool_id"],
                    side=intent["side"],
                    strategy_id=intent["strategy_id"],
                    strategy_version=intent["strategy_version"],
                    nonce=intent["nonce"],
                    expires_at=intent["expires_at"]
                )
                recomputed_digest = compute_intent_digest(reconstructed_msg)

                if recomputed_digest != intent["canonical_intent_digest"]:
                    raise HTTPException(
                        status_code=400,
                        detail="SECURITY VIOLATION: Stored intent digest does not match reconstructed canonical intent fields."
                    )

                expected_tx_hash = hashlib.sha256(recomputed_digest.encode("utf-8")).hexdigest()
                if transaction_message_hash != expected_tx_hash or transaction_message_hash != intent["transaction_message_hash"]:
                    raise HTTPException(
                        status_code=400,
                        detail="SECURITY VIOLATION (EXEC-03): Transaction message hash does not match canonical intent digest."
                    )

                clean_sig = signature_hex.strip()
                if clean_sig.startswith("0x"):
                    clean_sig = clean_sig[2:]

                try:
                    sig_bytes = bytes.fromhex(clean_sig)
                except Exception:
                    raise HTTPException(
                        status_code=400,
                        detail="SECURITY VIOLATION: Invalid hexadecimal signature encoding."
                    )

                msg_bytes = recomputed_digest.encode("utf-8")
                is_valid = self._verifier.verify(
                    chain_id=intent["chain_id"],
                    signer_address=signed_by_address,
                    message_bytes=msg_bytes,
                    signature_bytes=sig_bytes
                )
                if not is_valid:
                    raise HTTPException(
                        status_code=400,
                        detail="SECURITY VIOLATION (EXEC-03): Cryptographic signature verification failed or signer address mismatch."
                    )

                # 1. Update intent state to APPROVED
                update_cur = conn.execute("""
                    UPDATE execution_intents
                    SET status = ?, is_consumed = 1, consumed_at = ?
                    WHERE intent_id = ? AND status = ? AND is_consumed = 0
                """, (
                    IntentLifecycleState.APPROVED,
                    now.isoformat(),
                    intent_id,
                    IntentLifecycleState.AWAITING_APPROVAL
                ))

                if update_cur.rowcount == 0:
                    raise HTTPException(
                        status_code=409,
                        detail="SECURITY VIOLATION (EXEC-06): Trade approval race condition detected. Already consumed."
                    )

                journal_res = None
                if journal_payload:
                    # 2. Post journal inside the SAME SQLite connection & transaction
                    journal_res = ledger_service.post_journal_in_connection(
                        conn=conn,
                        journal_id=journal_payload["journal_id"],
                        idempotency_key=journal_payload["idempotency_key"],
                        journal_type=journal_payload.get("journal_type", "SWAP"),
                        description=journal_payload.get("description", f"Intent {intent_id} execution"),
                        lines=journal_payload["lines"],
                        transaction_signature=journal_payload.get("transaction_signature"),
                        order_id=intent_id
                    )

                    # 3. Insert outbox record with POSTED status
                    conn.execute("""
                        INSERT INTO execution_intent_outbox (
                            intent_id, idempotency_key, payload_json, status, attempts, last_error, created_at, posted_at
                        ) VALUES (?, ?, ?, 'POSTED', 1, NULL, ?, ?);
                    """, (intent_id, journal_payload["idempotency_key"], json.dumps(journal_payload), now.isoformat(), now.isoformat()))

                conn.execute("COMMIT;")
                return {
                    "intent_id": intent_id,
                    "status": "APPROVED",
                    "is_consumed": True,
                    "consumed_at": now.isoformat(),
                    "journal": journal_res
                }
            except Exception:
                try:
                    conn.execute("ROLLBACK;")
                except Exception:
                    pass
                raise
            finally:
                conn.close()

    def reconcile_unposted_intents(self, ledger_service: Any) -> Dict[str, Any]:
        """
        Durable crash recovery and reconciliation (Phase P10.2-B.1):
        Audits execution intents and durable outbox against the double-entry financial ledger.
        - Drains pending outbox records to post journals idempotently.
        - If intent has expired without ledger posting, marks intent EXPIRED with is_consumed = 1
          (never is_consumed = 0; replay is permanently prevented).
        """
        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute("BEGIN IMMEDIATE;")
                now = datetime.now(timezone.utc)
                outbox_recovered = 0
                outbox_terminal_failed = 0
                reconciled = []
                expired = []

                # 1. Drain pending outbox records
                cur_outbox = conn.execute("SELECT * FROM execution_intent_outbox WHERE status = 'PENDING';")
                pending_outbox = [dict(r) for r in cur_outbox.fetchall()]

                for ob in pending_outbox:
                    ob_intent_id = ob["intent_id"]
                    cur_intent = conn.execute("SELECT * FROM execution_intents WHERE intent_id = ?;", (ob_intent_id,))
                    intent_row = cur_intent.fetchone()
                    if not intent_row:
                        continue

                    intent_data = dict(intent_row)
                    expires_at = datetime.fromisoformat(intent_data["expires_at"])
                    if now > expires_at:
                        conn.execute(
                            "UPDATE execution_intent_outbox SET status = 'EXPIRED_UNPOSTED', last_error = 'Expired before posting' WHERE intent_id = ?;",
                            (ob_intent_id,)
                        )
                        conn.execute(
                            "UPDATE execution_intents SET status = ?, is_consumed = 1 WHERE intent_id = ?;",
                            (IntentLifecycleState.EXPIRED, ob_intent_id)
                        )
                        expired.append(ob_intent_id)
                        continue

                    # Attempt posting via ledger_service
                    try:
                        payload = json.loads(ob["payload_json"])
                        if hasattr(ledger_service, "post_journal_in_connection"):
                            ledger_service.post_journal_in_connection(conn=conn, **payload)
                        else:
                            ledger_service.post_journal(**payload)
                        conn.execute(
                            "UPDATE execution_intent_outbox SET status = 'POSTED', posted_at = ? WHERE intent_id = ?;",
                            (now.isoformat(), ob_intent_id)
                        )
                        outbox_recovered += 1
                    except Exception as e:
                        err_str = str(e).lower()
                        if "already consumed" in err_str or "duplicate" in err_str:
                            conn.execute(
                                "UPDATE execution_intent_outbox SET status = 'POSTED', posted_at = ? WHERE intent_id = ?;",
                                (now.isoformat(), ob_intent_id)
                            )
                            outbox_recovered += 1
                        elif "violation" in err_str or "unbalanced" in err_str:
                            conn.execute(
                                "UPDATE execution_intent_outbox SET status = 'FAILED_TERMINAL', last_error = ? WHERE intent_id = ?;",
                                (str(e), ob_intent_id)
                            )
                            conn.execute(
                                "UPDATE execution_intents SET status = 'FAILED_LEDGER_POSTING' WHERE intent_id = ?;",
                                (ob_intent_id,)
                            )
                            outbox_terminal_failed += 1
                        else:
                            conn.execute(
                                "UPDATE execution_intent_outbox SET attempts = attempts + 1, last_error = ? WHERE intent_id = ?;",
                                (str(e), ob_intent_id)
                            )

                # 2. Audit all approved intents against ledger
                cur = conn.execute("SELECT * FROM execution_intents WHERE status = ?;", (IntentLifecycleState.APPROVED,))
                approved_rows = [dict(r) for r in cur.fetchall()]

                for intent in approved_rows:
                    i_id = intent["intent_id"]
                    journal = None
                    try:
                        journal = ledger_service.get_journal_by_order_id(i_id)
                    except Exception:
                        pass

                    if not journal:
                        expires_at = datetime.fromisoformat(intent["expires_at"])
                        if now > expires_at:
                            # CRITICAL FIX: Keep is_consumed = 1. Expired intents must NEVER return to approvable state.
                            conn.execute(
                                "UPDATE execution_intents SET status = ?, is_consumed = 1 WHERE intent_id = ?;",
                                (IntentLifecycleState.EXPIRED, i_id)
                            )
                            expired.append(i_id)
                        else:
                            reconciled.append({
                                "intent_id": i_id,
                                "action": "PENDING_JOURNAL",
                                "status": "APPROVED_AWAITING_POSTING"
                            })
                    else:
                        reconciled.append({
                            "intent_id": i_id,
                            "action": "VERIFIED_POSTED",
                            "journal_id": journal.get("id")
                        })

                conn.execute("COMMIT;")
                return {
                    "total_approved_audited": len(approved_rows),
                    "reconciled_count": len(reconciled),
                    "expired_unposted_recovered": len(expired),
                    "expired_intent_ids": expired,
                    "outbox_recovered": outbox_recovered,
                    "outbox_terminal_failed": outbox_terminal_failed,
                    "details": reconciled
                }
            except Exception:
                try:
                    conn.execute("ROLLBACK;")
                except Exception:
                    pass
                raise
            finally:
                conn.close()

execution_intent_store = ExecutionIntentStore()
