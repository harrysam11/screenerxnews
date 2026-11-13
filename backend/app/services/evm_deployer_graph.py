from typing import Dict, Any, List, Optional
from decimal import Decimal

class EvmDeployerGraph:
    """
    Traces EVM deployer funding lineage and multi-launch behavior.
    """
    KNOWN_MIXERS = {
        "0xd90e2f925da726b50c4ed8d0fb90ad053324f31b", # Tornado 0.1 ETH
        "0x47ce0c6ed5b0ce3d3a51fdb1c52dc66a7c3c2936", # Tornado 1 ETH
        "0x910cbd523d972eb0a6f4cae4618ad62622b39dbf", # Tornado 10 ETH
        "0xa160cd94361a382170b2c4285fee14227239b415"  # Tornado 100 ETH
    }

    @classmethod
    def evaluate_deployer(
        cls,
        chain_id: str,
        deployer_address: str,
        initial_funder_address: Optional[str],
        prior_deployments: List[str]
    ) -> Dict[str, Any]:
        clean_dev = deployer_address.lower()
        clean_funder = initial_funder_address.lower() if initial_funder_address else None

        is_mixer_funded = clean_funder in cls.KNOWN_MIXERS
        serial_deployer = len(prior_deployments) >= 3

        if is_mixer_funded:
            risk_level = "CRITICAL"
            funder_label = "TORNADO_CASH_MIXER"
        elif serial_deployer:
            risk_level = "HIGH"
            funder_label = "SERIAL_LAUNCHER"
        elif clean_funder:
            risk_level = "LOW"
            funder_label = "STANDARD_FUNDER"
        else:
            risk_level = "MODERATE"
            funder_label = "UNKNOWN_ORIGIN"

        return {
            "chain_id": chain_id,
            "deployer_address": clean_dev,
            "initial_funder_address": clean_funder,
            "funder_label": funder_label,
            "is_mixer_funded": is_mixer_funded,
            "prior_deployments_count": len(prior_deployments),
            "risk_level": risk_level
        }

evm_deployer_graph = EvmDeployerGraph()
