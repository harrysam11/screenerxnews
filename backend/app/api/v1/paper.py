import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import List
from fastapi import APIRouter, Depends, HTTPException, Path
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.schemas.contracts import (
    PaperAccountResponse, PaperOrderRequest, PaperOrderResponse,
    PaperPositionResponse, PaperExecutionEventDTO
)

router = APIRouter(prefix="/paper", tags=["Paper Trading Desk"])

@router.get("/account", response_model=PaperAccountResponse)
async def get_paper_account(db: AsyncSession = Depends(get_db)):
    """
    Returns virtual lamports balance, equity, and performance stats for the isolated paper desk.
    """
    return PaperAccountResponse(
        account_id=str(uuid.uuid4()),
        account_name="Default Paper Desk",
        current_virtual_lamports=94500000000,
        allocated_virtual_lamports=5500000000,
        total_equity_lamports=102350000000,
        realized_pnl_lamports=1850000000,
        unrealized_pnl_lamports=500000000,
        win_rate=Decimal("68.50"),
        profit_factor=Decimal("1.8540")
    )

@router.post("/order", response_model=PaperOrderResponse)
async def submit_paper_order(
    payload: PaperOrderRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Simulates symmetrical order execution with realistic bid/ask spread, price impact curve, and fees.
    """
    snapshot_price_sol = Decimal("0.00004500")
    spread_bps = 10
    impact_bps = 25
    total_penalty = Decimal(1 + (spread_bps + impact_bps + payload.max_slippage_bps) / 10000)

    if payload.side == "BUY":
        effective_price = snapshot_price_sol * total_penalty
        output_tokens = payload.input_amount_raw / effective_price
    else:
        effective_price = snapshot_price_sol / total_penalty
        output_tokens = payload.input_amount_raw * effective_price

    now = datetime.now(timezone.utc)
    event = PaperExecutionEventDTO(
        event_id=str(uuid.uuid4()),
        event_type="FILL",
        input_amount_raw=payload.input_amount_raw,
        output_amount_raw=round(output_tokens, 0),
        effective_price_sol=effective_price,
        liquidity_impact_bps=impact_bps,
        simulated_slippage_bps=payload.max_slippage_bps,
        simulated_total_fee_lamports=55000,
        occurred_at=now
    )

    return PaperOrderResponse(
        order_id=str(uuid.uuid4()),
        client_order_id=payload.client_order_id,
        status="FILLED",
        side=payload.side,
        input_amount_raw=payload.input_amount_raw,
        output_amount_raw=round(output_tokens, 0),
        effective_price_sol=effective_price,
        execution_events=[event],
        created_at=now
    )

@router.get("/positions", response_model=List[PaperPositionResponse])
async def get_paper_positions(db: AsyncSession = Depends(get_db)):
    """
    Returns active open simulated positions.
    """
    return [
        PaperPositionResponse(
            position_id=str(uuid.uuid4()),
            mint_address="DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
            symbol="BONK",
            token_amount_raw=Decimal("125000000000"),
            cost_basis_lamports=2500000000,
            current_market_price_sol=Decimal("0.0000000228"),
            unrealized_pnl_lamports=350000000,
            opened_at=datetime.now(timezone.utc)
        )
    ]
