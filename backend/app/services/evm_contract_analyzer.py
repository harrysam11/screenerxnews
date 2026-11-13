from typing import Dict, Any, Optional
from decimal import Decimal
from app.services.evm_proxy_analyzer import evm_proxy_analyzer
from app.services.evm_permission_analyzer import evm_permission_analyzer

class EvmContractAnalyzer:
    """
    Consolidated EVM token security analyzer.
    """
    DETECTOR_VERSION = "v2_evm_contract_analyzer"

    @classmethod
    def evaluate_contract_security(
        cls,
        chain_id: str,
        contract_address: str,
        bytecode_hex: str,
        storage_slots: Dict[str, str],
        owner_address: Optional[str] = None,
        buy_tax_bps: Optional[int] = None,
        sell_tax_bps: Optional[int] = None
    ) -> Dict[str, Any]:
        proxy_info = evm_proxy_analyzer.evaluate_proxy_storage(storage_slots)
        permission_info = evm_permission_analyzer.analyze_bytecode(bytecode_hex)
        ownership_state = evm_permission_analyzer.classify_ownership(owner_address)

        findings = []
        is_high_risk = False

        if proxy_info["is_proxy"]:
            findings.append({
                "code": "UPGRADEABLE_PROXY_DETECTED",
                "severity": "WARNING",
                "description": f"Contract is an upgradeable proxy ({proxy_info['proxy_standard']}) pointing to {proxy_info['implementation_address']}"
            })

        if ownership_state == "ACTIVE_OWNER":
            findings.append({
                "code": "OWNERSHIP_NOT_RENOUNCED",
                "severity": "WARNING",
                "description": f"Active contract owner: {owner_address}"
            })

        if permission_info["can_mint_additional_supply"]:
            is_high_risk = True
            findings.append({
                "code": "MINTABLE_SUPPLY",
                "severity": "CRITICAL_FAIL",
                "description": "Bytecode exposes arbitrary mint function"
            })

        if permission_info["can_pause_transfers"]:
            findings.append({
                "code": "PAUSABLE_TRANSFERS",
                "severity": "WARNING",
                "description": "Owner has permission to pause token trading"
            })

        if sell_tax_bps is not None and sell_tax_bps > 1000: # > 10%
            is_high_risk = True
            findings.append({
                "code": "EXCESSIVE_SELL_TAX",
                "severity": "CRITICAL_FAIL",
                "description": f"Excessive sell tax detected: {sell_tax_bps / 100}%"
            })

        return {
            "chain_id": chain_id,
            "contract_address": contract_address.lower(),
            "ownership_state": ownership_state,
            "owner_address": owner_address,
            "proxy": proxy_info,
            "permissions": permission_info,
            "buy_tax_bps": buy_tax_bps,
            "sell_tax_bps": sell_tax_bps,
            "is_high_risk": is_high_risk,
            "findings": findings,
            "detector_version": cls.DETECTOR_VERSION
        }

evm_contract_analyzer = EvmContractAnalyzer()
