from typing import List, Optional, Dict, Any
from decimal import Decimal
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, Query, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.schemas.contracts import (
    PaperAccountResponse,
    PaperOrderRequest,
    PaperOrderResponse,
    PaperPositionResponse
)
from app.services.paper_simulation_engine import paper_simulation_engine
from app.services.persistent_paper_service import persistent_paper_service

router = APIRouter(prefix="/paper", tags=["Paper Trading Desk"])


class UpdateRiskLimitsRequest(BaseModel):
    chain_id: Optional[str] = None
    max_position_size_usd: Optional[float] = None
    max_order_value_usd: Optional[float] = None
    max_portfolio_exposure_usd: Optional[float] = None
    max_daily_loss_usd: Optional[float] = None
    max_drawdown_pct: Optional[float] = None
    max_concurrent_positions: Optional[int] = None
    max_slippage_bps: Optional[int] = None
    stale_data_threshold_seconds: Optional[int] = None


class PaperReconcileRequest(BaseModel):
    chain_id: Optional[str] = "solana:mainnet"


@router.get("/account", response_model=PaperAccountResponse)
async def get_paper_account(
    chain_id: str = Query("solana:mainnet", description="CAIP-2 Chain Identifier"),
    db: AsyncSession = Depends(get_db)
):
    """
    Returns virtual balance, equity, and performance stats for the isolated paper desk.
    """
    return paper_simulation_engine.get_account(chain_id)


@router.get("/accounts")
async def get_paper_accounts(
    db: AsyncSession = Depends(get_db)
):
    """
    Returns simulated accounts across all registered chain desks.
    """
    return persistent_paper_service.list_accounts()


@router.post("/order", response_model=PaperOrderResponse)
async def submit_paper_order(
    payload: PaperOrderRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Simulates symmetrical order execution with realistic bid/ask spread, price impact curve, and fees.
    """
    if getattr(payload, "proposal_id", None) or getattr(payload, "origin", None) == "STRATEGY_PROPOSAL":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Strategy-originated orders must be submitted via the strategy proposal bridge (/api/v1/strategies/proposals/{proposal_id}/execute)."
        )

    # Baseline market price reference for simulation
    mock_market_price = Decimal("0.00004500") if payload.chain_id == "solana:mainnet" else Decimal("0.00000250")
    return paper_simulation_engine.execute_order(
        request=payload,
        current_market_price_native=mock_market_price,
        pool_liquidity_usd=Decimal("1500000.0")
    )


@router.get("/orders")
async def get_paper_orders(
    chain_id: Optional[str] = Query(None, description="CAIP-2 Chain Identifier"),
    status: Optional[str] = Query(None, description="Order status filter (e.g. FILLED, REJECTED, SUBMITTED, CANCELLED)"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db)
):
    """
    Lists historical and active paper orders.
    """
    return persistent_paper_service.list_orders(chain_id=chain_id, status=status, limit=limit, offset=offset)


@router.post("/orders/{order_id}/cancel")
async def cancel_paper_order(
    order_id: str,
    db: AsyncSession = Depends(get_db)
):
    """
    Cancels an open or submitted paper order.
    """
    try:
        return persistent_paper_service.cancel_order(order_id)
    except ValueError as e:
        if "not found" in str(e).lower():
            raise HTTPException(status_code=404, detail=str(e))
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/positions", response_model=List[PaperPositionResponse])
async def get_paper_positions(
    chain_id: str = Query("solana:mainnet", description="CAIP-2 Chain Identifier"),
    db: AsyncSession = Depends(get_db)
):
    """
    Returns active open simulated positions for the specified chain desk.
    """
    positions = paper_simulation_engine.get_positions(chain_id)
    if not positions and chain_id == "solana:mainnet":
        # Provide seeded baseline if positions list is empty
        return [
            PaperPositionResponse(
                position_id="default-pos-bonk",
                mint_address="DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
                asset_address="DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
                chain_id="solana:mainnet",
                symbol="BONK",
                token_amount_raw=Decimal("125000000000"),
                cost_basis_lamports=2500000000,
                cost_basis_native=Decimal("2.5"),
                current_market_price_sol=Decimal("0.0000000228"),
                current_market_price_native=Decimal("0.0000000228"),
                unrealized_pnl_lamports=350000000,
                opened_at=datetime.now(timezone.utc)
            )
        ]
    return positions


@router.get("/fills")
async def get_paper_fills(
    chain_id: Optional[str] = Query(None, description="CAIP-2 Chain Identifier"),
    order_id: Optional[str] = Query(None, description="Filter by Order ID"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db)
):
    """
    Lists executed paper trade fills.
    """
    return persistent_paper_service.list_fills(chain_id=chain_id, order_id=order_id, limit=limit, offset=offset)


@router.get("/valuation")
async def get_portfolio_valuation(
    chain_id: Optional[str] = Query(None, description="Optional CAIP-2 Chain Identifier"),
    db: AsyncSession = Depends(get_db)
):
    """
    Returns total portfolio valuation and equity breakdowns across all desks.
    """
    return persistent_paper_service.get_portfolio_valuation(chain_id=chain_id)


@router.get("/risk")
async def get_paper_risk(
    chain_id: Optional[str] = Query(None, description="Optional CAIP-2 Chain Identifier"),
    db: AsyncSession = Depends(get_db)
):
    """
    Returns active risk management limits and guardrails.
    """
    return persistent_paper_service.get_risk_limits(chain_id=chain_id)


@router.put("/risk")
async def update_paper_risk(
    payload: UpdateRiskLimitsRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Updates risk limits and thresholds for paper trading desks.
    """
    return persistent_paper_service.update_risk_limits(
        chain_id=payload.chain_id,
        max_position_size_usd=payload.max_position_size_usd,
        max_order_value_usd=payload.max_order_value_usd,
        max_portfolio_exposure_usd=payload.max_portfolio_exposure_usd,
        max_daily_loss_usd=payload.max_daily_loss_usd,
        max_drawdown_pct=payload.max_drawdown_pct,
        max_concurrent_positions=payload.max_concurrent_positions,
        max_slippage_bps=payload.max_slippage_bps,
        stale_data_threshold_seconds=payload.stale_data_threshold_seconds
    )


@router.post("/reconcile")
async def reconcile_paper_portfolio(
    payload: Optional[PaperReconcileRequest] = None,
    chain_id: Optional[str] = Query(None, description="Optional CAIP-2 Chain Identifier"),
    db: AsyncSession = Depends(get_db)
):
    """
    Executes portfolio reconciliation verifying ending equity == cash + positions value.
    """
    target_chain = (payload.chain_id if payload and payload.chain_id else None) or chain_id or "solana:mainnet"
    try:
        return persistent_paper_service.reconcile_portfolio(target_chain)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
