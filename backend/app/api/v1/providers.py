import time
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, HTTPException, Query, Body, status, Request, Header, Depends
from pydantic import BaseModel, Field
from app.core.config import settings
from app.core.network import get_http_client, OfflineModeBlockedException
from app.services.provider_manager import provider_manager, ProviderCapability, ALL_12_CHAINS
from app.services.credential_vault import credential_vault

router = APIRouter(prefix="/providers", tags=["providers"])

def verify_admin_access(
    request: Request,
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    authorization: Optional[str] = Header(None)
):
    """
    SEC-06: Protects administrative/mutating provider configuration endpoints.
    - Default to loopback (127.0.0.1, ::1, testclient).
    - If ADMIN_API_TOKEN is configured: mandates matching X-API-Key or Bearer token.
    - If ADMIN_API_TOKEN is NOT configured and request originates from remote host: rejects with HTTP 403 Forbidden.
    """
    if settings.ADMIN_API_TOKEN:
        token = x_api_key
        if not token and authorization:
            if authorization.startswith("Bearer "):
                token = authorization[7:].strip()
            else:
                token = authorization.strip()
        if not token or token != settings.ADMIN_API_TOKEN:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing administrative API token (X-API-Key or Bearer authorization required)."
            )
        return

    client_host = request.client.host if request.client else "127.0.0.1"
    loopback_hosts = {"127.0.0.1", "::1", "localhost", "testclient"}
    if client_host not in loopback_hosts:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Remote administrative access from {client_host} is forbidden. Configure ADMIN_API_TOKEN to enable remote administration."
        )

# Pydantic Request / Response Models
class ProviderRegisterRequest(BaseModel):
    id: str
    name: str
    provider_type: str
    tier: str = "free"
    is_enabled: bool = False
    is_paid: bool = False
    priority: int = 100
    capabilities: List[str] = Field(default_factory=list)
    chains: List[str] = Field(default_factory=lambda: list(ALL_12_CHAINS))
    auth_type: str = "none"
    rate_limit_rpm: int = 60
    daily_request_budget: int = 1000
    daily_spend_budget_usd: float = 0.0
    timeout_seconds: float = 10.0
    max_retries: int = 3

class CredentialStoreRequest(BaseModel):
    secret: str
    credential_type: str = "api_key"

class RpcEndpointRegisterRequest(BaseModel):
    id: str
    chain_id: str
    name: str
    endpoint_url: str
    provider_id: Optional[str] = None
    ws_url: Optional[str] = None
    priority: int = 100
    is_enabled: bool = False
    is_paid: bool = False
    daily_budget: int = 50000

class ModeChangeRequest(BaseModel):
    mode: str

class FallbackRuleRequest(BaseModel):
    capability: str
    primary_provider_id: str
    fallback_provider_id: str
    chain_id: Optional[str] = None
    is_authorized: bool = True
    priority: int = 10

# =============================================================================
# Providers Management
# =============================================================================

@router.get("")
def list_providers(
    capability: Optional[str] = Query(None),
    chain_id: Optional[str] = Query(None),
    enabled_only: bool = Query(False)
):
    """
    Lists user-configured data and RPC providers.
    """
    providers = provider_manager.list_providers(
        capability=capability,
        chain_id=chain_id,
        enabled_only=enabled_only
    )
    return {"status": "SUCCESS", "count": len(providers), "providers": providers}

@router.post("", status_code=status.HTTP_201_CREATED, dependencies=[Depends(verify_admin_access)])
def register_provider(req: ProviderRegisterRequest):
    """
    Registers or updates a user-configured provider.
    """
    created = provider_manager.register_provider(
        provider_id=req.id,
        name=req.name,
        provider_type=req.provider_type,
        tier=req.tier,
        is_enabled=req.is_enabled,
        is_paid=req.is_paid,
        priority=req.priority,
        capabilities=req.capabilities,
        chains=req.chains,
        auth_type=req.auth_type,
        rate_limit_rpm=req.rate_limit_rpm,
        daily_request_budget=req.daily_request_budget,
        daily_spend_budget_usd=req.daily_spend_budget_usd,
        timeout_seconds=req.timeout_seconds,
        max_retries=req.max_retries
    )
    return {"status": "SUCCESS", "provider": created}

@router.get("/{provider_id}")
def get_provider(provider_id: str):
    p = provider_manager.get_provider(provider_id)
    if not p:
        raise HTTPException(status_code=404, detail="Provider not found")
    return {"status": "SUCCESS", "provider": p}

@router.post("/{provider_id}/enable", dependencies=[Depends(verify_admin_access)])
def enable_provider(provider_id: str):
    success = provider_manager.enable_provider(provider_id)
    if not success:
        raise HTTPException(status_code=404, detail="Provider not found")
    return {"status": "SUCCESS", "provider_id": provider_id, "is_enabled": True}

@router.post("/{provider_id}/disable", dependencies=[Depends(verify_admin_access)])
def disable_provider(provider_id: str):
    success = provider_manager.disable_provider(provider_id)
    if not success:
        raise HTTPException(status_code=404, detail="Provider not found")
    return {"status": "SUCCESS", "provider_id": provider_id, "is_enabled": False}

# =============================================================================
# Encrypted Credentials (Zero-Plaintext)
# =============================================================================

@router.post("/{provider_id}/credentials", status_code=status.HTTP_201_CREATED, dependencies=[Depends(verify_admin_access)])
def store_credential(provider_id: str, req: CredentialStoreRequest):
    """
    Stores an encrypted credential for a provider in the local encrypted vault.
    Never exposes or logs the plaintext secret.
    """
    p = provider_manager.get_provider(provider_id)
    if not p:
        raise HTTPException(status_code=404, detail="Provider not found")

    cred = credential_vault.store_credential(
        provider_id=provider_id,
        secret=req.secret,
        credential_type=req.credential_type
    )
    return {"status": "SUCCESS", "credential": cred}

@router.get("/{provider_id}/credentials")
def list_provider_credentials(provider_id: str):
    """
    Lists stored credentials for a provider with safe redacted previews.
    """
    creds = credential_vault.list_credentials(provider_id=provider_id)
    return {"status": "SUCCESS", "count": len(creds), "credentials": creds}

@router.post("/credentials/{credential_id}/revoke", dependencies=[Depends(verify_admin_access)])
def revoke_credential(credential_id: str):
    success = credential_vault.revoke_credential(credential_id)
    if not success:
        raise HTTPException(status_code=404, detail="Credential not found or already revoked")
    return {"status": "SUCCESS", "credential_id": credential_id, "is_revoked": True}

# =============================================================================
# RPC Endpoints (12 Chains)
# =============================================================================

@router.get("/rpc-endpoints/list")
def list_rpc_endpoints(
    chain_id: Optional[str] = Query(None),
    enabled_only: bool = Query(False)
):
    endpoints = provider_manager.list_rpc_endpoints(chain_id=chain_id, enabled_only=enabled_only)
    return {"status": "SUCCESS", "count": len(endpoints), "endpoints": endpoints}

@router.post("/rpc-endpoints", status_code=status.HTTP_201_CREATED, dependencies=[Depends(verify_admin_access)])
def register_rpc_endpoint(req: RpcEndpointRegisterRequest):
    created = provider_manager.register_rpc_endpoint(
        endpoint_id=req.id,
        chain_id=req.chain_id,
        name=req.name,
        endpoint_url=req.endpoint_url,
        provider_id=req.provider_id,
        ws_url=req.ws_url,
        priority=req.priority,
        is_enabled=req.is_enabled,
        is_paid=req.is_paid,
        daily_budget=req.daily_budget
    )
    return {"status": "SUCCESS", "endpoint": created}

@router.post("/rpc-endpoints/{endpoint_id}/enable", dependencies=[Depends(verify_admin_access)])
def enable_rpc_endpoint(endpoint_id: str):
    success = provider_manager.enable_rpc_endpoint(endpoint_id)
    if not success:
        raise HTTPException(status_code=404, detail="RPC Endpoint not found")
    return {"status": "SUCCESS", "endpoint_id": endpoint_id, "is_enabled": True}

@router.post("/rpc-endpoints/{endpoint_id}/disable", dependencies=[Depends(verify_admin_access)])
def disable_rpc_endpoint(endpoint_id: str):
    success = provider_manager.disable_rpc_endpoint(endpoint_id)
    if not success:
        raise HTTPException(status_code=404, detail="RPC Endpoint not found")
    return {"status": "SUCCESS", "endpoint_id": endpoint_id, "is_enabled": False}

@router.post("/rpc-endpoints/{endpoint_id}/test", dependencies=[Depends(verify_admin_access)])
async def test_rpc_endpoint(endpoint_id: str):
    """
    Tests connectivity to an RPC endpoint. Blocks immediately in offline mode.
    """
    ep = provider_manager.get_rpc_endpoint(endpoint_id)
    if not ep:
        raise HTTPException(status_code=404, detail="RPC Endpoint not found")

    if provider_manager.is_offline_mode():
        return {
            "status": "OFFLINE_BLOCKED",
            "success": False,
            "message": "Testing external endpoints is disabled in offline mode"
        }

    start = time.monotonic()
    now_str = datetime.now(timezone.utc).isoformat()
    endpoint_url = ep["endpoint_url"]

    # Simple JSON-RPC ping
    payload = {"jsonrpc": "2.0", "id": 1, "method": "web3_clientVersion", "params": []}
    if ep["chain_id"].startswith("solana"):
        payload = {"jsonrpc": "2.0", "id": 1, "method": "getHealth", "params": []}

    try:
        async with get_http_client(timeout=5.0) as client:
            resp = await client.post(endpoint_url, json=payload)
            elapsed_ms = round((time.monotonic() - start) * 1000, 2)
            is_ok = (resp.status_code == 200)

            return {
                "status": "SUCCESS" if is_ok else "HTTP_ERROR",
                "success": is_ok,
                "status_code": resp.status_code,
                "latency_ms": elapsed_ms,
                "checked_at": now_str
            }
    except OfflineModeBlockedException:
        return {
            "status": "OFFLINE_BLOCKED",
            "success": False,
            "message": "Testing external endpoints is disabled in offline mode"
        }
    except Exception as e:
        elapsed_ms = round((time.monotonic() - start) * 1000, 2)
        return {
            "status": "CONNECTION_FAILED",
            "success": False,
            "latency_ms": elapsed_ms,
            "error": str(e),
            "checked_at": now_str
        }

# =============================================================================
# Operating Mode & Offline Control
# =============================================================================

@router.get("/mode/status")
def get_operating_mode():
    return {
        "status": "SUCCESS",
        "operating_mode": provider_manager.set_operating_mode(provider_manager.is_offline_mode() and "offline_research" or "online_research") if False else provider_manager._get_connection() and "active",
        "mode": "offline_research" if provider_manager.is_offline_mode() else "online_research",
        "is_offline": provider_manager.is_offline_mode(),
        "allow_live_trading": False
    }

@router.post("/mode/set", dependencies=[Depends(verify_admin_access)])
def set_operating_mode(req: ModeChangeRequest):
    try:
        new_mode = provider_manager.set_operating_mode(req.mode)
        return {
            "status": "SUCCESS",
            "operating_mode": new_mode,
            "is_offline": provider_manager.is_offline_mode()
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

# =============================================================================
# Fallback Rules & Quotas
# =============================================================================

@router.get("/fallback-rules")
def list_fallback_rules(capability: Optional[str] = Query(None)):
    rules = provider_manager.list_fallback_rules(capability=capability)
    return {"status": "SUCCESS", "count": len(rules), "rules": rules}

@router.post("/fallback-rules", status_code=status.HTTP_201_CREATED, dependencies=[Depends(verify_admin_access)])
def add_fallback_rule(req: FallbackRuleRequest):
    rule = provider_manager.add_fallback_rule(
        capability=req.capability,
        primary_provider_id=req.primary_provider_id,
        fallback_provider_id=req.fallback_provider_id,
        chain_id=req.chain_id,
        is_authorized=req.is_authorized,
        priority=req.priority
    )
    return {"status": "SUCCESS", "rule": rule}

@router.get("/usage")
def get_usage_summary(
    provider_id: Optional[str] = Query(None),
    day_date: Optional[str] = Query(None)
):
    usage = provider_manager.get_usage_summary(provider_id=provider_id, day_date=day_date)
    return {"status": "SUCCESS", "count": len(usage), "records": usage}

@router.get("/audit-log")
def get_audit_log(
    event_type: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500)
):
    events = provider_manager.get_audit_log(event_type=event_type, limit=limit)
    return {"status": "SUCCESS", "count": len(events), "events": events}
