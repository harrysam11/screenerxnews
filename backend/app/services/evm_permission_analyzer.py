from typing import Dict, Any, List, Optional

class EvmPermissionAnalyzer:
    """
    Analyzes EVM contract bytecode and function selectors for administrative privileges.
    """
    KNOWN_SELECTORS = {
        "8da5cb5b": "owner()",
        "715018a6": "renounceOwnership()",
        "f2fde38b": "transferOwnership(address)",
        "8456cb59": "pause()",
        "3f4ba83a": "unpause()",
        "40c10f19": "mint(address,uint256)",
        "095ea7b3": "approve(address,uint256)",
        "a9059cbb": "transfer(address,uint256)"
    }

    SUSPICIOUS_SIGNATURES = {
        "blacklist": ["blacklist", "isBlacklisted", "bot", "isBot", "addBot"],
        "tax": ["setTax", "setFees", "setFee", "setBuyTax", "setSellTax"],
        "max_tx": ["setMaxTx", "setMaxWallet", "setTxLimit"]
    }

    @classmethod
    def analyze_bytecode(cls, bytecode_hex: str) -> Dict[str, Any]:
        clean_code = bytecode_hex.lower()
        if clean_code.startswith("0x"):
            clean_code = clean_code[2:]

        selectors_found = []
        for sel, name in cls.KNOWN_SELECTORS.items():
            if sel in clean_code:
                selectors_found.append(name)

        has_owner = "owner()" in selectors_found
        can_renounce = "renounceOwnership()" in selectors_found
        can_pause = "pause()" in selectors_found
        can_mint = "mint(address,uint256)" in selectors_found

        return {
            "has_owner": has_owner,
            "can_renounce_ownership": can_renounce,
            "can_pause_transfers": can_pause,
            "can_mint_additional_supply": can_mint,
            "selectors_found": selectors_found
        }

    @classmethod
    def classify_ownership(cls, owner_address: Optional[str]) -> str:
        if not owner_address or owner_address.lower() in (
            "0x0000000000000000000000000000000000000000",
            "0x000000000000000000000000000000000000dead"
        ):
            return "RENOUNCED"
        return "ACTIVE_OWNER"

evm_permission_analyzer = EvmPermissionAnalyzer()
