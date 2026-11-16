import os
import tempfile
from typing import Dict, List, Optional, Any
from decimal import Decimal
from datetime import datetime, timezone

from app.core.config import settings
from app.schemas.contracts import (
    PaperAccountResponse,
    PaperOrderRequest,
    PaperOrderResponse,
    PaperPositionResponse,
    PaperExecutionEventDTO
)
from app.services.persistent_paper_service import PersistentPaperService, persistent_paper_service

class PaperSimulationEngine:
    """
    Multi-chain paper trading engine backed by persistent SQLite storage.
    Provides isolated virtual desks, AMM impact curves, slippage validation,
    double-entry financial ledger integration, and crash recovery.
    """
    ENGINE_VERSION = "v3_persistent_multichain_paper_engine"
    DEFAULT_INITIAL_BALANCES = PersistentPaperService.DEFAULT_INITIAL_BALANCES

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            # Ephemeral isolated SQLite database for unanchored test instances
            self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
            self.db_path = self._tmp.name
        else:
            self.db_path = db_path
            self._tmp = None

        self._service = PersistentPaperService(self.db_path)

    def get_account(self, chain_id: str = "solana:mainnet") -> PaperAccountResponse:
        return self._service.get_account(chain_id)

    def calculate_price_impact_bps(self, trade_amount_usd: Decimal, pool_liquidity_usd: Decimal) -> int:
        return self._service.calculate_price_impact_bps(trade_amount_usd, pool_liquidity_usd)

    def execute_order(
        self,
        request: PaperOrderRequest,
        current_market_price_native: Optional[Decimal] = None,
        pool_liquidity_usd: Decimal = Decimal("100000.0"),
        native_price_usd: Optional[Decimal] = Decimal("200.0"),
        order_type: str = "MARKET",
        limit_price_native: Optional[Decimal] = None
    ) -> PaperOrderResponse:
        return self._service.execute_order(
            request=request,
            current_market_price_native=current_market_price_native,
            pool_liquidity_usd=pool_liquidity_usd,
            native_price_usd=native_price_usd,
            order_type=order_type,
            limit_price_native=limit_price_native
        )

    def get_positions(self, chain_id: str = "solana:mainnet") -> List[PaperPositionResponse]:
        return self._service.get_positions(chain_id)

    def __del__(self):
        if getattr(self, "_tmp", None) and os.path.exists(self.db_path):
            try:
                os.remove(self.db_path)
            except Exception:
                pass

_paper_simulation_engine_instance: Optional[PaperSimulationEngine] = None

def get_paper_simulation_engine() -> PaperSimulationEngine:
    global _paper_simulation_engine_instance
    canonical_path = settings.get_canonical_sqlite_path()
    if _paper_simulation_engine_instance is None or _paper_simulation_engine_instance.db_path != canonical_path:
        _paper_simulation_engine_instance = PaperSimulationEngine(db_path=canonical_path)
    return _paper_simulation_engine_instance

class _LazyPaperSimulationEngineProxy:
    @property
    def is_initialized(self) -> bool:
        return _paper_simulation_engine_instance is not None

    def __getattr__(self, name: str) -> Any:
        return getattr(get_paper_simulation_engine(), name)

paper_simulation_engine: PaperSimulationEngine = _LazyPaperSimulationEngineProxy()  # type: ignore

