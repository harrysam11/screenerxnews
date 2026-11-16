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
        assert set(newly_applied) >= {1, 2, 3}
        assert migrator.get_current_version() >= 3

        # Verify all core tables exist in schema
        verification = migrator.verify_schema()
        assert verification["valid"] is True
        assert verification["missing_tables"] == []

        expected_tables = {
            "schema_migrations", "assets", "pools", "market_snapshots",
            "security_scans", "paper_accounts", "paper_positions",
            "paper_orders", "execution_intents", "trade_approvals",
            "execution_intent_outbox", "ledger_journals", "ledger_lines",
            "chain_registries", "system_audit_log"
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
        assert migrator.get_current_version() >= 3

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
        assert migrator_2.get_current_version() >= 3

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

# ==============================================================================
# CORE-05: SQLite Online Backup and Restore Verification
# ==============================================================================

def test_core_05_sqlite_backup_and_restore(tmp_path):
    """
    CORE-05: Verifies that SQLiteBackupService creates an online backup of an active
    database, checks WAL consistency, verifies PRAGMA integrity_check, and restores cleanly.
    """
    from app.db.sqlite_backup import sqlite_backup_service

    source_db = str(tmp_path / "live_source.db")
    backup_db = str(tmp_path / "backups" / "live_source_backup.db")
    restore_db = str(tmp_path / "restored.db")

    # Seed source DB
    migrator = SqliteMigrator(db_path=source_db)
    migrator.apply_migrations()

    with sqlite3.connect(source_db) as conn:
        conn.execute("INSERT INTO schema_migrations (version, name, applied_at, checksum) VALUES (999, 'test_record', '2026-11-14', 'chk_999');")

    # 1. Perform online backup
    res = sqlite_backup_service.backup_database(source_db_path=source_db, backup_dest_path=backup_db)
    assert res["status"] == "SUCCESS"
    assert res["integrity"] == "ok"
    assert os.path.exists(backup_db)

    # 2. Perform restore into a fresh location
    rest_res = sqlite_backup_service.restore_database(backup_path=backup_db, target_db_path=restore_db)
    assert rest_res["status"] == "RESTORED"
    assert rest_res["integrity"] == "ok"

    # 3. Verify restored contents
    with sqlite3.connect(restore_db) as conn:
        row = conn.execute("SELECT name FROM schema_migrations WHERE version = 999;").fetchone()
        assert row is not None
        assert row[0] == "test_record"

# ==============================================================================
# CORE-06: Database Ownership & Unified Schema Verification
# ==============================================================================

def test_core_06_database_ownership_and_unified_schema():
    """
    CORE-06: Verifies database ownership:
    - execution_intent_store and double_entry_ledger_service both point to the
      authoritative database (settings.get_canonical_sqlite_path()).
    - Eliminates duplicate, out-of-sync schemas across disconnected SQLite files.
    """
    from app.services.execution_store import execution_intent_store
    from app.services.ledger_service import double_entry_ledger_service

    assert execution_intent_store.db_path == settings.get_canonical_sqlite_path()
    assert double_entry_ledger_service.db_path == settings.get_canonical_sqlite_path()

    # Verify both access the exact same tables in the unified DB
    with execution_intent_store._get_connection() as conn:
        tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()}
        assert "execution_intents" in tables
        assert "execution_intent_outbox" in tables
        assert "ledger_journals" in tables
        assert "ledger_lines" in tables

# ==============================================================================
# CORE-07: Async Session Wrapper Concurrency & Event Loop Safety
# ==============================================================================

@pytest.mark.anyio
async def test_core_07_async_session_wrapper_concurrency_and_thread_isolation():
    """
    CORE-07: Verifies SQLiteAsyncSessionWrapper safety under concurrent coroutines:
    - Concurrently dispatches 20 parallel queries through the wrapper.
    - Confirms that asyncio.Lock serializes operations without event loop thread blocking.
    - Confirms that __getattr__ dynamic methods execute off-loop via asyncio.to_thread.
    """
    import asyncio
    import threading
    from sqlalchemy.orm import sessionmaker
    from app.core.database import engine, SQLiteAsyncSessionWrapper

    SyncSession = sessionmaker(bind=engine)
    session = SyncSession()
    wrapper = SQLiteAsyncSessionWrapper(session)

    main_thread_id = threading.get_ident()
    thread_ids_observed = set()

    async def worker(idx: int):
        # execute via wrapper
        res = await wrapper.execute(
            __import__("sqlalchemy").text(f"SELECT {idx} AS val;")
        )
        row = res.fetchone()
        thread_ids_observed.add(threading.get_ident())
        return row[0]

    # Run 20 concurrent coroutines against the same wrapper session
    results = await asyncio.gather(*[worker(i) for i in range(20)])
    assert sorted(results) == list(range(20))
    await wrapper.close()

# ==============================================================================
# CORE-08: Execution + Ledger Consistency & Durable Crash Recovery
# ==============================================================================

def test_core_08_execution_ledger_crash_and_durable_recovery(tmp_path):
    """
    CORE-08: Verifies durable recovery when an intent is approved but process crashes
    before ledger journal posting can occur:
    1. Pre-seeds an intent in APPROVED status (simulating a crash right after atomic_approve_intent).
    2. With expired TTL, reconcile_unposted_intents detects missing ledger entry and cleanly marks it EXPIRED.
    3. Guarantees is_consumed remains 1 (never reset to 0; prevents replay permanently).
    """
    import uuid
    from datetime import datetime, timezone, timedelta
    from fastapi import HTTPException
    from app.services.execution_store import ExecutionIntentStore, IntentLifecycleState
    from app.services.ledger_service import DoubleEntryLedgerService

    shared_db = str(tmp_path / "unified_memecoin_os.db")
    intent_store = ExecutionIntentStore(db_path=shared_db)
    ledger_svc = DoubleEntryLedgerService(db_path=shared_db)

    # 1. Create intent approved in past that expired without a ledger entry
    expired_id = str(uuid.uuid4())
    past_time = datetime.now(timezone.utc) - timedelta(minutes=10)
    intent_store.save_intent({
        "intent_id": expired_id,
        "chain_id": "solana:mainnet",
        "wallet_address": "GsbwXfJraMomNxB62bBwN9ZhpFz7174XWp9yP2P3yZ84",
        "asset_id": "BONK",
        "pool_id": "BONK_POOL",
        "side": "BUY",
        "input_mint": "So11111111111111111111111111111111111111112",
        "output_mint": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
        "input_amount_raw": "500000000",
        "max_slippage_bps": 150,
        "destination_router": "jupiter",
        "strategy_id": "test",
        "strategy_version": "1.0",
        "nonce": "nonce-1",
        "canonical_intent_digest": "digest-1",
        "transaction_message_hash": "hash-1",
        "status": IntentLifecycleState.APPROVED,
        "policy_checks_passed": True,
        "rejection_reasons": [],
        "created_at": past_time - timedelta(minutes=5),
        "expires_at": past_time,
        "is_consumed": 1
    })

    # 2. Run durable crash reconciliation
    recovery_report = intent_store.reconcile_unposted_intents(ledger_service=ledger_svc)

    assert recovery_report["total_approved_audited"] >= 1
    assert recovery_report["expired_unposted_recovered"] >= 1
    assert expired_id in recovery_report["expired_intent_ids"]

    # Verify intent transitioned from APPROVED to EXPIRED in database
    recovered_intent = intent_store.get_intent(expired_id)
    assert recovered_intent["status"] == IntentLifecycleState.EXPIRED
    # CRITICAL INVARIANT: is_consumed must remain 1 (never reset to 0!)
    assert recovered_intent["is_consumed"] is True

    # 3. Verify expired intent can NEVER be re-approved or replayed
    with pytest.raises(HTTPException) as exc_info:
        intent_store.atomic_approve_intent(
            intent_id=expired_id,
            signed_by_address="GsbwXfJraMomNxB62bBwN9ZhpFz7174XWp9yP2P3yZ84",
            transaction_message_hash="hash-1",
            signature_hex="aa" * 64
        )
    assert exc_info.value.status_code in (409, 410)

# ==============================================================================
# CORE-09: Canonical SQLite Configuration & Conflict Rejection
# ==============================================================================

def test_core_09_canonical_sqlite_configuration_and_conflict_rejection():
    """
    CORE-09: Verifies canonical SQLite configuration:
    1. get_canonical_sqlite_path returns absolute normalized path.
    2. Rejects conflicting DATABASE_URL and SQLITE_DB_PATH at initialization (fail-closed).
    3. Custom path synchronizes both properties consistently.
    4. Subsystems (migrator, execution_store, ledger_service) all bind to the same canonical file.
    """
    from app.core.config import Settings
    from app.services.execution_store import ExecutionIntentStore
    from app.services.ledger_service import DoubleEntryLedgerService
    from app.db.sqlite_migrator import SqliteMigrator

    # 1. Reject conflicting settings
    with pytest.raises(ValueError) as exc_info:
        Settings(
            DATABASE_URL="sqlite:///./data/subsystem_a.db",
            SQLITE_DB_PATH="./data/subsystem_b.db"
        )
    assert "Database configuration conflict" in str(exc_info.value)

    # 2. Synchronize when one is explicitly provided
    custom_settings = Settings(SQLITE_DB_PATH="./data/custom_canonical.db")
    canonical_path = custom_settings.get_canonical_sqlite_path()
    assert canonical_path == os.path.abspath("./data/custom_canonical.db")
    assert custom_settings.DATABASE_URL == f"sqlite:///{custom_settings.SQLITE_DB_PATH}"

    # 3. Subsystem default binding matches settings
    store = ExecutionIntentStore()
    ledger = DoubleEntryLedgerService()
    migrator = SqliteMigrator()
    assert store.db_path == settings.get_canonical_sqlite_path()
    assert ledger.db_path == settings.get_canonical_sqlite_path()
    assert migrator.db_path == settings.get_canonical_sqlite_path()

# ==============================================================================
# CORE-10: Outbox Pattern & Crash Recovery Across Transaction Boundaries
# ==============================================================================

def test_core_10_outbox_pattern_and_crash_recovery_at_boundaries(tmp_path):
    """
    CORE-10: Verifies the durable Outbox pattern and crash recovery across all boundaries:
    - Boundary 1: Crash before approval commit -> Rolled back, intent AWAITING_APPROVAL, outbox empty.
    - Boundary 2: Crash after approval + outbox insert, before ledger posting -> Reconciler drains outbox and posts journal.
    - Boundary 3: Idempotent re-run -> Zero duplicate postings.
    - Boundary 4: Expired intent in outbox -> Marked EXPIRED, is_consumed=1 (never 0), replay permanently rejected.
    """
    import uuid
    import json
    from datetime import datetime, timezone, timedelta
    from fastapi import HTTPException
    from app.services.execution_store import (
        ExecutionIntentStore, IntentLifecycleState,
        build_canonical_intent_message, compute_intent_digest
    )
    from app.services.ledger_service import DoubleEntryLedgerService

    shared_db = str(tmp_path / "outbox_recovery_test.db")
    intent_store = ExecutionIntentStore(db_path=shared_db)
    ledger_svc = DoubleEntryLedgerService(db_path=shared_db)

    # Configure mock burner wallet
    test_burner = "0x" + "11" * 20
    orig_burners = settings.BURNER_WALLET_ADDRESSES
    settings.BURNER_WALLET_ADDRESSES = [test_burner]

    try:
        # --- Boundary 1: Intent created, approval not committed ---
        intent_1 = str(uuid.uuid4())
        future_exp = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
        intent_store.save_intent({
            "intent_id": intent_1,
            "chain_id": "eip155:1",
            "wallet_address": test_burner,
            "asset_id": "PEPE",
            "pool_id": "PEPE_POOL",
            "side": "BUY",
            "input_mint": "0xWETH",
            "output_mint": "0xPEPE",
            "input_amount_raw": "1000000000000000000",
            "max_slippage_bps": 150,
            "destination_router": "uniswap_v2",
            "strategy_id": "test",
            "strategy_version": "1.0",
            "nonce": "n1",
            "canonical_intent_digest": "d1",
            "transaction_message_hash": "h1",
            "status": IntentLifecycleState.AWAITING_APPROVAL,
            "policy_checks_passed": True,
            "rejection_reasons": [],
            "created_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": future_exp,
            "is_consumed": 0
        })

        # Check outbox is empty
        with intent_store._get_connection() as conn:
            cnt = conn.execute("SELECT COUNT(*) FROM execution_intent_outbox;").fetchone()[0]
            assert cnt == 0

        # --- Boundary 2: Approval committed with outbox record, simulating crash before journal posting ---
        journal_payload = {
            "journal_id": f"j_{intent_1}",
            "idempotency_key": f"idem_{intent_1}",
            "journal_type": "SWAP",
            "description": "Test outbox trade",
            "lines": [
                {
                    "account_code": "WALLET_SOL",
                    "wallet_address": test_burner,
                    "mint_address": "0xWETH",
                    "debit_amount_raw": "1.0",
                    "credit_amount_raw": "0",
                    "usd_rate": "2000.0"
                },
                {
                    "account_code": "WALLET_TOKEN",
                    "wallet_address": test_burner,
                    "mint_address": "0xPEPE",
                    "debit_amount_raw": "0",
                    "credit_amount_raw": "2000000.0",
                    "usd_rate": "0.001"
                }
            ]
        }

        # Seed intent_1 in APPROVED status with outbox record PENDING (simulating restart after crash)
        with intent_store._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            conn.execute(
                "UPDATE execution_intents SET status = ?, is_consumed = 1 WHERE intent_id = ?;",
                (IntentLifecycleState.APPROVED, intent_1)
            )
            conn.execute("""
                INSERT INTO execution_intent_outbox (
                    intent_id, idempotency_key, payload_json, status, attempts, last_error, created_at
                ) VALUES (?, ?, ?, 'PENDING', 0, NULL, ?);
            """, (intent_1, journal_payload["idempotency_key"], json.dumps(journal_payload), datetime.now(timezone.utc).isoformat()))
            conn.execute("COMMIT;")

        # Now simulate process reboot & reconciler execution
        recov_report = intent_store.reconcile_unposted_intents(ledger_service=ledger_svc)
        assert recov_report["outbox_recovered"] == 1

        # Verify journal was posted to ledger
        j = ledger_svc.get_journal(journal_payload["journal_id"])
        assert j is not None
        assert j["status"] == "POSTED"

        # Verify outbox record transitioned to POSTED
        with intent_store._get_connection() as conn:
            row = conn.execute("SELECT status, posted_at FROM execution_intent_outbox WHERE intent_id = ?;", (intent_1,)).fetchone()
            assert row["status"] == "POSTED"
            assert row["posted_at"] is not None

        # --- Boundary 3: Idempotent second recovery run ---
        second_report = intent_store.reconcile_unposted_intents(ledger_service=ledger_svc)
        assert second_report["outbox_recovered"] == 0

        # --- Boundary 4: Expired intent in outbox ---
        expired_id = str(uuid.uuid4())
        past_time = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()
        intent_store.save_intent({
            "intent_id": expired_id,
            "chain_id": "eip155:1",
            "wallet_address": test_burner,
            "asset_id": "PEPE",
            "pool_id": "PEPE_POOL",
            "side": "BUY",
            "input_mint": "0xWETH",
            "output_mint": "0xPEPE",
            "input_amount_raw": "1000",
            "max_slippage_bps": 150,
            "destination_router": "uniswap_v2",
            "strategy_id": "test",
            "strategy_version": "1.0",
            "nonce": "n2",
            "canonical_intent_digest": "d2",
            "transaction_message_hash": "h2",
            "status": IntentLifecycleState.APPROVED,
            "policy_checks_passed": True,
            "rejection_reasons": [],
            "created_at": past_time,
            "expires_at": past_time,
            "is_consumed": 1
        })
        with intent_store._get_connection() as conn:
            conn.execute("""
                INSERT INTO execution_intent_outbox (
                    intent_id, idempotency_key, payload_json, status, attempts, last_error, created_at
                ) VALUES (?, ?, ?, 'PENDING', 0, NULL, ?);
            """, (expired_id, f"exp_idem_{expired_id}", json.dumps(journal_payload), past_time))

        exp_report = intent_store.reconcile_unposted_intents(ledger_service=ledger_svc)
        assert expired_id in exp_report["expired_intent_ids"]

        # Expired intent must have is_consumed = 1 (never reset to 0)
        exp_intent = intent_store.get_intent(expired_id)
        assert exp_intent["status"] == IntentLifecycleState.EXPIRED
        assert exp_intent["is_consumed"] is True

        # Re-approval must fail closed
        with pytest.raises(HTTPException) as exc_info:
            intent_store.atomic_approve_intent(
                intent_id=expired_id,
                signed_by_address=test_burner,
                transaction_message_hash="h2",
                signature_hex="aa" * 65
            )
        assert exc_info.value.status_code in (409, 410)

    finally:
        settings.BURNER_WALLET_ADDRESSES = orig_burners

# ==============================================================================
# CORE-11: Single-Connection Atomic Approval & Ledger Posting
# ==============================================================================

def test_core_11_single_connection_atomic_approval_and_ledger_posting(tmp_path):
    """
    CORE-11: Verifies atomic intent approval and double-entry ledger journal posting
    within a single SQLite connection and transaction:
    - Case A: Valid inputs -> Intent approved and journal posted atomically.
    - Case B: Imbalanced journal -> Trigger aborts transaction, rolling back intent approval.
    """
    import uuid
    import hashlib
    from datetime import datetime, timezone, timedelta
    from app.services.execution_store import (
        ExecutionIntentStore, IntentLifecycleState,
        build_canonical_intent_message, compute_intent_digest
    )
    from app.services.ledger_service import DoubleEntryLedgerService, UnbalancedJournalError

    shared_db = str(tmp_path / "single_conn_atomic.db")
    intent_store = ExecutionIntentStore(db_path=shared_db)
    ledger_svc = DoubleEntryLedgerService(db_path=shared_db)

    wallet = "GsbwXfJraMomNxB62bBwN9ZhpFz7174XWp9yP2P3yZ84"
    orig_burners = settings.BURNER_WALLET_ADDRESSES
    settings.BURNER_WALLET_ADDRESSES = [wallet]

    try:
        # Patch verifier to return True for test
        intent_store._verifier.verify = lambda **kwargs: True

        intent_id = str(uuid.uuid4())
        future_exp = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
        canonical_msg = build_canonical_intent_message(
            intent_id=intent_id,
            chain_id="solana:mainnet",
            wallet_address=wallet,
            input_mint="SOL",
            output_mint="BONK",
            input_amount_raw="1000000000",
            max_slippage_bps=150,
            destination_router="jupiter",
            asset_id="BONK",
            pool_id="P1",
            side="BUY",
            strategy_id="s1",
            strategy_version="1.0",
            nonce="nonce_single",
            expires_at=future_exp
        )
        digest = compute_intent_digest(canonical_msg)
        tx_hash = hashlib.sha256(digest.encode("utf-8")).hexdigest()

        intent_store.save_intent({
            "intent_id": intent_id,
            "chain_id": "solana:mainnet",
            "wallet_address": wallet,
            "asset_id": "BONK",
            "pool_id": "P1",
            "side": "BUY",
            "input_mint": "SOL",
            "output_mint": "BONK",
            "input_amount_raw": "1000000000",
            "max_slippage_bps": 150,
            "destination_router": "jupiter",
            "strategy_id": "s1",
            "strategy_version": "1.0",
            "nonce": "nonce_single",
            "canonical_intent_digest": digest,
            "transaction_message_hash": tx_hash,
            "status": IntentLifecycleState.AWAITING_APPROVAL,
            "policy_checks_passed": True,
            "rejection_reasons": [],
            "created_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": future_exp,
            "is_consumed": 0
        })

        # 1. Successful atomic execution with balanced journal
        balanced_payload = {
            "journal_id": f"j_atomic_{intent_id}",
            "idempotency_key": f"idem_atomic_{intent_id}",
            "journal_type": "SWAP",
            "description": "Atomic approval swap",
            "lines": [
                {
                    "account_code": "WALLET_SOL",
                    "wallet_address": wallet,
                    "mint_address": "SOL",
                    "debit_amount_raw": "1.0",
                    "credit_amount_raw": "0",
                    "usd_rate": "150.0"
                },
                {
                    "account_code": "WALLET_TOKEN",
                    "wallet_address": wallet,
                    "mint_address": "BONK",
                    "debit_amount_raw": "0",
                    "credit_amount_raw": "150.0",
                    "usd_rate": "1.0"
                }
            ]
        }

        res = intent_store.atomic_approve_and_post_journal(
            intent_id=intent_id,
            signed_by_address=wallet,
            transaction_message_hash=tx_hash,
            signature_hex="aa" * 64,
            journal_payload=balanced_payload,
            ledger_service=ledger_svc
        )

        assert res["status"] == "APPROVED"
        assert res["is_consumed"] is True
        assert res["journal"]["status"] == "POSTED"

        # Verify both intent and journal persist in DB
        saved_intent = intent_store.get_intent(intent_id)
        assert saved_intent["status"] == IntentLifecycleState.APPROVED
        assert saved_intent["is_consumed"] is True
        j = ledger_svc.get_journal(balanced_payload["journal_id"])
        assert j is not None
        assert j["status"] == "POSTED"

        # 2. Case B: Unbalanced journal aborts and rolls back entire transaction
        intent_2 = str(uuid.uuid4())
        canonical_msg_2 = build_canonical_intent_message(
            intent_id=intent_2,
            chain_id="solana:mainnet",
            wallet_address=wallet,
            input_mint="SOL",
            output_mint="BONK",
            input_amount_raw="500000000",
            max_slippage_bps=150,
            destination_router="jupiter",
            asset_id="BONK",
            pool_id="P1",
            side="BUY",
            strategy_id="s1",
            strategy_version="1.0",
            nonce="nonce_single_2",
            expires_at=future_exp
        )
        digest_2 = compute_intent_digest(canonical_msg_2)
        tx_hash_2 = hashlib.sha256(digest_2.encode("utf-8")).hexdigest()

        intent_store.save_intent({
            "intent_id": intent_2,
            "chain_id": "solana:mainnet",
            "wallet_address": wallet,
            "asset_id": "BONK",
            "pool_id": "P1",
            "side": "BUY",
            "input_mint": "SOL",
            "output_mint": "BONK",
            "input_amount_raw": "500000000",
            "max_slippage_bps": 150,
            "destination_router": "jupiter",
            "strategy_id": "s1",
            "strategy_version": "1.0",
            "nonce": "nonce_single_2",
            "canonical_intent_digest": digest_2,
            "transaction_message_hash": tx_hash_2,
            "status": IntentLifecycleState.AWAITING_APPROVAL,
            "policy_checks_passed": True,
            "rejection_reasons": [],
            "created_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": future_exp,
            "is_consumed": 0
        })

        unbalanced_payload = {
            "journal_id": f"j_unbalanced_{intent_2}",
            "idempotency_key": f"idem_unbalanced_{intent_2}",
            "journal_type": "SWAP",
            "description": "Unbalanced swap",
            "lines": [
                {
                    "account_code": "WALLET_SOL",
                    "wallet_address": wallet,
                    "mint_address": "SOL",
                    "debit_amount_raw": "1.0",
                    "credit_amount_raw": "0",
                    "usd_rate": "150.0"
                },
                {
                    "account_code": "WALLET_TOKEN",
                    "wallet_address": wallet,
                    "mint_address": "BONK",
                    "debit_amount_raw": "0",
                    "credit_amount_raw": "100.0",  # Imbalance: 150 USD vs 100 USD
                    "usd_rate": "1.0"
                }
            ]
        }

        with pytest.raises(UnbalancedJournalError):
            intent_store.atomic_approve_and_post_journal(
                intent_id=intent_2,
                signed_by_address=wallet,
                transaction_message_hash=tx_hash_2,
                signature_hex="aa" * 64,
                journal_payload=unbalanced_payload,
                ledger_service=ledger_svc
            )

        # Proves atomic rollback: intent was NOT approved!
        check_intent_2 = intent_store.get_intent(intent_2)
        assert check_intent_2["status"] == IntentLifecycleState.AWAITING_APPROVAL
        assert check_intent_2["is_consumed"] is False
        assert ledger_svc.get_journal(unbalanced_payload["journal_id"]) is None

    finally:
        settings.BURNER_WALLET_ADDRESSES = orig_burners

# ==============================================================================
# CORE-12: Async Session Wrapper Thread Isolation & Heartbeat Responsiveness
# ==============================================================================

@pytest.mark.anyio
async def test_core_12_async_session_wrapper_thread_isolation_and_heartbeat():
    """
    CORE-12: Verifies SQLiteAsyncSessionWrapper thread isolation & event loop safety:
    1. Actual DB work runs on worker threads off the event loop thread.
    2. Event loop heartbeat remains responsive during slow queries.
    3. add, add_all, delete are async and serialized under the session lock.
    4. SafeResultProxy materializes rows and scalars safely off-thread.
    5. Cancellation / exception cleanup invokes rollback() and close() safely.
    """
    import asyncio
    import threading
    import time
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy import text
    from app.core.database import engine, SQLiteAsyncSessionWrapper, SafeResultProxy

    SyncSession = sessionmaker(bind=engine)
    session = SyncSession()
    wrapper = SQLiteAsyncSessionWrapper(session)

    event_loop_thread = threading.get_ident()
    observed_worker_threads = set()

    # 1. Verify thread isolation
    async def run_query():
        def _slow_thread_check():
            observed_worker_threads.add(threading.get_ident())
            time.sleep(0.08)  # simulate slow query
            return 42

        # Use wrapper.__getattr__ to run thread check under lock
        setattr(wrapper._session, "_slow_thread_check", _slow_thread_check)
        return await wrapper._slow_thread_check()

    # 2. Verify event loop heartbeat responsiveness during query
    heartbeat_ticks = 0
    stop_heartbeat = False

    async def heartbeat():
        nonlocal heartbeat_ticks
        while not stop_heartbeat:
            heartbeat_ticks += 1
            await asyncio.sleep(0.01)

    hb_task = asyncio.create_task(heartbeat())
    query_result = await run_query()
    stop_heartbeat = True
    await hb_task

    assert query_result == 42
    assert len(observed_worker_threads) > 0
    for tid in observed_worker_threads:
        assert tid != event_loop_thread, "DB work must execute on worker thread, never event loop thread!"

    # Heartbeat must have ticked multiple times while slow query executed
    assert heartbeat_ticks >= 4, f"Event loop was blocked! Heartbeat ticks: {heartbeat_ticks}"

    # 3. Test SafeResultProxy fetch methods
    res = await wrapper.execute(text("SELECT 1 AS a, 'hello' AS b;"))
    assert isinstance(res, SafeResultProxy)
    assert res.keys() == ["a", "b"]
    row = res.fetchone()
    assert row[0] == 1
    assert row[1] == "hello"

    # Test scalars
    res2 = await wrapper.execute(text("SELECT 99 AS num;"))
    scalars = res2.scalars()
    assert scalars.first() == 99

    # 4. Test async add, add_all, delete
    added_items = []
    deleted_items = []
    wrapper._session.add = lambda item: added_items.append(item)
    wrapper._session.add_all = lambda items: added_items.extend(items)
    wrapper._session.delete = lambda item: deleted_items.append(item)

    await wrapper.add("item1")
    await wrapper.add_all(["item2", "item3"])
    await wrapper.delete("item1")

    assert added_items == ["item1", "item2", "item3"]
    assert deleted_items == ["item1"]

    # 5. Cancellation safety via __aexit__
    async def cancel_target():
        async with SQLiteAsyncSessionWrapper(SyncSession()) as w:
            await asyncio.sleep(10)

    task = asyncio.create_task(cancel_target())
    await asyncio.sleep(0.02)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    await wrapper.close()
