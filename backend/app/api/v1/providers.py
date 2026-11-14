from typing import List, Dict, Any, Optional
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from app.services.provider_manager import provider_manager, EndpointConfig, OfflineModeException, QuotaExhaustedException
from app.services.credential_vault import credential_vault

router = APIRouter(prefix="/providers", tags=["Connector & Provider Manager"])

class AddEndpointRequest(BaseModel):
    endpoint_id: str
    chain_id: str
    url: str
    provider_name: str
    category: str = "rpc"
    priority: int = 1
    weight: int = 100
    timeout_ms: int = 5000
    rate_limit_rpm: int = 120
    budget_tier: str = "custom"
    daily_quota_limit: int = 10000

class StoreCredentialRequest(BaseModel):
    credential_id: str
    provider_name: str
    category: str
    secret_value: str

class OfflineSwitchRequest(BaseModel):
    offline: bool

@router.get("", response_model=List[EndpointConfig])
async def list_provider_endpoints(chain_id: Optional[str] = Query(None, description="Filter by CAIP-2 chain")):
    """
    Returns configured RPC and data provider endpoints across all 12 chains.
    """
    return provider_manager.list_endpoints(chain_id)

@router.post("/endpoint", response_model=EndpointConfig)
async def add_provider_endpoint(payload: AddEndpointRequest):
    """
    User-owned custom RPC/WebSocket endpoint registration (API-01).
    """
    ep = EndpointConfig(
        endpoint_id=payload.endpoint_id,
        chain_id=payload.chain_id,
        url=payload.url,
        provider_name=payload.provider_name,
        category=payload.category,
        priority=payload.priority,
        weight=payload.weight,
        timeout_ms=payload.timeout_ms,
        rate_limit_rpm=payload.rate_limit_rpm,
        budget_tier=payload.budget_tier,
        daily_quota_limit=payload.daily_quota_limit,
        is_active=True
    )
    return provider_manager.add_endpoint(ep)

@router.delete("/endpoint/{endpoint_id}")
async def delete_provider_endpoint(endpoint_id: str):
    """
    Deletes a user-configured endpoint.
    """
    success = provider_manager.delete_endpoint(endpoint_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"Endpoint {endpoint_id} not found")
    return {"status": "success", "deleted_endpoint_id": endpoint_id}

@router.post("/test/{endpoint_id}")
async def test_endpoint_connectivity(endpoint_id: str):
    """
    Executes live protocol connectivity probe and latency test (API-01).
    """
    try:
        return await provider_manager.test_endpoint(endpoint_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/credentials")
async def store_provider_credential(payload: StoreCredentialRequest):
    """
    Securely encrypts and stores user API keys using AES-256-GCM (API-03).
    Returns confirmation with credential value strictly masked.
    """
    meta = credential_vault.store_credential(
        credential_id=payload.credential_id,
        provider_name=payload.provider_name,
        category=payload.category,
        secret_value=payload.secret_value
    )
    return {
        "status": "success",
        "message": "Credential encrypted and safely stored in zero-exposure vault",
        "credential": meta
    }

@router.get("/credentials")
async def list_registered_credentials():
    """
    Lists registered provider credentials with values strictly masked.
    """
    return credential_vault.list_credentials()

@router.post("/offline-switch")
async def toggle_offline_mode(payload: OfflineSwitchRequest):
    """
    Toggles global offline research mode.
    When offline, all outbound network requests are immediately blocked fail-closed.
    """
    provider_manager.set_offline_mode(payload.offline)
    return {
        "status": "success",
        "is_offline": provider_manager.is_offline(),
        "operating_mode": "offline_research" if payload.offline else "paper_trading"
    }

@router.get("/export")
async def export_provider_configuration():
    """
    Exports provider configuration without exposing credentials (API-03).
    """
    return provider_manager.export_safe_configuration()
