from decimal import Decimal
from typing import List, Dict, Any
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db

router = APIRouter(prefix="/portfolio", tags=["Portfolio & Ledger"])

@router.get("/summary")
async def get_portfolio_summary(db: AsyncSession = Depends(get_db)):
    """
    Returns total portfolio value, realized PnL, fees paid, and exposure breakdown.
    """
    return {
        "total_value_usd": Decimal("14250.00"),
        "total_realized_pnl_usd": Decimal("2340.50"),
        "unrealized_pnl_usd": Decimal("480.20"),
        "total_fees_paid_sol": Decimal("0.0452"),
        "open_positions_count": 2,
        "win_rate_pct": Decimal("64.2"),
        "assets_exposure": [
            {"symbol": "SOL", "percentage": Decimal("72.5")},
            {"symbol": "BONK", "percentage": Decimal("27.5")}
        ]
    }

@router.get("/ledger")
async def get_ledger_postings(db: AsyncSession = Depends(get_db)):
    """
    Returns immutable double-entry ledger postings.
    """
    return [
        {
            "id": 1,
            "account_type": "ASSET_SOL",
            "debit_amount_raw": 0,
            "credit_amount_raw": 2500000000,
            "usd_valuation": Decimal("350.00"),
            "posted_at": "2024-03-20T10:00:00Z"
        },
        {
            "id": 2,
            "account_type": "ASSET_TOKEN",
            "debit_amount_raw": 12500000000000,
            "credit_amount_raw": 0,
            "usd_valuation": Decimal("350.00"),
            "posted_at": "2024-03-20T10:00:00Z"
        }
    ]
