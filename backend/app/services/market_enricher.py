import uuid
from typing import Dict, Any, Optional
from decimal import Decimal
from datetime import datetime, timezone

class MarketDataEnricher:
    """
    Enriches candidate pool data into a validated Market Snapshot with full provenance.
    """
    @staticmethod
    def create_snapshot(
        asset_id: str,
        pool_id: str,
        pair_data: Dict[str, Any],
        request_id: Optional[str] = None
    ) -> Dict[str, Any]:
        now = datetime.now(timezone.utc)
        req_id = request_id or str(uuid.uuid4())

        price_usd_str = pair_data.get("priceUsd", "0")
        liquidity_usd_val = pair_data.get("liquidity", {}).get("usd", 0)
        volume_data = pair_data.get("volume", {})
        txns_data = pair_data.get("txns", {})

        # Compute data quality
        has_price = Decimal(str(price_usd_str)) > 0
        has_liq = float(liquidity_usd_val) > 0
        quality = "COMPLETE" if (has_price and has_liq) else "PARTIAL"

        return {
            "asset_id": asset_id,
            "pool_id": pool_id,
            "request_id": req_id,
            "source": "dexscreener",
            "source_record_id": pair_data.get("pairAddress"),
            "observed_at": now,
            "ingested_at": now,
            "slot": None,
            "price_usd": Decimal(str(price_usd_str)),
            "price_sol": None,
            "bid_price_sol": None,
            "ask_price_sol": None,
            "market_cap_usd": Decimal(str(pair_data.get("marketCap", 0))),
            "fdv_usd": Decimal(str(pair_data.get("fdv", 0))),
            "liquidity_usd": Decimal(str(liquidity_usd_val)),
            "volume_5m": Decimal(str(volume_data.get("m5", 0))),
            "volume_1h": Decimal(str(volume_data.get("h1", 0))),
            "volume_24h": Decimal(str(volume_data.get("h24", 0))),
            "buys_1h": txns_data.get("h1", {}).get("buys", 0),
            "sells_1h": txns_data.get("h1", {}).get("sells", 0),
            "data_quality": quality,
            "is_stale": False
        }

market_enricher = MarketDataEnricher()
