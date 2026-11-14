import os
import sqlite3
import base64
import logging
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from app.core.config import settings

logger = logging.getLogger("memecoin_os.credential_vault")

class CredentialVault:
    """
    Zero-exposure credential vault.
    Encrypts API keys, private tokens, and provider credentials using AES-256-GCM.
    Credentials never appear in logs, console output, or exported configurations (API-03).
    """

    def __init__(self, db_path: Optional[str] = None, secret_key: Optional[str] = None):
        if db_path is None:
            self.db_path = settings.SQLITE_DB_PATH
        else:
            self.db_path = db_path

        if self.db_path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)

        # Derive 256-bit encryption key from secret_key using PBKDF2
        raw_secret = (secret_key or os.getenv("SECRET_KEY", "local_vault_master_key_default_32bytes")).encode("utf-8")
        salt = b"screenerxnews_vault_salt_v1"
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100_000
        )
        self._key = kdf.derive(raw_secret)
        self._aesgcm = AESGCM(self._key)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        if self.db_path == ":memory:":
            conn = sqlite3.connect("file:vault_mem?mode=memory&cache=shared", uri=True, timeout=30.0, isolation_level=None)
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
                CREATE TABLE IF NOT EXISTS encrypted_credentials (
                    credential_id TEXT PRIMARY KEY,
                    provider_name TEXT NOT NULL,
                    category TEXT NOT NULL,
                    encrypted_data TEXT NOT NULL,
                    nonce TEXT NOT NULL,
                    masked_hint TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
            """)

    @staticmethod
    def mask_secret(secret: str) -> str:
        """
        Generates a non-reversible visual mask for UI display (e.g., sk_live_...94b2).
        """
        if not secret:
            return ""
        if len(secret) <= 8:
            return "***"
        prefix = secret[:4]
        suffix = secret[-4:]
        return f"{prefix}...{suffix}"

    def store_credential(self, credential_id: str, provider_name: str, category: str, secret_value: str) -> Dict[str, Any]:
        """
        Encrypts and stores a credential value. Returns metadata with secret strictly masked.
        """
        nonce = os.urandom(12)
        ciphertext = self._aesgcm.encrypt(nonce, secret_value.encode("utf-8"), None)
        encrypted_b64 = base64.b64encode(ciphertext).decode("utf-8")
        nonce_b64 = base64.b64encode(nonce).decode("utf-8")
        masked_hint = self.mask_secret(secret_value)
        now_str = datetime.now(timezone.utc).isoformat()

        with self._get_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO encrypted_credentials 
                (credential_id, provider_name, category, encrypted_data, nonce, masked_hint, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?);
            """, (credential_id, provider_name, category, encrypted_b64, nonce_b64, masked_hint, now_str, now_str))

        return {
            "credential_id": credential_id,
            "provider_name": provider_name,
            "category": category,
            "masked_hint": masked_hint,
            "updated_at": now_str
        }

    def get_credential(self, credential_id: str) -> Optional[str]:
        """
        Retrieves and decrypts the credential for internal service use only.
        NEVER expose the output of this function to API responses or logs.
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT encrypted_data, nonce FROM encrypted_credentials WHERE credential_id = ?;",
                (credential_id,)
            )
            row = cursor.fetchone()
            if not row:
                return None

            ciphertext = base64.b64decode(row["encrypted_data"])
            nonce = base64.b64decode(row["nonce"])
            plaintext_bytes = self._aesgcm.decrypt(nonce, ciphertext, None)
            return plaintext_bytes.decode("utf-8")

    def delete_credential(self, credential_id: str) -> bool:
        with self._get_connection() as conn:
            cursor = conn.execute("DELETE FROM encrypted_credentials WHERE credential_id = ?;", (credential_id,))
            return cursor.rowcount > 0

    def list_credentials(self) -> List[Dict[str, Any]]:
        """
        Returns all registered credentials with values strictly masked.
        """
        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT credential_id, provider_name, category, masked_hint, created_at, updated_at
                FROM encrypted_credentials ORDER BY provider_name ASC;
            """)
            return [dict(row) for row in cursor.fetchall()]

    @classmethod
    def sanitize_log_message(cls, message: str, known_secrets: Optional[List[str]] = None) -> str:
        """
        Replaces sensitive credential substrings in text before logging.
        """
        sanitized = message
        if known_secrets:
            for secret in known_secrets:
                if secret and secret in sanitized:
                    sanitized = sanitized.replace(secret, "[REDACTED_CREDENTIAL]")
        return sanitized

credential_vault = CredentialVault()
