"""
Market Data Quality & Intelligence Service (Phase P1.3).

Provides:
1. Explainable, deterministic quality scoring (0.0 - 100.0) with itemized factor scores and explicit deductions.
2. Heuristic anomaly indicators (suspicious turnover, price discrepancy, insufficient backing, insufficient data).
3. Cross-chain token identity disambiguation and collision candidate tracking (chain + contract address as identity).
4. New-pair monitoring and configurable discovery alert rules.
5. In-memory FIFO ring-buffer snapshots (bounded to 50 snapshots).
"""

import math
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, List, Optional, Tuple, Any

from app.schemas.market_data import (
    AggregatedTokenRanking,
    MarketPairObservation,
    MarketDataFreshness,
    ClassificationSource,
    ClassificationConfidence,
    QualityFactorBreakdown,
    MarketSnapshotDTO
)

SCORING_VERSION = "v1.3_market_quality"
MAX_SNAPSHOT_CAPACITY = 50


class MarketIntelligenceService:
    """
    In-memory market quality evaluation, anomaly detection, collision disambiguation,
    and snapshot management engine. Operates purely in-memory with zero operational DB mutations.
    """

    def __init__(self, max_snapshots: int = MAX_SNAPSHOT_CAPACITY):
        self._snapshots: deque[MarketSnapshotDTO] = deque(maxlen=max_snapshots)

    # -------------------------------------------------------------------------
    # 1. Quality Scoring Engine
    # -------------------------------------------------------------------------
    def evaluate_quality_score(
        self,
        token: AggregatedTokenRanking,
        anomaly_flags: Optional[List[str]] = None
    ) -> QualityFactorBreakdown:
        """
        Computes deterministic, explainable composite quality score bounded between 0.0 and 100.0.
        Separates liquidity depth, volume consistency, pool corroboration, provenance,
        and data completeness, with itemized deductions.
        """
        flags = anomaly_flags or []
        explanation_parts: List[str] = []

        # 1. Liquidity Quality (0 - 25 points)
        liq = token.liquidity_usd or Decimal("0.0")
        if liq <= Decimal("0.0"):
            liq_score = Decimal("0.0")
            explanation_parts.append("Zero liquidity: 0.0/25 pts.")
        elif liq < Decimal("1000.0"):
            liq_score = Decimal("5.0")
            explanation_parts.append(f"Micro-liquidity (${liq:,.0f}): 5.0/25 pts.")
        elif liq < Decimal("10000.0"):
            # Scale from 5.0 to 15.0
            fraction = (liq - Decimal("1000.0")) / Decimal("9000.0")
            liq_score = Decimal("5.0") + (Decimal("10.0") * fraction)
            explanation_parts.append(f"Moderate liquidity (${liq:,.0f}): {liq_score:.1f}/25 pts.")
        elif liq < Decimal("100000.0"):
            # Scale from 15.0 to 20.0
            fraction = (liq - Decimal("10000.0")) / Decimal("90000.0")
            liq_score = Decimal("15.0") + (Decimal("5.0") * fraction)
            explanation_parts.append(f"Healthy liquidity (${liq:,.0f}): {liq_score:.1f}/25 pts.")
        else:
            # Scale from 20.0 to 25.0 (capped at $500k)
            fraction = min(Decimal("1.0"), (liq - Decimal("100000.0")) / Decimal("400000.0"))
            liq_score = Decimal("20.0") + (Decimal("5.0") * fraction)
            explanation_parts.append(f"Deep liquidity (${liq:,.0f}): {liq_score:.1f}/25 pts.")
        liq_score = round(liq_score, 1)

        # 2. Volume / Activity Quality (0 - 25 points)
        vol_24h = token.volume_24h_usd or Decimal("0.0")
        if vol_24h <= Decimal("0.0"):
            vol_score = Decimal("0.0")
            explanation_parts.append("Zero 24h volume: 0.0/25 pts.")
        else:
            # Base volume presence
            if liq > Decimal("0.0"):
                ratio = vol_24h / liq
                if ratio > Decimal("50.0"):
                    # Anomalous turnover: lower score
                    vol_score = Decimal("5.0")
                    explanation_parts.append(f"High volume (${vol_24h:,.0f}) with anomalous turnover ratio ({ratio:.1f}x): 5.0/25 pts.")
                elif ratio >= Decimal("0.1") and ratio <= Decimal("10.0"):
                    # Healthy organic turnover
                    vol_score = Decimal("18.0")
                    explanation_parts.append(f"Consistent volume (${vol_24h:,.0f}, {ratio:.1f}x turnover): 18.0/25 pts.")
                else:
                    vol_score = Decimal("12.0")
                    explanation_parts.append(f"Moderate volume (${vol_24h:,.0f}, {ratio:.1f}x turnover): 12.0/25 pts.")
            else:
                vol_score = Decimal("5.0")
                explanation_parts.append(f"Volume (${vol_24h:,.0f}) with unverified liquidity: 5.0/25 pts.")

            # Bonus for short-term activity corroboration (up to 7 pts)
            if token.volume_1h_usd and token.volume_1h_usd > Decimal("0.0"):
                vol_score += Decimal("3.0")
            if token.volume_5m_usd and token.volume_5m_usd > Decimal("0.0"):
                vol_score += Decimal("2.0")
            if token.txns_24h_total and token.txns_24h_total > 0:
                vol_score += Decimal("2.0")
            vol_score = min(Decimal("25.0"), vol_score)
        vol_score = round(vol_score, 1)

        # 3. Pool Depth & Corroboration (0 - 20 points)
        pools_count = token.distinct_pools_aggregated or token.pool_count or 1
        endpoints_count = len(token.endpoint_sources) if token.endpoint_sources else 1
        if pools_count >= 3 and endpoints_count >= 2:
            pool_score = Decimal("20.0")
            explanation_parts.append(f"Multi-pool depth ({pools_count} pools, {endpoints_count} endpoints): 20.0/20 pts.")
        elif pools_count >= 2:
            pool_score = Decimal("16.0")
            explanation_parts.append(f"Multi-pool depth ({pools_count} pools): 16.0/20 pts.")
        elif endpoints_count >= 2:
            pool_score = Decimal("14.0")
            explanation_parts.append(f"Cross-endpoint corroboration ({endpoints_count} sources): 14.0/20 pts.")
        else:
            pool_score = Decimal("10.0")
            explanation_parts.append("Single pool observation: 10.0/20 pts.")
        pool_score = round(pool_score, 1)

        # 4. Provenance Confidence (0 - 15 points)
        if token.classification_source == ClassificationSource.CURATED_REGISTRY and token.confidence == ClassificationConfidence.HIGH:
            prov_score = Decimal("15.0")
            explanation_parts.append("Curated registry contract match: 15.0/15 pts.")
        elif token.confidence == ClassificationConfidence.MEDIUM:
            prov_score = Decimal("10.0")
            explanation_parts.append("Heuristic classification (MEDIUM): 10.0/15 pts.")
        else:
            prov_score = Decimal("5.0")
            explanation_parts.append("Unverified candidate classification (LOW): 5.0/15 pts.")
        prov_score = round(prov_score, 1)

        # 5. Data Completeness & Freshness (0 - 15 points)
        completeness_pts = Decimal("0.0")
        if token.price_usd is not None and token.price_usd > Decimal("0.0"):
            completeness_pts += Decimal("3.0")
        if token.liquidity_usd is not None and token.liquidity_usd > Decimal("0.0"):
            completeness_pts += Decimal("3.0")
        if token.volume_24h_usd is not None:
            completeness_pts += Decimal("2.0")
        if token.pair_created_at is not None:
            completeness_pts += Decimal("2.0")

        # Freshness pts (up to 5 pts)
        if token.freshness_status == MarketDataFreshness.FRESH:
            fresh_pts = Decimal("5.0")
        elif token.freshness_status == MarketDataFreshness.STALE:
            fresh_pts = Decimal("1.0")
        else:
            fresh_pts = Decimal("0.0")

        data_score = round(completeness_pts + fresh_pts, 1)
        explanation_parts.append(f"Data completeness ({completeness_pts:.0f} pts) & freshness ({fresh_pts:.0f} pts): {data_score:.1f}/15 pts.")

        base_sum = liq_score + vol_score + pool_score + prov_score + data_score

        # Explicit Itemized Deductions
        deductions: Dict[str, Decimal] = {}
        if token.price_usd is None or token.price_usd <= Decimal("0.0"):
            deductions["MISSING_PRICE"] = Decimal("20.0")
        if token.pair_created_at is None:
            deductions["MISSING_PAIR_CREATED_AT"] = Decimal("10.0")
        if token.freshness_status == MarketDataFreshness.STALE:
            deductions["STALE_OBSERVATION"] = Decimal("15.0")
        if liq < Decimal("5000.0"):
            deductions["MICRO_LIQUIDITY_WARNING"] = Decimal("20.0")
        if "SUSPICIOUS_VOLUME_TURNOVER" in flags:
            deductions["EXCESSIVE_TURNOVER_ANOMALY"] = Decimal("25.0")
        if "ZERO_TX_WITH_VOLUME" in flags:
            deductions["ZERO_TX_TURNOVER_ANOMALY"] = Decimal("15.0")
        if "MULTI_POOL_PRICE_DISCREPANCY" in flags:
            deductions["MULTI_POOL_PRICE_DISCREPANCY"] = Decimal("15.0")

        total_deductions = sum(deductions.values())
        composite_score = max(Decimal("0.0"), min(Decimal("100.0"), base_sum - total_deductions))
        composite_score = round(composite_score, 1)

        if deductions:
            ded_str = ", ".join(f"{k} (-{v:.0f})" for k, v in deductions.items())
            explanation_parts.append(f"Deductions applied: {ded_str}.")
        explanation_parts.append(f"Final composite quality score: {composite_score:.1f}/100.0.")

        return QualityFactorBreakdown(
            liquidity_quality=liq_score,
            volume_activity_quality=vol_score,
            pool_depth_corroboration=pool_score,
            provenance_confidence=prov_score,
            data_completeness_freshness=data_score,
            deductions=deductions,
            composite_score=composite_score,
            scoring_version=SCORING_VERSION,
            disclaimer="Quality score is a descriptive heuristic of market data depth and completeness, not an audit, endorsement, or safety guarantee.",
            explanation=" ".join(explanation_parts)
        )

    # -------------------------------------------------------------------------
    # 2. Heuristic Anomaly Detection
    # -------------------------------------------------------------------------
    def detect_anomalies(
        self,
        token: AggregatedTokenRanking,
        token_pools: Optional[List[MarketPairObservation]] = None
    ) -> Tuple[List[str], Dict[str, Any]]:
        """
        Evaluates heuristic market anomaly indicators.
        Missing data fields (e.g. transaction counts or market cap) produce INSUFFICIENT_DATA,
        never a false-positive anomaly flag.
        """
        flags: List[str] = []
        details: Dict[str, Any] = {}
        insufficient_data_reasons: List[str] = []

        # 1. Volume-to-Liquidity Ratio Anomaly (> 50x)
        liq = token.liquidity_usd or Decimal("0.0")
        vol = token.volume_24h_usd or Decimal("0.0")
        if liq > Decimal("0.0") and vol > Decimal("0.0"):
            ratio = vol / liq
            details["volume_to_liquidity_ratio"] = float(round(ratio, 2))
            if ratio > Decimal("50.0"):
                flags.append("SUSPICIOUS_VOLUME_TURNOVER")
                details["suspicious_turnover_note"] = (
                    f"24h volume (${vol:,.2f}) exceeds 50x liquidity (${liq:,.2f}); "
                    f"indicates high turnover that warrants multi-pool investigation."
                )
        else:
            details["volume_to_liquidity_ratio"] = None

        # 2. Multi-Pool Price Discrepancy (> 15%)
        if token_pools and len(token_pools) > 1:
            valid_prices = [p.price_usd for p in token_pools if p.price_usd and p.price_usd > Decimal("0.0")]
            if len(valid_prices) > 1:
                min_p = min(valid_prices)
                max_p = max(valid_prices)
                discrepancy = (max_p - min_p) / min_p
                details["multi_pool_price_discrepancy_pct"] = float(round(discrepancy * Decimal("100.0"), 2))
                if discrepancy > Decimal("0.15"):
                    flags.append("MULTI_POOL_PRICE_DISCREPANCY")
                    details["price_discrepancy_note"] = (
                        f"Price diverges by {discrepancy * 100:.1f}% across distinct pools "
                        f"(${min_p} vs ${max_p}); investigate pool-specific pricing and timestamp sync."
                    )
            else:
                details["multi_pool_price_discrepancy_pct"] = None
        else:
            details["multi_pool_price_discrepancy_pct"] = None

        # 3. Insufficient Liquidity Backing (< 1% of Market Cap / FDV)
        comparable_valuation = token.market_cap_usd if (token.market_cap_usd and token.market_cap_usd > Decimal("0.0")) else (token.fdv_usd if (token.fdv_usd and token.fdv_usd > Decimal("0.0")) else None)
        if comparable_valuation is not None and comparable_valuation > Decimal("0.0") and liq > Decimal("0.0"):
            backing_ratio = liq / comparable_valuation
            details["liquidity_backing_ratio"] = float(round(backing_ratio, 4))
            if backing_ratio < Decimal("0.01"):
                flags.append("INSUFFICIENT_LIQUIDITY_BACKING")
                details["insufficient_backing_note"] = (
                    f"Liquidity (${liq:,.2f}) represents < 1% of valuation (${comparable_valuation:,.2f}); "
                    f"indicates high slippage risk."
                )
        else:
            details["liquidity_backing_ratio"] = None
            insufficient_data_reasons.append("Market cap and FDV unavailable from provider observation.")

        # 4. Zero Transactions Turnover (Volume > 0 with 0 Recorded Txns)
        if token.txns_24h_total is not None:
            details["txns_24h_total"] = token.txns_24h_total
            if token.txns_24h_total == 0 and vol > Decimal("0.0"):
                flags.append("ZERO_TX_WITH_VOLUME")
                details["zero_tx_note"] = f"Reported 24h volume (${vol:,.2f}) with 0 recorded transactions during observation window."
        else:
            details["txns_24h_total"] = None
            insufficient_data_reasons.append("24h transaction counts unavailable from provider observation.")

        # 5. INSUFFICIENT_DATA Handling
        if insufficient_data_reasons:
            flags.append("INSUFFICIENT_DATA")
            details["insufficient_data_reasons"] = insufficient_data_reasons

        return flags, details

    # -------------------------------------------------------------------------
    # 3. Cross-Chain Identity & Collision Disambiguation
    # -------------------------------------------------------------------------
    def disambiguate_cross_chain_identities(
        self,
        tokens: List[AggregatedTokenRanking]
    ) -> List[AggregatedTokenRanking]:
        """
        Identifies ticker collisions across chains. Primary identity is strictly
        (chain_id, token_address). Ticker collisions are marked as collision candidates,
        not verified clones. Elects canonical candidate flag with explicit heuristic rationale.
        """
        # Group tokens by normalized symbol
        symbol_groups: Dict[str, List[AggregatedTokenRanking]] = {}
        for t in tokens:
            sym = t.symbol.strip().upper() if t.symbol else "UNKNOWN"
            if sym not in symbol_groups:
                symbol_groups[sym] = []
            symbol_groups[sym].append(t)

        disambiguated: List[AggregatedTokenRanking] = []
        for t in tokens:
            sym = t.symbol.strip().upper() if t.symbol else "UNKNOWN"
            group = symbol_groups.get(sym, [])

            if len(group) <= 1 or sym == "UNKNOWN":
                # Unique token or unspecified ticker
                t.collision_candidates_count = 0
                t.collision_candidate_addresses = []
                t.canonical_candidate_flag = True
                t.canonical_candidate_rationale = (
                    "Unique ticker observed across monitored chains; subject to independent contract verification."
                )
            else:
                t.collision_candidates_count = len(group) - 1
                t.collision_candidate_addresses = [
                    f"{other.chain_id}:{other.token_address}"
                    for other in group
                    if (other.chain_id, other.token_address) != (t.chain_id, t.token_address)
                ]

                # Elect canonical candidate flag
                # Priority 1: Curated registry match
                curated_matches = [
                    c for c in group
                    if c.classification_source == ClassificationSource.CURATED_REGISTRY
                    and c.confidence == ClassificationConfidence.HIGH
                ]
                if len(curated_matches) == 1:
                    if (t.chain_id, t.token_address) == (curated_matches[0].chain_id, curated_matches[0].token_address):
                        t.canonical_candidate_flag = True
                        t.canonical_candidate_rationale = (
                            f"Curated contract address match on {t.chain_id} in verified asset registry."
                        )
                    else:
                        t.canonical_candidate_flag = False
                        t.canonical_candidate_rationale = (
                            f"Secondary collision candidate sharing symbol '{sym}' with curated contract on {curated_matches[0].chain_id}."
                        )
                else:
                    # Priority 2: Highest observed liquidity heuristic
                    highest_liq_candidate = max(group, key=lambda c: c.liquidity_usd or Decimal("0.0"))
                    if (t.chain_id, t.token_address) == (highest_liq_candidate.chain_id, highest_liq_candidate.token_address):
                        t.canonical_candidate_flag = True
                        t.canonical_candidate_rationale = (
                            f"Heuristic candidate based on highest observed DEX liquidity (${t.liquidity_usd:,.2f}); "
                            f"unverified deployment, not proof of authenticity."
                        )
                    else:
                        t.canonical_candidate_flag = False
                        t.canonical_candidate_rationale = (
                            f"Secondary collision candidate with lower observed liquidity (${t.liquidity_usd:,.2f}) "
                            f"compared to lead candidate on {highest_liq_candidate.chain_id}."
                        )

            disambiguated.append(t)

        return disambiguated

    # -------------------------------------------------------------------------
    # 4. New-Pair Monitoring & Configurable Discovery Alerts
    # -------------------------------------------------------------------------
    def evaluate_discovery_alerts(
        self,
        token: AggregatedTokenRanking,
        anomaly_flags: Optional[List[str]] = None
    ) -> Tuple[bool, List[str]]:
        """
        Evaluates new-pair status (< 24h) and declarative discovery alert rules.
        """
        flags = anomaly_flags or []
        is_new_pair = (token.age_hours is not None and token.age_hours <= 24.0)
        alerts: List[str] = []

        # Alert 1: Breakout momentum on newly launched pair
        if token.age_hours is not None and token.age_hours <= 2.0:
            vol = token.volume_24h_usd or Decimal("0.0")
            liq = token.liquidity_usd or Decimal("0.0")
            if vol >= Decimal("10000.0") and liq >= Decimal("5000.0"):
                alerts.append("NEW_PAIR_HIGH_MOMENTUM")

        # Alert 2: Rapid 1h acceleration on young pair
        if token.age_hours is not None and token.age_hours <= 6.0:
            vol_1h = token.volume_1h_usd or Decimal("0.0")
            if vol_1h >= Decimal("5000.0"):
                alerts.append("NEW_PAIR_ACCELERATION")

        # Alert 3: Suspicious turnover alert
        if "SUSPICIOUS_VOLUME_TURNOVER" in flags:
            alerts.append("ANOMALOUS_TURNOVER_ALERT")

        # Alert 4: Extreme price movement (1h or 5m)
        if token.price_change_1h_pct is not None and abs(token.price_change_1h_pct) >= Decimal("50.0"):
            alerts.append("EXTREME_PRICE_MOVEMENT_1H")
        if token.price_change_5m_pct is not None and abs(token.price_change_5m_pct) >= Decimal("20.0"):
            alerts.append("EXTREME_PRICE_MOVEMENT_5M")

        return is_new_pair, alerts

    # -------------------------------------------------------------------------
    # 5. Historical Observation Snapshots (In-Memory FIFO Ring Buffer)
    # -------------------------------------------------------------------------
    def capture_snapshot(
        self,
        tokens: List[AggregatedTokenRanking],
        total_pools: int
    ) -> MarketSnapshotDTO:
        """
        Captures an in-memory point-in-time observation snapshot into the ring buffer.
        Evicts oldest snapshot when capacity exceeds MAX_SNAPSHOT_CAPACITY (50).
        Disappears on process restart; not durable backtesting history.
        """
        now = datetime.now(timezone.utc)
        snap_id = f"snap_{int(time.time())}_{uuid.uuid4().hex[:6]}"

        chains = sorted(list({t.provider_network_id for t in tokens}))
        anomalies_count = sum(1 for t in tokens if t.anomaly_flags and any(f != "INSUFFICIENT_DATA" for f in t.anomaly_flags))

        valid_scores = [t.quality_score for t in tokens if t.quality_score is not None]
        avg_score = round(sum(valid_scores) / len(valid_scores), 1) if valid_scores else None

        tokens_summary = [
            {
                "chain_id": t.chain_id,
                "token_address": t.token_address,
                "symbol": t.symbol,
                "price_usd": float(t.price_usd) if t.price_usd else None,
                "liquidity_usd": float(t.liquidity_usd) if t.liquidity_usd else None,
                "volume_24h_usd": float(t.volume_24h_usd) if t.volume_24h_usd else None,
                "quality_score": float(t.quality_score) if t.quality_score is not None else None,
                "anomaly_flags": t.anomaly_flags,
                "canonical_candidate_flag": t.canonical_candidate_flag
            }
            for t in tokens[:10]  # top 10 summary
        ]

        snapshot = MarketSnapshotDTO(
            snapshot_id=snap_id,
            captured_at=now,
            total_tokens=len(tokens),
            total_pools=total_pools,
            chains_represented=chains,
            average_quality_score=avg_score,
            anomalies_detected_count=anomalies_count,
            source_provenance="IN_MEMORY_PUBLIC_OBSERVATIONS",
            note="Episodic in-memory snapshot ring buffer (max 50). Disappears on process restart; not durable backtesting history.",
            tokens_summary=tokens_summary
        )

        self._snapshots.append(snapshot)
        return snapshot

    def get_snapshots(self, limit: int = 50, offset: int = 0) -> List[MarketSnapshotDTO]:
        """Returns ordered list of in-memory snapshots (newest first)."""
        snaps = list(self._snapshots)
        snaps.reverse()
        return snaps[offset: offset + limit]

    def get_snapshot_by_id(self, snapshot_id: str) -> Optional[MarketSnapshotDTO]:
        """Finds snapshot by ID in ring buffer."""
        for s in self._snapshots:
            if s.snapshot_id == snapshot_id:
                return s
        return None

    def clear_snapshots(self) -> None:
        """Clears snapshot buffer (used in tests)."""
        self._snapshots.clear()


# Global singleton
market_intelligence_service = MarketIntelligenceService()
