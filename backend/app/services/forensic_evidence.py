from typing import Dict, Any, List, Optional
from decimal import Decimal
from datetime import datetime, timezone
from app.schemas.contracts import EvidenceStatus

class ForensicEvidenceReport:
    """
    Standard forensic evidence envelope guaranteeing reproducible research.
    """
    @staticmethod
    def build_report(
        asset_id: str,
        mint_address: str,
        pool_address: str,
        slot_range: Dict[str, int],
        first_buyers: Dict[str, Any],
        deployer_analysis: Dict[str, Any],
        clusters: List[Dict[str, Any]],
        liquidity_analysis: Dict[str, Any]
    ) -> Dict[str, Any]:
        now = datetime.now(timezone.utc)

        # Calculate overall forensic confidence
        if first_buyers.get("confidence") == EvidenceStatus.INSUFFICIENT:
            overall_status = EvidenceStatus.INSUFFICIENT
        elif first_buyers.get("bundler_cluster_detected") or len(clusters) > 0:
            overall_status = EvidenceStatus.PROBABILISTIC
        else:
            overall_status = EvidenceStatus.VERIFIED

        return {
            "asset_id": asset_id,
            "mint_address": mint_address,
            "pool_address": pool_address,
            "observation_window": slot_range,
            "evidence_status": overall_status.value,
            "first_buyers_held_pct": first_buyers.get("total_held_pct", Decimal("0")),
            "bundler_cluster_detected": first_buyers.get("bundler_cluster_detected", False),
            "deployer_risk": deployer_analysis.get("risk_level", "UNKNOWN"),
            "deployer_holding_pct": deployer_analysis.get("current_holding_pct", Decimal("0")),
            "lp_status": liquidity_analysis.get("lp_status", "UNKNOWN"),
            "lp_burned_pct": liquidity_analysis.get("burn_pct", Decimal("0")),
            "clusters_count": len(clusters),
            "clusters": clusters,
            "generated_at": now.isoformat(),
            "reproducibility": {
                "source": "solana_rpc",
                "commitment": "confirmed",
                "parser_version": "v2_solana_parser"
            }
        }

forensic_evidence_report = ForensicEvidenceReport()
