import uuid
from typing import Dict, Any, Optional
from decimal import Decimal
from datetime import datetime, timezone

class EvmMarketEnricher:
    """
    Enriches EVM pools with reserve observations and price calculation.
    """
    @staticmethod
    def enrich_pool_reserves(
        chain_id: str,
        asset_id: str,
        pool_id: str,
        reserve0: int,
        reserve1: int,
        decimals0: int,
        decimals1: int,
        block_number: int,
        block_hash: str
    ) -> Dict[str, Any]:
        now = datetime.now(timezone.utc)

        # Basic constant product reserve price calculation
        # price = (reserve1 / 10^decimals1) / (reserve0 / 10^decimals0)
        dec_adj0 = Decimal(str(reserve0)) / (Decimal(10) ** decimals0)
        dec_adj1 = Decimal(str(reserve1)) / (Decimal(10) ** decimals1)

        if dec_adj0 > Decimal("0"):
            price_quote_per_base = dec_adj1 / dec_adj0
        else:
            price_quote_per_base = Decimal("0")

        return {
            "chain_id": chain_id,
            "asset_id": asset_id,
            "pool_id": pool_id,
            "request_id": str(uuid.uuid4()),
            "source": "evm_onchain_reserves",
            "block_number": block_number,
            "block_hash": block_hash,
            "observed_at": now,
            "ingested_at": now,
            "price_in_quote": price_quote_per_base,
            "reserve0_raw": reserve0,
            "reserve1_raw": reserve1,
            "data_quality": "COMPLETE" if (reserve0 > 0 and reserve1 > 0) else "DEGRADED",
            "is_canonical": True,
            "is_stale": False
        }

evm_market_enricher = EvmMarketEnricher()
