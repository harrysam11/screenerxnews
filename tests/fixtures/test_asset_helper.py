"""
Isolated Test Asset Fixture Helper (Phase P10.2-I.2).
Strictly isolated to automated test execution environments.
Production services do NOT expose test registration interfaces.
Requires settings.APP_ENV == "test" to operate; fails closed in all other environments.
"""

import os
import sys
import json
import sqlite3
import hashlib
from datetime import datetime, timezone
from typing import Dict, Any, Optional, Union

from app.core.config import settings
from app.core.chains import normalize_chain_id
from app.schemas.contracts import DecimalProvenanceSource


def register_test_asset_fixture(
    db_target: Union[str, sqlite3.Connection],
    chain_id: str,
    asset_address: str,
    decimals: int,
    symbol: Optional[str] = None,
    name: Optional[str] = None,
    authoritative: bool = False
) -> Dict[str, Any]:
    """
    Test-only fixture helper. Structurally separated from production application services.
    
    Security Boundaries:
    1. APP_ENV must explicitly equal 'test'.
       Fails closed when APP_ENV is unset, 'development', 'production', or any other mode.
    2. Does not rely on runtime presence of pytest in sys.modules or environment variables alone.
    3. If authoritative=False (default), records verification_source='CONTROLLED_TEST_FIXTURE'.
       These records can NEVER authorize paper trading orders or live execution.
    4. If authoritative=True (for testing simulated trade execution pathways), records
       verification_source='AUTHENTICATED_RPC_EVIDENCE' with full mock RPC payload provenance.
    """
    env = getattr(settings, "APP_ENV", "").lower()
    if env != "test":
        raise PermissionError(
            f"Test asset registration is strictly forbidden outside explicit test context (current APP_ENV='{env}'). "
            "APP_ENV must be explicitly configured as 'test'."
        )

    if decimals is None or not (0 <= int(decimals) <= 36):
        raise ValueError(f"Invalid decimal value '{decimals}'. Decimals must be between 0 and 36.")

    norm_chain = normalize_chain_id(chain_id)
    now_str = datetime.now(timezone.utc).isoformat()
    sym = symbol or "TEST"
    token_name = name or f"Test Token {sym}"
    tok_id = f"tok_test_{hashlib.sha256(f'{norm_chain}:{asset_address}'.encode()).hexdigest()[:12]}"
    ver_id = f"ver_test_{hashlib.sha256(f'{norm_chain}:{asset_address}'.encode()).hexdigest()[:12]}"

    if authoritative:
        source = DecimalProvenanceSource.AUTHENTICATED_RPC_EVIDENCE.value
        method = "ONCHAIN_RPC_MINT_DATA"
        status = "VERIFIED"
        raw_meta = {
            "authority": "TEST_HARNESS_MOCK_RPC",
            "chain_id": norm_chain,
            "mint": asset_address,
            "decimals": int(decimals),
            "symbol": sym,
            "slot": 250000000,
            "retrieved_at": now_str
        }
    else:
        source = DecimalProvenanceSource.CONTROLLED_TEST_FIXTURE.value
        method = "SYNTHETIC_TEST_FIXTURE"
        status = "UNVERIFIED"
        raw_meta = {
            "fixture_type": "CONTROLLED_TEST_FIXTURE",
            "chain_id": norm_chain,
            "mint": asset_address,
            "decimals": int(decimals),
            "symbol": sym,
            "is_test_only": True
        }

    raw_str = json.dumps(raw_meta, sort_keys=True)
    raw_hash = hashlib.sha256(raw_str.encode("utf-8")).hexdigest()

    def _apply_to_conn(conn: sqlite3.Connection):
        conn.execute("BEGIN IMMEDIATE;")
        try:
            # 1. Insert/update discovered_tokens
            conn.execute(
                """
                INSERT INTO discovered_tokens (
                    id, chain_id, address, symbol, name, decimals, is_verified, discovery_source, first_seen_at, last_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'TEST_FIXTURE', ?, ?)
                ON CONFLICT(chain_id, address) DO UPDATE SET
                    decimals = excluded.decimals,
                    symbol = excluded.symbol,
                    name = excluded.name,
                    is_verified = excluded.is_verified,
                    last_seen_at = excluded.last_seen_at;
                """,
                (tok_id, norm_chain, asset_address, sym, token_name, int(decimals),
                 1 if authoritative else 0, now_str, now_str)
            )

            # 2. Insert/update token_decimal_verifications
            conn.execute(
                """
                INSERT INTO token_decimal_verifications (
                    verification_id, chain_id, asset_address, decimals, symbol,
                    verification_source, verification_method, raw_metadata_hash, raw_metadata_json,
                    status, verified_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(chain_id, asset_address) DO UPDATE SET
                    decimals = excluded.decimals,
                    symbol = excluded.symbol,
                    verification_source = excluded.verification_source,
                    verification_method = excluded.verification_method,
                    raw_metadata_hash = excluded.raw_metadata_hash,
                    raw_metadata_json = excluded.raw_metadata_json,
                    status = excluded.status,
                    verified_at = excluded.verified_at;
                """,
                (ver_id, norm_chain, asset_address, int(decimals), sym,
                 source, method, raw_hash, raw_str, status, now_str, now_str)
            )
            conn.execute("COMMIT;")
        except Exception:
            conn.execute("ROLLBACK;")
            raise

    if isinstance(db_target, sqlite3.Connection):
        _apply_to_conn(db_target)
    else:
        with sqlite3.connect(db_target, timeout=30.0) as conn:
            _apply_to_conn(conn)

    return {
        "chain_id": norm_chain,
        "asset_address": asset_address,
        "decimals": int(decimals),
        "status": status,
        "verification_source": source,
        "verification_method": method,
        "raw_metadata_hash": raw_hash,
        "raw_metadata_json": raw_str,
        "verified_at": now_str,
        "is_authoritative": authoritative
    }
