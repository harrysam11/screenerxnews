import json
import uuid
import sqlite3
import logging
from decimal import Decimal
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

from app.core.config import settings
from app.core.chains import normalize_chain_id
from app.schemas.contracts import (
    TriState,
    OpportunityScoreBreakdown,
    EvidenceStatus
)
from app.services.persistent_paper_service import PersistentPaperService, persistent_paper_service, DecimalProvenanceSource
from app.services.multichain_scorer import multichain_scorer

logger = logging.getLogger(__name__)

class CandidateResearchScorer:
    """
    Candidate Research Scoring & Ranking Service (Phase P10.2-J.1 Deliverable 5).
    Evaluates candidate assets against:
    - Canonical asset identity (chain_id, asset_address).
    - Decimal verification status and TTL freshness.
    - Transparent 4-component score: M (Market), S (Safety), W (Liquidity), N (Narrative).
    - Clear penalties for missing data, expired TTLs, or unverified decimals (no fabrication).
    - Persists audit trails in candidate_research_scores table.
    """

    SCORING_VERSION = "v1.0_research_scorer"

    def __init__(self, db_path: Optional[str] = None, paper_service: Optional[PersistentPaperService] = None):
        self.db_path = db_path or settings.get_canonical_sqlite_path()
        self.paper_service = paper_service or (PersistentPaperService(db_path=self.db_path) if db_path else persistent_paper_service)

    def _get_connection(self) -> sqlite3.Connection:
        if self.db_path == ":memory:":
            conn = sqlite3.connect("file:cand_scoring_mem?mode=memory&cache=shared", uri=True, timeout=30.0, isolation_level=None)
        else:
            conn = sqlite3.connect(self.db_path, timeout=30.0, check_same_thread=False, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    def score_candidate(
        self,
        chain_id: str,
        asset_address: str,
        symbol: Optional[str] = None,
        name: Optional[str] = None,
        liquidity_usd: Decimal = Decimal("0"),
        volume_5m_usd: Decimal = Decimal("0"),
        volume_1h_usd: Decimal = Decimal("0"),
        tx_count_5m: int = 0,
        price_change_5m_pct: Decimal = Decimal("0"),
        is_honeypot: bool = False,
        sell_tax_bps: Optional[int] = None,
        is_mint_revoked: Optional[TriState] = None,
        is_freeze_revoked: Optional[TriState] = None,
        lp_status: Optional[str] = None,
        top10_concentration_pct: Optional[Decimal] = None,
        bundler_cluster_detected: bool = False,
        is_mixer_funded: bool = False,
        provenance: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Computes explainable candidate research score and persists evaluation in SQLite.
        """
        norm_chain = normalize_chain_id(chain_id)
        now_str = datetime.now(timezone.utc).isoformat()
        score_id = f"scr_{uuid.uuid4().hex[:12]}"

        # 1. Inspect decimal verification status & TTL freshness
        dec_status = self.paper_service.get_decimal_verification_status(norm_chain, asset_address)
        is_decimal_verified = False
        is_decimal_expired = False
        data_freshness_status = "UNKNOWN"
        verification_status = "UNVERIFIED"

        limitations: List[str] = []
        risk_findings: List[str] = []

        if dec_status:
            verification_status = dec_status.get("status", "UNVERIFIED")
            is_decimal_expired = dec_status.get("is_expired", False)
            if verification_status == "VERIFIED":
                if is_decimal_expired:
                    data_freshness_status = "EXPIRED"
                    limitations.append("Token decimal verification TTL has expired; re-verification required.")
                    risk_findings.append("STALE_DECIMAL_VERIFICATION")
                else:
                    data_freshness_status = "FRESH"
                    is_decimal_verified = True
            else:
                data_freshness_status = "UNVERIFIED"
                limitations.append(f"Token decimal status is '{verification_status}'; not verified by authoritative source.")
                risk_findings.append(f"UNVERIFIED_DECIMALS_{verification_status}")
        else:
            limitations.append("No token decimal verification record found; fail closed.")
            risk_findings.append("MISSING_DECIMAL_VERIFICATION")

        # 2. Check for missing liquidity / volume data
        if liquidity_usd <= Decimal("0"):
            limitations.append("Zero or missing liquidity pool data; penalty applied.")
            risk_findings.append("ZERO_LIQUIDITY")

        # 3. Calculate 4-component composite score via multichain_scorer
        breakdown = multichain_scorer.compute_composite_opportunity_score(
            chain_id=norm_chain,
            asset_address=asset_address,
            liquidity_usd=liquidity_usd,
            volume_5m_usd=volume_5m_usd,
            volume_1h_usd=volume_1h_usd,
            tx_count_5m=tx_count_5m,
            price_change_5m_pct=price_change_5m_pct,
            is_honeypot=is_honeypot,
            sell_tax_bps=sell_tax_bps,
            is_mint_revoked=is_mint_revoked,
            is_freeze_revoked=is_freeze_revoked,
            lp_status=lp_status,
            top10_concentration_pct=top10_concentration_pct,
            bundler_cluster_detected=bundler_cluster_detected,
            is_mixer_funded=is_mixer_funded,
            token_symbol=symbol or "",
            token_name=name or ""
        )

        composite_score = float(breakdown.composite_score) if breakdown.composite_score is not None else None
        hard_reject = breakdown.hard_reject_triggered

        # 4. Apply strict financial safety penalty for unverified or expired decimals
        if not is_decimal_verified or is_decimal_expired:
            hard_reject = True
            breakdown.rejection_reasons.extend(risk_findings)
            # Penalize composite score: unverified assets cannot be scored as top candidates
            if composite_score is not None:
                composite_score = min(composite_score, 10.0)

        # Confidence assessment
        if hard_reject:
            confidence = "REJECTED"
        elif is_decimal_verified and data_freshness_status == "FRESH" and liquidity_usd > Decimal("10000"):
            confidence = "HIGH"
        elif is_decimal_verified:
            confidence = "MEDIUM"
        else:
            confidence = "LOW"

        breakdown_dict = breakdown.model_dump()
        breakdown_dict["confidence"] = confidence
        breakdown_dict["limitations"] = limitations
        breakdown_dict["is_decimal_verified"] = is_decimal_verified
        breakdown_dict["is_decimal_expired"] = is_decimal_expired

        breakdown_json = json.dumps(breakdown_dict, default=str)
        risk_json = json.dumps(breakdown.rejection_reasons or risk_findings, default=str)
        provenance_json = json.dumps(provenance or {}, default=str)

        # 5. Persist to candidate_research_scores under BEGIN IMMEDIATE;
        with self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            try:
                conn.execute(
                    """
                    INSERT INTO candidate_research_scores (
                        score_id, chain_id, asset_address, symbol, composite_score,
                        score_breakdown_json, risk_findings_json, data_freshness_status,
                        verification_status, provenance_json, scoring_version, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        score_id, norm_chain, asset_address, symbol, composite_score,
                        breakdown_json, risk_json, data_freshness_status,
                        verification_status, provenance_json, self.SCORING_VERSION, now_str
                    )
                )
                conn.execute("COMMIT;")
            except Exception:
                conn.execute("ROLLBACK;")
                raise

        return {
            "score_id": score_id,
            "chain_id": norm_chain,
            "asset_address": asset_address,
            "symbol": symbol,
            "composite_score": composite_score,
            "confidence": confidence,
            "hard_reject_triggered": hard_reject,
            "data_freshness_status": data_freshness_status,
            "verification_status": verification_status,
            "score_breakdown": breakdown_dict,
            "risk_findings": breakdown.rejection_reasons or risk_findings,
            "limitations": limitations,
            "scoring_version": self.SCORING_VERSION,
            "created_at": now_str
        }

    def get_rankings(
        self,
        chain_id: Optional[str] = None,
        min_score: float = 0.0,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """
        Retrieves candidate rankings sorted by composite score descending.
        """
        with self._get_connection() as conn:
            query = """
                SELECT * FROM candidate_research_scores
                WHERE composite_score IS NOT NULL AND composite_score >= ?
            """
            params: List[Any] = [min_score]
            if chain_id:
                norm_c = normalize_chain_id(chain_id)
                query += " AND chain_id = ?"
                params.append(norm_c)

            query += " ORDER BY composite_score DESC, created_at DESC LIMIT ?;"
            params.append(limit)

            cur = conn.execute(query, params)
            results = []
            for row in cur.fetchall():
                it = dict(row)
                try:
                    it["score_breakdown"] = json.loads(it["score_breakdown_json"])
                except Exception:
                    it["score_breakdown"] = {}
                try:
                    it["risk_findings"] = json.loads(it["risk_findings_json"])
                except Exception:
                    it["risk_findings"] = []
                try:
                    it["provenance"] = json.loads(it["provenance_json"])
                except Exception:
                    it["provenance"] = {}
                results.append(it)
            return results

candidate_research_scorer = CandidateResearchScorer()
