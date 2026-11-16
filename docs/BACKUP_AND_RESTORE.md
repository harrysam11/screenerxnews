# Backup, Restore & Vault Recovery Guide (P10.2-C.2)

---

## 1. Online Database Backup Mechanism

The application utilizes Python's built-in `sqlite3.Connection.backup()` API in `backend/app/db/sqlite_backup.py`.

### Guarantees:
- Safe to execute while writers are actively posting orders or ledger journals under WAL mode.
- Checkpoints WAL pages to the main database file before copying.
- Validates the resulting backup using `PRAGMA integrity_check`.

```python
from app.db.sqlite_backup import sqlite_backup_service

# Create online database backup
result = sqlite_backup_service.backup_database(
    source_db_path="data/memecoin_os.db",
    backup_dest_path="data/backups/memecoin_os_backup.db"
)
# {'status': 'SUCCESS', 'integrity': 'ok', ...}

# Restore database from backup
restore_res = sqlite_backup_service.restore_database(
    backup_path="data/backups/memecoin_os_backup.db",
    target_db_path="data/memecoin_os.db"
)
# {'status': 'RESTORED', 'integrity': 'ok', ...}
```

---

## 2. SEC-05: Vault Key Isolation & Encrypted Recovery Packages

> [!CAUTION]
> The database backup procedure explicitly **never** bundles `.vault_key` or `.vault_salt` into the database file. Backing up the database file alone does not leak encryption keys.

To safely back up and transfer the master encryption credentials, operators use encrypted vault recovery packages (`.vaultpkg`):

### Exporting an Encrypted Recovery Package

```python
from app.db.sqlite_backup import sqlite_backup_service

# Exports .vault_key and .vault_salt encrypted with user passphrase
pkg_result = sqlite_backup_service.export_vault_recovery_package(
    passphrase="MySuperSecretRecoveryPassphrase!2026",
    export_path="data/backups/vault_backup_20261114.vaultpkg",
    vault_dir="data"
)
# {'status': 'EXPORTED', 'export_path': '...', 'permissions': '0600', ...}
```

The recovery package:
- Is encrypted using AES-Fernet with a key derived from the user's passphrase via PBKDF2HMAC (SHA-256, 100,000 iterations).
- Is saved with restricted POSIX `0600` permissions.
- Contains the installation's `.vault_key`, `.vault_salt`, and `.vault_meta.json`.

### Importing / Restoring an Encrypted Recovery Package

```python
# Restores keys and salt to a new installation directory
restore_result = sqlite_backup_service.import_vault_recovery_package(
    passphrase="MySuperSecretRecoveryPassphrase!2026",
    import_path="data/backups/vault_backup_20261114.vaultpkg",
    target_vault_dir="data"
)
# {'status': 'RESTORED', 'restored_files': ['.vault_key', '.vault_salt', '.vault_meta.json'], ...}
```

If an incorrect passphrase is provided, the import fails with a cryptographic decryption error and no files are written.
