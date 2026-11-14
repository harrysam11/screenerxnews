from typing import List, Optional, Dict, Any
from fastapi import APIRouter, HTTPException, Query, Body
from app.strategy.runner import strategy_registry, strategy_runner
from app.schemas.contracts import TokenDiscoveryResponse
from app.strategy.base import StrategySignal

router = APIRouter(prefix="/strategies", tags=["Strategy SDK"])

@router.get("", response_model=List[Dict[str, Any]])
async def list_strategies():
    """
    Lists all registered strategies available in the Strategy SDK.
    """
    return strategy_registry.list_strategies()

@router.post("/evaluate", response_model=List[StrategySignal])
async def evaluate_token_observation(
    token: TokenDiscoveryResponse,
    strategy_id: Optional[str] = Query(default=None, description="Optional specific strategy ID")
):
    """
    Evaluates a single token discovery observation across active strategies.
    """
    strategy_ids = [strategy_id] if strategy_id else None
    return strategy_runner.evaluate_discovery(token, strategy_ids=strategy_ids)

@router.post("/backtest/{strategy_id}")
async def backtest_strategy(
    strategy_id: str,
    observations: List[TokenDiscoveryResponse] = Body(...)
):
    """
    Executes a dry-run backtest of a strategy against an array of token observations.
    """
    try:
        return strategy_runner.backtest(observations, strategy_id=strategy_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
