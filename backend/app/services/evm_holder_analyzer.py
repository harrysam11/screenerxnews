from typing import Dict, List, Any
from decimal import Decimal

class EvmHolderAnalyzer:
    """
    Analyzes token balance distributions and holder concentration from Transfer events.
    """
    DEAD_ADDRESSES = {
        "0x0000000000000000000000000000000000000000",
        "0x000000000000000000000000000000000000dead"
    }

    @classmethod
    def analyze_holders(
        cls,
        balances: Dict[str, int],
        total_supply: int,
        pool_address: str
    ) -> Dict[str, Any]:
        if total_supply <= 0:
            return {
                "top10_concentration_pct": Decimal("0.00"),
                "circulating_supply_pct": Decimal("0.00"),
                "holder_coverage_pct": Decimal("0.00"),
                "holders_count": 0
            }

        # Filter out dead burn addresses and the liquidity pool itself for holder concentration
        filtered_holders = []
        burn_amount = 0
        pool_amount = 0

        for addr, bal in balances.items():
            clean_addr = addr.lower()
            if clean_addr in cls.DEAD_ADDRESSES:
                burn_amount += bal
            elif clean_addr == pool_address.lower():
                pool_amount += bal
            else:
                filtered_holders.append({"address": clean_addr, "balance": bal})

        filtered_holders.sort(key=lambda x: x["balance"], reverse=True)
        top10_sum = sum(h["balance"] for h in filtered_holders[:10])

        top10_pct = (Decimal(str(top10_sum)) / Decimal(str(total_supply))) * Decimal("100")
        circulating = total_supply - burn_amount
        circ_pct = (Decimal(str(circulating)) / Decimal(str(total_supply))) * Decimal("100")

        return {
            "top10_concentration_pct": round(top10_pct, 2),
            "circulating_supply_pct": round(circ_pct, 2),
            "burned_supply_pct": round((Decimal(str(burn_amount)) / Decimal(str(total_supply))) * Decimal("100"), 2),
            "pool_supply_pct": round((Decimal(str(pool_amount)) / Decimal(str(total_supply))) * Decimal("100"), 2),
            "holders_count": len(filtered_holders),
            "top_holders": filtered_holders[:10]
        }

evm_holder_analyzer = EvmHolderAnalyzer()
