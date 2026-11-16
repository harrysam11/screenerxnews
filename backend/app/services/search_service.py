"""
Multi-Chain Search & On-Chain Research Engine (Phase P10.2-I).

Provides a unified Core research interface across all 12 supported chains:
- Exact address / mint search with chain awareness and normalization.
- CAIP-2 chain filter and CAIP-19 asset identifier parsing.
- Symbol and name search with exact ranking above fuzzy, avoiding cross-chain collisions.
- Pool and pair address search.
- Explorer URL parsing and sanitization with strict SSRF defense.
- Explicit data classifications:
  ONCHAIN_VERIFIED, PROVIDER_REPORTED, DERIVED_SNAPSHOT, EXTERNAL_REFERENCE,
  CACHED, STALE, UNAVAILABLE, UNVERIFIED.
- Forensic safety status:
  VERIFIED_SAFE_FOR_THIS_CHECK, RISK_DETECTED, PARTIAL, UNSUPPORTED, UNVERIFIED, STALE.
- Strict offline-mode enforcement: produces zero network calls when offline.
- Normalized FDV calculation using human supply units without fabricated defaults.
"""

import os
import re
import ipaddress
import urllib.parse
import sqlite3
import json
from decimal import Decimal
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple

from app.core.config import settings
from app.core.chains import (
    SOLANA_CAIP2_CANONICAL,
    SOLANA_LEGACY_ALIAS,
    SUPPORTED_CHAINS,
    normalize_chain_id,
    CAIP19AssetId
)
from app.core.address_validator import AddressValidator
from app.adapters.trojan import trojan_adapter
from app.adapters.banana_gun import banana_gun_adapter
from app.schemas.contracts import AUTHORITATIVE_DECIMAL_PROVENANCE_SOURCES

# =============================================================================
# SSRF Protection & URL Parser
# =============================================================================

# Block private IPv4 and IPv6 subnets
PRIVATE_IP_NETWORKS = [
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]

EXPLORER_HOST_MAP = {
    "solscan.io": SOLANA_CAIP2_CANONICAL,
    "explorer.solana.com": SOLANA_CAIP2_CANONICAL,
    "etherscan.io": "eip155:1",
    "basescan.org": "eip155:8453",
    "arbiscan.io": "eip155:42161",
    "optimistic.etherscan.io": "eip155:10",
    "polygonscan.com": "eip155:137",
    "snowtrace.io": "eip155:43114",
    "bscscan.com": "eip155:56",
    "tronscan.org": "tron:0x2b6653dc",
    "tonscan.org": "ton:-239",
    "suiview.xyz": "sui:mainnet",
    "explorer.aptoslabs.com": "aptos:1",
    "dexscreener.com": "dexscreener"
}

def is_safe_url(url: str) -> Tuple[bool, Optional[str]]:
    """Validates URL schema and hostname against SSRF."""
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        return False, "Malformed URL"

    if parsed.scheme not in ("http", "https"):
        return False, f"Unsupported scheme: {parsed.scheme}"

    hostname = parsed.hostname
    if not hostname:
        return False, "Missing hostname in URL"

    if hostname.lower() in ("localhost", "0.0.0.0", "127.0.0.1", "::1"):
        return False, "SSRF blocked: loopback hostname"

    # Check IP addresses
    try:
        ip = ipaddress.ip_address(hostname)
        for net in PRIVATE_IP_NETWORKS:
            if ip in net:
                return False, f"SSRF blocked: private IP {ip}"
    except ValueError:
        # Not a raw IP address, which is good
        pass

    return True, None

def sanitize_and_parse_explorer_url(url: str) -> Optional[Dict[str, str]]:
    """
    Parses explorer and screener URLs (Solscan, Etherscan, DexScreener, etc.)
    Extracts canonical chain_id and token/pool address safely.
    """
    safe, err = is_safe_url(url)
    if not safe:
        return None

    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()
    path = parsed.path.strip("/")
    parts = path.split("/")

    # Check Solscan: solscan.io/token/<mint> or /account/<mint>
    if "solscan.io" in host:
        if len(parts) >= 2 and parts[0] in ("token", "account"):
            return {
                "chain_id": SOLANA_CAIP2_CANONICAL,
                "address": parts[1],
                "type": "token" if parts[0] == "token" else "account"
            }

    # Etherscan, Basescan, Arbiscan, Polygonscan, BscScan
    for exp_host, chain in EXPLORER_HOST_MAP.items():
        if exp_host in host and chain != "dexscreener":
            if len(parts) >= 2 and parts[0] in ("token", "address"):
                return {
                    "chain_id": chain,
                    "address": parts[1],
                    "type": "token" if parts[0] == "token" else "account"
                }

    # DexScreener: dexscreener.com/<platform>/<address>
    if "dexscreener.com" in host:
        if len(parts) >= 2:
            platform = parts[0].lower()
            address = parts[1]
            chain_map = {
                "solana": SOLANA_CAIP2_CANONICAL,
                "ethereum": "eip155:1",
                "base": "eip155:8453",
                "arbitrum": "eip155:42161",
                "optimism": "eip155:10",
                "polygon": "eip155:137",
                "avalanche": "eip155:43114",
                "bsc": "eip155:56",
                "sui": "sui:mainnet",
                "aptos": "aptos:1",
                "ton": "ton:-239",
                "tron": "tron:0x2b6653dc"
            }
            chain_id = chain_map.get(platform, platform)
            return {
                "chain_id": normalize_chain_id(chain_id),
                "address": address,
                "type": "pool_or_token"
            }

    return None

# =============================================================================
# Search & Research Service
# =============================================================================

class SearchService:
    """
    Unified Multi-Chain Research & Search Service.
    Enforces offline integrity, provenance tracking, and data classification.
    """

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or settings.get_canonical_sqlite_path()
        if self.db_path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)

    def _get_connection(self) -> sqlite3.Connection:
        if self.db_path == ":memory:":
            conn = sqlite3.connect("file:search_mem?mode=memory&cache=shared", uri=True, timeout=30.0, isolation_level=None)
        else:
            conn = sqlite3.connect(self.db_path, timeout=30.0, check_same_thread=False, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        if self.db_path != ":memory:":
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute("PRAGMA busy_timeout = 30000;")
        return conn

    # =========================================================================
    # Multi-Chain Search Engine
    # =========================================================================

    def search(
        self,
        query: str,
        chain_id: Optional[str] = None,
        search_type: str = "all",
        limit: int = 50,
        offset: int = 0
    ) -> Dict[str, Any]:
        """
        Unified multi-chain search entrypoint.
        Processes:
        1. Explorer URLs (SSRF sanitized)
        2. CAIP-19 asset identifiers
        3. Exact addresses / mints across chains
        4. Symbol / Name search with exact match prioritization and cross-chain separation
        5. Pool / Pair addresses
        """
        q = query.strip()
        if not q:
            return {"query": q, "total_matches": 0, "results": []}

        norm_filter_chain = normalize_chain_id(chain_id) if chain_id else None

        # 1. URL pattern check
        if q.startswith("http://") or q.startswith("https://"):
            url_match = sanitize_and_parse_explorer_url(q)
            if url_match:
                target_chain = norm_filter_chain or url_match["chain_id"]
                target_addr = url_match["address"]
                return self._search_by_address(target_addr, target_chain, limit, offset, query_source="url")

        # 2. CAIP-19 pattern check
        if "/" in q and ":" in q:
            caip19 = CAIP19AssetId.parse(q)
            if caip19:
                target_chain = norm_filter_chain or caip19.chain_id
                target_addr = caip19.asset_reference
                return self._search_by_address(target_addr, target_chain, limit, offset, query_source="caip19")

        # 3. Check if query is an address
        if AddressValidator.is_valid_format_for_any_chain(q):
            return self._search_by_address(q, norm_filter_chain, limit, offset, query_source="address")

        # 4. Search by symbol / name / pools in local database
        return self._search_by_text_and_tokens(q, norm_filter_chain, search_type, limit, offset)

    def _search_by_address(
        self,
        address: str,
        chain_id: Optional[str],
        limit: int,
        offset: int,
        query_source: str = "address"
    ) -> Dict[str, Any]:
        """Searches tokens and pools by exact address."""
        results = []
        with self._get_connection() as conn:
            # Query tokens
            t_query = "SELECT * FROM discovered_tokens WHERE address = ? COLLATE NOCASE"
            t_params: List[Any] = [address]
            if chain_id:
                t_query += " AND (chain_id = ? OR (chain_id = 'solana:mainnet' AND ? = ?))"
                t_params.extend([chain_id, chain_id, SOLANA_CAIP2_CANONICAL])

            cur = conn.execute(t_query, t_params)
            for row in cur.fetchall():
                token_dict = dict(row)
                results.append(self._enrich_token_result(conn, token_dict, match_type="exact_address"))

            # Query pools
            p_query = "SELECT * FROM discovered_pools WHERE (pool_address = ? OR token0_address = ? OR token1_address = ?) COLLATE NOCASE"
            p_params: List[Any] = [address, address, address]
            if chain_id:
                p_query += " AND (chain_id = ? OR (chain_id = 'solana:mainnet' AND ? = ?))"
                p_params.extend([chain_id, chain_id, SOLANA_CAIP2_CANONICAL])

            cur = conn.execute(p_query, p_params)
            for row in cur.fetchall():
                pool_dict = dict(row)
                results.append(self._enrich_pool_result(conn, pool_dict, match_type="exact_pool_or_token"))

        paged_results = results[offset : offset + limit]
        return {
            "query": address,
            "query_type": query_source,
            "chain_filter": chain_id,
            "total_matches": len(results),
            "results": paged_results
        }

    def _search_by_text_and_tokens(
        self,
        term: str,
        chain_id: Optional[str],
        search_type: str,
        limit: int,
        offset: int
    ) -> Dict[str, Any]:
        """Searches tokens and pools by symbol and name with rank weighting."""
        results = []
        term_upper = term.upper()
        term_wild = f"%{term}%"

        with self._get_connection() as conn:
            if search_type in ("all", "token"):
                # Case-insensitive search on discovered_tokens
                t_query = """
                    SELECT * FROM discovered_tokens
                    WHERE (symbol LIKE ? OR name LIKE ?)
                """
                t_params: List[Any] = [term_wild, term_wild]
                if chain_id:
                    t_query += " AND (chain_id = ? OR (chain_id = 'solana:mainnet' AND ? = ?))"
                    t_params.extend([chain_id, chain_id, SOLANA_CAIP2_CANONICAL])

                cur = conn.execute(t_query, t_params)
                for row in cur.fetchall():
                    token_dict = dict(row)
                    sym = (token_dict.get("symbol") or "").upper()
                    name = (token_dict.get("name") or "").upper()

                    # Scoring / Rank weight
                    if sym == term_upper:
                        rank = 100  # Exact symbol match
                        mtype = "exact_symbol"
                    elif name == term_upper:
                        rank = 80   # Exact name match
                        mtype = "exact_name"
                    elif sym.startswith(term_upper):
                        rank = 60   # Symbol prefix
                        mtype = "prefix_symbol"
                    else:
                        rank = 40   # Substring match
                        mtype = "fuzzy_text"

                    enriched = self._enrich_token_result(conn, token_dict, match_type=mtype)
                    enriched["_rank"] = rank
                    results.append(enriched)

            if search_type in ("all", "pool"):
                p_query = """
                    SELECT * FROM discovered_pools
                    WHERE protocol LIKE ?
                """
                p_params: List[Any] = [term_wild]
                if chain_id:
                    p_query += " AND (chain_id = ? OR (chain_id = 'solana:mainnet' AND ? = ?))"
                    p_params.extend([chain_id, chain_id, SOLANA_CAIP2_CANONICAL])

                cur = conn.execute(p_query, p_params)
                for row in cur.fetchall():
                    pool_dict = dict(row)
                    enriched = self._enrich_pool_result(conn, pool_dict, match_type="protocol_match")
                    enriched["_rank"] = 20
                    results.append(enriched)

        # Sort descending by rank, then alphabetically
        results.sort(key=lambda r: (-r.get("_rank", 0), r.get("symbol", ""), r.get("chain_id", "")))
        for r in results:
            r.pop("_rank", None)

        paged_results = results[offset : offset + limit]
        return {
            "query": term,
            "query_type": "text_search",
            "chain_filter": chain_id,
            "total_matches": len(results),
            "results": paged_results
        }

    # =========================================================================
    # Enrichment & Provenance Helpers
    # =========================================================================

    def _enrich_token_result(self, conn: sqlite3.Connection, token: Dict[str, Any], match_type: str) -> Dict[str, Any]:
        """Attaches provenance, canonical identity, and classification to token search hit."""
        chain = normalize_chain_id(token["chain_id"])
        addr = token["address"]

        # Check decimal verification in token_decimal_verifications
        v_cur = conn.execute(
            """
            SELECT * FROM token_decimal_verifications
            WHERE (chain_id = ? OR chain_id = ?) AND asset_address = ?
            ORDER BY verified_at DESC LIMIT 1;
            """,
            (chain, SOLANA_LEGACY_ALIAS if chain == SOLANA_CAIP2_CANONICAL else chain, addr)
        )
        v_row = v_cur.fetchone()

        if v_row and v_row["status"] == "VERIFIED" and v_row["verification_source"] in AUTHORITATIVE_DECIMAL_PROVENANCE_SOURCES:
            classification = "ONCHAIN_VERIFIED"
            verification_status = "VERIFIED"
            verification_record = {
                "verified": True,
                "verification_source": v_row["verification_source"],
                "verification_method": v_row["verification_method"],
                "raw_metadata_hash": v_row["raw_metadata_hash"],
                "verified_at": v_row["verified_at"]
            }
        elif v_row:
            src = v_row["verification_source"]
            classification = "STATIC_SEED" if src in ("STATIC_SEED", "SEED_REGISTRY") else "UNVERIFIED"
            verification_status = "UNVERIFIED"
            verification_record = {
                "verified": False,
                "verification_source": src,
                "reason": "Static seed or non-authoritative fixture; authentic on-chain provider RPC verification required."
            }
        else:
            classification = "UNVERIFIED"
            verification_status = "UNVERIFIED"
            verification_record = {
                "verified": False,
                "reason": "Token observed in pool events but on-chain decimal verification not yet established."
            }

        # Build CAIP-19 identifier
        try:
            caip19_obj = CAIP19AssetId.from_chain_and_address(chain, addr)
            caip19_str = caip19_obj.to_caip19()
        except ValueError:
            caip19_str = f"{chain}/token:{addr}"

        return {
            "type": "token",
            "match_type": match_type,
            "chain_id": chain,
            "caip19_id": caip19_str,
            "address": addr,
            "symbol": token.get("symbol"),
            "name": token.get("name"),
            "decimals": token.get("decimals"),
            "total_supply": token.get("total_supply"),
            "data_classification": classification,
            "verification_status": verification_status,
            "verification_record": verification_record,
            "first_seen_at": token.get("first_seen_at"),
            "last_seen_at": token.get("last_seen_at")
        }

    def _enrich_pool_result(self, conn: sqlite3.Connection, pool: Dict[str, Any], match_type: str) -> Dict[str, Any]:
        """Attaches protocol details and classification to pool search hit."""
        chain = normalize_chain_id(pool["chain_id"])
        return {
            "type": "pool",
            "match_type": match_type,
            "chain_id": chain,
            "pool_address": pool["pool_address"],
            "protocol": pool["protocol"],
            "token0_address": pool["token0_address"],
            "token1_address": pool["token1_address"],
            "fee_tier_bps": pool.get("fee_tier_bps", 30),
            "data_classification": "DERIVED_SNAPSHOT",
            "is_canonical": bool(pool.get("is_canonical", 1)),
            "created_at": pool.get("created_at")
        }

    # =========================================================================
    # Asset Detail & Verification
    # =========================================================================

    def get_asset_details(self, chain_id: str, asset_reference: str) -> Optional[Dict[str, Any]]:
        """
        Retrieves complete asset detail with CAIP-19 identity and verification provenance.
        Returns None or structured unavailable object if asset does not exist.
        """
        norm_chain = normalize_chain_id(chain_id)
        clean_addr = asset_reference.strip()

        # Handle CAIP-19 format in asset_reference
        if "/" in clean_addr and ":" in clean_addr:
            c19 = CAIP19AssetId.parse(clean_addr)
            if c19:
                norm_chain = c19.chain_id
                clean_addr = c19.asset_reference

        with self._get_connection() as conn:
            cur = conn.execute(
                """
                SELECT * FROM discovered_tokens
                WHERE (chain_id = ? OR (chain_id = 'solana:mainnet' AND ? = ?))
                  AND address = ? COLLATE NOCASE;
                """,
                (norm_chain, norm_chain, SOLANA_CAIP2_CANONICAL, clean_addr)
            )
            row = cur.fetchone()
            if not row:
                return {
                    "chain_id": norm_chain,
                    "address": clean_addr,
                    "data_classification": "UNAVAILABLE",
                    "verification_status": "UNAVAILABLE",
                    "detail": f"Asset '{clean_addr}' on chain '{norm_chain}' is not present in local ingestion database.",
                    "metadata": None
                }

            token_dict = dict(row)
            enriched = self._enrich_token_result(conn, token_dict, match_type="exact_lookup")

            # Reference links
            ref_links = []
            if norm_chain in trojan_adapter.SUPPORTED_CHAINS:
                ref_links.append(trojan_adapter.get_reference_link(norm_chain, clean_addr))
            if norm_chain in banana_gun_adapter.SUPPORTED_CHAINS:
                ref_links.append(banana_gun_adapter.get_reference_link(norm_chain, clean_addr))

            enriched["external_references"] = ref_links
            return enriched

    # =========================================================================
    # Market Snapshot & Exact FDV Calculation
    # =========================================================================

    def get_asset_market_snapshot(self, chain_id: str, asset_reference: str) -> Dict[str, Any]:
        """
        Retrieves latest market data snapshot for the asset with strict freshness evaluation.
        Calculates FDV using human supply units without fabricated defaults.
        """
        norm_chain = normalize_chain_id(chain_id)
        clean_addr = asset_reference.strip()

        with self._get_connection() as conn:
            # First fetch token to know decimals & total_supply
            t_cur = conn.execute(
                """
                SELECT decimals, total_supply FROM discovered_tokens
                WHERE (chain_id = ? OR (chain_id = 'solana:mainnet' AND ? = ?))
                  AND address = ? COLLATE NOCASE;
                """,
                (norm_chain, norm_chain, SOLANA_CAIP2_CANONICAL, clean_addr)
            )
            t_row = t_cur.fetchone()
            decimals = t_row["decimals"] if t_row else None
            total_supply_str = t_row["total_supply"] if t_row else None

            # Look up pools containing this token
            p_cur = conn.execute(
                """
                SELECT id, pool_address FROM discovered_pools
                WHERE (chain_id = ? OR (chain_id = 'solana:mainnet' AND ? = ?))
                  AND (token0_address = ? OR token1_address = ?) COLLATE NOCASE;
                """,
                (norm_chain, norm_chain, SOLANA_CAIP2_CANONICAL, clean_addr, clean_addr)
            )
            pools = p_cur.fetchall()
            if not pools:
                return {
                    "chain_id": norm_chain,
                    "asset_address": clean_addr,
                    "data_classification": "UNAVAILABLE",
                    "freshness_status": "UNAVAILABLE",
                    "price_usd": None,
                    "fdv_usd": None,
                    "liquidity_usd": None,
                    "volume_24h_usd": None,
                    "unavailable_reason": "No discovered liquidity pools found for asset."
                }

            pool_ids = [p["id"] for p in pools]
            placeholders = ",".join(["?"] * len(pool_ids))

            m_cur = conn.execute(
                f"""
                SELECT * FROM market_data_snapshots
                WHERE pool_id IN ({placeholders})
                ORDER BY observation_time DESC, retrieved_at DESC LIMIT 1;
                """,
                pool_ids
            )
            snap = m_cur.fetchone()
            if not snap:
                return {
                    "chain_id": norm_chain,
                    "asset_address": clean_addr,
                    "data_classification": "UNAVAILABLE",
                    "freshness_status": "UNAVAILABLE",
                    "price_usd": None,
                    "fdv_usd": None,
                    "liquidity_usd": None,
                    "volume_24h_usd": None,
                    "unavailable_reason": "No market data snapshots ingested for associated pools."
                }

            snap_dict = dict(snap)
            obs_time = snap_dict.get("observation_time")
            freshness = self._compute_freshness(obs_time)

            price_usd = Decimal(str(snap_dict["price_usd"])) if snap_dict.get("price_usd") is not None else None
            fdv_usd = None
            fdv_reason = None

            # Exact FDV calculation: price * normalized_human_supply
            if price_usd is not None and total_supply_str is not None and decimals is not None:
                try:
                    raw_supply = Decimal(total_supply_str)
                    human_supply = raw_supply / (Decimal(10) ** decimals)
                    fdv_usd = price_usd * human_supply
                except Exception as e:
                    fdv_reason = f"FDV calculation error: {str(e)}"
            else:
                missing = []
                if price_usd is None: missing.append("price_usd")
                if total_supply_str is None: missing.append("total_supply")
                if decimals is None: missing.append("decimals")
                fdv_reason = f"Missing required fields for FDV calculation: {', '.join(missing)}"

            classification = "DERIVED_SNAPSHOT"
            if freshness == "STALE":
                classification = "STALE"

            return {
                "chain_id": norm_chain,
                "asset_address": clean_addr,
                "source": snap_dict.get("provider_id", "local_snapshots"),
                "data_classification": classification,
                "freshness_status": freshness,
                "observation_time": obs_time,
                "retrieved_at": snap_dict.get("retrieved_at"),
                "price_usd": float(price_usd) if price_usd is not None else None,
                "fdv_usd": float(fdv_usd) if fdv_usd is not None else None,
                "fdv_unavailable_reason": fdv_reason,
                "liquidity_usd": snap_dict.get("liquidity_usd"),
                "volume_24h_usd": snap_dict.get("volume_24h_usd"),
                "reserves_token0": snap_dict.get("reserves_token0"),
                "reserves_token1": snap_dict.get("reserves_token1"),
                "confidence_score": snap_dict.get("confidence_score")
            }

    def _compute_freshness(self, timestamp_str: Optional[str]) -> str:
        """Evaluates timestamp age into REALTIME, ACCEPTABLE, or STALE."""
        if not timestamp_str:
            return "STALE"
        try:
            ts = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
            age = (datetime.now(timezone.utc) - ts).total_seconds()
            if age < 60:
                return "REALTIME"
            elif age < 300:
                return "ACCEPTABLE"
            else:
                return "STALE"
        except Exception:
            return "STALE"

    # =========================================================================
    # Pools & Forensics
    # =========================================================================

    def get_asset_pools(self, chain_id: str, asset_reference: str) -> List[Dict[str, Any]]:
        """Retrieves all liquidity pools containing the given asset."""
        norm_chain = normalize_chain_id(chain_id)
        clean_addr = asset_reference.strip()

        with self._get_connection() as conn:
            cur = conn.execute(
                """
                SELECT * FROM discovered_pools
                WHERE (chain_id = ? OR (chain_id = 'solana:mainnet' AND ? = ?))
                  AND (token0_address = ? OR token1_address = ?) COLLATE NOCASE;
                """,
                (norm_chain, norm_chain, SOLANA_CAIP2_CANONICAL, clean_addr, clean_addr)
            )
            return [self._enrich_pool_result(conn, dict(row), match_type="asset_pool") for row in cur.fetchall()]

    def get_asset_forensics(self, chain_id: str, asset_reference: str) -> Dict[str, Any]:
        """
        Evaluates security and forensic safety classification for an asset.
        Returns VERIFIED_SAFE_FOR_THIS_CHECK, RISK_DETECTED, PARTIAL, UNSUPPORTED, UNVERIFIED, or STALE.
        """
        norm_chain = normalize_chain_id(chain_id)
        clean_addr = asset_reference.strip()

        with self._get_connection() as conn:
            cur = conn.execute(
                """
                SELECT * FROM discovered_tokens
                WHERE (chain_id = ? OR (chain_id = 'solana:mainnet' AND ? = ?))
                  AND address = ? COLLATE NOCASE;
                """,
                (norm_chain, norm_chain, SOLANA_CAIP2_CANONICAL, clean_addr)
            )
            row = cur.fetchone()
            if not row:
                return {
                    "chain_id": norm_chain,
                    "asset_address": clean_addr,
                    "forensic_status": "UNAVAILABLE",
                    "reason": "Asset not found in database."
                }

            token_dict = dict(row)
            metadata = json.loads(token_dict.get("metadata_json") or "{}")

            # Evaluate chain-specific forensic indicators
            checks = {}
            risks_detected = []

            if norm_chain == SOLANA_CAIP2_CANONICAL:
                # Solana checks: mint authority revoked, freeze authority revoked
                mint_auth = metadata.get("mint_authority")
                freeze_auth = metadata.get("freeze_authority")

                checks["mint_authority_revoked"] = (mint_auth is None)
                checks["freeze_authority_revoked"] = (freeze_auth is None)

                if mint_auth is not None:
                    risks_detected.append("Mint authority is active (unlimited supply risk)")
                if freeze_auth is not None:
                    risks_detected.append("Freeze authority is active (wallet freezing risk)")

                status = "RISK_DETECTED" if risks_detected else "VERIFIED_SAFE_FOR_THIS_CHECK"
            elif norm_chain.startswith("eip155:"):
                # EVM checks: proxy check, owner check
                owner = metadata.get("owner")
                is_proxy = metadata.get("is_proxy")

                checks["is_proxy"] = bool(is_proxy)
                checks["renounced_ownership"] = (owner in (None, "0x0000000000000000000000000000000000000000"))

                if is_proxy:
                    risks_detected.append("Contract is an upgradeable proxy")
                if not checks["renounced_ownership"]:
                    risks_detected.append("Contract ownership is not renounced")

                status = "RISK_DETECTED" if risks_detected else "PARTIAL"
            else:
                status = "UNSUPPORTED"
                checks["note"] = f"Automated on-chain forensic parser for chain '{norm_chain}' is partially implemented."

            return {
                "chain_id": norm_chain,
                "asset_address": clean_addr,
                "forensic_status": status,
                "checks_performed": checks,
                "risks_detected": risks_detected,
                "evaluated_at": datetime.now(timezone.utc).isoformat()
            }

    # =========================================================================
    # Integrations Status
    # =========================================================================

    def get_integrations_status(self) -> Dict[str, Any]:
        """Returns boundary and availability status of all external adapters & reference sources."""
        return {
            "mode": settings.OPERATING_MODE,
            "offline_mode": settings.is_offline or settings.OPERATING_MODE == "offline_research",
            "adapters": {
                "trojan": trojan_adapter.get_adapter_status(),
                "banana_gun": banana_gun_adapter.get_adapter_status()
            },
            "boundaries": {
                "live_trading_allowed": settings.ALLOW_LIVE_TRADING,
                "execution_authority": "Core Only",
                "private_keys_stored": False,
                "bot_delegation_allowed": False
            }
        }

search_service = SearchService()
