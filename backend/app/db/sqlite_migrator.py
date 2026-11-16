import os
import sqlite3
import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import List, Dict, Any, Optional
import re
from app.core.config import settings


def parse_strict_raw_unit(value: Any, field_name: str, allow_empty: bool = False) -> int:
    """
    Parses and strictly validates non-negative integer raw unit quantities.
    Rejects:
    - Boolean values (True, False)
    - Fractional strings (e.g. '1.5')
    - Scientific notation (e.g. '1e18', '1E18')
    - Negative numbers (e.g. '-1')
    - Signs (e.g. '+1')
    - Whitespace (e.g. ' 1 ', '10 ')
    - Unicode digits/numerals (e.g. '１２３', '²', '١٢٣')
    - Malformed strings (e.g. 'abc', '12a34')
    - Binary floats (e.g. 1.0, 1.5)
    Preserves arbitrary-size Python integers exactly.
    """
    if value is None or value == "":
        if allow_empty:
            return 0
        raise ValueError(f"Strict raw unit validation failed for '{field_name}': cannot be NULL or empty.")

    if isinstance(value, bool):
        raise ValueError(f"Strict raw unit validation failed for '{field_name}': boolean type forbidden.")

    if isinstance(value, float):
        raise ValueError(f"Strict raw unit validation failed for '{field_name}': binary float type forbidden.")

    if isinstance(value, int):
        if value < 0:
            raise ValueError(f"Strict raw unit validation failed for '{field_name}': negative value {value} forbidden.")
        return value

    if not isinstance(value, str):
        raise ValueError(f"Strict raw unit validation failed for '{field_name}': invalid type {type(value).__name__}.")

    # Canonical string representation must be strictly ASCII digits: ^[0-9]+$ (no signs, no whitespace, no dots, no exponents, no unicode digits)
    if not (value and all(c in "0123456789" for c in value)):
        raise ValueError(f"Strict raw unit validation failed for '{field_name}': invalid format '{value}'. Must be non-negative ASCII integer digits only.")

    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"Strict raw unit validation failed for '{field_name}': invalid integer literal '{value}'.") from exc


def format_decimal_exact(val: Any) -> str:
    """
    Serializes a Decimal, int, or numeric string to an exact fixed-point string
    without scientific notation, trailing decimal points, or precision clipping.
    
    Guarantees:
    - Never invokes Decimal.normalize() or operations depending on the ambient
      process/thread Decimal context precision (e.g. prec=28).
    - Preserves all significant digits exactly (including 29+, 50+, 100+ digits).
    - Never converts through Python float.
    - Explicitly rejects None, booleans, and non-finite values (NaN, sNaN, Infinity).
    - Deterministically formats zeros (including -0, 0.000) as "0".
    - Strips redundant trailing fractional zeros deterministically without changing value.
    - Preserves exact Decimal equality upon deserialization: Decimal(format_decimal_exact(d)) == d.
    """
    if val is None:
        raise ValueError("Invalid financial decimal value: None is not permitted.")
    if isinstance(val, bool):
        raise ValueError(f"Invalid financial decimal value: bool ({val!r}) is not permitted.")
    if isinstance(val, float):
        raise TypeError(
            f"Direct float values ({val!r}) are rejected in exact financial serialization "
            "to prevent IEEE-754 precision loss. Use Decimal, int, or exact string."
        )

    if isinstance(val, Decimal):
        d = val
    elif isinstance(val, int):
        d = Decimal(val)
    elif isinstance(val, str):
        val_str = val.strip()
        if not val_str:
            raise ValueError("Invalid financial decimal value: empty string.")
        try:
            d = Decimal(val_str)
        except InvalidOperation as exc:
            raise ValueError(f"Invalid decimal literal: {val!r}") from exc
    else:
        raise TypeError(f"Expected Decimal, int, or str, got {type(val).__name__} ({val!r})")

    if not d.is_finite():
        raise ValueError(f"Cannot serialize non-finite financial decimal: {val!r}")

    sign, digits, exponent = d.as_tuple()
    if not digits or all(digit == 0 for digit in digits):
        return "0"

    digits_str = "".join(str(digit) for digit in digits)
    if exponent >= 0:
        res = digits_str + ("0" * exponent)
    else:
        k = -exponent
        n = len(digits_str)
        if n > k:
            integer_part = digits_str[:n - k]
            fractional_part = digits_str[n - k:].rstrip("0")
        else:
            integer_part = "0"
            fractional_part = ("0" * (k - n) + digits_str).rstrip("0")

        if fractional_part:
            res = f"{integer_part}.{fractional_part}"
        else:
            res = integer_part

    if sign == 1 and res != "0":
        return f"-{res}"
    return res


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
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    journal_id TEXT NOT NULL REFERENCES ledger_journals(id) ON DELETE RESTRICT,
                    account_code TEXT NOT NULL,
                    wallet_address TEXT NOT NULL,
                    mint_address TEXT NOT NULL,
                    debit_amount_raw TEXT NOT NULL,
                    credit_amount_raw TEXT NOT NULL,
                    usd_rate TEXT NOT NULL,
                    debit_usd_units INTEGER NOT NULL DEFAULT 0,
                    credit_usd_units INTEGER NOT NULL DEFAULT 0,
                    CHECK (debit_usd_units >= 0 AND credit_usd_units >= 0),
                    CHECK (CAST(debit_amount_raw AS NUMERIC) >= 0 AND CAST(credit_amount_raw AS NUMERIC) >= 0)
                );
                """,
                """
                CREATE TRIGGER IF NOT EXISTS trg_prevent_direct_posted_insert
                BEFORE INSERT ON ledger_journals
                WHEN NEW.status = 'POSTED'
                BEGIN
                    SELECT RAISE(ABORT, 'FINANCIAL INTEGRITY VIOLATION (LEDGER-03): Direct INSERT with status=POSTED forbidden. Journals must be created in DRAFT.');
                END;
                """,
                """
                CREATE TRIGGER IF NOT EXISTS trg_enforce_valid_status_transition
                BEFORE UPDATE OF status ON ledger_journals
                WHEN NOT (OLD.status = 'DRAFT' AND NEW.status = 'POSTED')
                BEGIN
                    SELECT RAISE(ABORT, 'FINANCIAL INTEGRITY VIOLATION: Status rollback or invalid transition forbidden. State POSTED is irreversible.');
                END;
                """,
                """
                CREATE TRIGGER IF NOT EXISTS trg_check_journal_post_invariants
                BEFORE UPDATE OF status ON ledger_journals
                WHEN NEW.status = 'POSTED' AND OLD.status = 'DRAFT'
                BEGIN
                    SELECT RAISE(ABORT, 'FINANCIAL INTEGRITY VIOLATION (LEDGER-03): Journal has fewer than 2 lines.')
                    WHERE (SELECT COUNT(*) FROM ledger_lines WHERE journal_id = NEW.id) < 2;

                    SELECT RAISE(ABORT, 'FINANCIAL INTEGRITY VIOLATION (LEDGER-01): Line fixed-point USD units do not match source raw amount and USD rate.')
                    WHERE EXISTS (
                        SELECT 1 FROM ledger_lines
                        WHERE journal_id = NEW.id
                          AND (
                              debit_usd_units != CAST(ROUND(CAST(debit_amount_raw AS NUMERIC) * CAST(usd_rate AS NUMERIC) * 100000000.0) AS INTEGER)
                              OR
                              credit_usd_units != CAST(ROUND(CAST(credit_amount_raw AS NUMERIC) * CAST(usd_rate AS NUMERIC) * 100000000.0) AS INTEGER)
                          )
                    );

                    SELECT RAISE(ABORT, 'FINANCIAL INTEGRITY VIOLATION (LEDGER-01): Journal debits and credits do not balance in exact integer units.')
                    WHERE (
                        SELECT COALESCE(SUM(debit_usd_units - credit_usd_units), 0)
                        FROM ledger_lines WHERE journal_id = NEW.id
                    ) != 0;
                END;
                """,
                """
                CREATE TRIGGER IF NOT EXISTS trg_prevent_posted_journal_update
                BEFORE UPDATE ON ledger_journals
                WHEN OLD.status = 'POSTED'
                BEGIN
                    SELECT RAISE(ABORT, 'FINANCIAL INTEGRITY VIOLATION (LEDGER-05): Posted ledger journals cannot be modified or reverted.');
                END;
                """,
                """
                CREATE TRIGGER IF NOT EXISTS trg_prevent_posted_journal_delete
                BEFORE DELETE ON ledger_journals
                WHEN OLD.status = 'POSTED'
                BEGIN
                    SELECT RAISE(ABORT, 'FINANCIAL INTEGRITY VIOLATION (LEDGER-06): Posted ledger journals cannot be deleted.');
                END;
                """,
                """
                CREATE TRIGGER IF NOT EXISTS trg_prevent_line_insert_on_posted
                BEFORE INSERT ON ledger_lines
                BEGIN
                    SELECT RAISE(ABORT, 'FINANCIAL INTEGRITY VIOLATION: Cannot insert lines into an already POSTED journal.')
                    WHERE (SELECT status FROM ledger_journals WHERE id = NEW.journal_id) = 'POSTED';
                END;
                """,
                """
                CREATE TRIGGER IF NOT EXISTS trg_prevent_line_update
                BEFORE UPDATE ON ledger_lines
                BEGIN
                    SELECT RAISE(ABORT, 'FINANCIAL INTEGRITY VIOLATION (LEDGER-05): Posted ledger lines cannot be modified.');
                END;
                """,
                """
                CREATE TRIGGER IF NOT EXISTS trg_prevent_line_delete
                BEFORE DELETE ON ledger_lines
                BEGIN
                    SELECT RAISE(ABORT, 'FINANCIAL INTEGRITY VIOLATION (LEDGER-06): Posted ledger lines cannot be deleted.');
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
        },
        {
            "version": 4,
            "name": "004_provider_and_rpc_manager",
            "statements": [
                """
                CREATE TABLE IF NOT EXISTS providers (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    provider_type TEXT NOT NULL,
                    tier TEXT NOT NULL DEFAULT 'free',
                    is_enabled INTEGER NOT NULL DEFAULT 0,
                    is_paid INTEGER NOT NULL DEFAULT 0,
                    priority INTEGER NOT NULL DEFAULT 100,
                    capabilities_json TEXT NOT NULL DEFAULT '[]',
                    chains_json TEXT NOT NULL DEFAULT '[]',
                    auth_type TEXT NOT NULL DEFAULT 'none',
                    rate_limit_rpm INTEGER DEFAULT 60,
                    daily_request_budget INTEGER DEFAULT 1000,
                    daily_spend_budget_usd REAL DEFAULT 0.0,
                    timeout_seconds REAL DEFAULT 10.0,
                    max_retries INTEGER DEFAULT 3,
                    health_status TEXT NOT NULL DEFAULT 'unknown',
                    consecutive_failures INTEGER NOT NULL DEFAULT 0,
                    cooldown_until TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """,
                "CREATE INDEX IF NOT EXISTS idx_providers_enabled ON providers(is_enabled);",
                "CREATE INDEX IF NOT EXISTS idx_providers_type ON providers(provider_type);",
                """
                CREATE TABLE IF NOT EXISTS provider_credentials (
                    id TEXT PRIMARY KEY,
                    provider_id TEXT NOT NULL,
                    credential_type TEXT NOT NULL,
                    encrypted_secret TEXT NOT NULL,
                    key_fingerprint TEXT NOT NULL,
                    redacted_preview TEXT NOT NULL,
                    is_revoked INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (provider_id) REFERENCES providers(id) ON DELETE CASCADE
                );
                """,
                "CREATE INDEX IF NOT EXISTS idx_credentials_provider ON provider_credentials(provider_id);",
                """
                CREATE TABLE IF NOT EXISTS chain_rpc_endpoints (
                    id TEXT PRIMARY KEY,
                    chain_id TEXT NOT NULL,
                    provider_id TEXT,
                    name TEXT NOT NULL,
                    endpoint_url TEXT NOT NULL,
                    ws_url TEXT,
                    priority INTEGER NOT NULL DEFAULT 100,
                    is_enabled INTEGER NOT NULL DEFAULT 0,
                    is_paid INTEGER NOT NULL DEFAULT 0,
                    daily_budget INTEGER DEFAULT 5000,
                    health_status TEXT NOT NULL DEFAULT 'unknown',
                    last_checked_at TEXT,
                    last_success_at TEXT,
                    last_error TEXT,
                    consecutive_failures INTEGER NOT NULL DEFAULT 0,
                    cooldown_until TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (chain_id) REFERENCES chain_registries(chain_id) ON DELETE CASCADE,
                    FOREIGN KEY (provider_id) REFERENCES providers(id) ON DELETE SET NULL
                );
                """,
                "CREATE INDEX IF NOT EXISTS idx_rpc_chain ON chain_rpc_endpoints(chain_id);",
                "CREATE INDEX IF NOT EXISTS idx_rpc_provider ON chain_rpc_endpoints(provider_id);",
                "CREATE INDEX IF NOT EXISTS idx_rpc_enabled ON chain_rpc_endpoints(is_enabled);",
                """
                CREATE TABLE IF NOT EXISTS provider_usage_records (
                    id TEXT PRIMARY KEY,
                    provider_id TEXT NOT NULL,
                    chain_id TEXT,
                    capability TEXT NOT NULL,
                    day_date TEXT NOT NULL,
                    request_count INTEGER NOT NULL DEFAULT 0,
                    failed_count INTEGER NOT NULL DEFAULT 0,
                    estimated_cost_usd REAL NOT NULL DEFAULT 0.0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (provider_id) REFERENCES providers(id) ON DELETE CASCADE,
                    UNIQUE(provider_id, chain_id, capability, day_date)
                );
                """,
                "CREATE INDEX IF NOT EXISTS idx_usage_day ON provider_usage_records(day_date);",
                """
                CREATE TABLE IF NOT EXISTS provider_fallback_rules (
                    id TEXT PRIMARY KEY,
                    capability TEXT NOT NULL,
                    chain_id TEXT,
                    primary_provider_id TEXT NOT NULL,
                    fallback_provider_id TEXT NOT NULL,
                    is_authorized INTEGER NOT NULL DEFAULT 1,
                    priority INTEGER NOT NULL DEFAULT 10,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (primary_provider_id) REFERENCES providers(id) ON DELETE CASCADE,
                    FOREIGN KEY (fallback_provider_id) REFERENCES providers(id) ON DELETE CASCADE
                );
                """,
                """
                CREATE TABLE IF NOT EXISTS provider_audit_log (
                    id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    provider_id TEXT,
                    chain_id TEXT,
                    details_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );
                """,
                "CREATE INDEX IF NOT EXISTS idx_audit_event ON provider_audit_log(event_type);",
                """
                INSERT OR IGNORE INTO chain_rpc_endpoints (id, chain_id, name, endpoint_url, ws_url, priority, is_enabled, is_paid, daily_budget, created_at, updated_at) VALUES
                ('rpc-solana-public', 'solana:mainnet', 'Solana Public RPC', 'https://api.mainnet-beta.solana.com', 'wss://api.mainnet-beta.solana.com', 1, 0, 0, 50000, datetime('now'), datetime('now')),
                ('rpc-eth-llamarpc', 'eip155:1', 'Ethereum LlamaRPC', 'https://eth.llamarpc.com', NULL, 1, 0, 0, 50000, datetime('now'), datetime('now')),
                ('rpc-eth-ankr', 'eip155:1', 'Ethereum Ankr Public', 'https://rpc.ankr.com/eth', NULL, 2, 0, 0, 50000, datetime('now'), datetime('now')),
                ('rpc-base-public', 'eip155:8453', 'Base Public RPC', 'https://mainnet.base.org', NULL, 1, 0, 0, 50000, datetime('now'), datetime('now')),
                ('rpc-base-llamarpc', 'eip155:8453', 'Base LlamaRPC', 'https://base.llamarpc.com', NULL, 2, 0, 0, 50000, datetime('now'), datetime('now')),
                ('rpc-bsc-dataseed', 'eip155:56', 'BNB Dataseed', 'https://bsc-dataseed.binance.org', NULL, 1, 0, 0, 50000, datetime('now'), datetime('now')),
                ('rpc-bsc-llamarpc', 'eip155:56', 'BNB LlamaRPC', 'https://binance.llamarpc.com', NULL, 2, 0, 0, 50000, datetime('now'), datetime('now')),
                ('rpc-arb-public', 'eip155:42161', 'Arbitrum One Public', 'https://arb1.arbitrum.io/rpc', NULL, 1, 0, 0, 50000, datetime('now'), datetime('now')),
                ('rpc-opt-public', 'eip155:10', 'Optimism Public', 'https://mainnet.optimism.io', NULL, 1, 0, 0, 50000, datetime('now'), datetime('now')),
                ('rpc-poly-public', 'eip155:137', 'Polygon PoS Public', 'https://polygon-rpc.com', NULL, 1, 0, 0, 50000, datetime('now'), datetime('now')),
                ('rpc-avax-public', 'eip155:43114', 'Avalanche C-Chain Public', 'https://api.avax.network/ext/bc/C/rpc', NULL, 1, 0, 0, 50000, datetime('now'), datetime('now')),
                ('rpc-sui-public', 'sui:mainnet', 'Sui Fullnode Public', 'https://fullnode.mainnet.sui.io:443', NULL, 1, 0, 0, 50000, datetime('now'), datetime('now')),
                ('rpc-aptos-public', 'aptos:mainnet', 'Aptos Fullnode Public', 'https://fullnode.mainnet.aptoslabs.com/v1', NULL, 1, 0, 0, 50000, datetime('now'), datetime('now')),
                ('rpc-ton-public', 'ton:mainnet', 'TON Center Public', 'https://toncenter.com/api/v2/jsonRPC', NULL, 1, 0, 0, 50000, datetime('now'), datetime('now')),
                ('rpc-tron-public', 'tron:mainnet', 'TronGrid Public', 'https://api.trongrid.io', NULL, 1, 0, 0, 50000, datetime('now'), datetime('now'));
                """,
                """
                INSERT OR IGNORE INTO providers (id, name, provider_type, tier, is_enabled, is_paid, priority, capabilities_json, chains_json, auth_type, rate_limit_rpm, daily_request_budget, created_at, updated_at) VALUES
                ('prov-dexscreener', 'DEX Screener', 'market_data', 'free', 0, 0, 10, '["MARKET_DATA", "MARKET_DISCOVERY", "PRICE_LIQUIDITY"]', '["solana:mainnet", "eip155:1", "eip155:8453", "eip155:56", "eip155:42161", "eip155:10", "eip155:137", "eip155:43114", "sui:mainnet", "aptos:mainnet", "ton:mainnet", "tron:mainnet"]', 'none', 300, 10000, datetime('now'), datetime('now')),
                ('prov-goplus', 'GoPlus Security', 'security_forensics', 'free', 0, 0, 10, '["SECURITY_FORENSICS"]', '["solana:mainnet", "eip155:1", "eip155:8453", "eip155:56", "eip155:42161", "eip155:10", "eip155:137", "eip155:43114", "sui:mainnet", "ton:mainnet", "tron:mainnet"]', 'none', 30, 2000, datetime('now'), datetime('now')),
                ('prov-rugcheck', 'RugCheck XYZ', 'security_forensics', 'free', 0, 0, 10, '["SECURITY_FORENSICS", "TOKEN_METADATA"]', '["solana:mainnet"]', 'none', 60, 5000, datetime('now'), datetime('now')),
                ('prov-coingecko', 'CoinGecko Free API', 'market_data', 'free', 0, 0, 20, '["MARKET_DATA", "TOKEN_METADATA", "PRICE_LIQUIDITY"]', '["solana:mainnet", "eip155:1", "eip155:8453", "eip155:56", "eip155:42161", "eip155:10", "eip155:137", "eip155:43114"]', 'none', 30, 1000, datetime('now'), datetime('now'));
                """
            ]
        },
        {
            "version": 5,
            "name": "005_provider_reservations_and_caip2_canonicalization",
            "statements": [
                """
                CREATE TABLE IF NOT EXISTS provider_reservations (
                    id TEXT PRIMARY KEY,
                    provider_id TEXT NOT NULL,
                    capability TEXT NOT NULL,
                    chain_id TEXT,
                    cost_usd REAL NOT NULL DEFAULT 0.0,
                    status TEXT NOT NULL DEFAULT 'RESERVED',
                    idempotency_key TEXT,
                    reserved_at TEXT NOT NULL,
                    committed_at TEXT,
                    released_at TEXT,
                    failed_at TEXT,
                    error_message TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (provider_id) REFERENCES providers(id) ON DELETE CASCADE,
                    CHECK (status IN ('RESERVED', 'COMMITTED', 'RELEASED', 'FAILED', 'EXPIRED'))
                );
                """,
                "CREATE INDEX IF NOT EXISTS idx_res_provider_status ON provider_reservations(provider_id, status);",
                "CREATE INDEX IF NOT EXISTS idx_res_idempotency ON provider_reservations(idempotency_key);",
                "CREATE INDEX IF NOT EXISTS idx_res_reserved_at ON provider_reservations(reserved_at);"
            ]
        },
        {
            "version": 6,
            "name": "006_real_multichain_discovery_and_ingestion",
            "statements": [
                """
                CREATE TABLE IF NOT EXISTS discovered_tokens (
                    id TEXT PRIMARY KEY,
                    chain_id TEXT NOT NULL,
                    address TEXT NOT NULL,
                    symbol TEXT NOT NULL DEFAULT 'UNKNOWN',
                    name TEXT NOT NULL DEFAULT 'Unknown Token',
                    decimals INTEGER,
                    total_supply TEXT,
                    is_verified INTEGER NOT NULL DEFAULT 0,
                    discovery_source TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                    last_seen_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                    metadata_json TEXT,
                    UNIQUE(chain_id, address)
                );
                """,
                """
                CREATE TABLE IF NOT EXISTS discovered_pools (
                    id TEXT PRIMARY KEY,
                    chain_id TEXT NOT NULL,
                    pool_address TEXT NOT NULL,
                    protocol TEXT NOT NULL,
                    factory_address TEXT,
                    token0_address TEXT NOT NULL,
                    token1_address TEXT NOT NULL,
                    fee_tier_bps INTEGER DEFAULT 30,
                    block_number INTEGER,
                    slot INTEGER,
                    tx_hash TEXT,
                    log_index INTEGER,
                    discovery_source TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                    is_canonical INTEGER NOT NULL DEFAULT 1,
                    status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK(status IN ('ACTIVE', 'REORGED', 'INACTIVE')),
                    UNIQUE(chain_id, pool_address)
                );
                """,
                """
                CREATE TABLE IF NOT EXISTS market_data_snapshots (
                    id TEXT PRIMARY KEY,
                    pool_id TEXT NOT NULL,
                    chain_id TEXT NOT NULL,
                    provider_id TEXT NOT NULL,
                    price_usd REAL,
                    liquidity_usd REAL,
                    volume_24h_usd REAL,
                    reserves_token0 REAL,
                    reserves_token1 REAL,
                    price_impact_bps REAL,
                    fdv_usd REAL,
                    confidence_score REAL NOT NULL DEFAULT 1.0,
                    data_freshness TEXT NOT NULL DEFAULT 'REALTIME',
                    observation_time TEXT NOT NULL,
                    retrieved_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                    raw_payload_sanitized TEXT
                );
                """,
                """
                CREATE TABLE IF NOT EXISTS ingestion_checkpoints (
                    id TEXT PRIMARY KEY,
                    chain_id TEXT NOT NULL,
                    protocol TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    last_block_or_slot INTEGER NOT NULL DEFAULT 0,
                    last_block_hash TEXT,
                    last_event_id TEXT,
                    lag_blocks_or_slots INTEGER DEFAULT 0,
                    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                    UNIQUE(chain_id, protocol, source_type)
                );
                """,
                """
                CREATE TABLE IF NOT EXISTS reorg_invalidation_events (
                    id TEXT PRIMARY KEY,
                    chain_id TEXT NOT NULL,
                    block_number INTEGER NOT NULL,
                    block_hash TEXT NOT NULL,
                    affected_pools_count INTEGER NOT NULL DEFAULT 0,
                    invalidated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                    reason TEXT NOT NULL
                );
                """,
                "CREATE INDEX IF NOT EXISTS idx_disc_tokens_chain ON discovered_tokens(chain_id);",
                "CREATE INDEX IF NOT EXISTS idx_disc_pools_chain_proto ON discovered_pools(chain_id, protocol);",
                "CREATE INDEX IF NOT EXISTS idx_disc_pools_tokens ON discovered_pools(token0_address, token1_address);",
                "CREATE INDEX IF NOT EXISTS idx_mkt_snapshots_pool ON market_data_snapshots(pool_id, observation_time);",
                "CREATE INDEX IF NOT EXISTS idx_checkpoints_chain ON ingestion_checkpoints(chain_id, protocol);"
            ]
        },
        {
            "version": 7,
            "name": "007_persistent_multichain_paper_trading",
            "statements": [
                """
                CREATE TABLE IF NOT EXISTS paper_portfolios (
                    portfolio_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
                );
                """,
                """
                CREATE TABLE IF NOT EXISTS paper_account_balances (
                    id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    chain_id TEXT NOT NULL,
                    asset_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    decimals INTEGER NOT NULL,
                    free_amount_raw TEXT NOT NULL,
                    locked_amount_raw TEXT NOT NULL DEFAULT '0',
                    usd_rate REAL NOT NULL DEFAULT 1.0,
                    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                    UNIQUE(account_id, chain_id, asset_id)
                );
                """,
                """
                CREATE TABLE IF NOT EXISTS paper_orders_v2 (
                    order_id TEXT PRIMARY KEY,
                    client_order_id TEXT UNIQUE,
                    account_id TEXT NOT NULL,
                    chain_id TEXT NOT NULL,
                    asset_id TEXT NOT NULL,
                    pool_id TEXT,
                    side TEXT NOT NULL CHECK(side IN ('BUY', 'SELL')),
                    order_type TEXT NOT NULL DEFAULT 'MARKET' CHECK(order_type IN ('MARKET', 'LIMIT')),
                    status TEXT NOT NULL CHECK(status IN ('PENDING', 'SUBMITTED', 'FILLED', 'PARTIALLY_FILLED', 'CANCELLED', 'EXPIRED', 'REJECTED')),
                    requested_quantity TEXT NOT NULL,
                    filled_quantity TEXT NOT NULL DEFAULT '0',
                    limit_price_native REAL,
                    executed_price_native REAL,
                    max_slippage_bps INTEGER NOT NULL DEFAULT 100,
                    fee_paid_native REAL NOT NULL DEFAULT 0.0,
                    simulated_gas_native REAL NOT NULL DEFAULT 0.0,
                    rejection_reason TEXT,
                    execution_assumptions_json TEXT,
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
                );
                """,
                """
                CREATE TABLE IF NOT EXISTS paper_fills (
                    fill_id TEXT PRIMARY KEY,
                    order_id TEXT NOT NULL,
                    account_id TEXT NOT NULL,
                    chain_id TEXT NOT NULL,
                    asset_id TEXT NOT NULL,
                    side TEXT NOT NULL,
                    fill_quantity TEXT NOT NULL,
                    fill_price_native REAL NOT NULL,
                    fee_native REAL NOT NULL DEFAULT 0.0,
                    slippage_bps INTEGER NOT NULL DEFAULT 0,
                    market_data_snapshot_id TEXT,
                    occurred_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
                );
                """,
                """
                CREATE TABLE IF NOT EXISTS paper_positions_v2 (
                    position_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    chain_id TEXT NOT NULL,
                    asset_address TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL DEFAULT 'LONG',
                    token_amount_raw TEXT NOT NULL,
                    cost_basis_native REAL NOT NULL,
                    entry_price_native REAL NOT NULL,
                    current_price_native REAL NOT NULL,
                    unrealized_pnl_native REAL NOT NULL DEFAULT 0.0,
                    realized_pnl_native REAL NOT NULL DEFAULT 0.0,
                    opened_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                    UNIQUE(account_id, chain_id, asset_address)
                );
                """,
                """
                CREATE TABLE IF NOT EXISTS paper_risk_limits (
                    id TEXT PRIMARY KEY,
                    chain_id TEXT,
                    max_position_size_usd REAL NOT NULL DEFAULT 100000.0,
                    max_order_value_usd REAL NOT NULL DEFAULT 50000.0,
                    max_portfolio_exposure_usd REAL NOT NULL DEFAULT 500000.0,
                    max_daily_loss_usd REAL NOT NULL DEFAULT 20000.0,
                    max_drawdown_pct REAL NOT NULL DEFAULT 25.0,
                    max_concurrent_positions INTEGER NOT NULL DEFAULT 20,
                    max_slippage_bps INTEGER NOT NULL DEFAULT 500,
                    stale_data_threshold_seconds INTEGER NOT NULL DEFAULT 300,
                    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
                );
                """,
                """
                CREATE TABLE IF NOT EXISTS paper_portfolio_valuations (
                    id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    chain_id TEXT NOT NULL,
                    cash_balance_usd REAL NOT NULL,
                    positions_value_usd REAL NOT NULL,
                    total_equity_usd REAL NOT NULL,
                    unrealized_pnl_usd REAL NOT NULL,
                    realized_pnl_usd REAL NOT NULL,
                    snapshot_time TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
                );
                """,
                """
                CREATE TABLE IF NOT EXISTS paper_reconciliation_records (
                    id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    chain_id TEXT NOT NULL,
                    opening_balance_usd REAL NOT NULL,
                    closing_balance_usd REAL NOT NULL,
                    calculated_equity_usd REAL NOT NULL,
                    discrepancy_usd REAL NOT NULL DEFAULT 0.0,
                    is_balanced INTEGER NOT NULL DEFAULT 1,
                    details_json TEXT,
                    reconciled_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
                );
                """,
                """
                CREATE TABLE IF NOT EXISTS paper_audit_log (
                    id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    account_id TEXT,
                    chain_id TEXT,
                    details_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
                );
                """,
                "CREATE INDEX IF NOT EXISTS idx_paper_balances_acc ON paper_account_balances(account_id, chain_id);",
                "CREATE INDEX IF NOT EXISTS idx_paper_orders_acc ON paper_orders_v2(account_id, chain_id, status);",
                "CREATE INDEX IF NOT EXISTS idx_paper_fills_order ON paper_fills(order_id);",
                "CREATE INDEX IF NOT EXISTS idx_paper_pos_acc ON paper_positions_v2(account_id, chain_id);",
                "CREATE INDEX IF NOT EXISTS idx_paper_valuations_acc ON paper_portfolio_valuations(account_id, snapshot_time);",
                "CREATE INDEX IF NOT EXISTS idx_paper_audit_type ON paper_audit_log(event_type);",
                """
                INSERT OR IGNORE INTO paper_portfolios (portfolio_id, name, description, is_active, created_at, updated_at)
                VALUES ('portfolio_default', 'Primary Multi-Chain Paper Portfolio', 'Local-first paper trading desk for simulation across 12 chains', 1, datetime('now'), datetime('now'));
                """,
                """
                INSERT OR IGNORE INTO paper_risk_limits (id, chain_id, max_position_size_usd, max_order_value_usd, max_portfolio_exposure_usd, max_daily_loss_usd, max_drawdown_pct, max_concurrent_positions, max_slippage_bps, stale_data_threshold_seconds, updated_at)
                VALUES ('risk_global_default', NULL, 100000.0, 50000.0, 500000.0, 20000.0, 25.0, 20, 500, 300, datetime('now'));
                """
            ]
        },
        {
            "version": 8,
            "name": "008_strategy_sdk_signals_and_audit",
            "statements": [
                """
                CREATE TABLE IF NOT EXISTS strategy_definitions (
                    strategy_id TEXT PRIMARY KEY,
                    version TEXT NOT NULL,
                    name TEXT NOT NULL,
                    description TEXT,
                    author TEXT NOT NULL DEFAULT 'SYSTEM',
                    supported_chains_json TEXT NOT NULL,
                    supported_asset_types_json TEXT NOT NULL,
                    required_market_fields_json TEXT NOT NULL,
                    required_forensic_fields_json TEXT NOT NULL,
                    config_schema_json TEXT NOT NULL,
                    lookback_window_seconds INTEGER NOT NULL DEFAULT 3600,
                    supported_modes_json TEXT NOT NULL,
                    timeout_seconds REAL NOT NULL DEFAULT 5.0,
                    lifecycle_status TEXT NOT NULL CHECK(lifecycle_status IN ('DISCOVERED', 'VALIDATED', 'ENABLED', 'RUNNING', 'PAUSED', 'DISABLED', 'RETIRED')),
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
                );
                """,
                """
                CREATE TABLE IF NOT EXISTS strategy_configurations (
                    config_id TEXT PRIMARY KEY,
                    strategy_id TEXT NOT NULL,
                    version TEXT NOT NULL,
                    chain_id TEXT,
                    parameters_json TEXT NOT NULL,
                    risk_preferences_json TEXT NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                    FOREIGN KEY(strategy_id) REFERENCES strategy_definitions(strategy_id) ON DELETE CASCADE
                );
                """,
                """
                CREATE TABLE IF NOT EXISTS strategy_runs (
                    run_id TEXT PRIMARY KEY,
                    strategy_id TEXT NOT NULL,
                    strategy_version TEXT NOT NULL,
                    chain_id TEXT NOT NULL,
                    account_id TEXT,
                    mode TEXT NOT NULL CHECK(mode IN ('RESEARCH', 'BACKTEST', 'PAPER')),
                    input_snapshot_id TEXT,
                    input_dataset_hash TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('STARTED', 'COMPLETED', 'FAILED', 'TIMED_OUT')),
                    error_message TEXT,
                    execution_duration_ms REAL NOT NULL DEFAULT 0.0,
                    started_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                    completed_at TEXT
                );
                """,
                """
                CREATE TABLE IF NOT EXISTS strategy_signals (
                    signal_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    strategy_id TEXT NOT NULL,
                    strategy_version TEXT NOT NULL,
                    chain_id TEXT NOT NULL,
                    asset_id TEXT NOT NULL,
                    pool_id TEXT,
                    action TEXT NOT NULL CHECK(action IN ('BUY', 'SELL', 'HOLD', 'INVALID')),
                    confidence REAL NOT NULL,
                    entry_reference_price_native REAL,
                    entry_reference_price_usd REAL,
                    price_source TEXT NOT NULL,
                    suggested_size_raw TEXT,
                    suggested_size_human REAL,
                    stop_loss_pct REAL,
                    take_profit_pct REAL,
                    reasons_json TEXT NOT NULL,
                    provenance_json TEXT NOT NULL,
                    input_timestamp TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
                );
                """,
                """
                CREATE TABLE IF NOT EXISTS strategy_audit_log (
                    audit_id TEXT PRIMARY KEY,
                    strategy_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    details_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
                );
                """,
                "CREATE INDEX IF NOT EXISTS idx_strat_def_status ON strategy_definitions(lifecycle_status);",
                "CREATE INDEX IF NOT EXISTS idx_strat_conf_strat ON strategy_configurations(strategy_id, chain_id);",
                "CREATE INDEX IF NOT EXISTS idx_strat_runs_strat ON strategy_runs(strategy_id, started_at);",
                "CREATE INDEX IF NOT EXISTS idx_strat_signals_asset ON strategy_signals(chain_id, asset_id, action);",
                "CREATE INDEX IF NOT EXISTS idx_strat_signals_run ON strategy_signals(run_id);",
                "CREATE INDEX IF NOT EXISTS idx_strat_audit_strat ON strategy_audit_log(strategy_id, event_type);"
            ]
        },
        {
            "version": 9,
            "name": "009_strategy_proposals_and_replay_context",
            "statements": [
                """
                CREATE TABLE IF NOT EXISTS strategy_proposals (
                    proposal_id TEXT PRIMARY KEY,
                    signal_id TEXT NOT NULL,
                    account_id TEXT NOT NULL,
                    strategy_id TEXT NOT NULL,
                    chain_id TEXT NOT NULL,
                    asset_id TEXT NOT NULL,
                    side TEXT NOT NULL CHECK(side IN ('BUY', 'SELL')),
                    suggested_size_raw TEXT,
                    suggested_size_human TEXT,
                    estimated_usd_value TEXT NOT NULL,
                    core_risk_validated INTEGER NOT NULL DEFAULT 0,
                    rejection_reason TEXT,
                    execution_status TEXT NOT NULL DEFAULT 'PROPOSED_ONLY' CHECK(execution_status IN ('PROPOSED_ONLY', 'CONSUMED', 'REJECTED', 'EXPIRED')),
                    requires_core_authorization INTEGER NOT NULL DEFAULT 1,
                    provenance_json TEXT NOT NULL DEFAULT '{}',
                    expires_at TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                    FOREIGN KEY(signal_id) REFERENCES strategy_signals(signal_id),
                    UNIQUE(signal_id, account_id)
                );
                """,
                "CREATE INDEX IF NOT EXISTS idx_strat_props_signal ON strategy_proposals(signal_id, account_id);",
                "CREATE INDEX IF NOT EXISTS idx_strat_props_account ON strategy_proposals(account_id, execution_status);",
                "ALTER TABLE strategy_runs ADD COLUMN input_context_json TEXT;"
            ]
        },
        {
            "version": 10,
            "name": "010_atomic_paper_execution_and_decimal_provenance",
            "statements": [
                """
                CREATE TABLE IF NOT EXISTS token_decimal_verifications (
                    verification_id TEXT PRIMARY KEY,
                    chain_id TEXT NOT NULL,
                    asset_address TEXT NOT NULL,
                    decimals INTEGER NOT NULL CHECK(decimals >= 0 AND decimals <= 36),
                    symbol TEXT,
                    verification_source TEXT NOT NULL,
                    verification_method TEXT NOT NULL,
                    raw_metadata_hash TEXT NOT NULL,
                    raw_metadata_json TEXT NOT NULL DEFAULT '{}',
                    status TEXT NOT NULL DEFAULT 'VERIFIED' CHECK(status IN ('VERIFIED', 'UNVERIFIED', 'REVOKED')),
                    verified_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                    UNIQUE(chain_id, asset_address)
                );
                """,
                "CREATE INDEX IF NOT EXISTS idx_tok_dec_ver ON token_decimal_verifications(chain_id, asset_address, status);",
                "ALTER TABLE paper_orders_v2 ADD COLUMN origin TEXT DEFAULT 'MANUAL';",
                "ALTER TABLE paper_orders_v2 ADD COLUMN proposal_id TEXT;",
                "ALTER TABLE paper_orders_v2 ADD COLUMN authorization_hash TEXT;",
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_paper_orders_proposal_uniq ON paper_orders_v2(proposal_id) WHERE proposal_id IS NOT NULL;",
                "CREATE INDEX IF NOT EXISTS idx_paper_orders_origin ON paper_orders_v2(origin);",
                "ALTER TABLE strategy_proposals ADD COLUMN order_id TEXT;",
                "ALTER TABLE strategy_proposals ADD COLUMN consumed_at TEXT;",
                "ALTER TABLE strategy_proposals ADD COLUMN authorization_hash TEXT;",
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_strat_proposals_order_uniq ON strategy_proposals(order_id) WHERE order_id IS NOT NULL;",
                "ALTER TABLE strategy_runs ADD COLUMN config_snapshot_json TEXT;",
                "ALTER TABLE strategy_runs ADD COLUMN config_hash TEXT;",
                "ALTER TABLE strategy_runs ADD COLUMN sdk_version TEXT;"
            ]
        },
        {
            "version": 11,
            "name": "011_remediate_decimal_provenance_and_raw_metadata",
            "statements": [
                "ALTER TABLE token_decimal_verifications ADD COLUMN raw_metadata_json TEXT DEFAULT '{}';",
                "DELETE FROM token_decimal_verifications WHERE raw_metadata_hash = 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855' OR asset_address = '0x1234567890abcdef1234567890abcdef12345678';",
                "UPDATE discovered_tokens SET decimals = 5 WHERE address = 'DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263' OR symbol = 'BONK';",
                """
                INSERT OR REPLACE INTO token_decimal_verifications (
                    verification_id, chain_id, asset_address, decimals, symbol, verification_source,
                    verification_method, raw_metadata_hash, raw_metadata_json, status, verified_at, created_at
                ) VALUES
                ('ver-bonk-sol', 'solana:mainnet', 'DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263', 5, 'BONK', 'OFFICIAL_SPL_MINT', 'ONCHAIN_RPC_MINT_DATA', '9a964b0276f8b1783bb9d5347e090bd69bd34e2ee38f791d33fb2e8c06794f7d', '{"authority": "SPL_MINT_DATA", "chain_id": "solana:mainnet", "decimals": 5, "mint": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263", "program": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA", "source_url": "https://solana.com", "symbol": "BONK"}', 'VERIFIED', datetime('now'), datetime('now')),
                ('ver-usdc-sol', 'solana:mainnet', 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v', 6, 'USDC', 'OFFICIAL_SPL_MINT', 'ONCHAIN_RPC_MINT_DATA', 'dbc43a3ca812bccc4a16a3d6e5d86da7315593f5548b07bd64cdce669a0294f0', '{"authority": "CENTRE_CIRCLE_OFFICIAL", "chain_id": "solana:mainnet", "decimals": 6, "mint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", "program": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA", "symbol": "USDC"}', 'VERIFIED', datetime('now'), datetime('now')),
                ('ver-weth-eth', 'eip155:1', '0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2', 18, 'WETH', 'OFFICIAL_EIP20_REGISTRY', 'ONCHAIN_RPC_STATIC_CALL', '80205fcdce987f3f1e5b3992d69dbab8f51c63c52f9bb180a721aab88be91f17', '{"address": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2", "authority": "CANONICAL_WRAPPER", "chain_id": "eip155:1", "decimals": 18, "standard": "ERC20", "symbol": "WETH"}', 'VERIFIED', datetime('now'), datetime('now')),
                ('ver-usdc-eth', 'eip155:1', '0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48', 6, 'USDC', 'OFFICIAL_EIP20_REGISTRY', 'ONCHAIN_RPC_STATIC_CALL', '9d625afb3ea85ae1e2c24877ad465dfdfb6efb93838be7282825fc99ab05a97b', '{"address": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", "authority": "CENTRE_CIRCLE_OFFICIAL", "chain_id": "eip155:1", "decimals": 6, "standard": "ERC20", "symbol": "USDC"}', 'VERIFIED', datetime('now'), datetime('now')),
                ('ver-wbtc-eth', 'eip155:1', '0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599', 8, 'WBTC', 'OFFICIAL_EIP20_REGISTRY', 'ONCHAIN_RPC_STATIC_CALL', 'cf064311761d08ff5d4a4fa9b841716d56463de72f9cd8d05d2f804bbde95f4f', '{"address": "0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599", "authority": "BITGO_CANONICAL", "chain_id": "eip155:1", "decimals": 8, "standard": "ERC20", "symbol": "WBTC"}', 'VERIFIED', datetime('now'), datetime('now')),
                ('ver-weth-base', 'eip155:8453', '0x4200000000000000000000000000000000000006', 18, 'WETH', 'OFFICIAL_EIP20_REGISTRY', 'ONCHAIN_RPC_STATIC_CALL', '5a7b84163059e38993d499388bb3e85b98525c1f65db900cba35f5ea8fce168a', '{"address": "0x4200000000000000000000000000000000000006", "authority": "BASE_SYSTEM_WRAPPER", "chain_id": "eip155:8453", "decimals": 18, "standard": "ERC20", "symbol": "WETH"}', 'VERIFIED', datetime('now'), datetime('now')),
                ('ver-degen-base', 'eip155:8453', '0x4ed4E862860be51a745742C749e49753e1d528b8', 18, 'DEGEN', 'OFFICIAL_EIP20_REGISTRY', 'ONCHAIN_RPC_STATIC_CALL', 'b2754759bda37327432391e73a506ff7224dd8190421bb7389eabeb9f3a53ae8', '{"address": "0x4ed4E862860be51a745742C749e49753e1d528b8", "authority": "DEGEN_DAO_CANONICAL", "chain_id": "eip155:8453", "decimals": 18, "standard": "ERC20", "symbol": "DEGEN"}', 'VERIFIED', datetime('now'), datetime('now'));
                """
            ]
        },
        {
            "version": 12,
            "name": "012_canonical_solana_caip2_identity",
            "statements": [
                # 1. paper_accounts (UNIQUE(chain_id))
                "DELETE FROM paper_accounts WHERE chain_id IN ('solana:mainnet', 'solana', 'solana:mainnet-beta') AND EXISTS (SELECT 1 FROM paper_accounts WHERE chain_id = 'solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp');",
                "UPDATE paper_accounts SET chain_id = 'solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp' WHERE chain_id IN ('solana:mainnet', 'solana', 'solana:mainnet-beta');",
                # 2. paper_account_balances
                "UPDATE paper_account_balances SET chain_id = 'solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp' WHERE chain_id IN ('solana:mainnet', 'solana', 'solana:mainnet-beta');",
                # 3. paper_positions_v2
                "UPDATE paper_positions_v2 SET chain_id = 'solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp' WHERE chain_id IN ('solana:mainnet', 'solana', 'solana:mainnet-beta');",
                # 4. paper_orders_v2
                "UPDATE paper_orders_v2 SET chain_id = 'solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp' WHERE chain_id IN ('solana:mainnet', 'solana', 'solana:mainnet-beta');",
                # 5. strategy_proposals
                "UPDATE strategy_proposals SET chain_id = 'solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp' WHERE chain_id IN ('solana:mainnet', 'solana', 'solana:mainnet-beta');",
                # 6. strategy_signals
                "UPDATE strategy_signals SET chain_id = 'solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp' WHERE chain_id IN ('solana:mainnet', 'solana', 'solana:mainnet-beta');",
                # 7. strategy_configurations
                "DELETE FROM strategy_configurations WHERE chain_id IN ('solana:mainnet', 'solana', 'solana:mainnet-beta') AND strategy_id IN (SELECT strategy_id FROM strategy_configurations WHERE chain_id = 'solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp');",
                "UPDATE strategy_configurations SET chain_id = 'solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp' WHERE chain_id IN ('solana:mainnet', 'solana', 'solana:mainnet-beta');",
                # 8. token_decimal_verifications (UNIQUE(chain_id, asset_address))
                "DELETE FROM token_decimal_verifications WHERE chain_id IN ('solana:mainnet', 'solana', 'solana:mainnet-beta') AND asset_address IN (SELECT asset_address FROM token_decimal_verifications WHERE chain_id = 'solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp');",
                "UPDATE token_decimal_verifications SET chain_id = 'solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp' WHERE chain_id IN ('solana:mainnet', 'solana', 'solana:mainnet-beta');",
                # 9. discovered_tokens (UNIQUE(chain_id, address))
                "DELETE FROM discovered_tokens WHERE chain_id IN ('solana:mainnet', 'solana', 'solana:mainnet-beta') AND address IN (SELECT address FROM discovered_tokens WHERE chain_id = 'solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp');",
                "UPDATE discovered_tokens SET chain_id = 'solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp' WHERE chain_id IN ('solana:mainnet', 'solana', 'solana:mainnet-beta');",
                # 10. discovered_pools (UNIQUE(chain_id, pool_address))
                "DELETE FROM discovered_pools WHERE chain_id IN ('solana:mainnet', 'solana', 'solana:mainnet-beta') AND pool_address IN (SELECT pool_address FROM discovered_pools WHERE chain_id = 'solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp');",
                "UPDATE discovered_pools SET chain_id = 'solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp' WHERE chain_id IN ('solana:mainnet', 'solana', 'solana:mainnet-beta');",
                # 11. market_data_snapshots
                "UPDATE market_data_snapshots SET chain_id = 'solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp' WHERE chain_id IN ('solana:mainnet', 'solana', 'solana:mainnet-beta');",
                # 12. ingestion_checkpoints (PRIMARY KEY(chain_id, protocol))
                "DELETE FROM ingestion_checkpoints WHERE chain_id IN ('solana:mainnet', 'solana', 'solana:mainnet-beta') AND protocol IN (SELECT protocol FROM ingestion_checkpoints WHERE chain_id = 'solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp');",
                "UPDATE ingestion_checkpoints SET chain_id = 'solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp' WHERE chain_id IN ('solana:mainnet', 'solana', 'solana:mainnet-beta');"
            ]
        },
        {
            "version": 13,
            "name": "013_reconcile_canonical_solana_caip2_data_safety",
            "statements": [
                "UPDATE strategy_runs SET chain_id = 'solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp' WHERE chain_id IN ('solana:mainnet', 'solana', 'solana:mainnet-beta');",
                "UPDATE reorg_invalidation_events SET chain_id = 'solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp' WHERE chain_id IN ('solana:mainnet', 'solana', 'solana:mainnet-beta');",
                "UPDATE paper_risk_limits SET chain_id = 'solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp' WHERE chain_id IN ('solana:mainnet', 'solana', 'solana:mainnet-beta');",
                "UPDATE provider_usage_records SET chain_id = 'solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp' WHERE chain_id IN ('solana:mainnet', 'solana', 'solana:mainnet-beta');",
                "UPDATE provider_reservations SET chain_id = 'solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp' WHERE chain_id IN ('solana:mainnet', 'solana', 'solana:mainnet-beta');",
                "UPDATE token_decimal_verifications SET status = 'UNVERIFIED', verification_source = 'STATIC_SEED', verification_method = 'STATIC_MIGRATION_SEED' WHERE asset_address = 'DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263' OR asset_address = '0x1234567890abcdef1234567890abcdef12345678';",
                "UPDATE discovered_tokens SET is_verified = 0 WHERE address = 'DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263' OR address = '0x1234567890abcdef1234567890abcdef12345678';"
            ],
            "handler": "_reconcile_canonical_solana_caip2_data_safety"
        },
        {
            "version": 14,
            "name": "014_exact_financial_decimal_storage",
            "statements": [
                "SELECT 1;"
            ],
            "handler": "_migrate_exact_financial_decimal_storage"
        },
        {
            "version": 15,
            "name": "015_collector_nonce_replay_protection",
            "statements": [
                """
                CREATE TABLE IF NOT EXISTS collector_evidence_nonces (
                    nonce TEXT PRIMARY KEY,
                    collector_id TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    expires_at REAL NOT NULL,
                    created_at TEXT NOT NULL
                );
                """,
                """
                CREATE INDEX IF NOT EXISTS idx_collector_nonces_exp ON collector_evidence_nonces(expires_at);
                """
            ]
        },
        {
            "version": 16,
            "name": "016_exact_orders_and_fills_decimal_storage",
            "statements": [
                "SELECT 1;"
            ],
            "handler": "_migrate_orders_and_fills_exact_decimal_storage"
        },
        {
            "version": 17,
            "name": "017_provider_reliability_and_decimal_ttl",
            "statements": [
                "SELECT 1;"
            ],
            "handler": "_migrate_provider_reliability_and_decimal_ttl"
        }
    ]

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            self.db_path = settings.get_canonical_sqlite_path()
        else:
            self.db_path = db_path

        if self.db_path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)

    def _get_connection(self) -> sqlite3.Connection:
        if self.db_path == ":memory:":
            conn = sqlite3.connect("file:memecoin_migrator_mem?mode=memory&cache=shared", uri=True, timeout=30.0, isolation_level=None)
        else:
            from app.core.database import assert_test_db_isolated
            assert_test_db_isolated(self.db_path)
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
        # Ensure ledger_lines schema has modern account_code column
        try:
            cur = conn.execute("PRAGMA table_info(ledger_lines);")
            cols = [r["name"] for r in cur.fetchall()]
            if cols and "account_code" not in cols:
                conn.execute("DROP TABLE IF EXISTS ledger_lines;")
                conn.execute("""
                    CREATE TABLE ledger_lines (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        journal_id TEXT NOT NULL REFERENCES ledger_journals(id) ON DELETE RESTRICT,
                        account_code TEXT NOT NULL,
                        wallet_address TEXT NOT NULL,
                        mint_address TEXT NOT NULL,
                        debit_amount_raw TEXT NOT NULL,
                        credit_amount_raw TEXT NOT NULL,
                        usd_rate TEXT NOT NULL,
                        debit_usd_units INTEGER NOT NULL DEFAULT 0,
                        credit_usd_units INTEGER NOT NULL DEFAULT 0,
                        CHECK (debit_usd_units >= 0 AND credit_usd_units >= 0),
                        CHECK (CAST(debit_amount_raw AS NUMERIC) >= 0 AND CAST(credit_amount_raw AS NUMERIC) >= 0)
                    );
                """)
        except Exception:
            pass

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
                    handler_name = migration.get("handler")
                    if handler_name:
                        handler_fn = getattr(self, handler_name, None)
                        if callable(handler_fn):
                            handler_fn(conn)

                    for stmt in statements:
                        clean_stmt = stmt.strip()
                        if clean_stmt:
                            try:
                                conn.execute(clean_stmt)
                            except sqlite3.OperationalError as oe:
                                if "duplicate column name" in str(oe).lower():
                                    pass
                                else:
                                    raise

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
            "execution_intent_outbox", "ledger_journals", "ledger_lines",
            "chain_registries", "system_audit_log",
            "providers", "provider_credentials", "chain_rpc_endpoints",
            "provider_usage_records", "provider_fallback_rules", "provider_audit_log",
            "provider_reservations",
            "discovered_tokens", "discovered_pools", "market_data_snapshots",
            "ingestion_checkpoints", "reorg_invalidation_events",
            "paper_portfolios", "paper_account_balances", "paper_orders_v2",
            "paper_fills", "paper_positions_v2", "paper_risk_limits",
            "paper_portfolio_valuations", "paper_reconciliation_records", "paper_audit_log",
            "strategy_definitions", "strategy_configurations", "strategy_runs",
            "strategy_signals", "strategy_audit_log", "strategy_proposals",
            "token_decimal_verifications", "collector_evidence_nonces",
            "token_decimal_verification_history", "candidate_research_scores"
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

    def _reconcile_canonical_solana_caip2_data_safety(self, conn: sqlite3.Connection):
        """
        Migration 13 Handler: Safe preflight conflict detection and deterministic reconciliation.
        Enforces atomic migration boundaries: fails closed before any data mutation if unresolvable
        collisions are detected between legacy and canonical records.
        """
        legacy_chains = ("solana:mainnet", "solana", "solana:mainnet-beta")
        canonical_chain = "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"

        # ---------------------------------------------------------------------
        # 1. Preflight Conflict Detection
        # ---------------------------------------------------------------------

        # 1a. paper_accounts conflict detection
        cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='paper_accounts';")
        if cur.fetchone():
            leg_accs = conn.execute("SELECT * FROM paper_accounts WHERE chain_id IN (?, ?, ?);", legacy_chains).fetchall()
            can_accs = conn.execute("SELECT * FROM paper_accounts WHERE chain_id = ?;", (canonical_chain,)).fetchall()
            if leg_accs and can_accs:
                for l_acc in leg_accs:
                    for c_acc in can_accs:
                        if l_acc["account_id"] != c_acc["account_id"]:
                            l_bal = float(l_acc["current_balance_usd"])
                            c_bal = float(c_acc["current_balance_usd"])
                            # Check if both have active orders
                            o_cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='paper_orders_v2';")
                            if o_cur.fetchone():
                                l_orders = conn.execute("SELECT COUNT(*) FROM paper_orders_v2 WHERE account_id = ?;", (l_acc["account_id"],)).fetchone()[0]
                                c_orders = conn.execute("SELECT COUNT(*) FROM paper_orders_v2 WHERE account_id = ?;", (c_acc["account_id"],)).fetchone()[0]
                            else:
                                l_orders, c_orders = 0, 0
                            if abs(l_bal - c_bal) > 0.01 and (l_bal > 0.01 or c_bal > 0.01):
                                raise RuntimeError(
                                    f"Preflight conflict in paper_accounts: legacy account {l_acc['account_id']} "
                                    f"(balance ${l_bal:.2f}, {l_orders} orders) conflicts with canonical account "
                                    f"{c_acc['account_id']} (balance ${c_bal:.2f}, {c_orders} orders). Cannot safely merge."
                                )

        # 1b. paper_positions_v2 conflict detection
        cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='paper_positions_v2';")
        if cur.fetchone():
            dup_cur = conn.execute("""
                SELECT l.position_id, c.position_id, l.token_amount_raw, c.token_amount_raw, l.account_id, c.account_id
                FROM paper_positions_v2 l
                JOIN paper_positions_v2 c ON l.asset_address = c.asset_address
                WHERE l.chain_id IN (?, ?, ?) AND c.chain_id = ?
                  AND (l.account_id = c.account_id OR (l.account_id IN (SELECT account_id FROM paper_accounts WHERE chain_id IN (?, ?, ?)) AND c.account_id IN (SELECT account_id FROM paper_accounts WHERE chain_id = ?)));
            """, (*legacy_chains, canonical_chain, *legacy_chains, canonical_chain))
            for l_pos, c_pos, l_amt, c_amt, l_aid, c_aid in dup_cur.fetchall():
                if str(l_amt) != str(c_amt):
                    raise RuntimeError(
                        f"Preflight conflict in paper_positions_v2: conflicting position quantities {l_amt} vs {c_amt}."
                    )

        # 1c. strategy_configurations conflict detection
        cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='strategy_configurations';")
        if cur.fetchone():
            dup_cfg = conn.execute("""
                SELECT l.strategy_id, l.parameters_json, c.parameters_json
                FROM strategy_configurations l
                JOIN strategy_configurations c ON l.strategy_id = c.strategy_id
                WHERE l.chain_id IN (?, ?, ?) AND c.chain_id = ?;
            """, (*legacy_chains, canonical_chain)).fetchall()
            for sid, l_params, c_params in dup_cfg:
                if l_params != c_params:
                    raise RuntimeError(
                        f"Preflight conflict in strategy_configurations for '{sid}': conflicting parameters {l_params} vs {c_params}."
                    )

        # 1d. token_decimal_verifications conflict detection
        cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='token_decimal_verifications';")
        if cur.fetchone():
            dup_ver = conn.execute("""
                SELECT l.verification_id, c.verification_id, l.asset_address, l.decimals, c.decimals
                FROM token_decimal_verifications l
                JOIN token_decimal_verifications c ON (l.asset_address = c.asset_address OR LOWER(l.asset_address) = LOWER(c.asset_address))
                WHERE l.chain_id IN (?, ?, ?) AND c.chain_id = ?;
            """, (*legacy_chains, canonical_chain)).fetchall()
            for l_id, c_id, addr, l_dec, c_dec in dup_ver:
                if int(l_dec) != int(c_dec):
                    raise RuntimeError(
                        f"Preflight conflict in token_decimal_verifications: conflicting decimals for {addr} ({l_dec} vs {c_dec})."
                    )

        # 1e. discovered_tokens conflict detection
        cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='discovered_tokens';")
        if cur.fetchone():
            dup_tok = conn.execute("""
                SELECT l.id, c.id, l.address, l.decimals, c.decimals
                FROM discovered_tokens l
                JOIN discovered_tokens c ON (l.address = c.address OR LOWER(l.address) = LOWER(c.address))
                WHERE l.chain_id IN (?, ?, ?) AND c.chain_id = ?;
            """, (*legacy_chains, canonical_chain)).fetchall()
            for l_id, c_id, addr, l_dec, c_dec in dup_tok:
                if l_dec is not None and c_dec is not None and int(l_dec) != int(c_dec):
                    raise RuntimeError(
                        f"Preflight conflict in discovered_tokens: conflicting decimals for {addr} ({l_dec} vs {c_dec})."
                    )

        # 1f. paper_risk_limits conflict detection
        cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='paper_risk_limits';")
        if cur.fetchone():
            leg_rl = conn.execute("SELECT * FROM paper_risk_limits WHERE chain_id IN (?, ?, ?);", legacy_chains).fetchall()
            can_rl = conn.execute("SELECT * FROM paper_risk_limits WHERE chain_id = ?;", (canonical_chain,)).fetchall()
            if leg_rl and can_rl:
                for l in leg_rl:
                    for c in can_rl:
                        if l["max_order_value_usd"] != c["max_order_value_usd"]:
                            raise RuntimeError("Preflight conflict in paper_risk_limits: conflicting order value limits.")

        # ---------------------------------------------------------------------
        # 2. Deterministic Reconciliation & Foreign Key Integrity
        # ---------------------------------------------------------------------

        # 2a. Reconcile paper_accounts
        cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='paper_accounts';")
        if cur.fetchone():
            leg_accs = conn.execute("SELECT * FROM paper_accounts WHERE chain_id IN (?, ?, ?);", legacy_chains).fetchall()
            can_accs = conn.execute("SELECT * FROM paper_accounts WHERE chain_id = ?;", (canonical_chain,)).fetchall()
            if len(leg_accs) > 1:
                raise RuntimeError(
                    f"Preflight conflict in paper_accounts: cannot unambiguously reconcile "
                    f"{len(leg_accs)} legacy accounts without explicit account linkage."
                )
            if len(can_accs) > 1:
                raise RuntimeError(
                    f"Preflight conflict in paper_accounts: cannot unambiguously reconcile "
                    f"{len(can_accs)} canonical accounts without explicit account linkage."
                )
            if leg_accs and can_accs:
                l_acc = leg_accs[0]
                c_acc = can_accs[0]
                # Re-point balances with UNIQUE constraint handling
                b_check = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='paper_account_balances';")
                if b_check.fetchone():
                    cur_b = conn.execute("SELECT * FROM paper_account_balances WHERE account_id = ?;", (l_acc["account_id"],)).fetchall()
                    for b_row in cur_b:
                        existing_c_bal = conn.execute(
                            "SELECT * FROM paper_account_balances WHERE account_id = ? AND asset_id = ?;",
                            (c_acc["account_id"], b_row["asset_id"])
                        ).fetchone()
                        if existing_c_bal:
                            c_free = parse_strict_raw_unit(existing_c_bal["free_amount_raw"], "free_amount_raw")
                            l_free = parse_strict_raw_unit(b_row["free_amount_raw"], "free_amount_raw")
                            merged_free = c_free + l_free

                            c_locked = parse_strict_raw_unit(existing_c_bal["locked_amount_raw"], "locked_amount_raw")
                            l_locked = parse_strict_raw_unit(b_row["locked_amount_raw"], "locked_amount_raw")
                            merged_locked = c_locked + l_locked

                            conn.execute(
                                "UPDATE paper_account_balances SET free_amount_raw = ?, locked_amount_raw = ? WHERE id = ?;",
                                (str(merged_free), str(merged_locked), existing_c_bal["id"])
                            )
                            conn.execute("DELETE FROM paper_account_balances WHERE id = ?;", (b_row["id"],))
                        else:
                            conn.execute("UPDATE paper_account_balances SET account_id = ? WHERE id = ?;", (c_acc["account_id"], b_row["id"]))

                # Re-point positions with UNIQUE constraint handling
                p_check = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='paper_positions_v2';")
                if p_check.fetchone():
                    cur_p = conn.execute("SELECT * FROM paper_positions_v2 WHERE account_id = ?;", (l_acc["account_id"],)).fetchall()
                    for p_row in cur_p:
                        existing_c_pos = conn.execute(
                            "SELECT * FROM paper_positions_v2 WHERE account_id = ? AND asset_address = ?;",
                            (c_acc["account_id"], p_row["asset_address"])
                        ).fetchone()
                        if existing_c_pos:
                            amt_c_int = parse_strict_raw_unit(existing_c_pos["token_amount_raw"], "token_amount_raw")
                            amt_l_int = parse_strict_raw_unit(p_row["token_amount_raw"], "token_amount_raw")
                            merged_amt_int = amt_c_int + amt_l_int

                            amt_c = Decimal(str(amt_c_int))
                            amt_l = Decimal(str(amt_l_int))
                            merged_amt = Decimal(str(merged_amt_int))

                            cost_c = Decimal(str(existing_c_pos["cost_basis_native"] or "0"))
                            cost_l = Decimal(str(p_row["cost_basis_native"] or "0"))
                            merged_cost = cost_c + cost_l

                            if merged_amt > Decimal("0"):
                                entry_c = Decimal(str(existing_c_pos["entry_price_native"] or "0"))
                                entry_l = Decimal(str(p_row["entry_price_native"] or "0"))
                                merged_entry = ((amt_c * entry_c) + (amt_l * entry_l)) / merged_amt
                            else:
                                merged_entry = Decimal(str(existing_c_pos["entry_price_native"] or "0"))

                            pnl_u_c = Decimal(str(existing_c_pos["unrealized_pnl_native"] or "0"))
                            pnl_u_l = Decimal(str(p_row["unrealized_pnl_native"] or "0"))
                            merged_unrealized_pnl = pnl_u_c + pnl_u_l

                            pnl_r_c = Decimal(str(existing_c_pos["realized_pnl_native"] or "0"))
                            pnl_r_l = Decimal(str(p_row["realized_pnl_native"] or "0"))
                            merged_realized_pnl = pnl_r_c + pnl_r_l

                            latest_updated = max(
                                str(existing_c_pos["updated_at"] or ""),
                                str(p_row["updated_at"] or ""),
                                datetime.now(timezone.utc).isoformat()
                            )

                            conn.execute(
                                """
                                UPDATE paper_positions_v2
                                SET token_amount_raw = ?,
                                    cost_basis_native = ?,
                                    entry_price_native = ?,
                                    unrealized_pnl_native = ?,
                                    realized_pnl_native = ?,
                                    updated_at = ?
                                WHERE position_id = ?;
                                """,
                                (
                                    str(merged_amt_int),
                                    format_decimal_exact(merged_cost),
                                    format_decimal_exact(merged_entry),
                                    format_decimal_exact(merged_unrealized_pnl),
                                    format_decimal_exact(merged_realized_pnl),
                                    latest_updated,
                                    existing_c_pos["position_id"]
                                )
                            )
                            conn.execute("DELETE FROM paper_positions_v2 WHERE position_id = ?;", (p_row["position_id"],))
                        else:
                            conn.execute("UPDATE paper_positions_v2 SET account_id = ? WHERE position_id = ?;", (c_acc["account_id"], p_row["position_id"]))

                # Re-point orders & proposals
                for tbl in ("paper_orders_v2", "strategy_proposals"):
                    t_check = conn.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{tbl}';")
                    if t_check.fetchone():
                        conn.execute(f"UPDATE {tbl} SET account_id = ? WHERE account_id = ?;", (c_acc["account_id"], l_acc["account_id"]))

                conn.execute("DELETE FROM paper_accounts WHERE account_id = ?;", (l_acc["account_id"],))
            conn.execute("UPDATE paper_accounts SET chain_id = ? WHERE chain_id IN (?, ?, ?);", (canonical_chain, *legacy_chains))

        # 2c. Reconcile paper_account_balances chain_id
        cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='paper_account_balances';")
        if cur.fetchone():
            conn.execute("UPDATE paper_account_balances SET chain_id = ? WHERE chain_id IN (?, ?, ?);", (canonical_chain, *legacy_chains))

        # 2d. Reconcile paper_positions_v2
        cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='paper_positions_v2';")
        if cur.fetchone():
            dup_cur = conn.execute("""
                SELECT l.position_id, c.position_id, l.token_amount_raw, c.token_amount_raw,
                       l.cost_basis_native, c.cost_basis_native,
                       l.entry_price_native, c.entry_price_native,
                       l.unrealized_pnl_native, c.unrealized_pnl_native,
                       l.realized_pnl_native, c.realized_pnl_native
                FROM paper_positions_v2 l
                JOIN paper_positions_v2 c ON l.account_id = c.account_id AND l.asset_address = c.asset_address
                WHERE l.chain_id IN (?, ?, ?) AND c.chain_id = ?;
            """, (*legacy_chains, canonical_chain))
            for l_pos, c_pos, l_amt, c_amt, l_cost, c_cost, l_entry, c_entry, l_upnl, c_upnl, l_rpnl, c_rpnl in dup_cur.fetchall():
                amt_l_int = parse_strict_raw_unit(l_amt, "token_amount_raw")
                amt_c_int = parse_strict_raw_unit(c_amt, "token_amount_raw")
                merged_amt_int = amt_c_int + amt_l_int

                amt_l = Decimal(str(amt_l_int))
                amt_c = Decimal(str(amt_c_int))
                merged_amt = Decimal(str(merged_amt_int))

                cost_l = Decimal(str(l_cost or "0"))
                cost_c = Decimal(str(c_cost or "0"))
                merged_cost = cost_c + cost_l

                if merged_amt > Decimal("0"):
                    entry_l = Decimal(str(l_entry or "0"))
                    entry_c = Decimal(str(c_entry or "0"))
                    merged_entry = ((amt_c * entry_c) + (amt_l * entry_l)) / merged_amt
                else:
                    merged_entry = Decimal(str(c_entry or "0"))

                merged_upnl = Decimal(str(c_upnl or "0")) + Decimal(str(l_upnl or "0"))
                merged_rpnl = Decimal(str(c_rpnl or "0")) + Decimal(str(l_rpnl or "0"))

                conn.execute(
                    """
                    UPDATE paper_positions_v2
                    SET token_amount_raw = ?,
                        cost_basis_native = ?,
                        entry_price_native = ?,
                        unrealized_pnl_native = ?,
                        realized_pnl_native = ?
                    WHERE position_id = ?;
                    """,
                    (
                        str(merged_amt_int),
                        format_decimal_exact(merged_cost),
                        format_decimal_exact(merged_entry),
                        format_decimal_exact(merged_upnl),
                        format_decimal_exact(merged_rpnl),
                        c_pos
                    )
                )
                conn.execute("DELETE FROM paper_positions_v2 WHERE position_id = ?;", (l_pos,))
            conn.execute("UPDATE paper_positions_v2 SET chain_id = ? WHERE chain_id IN (?, ?, ?);", (canonical_chain, *legacy_chains))

        # 2e. Reconcile strategy_configurations
        cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='strategy_configurations';")
        if cur.fetchone():
            dup_cfg = conn.execute("""
                SELECT l.strategy_id
                FROM strategy_configurations l
                JOIN strategy_configurations c ON l.strategy_id = c.strategy_id
                WHERE l.chain_id IN (?, ?, ?) AND c.chain_id = ?;
            """, (*legacy_chains, canonical_chain)).fetchall()
            for (sid,) in dup_cfg:
                conn.execute("DELETE FROM strategy_configurations WHERE strategy_id = ? AND chain_id IN (?, ?, ?);", (sid, *legacy_chains))
            conn.execute("UPDATE strategy_configurations SET chain_id = ? WHERE chain_id IN (?, ?, ?);", (canonical_chain, *legacy_chains))

        # 2f. Reconcile token_decimal_verifications
        cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='token_decimal_verifications';")
        if cur.fetchone():
            dup_ver = conn.execute("""
                SELECT l.verification_id
                FROM token_decimal_verifications l
                JOIN token_decimal_verifications c ON (l.asset_address = c.asset_address OR LOWER(l.asset_address) = LOWER(c.asset_address))
                WHERE l.chain_id IN (?, ?, ?) AND c.chain_id = ?;
            """, (*legacy_chains, canonical_chain)).fetchall()
            for (l_id,) in dup_ver:
                conn.execute("DELETE FROM token_decimal_verifications WHERE verification_id = ?;", (l_id,))
            conn.execute("UPDATE token_decimal_verifications SET chain_id = ? WHERE chain_id IN (?, ?, ?);", (canonical_chain, *legacy_chains))

        # 2g. Reconcile discovered_tokens
        cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='discovered_tokens';")
        if cur.fetchone():
            dup_tok = conn.execute("""
                SELECT l.id, c.id, l.first_seen_at, c.first_seen_at, l.last_seen_at, c.last_seen_at
                FROM discovered_tokens l
                JOIN discovered_tokens c ON (l.address = c.address OR LOWER(l.address) = LOWER(c.address))
                WHERE l.chain_id IN (?, ?, ?) AND c.chain_id = ?;
            """, (*legacy_chains, canonical_chain)).fetchall()
            for l_id, c_id, l_fs, c_fs, l_ls, c_ls in dup_tok:
                earliest_seen = min(filter(None, [l_fs, c_fs]), default=None)
                latest_seen = max(filter(None, [l_ls, c_ls]), default=None)
                conn.execute("UPDATE discovered_tokens SET first_seen_at = ?, last_seen_at = ? WHERE id = ?;", (earliest_seen, latest_seen, c_id))
                conn.execute("DELETE FROM discovered_tokens WHERE id = ?;", (l_id,))
            conn.execute("UPDATE discovered_tokens SET chain_id = ? WHERE chain_id IN (?, ?, ?);", (canonical_chain, *legacy_chains))

        # 2h. Reconcile discovered_pools
        cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='discovered_pools';")
        if cur.fetchone():
            dup_pools = conn.execute("""
                SELECT l.id
                FROM discovered_pools l
                JOIN discovered_pools c ON (l.pool_address = c.pool_address OR LOWER(l.pool_address) = LOWER(c.pool_address))
                WHERE l.chain_id IN (?, ?, ?) AND c.chain_id = ?;
            """, (*legacy_chains, canonical_chain)).fetchall()
            for (l_id,) in dup_pools:
                conn.execute("DELETE FROM discovered_pools WHERE id = ?;", (l_id,))
            conn.execute("UPDATE discovered_pools SET chain_id = ? WHERE chain_id IN (?, ?, ?);", (canonical_chain, *legacy_chains))

        # 2i. Reconcile ingestion_checkpoints
        cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='ingestion_checkpoints';")
        if cur.fetchone():
            dup_cp = conn.execute("""
                SELECT l.protocol, l.source_type, l.last_block_or_slot, c.last_block_or_slot
                FROM ingestion_checkpoints l
                JOIN ingestion_checkpoints c ON l.protocol = c.protocol AND l.source_type = c.source_type
                WHERE l.chain_id IN (?, ?, ?) AND c.chain_id = ?;
            """, (*legacy_chains, canonical_chain)).fetchall()
            for proto, src_type, l_slot, c_slot in dup_cp:
                max_slot = max(int(l_slot or 0), int(c_slot or 0))
                conn.execute(
                    "UPDATE ingestion_checkpoints SET last_block_or_slot = ? WHERE chain_id = ? AND protocol = ? AND source_type = ?;",
                    (max_slot, canonical_chain, proto, src_type)
                )
                conn.execute(
                    "DELETE FROM ingestion_checkpoints WHERE protocol = ? AND source_type = ? AND chain_id IN (?, ?, ?);",
                    (proto, src_type, *legacy_chains)
                )
            conn.execute("UPDATE ingestion_checkpoints SET chain_id = ? WHERE chain_id IN (?, ?, ?);", (canonical_chain, *legacy_chains))

        # 2j. Reconcile paper_risk_limits
        cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='paper_risk_limits';")
        if cur.fetchone():
            leg_rl = conn.execute("SELECT id FROM paper_risk_limits WHERE chain_id IN (?, ?, ?);", legacy_chains).fetchall()
            can_rl = conn.execute("SELECT id FROM paper_risk_limits WHERE chain_id = ?;", (canonical_chain,)).fetchall()
            if leg_rl and can_rl:
                for (l_id,) in leg_rl:
                    conn.execute("DELETE FROM paper_risk_limits WHERE id = ?;", (l_id,))
            conn.execute("UPDATE paper_risk_limits SET chain_id = ? WHERE chain_id IN (?, ?, ?);", (canonical_chain, *legacy_chains))

        # 2k. Reconcile providers.chains_json
        cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='providers';")
        if cur.fetchone():
            p_rows = conn.execute("SELECT id, chains_json FROM providers;").fetchall()
            for p_id, raw_cj in p_rows:
                raw_cj = raw_cj or "[]"
                try:
                    chains = json.loads(raw_cj)
                    if isinstance(chains, list):
                        updated = False
                        new_chains = []
                        for ch in chains:
                            if ch in legacy_chains:
                                if canonical_chain not in new_chains:
                                    new_chains.append(canonical_chain)
                                updated = True
                            else:
                                if ch not in new_chains:
                                    new_chains.append(ch)
                        if updated:
                            conn.execute("UPDATE providers SET chains_json = ? WHERE id = ?;", (json.dumps(new_chains), p_id))
                except Exception:
                    pass

        # 2l. Reconcile other tables
        for tbl in ("strategy_runs", "strategy_proposals", "strategy_signals", "paper_orders_v2",
                    "market_data_snapshots", "reorg_invalidation_events",
                    "provider_usage_records", "provider_reservations"):
            cur = conn.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{tbl}';")
            if cur.fetchone():
                conn.execute(f"UPDATE {tbl} SET chain_id = ? WHERE chain_id IN (?, ?, ?);", (canonical_chain, *legacy_chains))

    def _migrate_exact_financial_decimal_storage(self, conn: sqlite3.Connection):
        """
        Recreates paper_positions_v2 with exact TEXT affinity for financial fields,
        converting existing values via format_decimal_exact to eliminate IEEE-754
        representation drift and precision loss.
        """
        p_check = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='paper_positions_v2';").fetchone()
        if not p_check:
            return

        conn.execute("""
            CREATE TABLE IF NOT EXISTS paper_positions_v2_new (
                position_id TEXT PRIMARY KEY,
                account_id TEXT NOT NULL,
                chain_id TEXT NOT NULL,
                asset_address TEXT NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL DEFAULT 'LONG',
                token_amount_raw TEXT NOT NULL,
                cost_basis_native TEXT NOT NULL DEFAULT '0.0',
                entry_price_native TEXT NOT NULL DEFAULT '0.0',
                current_price_native TEXT NOT NULL DEFAULT '0.0',
                unrealized_pnl_native TEXT NOT NULL DEFAULT '0.0',
                realized_pnl_native TEXT NOT NULL DEFAULT '0.0',
                opened_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                UNIQUE(account_id, chain_id, asset_address)
            );
        """)

        rows = conn.execute("SELECT * FROM paper_positions_v2;").fetchall()
        for r in rows:
            amt_raw = str(r["token_amount_raw"] or "0")
            cb = format_decimal_exact(Decimal(str(r["cost_basis_native"] or "0.0")))
            ep = format_decimal_exact(Decimal(str(r["entry_price_native"] or "0.0")))
            cp = format_decimal_exact(Decimal(str(r["current_price_native"] or "0.0")))
            upnl = format_decimal_exact(Decimal(str(r["unrealized_pnl_native"] or "0.0")))
            rpnl = format_decimal_exact(Decimal(str(r["realized_pnl_native"] or "0.0")))
            conn.execute(
                """
                INSERT OR REPLACE INTO paper_positions_v2_new (
                    position_id, account_id, chain_id, asset_address, symbol, side,
                    token_amount_raw, cost_basis_native, entry_price_native, current_price_native,
                    unrealized_pnl_native, realized_pnl_native, opened_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    r["position_id"], r["account_id"], r["chain_id"], r["asset_address"],
                    r["symbol"], r["side"], amt_raw, cb, ep, cp, upnl, rpnl,
                    r["opened_at"], r["updated_at"]
                )
            )

        conn.execute("DROP TABLE paper_positions_v2;")
        conn.execute("ALTER TABLE paper_positions_v2_new RENAME TO paper_positions_v2;")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_paper_pos_acc ON paper_positions_v2(account_id, chain_id);")

    def _migrate_orders_and_fills_exact_decimal_storage(self, conn: sqlite3.Connection):
        """
        Recreates paper_orders_v2 and paper_fills with exact TEXT affinity for financial fields,
        converting existing values via format_decimal_exact to eliminate IEEE-754
        representation drift and precision loss.
        """
        def _safe_convert(val: Any, nullable: bool = True, default: str = "0.0") -> Optional[str]:
            if val is None:
                if nullable:
                    return None
                val = default
            if isinstance(val, (int, str, Decimal)):
                return format_decimal_exact(val)
            # If float from SQLite REAL column: convert to str representation safely without IEEE-754 rounding
            return format_decimal_exact(str(val))

        # 1. Rebuild paper_orders_v2 with TEXT affinity
        o_check = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='paper_orders_v2';").fetchone()
        if o_check:
            cur = conn.execute("PRAGMA table_info(paper_orders_v2);")
            cols = {r["name"]: r["type"].upper() for r in cur.fetchall()}
            if cols.get("limit_price_native") != "TEXT" or cols.get("executed_price_native") != "TEXT":
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS paper_orders_v2_new (
                        order_id TEXT PRIMARY KEY,
                        client_order_id TEXT UNIQUE,
                        account_id TEXT NOT NULL,
                        chain_id TEXT NOT NULL,
                        asset_id TEXT NOT NULL,
                        pool_id TEXT,
                        side TEXT NOT NULL CHECK(side IN ('BUY', 'SELL')),
                        order_type TEXT NOT NULL DEFAULT 'MARKET' CHECK(order_type IN ('MARKET', 'LIMIT')),
                        status TEXT NOT NULL CHECK(status IN ('PENDING', 'SUBMITTED', 'FILLED', 'PARTIALLY_FILLED', 'CANCELLED', 'EXPIRED', 'REJECTED')),
                        requested_quantity TEXT NOT NULL,
                        filled_quantity TEXT NOT NULL DEFAULT '0',
                        limit_price_native TEXT,
                        executed_price_native TEXT,
                        max_slippage_bps INTEGER NOT NULL DEFAULT 100,
                        fee_paid_native TEXT NOT NULL DEFAULT '0.0',
                        simulated_gas_native TEXT NOT NULL DEFAULT '0.0',
                        rejection_reason TEXT,
                        execution_assumptions_json TEXT,
                        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                        updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                        origin TEXT DEFAULT 'MANUAL',
                        proposal_id TEXT,
                        authorization_hash TEXT
                    );
                """)
                rows = conn.execute("SELECT * FROM paper_orders_v2;").fetchall()
                for r in rows:
                    lp = _safe_convert(r["limit_price_native"], nullable=True)
                    ep = _safe_convert(r["executed_price_native"], nullable=True)
                    fp = _safe_convert(r["fee_paid_native"] if "fee_paid_native" in r.keys() else "0.0", nullable=False, default="0.0")
                    sg = _safe_convert(r["simulated_gas_native"] if "simulated_gas_native" in r.keys() else "0.0", nullable=False, default="0.0")
                    req_qty = str(r["requested_quantity"])
                    fill_qty = str(r["filled_quantity"] or "0")
                    origin = r["origin"] if "origin" in r.keys() else "MANUAL"
                    proposal_id = r["proposal_id"] if "proposal_id" in r.keys() else None
                    auth_hash = r["authorization_hash"] if "authorization_hash" in r.keys() else None

                    conn.execute("""
                        INSERT OR REPLACE INTO paper_orders_v2_new (
                            order_id, client_order_id, account_id, chain_id, asset_id,
                            pool_id, side, order_type, status, requested_quantity, filled_quantity,
                            limit_price_native, executed_price_native, max_slippage_bps,
                            fee_paid_native, simulated_gas_native, rejection_reason,
                            execution_assumptions_json, created_at, updated_at,
                            origin, proposal_id, authorization_hash
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """, (
                        r["order_id"], r["client_order_id"], r["account_id"], r["chain_id"], r["asset_id"],
                        r["pool_id"], r["side"], r["order_type"], r["status"], req_qty, fill_qty,
                        lp, ep, r["max_slippage_bps"],
                        fp, sg, r["rejection_reason"],
                        r["execution_assumptions_json"], r["created_at"], r["updated_at"],
                        origin, proposal_id, auth_hash
                    ))
                conn.execute("DROP TABLE paper_orders_v2;")
                conn.execute("ALTER TABLE paper_orders_v2_new RENAME TO paper_orders_v2;")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_paper_orders_acc ON paper_orders_v2(account_id, chain_id, status);")
                conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_paper_orders_proposal_uniq ON paper_orders_v2(proposal_id) WHERE proposal_id IS NOT NULL;")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_paper_orders_origin ON paper_orders_v2(origin);")

        # 2. Rebuild paper_fills with TEXT affinity
        f_check = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='paper_fills';").fetchone()
        if f_check:
            cur = conn.execute("PRAGMA table_info(paper_fills);")
            cols = {r["name"]: r["type"].upper() for r in cur.fetchall()}
            if cols.get("fill_price_native") != "TEXT":
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS paper_fills_new (
                        fill_id TEXT PRIMARY KEY,
                        order_id TEXT NOT NULL,
                        account_id TEXT NOT NULL,
                        chain_id TEXT NOT NULL,
                        asset_id TEXT NOT NULL,
                        side TEXT NOT NULL,
                        fill_quantity TEXT NOT NULL,
                        fill_price_native TEXT NOT NULL,
                        fee_native TEXT NOT NULL DEFAULT '0.0',
                        slippage_bps INTEGER NOT NULL DEFAULT 0,
                        market_data_snapshot_id TEXT,
                        occurred_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
                    );
                """)
                rows = conn.execute("SELECT * FROM paper_fills;").fetchall()
                for r in rows:
                    fill_qty = str(r["fill_quantity"])
                    fill_price = _safe_convert(r["fill_price_native"], nullable=False)
                    fee = _safe_convert(r["fee_native"] if "fee_native" in r.keys() else "0.0", nullable=False, default="0.0")

                    conn.execute("""
                        INSERT OR REPLACE INTO paper_fills_new (
                            fill_id, order_id, account_id, chain_id, asset_id, side,
                            fill_quantity, fill_price_native, fee_native, slippage_bps,
                            market_data_snapshot_id, occurred_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """, (
                        r["fill_id"], r["order_id"], r["account_id"], r["chain_id"], r["asset_id"], r["side"],
                        fill_qty, fill_price, fee, r["slippage_bps"],
                        r["market_data_snapshot_id"], r["occurred_at"]
                    ))
                conn.execute("DROP TABLE paper_fills;")
                conn.execute("ALTER TABLE paper_fills_new RENAME TO paper_fills;")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_paper_fills_order ON paper_fills(order_id);")

    def _migrate_provider_reliability_and_decimal_ttl(self, conn: sqlite3.Connection):
        """
        P10.2-J.1: Adds provider reliability metrics, circuit breaker state,
        decimal verification TTL and expiration tracking, verification history,
        and candidate research scoring tables.
        """
        # 1. Update providers table
        p_cur = conn.execute("PRAGMA table_info(providers);")
        p_cols = {r["name"] for r in p_cur.fetchall()}
        if "last_success_at" not in p_cols:
            conn.execute("ALTER TABLE providers ADD COLUMN last_success_at TEXT;")
        if "last_failure_at" not in p_cols:
            conn.execute("ALTER TABLE providers ADD COLUMN last_failure_at TEXT;")
        if "last_latency_ms" not in p_cols:
            conn.execute("ALTER TABLE providers ADD COLUMN last_latency_ms REAL;")
        if "circuit_state" not in p_cols:
            conn.execute("ALTER TABLE providers ADD COLUMN circuit_state TEXT DEFAULT 'CLOSED';")

        # 2. Update token_decimal_verifications table
        t_cur = conn.execute("PRAGMA table_info(token_decimal_verifications);")
        t_cols = {r["name"] for r in t_cur.fetchall()}
        if "ttl_seconds" not in t_cols:
            conn.execute("ALTER TABLE token_decimal_verifications ADD COLUMN ttl_seconds INTEGER DEFAULT 86400;")
        if "expires_at" not in t_cols:
            conn.execute("ALTER TABLE token_decimal_verifications ADD COLUMN expires_at TEXT;")
        if "evidence_provenance_json" not in t_cols:
            conn.execute("ALTER TABLE token_decimal_verifications ADD COLUMN evidence_provenance_json TEXT DEFAULT '{}';")

        # Backfill expires_at for existing verifications
        conn.execute("""
            UPDATE token_decimal_verifications
            SET expires_at = strftime('%Y-%m-%dT%H:%M:%SZ', datetime(verified_at, '+' || COALESCE(ttl_seconds, 86400) || ' seconds'))
            WHERE expires_at IS NULL AND verified_at IS NOT NULL;
        """)

        # 3. Create verification audit history table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS token_decimal_verification_history (
                history_id TEXT PRIMARY KEY,
                verification_id TEXT NOT NULL,
                chain_id TEXT NOT NULL,
                asset_address TEXT NOT NULL,
                decimals INTEGER NOT NULL,
                symbol TEXT,
                verification_source TEXT NOT NULL,
                verification_method TEXT NOT NULL,
                raw_metadata_hash TEXT NOT NULL,
                raw_metadata_json TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL,
                verified_at TEXT NOT NULL,
                archived_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
            );
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tok_dec_hist ON token_decimal_verification_history(chain_id, asset_address);")

        # 4. Create candidate research scores table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS candidate_research_scores (
                score_id TEXT PRIMARY KEY,
                chain_id TEXT NOT NULL,
                asset_address TEXT NOT NULL,
                symbol TEXT,
                composite_score REAL,
                score_breakdown_json TEXT NOT NULL,
                risk_findings_json TEXT NOT NULL,
                data_freshness_status TEXT NOT NULL,
                verification_status TEXT NOT NULL,
                provenance_json TEXT NOT NULL,
                scoring_version TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
            );
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cand_scores_asset ON candidate_research_scores(chain_id, asset_address);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cand_scores_created ON candidate_research_scores(created_at);")

sqlite_migrator = SqliteMigrator()

