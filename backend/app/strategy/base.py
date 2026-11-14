import uuid
from abc import ABC, abstractmethod
from enum import Enum
from typing import List, Optional, Dict, Any
from decimal import Decimal
from datetime import datetime, timezone
from pydantic import BaseModel, Field
from app.schemas.contracts import (
    TokenDiscoveryResponse, TokenSecurityReport, TriState
)

class SignalAction(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"

class StrategySignal(BaseModel):
    """
    Standard signal emitted by a Strategy SDK algorithm (Phase P10.2-G).
    """
    signal_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    strategy_id: str
    strategy_version: str
    chain_id: str
    asset_id: str
    action: SignalAction
    confidence_score: Decimal = Field(..., ge=0, le=100, description="0.0 to 100.0 confidence")
    allocated_amount_native: Decimal = Field(..., gt=0, description="Amount of native currency to trade")
    max_slippage_bps: int = Field(default=200, ge=10, le=2000)
    rationale: str
    metadata: Dict[str, Any] = Field(default_factory=dict)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class BaseStrategy(ABC):
    """
    Abstract base class for user-owned trading strategies.
    Runs self-hosted without requiring n8n or Windmill orchestrators.
    """
    strategy_id: str
    strategy_version: str = "1.0.0"
    description: str = ""
    supported_chains: List[str] = []

    def __init__(
        self,
        strategy_id: str,
        strategy_version: str = "1.0.0",
        description: str = "",
        supported_chains: Optional[List[str]] = None
    ):
        self.strategy_id = strategy_id
        self.strategy_version = strategy_version
        self.description = description
        self.supported_chains = supported_chains or [
            "solana:mainnet", "eip155:1", "eip155:8453", "eip155:56",
            "eip155:42161", "eip155:10", "eip155:137", "eip155:43114",
            "sui:mainnet", "aptos:mainnet", "ton:mainnet", "tron:mainnet"
        ]

    def supports_chain(self, chain_id: str) -> bool:
        return chain_id in self.supported_chains

    @abstractmethod
    def on_discovery(self, token: TokenDiscoveryResponse) -> Optional[StrategySignal]:
        """
        Invoked when a new token observation is ingested.
        """
        pass

    @abstractmethod
    def on_forensics(self, report: TokenSecurityReport) -> Optional[StrategySignal]:
        """
        Invoked when a forensic security scan is completed.
        """
        pass

    def on_ticker(
        self,
        chain_id: str,
        asset_id: str,
        current_price_native: Decimal,
        pool_liquidity_usd: Decimal
    ) -> Optional[StrategySignal]:
        """
        Optional hook invoked on price or liquidity movement.
        """
        return None
