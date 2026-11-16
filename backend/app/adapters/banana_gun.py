"""
Banana Gun Read-Only External Reference Adapter (P10.2-I).

Strict Boundaries:
- External intelligence and reference link-out source only.
- NOT an execution authority.
- No wallet connection, no private keys, no seed storage, no signing, no transaction broadcast.
- No direct order creation or ledger modification.
- Explains unsupported automated API status cleanly without reverse-engineering private endpoints.
- Respects offline mode and sovereign provider controls.
"""

from typing import Dict, Any, Optional, List
from app.core.config import settings
from app.core.chains import SOLANA_CAIP2_CANONICAL, normalize_chain_id
from app.core.network import assert_online_mode

class BananaGunReferenceAdapter:
    """
    Read-only reference adapter for Banana Gun Bot intelligence / link-outs.
    Does NOT support execution, trading, or key management.
    """
    ADAPTER_NAME = "banana_gun"
    DISPLAY_NAME = "Banana Gun (Reference Adapter)"
    API_AVAILABLE = False
    SUPPORTS_EXECUTION = False
    SUPPORTS_SIGNING = False
    DATA_CLASSIFICATION = "EXTERNAL_REFERENCE"

    SUPPORTED_CHAINS = [
        "eip155:1",     # Ethereum
        "eip155:8453",  # Base
        SOLANA_CAIP2_CANONICAL
    ]

    UNSUPPORTED_REASON = (
        "Banana Gun operates as a closed custodial/bot trading system without an open, unauthenticated "
        "REST API for external intelligence aggregation. Core maintains complete custody isolation and "
        "does not ingest bot credentials, private keys, or external bot execution signals as trading authority."
    )

    def __init__(self):
        pass

    def get_adapter_status(self) -> Dict[str, Any]:
        """Returns standard capability and boundary status for Banana Gun reference adapter."""
        return {
            "adapter_name": self.ADAPTER_NAME,
            "display_name": self.DISPLAY_NAME,
            "api_available": self.API_AVAILABLE,
            "supports_execution": self.SUPPORTS_EXECUTION,
            "supports_signing": self.SUPPORTS_SIGNING,
            "data_classification": self.DATA_CLASSIFICATION,
            "supported_chains": list(self.SUPPORTED_CHAINS),
            "unsupported_reason": self.UNSUPPORTED_REASON,
            "offline_mode": settings.is_offline or settings.OPERATING_MODE == "offline_research"
        }

    def get_reference_link(self, chain_id: str, asset_address: str) -> Dict[str, Any]:
        """
        Builds a safe, external research link-out for the given asset.
        Does not perform network requests and operates cleanly in offline mode.
        """
        norm_chain = normalize_chain_id(chain_id)
        clean_addr = asset_address.strip()

        if norm_chain == "eip155:1":
            url = f"https://t.me/BananaGunSniper_bot?start=snipe_{clean_addr}"
        elif norm_chain == "eip155:8453":
            url = f"https://t.me/BananaGunBase_bot?start=snipe_{clean_addr}"
        elif norm_chain == SOLANA_CAIP2_CANONICAL:
            url = f"https://t.me/BananaGunSolana_bot?start=snipe_{clean_addr}"
        else:
            url = f"https://bananagun.io/research?chain={norm_chain}&address={clean_addr}"

        return {
            "source": self.ADAPTER_NAME,
            "classification": self.DATA_CLASSIFICATION,
            "chain_id": norm_chain,
            "asset_address": clean_addr,
            "reference_url": url,
            "attribution": "Banana Gun Sniper Reference",
            "is_execution_authorized": False,
            "note": "Reference link only. Does not grant execution authorization or bypass Core controls."
        }

    async def fetch_token_reference_metadata(self, chain_id: str, asset_address: str) -> Dict[str, Any]:
        """
        Returns reference status for the asset.
        Explicitly returns unsupported API status with clear explanation.
        """
        return {
            "chain_id": normalize_chain_id(chain_id),
            "asset_address": asset_address.strip(),
            "source": self.ADAPTER_NAME,
            "classification": self.DATA_CLASSIFICATION,
            "api_available": False,
            "data": None,
            "unavailable_reason": self.UNSUPPORTED_REASON,
            "reference_link": self.get_reference_link(chain_id, asset_address)
        }

    # =========================================================================
    # Strict Boundary Assertions (Prohibited Operations)
    # =========================================================================

    def create_order(self, *args, **kwargs):
        raise PermissionError(
            "Forbidden: BananaGunReferenceAdapter does not support order creation. "
            "Execution authority belongs exclusively to Core."
        )

    def sign_transaction(self, *args, **kwargs):
        raise PermissionError(
            "Forbidden: BananaGunReferenceAdapter does not support key access or transaction signing."
        )

    def submit_transaction(self, *args, **kwargs):
        raise PermissionError(
            "Forbidden: BananaGunReferenceAdapter does not support transaction broadcast."
        )

banana_gun_adapter = BananaGunReferenceAdapter()
