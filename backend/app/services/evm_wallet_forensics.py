from typing import Dict, Any, List, Optional
from decimal import Decimal
from app.schemas.contracts import EvidenceStatus

class EvmWalletForensics:
    """
    Analyzes early buyers, block proximity, and funding trees for EVM tokens.
    """
    DETECTOR_VERSION = "v2_evm_wallet_forensics"

    @classmethod
    def analyze_early_buyers(
        cls,
        chain_id: str,
        pool_creation_block: int,
        deployer_address: str,
        buyer_swaps: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        buyer_swaps list: [{wallet, block_number, amount_tokens, native_spent, funder}]
        """
        if not buyer_swaps:
            return {
                "chain_id": chain_id,
                "confidence": EvidenceStatus.INSUFFICIENT,
                "bundler_cluster_detected": False,
                "buyers": []
            }

        same_block_count = 0
        shared_funder_count = 0
        funder_map: Dict[str, int] = {}

        for b in buyer_swaps:
            funder = b.get("funder")
            if funder:
                funder_map[funder] = funder_map.get(funder, 0) + 1
            if b.get("block_number") == pool_creation_block:
                same_block_count += 1

        shared_funder_count = sum(1 for b in buyer_swaps if funder_map.get(b.get("funder", ""), 0) > 1)
        bundler_detected = (same_block_count >= 3) or (shared_funder_count >= 3)

        return {
            "chain_id": chain_id,
            "confidence": EvidenceStatus.PROBABILISTIC if len(buyer_swaps) >= 5 else EvidenceStatus.INSUFFICIENT,
            "bundler_cluster_detected": bundler_detected,
            "same_block_count": same_block_count,
            "shared_funder_count": shared_funder_count,
            "buyers_count": len(buyer_swaps),
            "detector_version": cls.DETECTOR_VERSION
        }

evm_wallet_forensics = EvmWalletForensics()
