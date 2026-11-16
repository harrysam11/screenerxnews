import os
import re
import json
import base64
import hashlib
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from app.core.config import settings

class CredentialVaultService:
    """
    Local Encrypted Credential Vault (Phase P10.2-C & P10.2-C.2).
    Provides user-owned, zero-plaintext storage of external provider secrets,
    API keys, and private RPC tokens using authenticated Fernet symmetric encryption.
    SEC-04: Unique persistent cryptographic salt per installation with 0600 permissions
    and graceful fallback migration for legacy credentials.
    """

    def __init__(self, key: Optional[str] = None, salt: Optional[bytes] = None, db_path: Optional[str] = None):
        self.db_path = db_path or settings.get_canonical_sqlite_path()
        self._raw_key = key or self._resolve_or_create_master_key()
        self._salt = salt or self._resolve_or_create_salt()
        self._fernet = self._derive_fernet(self._raw_key, self._salt)
        self._legacy_salt = settings.CREDENTIAL_VAULT_SALT.encode("utf-8")
        self._legacy_fernet = self._derive_fernet(self._raw_key, self._legacy_salt)

    def _resolve_or_create_master_key(self) -> str:
        # 1. Environment setting
        if settings.CREDENTIAL_VAULT_KEY:
            return settings.CREDENTIAL_VAULT_KEY

        # 2. Local persistent key file
        if self.db_path != ":memory:":
            db_dir = os.path.dirname(os.path.abspath(self.db_path))
            key_file = os.path.join(db_dir, ".vault_key")
            if os.path.exists(key_file):
                try:
                    with open(key_file, "r", encoding="utf-8") as f:
                        saved_key = f.read().strip()
                        if saved_key:
                            return saved_key
                except Exception:
                    pass
            # Generate and persist key file with restricted permissions
            new_key = base64.urlsafe_b64encode(os.urandom(32)).decode("utf-8")
            try:
                os.makedirs(db_dir, exist_ok=True)
                with open(key_file, "w", encoding="utf-8") as f:
                    f.write(new_key)
                os.chmod(key_file, 0o600)
            except Exception:
                pass
            return new_key

        # 3. Ephemeral default for in-memory mode
        return "screenerxnews_inmemory_vault_master_key_32b_fixed"

    def _resolve_or_create_salt(self) -> bytes:
        if self.db_path != ":memory:":
            db_dir = os.path.dirname(os.path.abspath(self.db_path))
            salt_file = os.path.join(db_dir, ".vault_salt")
            meta_file = os.path.join(db_dir, ".vault_meta.json")

            if os.path.exists(salt_file):
                try:
                    with open(salt_file, "rb") as f:
                        saved_salt = f.read()
                        if len(saved_salt) >= 16:
                            return saved_salt
                except Exception:
                    pass

            # Generate >= 16-byte random salt per installation
            new_salt = os.urandom(32)
            try:
                os.makedirs(db_dir, exist_ok=True)
                with open(salt_file, "wb") as f:
                    f.write(new_salt)
                os.chmod(salt_file, 0o600)

                meta = {
                    "kdf": "PBKDF2HMAC",
                    "hash": "SHA256",
                    "iterations": 100000,
                    "salt_length": len(new_salt),
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "version": 2
                }
                with open(meta_file, "w", encoding="utf-8") as f:
                    json.dump(meta, f, indent=2)
                os.chmod(meta_file, 0o600)
            except Exception:
                pass
            return new_salt

        return settings.CREDENTIAL_VAULT_SALT.encode("utf-8")

    def _derive_fernet(self, master_key: str, salt: bytes) -> Fernet:
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100000,
        )
        derived = kdf.derive(master_key.encode("utf-8"))
        fernet_key = base64.urlsafe_b64encode(derived)
        return Fernet(fernet_key)

    @staticmethod
    def redact_secret(secret: str) -> str:
        """
        Redacts secret text for safe display and logging.
        Retains first 4 and last 4 characters if long enough; otherwise masks fully.
        """
        if not secret:
            return ""
        s = str(secret).strip()
        if len(s) <= 8:
            return "******"
        return f"{s[:4]}...{s[-4:]}"

    @staticmethod
    def sanitize_url(url: str) -> str:
        """
        Strips API keys and tokens from RPC URLs, query parameters, and URL paths.
        Prevents credential leakage in log files, database queries, and audit trails.
        """
        if not url:
            return ""
        cleaned = str(url)
        # Query parameter masking: ?key=..., &api_key=..., &token=...
        cleaned = re.sub(
            r"([?&](?:api[_-]?key|key|token|secret|auth)=)([^&]+)",
            r"\1[REDACTED]",
            cleaned,
            flags=re.IGNORECASE
        )
        # Path token masking (e.g. Alchemy, Infura, QuickNode /v2/<token>, /v3/<token>)
        cleaned = re.sub(
            r"(/(?:v1|v2|v3|rpc)/)([a-zA-Z0-9_\-]{16,})",
            r"\1[REDACTED]",
            cleaned,
            flags=re.IGNORECASE
        )
        return cleaned

    @classmethod
    def redact_dict(cls, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Recursively sanitizes dictionary payloads to eliminate sensitive credential fields.
        """
        sensitive_patterns = {"key", "secret", "token", "password", "private", "credential", "auth"}
        sanitized = {}
        for k, v in data.items():
            k_lower = str(k).lower()
            if any(p in k_lower for p in sensitive_patterns) and isinstance(v, str):
                sanitized[k] = cls.redact_secret(v)
            elif isinstance(v, dict):
                sanitized[k] = cls.redact_dict(v)
            elif isinstance(v, list):
                sanitized[k] = [
                    cls.redact_dict(item) if isinstance(item, dict) else item
                    for item in v
                ]
            else:
                sanitized[k] = v
        return sanitized

    def encrypt(self, plaintext: str) -> str:
        if not plaintext:
            raise ValueError("Cannot encrypt empty secret")
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8")

    def decrypt(self, ciphertext: str) -> str:
        if not ciphertext:
            raise ValueError("Cannot decrypt empty ciphertext")
        try:
            return self._fernet.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
        except Exception as e:
            # Fallback migration: try decrypting using legacy salt
            try:
                return self._legacy_fernet.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
            except Exception:
                raise e

    def generate_fingerprint(self, secret: str) -> str:
        return hashlib.sha256(secret.encode("utf-8")).hexdigest()[:16]

    def _get_connection(self) -> sqlite3.Connection:
        if self.db_path == ":memory:":
            conn = sqlite3.connect("file:memecoin_vault_mem?mode=memory&cache=shared", uri=True, timeout=30.0, isolation_level=None)
        else:
            conn = sqlite3.connect(self.db_path, timeout=30.0, check_same_thread=False, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        if self.db_path != ":memory:":
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute("PRAGMA busy_timeout = 30000;")
        return conn

    def store_credential(
        self,
        provider_id: str,
        secret: str,
        credential_type: str = "api_key"
    ) -> Dict[str, Any]:
        """
        Encrypts and stores a credential in the SQLite database.
        Returns safe metadata preview without exposing the raw secret.
        """
        if not secret or not secret.strip():
            raise ValueError("Secret cannot be empty")

        encrypted = self.encrypt(secret.strip())
        fingerprint = self.generate_fingerprint(secret.strip())
        preview = self.redact_secret(secret.strip())
        cred_id = f"cred-{uuid.uuid4().hex[:12]}"
        now_str = datetime.now(timezone.utc).isoformat()

        with self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            try:
                # Mark existing active credentials for this provider and type as revoked
                conn.execute(
                    """
                    UPDATE provider_credentials
                    SET is_revoked = 1, updated_at = ?
                    WHERE provider_id = ? AND credential_type = ? AND is_revoked = 0;
                    """,
                    (now_str, provider_id, credential_type)
                )

                conn.execute(
                    """
                    INSERT INTO provider_credentials (
                        id, provider_id, credential_type, encrypted_secret,
                        key_fingerprint, redacted_preview, is_revoked, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?);
                    """,
                    (cred_id, provider_id, credential_type, encrypted, fingerprint, preview, now_str, now_str)
                )

                # Record credential update audit log
                audit_id = f"audit-{uuid.uuid4().hex[:12]}"
                conn.execute(
                    """
                    INSERT INTO provider_audit_log (id, event_type, provider_id, details_json, created_at)
                    VALUES (?, 'CREDENTIAL_UPDATED', ?, ?, ?);
                    """,
                    (audit_id, provider_id, f'{{"credential_id":"{cred_id}","fingerprint":"{fingerprint}"}}', now_str)
                )

                conn.execute("COMMIT;")
            except Exception as e:
                conn.execute("ROLLBACK;")
                raise e

        return {
            "id": cred_id,
            "provider_id": provider_id,
            "credential_type": credential_type,
            "key_fingerprint": fingerprint,
            "redacted_preview": preview,
            "is_revoked": 0,
            "created_at": now_str
        }

    def get_credential_secret(self, provider_id: str, credential_type: str = "api_key") -> Optional[str]:
        """
        Retrieves and decrypts the active credential secret for authorized internal execution.
        Returns None if no active credential exists.
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT encrypted_secret FROM provider_credentials
                WHERE provider_id = ? AND credential_type = ? AND is_revoked = 0
                ORDER BY created_at DESC LIMIT 1;
                """,
                (provider_id, credential_type)
            )
            row = cursor.fetchone()
            if not row:
                return None
            try:
                return self.decrypt(row["encrypted_secret"])
            except Exception:
                return None

    def revoke_credential(self, credential_id: str) -> bool:
        """
        Revokes a credential, preventing future decryption or outbound requests.
        """
        now_str = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            try:
                cursor = conn.execute(
                    """
                    UPDATE provider_credentials
                    SET is_revoked = 1, updated_at = ?
                    WHERE id = ? AND is_revoked = 0;
                    """,
                    (now_str, credential_id)
                )
                if cursor.rowcount > 0:
                    audit_id = f"audit-{uuid.uuid4().hex[:12]}"
                    conn.execute(
                        """
                        INSERT INTO provider_audit_log (id, event_type, details_json, created_at)
                        VALUES (?, 'CREDENTIAL_REVOKED', ?, ?);
                        """,
                        (audit_id, f'{{"credential_id":"{credential_id}"}}', now_str)
                    )
                    conn.execute("COMMIT;")
                    return True
                conn.execute("COMMIT;")
                return False
            except Exception as e:
                conn.execute("ROLLBACK;")
                raise e

    def list_credentials(self, provider_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Lists stored credential metadata. Guarantees zero secret exposure.
        """
        with self._get_connection() as conn:
            if provider_id:
                cursor = conn.execute(
                    """
                    SELECT id, provider_id, credential_type, key_fingerprint,
                           redacted_preview, is_revoked, created_at, updated_at
                    FROM provider_credentials
                    WHERE provider_id = ?
                    ORDER BY created_at DESC;
                    """,
                    (provider_id,)
                )
            else:
                cursor = conn.execute(
                    """
                    SELECT id, provider_id, credential_type, key_fingerprint,
                           redacted_preview, is_revoked, created_at, updated_at
                    FROM provider_credentials
                    ORDER BY created_at DESC;
                    """
                )
            return [dict(row) for row in cursor.fetchall()]

credential_vault = CredentialVaultService()
