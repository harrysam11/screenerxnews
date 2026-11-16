"""
P1.5-F Strategy Paper Trading Schemas.
Defines typed DTOs for strategy paper accounts, simulated orders,
virtual positions, and step execution.
"""

from enum import Enum
from typing import List, Optional, Dict, Any
from decimal import Decimal
from pydantic import BaseModel, Field, ConfigDict

from app.schemas.ohlcv import Timeframe


class PaperOrderStatus(str, Enum):
    SUBMITTED = "SUBMITTED"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class PaperOrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class PaperAccountCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_id: str
    name: str
    initial_balance_usd: Decimal = Field(default=Decimal("10000.0"), ge=Decimal("100.0"))


class StrategyPaperOrderDTO(BaseModel):
    order_id: str
    account_id: str
    strategy_id: str
    chain_id: str
    pool_address: str
    side: PaperOrderSide
    quantity: Decimal
    price: Decimal
    fee_usd: Decimal
    slippage_usd: Decimal
    status: PaperOrderStatus
    idempotency_key: str
    created_at: str
    filled_at: Optional[str] = None


class StrategyPaperPositionDTO(BaseModel):
    position_id: str
    account_id: str
    strategy_id: str
    chain_id: str
    pool_address: str
    side: PaperOrderSide
    quantity: Decimal
    entry_price: Decimal
    current_price: Optional[Decimal] = None
    cost_basis_usd: Decimal
    unrealized_pnl_usd: Decimal = Decimal("0")
    realized_pnl_usd: Decimal = Decimal("0")
    status: str
    opened_at: str
    closed_at: Optional[str] = None


class StrategyPaperAccountDTO(BaseModel):
    account_id: str
    name: str
    cash_balance_usd: Decimal
    initial_balance_usd: Decimal
    realized_pnl_usd: Decimal
    unrealized_pnl_usd: Decimal = Decimal("0")
    total_equity_usd: Decimal
    open_positions: List[StrategyPaperPositionDTO] = Field(default_factory=list)
    recent_orders: List[StrategyPaperOrderDTO] = Field(default_factory=list)
    status: str
    created_at: str
    updated_at: str


class StrategyPaperStepRequest(BaseModel):
    """Evaluates strategy and triggers simulated paper execution if conditions match."""
    model_config = ConfigDict(extra="forbid")
    account_id: str
    strategy_id: str
    chain_id: str
    pool_address: str
    timeframe: Timeframe = Timeframe.M15
    position_size_usd: Decimal = Field(default=Decimal("1000.0"), ge=Decimal("10.0"))
    fee_rate_bps: int = Field(default=30, ge=0, le=500)
    slippage_bps: int = Field(default=20, ge=0, le=500)


class StrategyPaperStepResponse(BaseModel):
    account_id: str
    strategy_id: str
    evaluation_matched_entry: bool
    evaluation_matched_exit: bool
    order_executed: bool
    order: Optional[StrategyPaperOrderDTO] = None
    account_summary: StrategyPaperAccountDTO
    explanation: str
