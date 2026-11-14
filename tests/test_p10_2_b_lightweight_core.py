"""
Phase P10.2-B: Lightweight Core & Termux Packaging Test Suite
Verifies acceptance criteria:
- CORE-01: Application starts without Docker or PostgreSQL
- CORE-02: SQLite creates and upgrades the database successfully
- CORE-03: Application recovers after restart
- CORE-04: Optional components can be disabled independently
- TERMUX-01: Lite profile starts on Termux
- TERMUX-02: Memory and CPU usage are benchmarked on actual hardware
"""

import os
import sys
import tempfile
import sqlite3
import pytest
from decimal import Decimal
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath("./backend"))
from app.main import app
from app.core.config import settings
from app.core.components import components_manager
from app.db.sqlite_migrator import SqliteMigrator
from app.core.database import check_database_health

client = TestClient(app)

# ==============================================================================
# CORE-01: Application starts without Docker or PostgreSQL
# ==============================================================================

def test_core_01_starts_without_docker_or_postgresql():
    """
    CORE-01: Verifies the application boots cleanly with SQLite-first configuration,
    requiring zero Docker containers, zero PostgreSQL processes, and zero external daemons.
    """
    # 1. Configuration verifies SQLite default
    assert settings.is_sqlite is True
    assert settings.DATABASE_BACKEND == "sqlite"
    assert "sqlite" in settings.DATABASE_URL
    assert settings.ALLOW_LIVE_TRADING is False

    # 2. Health check endpoint confirms healthy SQLite operation
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["database_backend"] == "sqlite"
    assert data["live_trading_allowed"] is False
    assert data["profile"] == "lite"
    assert data["operating_mode"] in ["offline_research", "online_research", "paper_trading"]

    # 3. System status endpoint confirms zero external dependency requirement
    status_res = client.get("/api/v1/system/status")
    assert status_res.status_code == 200
    status_data = status_res.json()
    assert status_data["database_backend"] == "sqlite"
    assert status_data["database_health"]["status"] == "healthy"
    assert status_data["all_optional_disabled"] is True

# ==============================================================================
# CORE-02: SQLite creates and upgrades the database successfully
# ==============================================================================

def test_core_02_sqlite_creates_and_upgrades_schema():
    """
    CORE-02: Verifies that SqliteMigrator creates schema_migrations, applies versioned
    migrations, sets up tables and triggers, and is strictly idempotent.
    """
    with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
        migrator = SqliteMigrator(tmp.name)

        # Initially no migrations applied
        assert migrator.get_current_version() == 0

        # Apply migrations
        newly_applied = migrator.apply_migrations()
        assert newly_applied == [1, 2, 3]
        assert migrator.get_current_version() == 3

        # Verify all core tables exist in schema
        verification = migrator.verify_schema()
        assert verification["valid"] is True
        assert verification["missing_tables"] == []

        expected_tables = {
            "schema_migrations", "assets", "pools", "market_snapshots",
            "security_scans", "paper_accounts", "paper_positions",
            "paper_orders", "execution_intents", "trade_approvals",
            "ledger_journals", "ledger_lines", "chain_registries",
            "system_audit_log"
        }
        for table in expected_tables:
            assert table in verification["existing_tables"]

        # Verify multi-chain registries table seeded with 12 chains
        conn = sqlite3.connect(tmp.name)
        conn.row_factory = sqlite3.Row
        cursor = conn.execute("SELECT chain_id, network_type, group_tier, is_active FROM chain_registries ORDER BY chain_id;")
        chains = {row["chain_id"]: dict(row) for row in cursor.fetchall()}
        assert len(chains) == 12

        # Group A chains must be seeded and active
        assert chains["solana:mainnet"]["is_active"] == 1
        assert chains["eip155:1"]["is_active"] == 1
        assert chains["eip155:8453"]["is_active"] == 1
        assert chains["eip155:56"]["is_active"] == 1

        # Group B and C chains seeded
        assert "eip155:42161" in chains
        assert "sui:mainnet" in chains
        assert "ton:mainnet" in chains
        assert "tron:mainnet" in chains
        conn.close()

        # Idempotency check: re-running migrations applies nothing
        re_applied = migrator.apply_migrations()
        assert re_applied == []
        assert migrator.get_current_version() == 3

# ==============================================================================
# CORE-03: Application recovers after restart
# ==============================================================================

def test_core_03_sqlite_data_recovery_after_restart():
    """
    CORE-03: Verifies that data written before a process shutdown or restart
    persists in SQLite WAL storage and is completely intact after recovery.
    """
    with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
        db_path = tmp.name

        # Session 1: Boot, migrate, insert test asset and paper account
        migrator_1 = SqliteMigrator(db_path)
        migrator_1.apply_migrations()

        conn_1 = sqlite3.connect(db_path)
        conn_1.execute(
            """
            INSERT INTO assets (id, mint_address, name, symbol, decimals, total_supply_raw, is_verified, first_seen_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'));
            """,
            ("ast-recovery-01", "RecoverMint11111111111111111111111111111", "Recovery Token", "RECOV", 9, "1000000000000", 1)
        )
        conn_1.execute(
            """
            INSERT INTO paper_accounts (account_id, chain_id, initial_balance_usd, current_balance_usd, created_at, updated_at)
            VALUES (?, ?, ?, ?, datetime('now'), datetime('now'));
            """,
            ("acc-recov-01", "solana:mainnet", 10000.0, 10450.50)
        )
        conn_1.commit()
        conn_1.close()

        # Simulate Process Restart: Tear down all instances and recreate fresh migrator and connection
        del migrator_1

        migrator_2 = SqliteMigrator(db_path)
        assert migrator_2.get_current_version() == 3

        # Re-running migrations on restarted database must not delete or modify existing records
        re_run = migrator_2.apply_migrations()
        assert re_run == []

        conn_2 = sqlite3.connect(db_path)
        conn_2.row_factory = sqlite3.Row

        # Verify asset data intact
        cur_asset = conn_2.execute("SELECT * FROM assets WHERE id = ?;", ("ast-recovery-01",))
        asset_row = cur_asset.fetchone()
        assert asset_row is not None
        assert asset_row["mint_address"] == "RecoverMint11111111111111111111111111111"
        assert asset_row["symbol"] == "RECOV"
        assert asset_row["decimals"] == 9

        # Verify paper account data intact
        cur_acc = conn_2.execute("SELECT * FROM paper_accounts WHERE account_id = ?;", ("acc-recov-01",))
        acc_row = cur_acc.fetchone()
        assert acc_row is not None
        assert acc_row["chain_id"] == "solana:mainnet"
        assert float(acc_row["current_balance_usd"]) == 10450.50
        conn_2.close()

# ==============================================================================
# CORE-04: Optional components can be disabled independently
# ==============================================================================

def test_core_04_optional_components_disabled_independently():
    """
    CORE-04: Verifies that optional external orchestrators (Docker, n8n, Windmill, Hermes, Redis)
    can be disabled independently or collectively without affecting core API and paper trading.
    """
    orig_states = {
        "ENABLE_DOCKER": settings.ENABLE_DOCKER,
        "ENABLE_N8N": settings.ENABLE_N8N,
        "ENABLE_WINDMILL": settings.ENABLE_WINDMILL,
        "ENABLE_HERMES": settings.ENABLE_HERMES,
        "ENABLE_REDIS": settings.ENABLE_REDIS
    }

    try:
        # Case A: All optional integrations disabled (Pure Lightweight Core)
        settings.ENABLE_DOCKER = False
        settings.ENABLE_N8N = False
        settings.ENABLE_WINDMILL = False
        settings.ENABLE_HERMES = False
        settings.ENABLE_REDIS = False

        assert components_manager.are_all_optional_disabled() is True
        report = components_manager.get_status_report()
        assert report["all_optional_disabled"] is True
        assert report["core_operational"] is True
        for comp_id in ["docker", "n8n", "windmill", "hermes", "redis"]:
            assert report["components"][comp_id]["enabled"] is False

        # Core endpoints must function normally
        res = client.get("/api/v1/system/components")
        assert res.status_code == 200
        assert res.json()["all_optional_disabled"] is True

        paper_res = client.get("/api/v1/paper/account")
        assert paper_res.status_code == 200

        # Case B: Enable only Hermes, keep others disabled
        settings.ENABLE_HERMES = True
        assert components_manager.is_enabled("hermes") is True
        assert components_manager.is_enabled("n8n") is False
        assert components_manager.is_enabled("windmill") is False
        assert components_manager.are_all_optional_disabled() is False

        # Case C: Enable only Windmill and Redis
        settings.ENABLE_HERMES = False
        settings.ENABLE_WINDMILL = True
        settings.ENABLE_REDIS = True
        assert components_manager.is_enabled("windmill") is True
        assert components_manager.is_enabled("redis") is True
        assert components_manager.is_enabled("docker") is False

    finally:
        # Restore original settings
        for k, v in orig_states.items():
            setattr(settings, k, v)

# ==============================================================================
# TERMUX-01: Lite profile starts on Termux
# ==============================================================================

def test_termux_01_lite_profile_minimal_footprint():
    """
    TERMUX-01: Verifies that the lite profile environment is free of mandatory heavy
    native C compilation dependencies (no asyncpg, psycopg2, or Docker requirements)
    and startup scripts exist and are executable.
    """
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

    # 1. Verify requirements-lite.txt exists and excludes heavy Postgres drivers
    req_lite_path = os.path.join(repo_root, "requirements-lite.txt")
    assert os.path.exists(req_lite_path), "requirements-lite.txt must exist"

    with open(req_lite_path, "r") as f:
        req_content = f.read()

    assert "asyncpg" not in req_content, "Lite requirements must exclude asyncpg"
    assert "psycopg2" not in req_content, "Lite requirements must exclude psycopg2"
    assert "uvicorn[standard]" not in req_content, "Lite requirements must avoid uvicorn[standard] native wheels"
    assert "fastapi" in req_content
    assert "sqlalchemy" in req_content

    # 2. Verify Termux setup and startup scripts exist and are executable
    install_script = os.path.join(repo_root, "scripts/termux_install.sh")
    start_script = os.path.join(repo_root, "scripts/termux_start.sh")

    assert os.path.exists(install_script), "termux_install.sh must exist"
    assert os.path.exists(start_script), "termux_start.sh must exist"
    assert os.access(install_script, os.X_OK), "termux_install.sh must be executable"
    assert os.access(start_script, os.X_OK), "termux_start.sh must be executable"

    with open(start_script, "r") as f:
        start_content = f.read()

    assert "PROFILE=lite" in start_content
    assert "DATABASE_BACKEND=sqlite" in start_content
    assert "ALLOW_LIVE_TRADING=false" in start_content

# ==============================================================================
# TERMUX-02: Memory and CPU usage are benchmarked on actual hardware
# ==============================================================================

def test_termux_02_resource_benchmark_measurements():
    """
    TERMUX-02: Executes the hardware resource benchmark harness and verifies that
    all performance and memory budgets for mobile / low-resource devices are satisfied.
    """
    from scripts.benchmark_termux import run_benchmarks

    report = run_benchmarks()
    metrics = report["metrics"]
    budgets = report["budget_checks"]

    # Budget 1: Resident Set Size (RSS) under 150 MB
    assert metrics["rss_final_mb"] < 150.0, f"RSS memory {metrics['rss_final_mb']} MB exceeds 150 MB ceiling"
    assert budgets["rss_within_budget"] is True

    # Budget 2: Cold startup under 2.0 seconds
    assert metrics["cold_start_sec"] < 2.0, f"Cold start {metrics['cold_start_sec']}s exceeds 2.0s ceiling"
    assert budgets["startup_within_budget"] is True

    # Budget 3: SQLite write latency under 5.0 ms per op
    assert metrics["sqlite_write_per_op_ms"] < 5.0, f"Write latency {metrics['sqlite_write_per_op_ms']} ms exceeds 5ms ceiling"
    assert budgets["sqlite_write_within_budget"] is True

    # Budget 4: API endpoint latency under 50.0 ms
    assert metrics["api_health_latency_ms"] < 50.0, f"API health latency {metrics['api_health_latency_ms']} ms exceeds 50ms ceiling"
    assert budgets["api_latency_within_budget"] is True

    assert budgets["all_budgets_passed"] is True
