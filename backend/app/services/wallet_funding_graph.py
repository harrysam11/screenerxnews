from typing import Dict, Any, List
from decimal import Decimal
from app.services.wallet_cluster_engine import wallet_cluster_engine

class WalletFundingGraph:
    @staticmethod
    def build_funding_clusters(
        funder_map: Dict[str, str],
        slot_map: Dict[str, int],
        deployer_address: str
    ) -> List[Dict[str, Any]]:
        return wallet_cluster_engine.evaluate_cluster(
            funder_map=funder_map,
            slot_map=slot_map,
            deployer_address=deployer_address
        )

wallet_funding_graph = WalletFundingGraph()
