"""
WF-20 / WF-36: Simulated Execution Engine (Windmill Paper Execution Worker)
Executes virtual paper orders with constant-product AMM impact curves and slippage validation.
"""
import os
import sys
import uuid
from decimal import Decimal
from typing import Dict, Any

sys.path.insert(0, os.path.abspath("./backend"))
from app.services.paper_simulation_engine import paper_simulation_engine
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

    res = paper_simulation_engine.execute_order(
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

def main(chain_id: str = "solana:mainnet") -> Dict[str, Any]:
    return execute_simulated_order(
        chain_id=chain_id,
        asset_id="DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
        pool_id="0xbonkpool",
        side="BUY",
        input_mint="SOL",
        output_mint="BONK",
        input_amount_raw=Decimal("1.5")
    )

if __name__ == "__main__":
    import json
    print(json.dumps(main()))
