import os
import shutil
import tempfile
import pytest
import sqlite3
from app.core.config import settings

# Capture true pre-test settings BEFORE any overrides are applied
ORIGINAL_APP_ENV = getattr(settings, "APP_ENV", "development")
ORIGINAL_SQLITE_DB_PATH = getattr(settings, "SQLITE_DB_PATH", "./data/memecoin_os.db")
ORIGINAL_RESEARCH_DB_PATH = os.environ.get("RESEARCH_HISTORY_DB_PATH")

# Create a unique session directory in temp
SESSION_TEST_DIR = tempfile.mkdtemp(prefix="test_session_memecoin_")
SESSION_TEST_DB = os.path.join(SESSION_TEST_DIR, "isolated_test.db")
SESSION_RESEARCH_DB = os.path.join(SESSION_TEST_DIR, "isolated_research_history.db")

# Enforce test environment and isolated test database paths at conftest import time
settings.APP_ENV = "test"
settings.SQLITE_DB_PATH = SESSION_TEST_DB
os.environ["RESEARCH_HISTORY_DB_PATH"] = SESSION_RESEARCH_DB

@pytest.fixture(autouse=True, scope="session")
def configure_test_environment():
    # 1. Initialize schema migrations on isolated test database
    from app.db.sqlite_migrator import SqliteMigrator
    migrator = SqliteMigrator(db_path=SESSION_TEST_DB)
    migrator.apply_migrations()

    # 2. Test-fixture schema compatibility for isolated dashboard test
    # Adds performance attribution columns to isolated paper_accounts if absent
    with sqlite3.connect(SESSION_TEST_DB) as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(paper_accounts);").fetchall()]
        for col, col_type in [
            ("winning_trades", "INTEGER DEFAULT 0"),
            ("losing_trades", "INTEGER DEFAULT 0"),
            ("gross_profits", "REAL DEFAULT 0.0"),
            ("gross_losses", "REAL DEFAULT 0.0")
        ]:
            if col not in cols:
                conn.execute(f"ALTER TABLE paper_accounts ADD COLUMN {col} {col_type};")
        conn.commit()

    yield

    # Restore true pre-test settings captured prior to test overrides
    settings.APP_ENV = ORIGINAL_APP_ENV
    settings.SQLITE_DB_PATH = ORIGINAL_SQLITE_DB_PATH
    if ORIGINAL_RESEARCH_DB_PATH is not None:
        os.environ["RESEARCH_HISTORY_DB_PATH"] = ORIGINAL_RESEARCH_DB_PATH
    else:
        os.environ.pop("RESEARCH_HISTORY_DB_PATH", None)

    # Clean up only the unique temporary session directory
    shutil.rmtree(SESSION_TEST_DIR, ignore_errors=True)
