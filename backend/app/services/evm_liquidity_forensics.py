from typing import Dict, Any, Optional
from decimal import Decimal
from app.schemas.contracts import TriState

class EvmLiquidityForensics:
    """
    Analyzes EVM LP tokens, burn addresses (0x0 / 0xdead), and locker contracts (Uncx / PinkSale).
    """
    BURN_ADDRESSES = {
        "0x0000000000000000000000000000000000000000",
        "0x000000000000000000000000000000000000dead"
    }

    KNOWN_LOCKERS = {
        "0x663a5c229c09b049e36dcc11a9b0d4a8eb9db214": "Uncx Network Locker",
        "0x71b5759d73262fbbe24395680ab039e226066259": "PinkSale Locker",
        "0xe2fe530c047f2d85298b07d9333c05737f1435fb": "Team Finance Locker"
    }

    @classmethod
    def evaluate_lp_safety(
        cls,
        total_lp_supply: int,
        lp_balances: Dict[str, int]
    ) -> Dict[str, Any]:
        if total_lp_supply <= 0:
            return {
                "lp_status": "UNKNOWN",
                "lp_burned": TriState.UNKNOWN,
                "burn_pct": Decimal("0.00"),
                "locked_pct": Decimal("0.00"),
                "is_removable": True
            }

        burned_amount = sum(lp_balances.get(addr, 0) for addr in cls.BURN_ADDRESSES)
        locked_amount = sum(lp_balances.get(addr, 0) for addr in cls.KNOWN_LOCKERS)

        burn_pct = (Decimal(str(burned_amount)) / Decimal(str(total_lp_supply))) * Decimal("100")
        locked_pct = (Decimal(str(locked_amount)) / Decimal(str(total_lp_supply))) * Decimal("100")

        if burn_pct >= Decimal("95.00"):
            status = "BURNED"
            burned_state = TriState.TRUE
            is_removable = False
        elif (burn_pct + locked_pct) >= Decimal("95.00"):
            status = "LOCKED"
            burned_state = TriState.FALSE
            is_removable = False
        else:
            status = "UNLOCKED"
            burned_state = TriState.FALSE
            is_removable = True

        return {
            "lp_status": status,
            "lp_burned": burned_state,
            "burn_pct": round(burn_pct, 2),
            "locked_pct": round(locked_pct, 2),
            "is_removable": is_removable
        }

evm_liquidity_forensics = EvmLiquidityForensics()
