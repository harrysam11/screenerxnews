"""
summary: Simulated Paper Execution Engine (WF-20 / WF-36)
description: Executes virtual paper orders with exact constant-product AMM impact curves, risk validation, and double-entry ledger integration.
schema:
  type: object
  properties:
    chain_id:
      type: string
      default: solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp
      description: Target CAIP-2 chain identifier
    asset_id:
      type: string
      default: DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263
      description: Asset contract or mint address to trade
    pool_id:
      type: string
      default: 0xbonkpool
      description: Target liquidity pool identifier
    side:
      type: string
      default: BUY
      enum: [BUY, SELL]
      description: Order execution side
    input_amount_raw:
      type: number
      default: 1.0
      description: Quantity of input token/native to spend
    max_slippage_bps:
      type: integer
      default: 150
      description: Maximum allowable slippage in basis points
"""
import os
import sys
import uuid
from decimal import Decimal
from typing import Dict, Any

sys.path.insert(0, os.path.abspath("./backend"))
try:
    from app.services.persistent_paper_service import persistent_paper_service as paper_engine
except ImportError:
    from app.services.paper_simulation_engine import paper_simulation_engine as paper_engine

from app.schemas.contracts import PaperOrderRequest

def execute_simulated_order(
    chain_id: str,
    asset_id: str,
    pool_id: str,
    side: str,
    input_mint: str,
    output_mint: str,
    input_amount_raw: Decimal,
    max_slippage_bps: int = 150,
    current_market_price_native: Decimal = Decimal("0.00004500")
) -> Dict[str, Any]:
    req = PaperOrderRequest(
        account_id=str(uuid.uuid4()),
        client_order_id=f"wf20-{uuid.uuid4()}",
        asset_id=asset_id,
        pool_id=pool_id,
        side=side,
        input_mint=input_mint,
        output_mint=output_mint,
        input_amount_raw=input_amount_raw,
        max_slippage_bps=max_slippage_bps,
        chain_id=chain_id
    )

    res = paper_engine.execute_order(
        request=req,
        current_market_price_native=current_market_price_native,
        pool_liquidity_usd=Decimal("150000.0")
    )

    return {
        "status": res.status,
        "order_id": res.order_id,
        "side": res.side,
        "chain_id": res.chain_id,
        "output_amount_raw": float(res.output_amount_raw) if res.output_amount_raw else None,
        "effective_price_native": float(res.effective_price_native) if res.effective_price_native else None,
        "rejection_reason": res.rejection_reason
    }

def main(
    chain_id: str = "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp",
    asset_id: str = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
    pool_id: str = "0xbonkpool",
    side: str = "BUY",
    input_amount: float = 1.0,
    max_slippage_bps: int = 150
) -> Dict[str, Any]:
    return execute_simulated_order(
        chain_id=chain_id,
        asset_id=asset_id,
        pool_id=pool_id,
        side=side,
        input_mint="native" if side == "BUY" else asset_id,
        output_mint=asset_id if side == "BUY" else "native",
        input_amount_raw=Decimal(str(input_amount)),
        max_slippage_bps=max_slippage_bps,
        current_market_price_native=Decimal("0.00004500")
    )

if __name__ == "__main__":
    import json
    print(json.dumps(main(), default=str))
