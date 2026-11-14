import os
import sqlite3
import hashlib
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from app.core.config import settings

class SqliteMigrator:
    """
    Durable, version-tracked SQLite Schema Migrator for Multi-Chain Memecoin OS.
    Executes incremental migrations inside atomic transactions, validates schema integrity,
    and supports restart recovery on both Linux workstations and Termux Android devices.
    """

    MIGRATIONS = [
        {
            "version": 1,
            "name": "001_core_sqlite_schema",
            "statements": [
                """
                CREATE TABLE IF NOT EXISTS assets (
                    id TEXT PRIMARY KEY,
                    mint_address TEXT UNIQUE NOT NULL,
                    token_program TEXT,
                    name TEXT,
                    symbol TEXT,
                    decimals INTEGER,
                    total_supply_raw TEXT,
                    metadata_uri TEXT,
                    is_token_2022 INTEGER DEFAULT 0,
                    token_2022_extensions TEXT DEFAULT '[]',
                    is_verified INTEGER NOT NULL DEFAULT 0,
                    first_seen_at TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """,
                "CREATE INDEX IF NOT EXISTS idx_assets_mint ON assets(mint_address);",
                """
                CREATE TABLE IF NOT EXISTS pools (
                    id TEXT PRIMARY KEY,
                    asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
                    pool_address TEXT UNIQUE NOT NULL,
                    dex_program TEXT NOT NULL,
                    dex_version TEXT NOT NULL DEFAULT 'v1',
                    base_mint TEXT NOT NULL,
                    quote_mint TEXT NOT NULL,
                    created_slot INTEGER,
                    created_at TEXT,
                    lp_status TEXT NOT NULL DEFAULT 'unknown',
                    lp_burn_pct REAL DEFAULT 0.0,
                    lp_locked_pct REAL DEFAULT 0.0,
                    lp_lock_expiry TEXT,
                    updated_at TEXT
                );
                """,
                "CREATE INDEX IF NOT EXISTS idx_pools_address ON pools(pool_address);",
                "CREATE INDEX IF NOT EXISTS idx_pools_asset ON pools(asset_id);",
                """
                CREATE TABLE IF NOT EXISTS market_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
                    pool_id TEXT REFERENCES pools(id) ON DELETE CASCADE,
                    captured_at TEXT NOT NULL,
                    price_usd REAL NOT NULL,
                    price_native REAL,
                    market_cap_usd REAL,
                    fdv_usd REAL,
                    volume_24h_usd REAL,
                    liquidity_usd REAL
                );
                """,
                "CREATE INDEX IF NOT EXISTS idx_snapshots_asset ON market_snapshots(asset_id);",
                "CREATE INDEX IF NOT EXISTS idx_snapshots_captured ON market_snapshots(captured_at);",
                """
                CREATE TABLE IF NOT EXISTS security_scans (
                    id TEXT PRIMARY KEY,
                    asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
                    scanned_at TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    risk_score REAL NOT NULL,
                    honeypot_detected INTEGER DEFAULT 0,
                    mint_revoked INTEGER DEFAULT 0,
                    lp_burned INTEGER DEFAULT 0,
                    raw_scan_json TEXT
                );
                """,
                "CREATE INDEX IF NOT EXISTS idx_scans_asset ON security_scans(asset_id);",
                """
                CREATE TABLE IF NOT EXISTS paper_accounts (
                    account_id TEXT PRIMARY KEY,
                    chain_id TEXT NOT NULL UNIQUE,
                    initial_balance_usd REAL NOT NULL,
                    current_balance_usd REAL NOT NULL,
                    unrealized_pnl_usd REAL NOT NULL DEFAULT 0.0,
                    realized_pnl_usd REAL NOT NULL DEFAULT 0.0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """,
                """
                CREATE TABLE IF NOT EXISTS paper_positions (
                    position_id TEXT PRIMARY KEY,
                    chain_id TEXT NOT NULL,
                    asset_address TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    entry_price_usd REAL NOT NULL,
                    current_price_usd REAL NOT NULL,
                    quantity REAL NOT NULL,
                    cost_basis_usd REAL NOT NULL,
                    unrealized_pnl_usd REAL NOT NULL DEFAULT 0.0,
                    opened_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """,
                """
                CREATE TABLE IF NOT EXISTS paper_orders (
                    order_id TEXT PRIMARY KEY,
                    chain_id TEXT NOT NULL,
                    asset_address TEXT NOT NULL,
                    side TEXT NOT NULL,
                    quantity REAL NOT NULL,
                    price_usd REAL NOT NULL,
                    status TEXT NOT NULL,
                    fee_paid_usd REAL NOT NULL DEFAULT 0.0,
                    created_at TEXT NOT NULL
                );
                """,
                """
                CREATE TABLE IF NOT EXISTS execution_intents (
                    intent_id TEXT PRIMARY KEY,
                    chain_id TEXT NOT NULL,
                    wallet_address TEXT NOT NULL,
                    asset_id TEXT,
                    pool_id TEXT,
                    side TEXT NOT NULL,
                    input_mint TEXT NOT NULL,
                    output_mint TEXT NOT NULL,
                    input_amount_raw TEXT NOT NULL,
                    max_slippage_bps INTEGER NOT NULL,
                    destination_router TEXT NOT NULL,
                    strategy_id TEXT,
                    strategy_version TEXT,
                    nonce TEXT NOT NULL,
                    canonical_intent_digest TEXT NOT NULL UNIQUE,
                    transaction_message_hash TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL DEFAULT 'AWAITING_APPROVAL',
                    policy_checks_passed INTEGER NOT NULL DEFAULT 0,
                    rejection_reasons TEXT DEFAULT '[]',
                    is_consumed INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    consumed_at TEXT
                );
                """,
                """
                CREATE TABLE IF NOT EXISTS trade_approvals (
                    approval_id TEXT PRIMARY KEY,
                    intent_id TEXT NOT NULL UNIQUE REFERENCES execution_intents(intent_id) ON DELETE RESTRICT,
                    client_signature TEXT NOT NULL,
                    signer_public_key TEXT NOT NULL,
                    approved_at TEXT NOT NULL
                );
                """,
                """
                CREATE TABLE IF NOT EXISTS ledger_journals (
                    id TEXT PRIMARY KEY,
                    transaction_signature TEXT UNIQUE,
                    order_id TEXT,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    journal_type TEXT NOT NULL,
                    description TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'DRAFT',
                    posted_at TEXT NOT NULL
                );
                """,
                """
                CREATE TABLE IF NOT EXISTS ledger_lines (
                    id TEXT PRIMARY KEY,
                    journal_id TEXT NOT NULL REFERENCES ledger_journals(id) ON DELETE RESTRICT,
                    account_type TEXT NOT NULL,
                    debit_amount_raw TEXT NOT NULL DEFAULT '0',
                    credit_amount_raw TEXT NOT NULL DEFAULT '0',
                    currency TEXT NOT NULL,
                    usd_rate TEXT NOT NULL,
                    integer_units INTEGER NOT NULL
                );
                """,
                """
                CREATE TRIGGER IF NOT EXISTS trg_sqlite_check_journal_post_invariants
                BEFORE UPDATE OF status ON ledger_journals
                FOR EACH ROW
                WHEN NEW.status = 'POSTED' AND OLD.status != 'POSTED'
                BEGIN
                    SELECT CASE
                        WHEN (SELECT COUNT(*) FROM ledger_lines WHERE journal_id = NEW.id) < 2
                        THEN RAISE(ABORT, 'LEDGER INVARIANT VIOLATION: A posted journal must contain at least two entries.')
                    END;

                    SELECT CASE
                        WHEN (
                            SELECT ABS(
                                TOTAL(CAST(ROUND(CAST(debit_amount_raw AS REAL) * CAST(usd_rate AS REAL) * 100000000.0) AS INTEGER)) -
                                TOTAL(CAST(ROUND(CAST(credit_amount_raw AS REAL) * CAST(usd_rate AS REAL) * 100000000.0) AS INTEGER))
                            )
                            FROM ledger_lines
                            WHERE journal_id = NEW.id
                        ) > 0
                        THEN RAISE(ABORT, 'LEDGER INVARIANT VIOLATION: Transaction is unbalanced. Debits must equal credits in integer USD base units.')
                    END;
                END;
                """,
                """
                CREATE TRIGGER IF NOT EXISTS trg_sqlite_prevent_tamper_posted_journal
                BEFORE UPDATE ON ledger_journals
                FOR EACH ROW
                WHEN OLD.status = 'POSTED' AND NEW.status != 'POSTED'
                BEGIN
                    SELECT RAISE(ABORT, 'LEDGER IMMUTABILITY VIOLATION: Posted financial journals are permanently immutable and cannot be unposted.');
                END;
                """
            ]
        },
        {
            "version": 2,
            "name": "002_multichain_registry",
            "statements": [
                """
                CREATE TABLE IF NOT EXISTS chain_registries (
                    chain_id TEXT PRIMARY KEY,
                    chain_name TEXT NOT NULL,
                    network_type TEXT NOT NULL,
                    group_tier TEXT NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    rpc_url TEXT,
                    native_symbol TEXT NOT NULL,
                    decimals INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );
                """,
                """
                INSERT OR IGNORE INTO chain_registries (chain_id, chain_name, network_type, group_tier, is_active, rpc_url, native_symbol, decimals, created_at) VALUES
                ('solana:mainnet', 'Solana Mainnet', 'solana', 'A', 1, 'https://api.mainnet-beta.solana.com', 'SOL', 9, datetime('now')),
                ('eip155:1', 'Ethereum Mainnet', 'evm', 'A', 1, 'https://eth.llamarpc.com', 'ETH', 18, datetime('now')),
                ('eip155:8453', 'Base Mainnet', 'evm', 'A', 1, 'https://mainnet.base.org', 'ETH', 18, datetime('now')),
                ('eip155:56', 'BNB Smart Chain', 'evm', 'A', 1, 'https://bsc-dataseed.binance.org', 'BNB', 18, datetime('now')),
                ('eip155:42161', 'Arbitrum One', 'evm', 'B', 0, 'https://arb1.arbitrum.io/rpc', 'ETH', 18, datetime('now')),
                ('eip155:10', 'Optimism', 'evm', 'B', 0, 'https://mainnet.optimism.io', 'ETH', 18, datetime('now')),
                ('eip155:137', 'Polygon PoS', 'evm', 'B', 0, 'https://polygon-rpc.com', 'POL', 18, datetime('now')),
                ('eip155:43114', 'Avalanche C-Chain', 'evm', 'B', 0, 'https://api.avax.network/ext/bc/C/rpc', 'AVAX', 18, datetime('now')),
                ('sui:mainnet', 'Sui Mainnet', 'move', 'C', 0, 'https://fullnode.mainnet.sui.io:443', 'SUI', 9, datetime('now')),
                ('aptos:mainnet', 'Aptos Mainnet', 'move', 'C', 0, 'https://fullnode.mainnet.aptoslabs.com/v1', 'APT', 8, datetime('now')),
                ('ton:mainnet', 'The Open Network', 'tvm', 'C', 0, 'https://toncenter.com/api/v2/jsonRPC', 'TON', 9, datetime('now')),
                ('tron:mainnet', 'TRON Mainnet', 'tvm', 'C', 0, 'https://api.trongrid.io', 'TRX', 6, datetime('now'));
                """
            ]
        },
        {
            "version": 3,
            "name": "003_system_audit_log",
            "statements": [
                """
                CREATE TABLE IF NOT EXISTS system_audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    details TEXT,
                    actor TEXT DEFAULT 'system',
                    created_at TEXT NOT NULL
                );
                """
            ]
        }
    ]

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            raw_url = settings.DATABASE_URL
            if ":memory:" in raw_url:
                self.db_path = ":memory:"
            else:
                self.db_path = settings.SQLITE_DB_PATH
                if raw_url.startswith("sqlite:///"):
                    clean = raw_url.replace("sqlite:///", "")
                    if clean:
                        self.db_path = clean
        else:
            self.db_path = db_path

        if self.db_path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)

    def _get_connection(self) -> sqlite3.Connection:
        if self.db_path == ":memory:":
            conn = sqlite3.connect("file:memecoin_migrator_mem?mode=memory&cache=shared", uri=True, timeout=30.0, isolation_level=None)
        else:
            conn = sqlite3.connect(self.db_path, timeout=30.0, check_same_thread=False, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        if self.db_path != ":memory:":
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute("PRAGMA busy_timeout = 30000;")
        return conn

    def _ensure_migration_table(self, conn: sqlite3.Connection):
        conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                applied_at TEXT NOT NULL,
                checksum TEXT NOT NULL
            );
        """)

    def get_applied_versions(self) -> List[int]:
        with self._get_connection() as conn:
            self._ensure_migration_table(conn)
            cursor = conn.execute("SELECT version FROM schema_migrations ORDER BY version ASC;")
            return [row["version"] for row in cursor.fetchall()]

    def get_current_version(self) -> int:
        versions = self.get_applied_versions()
        return max(versions) if versions else 0

    def apply_migrations(self) -> List[int]:
        """
        Executes pending migrations sequentially within immediate transactions.
        Returns list of newly applied migration versions.
        """
        applied_versions = self.get_applied_versions()
        newly_applied = []

        conn = self._get_connection()
        try:
            self._ensure_migration_table(conn)
            for migration in self.MIGRATIONS:
                version = migration["version"]
                name = migration["name"]
                if version in applied_versions:
                    continue

                statements = migration["statements"]
                combined_sql = "\n".join(statements)
                checksum = hashlib.sha256(combined_sql.encode("utf-8")).hexdigest()

                conn.execute("BEGIN IMMEDIATE;")
                try:
                    for stmt in statements:
                        clean_stmt = stmt.strip()
                        if clean_stmt:
                            conn.execute(clean_stmt)

                    now_str = datetime.now(timezone.utc).isoformat()
                    conn.execute(
                        "INSERT INTO schema_migrations (version, name, applied_at, checksum) VALUES (?, ?, ?, ?);",
                        (version, name, now_str, checksum)
                    )
                    conn.execute("COMMIT;")
                    newly_applied.append(version)
                except Exception as e:
                    conn.execute("ROLLBACK;")
                    raise RuntimeError(f"Migration {version} ({name}) failed: {str(e)}") from e
        finally:
            conn.close()

        return newly_applied

    def verify_schema(self) -> Dict[str, Any]:
        """
        Verifies that all core tables exist in sqlite_master.
        """
        required_tables = [
            "schema_migrations", "assets", "pools", "market_snapshots",
            "security_scans", "paper_accounts", "paper_positions",
            "paper_orders", "execution_intents", "trade_approvals",
            "ledger_journals", "ledger_lines", "chain_registries",
            "system_audit_log"
        ]

        with self._get_connection() as conn:
            cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table';")
            existing_tables = {row["name"] for row in cursor.fetchall()}

        missing = [t for t in required_tables if t not in existing_tables]
        return {
            "valid": len(missing) == 0,
            "current_version": self.get_current_version(),
            "existing_tables": sorted(list(existing_tables)),
            "missing_tables": missing
        }
