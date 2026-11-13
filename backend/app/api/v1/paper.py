import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import List
from fastapi import APIRouter, Depends, HTTPException, Path
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.schemas.contracts import (
    PaperAccountResponse, PaperOrderRequest, PaperOrderResponse, PaperPositionResponse
)

router = APIRouter(prefix="/paper", tags=["Paper Trading Desk"])

@router.get("/account", response_model=PaperAccountResponse)
async def get_paper_account(db: AsyncSession = Depends(get_db)):
    """
    Returns virtual balance, equity, and performance stats for the isolated paper trading desk.
    """
    return PaperAccountResponse(
        account_id=str(uuid.uuid4()),
        account_name="Default Paper Desk",
        virtual_sol_balance=Decimal("94.500000000"),
        allocated_sol=Decimal("5.500000000"),
        total_equity_sol=Decimal("102.350000000"),
        realized_pnl_sol=Decimal("1.850000000"),
        unrealized_pnl_sol=Decimal("0.500000000"),
        win_rate=Decimal("68.50"),
        profit_factor=Decimal("1.8540")
    )

@router.post("/order", response_model=PaperOrderResponse)
async def submit_paper_order(
    payload: PaperOrderRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Simulates order execution with realistic slippage, liquidity impact, and fees.
    """
    snapshot_price = Decimal("0.00004500") # Virtual SOL price
    # Apply simulated slippage penalty (e.g. 1.2%)
    fill_price = snapshot_price * Decimal("1.012")
    tokens_received = (payload.amount_sol / fill_price)

    return PaperOrderResponse(
        order_id=str(uuid.uuid4()),
        status="FILLED",
        side=payload.side,
        amount_sol=payload.amount_sol,
        fill_price_sol=fill_price,
        tokens_received=round(tokens_received, 0),
        simulated_slippage_bps=120,
        simulated_fee_sol=Decimal("0.0005"),
        filled_at=datetime.now(timezone.utc)
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
            token_amount=Decimal("125000000"),
            cost_basis_sol=Decimal("2.500000000"),
            current_value_sol=Decimal("2.850000000"),
            unrealized_pnl_sol=Decimal("0.350000000"),
            unrealized_pnl_pct=Decimal("14.00"),
            average_entry_price_sol=Decimal("0.000000020"),
            current_market_price_sol=Decimal("0.0000000228"),
            opened_at=datetime.now(timezone.utc)
        )
    ]

@router.post("/positions/{position_id}/close", response_model=PaperOrderResponse)
async def close_paper_position(
    position_id: str = Path(..., description="Paper Position UUID"),
    db: AsyncSession = Depends(get_db)
):
    """
    Simulates closing an active paper position at current market price.
    """
    return PaperOrderResponse(
        order_id=str(uuid.uuid4()),
        status="FILLED",
        side="SELL",
        amount_sol=Decimal("2.850000000"),
        fill_price_sol=Decimal("0.0000000225"),
        tokens_received=Decimal("0"),
        simulated_slippage_bps=80,
        simulated_fee_sol=Decimal("0.0005"),
        filled_at=datetime.now(timezone.utc)
    )
