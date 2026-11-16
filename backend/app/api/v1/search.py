"""
Multi-Chain Search & On-Chain Research Endpoints (Phase P10.2-I).

Exposes:
- GET /api/v1/search: unified multi-chain search across all 12 chains.
- GET /api/v1/assets/{chain_id}/{asset_reference}: asset detail & verification provenance.
- GET /api/v1/assets/{asset_id}/market & /api/v1/assets/{chain_id}/{asset_reference}/market: market snapshots & FDV.
- GET /api/v1/assets/{asset_id}/pools & /api/v1/assets/{chain_id}/{asset_reference}/pools: liquidity pool discovery.
- GET /api/v1/assets/{asset_id}/forensics & /api/v1/assets/{chain_id}/{asset_reference}/forensics: forensic safety status.
- GET /api/v1/integrations: reference adapter boundaries and availability status.
"""

from typing import Optional, Dict, Any, List
from fastapi import APIRouter, Query, HTTPException, status
from pydantic import BaseModel, Field

from app.core.chains import normalize_chain_id, CAIP19AssetId
from app.services.search_service import search_service

router = APIRouter(tags=["Multi-Chain Search & Research"])

@router.get("/search")
async def search_multi_chain(
    q: str = Query(..., description="Address, symbol, name, CAIP-19 identifier, or explorer URL"),
    chain_id: Optional[str] = Query(None, description="Optional CAIP-2 chain filter"),
    search_type: str = Query("all", pattern="^(all|token|pool|address)$", description="Search target filter"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0)
):
    """
    Unified multi-chain search across 12 chains.
    Enforces SSRF validation on URLs, handles exact and fuzzy symbols,
    and returns explicit data classifications (ONCHAIN_VERIFIED, DERIVED_SNAPSHOT, etc.).
    """
    return search_service.search(
        query=q,
        chain_id=chain_id,
        search_type=search_type,
        limit=limit,
        offset=offset
    )

@router.get("/assets/{chain_id}/{asset_reference}")
async def get_asset_detail(chain_id: str, asset_reference: str):
    """
    Retrieves asset details, canonical CAIP-19 identity, and cryptographic decimal provenance.
    """
    detail = search_service.get_asset_details(chain_id, asset_reference)
    if not detail or detail.get("data_classification") == "UNAVAILABLE":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=detail.get("detail", f"Asset '{asset_reference}' not found on chain '{chain_id}'") if detail else "Asset not found"
        )
    return detail

@router.get("/assets/{chain_id}/{asset_reference}/market")
async def get_asset_market(chain_id: str, asset_reference: str):
    """
    Returns latest market data snapshot, freshness evaluation, and exact FDV calculation.
    """
    snapshot = search_service.get_asset_market_snapshot(chain_id, asset_reference)
    if snapshot.get("data_classification") == "UNAVAILABLE":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=snapshot.get("unavailable_reason", "Market data unavailable")
        )
    return snapshot

@router.get("/assets/{chain_id}/{asset_reference}/pools")
async def get_asset_pools(chain_id: str, asset_reference: str):
    """
    Returns liquidity pools discovered for this asset across protocols.
    """
    return search_service.get_asset_pools(chain_id, asset_reference)

@router.get("/assets/{chain_id}/{asset_reference}/forensics")
async def get_asset_forensics(chain_id: str, asset_reference: str):
    """
    Returns forensic safety classification (VERIFIED_SAFE_FOR_THIS_CHECK, RISK_DETECTED, etc.).
    """
    forensics = search_service.get_asset_forensics(chain_id, asset_reference)
    if forensics.get("forensic_status") == "UNAVAILABLE":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=forensics.get("reason", "Forensics unavailable")
        )
    return forensics

# =============================================================================
# CAIP-19 / Single-Path Asset Helpers
# =============================================================================

def _parse_asset_id(asset_id: str) -> tuple[str, str]:
    """Parses CAIP-19 or colon-separated chain:address."""
    if "/" in asset_id and ":" in asset_id:
        c19 = CAIP19AssetId.parse(asset_id)
        if c19:
            return c19.chain_id, c19.asset_reference
    if ":" in asset_id:
        parts = asset_id.rsplit(":", 1)
        return parts[0], parts[1]
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"Invalid asset identifier format '{asset_id}'. Use CAIP-19 or chain_id/address."
    )

@router.get("/assets/{asset_id}/market")
async def get_asset_id_market(asset_id: str):
    chain_id, address = _parse_asset_id(asset_id)
    return await get_asset_market(chain_id, address)

@router.get("/assets/{asset_id}/pools")
async def get_asset_id_pools(asset_id: str):
    chain_id, address = _parse_asset_id(asset_id)
    return await get_asset_pools(chain_id, address)

@router.get("/assets/{asset_id}/forensics")
async def get_asset_id_forensics(asset_id: str):
    chain_id, address = _parse_asset_id(asset_id)
    return await get_asset_forensics(chain_id, address)

@router.get("/integrations")
async def get_integrations_status():
    """
    Returns boundary and availability status of external reference tools and adapters.
    Verifies that no external bot has execution or signing authority.
    """
    return search_service.get_integrations_status()
