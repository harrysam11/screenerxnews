from typing import Dict, Any, Optional
from decimal import Decimal
from datetime import datetime, timezone

class EvmSellSimulator:
    """
    Read-only simulation framework to test Buy and Sell paths on EVM DEX routers.
    """
    SIMULATOR_VERSION = "v2_sell_simulator"

    @classmethod
    def evaluate_simulation_results(
        cls,
        chain_id: str,
        token_address: str,
        buy_success: bool,
        buy_input_native: Decimal,
        buy_output_tokens: Decimal,
        expected_buy_output: Decimal,
        sell_success: bool,
        sell_input_tokens: Decimal,
        sell_output_native: Decimal,
        expected_sell_output: Decimal,
        revert_reason: Optional[str] = None
    ) -> Dict[str, Any]:
        now = datetime.now(timezone.utc)

        # Calculate realized taxes
        if buy_success and expected_buy_output > Decimal("0"):
            buy_loss = max(Decimal("0"), expected_buy_output - buy_output_tokens)
            buy_tax_pct = (buy_loss / expected_buy_output) * Decimal("100")
        else:
            buy_tax_pct = None

        if sell_success and expected_sell_output > Decimal("0"):
            sell_loss = max(Decimal("0"), expected_sell_output - sell_output_native)
            sell_tax_pct = (sell_loss / expected_sell_output) * Decimal("100")
        else:
            sell_tax_pct = None

        # Determine honeypot classification
        is_honeypot = False
        rejection_reason = None

        if not sell_success:
            if buy_success:
                is_honeypot = True
                rejection_reason = f"CANNOT_SELL: Sell simulation reverted ({revert_reason or 'Execution Reverted'})"
            else:
                rejection_reason = "UNTRADABLE_OR_NO_LIQUIDITY: Both buy and sell paths failed"
        elif sell_tax_pct is not None and sell_tax_pct >= Decimal("50.00"):
            is_honeypot = True
            rejection_reason = f"HONEYPOT_TAX: Excessive sell tax of {sell_tax_pct:.1f}%"

        return {
            "chain_id": chain_id,
            "token_address": token_address.lower(),
            "buy_success": buy_success,
            "buy_tax_pct": round(buy_tax_pct, 2) if buy_tax_pct is not None else None,
            "sell_success": sell_success,
            "sell_tax_pct": round(sell_tax_pct, 2) if sell_tax_pct is not None else None,
            "is_honeypot": is_honeypot,
            "rejection_reason": rejection_reason,
            "revert_reason": revert_reason,
            "simulated_at": now.isoformat(),
            "simulator_version": cls.SIMULATOR_VERSION
        }

evm_sell_simulator = EvmSellSimulator()
