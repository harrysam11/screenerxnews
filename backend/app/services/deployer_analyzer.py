from typing import Dict, Any, List, Optional
from decimal import Decimal
from datetime import datetime, timezone

class DeployerAnalyzer:
    """
    Analyzes token deployer/creator address, previous launches, and dump velocity.
    """
    DETECTOR_VERSION = "v2_deployer_analyzer"

    @classmethod
    def analyze_deployer(
        cls,
        deployer_address: str,
        initial_supply_raw: Decimal,
        current_balance_raw: Decimal,
        funder_address: Optional[str] = None,
        previous_launches_count: int = 0
    ) -> Dict[str, Any]:
        if initial_supply_raw <= Decimal("0"):
            holding_pct = Decimal("0.00")
        else:
            holding_pct = (current_balance_raw / initial_supply_raw) * Decimal("100")

        # Classify deployer behavior
        if holding_pct > Decimal("20.00"):
            risk_level = "HIGH"
            category = "MASSIVE_SUPPLY_RETENTION"
        elif previous_launches_count >= 5:
            risk_level = "HIGH"
            category = "SERIAL_DEPLOYER"
        elif funder_address is None:
            risk_level = "MODERATE"
            category = "FRESH_OR_UNFUNDED"
        else:
            risk_level = "LOW"
            category = "STANDARD_CREATOR"

        return {
            "deployer_address": deployer_address,
            "funder_address": funder_address,
            "current_holding_pct": round(holding_pct, 2),
            "previous_launches_count": previous_launches_count,
            "risk_level": risk_level,
            "category": category,
            "detector_version": cls.DETECTOR_VERSION
        }

deployer_analyzer = DeployerAnalyzer()
