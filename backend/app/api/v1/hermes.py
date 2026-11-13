from typing import Dict, Any, Optional
from fastapi import APIRouter, Path, Query
from app.services.hermes_analyst import hermes_analyst

router = APIRouter(prefix="/hermes", tags=["Hermes AI Research Analyst"])

@router.get("/dossier/{chain_id}/{asset_address}")
async def get_token_dossier(
    chain_id: str = Path(..., description="CAIP-2 Chain Identifier"),
    asset_address: str = Path(..., description="Contract or mint address"),
    symbol: str = Query("MEME", description="Token symbol"),
    name: str = Query("Meme Token", description="Token name")
) -> Dict[str, Any]:
    """
    Returns a Hermes-synthesized intelligence dossier for an asset.
    """
    return hermes_analyst.synthesize_token_dossier(
        chain_id=chain_id,
        asset_address=asset_address,
        symbol=symbol,
        name=name
    )

@router.get("/daily-digest")
async def get_daily_digest() -> Dict[str, Any]:
    """
    Returns the daily multi-chain intelligence digest for WF-37 dispatch.
    """
    return hermes_analyst.generate_daily_digest()
