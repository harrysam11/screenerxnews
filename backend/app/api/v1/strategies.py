"""
FastAPI REST Endpoints for P10.2-G Strategy SDK and Plugin Contract.
Exposes strategy management, evaluation, replay, signal retrieval, and intent proposals.
Strategies produce proposed signals only and cannot execute orders or modify balances.
"""

import os
import sys
import secrets
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, HTTPException, Query, status, Request, Header, Depends
from pydantic import BaseModel, Field

from app.core.config import settings
from app.strategy.service import strategy_service
from app.strategy.base import StrategyLifecycle, StrategyMode

router = APIRouter(prefix="/strategies", tags=["Strategy SDK & Plugins"])


def verify_strategy_access(
    request: Request,
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token"),
    authorization: Optional[str] = Header(None)
):
    """
    Protects strategy execution, configuration, run history, and intent proposal endpoints.
    - Supports X-API-Key, X-Admin-Token, and Authorization: Bearer <token>.
    - Constant-time token comparison with secrets.compare_digest.
    - Proxy header detection: if X-Forwarded-For, X-Real-IP, or Forwarded headers are present,
      mandates a valid ADMIN_API_TOKEN even if peer IP appears to be loopback.
    - If peer IP is non-loopback, mandates a valid ADMIN_API_TOKEN.
    - 'testclient' exemption is strictly confined to test mode (TESTING=1, PYTEST_CURRENT_TEST, or pytest loaded).
    """
    expected_token = getattr(settings, "ADMIN_API_TOKEN", None) or os.environ.get("ADMIN_API_TOKEN")

    # Extract provided token
    token = x_api_key or x_admin_token
    if not token and authorization:
        if authorization.startswith("Bearer "):
            token = authorization[7:].strip()
        else:
            token = authorization.strip()

    # Detect reverse-proxy forwarding headers
    fwd_headers = [
        request.headers.get("x-forwarded-for"),
        request.headers.get("x-real-ip"),
        request.headers.get("forwarded")
    ]
    is_proxied = any(bool(h and h.strip()) for h in fwd_headers)

    client_host = request.client.host if request.client else "127.0.0.1"
    is_testing = (
        os.environ.get("TESTING") == "1"
        or "PYTEST_CURRENT_TEST" in os.environ
        or "pytest" in sys.modules
    )
    loopback_hosts = {"127.0.0.1", "::1", "localhost", "::ffff:127.0.0.1"}
    if is_testing:
        loopback_hosts.add("testclient")

    is_loopback = client_host in loopback_hosts

    # If ADMIN_API_TOKEN is configured: mandates matching token for all requests
    if expected_token:
        if not token or not secrets.compare_digest(token, expected_token):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing administrative API token (X-API-Key, X-Admin-Token, or Bearer required)."
            )
        return

    # If ADMIN_API_TOKEN is not configured: forbid remote or proxied requests
    if is_proxied or not is_loopback:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Remote/proxied administrative access from {client_host} is forbidden without ADMIN_API_TOKEN configured."
        )


class ValidateConfigRequest(BaseModel):
    parameters: Dict[str, Any] = Field(..., description="Strategy configuration parameters to validate")


class SaveConfigRequest(BaseModel):
    parameters: Dict[str, Any] = Field(..., description="Strategy parameters")
    risk_preferences: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Strategy-specific risk preferences")
    chain_id: Optional[str] = Field(None, description="Optional chain-specific override")


class TransitionStatusRequest(BaseModel):
    target_status: str = Field(..., description="Target lifecycle state (e.g. ENABLED, PAUSED, DISABLED, RETIRED)")


class EvaluateStrategyRequest(BaseModel):
    chain_id: str = Field(..., description="CAIP-2 Chain identifier")
    asset_id: str = Field(..., description="Target token address or identifier")
    pool_id: Optional[str] = Field(None, description="Optional pool address")
    mode: str = Field(default="RESEARCH", description="Execution mode: RESEARCH, BACKTEST, or PAPER")
    account_id: Optional[str] = Field(None, description="Optional account identifier")
    parameter_overrides: Optional[Dict[str, Any]] = Field(None, description="Optional runtime parameter overrides")


class ReplayEvaluationRequest(BaseModel):
    run_id: str = Field(..., description="Run identifier to replay")


class ProposeIntentRequest(BaseModel):
    signal_id: str = Field(..., description="Signal identifier to propose")
    account_id: str = Field(..., description="Target desk account identifier")


@router.get("", response_model=List[Dict[str, Any]])
async def list_strategies():
    """Returns all registered strategies with capability metadata and lifecycle state."""
    return strategy_service.list_strategies()


@router.get("/{strategy_id}", response_model=Dict[str, Any])
async def get_strategy_detail(strategy_id: str):
    """Retrieves metadata and current active configuration for a strategy."""
    strat = strategy_service.get_strategy(strategy_id)
    if not strat:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Strategy {strategy_id} not found")
    meta = strat.get_metadata().model_dump()
    cfg = strategy_service.get_configuration(strategy_id)
    return {
        "metadata": meta,
        "active_configuration": cfg,
        "environment": "PAPER_SIMULATED"
    }


@router.post("/{strategy_id}/validate-config", dependencies=[Depends(verify_strategy_access)])
async def validate_strategy_config(strategy_id: str, payload: ValidateConfigRequest):
    """Validates configuration parameters against strategy schema without saving."""
    is_valid, err = strategy_service.validate_configuration(strategy_id, payload.parameters)
    return {
        "strategy_id": strategy_id,
        "valid": is_valid,
        "error": err
    }


@router.post("/{strategy_id}/config", dependencies=[Depends(verify_strategy_access)])
async def save_strategy_config(strategy_id: str, payload: SaveConfigRequest):
    """Saves and activates a strategy configuration."""
    try:
        saved = strategy_service.save_configuration(
            strategy_id=strategy_id,
            parameters=payload.parameters,
            risk_preferences=payload.risk_preferences,
            chain_id=payload.chain_id
        )
        return saved
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/{strategy_id}/status", dependencies=[Depends(verify_strategy_access)])
async def transition_strategy_status(strategy_id: str, payload: TransitionStatusRequest):
    """Transitions a strategy's lifecycle state (DISCOVERED, VALIDATED, ENABLED, RUNNING, PAUSED, DISABLED, RETIRED)."""
    try:
        target = StrategyLifecycle(payload.target_status.upper())
        return strategy_service.transition_lifecycle(strategy_id, target)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/{strategy_id}/evaluate", dependencies=[Depends(verify_strategy_access)])
async def evaluate_strategy_endpoint(strategy_id: str, payload: EvaluateStrategyRequest):
    """
    Evaluates a strategy against Core market data, returning a proposed trade signal.
    Does NOT execute orders or modify balances.
    """
    try:
        mode = StrategyMode(payload.mode.upper())
        signal = strategy_service.evaluate_strategy(
            strategy_id=strategy_id,
            chain_id=payload.chain_id,
            asset_id=payload.asset_id,
            pool_id=payload.pool_id,
            mode=mode,
            account_id=payload.account_id,
            parameter_overrides=payload.parameter_overrides
        )
        return signal.model_dump()
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/replay", dependencies=[Depends(verify_strategy_access)])
async def replay_strategy_run(payload: ReplayEvaluationRequest):
    """Replays a historical evaluation run and validates deterministic reproducibility."""
    try:
        return strategy_service.replay_evaluation(payload.run_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@router.post("/propose-intent", dependencies=[Depends(verify_strategy_access)])
async def propose_trade_intent_endpoint(payload: ProposeIntentRequest):
    """
    Validates a proposed signal against Core risk rules.
    Explicitly DOES NOT execute an order; produces an authorized proposal for Core approval.
    """
    try:
        return strategy_service.propose_trade_intent(payload.signal_id, payload.account_id)
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/{strategy_id}/signals", dependencies=[Depends(verify_strategy_access)])
async def get_strategy_signals(
    strategy_id: str,
    chain_id: Optional[str] = Query(None),
    action: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0)
):
    """Retrieves historical signals produced by a strategy."""
    return strategy_service.list_signals(strategy_id=strategy_id, chain_id=chain_id, action=action, limit=limit, offset=offset)


@router.get("/{strategy_id}/runs", dependencies=[Depends(verify_strategy_access)])
async def get_strategy_runs(
    strategy_id: str,
    status_filter: Optional[str] = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0)
):
    """Retrieves evaluation run execution history and durations."""
    return strategy_service.list_runs(strategy_id=strategy_id, status=status_filter, limit=limit, offset=offset)


class ExecuteProposalRequest(BaseModel):
    account_id: str = Field(..., description="Target desk account identifier")


@router.post("/proposals/{proposal_id}/execute", dependencies=[Depends(verify_strategy_access)])
async def execute_strategy_proposal_endpoint(proposal_id: str, payload: ExecuteProposalRequest):
    """
    Executes a strategy-originated paper order authorized by a persistent proposal.
    Enforces atomic proposal consumption, paper order creation, fill, and ledger journal posting.
    """
    from app.services.persistent_paper_service import persistent_paper_service
    try:
        order_resp = persistent_paper_service.execute_strategy_proposal(
            proposal_id=proposal_id,
            account_id=payload.account_id
        )
        return order_resp.model_dump()
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@router.get("/proposals/{proposal_id}", dependencies=[Depends(verify_strategy_access)])
async def get_strategy_proposal_endpoint(proposal_id: str):
    """Retrieves a persistent strategy proposal by its identifier."""
    from app.services.persistent_paper_service import persistent_paper_service
    prop = persistent_paper_service.get_strategy_proposal(proposal_id)
    if not prop:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Proposal {proposal_id} not found")
    return prop


@router.get("/proposals/reconcile", dependencies=[Depends(verify_strategy_access)])
async def reconcile_proposals_endpoint(
    chain_id: Optional[str] = Query(None, description="Optional CAIP-2 chain filter")
):
    """Audits proposal-to-order lifecycle and reconciles consumed proposals against orders and ledger."""
    from app.services.persistent_paper_service import persistent_paper_service
    return persistent_paper_service.reconcile_strategy_proposals(chain_id=chain_id)
