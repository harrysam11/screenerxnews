from typing import Dict, Any, List, Set
from decimal import Decimal
from datetime import datetime, timezone

class WalletClusterEngine:
    """
    Graph clustering engine for detecting insider rings, coordinated bundlers,
    and deployer funding trees.
    """
    DETECTOR_VERSION = "v2_graph_cluster"

    @classmethod
    def evaluate_cluster(
        cls,
        funder_map: Dict[str, str], # {wallet_address: funder_address}
        slot_map: Dict[str, int],   # {wallet_address: slot}
        deployer_address: str
    ) -> List[Dict[str, Any]]:
        clusters: List[Dict[str, Any]] = []

        # 1. Group by common funder
        funder_groups: Dict[str, List[str]] = {}
        for wallet, funder in funder_map.items():
            if funder:
                funder_groups.setdefault(funder, []).append(wallet)

        for funder, members in funder_groups.items():
            if len(members) >= 2:
                is_deployer_root = (funder == deployer_address)
                confidence = Decimal("95.00") if is_deployer_root else Decimal("80.00")
                clusters.append({
                    "cluster_type": "DEPLOYER_FUNDING_TREE" if is_deployer_root else "SHARED_ROOT_FUNDER",
                    "funder_address": funder,
                    "members": members,
                    "confidence": confidence,
                    "detector_version": cls.DETECTOR_VERSION,
                    "evidence": {
                        "shared_funder": funder,
                        "member_count": len(members),
                        "is_deployer_root": is_deployer_root
                    }
                })

        # 2. Group by same-slot execution
        slot_groups: Dict[int, List[str]] = {}
        for wallet, slot in slot_map.items():
            slot_groups.setdefault(slot, []).append(wallet)

        for slot, members in slot_groups.items():
            if len(members) >= 3:
                clusters.append({
                    "cluster_type": "SAME_SLOT_BUNDLER",
                    "slot": slot,
                    "members": members,
                    "confidence": Decimal("75.00"),
                    "detector_version": cls.DETECTOR_VERSION,
                    "evidence": {
                        "slot": slot,
                        "bundled_wallet_count": len(members)
                    }
                })

        return clusters

    @classmethod
    def classify_wallet(
        cls,
        wallet_address: str,
        sol_balance_lamports: int,
        realized_pnl_usd: Decimal,
        win_rate: Decimal,
        is_deployer: bool,
        is_in_bundler_cluster: bool
    ) -> str:
        """
        Categorizes a wallet into defined archetypes.
        """
        if is_deployer:
            return "DEPLOYER"
        if is_in_bundler_cluster:
            return "BUNDLER_CANDIDATE"
        if sol_balance_lamports > 500_000_000_000: # > 500 SOL
            return "WHALE"
        if realized_pnl_usd > Decimal("10000.00") and win_rate >= Decimal("65.00"):
            return "SMART_MONEY"
        return "UNKNOWN"

wallet_cluster_engine = WalletClusterEngine()
