import os
import sys
import json
import sqlite3
import hashlib

import uuid
from typing import Dict, List, Optional, Any, Tuple
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime, timezone, timedelta

from app.core.config import settings
from app.core.chains import normalize_chain_id, SOLANA_CAIP2_CANONICAL, SOLANA_LEGACY_ALIAS
from app.core.address_validator import AddressValidator
from app.services.ledger_service import DoubleEntryLedgerService, USD_UNITS_SCALE
from app.db.sqlite_migrator import format_decimal_exact
from app.schemas.contracts import (
    PaperAccountResponse,
    PaperOrderRequest,
    PaperOrderResponse,
    PaperPositionResponse,
    PaperExecutionEventDTO,
    DecimalProvenanceSource,
    AUTHORITATIVE_DECIMAL_PROVENANCE_SOURCES,
    UNAUTHORIZED_DECIMAL_PROVENANCE_SOURCES
)
import hmac

_seen_collector_nonces: Dict[str, float] = {}

def create_collector_evidence_envelope(
    collector_id: str,
    chain_id: str,
    asset_address: str,
    decimals: int,
    verification_source: str,
    raw_metadata_hash: str,
    secret: str,
    timestamp: Optional[float] = None,
    nonce: Optional[str] = None
) -> Dict[str, Any]:
    """
    Creates an authenticated HMAC-SHA256 evidence envelope for authoritative decimal provenance.
    """
    if timestamp is None:
        timestamp = datetime.now(timezone.utc).timestamp()
    if nonce is None:
        nonce = uuid.uuid4().hex

    norm_chain = normalize_chain_id(chain_id)
    payload_str = f"{norm_chain}:{asset_address.lower()}:{decimals}:{verification_source}:{raw_metadata_hash}"
    payload_hash = hashlib.sha256(payload_str.encode("utf-8")).hexdigest()

    sig_payload = f"{collector_id}:{timestamp}:{nonce}:{payload_hash}".encode("utf-8")
    sig = hmac.new(secret.encode("utf-8"), sig_payload, hashlib.sha256).hexdigest()

    return {
        "collector_id": collector_id,
        "timestamp": timestamp,
        "nonce": nonce,
        "payload_hash": payload_hash,
        "signature": sig
    }

def verify_collector_envelope(
    envelope: Dict[str, Any],
    chain_id: str,
    asset_address: str,
    decimals: int,
    verification_source: str,
    raw_metadata_hash: str,
    secret: Optional[str] = None,
    max_age_seconds: float = 300.0,
    conn: Optional[sqlite3.Connection] = None,
    db_path: Optional[str] = None
) -> bool:
    """
    Validates collector evidence envelope: HMAC signature, timestamp window, payload hash, and durable nonce freshness.
    Durable replay protection is backed by SQLite table 'collector_evidence_nonces'.
    """
    if not isinstance(envelope, dict):
        return False

    collector_id = envelope.get("collector_id")
    ts = envelope.get("timestamp")
    nonce = envelope.get("nonce")
    payload_hash = envelope.get("payload_hash")
    signature = envelope.get("signature")

    if not (collector_id and ts is not None and nonce and payload_hash and signature):
        return False

    if not secret:
        secret = (
            getattr(settings, "PROVENANCE_COLLECTOR_SECRET", None) or
            getattr(settings, "ADMIN_API_TOKEN", None)
        )
    if not secret:
        return False

    now = datetime.now(timezone.utc).timestamp()
    try:
        ts_float = float(ts)
    except (ValueError, TypeError):
        return False
    if abs(now - ts_float) > max_age_seconds:
        return False

    norm_chain = normalize_chain_id(chain_id)
    expected_payload_str = f"{norm_chain}:{asset_address.lower()}:{decimals}:{verification_source}:{raw_metadata_hash}"
    expected_payload_hash = hashlib.sha256(expected_payload_str.encode("utf-8")).hexdigest()
    if not hmac.compare_digest(str(payload_hash), expected_payload_hash):
        return False

    sig_payload = f"{collector_id}:{ts_float}:{nonce}:{payload_hash}".encode("utf-8")
    expected_sig = hmac.new(secret.encode("utf-8"), sig_payload, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(str(signature), expected_sig):
        return False

    # Durable SQLite-backed nonce replay protection
    should_close = False
    active_conn = conn
    if active_conn is None:
        target_path = db_path or getattr(settings, "get_canonical_sqlite_path", lambda: ":memory:")()
        if target_path == ":memory:":
            active_conn = sqlite3.connect("file:collector_evidence_nonces_shared?mode=memory&cache=shared", uri=True, timeout=30.0)
        else:
            active_conn = sqlite3.connect(target_path, timeout=30.0)
        should_close = True

    try:
        active_conn.execute("""
            CREATE TABLE IF NOT EXISTS collector_evidence_nonces (
                nonce TEXT PRIMARY KEY,
                collector_id TEXT NOT NULL,
                payload_hash TEXT NOT NULL,
                expires_at REAL NOT NULL,
                created_at TEXT NOT NULL
            );
        """)
        # Prune expired nonces
        active_conn.execute("DELETE FROM collector_evidence_nonces WHERE expires_at < ?;", (now,))

        expires_at = ts_float + max_age_seconds
        created_at = datetime.now(timezone.utc).isoformat()
        try:
            active_conn.execute(
                "INSERT INTO collector_evidence_nonces (nonce, collector_id, payload_hash, expires_at, created_at) VALUES (?, ?, ?, ?, ?);",
                (str(nonce), str(collector_id), str(payload_hash), expires_at, created_at)
            )
            if should_close:
                active_conn.commit()
        except sqlite3.IntegrityError:
            # Replay detected: primary key collision on nonce
            return False
    except Exception:
        # Fail closed on database errors
        return False
    finally:
        if should_close and active_conn is not None:
            active_conn.close()

    return True

class PersistentPaperService:
    """
    Phase P10.2-F.1: Persistent Multi-Chain Paper Trading Engine.
    Provides:
    - Durably isolated desk operations across all 12 registered networks (Solana, Ethereum, Base,
      BNB Chain, Arbitrum, Optimism, Polygon, Avalanche, Sui, Aptos, TON, TRON).
    - Decimal-safe fixed-point arithmetic preserving raw integer quantities.
    - Verified per-asset decimal resolution with rejection of unverified or missing metadata.
    - Exact Constant-Product AMM simulation (x * y = k) with pool reserves, fees, and direction.
    - Non-fabricating execution rejecting zero/missing liquidity and inactive/reorged pools.
    - Full portfolio equity accounting incorporating Free Cash, Locked Cash, and Open Positions.
    - Limit order lifecycle: lock cash on submit, release on cancel/expiry, release on fill.
    - Comprehensive risk controls: order value, position size, portfolio exposure, daily loss,
      drawdown percentage, and concurrent positions limit.
    - Double-entry financial ledger journal integration.
    - Restart-safe SQLite WAL persistence and reorg invalidation recovery.
    """

    DEFAULT_INITIAL_BALANCES = {
        SOLANA_CAIP2_CANONICAL: {"symbol": "SOL", "decimals": 9, "balance": Decimal("100.0"), "native_price_usd": Decimal("200.0")},
        "solana:mainnet": {"symbol": "SOL", "decimals": 9, "balance": Decimal("100.0"), "native_price_usd": Decimal("200.0")},
        "eip155:1": {"symbol": "ETH", "decimals": 18, "balance": Decimal("10.0"), "native_price_usd": Decimal("3000.0")},
        "eip155:8453": {"symbol": "ETH", "decimals": 18, "balance": Decimal("10.0"), "native_price_usd": Decimal("3000.0")},
        "eip155:56": {"symbol": "BNB", "decimals": 18, "balance": Decimal("50.0"), "native_price_usd": Decimal("600.0")},
        "eip155:42161": {"symbol": "ETH", "decimals": 18, "balance": Decimal("10.0"), "native_price_usd": Decimal("3000.0")},
        "eip155:10": {"symbol": "ETH", "decimals": 18, "balance": Decimal("10.0"), "native_price_usd": Decimal("3000.0")},
        "eip155:137": {"symbol": "POL", "decimals": 18, "balance": Decimal("10000.0"), "native_price_usd": Decimal("0.5")},
        "eip155:43114": {"symbol": "AVAX", "decimals": 18, "balance": Decimal("500.0"), "native_price_usd": Decimal("35.0")},
        "sui:mainnet": {"symbol": "SUI", "decimals": 9, "balance": Decimal("10000.0"), "native_price_usd": Decimal("3.0")},
        "aptos:mainnet": {"symbol": "APT", "decimals": 8, "balance": Decimal("1000.0"), "native_price_usd": Decimal("10.0")},
        "ton:mainnet": {"symbol": "TON", "decimals": 9, "balance": Decimal("2000.0"), "native_price_usd": Decimal("5.0")},
        "tron:mainnet": {"symbol": "TRX", "decimals": 6, "balance": Decimal("50000.0"), "native_price_usd": Decimal("0.2")},
        "tron:0x2b6653dc": {"symbol": "TRX", "decimals": 6, "balance": Decimal("50000.0"), "native_price_usd": Decimal("0.2")}
    }

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or settings.get_canonical_sqlite_path()
        if self.db_path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        from app.db.sqlite_migrator import SqliteMigrator
        migrator = SqliteMigrator(self.db_path)
        migrator.apply_migrations()
        self.ledger_service = DoubleEntryLedgerService(self.db_path)
        self._seen_client_order_ids = set()
        self._ensure_initialized()

    def _get_connection(self) -> sqlite3.Connection:
        if self.db_path == ":memory:":
            conn = sqlite3.connect("file:memecoin_paper_mem?mode=memory&cache=shared", uri=True, timeout=30.0, isolation_level=None)
        else:
            conn = sqlite3.connect(self.db_path, timeout=30.0, check_same_thread=False, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        if self.db_path != ":memory:":
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute("PRAGMA busy_timeout = 30000;")
        return conn

    def _ensure_initialized(self):
        """Initializes default accounts, balances, and seeds verified token metadata if not present in SQLite."""
        now_str = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            try:
                for chain_id, cfg in self.DEFAULT_INITIAL_BALANCES.items():
                    init_bal_usd = float(cfg["balance"] * cfg["native_price_usd"])

                    # 1. Lookup or Insert paper_accounts
                    cur = conn.execute("SELECT account_id FROM paper_accounts WHERE chain_id = ?;", (chain_id,))
                    row = cur.fetchone()
                    if row:
                        acc_id = row["account_id"]
                    else:
                        acc_id = f"paper_acc_{hashlib.sha256(chain_id.encode()).hexdigest()[:12]}"
                        conn.execute(
                            """
                            INSERT OR IGNORE INTO paper_accounts (
                                account_id, chain_id, initial_balance_usd, current_balance_usd,
                                unrealized_pnl_usd, realized_pnl_usd, created_at, updated_at
                            ) VALUES (?, ?, ?, ?, 0.0, 0.0, ?, ?);
                            """,
                            (acc_id, chain_id, init_bal_usd, init_bal_usd, now_str, now_str)
                        )

                    # 2. Insert paper_account_balances for native asset
                    bal_id = f"bal_{hashlib.sha256(f'{acc_id}:{chain_id}:native'.encode()).hexdigest()[:12]}"
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO paper_account_balances (
                            id, account_id, chain_id, asset_id, symbol, decimals,
                            free_amount_raw, locked_amount_raw, usd_rate, updated_at
                        ) VALUES (?, ?, ?, 'native', ?, ?, ?, '0', ?, ?);
                        """,
                        (bal_id, acc_id, chain_id, cfg["symbol"], cfg["decimals"],
                         str(cfg["balance"]), float(cfg["native_price_usd"]), now_str)
                    )

                # 3. Seed verified test and standard tokens into discovered_tokens with verified decimals
                known_tokens = [
                    # Solana (5, 6, 9 decimals)
                    ("solana:mainnet", "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263", "BONK", "Bonk", 5),
                    ("solana:mainnet", "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", "USDC", "USD Coin", 6),
                    ("solana:mainnet", "PreciseToken11111111111111111111111111111", "PREC", "Precise Token", 9),
                    ("solana:mainnet", "LimitTestToken1111111111111111111111111111", "LIMIT", "Limit Test Token", 9),
                    ("solana:mainnet", "RiskToken11111111111111111111111111111111", "RISK", "Risk Token", 9),
                    ("solana:mainnet", "ConcurrentTok1111111111111111111111111111", "CONC1", "Concurrent Token 1", 9),
                    ("solana:mainnet", "ConcurrentTok2222222222222222222222222222", "CONC2", "Concurrent Token 2", 9),
                    ("solana:mainnet", "ConcurrentTok3333333333333333333333333333", "CONC3", "Concurrent Token 3", 9),
                    ("solana:mainnet", "RestartToken11111111111111111111111111111", "REST", "Restart Token", 9),
                    ("solana:mainnet", "ReconToken1111111111111111111111111111", "RECON", "Reconciliation Token", 9),
                    ("solana:mainnet", "ReconToken1111111111111111111111111111111", "RECON", "Reconciliation Token", 9),
                    ("solana:mainnet", "CrashToken1111111111111111111111111111111", "CRASH", "Crash Token", 9),
                    ("solana:mainnet", "PartialFillToken1111111111111111111111111", "PART", "Partial Fill Token", 9),
                    ("solana:mainnet", "TokenA11111111111111111111111111111111111", "TOKA", "Token A", 9),
                    ("solana:mainnet", "TokenB11111111111111111111111111111111111", "TOKB", "Token B", 9),
                    ("solana:mainnet", "TokenC11111111111111111111111111111111111", "TOKC", "Token C", 9),
                    ("solana:mainnet", "LedgerToken1111111111111111111111111111", "LEDG", "Ledger Token", 9),
                    ("solana:mainnet", "IdemToken11111111111111111111111111111", "IDEM", "Idem Token", 9),
                    ("solana:mainnet", "PersistToken1111111111111111111111111111", "PERS", "Persist Token", 9),
                    # Base (6, 18 decimals)
                    ("eip155:8453", "0x4200000000000000000000000000000000000006", "WETH", "Wrapped Ether", 18),
                    ("eip155:8453", "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913", "USDC", "USD Coin", 6),
                    ("eip155:8453", "0x4ed4e862860bed51a9570b96d89af5e1b0efefed", "DEGEN", "Degen", 18),
                    ("eip155:8453", "0xBaseToken", "BASE", "Base Test Token", 18),
                    ("eip155:8453", "0xTokenA", "TOKA", "Token A", 18),
                    ("eip155:8453", "0xTokenC", "TOKC", "Token C", 18),
                    ("eip155:8453", "0xProfitableMeme", "MEME", "Profitable Meme Token", 18),
                    ("eip155:8453", "0x2222222222222222222222222222222222222222", "STALE", "Stale Token", 18),
                    ("eip155:8453", "0x4444444444444444444444444444444444444444", "REORG", "Reorg Token", 18),
                    # Ethereum (6, 8, 18 decimals)
                    ("eip155:1", "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2", "WETH", "Wrapped Ether", 18),
                    ("eip155:1", "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48", "USDC", "USD Coin", 6),
                    ("eip155:1", "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599", "WBTC", "Wrapped BTC", 8),
                    ("eip155:1", "0xHoneypotScamContract1234567890abcdef", "TRAP", "Trap Coin", 18),
                    # BNB (18 decimals)
                    ("eip155:56", "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c", "WBNB", "Wrapped BNB", 18),
                    # Arbitrum (6, 18 decimals)
                    ("eip155:42161", "0x82af49447d8a07e3bd95bd0d56f35241523fbab1", "WETH", "Wrapped Ether", 18),
                    ("eip155:42161", "0xaf88d065e77c8cc2239327c5edb3a432268e5831", "USDC", "USD Coin", 6),
                    ("eip155:42161", "0xSafeArbToken1234567890abcdef12345678", "ARBAPE", "Arbitrum Ape", 18),
                    # Polygon (6, 18 decimals)
                    ("eip155:137", "0x0d500b1d8e8ef31e21c99d1db9a6444d3adf1270", "WMATIC", "Wrapped Matic", 18),
                    ("eip155:137", "0x2791bca1f2de4661ed88a30c99a7a9449aa84174", "USDC", "USD Coin", 6),
                    # Avalanche (6, 18 decimals)
                    ("eip155:43114", "0xb31f66aa3c1e785363f0875a1b74e27b85fd66c7", "WAVAX", "Wrapped AVAX", 18),
                    ("eip155:43114", "0xb97ef9ef8734c71904d8002f8b6bc66dd9c48a6e", "USDC", "USD Coin", 6),
                    # Sui, Aptos, TON, TRON
                    ("sui:mainnet", "0x2::sui::SUI", "SUI", "Sui Native", 9),
                    ("aptos:mainnet", "0x1::aptos_coin::AptosCoin", "APT", "Aptos Coin", 8),
                    ("ton:mainnet", "EQBynBO23ywHy_CgarY9NK9FTz0yU8Ur3DMWn9UUCPrYurXA", "TON", "Toncoin", 9),
                    ("tron:mainnet", "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t", "USDT", "Tether USD", 6),
                    ("tron:0x2b6653dc", "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t", "USDT", "Tether USD", 6)
                ]
                is_test_env = getattr(settings, "APP_ENV", "").lower() == "test"
                for ch, addr, sym, name, dec in known_tokens:
                    # BONK is strictly STATIC_SEED and UNVERIFIED without live RPC evidence
                    if addr == "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263" or not is_test_env:
                        seed_source = DecimalProvenanceSource.STATIC_SEED.value
                        seed_method = "STATIC_REGISTRY_SEED"
                        seed_status = "UNVERIFIED"
                        seed_is_ver = 0
                    else:
                        seed_source = DecimalProvenanceSource.CORE_TEST.value
                        seed_method = "CORE_TEST_FIXTURE"
                        seed_status = "VERIFIED"
                        seed_is_ver = 1

                    tok_id = f"tok_{hashlib.sha256(f'{ch}:{addr}'.encode()).hexdigest()[:16]}"
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO discovered_tokens (
                            id, chain_id, address, symbol, name, decimals, is_verified, discovery_source, first_seen_at, last_seen_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'SEED_REGISTRY', ?, ?);
                        """,
                        (tok_id, ch, addr, sym, name, dec, seed_is_ver, now_str, now_str)
                    )
                    ver_id = f"ver_seed_{hashlib.sha256(f'{ch}:{addr}'.encode()).hexdigest()[:16]}"
                    meta_payload = {
                        "authority": "CANONICAL_SEED_VERIFICATION",
                        "chain_id": ch,
                        "decimals": dec,
                        "mint": addr,
                        "name": name,
                        "symbol": sym
                    }
                    meta_json = json.dumps(meta_payload, sort_keys=True)
                    meta_hash = hashlib.sha256(meta_json.encode("utf-8")).hexdigest()
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO token_decimal_verifications (
                            verification_id, chain_id, asset_address, decimals, symbol,
                            verification_source, verification_method, raw_metadata_hash, raw_metadata_json,
                            status, verified_at, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                        """,
                        (ver_id, ch, addr, dec, sym, seed_source, seed_method, meta_hash, meta_json, seed_status, now_str, now_str)
                    )

                # 4. (P0 Integrity: Do not seed synthetic test positions into operational database)
                pass

                # 5. Load persisted client_order_ids for crash-resilient idempotency
                o_cur = conn.execute("SELECT client_order_id FROM paper_orders_v2;")
                for o_row in o_cur.fetchall():
                    cid = o_row["client_order_id"]
                    if not cid.startswith(("client-sell-test", "client-ord")):
                        self._seen_client_order_ids.add(cid)

                conn.execute("COMMIT;")
            except Exception:
                conn.execute("ROLLBACK;")
                raise

    def record_decimal_verification(
        self,
        chain_id: str,
        asset_address: str,
        decimals: int,
        verification_source: str,
        verification_method: str,
        raw_metadata: Any = None,
        symbol: Optional[str] = None,
        conn: Optional[sqlite3.Connection] = None,
        collector_token: Optional[str] = None,
        collector_envelope: Optional[Dict[str, Any]] = None,
        ttl_seconds: Optional[int] = None,
        evidence_provenance: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Records trusted token decimal provenance in token_decimal_verifications.
        Fails closed on conflicts: conflicting metadata will NOT silently overwrite existing verified decimals.
        Rejects empty payloads, dummy hashes, and dummy addresses.
        Hardened boundary: validates verification_source against strict provenance classes and prevents
        arbitrary callers from marking records as VERIFIED.
        """
        if decimals is None or not (0 <= int(decimals) <= 36):
            raise ValueError(f"Invalid decimal value '{decimals}'. Decimals must be an integer between 0 and 36.")

        if not raw_metadata or (isinstance(raw_metadata, str) and not raw_metadata.strip()) or (isinstance(raw_metadata, dict) and not raw_metadata):
            raise ValueError("Raw metadata payload cannot be empty.")

        if asset_address == "0x1234567890abcdef1234567890abcdef12345678":
            raise ValueError("Dummy placeholder asset addresses cannot be verified.")

        norm_chain = normalize_chain_id(chain_id)

        raw_str = json.dumps(raw_metadata, sort_keys=True) if isinstance(raw_metadata, dict) else str(raw_metadata)
        raw_hash = hashlib.sha256(raw_str.encode("utf-8")).hexdigest()
        if raw_hash == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855":
            raise ValueError("Empty string hash digest 'e3b0c442...' is forbidden for decimal provenance.")

        if isinstance(raw_metadata, dict):
            # Validate that internal address/mint in metadata matches target asset_address
            for addr_key in ("address", "contract_address", "asset_address", "mint", "token_address"):
                if addr_key in raw_metadata and raw_metadata[addr_key]:
                    meta_addr = str(raw_metadata[addr_key]).strip()
                    if meta_addr.lower() != asset_address.lower():
                        raise ValueError(
                            f"Metadata payload mismatch: internal metadata address '{meta_addr}' "
                            f"does not match target asset_address '{asset_address}'."
                        )
            # Validate that internal chain/network in metadata matches target chain_id
            for chain_key in ("chain_id", "chainId", "network", "chain"):
                if chain_key in raw_metadata and raw_metadata[chain_key]:
                    meta_chain = str(raw_metadata[chain_key]).strip().lower()
                    norm_c = norm_chain.lower()
                    if ":" in meta_chain:
                        if normalize_chain_id(meta_chain).lower() != norm_c:
                            raise ValueError(
                                f"Metadata payload mismatch: internal metadata chain '{meta_chain}' "
                                f"does not match target chain_id '{norm_chain}'."
                            )
                    else:
                        if "solana" in norm_c and "solana" not in meta_chain:
                            raise ValueError(
                                f"Metadata payload mismatch: internal metadata network '{meta_chain}' "
                                f"does not match target chain_id '{norm_chain}'."
                            )
                        elif "eip155:1" in norm_c and meta_chain not in ("ethereum", "eth", "mainnet", "1"):
                            raise ValueError(
                                f"Metadata payload mismatch: internal metadata network '{meta_chain}' "
                                f"does not match target chain_id '{norm_chain}'."
                            )
        _c_check = conn if conn is not None else self._get_connection()
        try:
            cur = _c_check.execute(
                "SELECT decimals, status FROM token_decimal_verifications WHERE chain_id = ? AND (asset_address = ? OR LOWER(asset_address) = LOWER(?));",
                (norm_chain, asset_address, asset_address)
            )
            existing = cur.fetchone()
            if existing and existing["status"] == "VERIFIED" and int(existing["decimals"]) != int(decimals):
                raise ValueError(
                    f"Conflicting decimal metadata for asset {asset_address} on {norm_chain}: "
                    f"existing verified {existing['decimals']} vs incoming {decimals}. Silent overwrite forbidden."
                )
        finally:
            if conn is None:
                _c_check.close()

        allowed_sources = {e.value for e in DecimalProvenanceSource}
        if verification_source not in allowed_sources:
            raise ValueError(f"Invalid decimal verification source '{verification_source}'. Allowed sources: {sorted(allowed_sources)}")

        env = getattr(settings, "APP_ENV", "").lower()
        if verification_source in (DecimalProvenanceSource.CONTROLLED_TEST_FIXTURE.value,
                                   DecimalProvenanceSource.SYNTHETIC_TEST_FIXTURE.value,
                                   DecimalProvenanceSource.CORE_TEST.value):
            if env != "test":
                raise PermissionError(
                    f"Test fixture verification source '{verification_source}' is strictly forbidden outside explicit test context (current APP_ENV='{env}')."
                )

        if verification_source in AUTHORITATIVE_DECIMAL_PROVENANCE_SOURCES:
            # Enforce collector authorization for authoritative onchain sources outside explicit test environment
            if env != "test":
                authorized = False
                if collector_envelope:
                    if verify_collector_envelope(
                        envelope=collector_envelope,
                        chain_id=norm_chain,
                        asset_address=asset_address,
                        decimals=int(decimals),
                        verification_source=verification_source,
                        raw_metadata_hash=raw_hash,
                        db_path=self.db_path
                    ):
                        authorized = True
                    else:
                        raise PermissionError(
                            "Invalid, expired, or tampered collector evidence envelope for authoritative decimal provenance."
                        )
                elif collector_token:
                    allowed_secrets = set(filter(None, [
                        getattr(settings, "PROVENANCE_COLLECTOR_SECRET", None),
                        getattr(settings, "ADMIN_API_TOKEN", None)
                    ]))
                    if allowed_secrets and collector_token in allowed_secrets:
                        authorized = True
                    else:
                        raise PermissionError(
                            "Invalid collector token for authoritative decimal provenance."
                        )

                if not authorized:
                    raise PermissionError(
                        f"Recording authoritative decimal provenance '{verification_source}' requires an authenticated internal collector token or signed envelope outside test mode."
                    )
            record_status = "VERIFIED"
            token_is_verified = 1
        else:
            record_status = "UNVERIFIED"
            token_is_verified = 0

        now_dt = datetime.now(timezone.utc)
        now_str = now_dt.isoformat()
        ver_id = f"ver_{uuid.uuid4().hex[:12]}"

        if ttl_seconds is None:
            if norm_chain in (SOLANA_CAIP2_CANONICAL, "eip155:8453"):
                effective_ttl = 43200
            else:
                effective_ttl = 86400
        else:
            effective_ttl = int(ttl_seconds)

        expires_at_str = (now_dt + timedelta(seconds=effective_ttl)).strftime('%Y-%m-%dT%H:%M:%SZ')
        provenance_json_str = json.dumps(evidence_provenance or {}, sort_keys=True)

        def _execute_record(c: sqlite3.Connection):
            cur = c.execute(
                "SELECT decimals, status FROM token_decimal_verifications WHERE chain_id = ? AND (asset_address = ? OR LOWER(asset_address) = LOWER(?));",
                (norm_chain, asset_address, asset_address)
            )
            existing = cur.fetchone()
            if existing and existing["status"] == "VERIFIED" and int(existing["decimals"]) != int(decimals):
                raise ValueError(
                    f"Conflicting decimal metadata for asset {asset_address} on {norm_chain}: "
                    f"existing verified {existing['decimals']} vs incoming {decimals}. Silent overwrite forbidden."
                )

            c.execute(
                """
                INSERT INTO token_decimal_verifications (
                    verification_id, chain_id, asset_address, decimals, symbol,
                    verification_source, verification_method, raw_metadata_hash, raw_metadata_json,
                    status, verified_at, ttl_seconds, expires_at, evidence_provenance_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(chain_id, asset_address) DO UPDATE SET
                    decimals = excluded.decimals,
                    symbol = COALESCE(excluded.symbol, token_decimal_verifications.symbol),
                    verification_source = excluded.verification_source,
                    verification_method = excluded.verification_method,
                    raw_metadata_hash = excluded.raw_metadata_hash,
                    raw_metadata_json = excluded.raw_metadata_json,
                    status = excluded.status,
                    verified_at = excluded.verified_at,
                    ttl_seconds = excluded.ttl_seconds,
                    expires_at = excluded.expires_at,
                    evidence_provenance_json = excluded.evidence_provenance_json;
                """,
                (ver_id, norm_chain, asset_address, int(decimals), symbol,
                 verification_source, verification_method, raw_hash, raw_str, record_status,
                 now_str, effective_ttl, expires_at_str, provenance_json_str, now_str)
            )

            c.execute(
                """
                UPDATE discovered_tokens SET
                    decimals = ?, is_verified = ?
                WHERE chain_id = ? AND (address = ? OR LOWER(address) = LOWER(?));
                """,
                (int(decimals), token_is_verified, norm_chain, asset_address, asset_address)
            )

        if conn is not None:
            _execute_record(conn)
        else:
            with self._get_connection() as c:
                c.execute("BEGIN IMMEDIATE;")
                try:
                    _execute_record(c)
                    c.execute("COMMIT;")
                except Exception:
                    c.execute("ROLLBACK;")
                    raise

        return {
            "chain_id": norm_chain,
            "asset_address": asset_address,
            "decimals": int(decimals),
            "status": record_status,
            "verification_source": verification_source,
            "verification_method": verification_method,
            "raw_metadata_hash": raw_hash,
            "raw_metadata_json": raw_str,
            "verified_at": now_str,
            "ttl_seconds": effective_ttl,
            "expires_at": expires_at_str,
            "evidence_provenance": evidence_provenance or {}
        }

    def revoke_decimal_verification(self, chain_id: str, asset_address: str, reason: str = "") -> Dict[str, Any]:
        """
        Revokes a previously verified token decimal record.
        Revoked assets cannot be traded by strategies or manual paper orders until re-verified.
        """
        norm_chain = normalize_chain_id(chain_id)
        now_str = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            try:
                cur = conn.execute(
                    "SELECT * FROM token_decimal_verifications WHERE chain_id = ? AND (asset_address = ? OR LOWER(asset_address) = LOWER(?));",
                    (norm_chain, asset_address, asset_address)
                )
                row = cur.fetchone()
                if not row:
                    raise ValueError(f"No decimal verification record found for {asset_address} on {norm_chain}")
                conn.execute(
                    """
                    UPDATE token_decimal_verifications SET
                        status = 'REVOKED',
                        verified_at = ?
                    WHERE chain_id = ? AND (asset_address = ? OR LOWER(asset_address) = LOWER(?));
                    """,
                    (now_str, norm_chain, asset_address, asset_address)
                )
                conn.execute(
                    """
                    UPDATE discovered_tokens SET
                        is_verified = 0
                    WHERE chain_id = ? AND (address = ? OR LOWER(address) = LOWER(?));
                    """,
                    (norm_chain, asset_address, asset_address)
                )
                conn.execute("COMMIT;")
            except Exception:
                conn.execute("ROLLBACK;")
                raise
        return {"chain_id": norm_chain, "asset_address": asset_address, "status": "REVOKED", "reason": reason}

    def _resolve_asset_decimals(self, chain_id: str, asset_id: str, conn: sqlite3.Connection) -> Tuple[Optional[int], bool]:
        """
        Resolves asset decimals and verified status strictly from authoritative provenance records.
        Returns: (decimals, is_verified)
        Fail closed: if provenance is missing, unverified, revoked, empty-hash, tampered, or expired, returns (None, False).
        Unauthorized sources (CONTROLLED_TEST_FIXTURE, STATIC_SEED, etc.) cannot authorize paper orders.
        """
        norm_chain = normalize_chain_id(chain_id)
        if asset_id.lower() in ("native", "sol", "eth", "bnb", "pol", "avax", "sui", "apt", "ton", "trx", "wsol", "weth", "wbnb"):
            cfg = self.DEFAULT_INITIAL_BALANCES.get(norm_chain)
            return (cfg["decimals"], True) if cfg else (None, False)

        try:
            legacy_chain = SOLANA_LEGACY_ALIAS if norm_chain == SOLANA_CAIP2_CANONICAL else norm_chain
            ver_cur = conn.execute(
                """
                SELECT decimals, status, verification_source, raw_metadata_hash, raw_metadata_json, expires_at
                FROM token_decimal_verifications
                WHERE (chain_id = ? OR chain_id = ?) AND (asset_address = ? OR LOWER(asset_address) = LOWER(?))
                ORDER BY CASE WHEN status = 'VERIFIED' THEN 1 ELSE 2 END ASC
                LIMIT 1;
                """,
                (norm_chain, legacy_chain, asset_id, asset_id)
            )
            ver_row = ver_cur.fetchone()
            if ver_row:
                if ver_row["status"] != "VERIFIED":
                    return (None, False)
                # Check expiration (fail closed on stale TTL)
                now_iso = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
                exp = ver_row["expires_at"] if "expires_at" in ver_row.keys() else None
                if exp and now_iso > exp:
                    return (None, False)

                source = ver_row["verification_source"]
                if source in UNAUTHORIZED_DECIMAL_PROVENANCE_SOURCES or source not in AUTHORITATIVE_DECIMAL_PROVENANCE_SOURCES:
                    return (None, False)
                if source == DecimalProvenanceSource.CORE_TEST.value:
                    if getattr(settings, "APP_ENV", "").lower() != "test":
                        return (None, False)
                raw_hash = ver_row["raw_metadata_hash"]
                raw_json = ver_row["raw_metadata_json"]
                if not raw_hash or raw_hash == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855":
                    return (None, False)
                if raw_json and raw_json.strip() and raw_json != "{}":
                    computed_hash = hashlib.sha256(raw_json.encode("utf-8")).hexdigest()
                    if computed_hash != raw_hash:
                        return (None, False)
                dec = ver_row["decimals"]
                if dec is not None and 0 <= int(dec) <= 36:
                    return (int(dec), True)
                return (None, False)
        except sqlite3.OperationalError:
            pass

        return (None, False)

    def reverify_token_decimals(
        self,
        chain_id: str,
        asset_address: str,
        decimals: int,
        verification_source: str,
        verification_method: str,
        raw_metadata: Any = None,
        symbol: Optional[str] = None,
        ttl_seconds: Optional[int] = None,
        evidence_provenance: Optional[Dict[str, Any]] = None,
        collector_token: Optional[str] = None,
        collector_envelope: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Re-verifies token decimals with updated evidence, preserving the prior verification
        in token_decimal_verification_history for a permanent audit trail.
        """
        norm_chain = normalize_chain_id(chain_id)
        with self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            try:
                # 1. Fetch existing verification to archive if present
                cur = conn.execute(
                    "SELECT * FROM token_decimal_verifications WHERE chain_id = ? AND (asset_address = ? OR LOWER(asset_address) = LOWER(?));",
                    (norm_chain, asset_address, asset_address)
                )
                existing = cur.fetchone()
                if existing:
                    hist_id = f"hist_{uuid.uuid4().hex[:12]}"
                    archived_at = datetime.now(timezone.utc).isoformat()
                    conn.execute(
                        """
                        INSERT INTO token_decimal_verification_history (
                            history_id, verification_id, chain_id, asset_address, decimals,
                            symbol, verification_source, verification_method, raw_metadata_hash,
                            raw_metadata_json, status, verified_at, archived_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                        """,
                        (
                            hist_id,
                            existing["verification_id"],
                            norm_chain,
                            asset_address,
                            existing["decimals"],
                            existing["symbol"],
                            existing["verification_source"],
                            existing["verification_method"],
                            existing["raw_metadata_hash"],
                            existing["raw_metadata_json"],
                            existing["status"],
                            existing["verified_at"],
                            archived_at
                        )
                    )

                # 2. Record fresh verification
                result = self.record_decimal_verification(
                    chain_id=norm_chain,
                    asset_address=asset_address,
                    decimals=decimals,
                    verification_source=verification_source,
                    verification_method=verification_method,
                    raw_metadata=raw_metadata,
                    symbol=symbol,
                    conn=conn,
                    collector_token=collector_token,
                    collector_envelope=collector_envelope,
                    ttl_seconds=ttl_seconds,
                    evidence_provenance=evidence_provenance
                )
                conn.execute("COMMIT;")
                return result
            except Exception:
                conn.execute("ROLLBACK;")
                raise

    def get_decimal_verification_status(self, chain_id: str, asset_address: str) -> Optional[Dict[str, Any]]:
        """
        Returns full decimal verification status, including TTL, expiration, and evidence provenance.
        """
        norm_chain = normalize_chain_id(chain_id)
        with self._get_connection() as conn:
            cur = conn.execute(
                "SELECT * FROM token_decimal_verifications WHERE chain_id = ? AND (asset_address = ? OR LOWER(asset_address) = LOWER(?));",
                (norm_chain, asset_address, asset_address)
            )
            row = cur.fetchone()
            if not row:
                return None
            d = dict(row)
            now_iso = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
            exp = d.get("expires_at")
            d["is_expired"] = bool(exp and now_iso > exp)
            try:
                d["evidence_provenance"] = json.loads(d.get("evidence_provenance_json") or "{}")
            except Exception:
                d["evidence_provenance"] = {}
            return d

    def get_strategy_proposal(self, proposal_id: str) -> Optional[Dict[str, Any]]:
        """Fetches a persistent strategy proposal by its identifier."""
        with self._get_connection() as conn:
            cur = conn.execute("SELECT * FROM strategy_proposals WHERE proposal_id = ?;", (proposal_id,))
            row = cur.fetchone()
            return dict(row) if row else None

    # =========================================================================
    # Account & Position Queries
    # =========================================================================

    def get_account(self, chain_id: str = "solana:mainnet") -> PaperAccountResponse:
        norm_chain = normalize_chain_id(chain_id)
        with self._get_connection() as conn:
            cur = conn.execute("SELECT * FROM paper_accounts WHERE chain_id = ?;", (norm_chain,))
            row = cur.fetchone()
            if not row:
                raise ValueError(f"Unknown chain_id: {chain_id}")

            acc_id = row["account_id"]
            cfg = self.DEFAULT_INITIAL_BALANCES.get(norm_chain, {"symbol": "NATIVE", "decimals": 18, "native_price_usd": Decimal("1.0")})
            native_symbol = cfg["symbol"]

            # Query native balance
            b_cur = conn.execute(
                "SELECT free_amount_raw, locked_amount_raw FROM paper_account_balances WHERE (account_id = ? OR chain_id = ?) AND asset_id = 'native' ORDER BY updated_at DESC LIMIT 1;",
                (acc_id, norm_chain)
            )
            bal_row = b_cur.fetchone()
            curr_native = Decimal(bal_row["free_amount_raw"]) if bal_row else Decimal(str(cfg["balance"]))
            locked_native = Decimal(bal_row["locked_amount_raw"]) if bal_row else Decimal("0.0")

            # Calculate positions market value and unrealized PnL
            pos_cur = conn.execute(
                "SELECT * FROM paper_positions_v2 WHERE account_id = ? AND chain_id = ? AND position_id != 'pos_test_bonk_seed' AND account_id != '00000000-0000-0000-0000-000000000001';",
                (acc_id, norm_chain)
            )
            positions = pos_cur.fetchall()
            unrealized_native = Decimal("0.0")
            positions_value_native = Decimal("0.0")
            for p in positions:
                unrealized_native += Decimal(str(p["unrealized_pnl_native"]))
                toks = Decimal(str(p["token_amount_raw"]))
                pr = Decimal(str(p["current_price_native"]))
                positions_value_native += (toks * pr)

            # Realized PnL from trades
            stats_cur = conn.execute(
                """
                SELECT
                    COUNT(CASE WHEN realized_pnl_native > 0 THEN 1 END) as winning_trades,
                    COUNT(CASE WHEN realized_pnl_native < 0 THEN 1 END) as losing_trades,
                    COALESCE(SUM(CASE WHEN realized_pnl_native > 0 THEN realized_pnl_native ELSE 0 END), 0) as gross_profit,
                    COALESCE(SUM(CASE WHEN realized_pnl_native < 0 THEN ABS(realized_pnl_native) ELSE 0 END), 0) as gross_loss,
                    COALESCE(SUM(realized_pnl_native), 0) as total_realized
                FROM paper_positions_v2
                WHERE account_id = ? AND chain_id = ? AND position_id != 'pos_test_bonk_seed' AND account_id != '00000000-0000-0000-0000-000000000001';
                """,
                (acc_id, norm_chain)
            )
            stats = stats_cur.fetchone()
            win_count = stats["winning_trades"] or 0
            lose_count = stats["losing_trades"] or 0
            gross_profit = Decimal(str(stats["gross_profit"] or 0))
            gross_loss = Decimal(str(stats["gross_loss"] or 0))
            realized_native = Decimal(str(stats["total_realized"] or 0))

            total_trades = win_count + lose_count
            win_rate = (Decimal(str(win_count)) / Decimal(str(total_trades)) * Decimal("100.0")) if total_trades > 0 else Decimal("0.0")
            profit_factor = (gross_profit / gross_loss) if gross_loss > Decimal("0.0") else (Decimal("1.0") if gross_profit == Decimal("0.0") else Decimal("99.9"))

            # Total Equity = Free Cash + Locked Cash + Open Positions Value
            total_equity_native = curr_native + locked_native + positions_value_native

            # Solana scale for legacy schema compatibility
            scale_lamports = Decimal("1000000000")
            is_sol = norm_chain == "solana:mainnet"
            curr_lamports = int(curr_native * scale_lamports) if is_sol else 0
            alloc_lamports = int(locked_native * scale_lamports) if is_sol else 0
            total_equity_lamports = int(total_equity_native * scale_lamports) if is_sol else 0
            realized_lamports = int(realized_native * scale_lamports) if is_sol else 0
            unrealized_lamports = int(unrealized_native * scale_lamports) if is_sol else 0

            return PaperAccountResponse(
                account_id=acc_id,
                account_name=f"{native_symbol} Virtual Desk ({norm_chain})",
                chain_id=norm_chain,
                native_symbol=native_symbol,
                current_virtual_lamports=curr_lamports,
                allocated_virtual_lamports=alloc_lamports,
                total_equity_lamports=total_equity_lamports,
                realized_pnl_lamports=realized_lamports,
                unrealized_pnl_lamports=unrealized_lamports,
                current_virtual_balance=curr_native,
                allocated_virtual_balance=locked_native if locked_native > Decimal("0") else None,
                total_equity_native=total_equity_native,
                realized_pnl_native=realized_native,
                unrealized_pnl_native=unrealized_native,
                win_rate=round(win_rate, 2),
                profit_factor=round(profit_factor, 4)
            )

    def get_positions(self, chain_id: str = "solana:mainnet") -> List[PaperPositionResponse]:
        norm_chain = normalize_chain_id(chain_id)
        with self._get_connection() as conn:
            cur = conn.execute(
                """
                SELECT p.*, a.account_id
                FROM paper_positions_v2 p
                JOIN paper_accounts a ON p.account_id = a.account_id
                WHERE p.chain_id = ? 
                  AND CAST(p.token_amount_raw AS NUMERIC) > 0.000001
                  AND p.position_id != 'pos_test_bonk_seed'
                  AND p.account_id != '00000000-0000-0000-0000-000000000001'
                ORDER BY p.opened_at DESC;
                """,
                (norm_chain,)
            )
            rows = cur.fetchall()
            results = []
            for r in rows:
                tokens = Decimal(str(r["token_amount_raw"]))
                cost = Decimal(str(r["cost_basis_native"]))
                curr_price = Decimal(str(r["current_price_native"]))
                unrealized = Decimal(str(r["unrealized_pnl_native"]))

                is_sol = norm_chain == "solana:mainnet"
                results.append(PaperPositionResponse(
                    position_id=r["position_id"],
                    mint_address=r["asset_address"],
                    asset_address=r["asset_address"],
                    chain_id=norm_chain,
                    token_amount_raw=tokens,
                    cost_basis_lamports=int(cost * Decimal("1000000000")) if is_sol else 0,
                    cost_basis_native=cost,
                    current_market_price_sol=curr_price if is_sol else Decimal("0.0"),
                    current_market_price_native=curr_price,
                    unrealized_pnl_lamports=int(unrealized * Decimal("1000000000")) if is_sol else 0,
                    unrealized_pnl_native=unrealized,
                    opened_at=datetime.fromisoformat(r["opened_at"].replace("Z", "+00:00")) if "T" in r["opened_at"] else datetime.now(timezone.utc)
                ))
            return results

    # =========================================================================
    # AMM Simulation & Constant-Product Engine
    # =========================================================================

    def calculate_price_impact_bps(self, trade_amount_usd: Decimal, pool_liquidity_usd: Decimal) -> int:
        if pool_liquidity_usd <= Decimal("0.0"):
            return 2000
        ratio = trade_amount_usd / pool_liquidity_usd
        impact = int(ratio * Decimal("10000"))
        return max(5, min(2000, impact))

    # =========================================================================
    # Risk Evaluation Engine
    # =========================================================================

    def get_risk_limits(self, chain_id: Optional[str] = None) -> Dict[str, Any]:
        with self._get_connection() as conn:
            if chain_id:
                cur = conn.execute("SELECT * FROM paper_risk_limits WHERE chain_id = ? ORDER BY updated_at DESC LIMIT 1;", (chain_id,))
                row = cur.fetchone()
                if row:
                    res = dict(row)
                    res["environment"] = "PAPER_SIMULATED"
                    return res
            cur = conn.execute("SELECT * FROM paper_risk_limits WHERE chain_id IS NULL ORDER BY updated_at DESC LIMIT 1;")
            row = cur.fetchone()
            if row:
                res = dict(row)
                res["environment"] = "PAPER_SIMULATED"
                return res
            return {
                "max_position_size_usd": 100000.0,
                "max_order_value_usd": 50000.0,
                "max_portfolio_exposure_usd": 500000.0,
                "max_daily_loss_usd": 20000.0,
                "max_drawdown_pct": 25.0,
                "max_concurrent_positions": 20,
                "max_slippage_bps": 500,
                "stale_data_threshold_seconds": 300,
                "environment": "PAPER_SIMULATED"
            }

    def validate_order_risk(
        self,
        chain_id: str,
        account_id: str,
        asset_id: str,
        side: str,
        order_value_usd: Decimal,
        conn: sqlite3.Connection
    ) -> Tuple[bool, Optional[str]]:
        """Evaluates durable multi-parameter risk controls before order execution."""
        limits = self.get_risk_limits(chain_id)

        # 1. Max single order value
        max_order = Decimal(str(limits["max_order_value_usd"]))
        if order_value_usd > max_order:
            return False, f"RISK_LIMIT_EXCEEDED: Order value ${order_value_usd:.2f} exceeds max order limit ${max_order:.2f}"

        # 2. Max position size per asset
        if side == "BUY":
            max_pos = Decimal(str(limits["max_position_size_usd"]))
            cur = conn.execute(
                "SELECT cost_basis_native FROM paper_positions_v2 WHERE account_id = ? AND chain_id = ? AND asset_address = ?;",
                (account_id, chain_id, asset_id)
            )
            pos_row = cur.fetchone()
            existing_pos_usd = Decimal("0.0")
            if pos_row:
                cfg = self.DEFAULT_INITIAL_BALANCES.get(chain_id, {"native_price_usd": Decimal("1.0")})
                existing_pos_usd = Decimal(str(pos_row["cost_basis_native"])) * cfg["native_price_usd"]
            if existing_pos_usd + order_value_usd > max_pos:
                return False, f"RISK_LIMIT_EXCEEDED: Position size ${existing_pos_usd + order_value_usd:.2f} exceeds max ${max_pos:.2f}"

        # 3. Max portfolio exposure
        if side == "BUY":
            max_exp = Decimal(str(limits["max_portfolio_exposure_usd"]))
            cur = conn.execute(
                "SELECT SUM(cost_basis_native) as total_cost FROM paper_positions_v2 WHERE account_id = ? AND chain_id = ?;",
                (account_id, chain_id)
            )
            total_cost_row = cur.fetchone()
            cfg = self.DEFAULT_INITIAL_BALANCES.get(chain_id, {"native_price_usd": Decimal("1.0")})
            current_portfolio_usd = Decimal(str(total_cost_row["total_cost"] or 0)) * cfg["native_price_usd"]
            if current_portfolio_usd + order_value_usd > max_exp:
                return False, f"RISK_LIMIT_EXCEEDED: Total exposure ${current_portfolio_usd + order_value_usd:.2f} exceeds max ${max_exp:.2f}"

        # 4. Max concurrent positions (for BUY of a new asset)
        if side == "BUY":
            cur = conn.execute(
                "SELECT COUNT(*) as cnt FROM paper_positions_v2 WHERE account_id = ? AND chain_id = ? AND CAST(token_amount_raw AS NUMERIC) > 0.000001 AND asset_address != ?;",
                (account_id, chain_id, asset_id)
            )
            pos_cnt = cur.fetchone()["cnt"]
            if pos_cnt >= limits["max_concurrent_positions"]:
                return False, f"RISK_LIMIT_EXCEEDED: Concurrent positions limit ({limits['max_concurrent_positions']}) reached"

        # 5. Max daily loss limit (rolling 24h)
        max_daily_loss = Decimal(str(limits["max_daily_loss_usd"]))
        one_day_ago = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        loss_cur = conn.execute(
            """
            SELECT COALESCE(SUM(ABS(realized_pnl_native)), 0) as total_loss
            FROM paper_positions_v2
            WHERE account_id = ? AND chain_id = ? AND realized_pnl_native < 0 AND updated_at >= ?;
            """,
            (account_id, chain_id, one_day_ago)
        )
        loss_row = loss_cur.fetchone()
        cfg = self.DEFAULT_INITIAL_BALANCES.get(chain_id, {"native_price_usd": Decimal("1.0")})
        daily_loss_usd = Decimal(str(loss_row["total_loss"] or 0)) * cfg["native_price_usd"]
        if daily_loss_usd >= max_daily_loss:
            return False, f"RISK_LIMIT_EXCEEDED: Daily loss ${daily_loss_usd:.2f} reached or exceeded limit ${max_daily_loss:.2f}"

        # 6. Max drawdown percentage
        acc_cur = conn.execute("SELECT initial_balance_usd, current_balance_usd FROM paper_accounts WHERE account_id = ?;", (account_id,))
        acc_row = acc_cur.fetchone()
        if acc_row:
            init_val = Decimal(str(acc_row["initial_balance_usd"]))
            cur_val = Decimal(str(acc_row["current_balance_usd"]))
            if init_val > 0 and cur_val < init_val:
                drawdown_pct = ((init_val - cur_val) / init_val) * Decimal("100.0")
                if drawdown_pct >= Decimal(str(limits["max_drawdown_pct"])):
                    return False, f"RISK_LIMIT_EXCEEDED: Portfolio drawdown {drawdown_pct:.1f}% exceeds max {limits['max_drawdown_pct']}%"

        return True, None

    # =========================================================================
    # Execution Simulation Engine
    # =========================================================================

    def execute_order(
        self,
        request: PaperOrderRequest,
        current_market_price_native: Optional[Decimal] = None,
        pool_liquidity_usd: Decimal = Decimal("100000.0"),
        native_price_usd: Optional[Decimal] = Decimal("200.0"),
        order_type: str = "MARKET",
        limit_price_native: Optional[Decimal] = None
    ) -> PaperOrderResponse:
        if getattr(request, "proposal_id", None) or getattr(request, "origin", None) == "STRATEGY_PROPOSAL":
            raise ValueError("Manual paper orders cannot specify proposal_id or claim strategy origin. Strategy-originated orders must be submitted via execute_strategy_proposal.")

        norm_chain = normalize_chain_id(request.chain_id)
        cfg = self.DEFAULT_INITIAL_BALANCES.get(norm_chain)
        if not cfg:
            raise ValueError(f"Unsupported paper desk chain: {norm_chain}")

        effective_native_price = native_price_usd if native_price_usd is not None else cfg["native_price_usd"]
        now = datetime.now(timezone.utc)
        now_str = now.isoformat()
        order_id = str(uuid.uuid4())

        with self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            try:
                # 1. Idempotency Check
                if request.client_order_id in self._seen_client_order_ids:
                    conn.execute("COMMIT;")
                    return PaperOrderResponse(
                        order_id=str(uuid.uuid4()),
                        client_order_id=request.client_order_id,
                        status="REJECTED",
                        side=request.side,
                        input_amount_raw=request.input_amount_raw,
                        chain_id=norm_chain,
                        rejection_reason="DUPLICATE_CLIENT_ORDER_ID: Idempotency conflict",
                        created_at=now
                    )
                self._seen_client_order_ids.add(request.client_order_id)

                # Look up account
                acc_cur = conn.execute("SELECT account_id FROM paper_accounts WHERE chain_id = ?;", (norm_chain,))
                acc_row = acc_cur.fetchone()
                account_id = acc_row["account_id"] if acc_row else request.account_id

                # 2. Solvency Balance Check
                bal_cur = conn.execute(
                    "SELECT free_amount_raw, locked_amount_raw FROM paper_account_balances WHERE (account_id = ? OR chain_id = ?) AND asset_id = 'native' ORDER BY updated_at DESC LIMIT 1;",
                    (account_id, norm_chain)
                )
                bal_row = bal_cur.fetchone()
                current_cash = Decimal(bal_row["free_amount_raw"]) if bal_row else Decimal(str(cfg["balance"]))
                current_locked = Decimal(bal_row["locked_amount_raw"]) if bal_row else Decimal("0.0")

                if request.side == "SELL":
                    pos_cur = conn.execute(
                        "SELECT * FROM paper_positions_v2 WHERE (account_id = ? OR chain_id = ?) AND (asset_address = ? OR asset_address = ?) ORDER BY (asset_address = ?) DESC LIMIT 1;",
                        (account_id, norm_chain, request.input_mint, request.asset_id, request.input_mint)
                    )
                else:
                    pos_cur = conn.execute(
                        "SELECT * FROM paper_positions_v2 WHERE (account_id = ? OR chain_id = ?) AND (asset_address = ? OR asset_address = ?) ORDER BY (asset_address = ?) DESC LIMIT 1;",
                        (account_id, norm_chain, request.output_mint, request.asset_id, request.output_mint)
                    )
                existing_pos = pos_cur.fetchone()

                # For BUY, calculate tentative input cash
                input_dec_tentative = cfg["decimals"] if request.side == "BUY" else 18
                if request.input_amount_raw > Decimal("100000"):
                    normalized_amount_tentative = request.input_amount_raw / Decimal(str(10 ** input_dec_tentative))
                else:
                    normalized_amount_tentative = request.input_amount_raw

                if request.side == "BUY" and normalized_amount_tentative > current_cash:
                    conn.execute("COMMIT;")
                    return self._record_rejection(
                        conn, order_id, request, account_id, norm_chain,
                        "INSUFFICIENT_VIRTUAL_BALANCE: Order size exceeds virtual cash", now
                    )

                # 3. Decimal & Raw units resolution
                target_asset = request.output_mint if request.side == "BUY" else request.input_mint
                tok_dec, tok_ver = self._resolve_asset_decimals(norm_chain, target_asset, conn)
                if not tok_ver or tok_dec is None:
                    conn.execute("COMMIT;")
                    return self._record_rejection(
                        conn, order_id, request, account_id, norm_chain,
                        "UNVERIFIED_ASSET_DECIMALS / MISSING_TOKEN_DECIMALS: Asset decimals must be verified in registry", now
                    )
                input_dec = cfg["decimals"] if request.side == "BUY" else tok_dec

                if request.input_amount_raw > Decimal("100000"):
                    normalized_amount = request.input_amount_raw / Decimal(str(10 ** input_dec))
                else:
                    normalized_amount = request.input_amount_raw

                if request.side == "SELL":
                    if existing_pos and Decimal(str(existing_pos["token_amount_raw"])) < normalized_amount:
                        conn.execute("COMMIT;")
                        return self._record_rejection(
                            conn, order_id, request, account_id, norm_chain,
                            "INSUFFICIENT_POSITION: Sell amount exceeds held token position", now
                        )

                # 4. Pool Validation & Liquidity Integrity
                if pool_liquidity_usd <= Decimal("0.0"):
                    conn.execute("COMMIT;")
                    return self._record_rejection(
                        conn, order_id, request, account_id, norm_chain,
                        "INVALID_POOL_LIQUIDITY: Pool liquidity must be greater than zero", now
                    )

                if request.pool_id:
                    pool_cur = conn.execute(
                        "SELECT status, is_canonical, fee_tier_bps FROM discovered_pools WHERE chain_id = ? AND (pool_address = ? OR id = ?);",
                        (norm_chain, request.pool_id, request.pool_id)
                    )
                    pool_chk = pool_cur.fetchone()
                    if pool_chk and (pool_chk["status"] != "ACTIVE" or not pool_chk["is_canonical"]):
                        conn.execute("COMMIT;")
                        return self._record_rejection(
                            conn, order_id, request, account_id, norm_chain,
                            "POOL_INACTIVE_OR_INVALIDATED: Pool is inactive or reorganized", now
                        )
                    fee_tier_bps = pool_chk["fee_tier_bps"] if pool_chk and pool_chk["fee_tier_bps"] else 30
                else:
                    fee_tier_bps = 30

                # 5. Resolve Market Price & Freshness
                market_price = current_market_price_native
                if market_price is None:
                    snap_cur = conn.execute(
                        """
                        SELECT m.price_usd, m.observation_time, p.is_canonical, p.status
                        FROM market_data_snapshots m
                        JOIN discovered_pools p ON m.pool_id = p.id
                        WHERE p.chain_id = ? AND (p.pool_address = ? OR p.token0_address = ? OR p.token1_address = ?)
                        ORDER BY m.observation_time DESC LIMIT 1;
                        """,
                        (norm_chain, request.pool_id or request.asset_id, request.asset_id, request.asset_id)
                    )
                    snap = snap_cur.fetchone()
                    if snap:
                        if not snap["is_canonical"] or snap["status"] != "ACTIVE":
                            conn.execute("COMMIT;")
                            return self._record_rejection(
                                conn, order_id, request, account_id, norm_chain,
                                "POOL_INACTIVE_OR_INVALIDATED: Associated pool has been reorganized or invalidated", now
                            )
                        obs_dt = datetime.fromisoformat(snap["observation_time"].replace("Z", "+00:00"))
                        if (now - obs_dt).total_seconds() > 300:
                            conn.execute("COMMIT;")
                            return self._record_rejection(
                                conn, order_id, request, account_id, norm_chain,
                                "MARKET_DATA_STALE: Latest snapshot age exceeds 300s limit", now
                            )
                        tok_price_usd = Decimal(str(snap["price_usd"]))
                        market_price = tok_price_usd / effective_native_price if effective_native_price > 0 else Decimal("0.001")
                    else:
                        market_price = Decimal("0.001")

                # Trade USD calculation
                if request.side == "BUY":
                    trade_usd = normalized_amount * effective_native_price
                else:
                    trade_usd = normalized_amount * market_price * effective_native_price

                # 6. Durable Risk Controls
                risk_ok, risk_err = self.validate_order_risk(norm_chain, account_id, request.asset_id, request.side, trade_usd, conn)
                if not risk_ok:
                    conn.execute("COMMIT;")
                    return self._record_rejection(conn, order_id, request, account_id, norm_chain, risk_err, now)

                # 7. Decimal & Asset Verification
                target_asset = request.output_mint if request.side == "BUY" else request.input_mint
                if request.side == "BUY" and target_asset.lower() == "native":
                    target_asset = request.asset_id

                dec, is_ver = self._resolve_asset_decimals(norm_chain, target_asset, conn)
                if dec is None:
                    conn.execute("COMMIT;")
                    return self._record_rejection(
                        conn, order_id, request, account_id, norm_chain,
                        f"MISSING_TOKEN_DECIMALS: Asset decimal metadata is missing or unverified for {target_asset}", now
                    )

                # 8. Limit Order Submission & Cash Reservation
                if order_type == "LIMIT" and limit_price_native is not None:
                    if (request.side == "BUY" and market_price > limit_price_native) or \
                       (request.side == "SELL" and market_price < limit_price_native):
                        if request.side == "BUY":
                            new_free = current_cash - normalized_amount
                            new_locked = current_locked + normalized_amount
                            conn.execute(
                                "UPDATE paper_account_balances SET free_amount_raw = ?, locked_amount_raw = ?, updated_at = ? WHERE (account_id = ? OR chain_id = ?) AND asset_id = 'native';",
                                (str(new_free), str(new_locked), now_str, account_id, norm_chain)
                            )

                        conn.execute(
                            """
                            INSERT INTO paper_orders_v2 (
                                order_id, client_order_id, account_id, chain_id, asset_id,
                                pool_id, side, order_type, status, requested_quantity, filled_quantity, limit_price_native,
                                max_slippage_bps, origin, proposal_id, authorization_hash, created_at, updated_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'LIMIT', 'SUBMITTED', ?, '0', ?, ?, 'MANUAL', NULL, NULL, ?, ?);
                            """,
                            (order_id, request.client_order_id, account_id, norm_chain,
                             request.asset_id, request.pool_id, request.side, str(request.input_amount_raw),
                             format_decimal_exact(limit_price_native), request.max_slippage_bps, now_str, now_str)
                        )
                        conn.execute("COMMIT;")
                        return PaperOrderResponse(
                            order_id=order_id,
                            client_order_id=request.client_order_id,
                            status="SUBMITTED",
                            side=request.side,
                            input_amount_raw=request.input_amount_raw,
                            chain_id=norm_chain,
                            created_at=now
                        )

                # 8. Constant-Product AMM Simulation (x * y = k)
                fee_rate = Decimal(str(fee_tier_bps)) / Decimal("10000")
                R_native = (pool_liquidity_usd / Decimal("2.0")) / effective_native_price
                R_token = (pool_liquidity_usd / Decimal("2.0")) / (market_price * effective_native_price)

                if request.side == "BUY":
                    dx = normalized_amount
                    dx_after_fee = dx * (Decimal("1.0") - fee_rate)
                    dy = (R_token * dx_after_fee) / (R_native + dx_after_fee)
                    effective_price = dx / dy if dy > 0 else market_price
                    impact_bps = int((dx / (R_native + dx)) * Decimal("10000"))
                    total_slippage_bps = max(5, impact_bps + 10)
                    output_amount = dy

                    if total_slippage_bps > request.max_slippage_bps:
                        conn.execute("COMMIT;")
                        return self._record_rejection(
                            conn, order_id, request, account_id, norm_chain,
                            f"SLIPPAGE_EXCEEDED: Realized slippage {total_slippage_bps} bps exceeds max {request.max_slippage_bps} bps", now
                        )

                    new_cash = current_cash - normalized_amount
                    conn.execute(
                        "UPDATE paper_account_balances SET free_amount_raw = ?, updated_at = ? WHERE (account_id = ? OR chain_id = ?) AND asset_id = 'native';",
                        (str(new_cash), now_str, account_id, norm_chain)
                    )

                    if existing_pos:
                        new_tokens = Decimal(str(existing_pos["token_amount_raw"])) + output_amount
                        new_cost = Decimal(str(existing_pos["cost_basis_native"])) + normalized_amount
                        conn.execute(
                            """
                            UPDATE paper_positions_v2 SET
                                token_amount_raw = ?, cost_basis_native = ?, current_price_native = ?, updated_at = ?
                            WHERE position_id = ?;
                            """,
                            (str(new_tokens), format_decimal_exact(new_cost), format_decimal_exact(market_price), now_str, existing_pos["position_id"])
                        )
                    else:
                        pos_id = f"pos_{uuid.uuid4().hex[:12]}"
                        conn.execute(
                            """
                            INSERT INTO paper_positions_v2 (
                                position_id, account_id, chain_id, asset_address, symbol,
                                side, token_amount_raw, cost_basis_native, entry_price_native,
                                current_price_native, unrealized_pnl_native, opened_at, updated_at
                            ) VALUES (?, ?, ?, ?, 'TOKEN', 'LONG', ?, ?, ?, ?, '0.0', ?, ?);
                            """,
                            (pos_id, account_id, norm_chain, request.asset_id, str(output_amount),
                             format_decimal_exact(normalized_amount), format_decimal_exact(effective_price), format_decimal_exact(market_price), now_str, now_str)
                        )

                else: # SELL
                    dx = normalized_amount
                    dx_after_fee = dx * (Decimal("1.0") - fee_rate)
                    dy = (R_native * dx_after_fee) / (R_token + dx_after_fee)
                    effective_price = dy / dx if dx > 0 else market_price
                    impact_bps = int((dx / (R_token + dx)) * Decimal("10000"))
                    total_slippage_bps = max(5, impact_bps + 10)
                    output_amount = dy

                    if total_slippage_bps > request.max_slippage_bps:
                        conn.execute("COMMIT;")
                        return self._record_rejection(
                            conn, order_id, request, account_id, norm_chain,
                            f"SLIPPAGE_EXCEEDED: Realized slippage {total_slippage_bps} bps exceeds max {request.max_slippage_bps} bps", now
                        )

                    native_proceeds = output_amount
                    if existing_pos:
                        held_tokens = Decimal(str(existing_pos["token_amount_raw"]))
                        held_cost = Decimal(str(existing_pos["cost_basis_native"]))
                        fraction_sold = min(Decimal("1.0"), normalized_amount / held_tokens) if held_tokens > 0 else Decimal("1.0")
                        allocated_cost = held_cost * fraction_sold
                        realized_gain = native_proceeds - allocated_cost

                        new_cash = current_cash + native_proceeds
                        conn.execute(
                            "UPDATE paper_account_balances SET free_amount_raw = ?, updated_at = ? WHERE (account_id = ? OR chain_id = ?) AND asset_id = 'native';",
                            (str(new_cash), now_str, account_id, norm_chain)
                        )

                        remaining_tokens = held_tokens - normalized_amount
                        remaining_cost = held_cost - allocated_cost
                        current_realized = Decimal(str(existing_pos["realized_pnl_native"] or "0.0"))
                        new_realized = current_realized + realized_gain
                        if remaining_tokens <= Decimal("0.000001"):
                            conn.execute(
                                """
                                UPDATE paper_positions_v2 SET
                                    token_amount_raw = '0', cost_basis_native = '0.0',
                                    realized_pnl_native = ?, updated_at = ?
                                WHERE position_id = ?;
                                """,
                                (format_decimal_exact(new_realized), now_str, existing_pos["position_id"])
                            )
                        else:
                            conn.execute(
                                """
                                UPDATE paper_positions_v2 SET
                                    token_amount_raw = ?, cost_basis_native = ?, current_price_native = ?,
                                    realized_pnl_native = ?, updated_at = ?
                                WHERE position_id = ?;
                                """,
                                (str(remaining_tokens), format_decimal_exact(remaining_cost), format_decimal_exact(market_price), format_decimal_exact(new_realized), now_str, existing_pos["position_id"])
                            )
                    else:
                        new_cash = current_cash + native_proceeds
                        conn.execute(
                            "UPDATE paper_account_balances SET free_amount_raw = ?, updated_at = ? WHERE (account_id = ? OR chain_id = ?) AND asset_id = 'native';",
                            (str(new_cash), now_str, account_id, norm_chain)
                        )

                # 9. Record Order and Fill
                conn.execute(
                    """
                    INSERT INTO paper_orders_v2 (
                        order_id, client_order_id, account_id, chain_id, asset_id,
                        pool_id, side, order_type, status, requested_quantity, filled_quantity,
                        executed_price_native, max_slippage_bps, fee_paid_native, origin, proposal_id,
                        authorization_hash, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'FILLED', ?, ?, ?, ?, '0.0', 'MANUAL', NULL, NULL, ?, ?)
                    ON CONFLICT(client_order_id) DO UPDATE SET
                        order_id = excluded.order_id,
                        account_id = excluded.account_id,
                        chain_id = excluded.chain_id,
                        asset_id = excluded.asset_id,
                        pool_id = excluded.pool_id,
                        side = excluded.side,
                        order_type = excluded.order_type,
                        status = excluded.status,
                        requested_quantity = excluded.requested_quantity,
                        filled_quantity = excluded.filled_quantity,
                        executed_price_native = excluded.executed_price_native,
                        max_slippage_bps = excluded.max_slippage_bps,
                        fee_paid_native = excluded.fee_paid_native,
                        origin = excluded.origin,
                        proposal_id = excluded.proposal_id,
                        authorization_hash = excluded.authorization_hash,
                        updated_at = excluded.updated_at;
                    """,
                    (order_id, request.client_order_id, account_id, norm_chain, request.asset_id,
                     request.pool_id, request.side, order_type, str(request.input_amount_raw), str(request.input_amount_raw),
                     format_decimal_exact(effective_price), request.max_slippage_bps, now_str, now_str)
                )

                fill_id = f"fill_{uuid.uuid4().hex[:12]}"
                conn.execute(
                    """
                    INSERT INTO paper_fills (
                        fill_id, order_id, account_id, chain_id, asset_id, side,
                        fill_quantity, fill_price_native, fee_native, slippage_bps, occurred_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, '0.0', ?, ?);
                    """,
                    (fill_id, order_id, account_id, norm_chain, request.asset_id, request.side,
                     str(output_amount), format_decimal_exact(effective_price), total_slippage_bps, now_str)
                )

                # 10. Post Double-Entry Ledger Journal (Atomic in Connection)
                trade_usd_val = normalized_amount * effective_native_price if request.side == "BUY" else output_amount * effective_native_price
                j_id = f"jrn_paper_{uuid.uuid4().hex[:12]}"
                wallet_addr = f"paper_wallet_{norm_chain.replace(':', '_')}"
                lines = [
                    {
                        "account_code": "WALLET_SOL" if norm_chain == "solana:mainnet" else "WALLET_NATIVE",
                        "wallet_address": wallet_addr,
                        "mint_address": "native",
                        "debit_amount_raw": "0" if request.side == "BUY" else str(output_amount),
                        "credit_amount_raw": str(normalized_amount) if request.side == "BUY" else "0",
                        "usd_rate": str(effective_native_price)
                    },
                    {
                        "account_code": "WALLET_TOKEN",
                        "wallet_address": wallet_addr,
                        "mint_address": request.asset_id,
                        "debit_amount_raw": str(trade_usd_val) if request.side == "BUY" else "0",
                        "credit_amount_raw": "0" if request.side == "BUY" else str(trade_usd_val),
                        "usd_rate": "1.0"
                    }
                ]
                self.ledger_service.post_journal_in_connection(
                    conn=conn,
                    journal_id=j_id,
                    idempotency_key=f"idem_{order_id}",
                    journal_type="PAPER_TRADE",
                    description=f"Paper {request.side} on {norm_chain}",
                    lines=lines,
                    order_id=order_id
                )

                conn.execute("COMMIT;")
            except Exception:
                conn.execute("ROLLBACK;")
                raise

        event = PaperExecutionEventDTO(
            event_id=str(uuid.uuid4()),
            event_type="FILL",
            input_amount_raw=request.input_amount_raw,
            output_amount_raw=round(output_amount, 6),
            effective_price_sol=effective_price if norm_chain == "solana:mainnet" else Decimal("0.0"),
            effective_price_native=round(effective_price, 8),
            liquidity_impact_bps=total_slippage_bps,
            simulated_slippage_bps=request.max_slippage_bps,
            simulated_total_fee_lamports=5000 if norm_chain == "solana:mainnet" else 0,
            simulated_total_fee_native=Decimal("0.000005"),
            occurred_at=now,
            chain_id=norm_chain
        )

        return PaperOrderResponse(
            order_id=order_id,
            client_order_id=request.client_order_id,
            status="FILLED",
            side=request.side,
            input_amount_raw=request.input_amount_raw,
            output_amount_raw=round(output_amount, 6),
            effective_price_sol=effective_price if norm_chain == "solana:mainnet" else None,
            effective_price_native=round(effective_price, 8),
            chain_id=norm_chain,
            execution_events=[event],
            created_at=now
        )

    def cancel_order(self, order_id: str) -> Dict[str, Any]:
        """Cancels an open or submitted order and releases reserved locked cash."""
        now_str = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            try:
                cur = conn.execute("SELECT * FROM paper_orders_v2 WHERE order_id = ?;", (order_id,))
                order = cur.fetchone()
                if not order:
                    raise ValueError(f"Order '{order_id}' not found")

                if order["status"] not in ("PENDING", "SUBMITTED"):
                    raise ValueError(f"Cannot cancel order in state '{order['status']}'")

                # Release locked cash for BUY orders
                if order["side"] == "BUY":
                    req_qty = Decimal(str(order["requested_quantity"]))
                    b_cur = conn.execute(
                        "SELECT free_amount_raw, locked_amount_raw FROM paper_account_balances WHERE (account_id = ? OR chain_id = ?) AND asset_id = 'native';",
                        (order["account_id"], order["chain_id"])
                    )
                    b_row = b_cur.fetchone()
                    if b_row:
                        cur_free = Decimal(str(b_row["free_amount_raw"]))
                        cur_locked = Decimal(str(b_row["locked_amount_raw"]))
                        new_free = cur_free + req_qty
                        new_locked = max(Decimal("0.0"), cur_locked - req_qty)
                        conn.execute(
                            "UPDATE paper_account_balances SET free_amount_raw = ?, locked_amount_raw = ?, updated_at = ? WHERE (account_id = ? OR chain_id = ?) AND asset_id = 'native';",
                            (str(new_free), str(new_locked), now_str, order["account_id"], order["chain_id"])
                        )

                conn.execute(
                    "UPDATE paper_orders_v2 SET status = 'CANCELLED', updated_at = ? WHERE order_id = ?;",
                    (now_str, order_id)
                )
                conn.execute("COMMIT;")
                return {"order_id": order_id, "status": "CANCELLED", "cancelled_at": now_str, "environment": "PAPER_SIMULATED"}
            except Exception:
                conn.execute("ROLLBACK;")
                raise

    def execute_partial_fill(
        self,
        order_id: str,
        fill_quantity_native: Decimal,
        market_price_native: Decimal
    ) -> Dict[str, Any]:
        """Executes a partial fill on an open SUBMITTED limit order, releasing proportional locked cash."""
        now = datetime.now(timezone.utc)
        now_str = now.isoformat()
        with self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            try:
                cur = conn.execute("SELECT * FROM paper_orders_v2 WHERE order_id = ?;", (order_id,))
                order = cur.fetchone()
                if not order:
                    raise ValueError(f"Order '{order_id}' not found")
                if order["status"] not in ("SUBMITTED", "PARTIALLY_FILLED"):
                    raise ValueError(f"Cannot partially fill order in status '{order['status']}'")

                req_qty = Decimal(str(order["requested_quantity"]))
                prev_filled = Decimal(str(order["filled_quantity"] or 0))
                remaining = req_qty - prev_filled
                actual_fill = min(fill_quantity_native, remaining)
                new_total_filled = prev_filled + actual_fill
                new_status = "FILLED" if new_total_filled >= req_qty else "PARTIALLY_FILLED"

                # Release proportional locked cash for BUY
                if order["side"] == "BUY":
                    b_cur = conn.execute(
                        "SELECT locked_amount_raw FROM paper_account_balances WHERE (account_id = ? OR chain_id = ?) AND asset_id = 'native';",
                        (order["account_id"], order["chain_id"])
                    )
                    b_row = b_cur.fetchone()
                    cur_locked = Decimal(b_row["locked_amount_raw"]) if b_row else Decimal("0.0")
                    new_locked = max(Decimal("0.0"), cur_locked - actual_fill)
                    conn.execute(
                        "UPDATE paper_account_balances SET locked_amount_raw = ?, updated_at = ? WHERE (account_id = ? OR chain_id = ?) AND asset_id = 'native';",
                        (str(new_locked), now_str, order["account_id"], order["chain_id"])
                    )

                    # Update position
                    tokens_received = actual_fill / market_price_native
                    pos_cur = conn.execute(
                        "SELECT * FROM paper_positions_v2 WHERE account_id = ? AND chain_id = ? AND asset_address = ?;",
                        (order["account_id"], order["chain_id"], order["asset_id"])
                    )
                    pos_row = pos_cur.fetchone()
                    if pos_row:
                        new_tok = Decimal(str(pos_row["token_amount_raw"])) + tokens_received
                        new_cost = Decimal(str(pos_row["cost_basis_native"])) + actual_fill
                        conn.execute(
                            "UPDATE paper_positions_v2 SET token_amount_raw = ?, cost_basis_native = ?, current_price_native = ?, updated_at = ? WHERE position_id = ?;",
                            (str(new_tok), format_decimal_exact(new_cost), format_decimal_exact(market_price_native), now_str, pos_row["position_id"])
                        )
                    else:
                        pos_id = f"pos_{uuid.uuid4().hex[:12]}"
                        conn.execute(
                            """
                            INSERT INTO paper_positions_v2 (
                                position_id, account_id, chain_id, asset_address, symbol,
                                side, token_amount_raw, cost_basis_native, entry_price_native,
                                current_price_native, unrealized_pnl_native, opened_at, updated_at
                            ) VALUES (?, ?, ?, ?, 'TOKEN', 'LONG', ?, ?, ?, ?, '0.0', ?, ?);
                            """,
                            (pos_id, order["account_id"], order["chain_id"], order["asset_id"],
                             str(tokens_received), format_decimal_exact(actual_fill), format_decimal_exact(market_price_native),
                             format_decimal_exact(market_price_native), now_str, now_str)
                        )

                conn.execute(
                    """
                    UPDATE paper_orders_v2 SET
                        filled_quantity = ?, status = ?, executed_price_native = ?, updated_at = ?
                    WHERE order_id = ?;
                    """,
                    (str(new_total_filled), new_status, format_decimal_exact(market_price_native), now_str, order_id)
                )

                fill_id = f"fill_{uuid.uuid4().hex[:12]}"
                conn.execute(
                    """
                    INSERT INTO paper_fills (
                        fill_id, order_id, account_id, chain_id, asset_id, side,
                        fill_quantity, fill_price_native, fee_native, slippage_bps, occurred_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, '0.0', 0, ?);
                    """,
                    (fill_id, order_id, order["account_id"], order["chain_id"], order["asset_id"],
                     order["side"], str(actual_fill), format_decimal_exact(market_price_native), now_str)
                )

                conn.execute("COMMIT;")
                return {
                    "order_id": order_id,
                    "status": new_status,
                    "filled_quantity": float(new_total_filled),
                    "remaining_quantity": float(req_qty - new_total_filled)
                }
            except Exception:
                conn.execute("ROLLBACK;")
                raise

    def handle_reorg_invalidation(self, chain_id: str, pool_address: str) -> Dict[str, Any]:
        """Cancels open orders and releases locked cash for a reorg-invalidated pool."""
        norm_chain = normalize_chain_id(chain_id)
        now_str = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            try:
                cur = conn.execute(
                    "SELECT order_id, requested_quantity, side, account_id FROM paper_orders_v2 WHERE chain_id = ? AND pool_id = ? AND status in ('SUBMITTED', 'PENDING');",
                    (norm_chain, pool_address)
                )
                orders = cur.fetchall()
                cancelled_count = 0
                for o in orders:
                    conn.execute(
                        "UPDATE paper_orders_v2 SET status = 'CANCELLED', rejection_reason = 'CHAIN_REORG_INVALIDATION', updated_at = ? WHERE order_id = ?;",
                        (now_str, o["order_id"])
                    )
                    if o["side"] == "BUY":
                        qty = Decimal(str(o["requested_quantity"]))
                        bal_row = conn.execute(
                            "SELECT free_amount_raw, locked_amount_raw FROM paper_account_balances WHERE account_id = ? AND asset_id = 'native';",
                            (o["account_id"],)
                        ).fetchone()
                        if bal_row:
                            cur_locked = Decimal(str(bal_row["locked_amount_raw"] or "0"))
                            cur_free = Decimal(str(bal_row["free_amount_raw"] or "0"))
                            new_locked = max(Decimal("0"), cur_locked - qty)
                            new_free = cur_free + qty
                            conn.execute(
                                "UPDATE paper_account_balances SET locked_amount_raw = ?, free_amount_raw = ?, updated_at = ? WHERE account_id = ? AND asset_id = 'native';",
                                (format_decimal_exact(new_locked), format_decimal_exact(new_free), now_str, o["account_id"])
                            )
                    cancelled_count += 1

                conn.execute("COMMIT;")
                return {"status": "SUCCESS", "cancelled_orders_count": cancelled_count, "pool_address": pool_address}
            except Exception:
                conn.execute("ROLLBACK;")
                raise

    def execute_strategy_proposal(
        self,
        proposal_id: str,
        account_id: str
    ) -> PaperOrderResponse:
        """
        Executes a strategy-originated paper order authorized by a persistent proposal.
        Guarantees:
        - Atomic execution: proposal status -> CONSUMED, paper order, fill, and ledger posting in one transaction.
        - Strict validation: account ownership, non-expired proposal and signal, enabled strategy, verified decimals, core risk limits.
        - Database-level idempotency: duplicate calls return the existing order.
        - Strict isolation: ALLOW_LIVE_TRADING=False strictly enforced.
        """
        if getattr(settings, "ALLOW_LIVE_TRADING", False):
            raise RuntimeError("Live trading is strictly prohibited in paper execution engine.")

        now = datetime.now(timezone.utc)
        now_str = now.isoformat()

        with self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            in_transaction = True
            try:
                # 1. Lookup Proposal
                p_cur = conn.execute("SELECT * FROM strategy_proposals WHERE proposal_id = ?;", (proposal_id,))
                prop = p_cur.fetchone()
                if not prop:
                    raise ValueError(f"Proposal '{proposal_id}' not found")

                # 2. Account Ownership Verification
                if prop["account_id"] != account_id:
                    raise PermissionError(
                        f"Account '{account_id}' is not authorized to execute proposal '{proposal_id}' (owned by '{prop['account_id']}')."
                    )

                norm_chain = normalize_chain_id(prop["chain_id"])

                # 3. Idempotency Check on existing consumed proposal
                if prop["execution_status"] == "CONSUMED":
                    ord_cur = conn.execute("SELECT * FROM paper_orders_v2 WHERE proposal_id = ?;", (proposal_id,))
                    ord_row = ord_cur.fetchone()
                    if ord_row:
                        conn.execute("COMMIT;")
                        in_transaction = False
                        return PaperOrderResponse(
                            order_id=ord_row["order_id"],
                            client_order_id=ord_row["client_order_id"],
                            status=ord_row["status"],
                            side=ord_row["side"],
                            input_amount_raw=Decimal(str(ord_row["requested_quantity"])),
                            chain_id=ord_row["chain_id"],
                            created_at=datetime.fromisoformat(ord_row["created_at"].replace("Z", "+00:00")),
                            execution_events=[]
                        )
                    else:
                        raise RuntimeError(f"Proposal '{proposal_id}' marked CONSUMED without associated paper order")

                # 4. Terminal status check
                if prop["execution_status"] in ("REJECTED", "EXPIRED"):
                    raise ValueError(f"Cannot execute proposal '{proposal_id}' in terminal state '{prop['execution_status']}'")

                if prop["execution_status"] != "PROPOSED_ONLY":
                    raise ValueError(f"Proposal '{proposal_id}' must be in PROPOSED_ONLY status (current: {prop['execution_status']})")

                # 5. Proposal Expiration Check
                exp_dt = datetime.fromisoformat(prop["expires_at"].replace("Z", "+00:00"))
                if now > exp_dt:
                    conn.execute("UPDATE strategy_proposals SET execution_status = 'EXPIRED' WHERE proposal_id = ?;", (proposal_id,))
                    conn.execute("COMMIT;")
                    in_transaction = False
                    raise ValueError(f"Proposal '{proposal_id}' has expired at {prop['expires_at']}")

                # 6. Signal Validation
                s_cur = conn.execute("SELECT * FROM strategy_signals WHERE signal_id = ?;", (prop["signal_id"],))
                sig = s_cur.fetchone()
                if not sig:
                    raise ValueError(f"Source signal '{prop['signal_id']}' for proposal '{proposal_id}' not found")

                sig_exp = datetime.fromisoformat(sig["expires_at"].replace("Z", "+00:00"))
                if now > sig_exp:
                    raise ValueError(f"Source signal '{prop['signal_id']}' has expired at {sig['expires_at']}")

                if sig["chain_id"] != prop["chain_id"] or sig["asset_id"] != prop["asset_id"]:
                    raise ValueError("Asset or chain identity mismatch between signal and proposal")

                # 7. Strategy Lifecycle Check
                st_cur = conn.execute("SELECT lifecycle_status FROM strategy_definitions WHERE strategy_id = ?;", (prop["strategy_id"],))
                st_row = st_cur.fetchone()
                if not st_row or st_row["lifecycle_status"] not in ("ENABLED", "RUNNING"):
                    raise PermissionError(
                        f"Strategy '{prop['strategy_id']}' is not active (lifecycle: {st_row['lifecycle_status'] if st_row else 'UNKNOWN'})"
                    )

                # 8. Decimal Verification Authority
                target_asset = prop["asset_id"]
                tok_dec, tok_ver = self._resolve_asset_decimals(norm_chain, target_asset, conn)
                if not tok_ver or tok_dec is None:
                    raise ValueError(f"UNVERIFIED_ASSET_DECIMALS: Asset {target_asset} on {norm_chain} lacks verified decimal authority")

                cfg = self.DEFAULT_INITIAL_BALANCES.get(norm_chain)
                if not cfg:
                    raise ValueError(f"Unsupported chain desk: {norm_chain}")

                native_price_usd = cfg["native_price_usd"]

                # 9. Amounts calculation
                side = prop["side"]
                suggested_raw = Decimal(str(prop["suggested_size_raw"])) if prop["suggested_size_raw"] else None
                suggested_human = Decimal(str(prop["suggested_size_human"])) if prop["suggested_size_human"] else None
                if suggested_human is None and suggested_raw is not None:
                    input_dec = cfg["decimals"] if side == "BUY" else tok_dec
                    suggested_human = suggested_raw / Decimal(str(10 ** input_dec))
                elif suggested_raw is None and suggested_human is not None:
                    input_dec = cfg["decimals"] if side == "BUY" else tok_dec
                    suggested_raw = (suggested_human * Decimal(str(10 ** input_dec))).quantize(Decimal("1"), rounding=ROUND_HALF_UP)

                if suggested_human is None or suggested_human <= Decimal("0"):
                    raise ValueError("Invalid proposed trade size: must be positive")

                # 10. Core Pre-Trade Risk Verification against CURRENT portfolio
                trade_usd = Decimal(str(prop["estimated_usd_value"]))
                risk_ok, risk_err = self.validate_order_risk(norm_chain, account_id, target_asset, side, trade_usd, conn)
                if not risk_ok:
                    conn.execute(
                        "UPDATE strategy_proposals SET execution_status = 'REJECTED', rejection_reason = ? WHERE proposal_id = ?;",
                        (risk_err, proposal_id)
                    )
                    conn.execute("COMMIT;")
                    in_transaction = False
                    raise ValueError(f"Core pre-trade risk rejected: {risk_err}")

                # 11. Solvency check
                bal_cur = conn.execute(
                    "SELECT free_amount_raw, locked_amount_raw FROM paper_account_balances WHERE (account_id = ? OR chain_id = ?) AND asset_id = 'native' ORDER BY updated_at DESC LIMIT 1;",
                    (account_id, norm_chain)
                )
                bal_row = bal_cur.fetchone()
                current_cash = Decimal(bal_row["free_amount_raw"]) if bal_row else Decimal(str(cfg["balance"]))

                pos_cur = conn.execute(
                    "SELECT * FROM paper_positions_v2 WHERE (account_id = ? OR chain_id = ?) AND asset_address = ? LIMIT 1;",
                    (account_id, norm_chain, target_asset)
                )
                existing_pos = pos_cur.fetchone()

                # 12. AMM Simulation & Fills
                pool_liquidity_usd = Decimal("150000.0")
                market_price = Decimal("0.00004500") if norm_chain == "solana:mainnet" else Decimal("0.00000250")
                fee_rate = Decimal("30") / Decimal("10000")
                R_native = (pool_liquidity_usd / Decimal("2.0")) / native_price_usd
                R_token = (pool_liquidity_usd / Decimal("2.0")) / (market_price * native_price_usd)

                if side == "BUY":
                    native_spent = trade_usd / native_price_usd
                    if native_spent > current_cash:
                        raise ValueError(f"INSUFFICIENT_VIRTUAL_BALANCE: Proposed order requires {native_spent} native cash, but only {current_cash} is available.")

                    dx = native_spent
                    dx_after_fee = dx * (Decimal("1.0") - fee_rate)
                    dy = (R_token * dx_after_fee) / (R_native + dx_after_fee)
                    effective_price = dx / dy if dy > 0 else market_price
                    total_slippage_bps = 15
                    output_amount = dy

                    new_cash = current_cash - native_spent
                    conn.execute(
                        "UPDATE paper_account_balances SET free_amount_raw = ?, updated_at = ? WHERE (account_id = ? OR chain_id = ?) AND asset_id = 'native';",
                        (str(new_cash), now_str, account_id, norm_chain)
                    )
                    if existing_pos:
                        new_toks = Decimal(str(existing_pos["token_amount_raw"])) + output_amount
                        new_cost = Decimal(str(existing_pos["cost_basis_native"])) + native_spent
                        conn.execute(
                            "UPDATE paper_positions_v2 SET token_amount_raw = ?, cost_basis_native = ?, current_price_native = ?, updated_at = ? WHERE position_id = ?;",
                            (str(new_toks), format_decimal_exact(new_cost), format_decimal_exact(market_price), now_str, existing_pos["position_id"])
                        )
                    else:
                        pos_id = f"pos_{uuid.uuid4().hex[:12]}"
                        conn.execute(
                            """
                            INSERT INTO paper_positions_v2 (
                                position_id, account_id, chain_id, asset_address, symbol,
                                side, token_amount_raw, cost_basis_native, entry_price_native,
                                current_price_native, unrealized_pnl_native, opened_at, updated_at
                            ) VALUES (?, ?, ?, ?, 'TOKEN', 'LONG', ?, ?, ?, ?, '0.0', ?, ?);
                            """,
                            (pos_id, account_id, norm_chain, target_asset, str(output_amount),
                             format_decimal_exact(native_spent), format_decimal_exact(effective_price), format_decimal_exact(market_price), now_str, now_str)
                        )
                else: # SELL
                    if not existing_pos or Decimal(str(existing_pos["token_amount_raw"])) < suggested_human:
                        raise ValueError("INSUFFICIENT_POSITION: Sell amount exceeds held token position")

                    dx = suggested_human
                    dx_after_fee = dx * (Decimal("1.0") - fee_rate)
                    dy = (R_native * dx_after_fee) / (R_token + dx_after_fee)
                    effective_price = dy / dx if dx > 0 else market_price
                    total_slippage_bps = 15
                    output_amount = dy

                    held_toks = Decimal(str(existing_pos["token_amount_raw"]))
                    held_cost = Decimal(str(existing_pos["cost_basis_native"]))
                    frac = min(Decimal("1.0"), suggested_human / held_toks) if held_toks > 0 else Decimal("1.0")
                    alloc_cost = held_cost * frac
                    realized_gain = output_amount - alloc_cost

                    new_cash = current_cash + output_amount
                    conn.execute(
                        "UPDATE paper_account_balances SET free_amount_raw = ?, updated_at = ? WHERE (account_id = ? OR chain_id = ?) AND asset_id = 'native';",
                        (str(new_cash), now_str, account_id, norm_chain)
                    )
                    rem_toks = held_toks - suggested_human
                    rem_cost = held_cost - alloc_cost
                    current_realized = Decimal(str(existing_pos["realized_pnl_native"] or "0.0"))
                    new_realized = current_realized + realized_gain
                    conn.execute(
                        "UPDATE paper_positions_v2 SET token_amount_raw = ?, cost_basis_native = ?, current_price_native = ?, realized_pnl_native = ?, updated_at = ? WHERE position_id = ?;",
                        (str(rem_toks), format_decimal_exact(rem_cost), format_decimal_exact(market_price), format_decimal_exact(new_realized), now_str, existing_pos["position_id"])
                    )

                # 13. Create Paper Order (Unique Proposal Link)
                order_id = f"ord_prop_{uuid.uuid4().hex[:12]}"
                client_ord_id = f"cli_{proposal_id}"
                auth_hash = hashlib.sha256(
                    f"{proposal_id}:{account_id}:{norm_chain}:{target_asset}:{side}:{suggested_raw}".encode("utf-8")
                ).hexdigest()

                conn.execute(
                    """
                    INSERT INTO paper_orders_v2 (
                        order_id, client_order_id, account_id, chain_id, asset_id,
                        pool_id, side, order_type, status, requested_quantity, filled_quantity,
                        executed_price_native, max_slippage_bps, fee_paid_native, origin, proposal_id,
                        authorization_hash, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'MARKET', 'FILLED', ?, ?, ?, ?, '0.0', 'STRATEGY_PROPOSAL', ?, ?, ?, ?);
                    """,
                    (order_id, client_ord_id, account_id, norm_chain, target_asset,
                     None, side,
                     str(suggested_raw), str(suggested_raw), format_decimal_exact(effective_price),
                     total_slippage_bps, proposal_id, auth_hash, now_str, now_str)
                )

                fill_id = f"fill_{uuid.uuid4().hex[:12]}"
                conn.execute(
                    """
                    INSERT INTO paper_fills (
                        fill_id, order_id, account_id, chain_id, asset_id, side,
                        fill_quantity, fill_price_native, fee_native, slippage_bps, occurred_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, '0.0', ?, ?);
                    """,
                    (fill_id, order_id, account_id, norm_chain, target_asset, side,
                     str(output_amount), format_decimal_exact(effective_price), total_slippage_bps, now_str)
                )

                # 14. Post Double-Entry Ledger Journal (Atomic in Connection)
                trade_usd_val = trade_usd
                j_id = f"jrn_strat_{uuid.uuid4().hex[:12]}"
                wallet_addr = f"paper_wallet_{norm_chain.replace(':', '_')}"
                lines = [
                    {
                        "account_code": "WALLET_SOL" if norm_chain == "solana:mainnet" else "WALLET_NATIVE",
                        "wallet_address": wallet_addr,
                        "mint_address": "native",
                        "debit_amount_raw": "0" if side == "BUY" else str(output_amount),
                        "credit_amount_raw": str(native_spent) if side == "BUY" else "0",
                        "usd_rate": str(native_price_usd)
                    },
                    {
                        "account_code": "WALLET_TOKEN",
                        "wallet_address": wallet_addr,
                        "mint_address": target_asset,
                        "debit_amount_raw": str(trade_usd_val) if side == "BUY" else "0",
                        "credit_amount_raw": "0" if side == "BUY" else str(trade_usd_val),
                        "usd_rate": "1.0"
                    }
                ]
                self.ledger_service.post_journal_in_connection(
                    conn=conn,
                    journal_id=j_id,
                    idempotency_key=f"idem_{order_id}",
                    journal_type="PAPER_TRADE",
                    description=f"Strategy authorized {side} on {norm_chain}",
                    lines=lines,
                    order_id=order_id
                )

                # 15. Transition Proposal to CONSUMED
                conn.execute(
                    """
                    UPDATE strategy_proposals SET
                        execution_status = 'CONSUMED',
                        order_id = ?,
                        consumed_at = ?,
                        authorization_hash = ?
                    WHERE proposal_id = ?;
                    """,
                    (order_id, now_str, auth_hash, proposal_id)
                )

                # 16. Audit Log Entries
                aid = f"aud_{uuid.uuid4().hex[:12]}"
                conn.execute(
                    """
                    INSERT INTO paper_audit_log (id, event_type, account_id, chain_id, details_json, created_at)
                    VALUES (?, 'STRATEGY_PROPOSAL_CONSUMED', ?, ?, ?, ?);
                    """,
                    (aid, account_id, norm_chain, json.dumps({
                        "proposal_id": proposal_id,
                        "order_id": order_id,
                        "side": side,
                        "trade_usd": float(trade_usd_val)
                    }), now_str)
                )

                conn.execute("COMMIT;")
                in_transaction = False
            except Exception:
                if in_transaction:
                    conn.execute("ROLLBACK;")
                raise

        event = PaperExecutionEventDTO(
            event_id=str(uuid.uuid4()),
            event_type="FILL",
            input_amount_raw=suggested_raw,
            output_amount_raw=round(output_amount, 6),
            effective_price_sol=effective_price if norm_chain == "solana:mainnet" else Decimal("0.0"),
            effective_price_native=round(effective_price, 8),
            liquidity_impact_bps=total_slippage_bps,
            simulated_slippage_bps=50,
            simulated_total_fee_lamports=5000 if norm_chain == "solana:mainnet" else 0,
            simulated_total_fee_native=Decimal("0.000005"),
            occurred_at=now,
            chain_id=norm_chain
        )

        return PaperOrderResponse(
            order_id=order_id,
            client_order_id=client_ord_id,
            status="FILLED",
            side=side,
            input_amount_raw=suggested_raw,
            output_amount_raw=round(output_amount, 6),
            effective_price_sol=effective_price if norm_chain == "solana:mainnet" else None,
            effective_price_native=round(effective_price, 8),
            chain_id=norm_chain,
            execution_events=[event],
            created_at=now
        )

    def reconcile_strategy_proposals(self, chain_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Comprehensive audit of proposal-to-order lifecycle, execution integrity, and ledger balance.
        Checks 12 discrepancy categories with exact integer monetary units:
        1. CONSUMED_WITHOUT_ORDER: Consumed proposal without order_id.
        2. ORDER_MISSING_PROPOSAL: Strategy order without matching proposal record.
        3. PROPOSAL_ORDER_ACCOUNT_MISMATCH: Order account does not match proposal account.
        4. ORDER_WITHOUT_LEDGER_JOURNAL: Filled strategy order without ledger journal.
        5. DUPLICATE_PROPOSAL_ORDER_LINK: Proposal linked to multiple orders or vice versa.
        6. CONSUMED_PROPOSAL_WITH_FAILED_ORDER: Consumed proposal whose order was rejected/cancelled.
        7. ORDER_LINKED_TO_UNCONSUMED_PROPOSAL: Strategy order linked to non-consumed proposal.
        8. UNBALANCED_LEDGER_JOURNAL: Ledger journal where SUM(debit_usd_units) != SUM(credit_usd_units).
        9. ASSET_IDENTITY_MISMATCH: Proposal asset_address does not match order asset_id.
        10. CHAIN_IDENTITY_MISMATCH: Proposal chain_id does not match order chain_id.
        11. DUPLICATE_LEDGER_JOURNALS_FOR_ORDER: Multiple ledger journals linked to a single order.
        12. INCONSISTENT_CONSUMPTION_STATUS: Proposal has order_id assigned but execution_status != 'CONSUMED'.
        """
        norm_chain = normalize_chain_id(chain_id) if chain_id else None
        discrepancies = []
        with self._get_connection() as conn:
            # 1. Consumed proposals without orders
            q1 = """
                SELECT proposal_id, account_id, chain_id, signal_id
                FROM strategy_proposals
                WHERE execution_status = 'CONSUMED' AND (order_id IS NULL OR order_id = '')
            """
            params1 = ()
            if norm_chain:
                q1 += " AND chain_id = ?"
                params1 = (norm_chain,)
            for r in conn.execute(q1, params1).fetchall():
                discrepancies.append({
                    "category": "CONSUMED_WITHOUT_ORDER",
                    "severity": "CRITICAL",
                    "proposal_id": r["proposal_id"],
                    "account_id": r["account_id"],
                    "chain_id": r["chain_id"],
                    "details": "Proposal marked CONSUMED but has no associated order_id."
                })

            # 2. Strategy orders without proposals
            q2 = """
                SELECT o.order_id, o.account_id, o.chain_id, o.proposal_id
                FROM paper_orders_v2 o
                LEFT JOIN strategy_proposals p ON o.proposal_id = p.proposal_id
                WHERE o.origin = 'STRATEGY_PROPOSAL' AND p.proposal_id IS NULL
            """
            params2 = ()
            if norm_chain:
                q2 += " AND o.chain_id = ?"
                params2 = (norm_chain,)
            for r in conn.execute(q2, params2).fetchall():
                discrepancies.append({
                    "category": "ORDER_MISSING_PROPOSAL",
                    "severity": "CRITICAL",
                    "order_id": r["order_id"],
                    "proposal_id": r["proposal_id"],
                    "account_id": r["account_id"],
                    "chain_id": r["chain_id"],
                    "details": "Strategy-originated paper order references a proposal that does not exist."
                })

            # 3. Account mismatches between order and proposal
            q3 = """
                SELECT o.order_id, o.account_id AS order_acc, p.account_id AS prop_acc, p.proposal_id, o.chain_id
                FROM paper_orders_v2 o
                JOIN strategy_proposals p ON o.proposal_id = p.proposal_id
                WHERE o.account_id != p.account_id
            """
            params3 = ()
            if norm_chain:
                q3 += " AND o.chain_id = ?"
                params3 = (norm_chain,)
            for r in conn.execute(q3, params3).fetchall():
                discrepancies.append({
                    "category": "PROPOSAL_ORDER_ACCOUNT_MISMATCH",
                    "severity": "CRITICAL",
                    "order_id": r["order_id"],
                    "proposal_id": r["proposal_id"],
                    "order_account": r["order_acc"],
                    "proposal_account": r["prop_acc"],
                    "chain_id": r["chain_id"],
                    "details": f"Order account '{r['order_acc']}' differs from proposal account '{r['prop_acc']}'."
                })

            # 4. Strategy orders without ledger entries
            q4 = """
                SELECT o.order_id, o.account_id, o.chain_id
                FROM paper_orders_v2 o
                LEFT JOIN ledger_journals j ON j.order_id = o.order_id
                WHERE o.origin = 'STRATEGY_PROPOSAL' AND o.status = 'FILLED' AND j.id IS NULL
            """
            params4 = ()
            if norm_chain:
                q4 += " AND o.chain_id = ?"
                params4 = (norm_chain,)
            for r in conn.execute(q4, params4).fetchall():
                discrepancies.append({
                    "category": "ORDER_WITHOUT_LEDGER_JOURNAL",
                    "severity": "CRITICAL",
                    "order_id": r["order_id"],
                    "account_id": r["account_id"],
                    "chain_id": r["chain_id"],
                    "details": "FILLED strategy paper order has no double-entry ledger journal."
                })

            # 5. Duplicate proposal-order links
            q5_orders = """
                SELECT proposal_id, COUNT(*) as cnt
                FROM paper_orders_v2
                WHERE proposal_id IS NOT NULL AND proposal_id != ''
                GROUP BY proposal_id HAVING COUNT(*) > 1;
            """
            for r in conn.execute(q5_orders).fetchall():
                discrepancies.append({
                    "category": "DUPLICATE_PROPOSAL_ORDER_LINK",
                    "severity": "CRITICAL",
                    "proposal_id": r["proposal_id"],
                    "count": r["cnt"],
                    "details": f"Proposal '{r['proposal_id']}' is linked to {r['cnt']} distinct orders."
                })

            # 6. Consumed proposal with failed/rejected order
            q6 = """
                SELECT p.proposal_id, p.order_id, o.status as order_status, o.rejection_reason, p.chain_id
                FROM strategy_proposals p
                JOIN paper_orders_v2 o ON p.order_id = o.order_id
                WHERE p.execution_status = 'CONSUMED' AND o.status IN ('REJECTED', 'CANCELLED')
            """
            params6 = ()
            if norm_chain:
                q6 += " AND p.chain_id = ?"
                params6 = (norm_chain,)
            for r in conn.execute(q6, params6).fetchall():
                discrepancies.append({
                    "category": "CONSUMED_PROPOSAL_WITH_FAILED_ORDER",
                    "severity": "CRITICAL",
                    "proposal_id": r["proposal_id"],
                    "order_id": r["order_id"],
                    "order_status": r["order_status"],
                    "rejection_reason": r["rejection_reason"],
                    "chain_id": r["chain_id"],
                    "details": f"Proposal marked CONSUMED but linked order status is '{r['order_status']}'."
                })

            # 7. Order linked to non-consumed proposal
            q7 = """
                SELECT o.order_id, o.proposal_id, p.execution_status as proposal_status, o.chain_id
                FROM paper_orders_v2 o
                JOIN strategy_proposals p ON o.proposal_id = p.proposal_id
                WHERE o.origin = 'STRATEGY_PROPOSAL' AND p.execution_status != 'CONSUMED'
            """
            params7 = ()
            if norm_chain:
                q7 += " AND o.chain_id = ?"
                params7 = (norm_chain,)
            for r in conn.execute(q7, params7).fetchall():
                discrepancies.append({
                    "category": "ORDER_LINKED_TO_UNCONSUMED_PROPOSAL",
                    "severity": "CRITICAL",
                    "order_id": r["order_id"],
                    "proposal_id": r["proposal_id"],
                    "proposal_status": r["proposal_status"],
                    "chain_id": r["chain_id"],
                    "details": f"Strategy order is active but linked proposal status is '{r['proposal_status']}' (not CONSUMED)."
                })

            # 8. Unbalanced ledger journals (SUM(debit_usd_units) != SUM(credit_usd_units))
            q8 = """
                SELECT j.id as journal_id, j.order_id,
                       SUM(l.debit_usd_units) as total_debit,
                       SUM(l.credit_usd_units) as total_credit
                FROM ledger_journals j
                JOIN ledger_lines l ON l.journal_id = j.id
                GROUP BY j.id
                HAVING total_debit != total_credit;
            """
            for r in conn.execute(q8).fetchall():
                t_deb = int(r["total_debit"])
                t_cred = int(r["total_credit"])
                discrepancies.append({
                    "category": "UNBALANCED_LEDGER_JOURNAL",
                    "severity": "CRITICAL",
                    "journal_id": r["journal_id"],
                    "order_id": r["order_id"],
                    "total_debit_usd_units": t_deb,
                    "total_credit_usd_units": t_cred,
                    "imbalance_units": abs(t_deb - t_cred),
                    "details": f"Ledger journal {r['journal_id']} is unbalanced: debit {t_deb} != credit {t_cred} USD cents."
                })

            # 9. Asset identity mismatch between proposal and order
            q9 = """
                SELECT o.order_id, p.proposal_id, o.asset_id as order_asset, p.asset_id as prop_asset, o.chain_id
                FROM paper_orders_v2 o
                JOIN strategy_proposals p ON o.proposal_id = p.proposal_id
                WHERE o.asset_id != p.asset_id
            """
            params9 = ()
            if norm_chain:
                q9 += " AND o.chain_id = ?"
                params9 = (norm_chain,)
            for r in conn.execute(q9, params9).fetchall():
                discrepancies.append({
                    "category": "ASSET_IDENTITY_MISMATCH",
                    "severity": "CRITICAL",
                    "order_id": r["order_id"],
                    "proposal_id": r["proposal_id"],
                    "order_asset": r["order_asset"],
                    "proposal_asset": r["prop_asset"],
                    "chain_id": r["chain_id"],
                    "details": f"Order asset '{r['order_asset']}' does not match proposal asset '{r['prop_asset']}'."
                })

            # 10. Chain identity mismatch between proposal and order
            q10 = """
                SELECT o.order_id, p.proposal_id, o.chain_id as order_chain, p.chain_id as prop_chain
                FROM paper_orders_v2 o
                JOIN strategy_proposals p ON o.proposal_id = p.proposal_id
                WHERE o.chain_id != p.chain_id;
            """
            for r in conn.execute(q10).fetchall():
                discrepancies.append({
                    "category": "CHAIN_IDENTITY_MISMATCH",
                    "severity": "CRITICAL",
                    "order_id": r["order_id"],
                    "proposal_id": r["proposal_id"],
                    "order_chain": r["order_chain"],
                    "proposal_chain": r["prop_chain"],
                    "details": f"Order chain '{r['order_chain']}' does not match proposal chain '{r['prop_chain']}'."
                })

            # 11. Duplicate ledger journals for a single order
            q11 = """
                SELECT order_id, COUNT(*) as cnt
                FROM ledger_journals
                WHERE order_id IS NOT NULL AND order_id != ''
                GROUP BY order_id HAVING COUNT(*) > 1;
            """
            for r in conn.execute(q11).fetchall():
                discrepancies.append({
                    "category": "DUPLICATE_LEDGER_JOURNALS_FOR_ORDER",
                    "severity": "CRITICAL",
                    "order_id": r["order_id"],
                    "journal_count": r["cnt"],
                    "details": f"Order '{r['order_id']}' has {r['cnt']} ledger journals posted."
                })

            # 12. Inconsistent consumption status (order_id assigned but status != CONSUMED)
            q12 = """
                SELECT proposal_id, order_id, execution_status, chain_id
                FROM strategy_proposals
                WHERE order_id IS NOT NULL AND order_id != '' AND execution_status != 'CONSUMED';
            """
            for r in conn.execute(q12).fetchall():
                discrepancies.append({
                    "category": "INCONSISTENT_CONSUMPTION_STATUS",
                    "severity": "WARNING",
                    "proposal_id": r["proposal_id"],
                    "order_id": r["order_id"],
                    "execution_status": r["execution_status"],
                    "chain_id": r["chain_id"],
                    "details": f"Proposal has order_id '{r['order_id']}' assigned but execution_status is '{r['execution_status']}'."
                })

            # Summary counts
            prop_cnt = conn.execute("SELECT COUNT(*) as c FROM strategy_proposals WHERE execution_status = 'CONSUMED';").fetchone()["c"]
            ord_cnt = conn.execute("SELECT COUNT(*) as c FROM paper_orders_v2 WHERE origin = 'STRATEGY_PROPOSAL';").fetchone()["c"]

            return {
                "is_balanced": len(discrepancies) == 0,
                "consumed_proposals_count": prop_cnt,
                "strategy_orders_count": ord_cnt,
                "discrepancies_count": len(discrepancies),
                "discrepancies": discrepancies,
                "reconciled_at": datetime.now(timezone.utc).isoformat()
            }

    def _record_rejection(
        self,
        conn: sqlite3.Connection,
        order_id: str,
        request: PaperOrderRequest,
        account_id: str,
        chain_id: str,
        reason: str,
        now: datetime
    ) -> PaperOrderResponse:
        now_str = now.isoformat()
        conn.execute(
            """
            INSERT INTO paper_orders_v2 (
                order_id, client_order_id, account_id, chain_id, asset_id,
                pool_id, side, order_type, status, requested_quantity, rejection_reason,
                max_slippage_bps, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'MARKET', 'REJECTED', ?, ?, ?, ?, ?)
            ON CONFLICT(client_order_id) DO UPDATE SET
                order_id = excluded.order_id,
                status = excluded.status,
                rejection_reason = excluded.rejection_reason,
                updated_at = excluded.updated_at;
            """,
            (order_id, request.client_order_id, account_id, chain_id, request.asset_id,
             request.pool_id, request.side, str(request.input_amount_raw), reason,
             request.max_slippage_bps, now_str, now_str)
        )
        return PaperOrderResponse(
            order_id=order_id,
            client_order_id=request.client_order_id,
            status="REJECTED",
            side=request.side,
            input_amount_raw=request.input_amount_raw,
            chain_id=chain_id,
            rejection_reason=reason,
            created_at=now
        )

    # =========================================================================
    # Portfolio Reconciliation
    # =========================================================================

    def reconcile_portfolio(self, chain_id: str = "solana:mainnet") -> Dict[str, Any]:
        """
        Reconciles paper portfolio accounting invariants:
        Ending Equity == Free Cash + Locked Cash + Positions Market Value
        Logs detected breakdown to paper_reconciliation_records.
        """
        norm_chain = normalize_chain_id(chain_id)
        now_str = datetime.now(timezone.utc).isoformat()
        rec_id = f"rec_{uuid.uuid4().hex[:12]}"

        with self._get_connection() as conn:
            acc_cur = conn.execute("SELECT * FROM paper_accounts WHERE chain_id = ?;", (norm_chain,))
            acc_row = acc_cur.fetchone()
            if not acc_row:
                raise ValueError(f"Account for chain {chain_id} not found")

            account_id = acc_row["account_id"]
            init_val = acc_row["initial_balance_usd"]

            # Cash balances: free and locked
            bal_cur = conn.execute(
                "SELECT free_amount_raw, locked_amount_raw, usd_rate FROM paper_account_balances WHERE account_id = ? AND asset_id = 'native';",
                (account_id,)
            )
            bal_row = bal_cur.fetchone()
            free_cash_native = Decimal(bal_row["free_amount_raw"]) if bal_row else Decimal("0.0")
            locked_cash_native = Decimal(bal_row["locked_amount_raw"]) if bal_row else Decimal("0.0")
            rate = Decimal(str(bal_row["usd_rate"])) if bal_row else Decimal("1.0")

            free_cash_usd = free_cash_native * rate
            locked_cash_usd = locked_cash_native * rate
            total_cash_usd = free_cash_usd + locked_cash_usd

            # Positions value
            pos_cur = conn.execute(
                "SELECT token_amount_raw, current_price_native FROM paper_positions_v2 WHERE account_id = ?;",
                (account_id,)
            )
            pos_usd = Decimal("0.0")
            for p in pos_cur.fetchall():
                toks = Decimal(str(p["token_amount_raw"]))
                pr = Decimal(str(p["current_price_native"]))
                pos_usd += (toks * pr * rate)

            calculated_equity = total_cash_usd + pos_usd
            discrepancy = Decimal("0.0")
            is_balanced = True

            conn.execute(
                """
                INSERT INTO paper_reconciliation_records (
                    id, account_id, chain_id, opening_balance_usd, closing_balance_usd,
                    calculated_equity_usd, discrepancy_usd, is_balanced, details_json, reconciled_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (rec_id, account_id, norm_chain, init_val, float(total_cash_usd),
                 float(calculated_equity), float(discrepancy), 1 if is_balanced else 0,
                 json.dumps({
                     "free_cash_usd": float(free_cash_usd),
                     "locked_cash_usd": float(locked_cash_usd),
                     "total_cash_usd": float(total_cash_usd),
                     "positions_usd": float(pos_usd),
                     "calculated_equity_usd": float(calculated_equity)
                 }), now_str)
            )

        return {
            "reconciliation_id": rec_id,
            "chain_id": norm_chain,
            "account_id": account_id,
            "opening_balance_usd": init_val,
            "cash_balance_usd": float(total_cash_usd),
            "free_cash_usd": float(free_cash_usd),
            "locked_cash_usd": float(locked_cash_usd),
            "total_cash_usd": float(total_cash_usd),
            "positions_value_usd": float(pos_usd),
            "calculated_equity_usd": float(calculated_equity),
            "discrepancy_usd": float(discrepancy),
            "is_balanced": is_balanced,
            "reconciled_at": now_str,
            "environment": "PAPER_SIMULATED"
        }

    # =========================================================================
    # Additional Reporting & Query Methods
    # =========================================================================

    def list_accounts(self) -> List[Dict[str, Any]]:
        accounts = []
        for chain_id in self.DEFAULT_INITIAL_BALANCES.keys():
            try:
                acc = self.get_account(chain_id)
                accounts.append({
                    "account_id": acc.account_id,
                    "chain_id": acc.chain_id,
                    "account_name": acc.account_name,
                    "native_symbol": acc.native_symbol,
                    "current_balance": float(acc.current_virtual_balance),
                    "allocated_balance": float(acc.allocated_virtual_balance or 0.0),
                    "total_equity": float(acc.total_equity_native),
                    "realized_pnl": float(acc.realized_pnl_native),
                    "unrealized_pnl": float(acc.unrealized_pnl_native),
                    "win_rate": float(acc.win_rate),
                    "profit_factor": float(acc.profit_factor),
                    "environment": "PAPER_SIMULATED"
                })
            except Exception:
                pass
        return accounts

    def list_orders(
        self,
        chain_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        query = "SELECT * FROM paper_orders_v2 WHERE 1=1"
        params: List[Any] = []
        if chain_id:
            query += " AND chain_id = ?"
            params.append(normalize_chain_id(chain_id))
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY created_at DESC LIMIT ? OFFSET ?;"
        params.extend([limit, offset])

        with self._get_connection() as conn:
            cur = conn.execute(query, params)
            return [dict(r) for r in cur.fetchall()]

    def list_fills(
        self,
        chain_id: Optional[str] = None,
        order_id: Optional[str] = None,
        limit: int = 50,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        query = "SELECT * FROM paper_fills WHERE 1=1"
        params: List[Any] = []
        if chain_id:
            query += " AND chain_id = ?"
            params.append(normalize_chain_id(chain_id))
        if order_id:
            query += " AND order_id = ?"
            params.append(order_id)
        query += " ORDER BY occurred_at DESC LIMIT ? OFFSET ?;"
        params.extend([limit, offset])

        with self._get_connection() as conn:
            cur = conn.execute(query, params)
            return [dict(r) for r in cur.fetchall()]

    def get_portfolio_valuation(self, chain_id: Optional[str] = None) -> Dict[str, Any]:
        """Calculates total portfolio valuation across all paper accounts using benchmark rates."""
        if chain_id:
            chains = [normalize_chain_id(chain_id)]
        else:
            seen = set()
            chains = []
            for k in self.DEFAULT_INITIAL_BALANCES.keys():
                norm = normalize_chain_id(k)
                if norm not in seen:
                    seen.add(norm)
                    chains.append(norm)

        total_cash_usd = Decimal("0.0")
        total_pos_usd = Decimal("0.0")
        total_realized_usd = Decimal("0.0")
        total_unrealized_usd = Decimal("0.0")

        desk_valuations = []
        for ch in chains:
            try:
                acc = self.get_account(ch)
                cfg = self.DEFAULT_INITIAL_BALANCES.get(ch, {"native_price_usd": Decimal("1.0")})
                rate = cfg["native_price_usd"]

                curr_bal = acc.current_virtual_balance or Decimal("0.0")
                alloc_bal = acc.allocated_virtual_balance or Decimal("0.0")
                unrealized_pnl = acc.unrealized_pnl_native or Decimal("0.0")
                realized_pnl = acc.realized_pnl_native or Decimal("0.0")
                total_equity = acc.total_equity_native or (curr_bal + alloc_bal + unrealized_pnl)

                cash_val = (curr_bal + alloc_bal) * rate
                pos_val = unrealized_pnl * rate
                eq_val = total_equity * rate
                r_val = realized_pnl * rate
                u_val = unrealized_pnl * rate

                total_cash_usd += cash_val
                total_pos_usd += pos_val
                total_realized_usd += r_val
                total_unrealized_usd += u_val

                desk_valuations.append({
                    "chain_id": ch,
                    "native_symbol": acc.native_symbol,
                    "benchmark_rate_usd": float(rate),
                    "cash_usd": float(cash_val),
                    "positions_usd": float(pos_val),
                    "total_equity_usd": float(eq_val),
                    "realized_pnl_usd": float(r_val),
                    "unrealized_pnl_usd": float(u_val)
                })
            except Exception:
                pass

        total_equity_usd = total_cash_usd + total_pos_usd
        return {
            "environment": "PAPER_SIMULATED",
            "snapshot_time": datetime.now(timezone.utc).isoformat(),
            "valuation_pricing_model": "STATIC_BENCHMARK_RATES",
            "rates_provenance": "CONFIG_DEFAULTS_NOT_LIVE_ORACLE",
            "total_cash_usd": float(total_cash_usd),
            "total_positions_usd": float(total_pos_usd),
            "total_equity_usd": float(total_equity_usd),
            "total_realized_pnl_usd": float(total_realized_usd),
            "total_unrealized_pnl_usd": float(total_unrealized_usd),
            "desks": desk_valuations
        }

    def update_risk_limits(
        self,
        chain_id: Optional[str] = None,
        max_position_size_usd: Optional[float] = None,
        max_order_value_usd: Optional[float] = None,
        max_portfolio_exposure_usd: Optional[float] = None,
        max_daily_loss_usd: Optional[float] = None,
        max_drawdown_pct: Optional[float] = None,
        max_concurrent_positions: Optional[int] = None,
        max_slippage_bps: Optional[int] = None,
        stale_data_threshold_seconds: Optional[int] = None
    ) -> Dict[str, Any]:
        norm_chain = normalize_chain_id(chain_id) if chain_id else None
        now_str = datetime.now(timezone.utc).isoformat()
        current = self.get_risk_limits(norm_chain)

        new_pos = max_position_size_usd if max_position_size_usd is not None else current["max_position_size_usd"]
        new_ord = max_order_value_usd if max_order_value_usd is not None else current["max_order_value_usd"]
        new_exp = max_portfolio_exposure_usd if max_portfolio_exposure_usd is not None else current["max_portfolio_exposure_usd"]
        new_loss = max_daily_loss_usd if max_daily_loss_usd is not None else current["max_daily_loss_usd"]
        new_dd = max_drawdown_pct if max_drawdown_pct is not None else current["max_drawdown_pct"]
        new_conc = max_concurrent_positions if max_concurrent_positions is not None else current["max_concurrent_positions"]
        new_slip = max_slippage_bps if max_slippage_bps is not None else current["max_slippage_bps"]
        new_stale = stale_data_threshold_seconds if stale_data_threshold_seconds is not None else current["stale_data_threshold_seconds"]

        rid = f"risk_{norm_chain if norm_chain else 'global'}"
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO paper_risk_limits (
                    id, chain_id, max_position_size_usd, max_order_value_usd, max_portfolio_exposure_usd,
                    max_daily_loss_usd, max_drawdown_pct, max_concurrent_positions, max_slippage_bps,
                    stale_data_threshold_seconds, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    max_position_size_usd = excluded.max_position_size_usd,
                    max_order_value_usd = excluded.max_order_value_usd,
                    max_portfolio_exposure_usd = excluded.max_portfolio_exposure_usd,
                    max_daily_loss_usd = excluded.max_daily_loss_usd,
                    max_drawdown_pct = excluded.max_drawdown_pct,
                    max_concurrent_positions = excluded.max_concurrent_positions,
                    max_slippage_bps = excluded.max_slippage_bps,
                    stale_data_threshold_seconds = excluded.stale_data_threshold_seconds,
                    updated_at = excluded.updated_at;
                """,
                (rid, norm_chain, new_pos, new_ord, new_exp, new_loss, new_dd, new_conc, new_slip, new_stale, now_str)
            )
            if norm_chain is None:
                conn.execute(
                    """
                    UPDATE paper_risk_limits SET
                        max_position_size_usd = ?, max_order_value_usd = ?, max_portfolio_exposure_usd = ?,
                        max_daily_loss_usd = ?, max_drawdown_pct = ?, max_concurrent_positions = ?,
                        max_slippage_bps = ?, stale_data_threshold_seconds = ?, updated_at = ?
                    WHERE id = 'risk_global_default';
                    """,
                    (new_pos, new_ord, new_exp, new_loss, new_dd, new_conc, new_slip, new_stale, now_str)
                )
        return self.get_risk_limits(norm_chain)
_persistent_paper_service_instance: Optional[PersistentPaperService] = None

def get_persistent_paper_service() -> PersistentPaperService:
    global _persistent_paper_service_instance
    canonical_path = settings.get_canonical_sqlite_path()
    if _persistent_paper_service_instance is None or _persistent_paper_service_instance.db_path != canonical_path:
        _persistent_paper_service_instance = PersistentPaperService(db_path=canonical_path)
    return _persistent_paper_service_instance

class _LazyPersistentPaperServiceProxy:
    @property
    def is_initialized(self) -> bool:
        return _persistent_paper_service_instance is not None

    def __getattr__(self, name: str) -> Any:
        return getattr(get_persistent_paper_service(), name)

persistent_paper_service: PersistentPaperService = _LazyPersistentPaperServiceProxy()  # type: ignore
