from typing import List, Optional
from decimal import Decimal
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.schemas.contracts import (
    PaperAccountResponse,
    PaperOrderRequest,
    PaperOrderResponse,
    PaperPositionResponse
)
from app.services.paper_simulation_engine import paper_simulation_engine

router = APIRouter(prefix="/paper", tags=["Paper Trading Desk"])

@router.get("/account", response_model=PaperAccountResponse)
async def get_paper_account(
    chain_id: str = Query("solana:mainnet", description="CAIP-2 Chain Identifier"),
    db: AsyncSession = Depends(get_db)
):
    """
    Returns virtual balance, equity, and performance stats for the isolated paper desk.
    """
    return paper_simulation_engine.get_account(chain_id)

@router.post("/order", response_model=PaperOrderResponse)
async def submit_paper_order(
    payload: PaperOrderRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Simulates symmetrical order execution with realistic bid/ask spread, price impact curve, and fees.
    """
    # Baseline market price reference for simulation
    mock_market_price = Decimal("0.00004500") if payload.chain_id == "solana:mainnet" else Decimal("0.00000250")
    return paper_simulation_engine.execute_order(
        request=payload,
        current_market_price_native=mock_market_price,
        pool_liquidity_usd=Decimal("150000.0")
    )

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
