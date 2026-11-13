import os
import sqlite3
import threading
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from fastapi import HTTPException
from app.core.config import settings
from app.services.execution_interfaces import ChainSignatureVerifier

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
            # Default to local data directory
            base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../data"))
            os.makedirs(base_dir, exist_ok=True)
            self.db_path = os.path.join(base_dir, "execution_intents.db")
        else:
            self.db_path = db_path

        self._lock = threading.Lock()
        self._verifier = ChainSignatureVerifier()
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    def _init_db(self):
        with self._lock:
            with self._get_connection() as conn:
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
                conn.commit()

    def save_intent(self, intent_data: Dict[str, Any]) -> None:
        with self._lock:
            with self._get_connection() as conn:
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
                conn.commit()

    def get_intent(self, intent_id: str) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            cur = conn.execute("SELECT * FROM execution_intents WHERE intent_id = ?", (intent_id,))
            row = cur.fetchone()
            if not row:
                return None
            data = dict(row)
            data["policy_checks_passed"] = bool(data["policy_checks_passed"])
            data["is_consumed"] = bool(data["is_consumed"])
            data["rejection_reasons"] = data["rejection_reasons"].split(",") if data["rejection_reasons"] else []
            return data

    def atomic_approve_intent(
        self,
        intent_id: str,
        signed_by_address: str,
        transaction_message_hash: str,
        signature_hex: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Executes atomic intent approval with full zero-trust validation:
        - EXEC-01: Rejected intents cannot be approved (throws 422).
        - EXEC-02: Signer must match the authorized wallet (throws 403).
        - EXEC-03: Changed message hash / payload invalidates approval (throws 400).
        - EXEC-04: Expired intent is rejected (throws 410).
        - EXEC-05 & EXEC-06: Atomic consumption prevents duplicate/concurrent approval (throws 409).
        """
        with self._lock:
            with self._get_connection() as conn:
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

                # 3. EXEC-04: Expired intent rejection
                expires_at = datetime.fromisoformat(intent["expires_at"])
                now = datetime.now(timezone.utc)
                if now > expires_at:
                    conn.execute("UPDATE execution_intents SET status = ? WHERE intent_id = ?", (IntentLifecycleState.EXPIRED, intent_id))
                    conn.commit()
                    raise HTTPException(
                        status_code=410,
                        detail="SECURITY VIOLATION (EXEC-04): Trade intent has expired. Re-approval rejected."
                    )

                # 4. EXEC-03: Transaction message hash mismatch
                if transaction_message_hash != intent["transaction_message_hash"]:
                    raise HTTPException(
                        status_code=400,
                        detail="SECURITY VIOLATION (EXEC-03): Transaction message hash does not match canonical intent digest."
                    )

                # 5. Cold Vault Hard Reject
                if signed_by_address in settings.COLD_VAULT_ADDRESSES:
                    raise HTTPException(
                        status_code=403,
                        detail="SECURITY VIOLATION: Cold vault signatures are permanently forbidden."
                    )

                # 6. EXEC-02: Signer must match originally authorized wallet
                if signed_by_address != intent["wallet_address"]:
                    raise HTTPException(
                        status_code=403,
                        detail="SECURITY VIOLATION (EXEC-02): Signer address does not match the authorized wallet."
                    )

                # 7. Burner Wallet Whitelist Verification
                if settings.BURNER_WALLET_ADDRESSES and signed_by_address not in settings.BURNER_WALLET_ADDRESSES:
                    raise HTTPException(
                        status_code=403,
                        detail="SECURITY VIOLATION: Signer is not an authorized burner wallet address."
                    )

                # 8. Cryptographic Signature Verification
                if signature_hex:
                    sig_bytes = bytes.fromhex(signature_hex)
                    msg_bytes = intent["canonical_intent_digest"].encode()
                    is_valid = self._verifier.verify(
                        chain_id=intent["chain_id"],
                        signer_address=signed_by_address,
                        message_bytes=msg_bytes,
                        signature_bytes=sig_bytes
                    )
                    if not is_valid:
                        raise HTTPException(
                            status_code=400,
                            detail="SECURITY VIOLATION: Cryptographic signature verification failed."
                        )

                # 9. EXEC-05 & EXEC-06: Atomic consumption in database
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
                conn.commit()

                if update_cur.rowcount == 0:
                    raise HTTPException(
                        status_code=409,
                        detail="SECURITY VIOLATION (EXEC-06): Trade approval race condition detected. Already consumed."
                    )

                return {
                    "intent_id": intent_id,
                    "status": "APPROVED",
                    "is_consumed": True,
                    "consumed_at": now.isoformat()
                }

execution_intent_store = ExecutionIntentStore()
