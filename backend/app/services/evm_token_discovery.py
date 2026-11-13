from typing import Dict, Any, Optional

class EvmTokenDiscovery:
    """
    Discovers new ERC-20 token deployments and safely normalizes contract metadata.
    """
    @classmethod
    def normalize_token_metadata(
        cls,
        chain_id: str,
        contract_address: str,
        name: Optional[str],
        symbol: Optional[str],
        decimals: Optional[int],
        total_supply: Optional[int] = None
    ) -> Dict[str, Any]:
        clean_address = contract_address.lower()

        # Handle non-standard or missing metadata gracefully
        safe_decimals = decimals if (decimals is not None and 0 <= decimals <= 36) else None
        safe_symbol = symbol.strip() if symbol else "UNKNOWN"
        safe_name = name.strip() if name else "Unknown Token"

        return {
            "chain_id": chain_id,
            "asset_address": clean_address,
            "symbol": safe_symbol,
            "name": safe_name,
            "decimals": safe_decimals,
            "total_supply_raw": total_supply,
            "is_verified": (safe_decimals is not None and safe_symbol != "UNKNOWN")
        }

evm_token_discovery = EvmTokenDiscovery()
