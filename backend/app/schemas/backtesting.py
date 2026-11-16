"""
P1.5-E Backtesting Engine Schemas.
Defines typed DTOs for deterministic backtest configuration, trades, equity curves,
drawdown tracking, and portfolio performance analytics.
"""

from enum import Enum
from typing import List, Optional, Dict, Any
from decimal import Decimal
from pydantic import BaseModel, Field, ConfigDict

from app.schemas.ohlcv import Timeframe


class TradeExitReason(str, Enum):
    SIGNAL = "SIGNAL"
    STOP_LOSS = "STOP_LOSS"
    TAKE_PROFIT = "TAKE_PROFIT"
    END_OF_DATA = "END_OF_DATA"


class TradeSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class BacktestConfigDTO(BaseModel):
    """Configuration parameters for historical backtesting simulation."""
    model_config = ConfigDict(extra="forbid")

    chain_id: str
    pool_address: str
    timeframe: Timeframe = Timeframe.M15
    initial_balance_usd: Decimal = Field(default=Decimal("10000.0"), ge=Decimal("100.0"))
    position_size_usd: Decimal = Field(default=Decimal("1000.0"), ge=Decimal("10.0"))
    fee_rate_bps: int = Field(default=30, ge=0, le=1000, description="DEX swap fee in basis points (30 = 0.30%)")
    slippage_bps: int = Field(default=20, ge=0, le=1000, description="Execution slippage in basis points (20 = 0.20%)")
    stop_loss_pct: Optional[Decimal] = Field(None, ge=Decimal("0.1"), le=Decimal("100.0"))
    take_profit_pct: Optional[Decimal] = Field(None, ge=Decimal("0.1"), le=Decimal("1000.0"))
    allow_short: bool = Field(default=False, description="Spot DEX trading is long-only by default")
    limit_bars: int = Field(default=200, ge=10, le=2000)


class BacktestTradeDTO(BaseModel):
    """Detailed record of an executed simulated trade in the journal."""
    trade_id: str
    side: TradeSide
    entry_bar_index: int
    entry_timestamp_utc: str
    entry_price: Decimal
    exit_bar_index: int
    exit_timestamp_utc: str
    exit_price: Decimal
    exit_reason: TradeExitReason
    quantity: Decimal
    size_usd: Decimal
    fees_paid_usd: Decimal
    slippage_paid_usd: Decimal
    gross_pnl_usd: Decimal
    net_pnl_usd: Decimal
    return_pct: Decimal
    holding_period_bars: int


class EquityPointDTO(BaseModel):
    """Snapshot of portfolio equity and drawdown at a specific candle."""
    bar_index: int
    timestamp_utc: str
    cash_balance_usd: Decimal
    unrealized_pnl_usd: Decimal
    total_equity_usd: Decimal
    drawdown_usd: Decimal
    drawdown_pct: Decimal


class BacktestResultDTO(BaseModel):
    """
    Comprehensive backtesting simulation report.
    Presents deterministic performance attribution, trade logs, and equity curves.
    Does NOT present results as guaranteed future returns.
    """
    backtest_id: str
    strategy_id: str
    strategy_version: str
    chain_id: str
    pool_address: str
    timeframe: Timeframe
    candles_evaluated: int
    initial_balance_usd: Decimal
    ending_balance_usd: Decimal
    net_pnl_usd: Decimal
    total_return_pct: Decimal
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate_pct: Decimal
    profit_factor: Optional[Decimal] = None
    gross_profit_usd: Decimal
    gross_loss_usd: Decimal
    max_drawdown_usd: Decimal
    max_drawdown_pct: Decimal
    expectancy_usd: Decimal
    average_trade_pnl_usd: Decimal
    average_win_usd: Decimal
    average_loss_usd: Decimal
    total_fees_paid_usd: Decimal
    total_slippage_paid_usd: Decimal
    skipped_trades_count: int
    data_quality_notes: List[str] = Field(default_factory=list)
    trades: List[BacktestTradeDTO] = Field(default_factory=list)
    equity_curve: List[EquityPointDTO] = Field(default_factory=list)
    run_at_utc: str


class BacktestRunRequest(BaseModel):
    """Request payload to execute a backtest."""
    model_config = ConfigDict(extra="forbid")

    strategy_id: Optional[str] = None
    strategy_definition: Optional[Dict[str, Any]] = None
    config: BacktestConfigDTO
