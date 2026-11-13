from typing import Dict, Any, List, Optional
from decimal import Decimal
from datetime import datetime, timezone
from app.schemas.contracts import FirstBuyerItem, EvidenceStatus

class FirstBuyersDecoder:
    """
    Decodes the earliest swap transactions for a newly created token pool,
    identifying real recipient wallets, funding roots, and bundled sniper activity.
    """
    @staticmethod
    def analyze_buyers(
        pool_address: str,
        deployer_address: Optional[str],
        creation_slot: int,
        raw_transactions: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Analyzes up to the first 25 buyers.
        raw_transactions is a list of decoded swap records containing:
        {
            "tx_signature": str,
            "slot": int,
            "wallet_address": str,
            "sol_spent_lamports": int,
            "tokens_bought_raw": Decimal,
            "funder_address": Optional[str],
            "holding_pct": Decimal
        }
        """
        if not raw_transactions:
            return {
                "total_held_pct": Decimal("0.00"),
                "bundler_cluster_detected": False,
                "confidence": EvidenceStatus.INSUFFICIENT,
                "buyers": []
            }

        # Track funding occurrences to detect shared root funders
        funder_counts: Dict[str, int] = {}
        for tx in raw_transactions:
            funder = tx.get("funder_address")
            if funder:
                funder_counts[funder] = funder_counts.get(funder, 0) + 1

        buyers: List[FirstBuyerItem] = []
        total_held = Decimal("0.00")
        same_slot_count = 0
        shared_funder_count = 0
        deployer_linked_count = 0

        for rank, tx in enumerate(raw_transactions[:10], start=1):
            wallet = tx["wallet_address"]
            funder = tx.get("funder_address")
            is_shared = (funder is not None and funder_counts.get(funder, 0) > 1)
            is_same_slot = (tx.get("slot") == creation_slot)
            is_deployer_link = (funder == deployer_address) if deployer_address else False

            if is_shared:
                shared_funder_count += 1
            if is_same_slot:
                same_slot_count += 1
            if is_deployer_link:
                deployer_linked_count += 1

            holding_pct = tx.get("holding_pct", Decimal("0.00"))
            total_held += holding_pct

            buyers.append(FirstBuyerItem(
                buy_rank=rank,
                wallet_address=wallet,
                sol_spent_lamports=tx.get("sol_spent_lamports", 0),
                tokens_bought_raw=tx.get("tokens_bought_raw", Decimal("0")),
                holding_pct=holding_pct,
                current_status="HOLDING" if holding_pct > Decimal("0.5") else "DUMPED_100",
                funder_address=funder,
                is_funder_shared=is_shared,
                is_same_slot_as_pool=is_same_slot
            ))

        # Heuristic probabilistic bundler detection:
        # If >= 3 of top 10 buyers share a funder, OR >= 4 bought in the exact creation slot
        bundler_detected = (shared_funder_count >= 3) or (same_slot_count >= 4) or (deployer_linked_count >= 2)

        return {
            "total_held_pct": total_held,
            "bundler_cluster_detected": bundler_detected,
            "shared_funder_count": shared_funder_count,
            "same_slot_count": same_slot_count,
            "deployer_linked_count": deployer_linked_count,
            "confidence": EvidenceStatus.PROBABILISTIC if len(buyers) >= 5 else EvidenceStatus.INSUFFICIENT,
            "buyers": buyers
        }

first_buyers_decoder = FirstBuyersDecoder()
