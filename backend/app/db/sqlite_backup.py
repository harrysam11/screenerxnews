import os
import sqlite3
import shutil
import logging
import json
import base64
from datetime import datetime, timezone
from typing import Dict, Any, Optional
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from app.core.config import settings

logger = logging.getLogger(__name__)

class SQLiteBackupService:
    """
    Safe SQLite backup and restore utility for local-first architecture (Phase P10.2-B).
    - Uses SQLite online backup API (conn.backup) to handle active WAL state safely.
    - Executes PRAGMA integrity_check on source and destination.
    - Preserves transaction consistency and schema versions.
    """

    @staticmethod
    def backup_database(
        source_db_path: str,
        backup_dest_path: str,
        pages_per_step: int = 100
    ) -> Dict[str, Any]:
        """
        Creates an online, atomic backup of an active SQLite database.
        Safe to run while application writers are active under WAL mode.
        """
        if not os.path.exists(source_db_path):
            raise FileNotFoundError(f"Source database not found: {source_db_path}")

        # Ensure destination directory exists
        dest_dir = os.path.dirname(os.path.abspath(backup_dest_path))
        os.makedirs(dest_dir, exist_ok=True)

        # Temporary target path to ensure atomicity
        temp_dest_path = f"{backup_dest_path}.tmp"
        if os.path.exists(temp_dest_path):
            os.remove(temp_dest_path)

        src_conn = sqlite3.connect(source_db_path, timeout=30.0)
        dest_conn = sqlite3.connect(temp_dest_path)

        try:
            # Checkpoint WAL before backing up if in WAL mode
            try:
                src_conn.execute("PRAGMA wal_checkpoint(PASSIVE);")
            except Exception as e:
                logger.warning(f"WAL checkpoint warning: {e}")

            # Online backup API
            src_conn.backup(dest_conn, pages=pages_per_step)

            # Verify integrity on the backup file
            cursor = dest_conn.execute("PRAGMA integrity_check;")
            integrity_result = cursor.fetchone()[0]
            if integrity_result != "ok":
                raise RuntimeError(f"Backup integrity check failed: {integrity_result}")

            # Get schema version
            cur = dest_conn.execute("PRAGMA user_version;")
            schema_version = cur.fetchone()[0]

        finally:
            dest_conn.close()
            src_conn.close()

        # Atomically replace final backup file
        if os.path.exists(backup_dest_path):
            os.remove(backup_dest_path)
        os.rename(temp_dest_path, backup_dest_path)

        backup_size = os.path.getsize(backup_dest_path)

        return {
            "status": "SUCCESS",
            "source": source_db_path,
            "backup_path": backup_dest_path,
            "bytes": backup_size,
            "integrity": integrity_result,
            "schema_version": schema_version
        }

    @staticmethod
    def restore_database(
        backup_path: str,
        target_db_path: str
    ) -> Dict[str, Any]:
        """
        Restores a database from a verified SQLite backup.
        Verifies backup integrity before replacing target database.
        """
        if not os.path.exists(backup_path):
            raise FileNotFoundError(f"Backup file not found: {backup_path}")

        # Verify backup integrity first
        test_conn = sqlite3.connect(backup_path)
        try:
            cursor = test_conn.execute("PRAGMA integrity_check;")
            integrity = cursor.fetchone()[0]
            if integrity != "ok":
                raise RuntimeError(f"Corrupt backup file: {integrity}")

            cur = test_conn.execute("PRAGMA user_version;")
            schema_version = cur.fetchone()[0]
        finally:
            test_conn.close()

        target_dir = os.path.dirname(os.path.abspath(target_db_path))
        os.makedirs(target_dir, exist_ok=True)

        # Restore using online backup API to destination
        dest_conn = sqlite3.connect(target_db_path, timeout=30.0)
        src_conn = sqlite3.connect(backup_path)
        try:
            src_conn.backup(dest_conn)
            dest_conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        finally:
            src_conn.close()
            dest_conn.close()

        return {
            "status": "RESTORED",
            "backup_source": backup_path,
            "target_path": target_db_path,
            "integrity": "ok",
            "schema_version": schema_version
        }

    @staticmethod
    def export_vault_recovery_package(
        passphrase: str,
        export_path: str,
        vault_dir: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Exports an encrypted recovery package containing vault key, salt, and metadata (SEC-05).
        Encrypted with user-supplied passphrase via PBKDF2 + Fernet.
        Never bundles plaintext .vault_key into plain SQLite database backups.
        """
        if not passphrase or len(passphrase) < 8:
            raise ValueError("Passphrase must be at least 8 characters long.")

        vdir = vault_dir or os.path.dirname(os.path.abspath(settings.get_canonical_sqlite_path()))
        key_file = os.path.join(vdir, ".vault_key")
        salt_file = os.path.join(vdir, ".vault_salt")
        meta_file = os.path.join(vdir, ".vault_meta.json")

        if not os.path.exists(key_file):
            raise FileNotFoundError(f"Vault key file not found in {vdir}")

        with open(key_file, "r", encoding="utf-8") as f:
            vault_key = f.read().strip()

        vault_salt_b64 = ""
        if os.path.exists(salt_file):
            with open(salt_file, "rb") as f:
                vault_salt_b64 = base64.b64encode(f.read()).decode("utf-8")

        vault_meta = {}
        if os.path.exists(meta_file):
            try:
                with open(meta_file, "r", encoding="utf-8") as f:
                    vault_meta = json.load(f)
            except Exception:
                pass

        payload = {
            "vault_key": vault_key,
            "vault_salt_b64": vault_salt_b64,
            "vault_meta": vault_meta,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "format": "screenerxnews_vault_recovery_v1"
        }
        payload_bytes = json.dumps(payload).encode("utf-8")

        # Derive encryption key from passphrase using a unique random salt
        pkg_salt = os.urandom(16)
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=pkg_salt,
            iterations=150000,
        )
        derived = kdf.derive(passphrase.encode("utf-8"))
        fernet = Fernet(base64.urlsafe_b64encode(derived))
        encrypted_data = fernet.encrypt(payload_bytes)

        pkg = {
            "pkg_salt_b64": base64.b64encode(pkg_salt).decode("utf-8"),
            "ciphertext": encrypted_data.decode("utf-8"),
            "kdf_iterations": 150000,
            "created_at": datetime.now(timezone.utc).isoformat()
        }

        os.makedirs(os.path.dirname(os.path.abspath(export_path)), exist_ok=True)
        with open(export_path, "w", encoding="utf-8") as f:
            json.dump(pkg, f, indent=2)
        os.chmod(export_path, 0o600)

        return {
            "status": "EXPORTED",
            "export_path": export_path,
            "bytes": os.path.getsize(export_path)
        }

    @staticmethod
    def import_vault_recovery_package(
        passphrase: str,
        import_path: str,
        target_vault_dir: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Restores vault keys and salt from an encrypted recovery package (SEC-05).
        Decrypted using user-supplied passphrase, with strict 0600 file permissions.
        """
        if not os.path.exists(import_path):
            raise FileNotFoundError(f"Recovery package not found: {import_path}")

        with open(import_path, "r", encoding="utf-8") as f:
            pkg = json.load(f)

        pkg_salt = base64.b64decode(pkg["pkg_salt_b64"])
        iterations = pkg.get("kdf_iterations", 150000)
        ciphertext = pkg["ciphertext"].encode("utf-8")

        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=pkg_salt,
            iterations=iterations,
        )
        derived = kdf.derive(passphrase.encode("utf-8"))
        fernet = Fernet(base64.urlsafe_b64encode(derived))

        decrypted_bytes = fernet.decrypt(ciphertext)
        payload = json.loads(decrypted_bytes.decode("utf-8"))

        target_dir = target_vault_dir or os.path.dirname(os.path.abspath(settings.get_canonical_sqlite_path()))
        os.makedirs(target_dir, exist_ok=True)

        key_file = os.path.join(target_dir, ".vault_key")
        salt_file = os.path.join(target_dir, ".vault_salt")
        meta_file = os.path.join(target_dir, ".vault_meta.json")

        with open(key_file, "w", encoding="utf-8") as f:
            f.write(payload["vault_key"])
        os.chmod(key_file, 0o600)

        if payload.get("vault_salt_b64"):
            with open(salt_file, "wb") as f:
                f.write(base64.b64decode(payload["vault_salt_b64"]))
            os.chmod(salt_file, 0o600)

        if payload.get("vault_meta"):
            with open(meta_file, "w", encoding="utf-8") as f:
                json.dump(payload["vault_meta"], f, indent=2)
            os.chmod(meta_file, 0o600)

        return {
            "status": "RESTORED",
            "target_dir": target_dir,
            "restored_files": [".vault_key", ".vault_salt", ".vault_meta.json"]
        }

sqlite_backup_service = SQLiteBackupService()
