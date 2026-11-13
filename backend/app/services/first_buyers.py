from typing import Dict, Any, List, Optional
from decimal import Decimal
from app.services.first_buyers_decoder import first_buyers_decoder

class FirstBuyersService:
    @staticmethod
    def get_first_buyers(
        pool_address: str,
        deployer_address: Optional[str],
        creation_slot: int,
        raw_transactions: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        return first_buyers_decoder.analyze_buyers(
            pool_address=pool_address,
            deployer_address=deployer_address,
            creation_slot=creation_slot,
            raw_transactions=raw_transactions
        )

first_buyers_service = FirstBuyersService()
