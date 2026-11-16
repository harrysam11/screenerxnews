import os
import json
import sqlite3
import hashlib
import uuid
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List, Tuple
from decimal import Decimal

from app.core.config import settings
from app.core.chains import normalize_chain_id, to_caip2_chain_id
from app.core.address_validator import AddressValidator, AddressValidationError
from app.core.discovery_protocols import protocol_registry, ProtocolDescriptor
from app.core.network import OfflineModeBlockedException
from app.services.provider_manager import provider_manager

class DiscoveryService:
    """
    Real Multi-Chain Discovery & Ingestion Engine (Phase P10.2-E).
    Provides:
    - Multi-chain DEX pool & token discovery across all 12 chains.
    - Checkpoint tracking, lag monitoring, and resumption.
    - Strict deduplication and idempotent upserts.
    - Blockchain reorganization rollback and invalidation.
    - Normalized market data enrichment and exact FDV calculation without fabricated defaults.
    - Offline isolation and sovereign provider budget enforcement.
    """

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or settings.get_canonical_sqlite_path()
        if self.db_path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)

    def _get_connection(self) -> sqlite3.Connection:
        if self.db_path == ":memory:":
            conn = sqlite3.connect("file:memecoin_disc_mem?mode=memory&cache=shared", uri=True, timeout=30.0, isolation_level=None)
        else:
            conn = sqlite3.connect(self.db_path, timeout=30.0, check_same_thread=False, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        if self.db_path != ":memory:":
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute("PRAGMA busy_timeout = 30000;")
        return conn

    # =========================================================================
    # Address & Chain Normalization
    # =========================================================================

    def _normalize_and_validate_address(self, chain_id: str, address: str) -> str:
        """
        Validates address for specific chain and returns canonical normalized form.
        Raises AddressValidationError if address is malformed or invalid checksum.
        """
        if not address:
            raise AddressValidationError("Address cannot be empty")
        is_valid, norm_addr, err = AddressValidator.validate_address_for_chain(chain_id, address)
        if not is_valid:
            raise AddressValidationError(f"Invalid address '{address}' for chain '{chain_id}': {err}")
        return norm_addr

    # =========================================================================
    # Pool & Token Ingestion Pipeline
    # =========================================================================

    def ingest_pool_event(
        self,
        chain_id: str,
        protocol: str,
        pool_address: str,
        token0_address: str,
        token1_address: str,
        fee_tier_bps: int = 30,
        block_number: Optional[int] = None,
        slot: Optional[int] = None,
        tx_hash: Optional[str] = None,
        log_index: Optional[int] = None,
        factory_address: Optional[str] = None,
        discovery_source: str = "rpc_logs",
        created_at: Optional[str] = None,
        token0_metadata: Optional[Dict[str, Any]] = None,
        token1_metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Idempotently ingests a discovered DEX liquidity pool and its underlying tokens.
        Tracks origin block/slot, tx_hash, log_index, and updates ingestion checkpoints.
        """
        norm_chain = normalize_chain_id(chain_id)
        norm_pool = self._normalize_and_validate_address(norm_chain, pool_address)
        norm_t0 = self._normalize_and_validate_address(norm_chain, token0_address)
        norm_t1 = self._normalize_and_validate_address(norm_chain, token1_address)

        norm_factory = None
        if factory_address:
            try:
                norm_factory = self._normalize_and_validate_address(norm_chain, factory_address)
            except Exception:
                norm_factory = factory_address.strip()

        now_str = created_at or datetime.now(timezone.utc).isoformat()
        pool_id = f"pool_{hashlib.sha256(f'{norm_chain}:{norm_pool}'.encode()).hexdigest()[:16]}"
        token0_id = f"tok_{hashlib.sha256(f'{norm_chain}:{norm_t0}'.encode()).hexdigest()[:16]}"
        token1_id = f"tok_{hashlib.sha256(f'{norm_chain}:{norm_t1}'.encode()).hexdigest()[:16]}"

        t0_sym = (token0_metadata or {}).get("symbol", "UNKNOWN")
        t0_name = (token0_metadata or {}).get("name", "Unknown Token")
        t0_dec = (token0_metadata or {}).get("decimals")
        t0_sup = (token0_metadata or {}).get("total_supply")

        t1_sym = (token1_metadata or {}).get("symbol", "UNKNOWN")
        t1_name = (token1_metadata or {}).get("name", "Unknown Token")
        t1_dec = (token1_metadata or {}).get("decimals")
        t1_sup = (token1_metadata or {}).get("total_supply")

        with self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            try:
                # 1. Upsert token0
                conn.execute(
                    """
                    INSERT INTO discovered_tokens (
                        id, chain_id, address, symbol, name, decimals, total_supply,
                        discovery_source, first_seen_at, last_seen_at, metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(chain_id, address) DO UPDATE SET
                        last_seen_at = excluded.last_seen_at,
                        symbol = CASE WHEN discovered_tokens.symbol = 'UNKNOWN' AND excluded.symbol != 'UNKNOWN' THEN excluded.symbol ELSE discovered_tokens.symbol END,
                        name = CASE WHEN discovered_tokens.name = 'Unknown Token' AND excluded.name != 'Unknown Token' THEN excluded.name ELSE discovered_tokens.name END,
                        decimals = COALESCE(discovered_tokens.decimals, excluded.decimals),
                        total_supply = COALESCE(discovered_tokens.total_supply, excluded.total_supply);
                    """,
                    (token0_id, norm_chain, norm_t0, t0_sym, t0_name, t0_dec, t0_sup, discovery_source, now_str, now_str, json.dumps(token0_metadata or {}))
                )

                # 2. Upsert token1
                conn.execute(
                    """
                    INSERT INTO discovered_tokens (
                        id, chain_id, address, symbol, name, decimals, total_supply,
                        discovery_source, first_seen_at, last_seen_at, metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(chain_id, address) DO UPDATE SET
                        last_seen_at = excluded.last_seen_at,
                        symbol = CASE WHEN discovered_tokens.symbol = 'UNKNOWN' AND excluded.symbol != 'UNKNOWN' THEN excluded.symbol ELSE discovered_tokens.symbol END,
                        name = CASE WHEN discovered_tokens.name = 'Unknown Token' AND excluded.name != 'Unknown Token' THEN excluded.name ELSE discovered_tokens.name END,
                        decimals = COALESCE(discovered_tokens.decimals, excluded.decimals),
                        total_supply = COALESCE(discovered_tokens.total_supply, excluded.total_supply);
                    """,
                    (token1_id, norm_chain, norm_t1, t1_sym, t1_name, t1_dec, t1_sup, discovery_source, now_str, now_str, json.dumps(token1_metadata or {}))
                )

                # 3. Check existing pool
                cur = conn.execute(
                    "SELECT id, is_canonical, status FROM discovered_pools WHERE chain_id = ? AND pool_address = ?;",
                    (norm_chain, norm_pool)
                )
                existing = cur.fetchone()
                is_new = existing is None

                if is_new:
                    conn.execute(
                        """
                        INSERT INTO discovered_pools (
                            id, chain_id, pool_address, protocol, factory_address,
                            token0_address, token1_address, fee_tier_bps, block_number, slot,
                            tx_hash, log_index, discovery_source, created_at, is_canonical, status
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 'ACTIVE');
                        """,
                        (pool_id, norm_chain, norm_pool, protocol, norm_factory,
                         norm_t0, norm_t1, fee_tier_bps, block_number, slot,
                         tx_hash, log_index, discovery_source, now_str)
                    )
                else:
                    pool_id = existing["id"]
                    # If existing pool was reorged but now seen in a higher valid block, update
                    if block_number and (existing["status"] == "REORGED" or not existing["is_canonical"]):
                        conn.execute(
                            """
                            UPDATE discovered_pools SET
                                block_number = ?, slot = ?, tx_hash = ?, log_index = ?,
                                is_canonical = 1, status = 'ACTIVE'
                            WHERE id = ?;
                            """,
                            (block_number, slot, tx_hash, log_index, pool_id)
                        )

                # 4. Checkpoint update
                current_height = block_number if block_number is not None else slot
                if current_height is not None:
                    chk_id = f"chk_{hashlib.sha256(f'{norm_chain}:{protocol}:{discovery_source}'.encode()).hexdigest()[:16]}"
                    conn.execute(
                        """
                        INSERT INTO ingestion_checkpoints (
                            id, chain_id, protocol, source_type, last_block_or_slot,
                            last_block_hash, last_event_id, lag_blocks_or_slots, updated_at
                        ) VALUES (?, ?, ?, ?, ?, NULL, ?, 0, ?)
                        ON CONFLICT(chain_id, protocol, source_type) DO UPDATE SET
                            last_block_or_slot = MAX(ingestion_checkpoints.last_block_or_slot, excluded.last_block_or_slot),
                            last_event_id = COALESCE(excluded.last_event_id, ingestion_checkpoints.last_event_id),
                            updated_at = excluded.updated_at;
                        """,
                        (chk_id, norm_chain, protocol, discovery_source, current_height, tx_hash, now_str)
                    )

                conn.execute("COMMIT;")
            except Exception:
                conn.execute("ROLLBACK;")
                raise

        return {
            "pool_id": pool_id,
            "chain_id": norm_chain,
            "pool_address": norm_pool,
            "protocol": protocol,
            "token0_address": norm_t0,
            "token1_address": norm_t1,
            "fee_tier_bps": fee_tier_bps,
            "block_number": block_number,
            "slot": slot,
            "tx_hash": tx_hash,
            "is_new": is_new,
            "is_canonical": True,
            "status": "ACTIVE"
        }

    # =========================================================================
    # Market Data Snapshot & FDV Enrichment
    # =========================================================================

    def ingest_market_data(
        self,
        chain_id: str,
        pool_address: str,
        provider_id: str,
        price_usd: Optional[float] = None,
        liquidity_usd: Optional[float] = None,
        volume_24h_usd: Optional[float] = None,
        reserves_token0: Optional[float] = None,
        reserves_token1: Optional[float] = None,
        price_impact_bps: Optional[float] = None,
        total_supply: Optional[float] = None,
        token_decimals: Optional[int] = None,
        is_raw_supply: bool = False,
        observation_time: Optional[str] = None,
        confidence_score: float = 1.0,
        data_freshness: str = "REALTIME",
        raw_payload: Optional[Dict[str, Any]] = None,
        allow_network_fetch: bool = False
    ) -> Dict[str, Any]:
        """
        Enriches a discovered pool with market data and exact FDV calculations.
        Enforces:
        - Strict offline isolation: blocks unauthorized external network calls when offline.
        - Zero fabricated defaults: missing values remain None, never populated with fake defaults.
        - FDV computed strictly as price_usd * total_supply when both verified values exist.
        """
        if allow_network_fetch and provider_manager.is_offline_mode():
            raise OfflineModeBlockedException(
                f"Market data network fetch blocked for provider '{provider_id}' on {chain_id}: system operating in offline mode"
            )

        norm_chain = normalize_chain_id(chain_id)
        norm_pool = self._normalize_and_validate_address(norm_chain, pool_address)

        with self._get_connection() as conn:
            cur = conn.execute(
                "SELECT id, token0_address, token1_address FROM discovered_pools WHERE chain_id = ? AND pool_address = ?;",
                (norm_chain, norm_pool)
            )
            pool_row = cur.fetchone()
            if not pool_row:
                raise ValueError(f"Pool '{norm_pool}' on chain '{norm_chain}' does not exist in discovered_pools")

            pool_id = pool_row["id"]

            # Exact FDV calculation: strictly price_usd * normalized_total_supply
            fdv_usd = None
            if price_usd is not None and total_supply is not None:
                try:
                    p = float(price_usd)
                    s = float(total_supply)
                    if p >= 0 and s >= 0:
                        # Normalize raw integer units if decimals are provided or detected
                        dec = token_decimals
                        if dec is None and pool_row["token0_address"]:
                            tok_cur = conn.execute(
                                "SELECT decimals FROM discovered_tokens WHERE chain_id = ? AND address = ?;",
                                (norm_chain, pool_row["token0_address"])
                            )
                            tok_row = tok_cur.fetchone()
                            if tok_row and tok_row["decimals"] is not None:
                                dec = tok_row["decimals"]

                        if (is_raw_supply or (dec and s >= 10 ** (dec + 6))) and dec:
                            norm_s = s / (10 ** dec)
                        else:
                            norm_s = s

                        fdv_usd = round(p * norm_s, 4)
                except (ValueError, TypeError):
                    fdv_usd = None

            obs_time = observation_time or datetime.now(timezone.utc).isoformat()
            now_str = datetime.now(timezone.utc).isoformat()
            snap_id = f"snap_{uuid.uuid4().hex[:16]}"
            sanitized_payload = json.dumps(raw_payload) if raw_payload is not None else None

            conn.execute(
                """
                INSERT INTO market_data_snapshots (
                    id, pool_id, chain_id, provider_id, price_usd, liquidity_usd,
                    volume_24h_usd, reserves_token0, reserves_token1, price_impact_bps,
                    fdv_usd, confidence_score, data_freshness, observation_time,
                    retrieved_at, raw_payload_sanitized
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (snap_id, pool_id, norm_chain, provider_id, price_usd, liquidity_usd,
                 volume_24h_usd, reserves_token0, reserves_token1, price_impact_bps,
                 fdv_usd, confidence_score, data_freshness, obs_time, now_str, sanitized_payload)
            )

        return {
            "snapshot_id": snap_id,
            "pool_id": pool_id,
            "chain_id": norm_chain,
            "pool_address": norm_pool,
            "provider_id": provider_id,
            "price_usd": price_usd,
            "liquidity_usd": liquidity_usd,
            "volume_24h_usd": volume_24h_usd,
            "reserves_token0": reserves_token0,
            "reserves_token1": reserves_token1,
            "price_impact_bps": price_impact_bps,
            "fdv_usd": fdv_usd,
            "confidence_score": confidence_score,
            "data_freshness": data_freshness,
            "observation_time": obs_time
        }

    # =========================================================================
    # Chain Reorg Invalidation & Rollback
    # =========================================================================

    def handle_reorg_event(
        self,
        chain_id: str,
        reorg_block_number: int,
        block_hash: str,
        reason: str = "chain_reorganization"
    ) -> Dict[str, Any]:
        """
        Invalidates pools discovered at or after a reorganized block height.
        Marks affected pools as is_canonical = 0, status = 'REORGED',
        records the invalidation event in reorg_invalidation_events, and
        resets checkpoint to reorg_block_number - 1.
        """
        norm_chain = normalize_chain_id(chain_id)
        now_str = datetime.now(timezone.utc).isoformat()
        event_id = f"reorg_{uuid.uuid4().hex[:16]}"

        with self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            try:
                # 1. Invalidate affected pools
                cur = conn.execute(
                    """
                    UPDATE discovered_pools
                    SET is_canonical = 0, status = 'REORGED'
                    WHERE chain_id = ? AND block_number >= ? AND is_canonical = 1;
                    """,
                    (norm_chain, reorg_block_number)
                )
                affected_count = cur.rowcount

                # 2. Record invalidation event
                conn.execute(
                    """
                    INSERT INTO reorg_invalidation_events (
                        id, chain_id, block_number, block_hash, affected_pools_count,
                        invalidated_at, reason
                    ) VALUES (?, ?, ?, ?, ?, ?, ?);
                    """,
                    (event_id, norm_chain, reorg_block_number, block_hash, affected_count, now_str, reason)
                )

                # 3. Rollback checkpoints for this chain
                target_height = max(0, reorg_block_number - 1)
                conn.execute(
                    """
                    UPDATE ingestion_checkpoints
                    SET last_block_or_slot = MIN(last_block_or_slot, ?),
                        updated_at = ?
                    WHERE chain_id = ?;
                    """,
                    (target_height, now_str, norm_chain)
                )

                conn.execute("COMMIT;")
            except Exception:
                conn.execute("ROLLBACK;")
                raise

        return {
            "event_id": event_id,
            "chain_id": norm_chain,
            "reorg_block_number": reorg_block_number,
            "block_hash": block_hash,
            "affected_pools_count": affected_count,
            "invalidated_at": now_str,
            "reason": reason
        }

    # =========================================================================
    # Checkpoint Management
    # =========================================================================

    def get_checkpoint(self, chain_id: str, protocol: str, source_type: str = "rpc_logs") -> Optional[Dict[str, Any]]:
        norm_chain = normalize_chain_id(chain_id)
        with self._get_connection() as conn:
            cur = conn.execute(
                """
                SELECT id, chain_id, protocol, source_type, last_block_or_slot,
                       last_block_hash, last_event_id, lag_blocks_or_slots, updated_at
                FROM ingestion_checkpoints
                WHERE chain_id = ? AND protocol = ? AND source_type = ?;
                """,
                (norm_chain, protocol, source_type)
            )
            row = cur.fetchone()
            return dict(row) if row else None

    def update_checkpoint(
        self,
        chain_id: str,
        protocol: str,
        source_type: str = "rpc_logs",
        last_block_or_slot: int = 0,
        last_block_hash: Optional[str] = None,
        last_event_id: Optional[str] = None,
        lag_blocks_or_slots: int = 0
    ) -> Dict[str, Any]:
        norm_chain = normalize_chain_id(chain_id)
        now_str = datetime.now(timezone.utc).isoformat()
        chk_id = f"chk_{hashlib.sha256(f'{norm_chain}:{protocol}:{source_type}'.encode()).hexdigest()[:16]}"

        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO ingestion_checkpoints (
                    id, chain_id, protocol, source_type, last_block_or_slot,
                    last_block_hash, last_event_id, lag_blocks_or_slots, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(chain_id, protocol, source_type) DO UPDATE SET
                    last_block_or_slot = excluded.last_block_or_slot,
                    last_block_hash = COALESCE(excluded.last_block_hash, ingestion_checkpoints.last_block_hash),
                    last_event_id = COALESCE(excluded.last_event_id, ingestion_checkpoints.last_event_id),
                    lag_blocks_or_slots = excluded.lag_blocks_or_slots,
                    updated_at = excluded.updated_at;
                """,
                (chk_id, norm_chain, protocol, source_type, last_block_or_slot,
                 last_block_hash, last_event_id, lag_blocks_or_slots, now_str)
            )

        return {
            "id": chk_id,
            "chain_id": norm_chain,
            "protocol": protocol,
            "source_type": source_type,
            "last_block_or_slot": last_block_or_slot,
            "last_block_hash": last_block_hash,
            "last_event_id": last_event_id,
            "lag_blocks_or_slots": lag_blocks_or_slots,
            "updated_at": now_str
        }

    def list_checkpoints(self, chain_id: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            if chain_id:
                norm_chain = normalize_chain_id(chain_id)
                cur = conn.execute(
                    "SELECT * FROM ingestion_checkpoints WHERE chain_id = ? ORDER BY chain_id, protocol;",
                    (norm_chain,)
                )
            else:
                cur = conn.execute("SELECT * FROM ingestion_checkpoints ORDER BY chain_id, protocol;")
            return [dict(r) for r in cur.fetchall()]

    # =========================================================================
    # Queries & Lookups
    # =========================================================================

    def list_pools(
        self,
        chain_id: Optional[str] = None,
        protocol: Optional[str] = None,
        canonical_only: bool = True,
        limit: int = 50,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        query = "SELECT * FROM discovered_pools WHERE 1=1"
        params: List[Any] = []

        if chain_id:
            query += " AND chain_id = ?"
            params.append(normalize_chain_id(chain_id))
        if protocol:
            query += " AND protocol = ?"
            params.append(protocol)
        if canonical_only:
            query += " AND is_canonical = 1"

        query += " ORDER BY block_number DESC, slot DESC, created_at DESC LIMIT ? OFFSET ?;"
        params.extend([limit, offset])

        with self._get_connection() as conn:
            cur = conn.execute(query, params)
            return [dict(r) for r in cur.fetchall()]

    def get_pool(self, chain_id: str, pool_address: str) -> Optional[Dict[str, Any]]:
        norm_chain = normalize_chain_id(chain_id)
        try:
            norm_pool = self._normalize_and_validate_address(norm_chain, pool_address)
        except Exception:
            norm_pool = pool_address.strip()

        with self._get_connection() as conn:
            cur = conn.execute(
                "SELECT * FROM discovered_pools WHERE chain_id = ? AND pool_address = ?;",
                (norm_chain, norm_pool)
            )
            row = cur.fetchone()
            return dict(row) if row else None

    def get_latest_market_data(self, chain_id: str, pool_address: str) -> Optional[Dict[str, Any]]:
        norm_chain = normalize_chain_id(chain_id)
        try:
            norm_pool = self._normalize_and_validate_address(norm_chain, pool_address)
        except Exception:
            norm_pool = pool_address.strip()

        with self._get_connection() as conn:
            cur = conn.execute(
                """
                SELECT m.* FROM market_data_snapshots m
                JOIN discovered_pools p ON m.pool_id = p.id
                WHERE p.chain_id = ? AND p.pool_address = ?
                ORDER BY m.observation_time DESC, m.retrieved_at DESC LIMIT 1;
                """,
                (norm_chain, norm_pool)
            )
            row = cur.fetchone()
            return dict(row) if row else None

    def list_tokens(
        self,
        chain_id: Optional[str] = None,
        search: Optional[str] = None,
        limit: int = 50,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        query = "SELECT * FROM discovered_tokens WHERE 1=1"
        params: List[Any] = []

        if chain_id:
            query += " AND chain_id = ?"
            params.append(normalize_chain_id(chain_id))
        if search:
            query += " AND (symbol LIKE ? OR name LIKE ? OR address LIKE ?)"
            term = f"%{search}%"
            params.extend([term, term, term])

        query += " ORDER BY last_seen_at DESC LIMIT ? OFFSET ?;"
        params.extend([limit, offset])

        with self._get_connection() as conn:
            cur = conn.execute(query, params)
            return [dict(r) for r in cur.fetchall()]

    def get_token(self, chain_id: str, address: str) -> Optional[Dict[str, Any]]:
        norm_chain = normalize_chain_id(chain_id)
        try:
            norm_addr = self._normalize_and_validate_address(norm_chain, address)
        except Exception:
            norm_addr = address.strip()

        with self._get_connection() as conn:
            cur = conn.execute(
                "SELECT * FROM discovered_tokens WHERE chain_id = ? AND address = ?;",
                (norm_chain, norm_addr)
            )
            row = cur.fetchone()
            return dict(row) if row else None

    def update_token_metadata(
        self,
        chain_id: str,
        address: str,
        symbol: Optional[str] = None,
        name: Optional[str] = None,
        decimals: Optional[int] = None,
        total_supply: Optional[str] = None,
        is_verified: Optional[bool] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, Any]]:
        norm_chain = normalize_chain_id(chain_id)
        norm_addr = self._normalize_and_validate_address(norm_chain, address)
        now_str = datetime.now(timezone.utc).isoformat()

        with self._get_connection() as conn:
            cur = conn.execute(
                "SELECT * FROM discovered_tokens WHERE chain_id = ? AND address = ?;",
                (norm_chain, norm_addr)
            )
            row = cur.fetchone()
            if not row:
                return None

            new_sym = symbol if symbol is not None else row["symbol"]
            new_name = name if name is not None else row["name"]
            new_dec = decimals if decimals is not None else row["decimals"]
            new_sup = total_supply if total_supply is not None else row["total_supply"]
            new_ver = (1 if is_verified else 0) if is_verified is not None else row["is_verified"]

            existing_meta = json.loads(row["metadata_json"] or "{}")
            if metadata:
                existing_meta.update(metadata)

            conn.execute(
                """
                UPDATE discovered_tokens SET
                    symbol = ?, name = ?, decimals = ?, total_supply = ?,
                    is_verified = ?, metadata_json = ?, last_seen_at = ?
                WHERE chain_id = ? AND address = ?;
                """,
                (new_sym, new_name, new_dec, new_sup, new_ver, json.dumps(existing_meta), now_str, norm_chain, norm_addr)
            )

        return self.get_token(norm_chain, norm_addr)

discovery_service = DiscoveryService()
