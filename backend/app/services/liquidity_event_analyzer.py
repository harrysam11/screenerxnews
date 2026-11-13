from typing import Dict, Any, Optional
from decimal import Decimal
from datetime import datetime, timezone
from app.schemas.contracts import TriState

class LiquidityEventAnalyzer:
    """
    Analyzes pool creation, LP token minting, burn transactions, and locks.
    """
    DEAD_BURN_ADDRESSES = {
        "11111111111111111111111111111111",
        "deaddeaddeaddeaddeaddeaddeaddeaddeaddeaddead"
    }

    @classmethod
    def analyze_lp_status(
        cls,
        total_lp_minted_raw: Decimal,
        burned_lp_amount_raw: Decimal,
        locked_lp_amount_raw: Decimal,
        lock_expiry: Optional[datetime] = None
    ) -> Dict[str, Any]:
        if total_lp_minted_raw <= Decimal("0"):
            return {
                "lp_status": "UNKNOWN",
                "lp_burned": TriState.UNKNOWN,
                "burn_pct": Decimal("0.00"),
                "locked_pct": Decimal("0.00"),
                "is_removable": True
            }

        burn_pct = (burned_lp_amount_raw / total_lp_minted_raw) * Decimal("100")
        locked_pct = (locked_lp_amount_raw / total_lp_minted_raw) * Decimal("100")

        if burn_pct >= Decimal("95.00"):
            status = "BURNED"
            burned_state = TriState.TRUE
            is_removable = False
        elif locked_pct >= Decimal("95.00"):
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
            "lock_expiry": lock_expiry,
            "is_removable": is_removable
        }

liquidity_event_analyzer = LiquidityEventAnalyzer()
