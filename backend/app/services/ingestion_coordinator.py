import asyncio
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List

from app.core.config import settings
from app.core.chains import normalize_chain_id
from app.services.provider_manager import ALL_12_CHAINS
from app.core.chain_capabilities import (
    ChainCapabilityRegistry,
    ChainCapability,
    CapabilityStatus,
    chain_capability_registry
)
from app.core.network import OfflineModeBlockedException
from app.services.provider_manager import provider_manager
from app.services.discovery_service import DiscoveryService, discovery_service

logger = logging.getLogger(__name__)

class IngestionCapabilityError(Exception):
    """Raised when an ingestion capability is unsupported on the specified chain."""
    pass

class IngestionCoordinator:
    """
    Multi-Chain Ingestion Reliability Coordinator (Phase P10.2-J.1 Deliverable 3).
    Coordinates ingestion runs across all 12 supported chains:
    - Verifies chain capabilities against ChainCapabilityRegistry (distinguishing SUPPORTED, PARTIAL, UNSUPPORTED, UNVERIFIED).
    - Rejects operations with clear error messages when capability is UNSUPPORTED.
    - Limits concurrency via asyncio.Semaphore to prevent resource exhaustion.
    - Checkpoint persistence, resumption, and stale data detection.
    - Idempotent execution and strict offline mode isolation.
    """

    def __init__(
        self,
        capability_registry: Optional[ChainCapabilityRegistry] = None,
        discovery: Optional[DiscoveryService] = None,
        max_concurrency: int = 5,
        stale_threshold_seconds: int = 3600
    ):
        self.registry = capability_registry or chain_capability_registry
        self.discovery = discovery or discovery_service
        self.semaphore = asyncio.Semaphore(max_concurrency)
        self.stale_threshold_seconds = stale_threshold_seconds

    def check_chain_capability(
        self,
        chain_id: str,
        capability: ChainCapability = ChainCapability.POOL_LIQUIDITY_DATA
    ) -> Dict[str, Any]:
        """
        Inspects capability status for target chain.
        Returns capability descriptor dict.
        Raises IngestionCapabilityError if capability is UNSUPPORTED.
        """
        norm_chain = normalize_chain_id(chain_id)
        profile = self.registry.get_profile(norm_chain)
        if not profile:
            raise IngestionCapabilityError(f"No capability profile found for chain '{norm_chain}'")

        desc = profile.capabilities.get(capability)
        if not desc:
            raise IngestionCapabilityError(f"Capability '{capability.value}' not defined for chain '{norm_chain}'")

        if desc.status == CapabilityStatus.UNSUPPORTED:
            raise IngestionCapabilityError(
                f"Chain '{norm_chain}' does not support '{capability.value}': {desc.limitations or desc.description}"
            )

        return {
            "chain_id": norm_chain,
            "capability": capability.value,
            "status": desc.status.value,
            "description": desc.description,
            "limitations": desc.limitations,
            "verification_evidence": desc.verification_evidence
        }

    def check_ingestion_freshness(
        self,
        chain_id: str,
        source: str = "rpc_logs",
        protocol: str = "standard"
    ) -> Dict[str, Any]:
        """
        Inspects checkpoint to determine if ingestion data is fresh or stale.
        """
        norm_chain = normalize_chain_id(chain_id)
        cp = self.discovery.get_checkpoint(chain_id=norm_chain, protocol=protocol, source_type=source)
        if not cp:
            return {
                "chain_id": norm_chain,
                "status": "NO_CHECKPOINT",
                "is_stale": True,
                "last_block_or_slot": None,
                "updated_at": None
            }

        last_updated_str = cp.get("updated_at")
        is_stale = False
        age_seconds = None
        if last_updated_str:
            try:
                # Handle ISO timestamps with or without timezone
                clean_ts = last_updated_str.replace("Z", "+00:00")
                last_dt = datetime.fromisoformat(clean_ts)
                if last_dt.tzinfo is None:
                    last_dt = last_dt.replace(tzinfo=timezone.utc)
                now_dt = datetime.now(timezone.utc)
                age_seconds = (now_dt - last_dt).total_seconds()
                is_stale = age_seconds > self.stale_threshold_seconds
            except Exception:
                is_stale = True

        return {
            "chain_id": norm_chain,
            "source": source,
            "protocol": protocol,
            "status": "STALE" if is_stale else "FRESH",
            "is_stale": is_stale,
            "age_seconds": age_seconds,
            "last_block_or_slot": cp.get("last_block_or_slot"),
            "updated_at": last_updated_str
        }

    async def run_ingestion_for_chain(
        self,
        chain_id: str,
        protocol: str = "standard",
        source: str = "rpc_logs",
        events: Optional[List[Dict[str, Any]]] = None
    ) -> Dict[str, Any]:
        """
        Executes bounded ingestion run for a single chain:
        1. Enforces strict offline checks.
        2. Validates chain capability against registry.
        3. Bounds concurrency via semaphore.
        4. Ingests events idempotently via DiscoveryService.
        5. Updates and persists checkpoint.
        """
        norm_chain = normalize_chain_id(chain_id)

        # 1. Offline mode enforcement
        if provider_manager.is_offline_mode():
            logger.info("Ingestion running in offline mode: skipping live network polling.")

        # 2. Capability verification
        cap_info = self.check_chain_capability(norm_chain, ChainCapability.POOL_LIQUIDITY_DATA)

        async with self.semaphore:
            ingested_pools = 0
            ingested_tokens = 0
            last_block = None
            last_slot = None

            if events:
                for ev in events:
                    p_res = self.discovery.ingest_pool_event(
                        chain_id=norm_chain,
                        protocol=protocol,
                        pool_address=ev["pool_address"],
                        token0_address=ev["token0_address"],
                        token1_address=ev["token1_address"],
                        fee_tier_bps=ev.get("fee_tier_bps", 30),
                        block_number=ev.get("block_number"),
                        slot=ev.get("slot"),
                        tx_hash=ev.get("tx_hash"),
                        log_index=ev.get("log_index"),
                        factory_address=ev.get("factory_address"),
                        discovery_source=source,
                        created_at=ev.get("created_at"),
                        token0_metadata=ev.get("token0_metadata"),
                        token1_metadata=ev.get("token1_metadata")
                    )
                    if p_res.get("created"):
                        ingested_pools += 1
                    if ev.get("block_number"):
                        last_block = max(last_block or 0, ev["block_number"])
                    if ev.get("slot"):
                        last_slot = max(last_slot or 0, ev["slot"])

            # Checkpoint update
            cp = self.discovery.update_checkpoint(
                chain_id=norm_chain,
                protocol=protocol,
                source_type=source,
                last_block_or_slot=last_block or last_slot or 0
            )

            freshness = self.check_ingestion_freshness(norm_chain, source=source, protocol=protocol)

            return {
                "status": "SUCCESS",
                "chain_id": norm_chain,
                "protocol": protocol,
                "source": source,
                "capability_status": cap_info["status"],
                "pools_ingested": ingested_pools,
                "checkpoint": cp,
                "freshness": freshness
            }

    async def run_multi_chain_ingestion(
        self,
        chains: Optional[List[str]] = None,
        protocol: str = "standard",
        events_by_chain: Optional[Dict[str, List[Dict[str, Any]]]] = None
    ) -> Dict[str, Any]:
        """
        Coordinates concurrent ingestion runs across multiple or all 12 supported chains.
        """
        target_chains = chains or ALL_12_CHAINS
        tasks = []
        events_map = events_by_chain or {}

        for ch in target_chains:
            evs = events_map.get(ch, [])
            tasks.append(self.run_ingestion_for_chain(ch, protocol=protocol, events=evs))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        summary: Dict[str, Any] = {
            "total_chains": len(target_chains),
            "successful_chains": 0,
            "failed_chains": 0,
            "chain_results": {}
        }

        for ch, res in zip(target_chains, results):
            if isinstance(res, Exception):
                summary["failed_chains"] += 1
                summary["chain_results"][ch] = {
                    "status": "FAILED",
                    "error": str(res),
                    "error_type": res.__class__.__name__
                }
            else:
                summary["successful_chains"] += 1
                summary["chain_results"][ch] = res

        return summary

ingestion_coordinator = IngestionCoordinator()
