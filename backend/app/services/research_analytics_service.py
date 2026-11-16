import logging
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation
from typing import List, Dict, Any, Optional
import json

from app.db.research_history_store import ResearchHistoryStore, format_decimal_exact, normalize_utc_timestamp
from app.schemas.research_analytics import (
    HistoricalObservationDTO,
    HistoricalObservationListResponse,
    TokenMomentumMetricsDTO,
    MomentumLeaderboardResponse,
    CrossChainChainMetrics,
    CrossChainMarketSummaryResponse,
    WatchlistResponse,
    StrategyFeatureVectorDTO,
    PruneAgedObservationsResponse
)
from app.schemas.market_data import AggregatedTokenRanking

logger = logging.getLogger("memecoin_os.research_analytics")


class ResearchAnalyticsService:
    """
    Research Intelligence & Historical Analysis Engine (Phase P1.4).
    Features:
    - Ingests and persists timestamped token observations to dedicated research_history.db.
    - Computes windowed momentum velocities, volume acceleration, and liquidity migration.
    - Compares multi-chain liquidity depth and volume share across monitored chains.
    - Manages research watchlists with configurable filter criteria.
    - Generates versioned, reproducible strategy feature vectors (v1.4_research_vector).
    - Completely read-only for analytics consumers; zero execution side effects.
    """

    def __init__(self, store: Optional[ResearchHistoryStore] = None):
        self.store = store or ResearchHistoryStore()

    # -------------------------------------------------------------------------
    # Observation Ingestion & Historical Series
    # -------------------------------------------------------------------------
    def record_token_observations(self, tokens: List[AggregatedTokenRanking]) -> int:
        """
        Extracts and logs immutable historical observations from aggregated screener tokens.
        Preserves financial precision, provenance, and idempotency.
        """
        obs_batch = []
        for t in tokens:
            obs = {
                "chain_id": t.chain_id,
                "token_address": t.token_address,
                "symbol": t.symbol,
                "name": t.name,
                "price_usd": str(t.price_usd) if t.price_usd is not None else None,
                "price_native": str(t.price_native) if t.price_native is not None else None,
                "liquidity_usd": str(t.liquidity_usd) if t.liquidity_usd is not None else None,
                "volume_24h_usd": str(t.volume_24h_usd) if t.volume_24h_usd is not None else None,
                "volume_1h_usd": str(t.volume_1h_usd) if t.volume_1h_usd is not None else None,
                "volume_5m_usd": str(t.volume_5m_usd) if t.volume_5m_usd is not None else None,
                "price_change_5m_pct": t.price_change_5m_pct,
                "price_change_1h_pct": t.price_change_1h_pct,
                "price_change_24h_pct": t.price_change_24h_pct,
                "fdv_usd": str(t.fdv_usd) if t.fdv_usd is not None else None,
                "market_cap_usd": str(t.market_cap_usd) if t.market_cap_usd is not None else None,
                "txns_24h_total": t.txns_24h_total,
                "quality_score": float(t.quality_score) if t.quality_score is not None else None,
                "scoring_version": "v1.4_research_vector",
                "anomaly_flags": t.anomaly_flags,
                "canonical_candidate_flag": t.canonical_candidate_flag,
                "provider_source": t.provider_source,
                "endpoint_sources": t.endpoint_sources,
                "observed_at": normalize_utc_timestamp(t.fetched_at)
            }
            obs_batch.append(obs)

        inserted_count = self.store.ingest_token_observations_batch(obs_batch)
        
        # After ingestion, derive & update momentum for each ingested token
        for obs in obs_batch:
            try:
                self.compute_and_update_momentum(obs["chain_id"], obs["token_address"])
            except Exception as e:
                logger.warning("Failed to update momentum for %s:%s: %s", obs["chain_id"], obs["token_address"], e)

        return inserted_count

    def get_token_history(
        self,
        chain_id: str,
        token_address: str,
        limit: int = 50,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None
    ) -> HistoricalObservationListResponse:
        """Queries historical observation series for a specific token."""
        raw_rows = self.store.query_token_observations(
            chain_id=chain_id,
            token_address=token_address,
            limit=limit,
            start_time=start_time,
            end_time=end_time
        )
        obs_dtos = []
        for r in raw_rows:
            try:
                anom = json.loads(r["anomaly_flags_json"])
            except Exception:
                anom = []
            try:
                endpoints = json.loads(r["endpoint_sources_json"])
            except Exception:
                endpoints = []

            obs_dtos.append(HistoricalObservationDTO(
                id=r["id"],
                chain_id=r["chain_id"],
                token_address=r["token_address"],
                symbol=r["symbol"],
                name=r["name"],
                price_usd=r["price_usd"],
                price_native=r["price_native"],
                liquidity_usd=r["liquidity_usd"],
                volume_24h_usd=r["volume_24h_usd"],
                volume_1h_usd=r["volume_1h_usd"],
                volume_5m_usd=r["volume_5m_usd"],
                price_change_5m_pct=r["price_change_5m_pct"],
                price_change_1h_pct=r["price_change_1h_pct"],
                price_change_24h_pct=r["price_change_24h_pct"],
                fdv_usd=r["fdv_usd"],
                market_cap_usd=r["market_cap_usd"],
                txns_24h_total=r["txns_24h_total"],
                quality_score=r["quality_score"],
                scoring_version=r["scoring_version"],
                anomaly_flags=anom,
                canonical_candidate_flag=bool(r["canonical_candidate_flag"]),
                provider_source=r["provider_source"],
                endpoint_sources=endpoints,
                observed_at=r["observed_at"],
                created_at=r["created_at"]
            ))

        return HistoricalObservationListResponse(
            chain_id=chain_id,
            token_address=token_address,
            total_observations=len(obs_dtos),
            observations=obs_dtos
        )

    # -------------------------------------------------------------------------
    # Momentum & Velocity Computation
    # -------------------------------------------------------------------------
    def compute_and_update_momentum(self, chain_id: str, token_address: str) -> Optional[TokenMomentumMetricsDTO]:
        """
        Derives windowed momentum velocities and liquidity migration direction from observation series.
        Separates provider-reported price changes from historically calculated momentum.
        """
        history = self.store.query_token_observations(chain_id, token_address, limit=100)
        if not history:
            return None

        newest = history[0]
        oldest = history[-1]
        obs_count = len(history)

        symbol = newest.get("symbol")
        
        # Provider-reported changes (unaltered observations from provider)
        provider_5m = newest.get("price_change_5m_pct")
        provider_1h = newest.get("price_change_1h_pct")
        provider_24h = newest.get("price_change_24h_pct")

        # Historical windowed velocity calculations from observation series
        hist_vel_5m: Optional[float] = None
        hist_vel_1h: Optional[float] = None
        hist_vel_24h: Optional[float] = None

        try:
            t_latest = datetime.fromisoformat(newest["observed_at"].replace("Z", "+00:00"))
            p_latest_val = newest.get("price_usd")
            p_latest = Decimal(p_latest_val) if p_latest_val is not None else None

            if p_latest and p_latest > Decimal("0") and obs_count >= 2:
                # Helper to calculate window percentage delta with strict temporal coverage requirements:
                # Requires that the baseline observation satisfies the requested window duration:
                # span_seconds >= target_seconds * min_coverage_ratio (default 70% coverage)
                # and span_seconds <= target_seconds * 1.25 (to prevent selecting points from an older window).
                def _calc_window_velocity(window_delta: timedelta, min_coverage_ratio: float = 0.70) -> Optional[float]:
                    target_seconds = window_delta.total_seconds()
                    min_span = target_seconds * min_coverage_ratio
                    max_span = target_seconds * 1.25

                    valid_candidates = []
                    for row in history:
                        try:
                            t_obs = datetime.fromisoformat(row["observed_at"].replace("Z", "+00:00"))
                            span = (t_latest - t_obs).total_seconds()
                            if min_span <= span <= max_span:
                                valid_candidates.append((span, row))
                        except Exception:
                            continue

                    if not valid_candidates:
                        return None

                    # Select candidate closest to target_seconds
                    best_span, baseline_obs = min(valid_candidates, key=lambda c: abs(c[0] - target_seconds))
                    p_old_val = baseline_obs.get("price_usd")
                    if p_old_val is not None:
                        p_old = Decimal(p_old_val)
                        if p_old > Decimal("0"):
                            delta = ((p_latest - p_old) / p_old) * Decimal("100")
                            return round(float(delta), 2)
                    return None

                hist_vel_5m = _calc_window_velocity(timedelta(minutes=5))
                hist_vel_1h = _calc_window_velocity(timedelta(hours=1))
                hist_vel_24h = _calc_window_velocity(timedelta(hours=24))

        except Exception as e:
            logger.warning("Error calculating historical velocity for %s:%s: %s", chain_id, token_address, e)

        hist_calc_status = "CALCULATED" if any(v is not None for v in (hist_vel_5m, hist_vel_1h, hist_vel_24h)) else "HISTORICAL_DATA_UNAVAILABLE"

        # Volume acceleration: (1h volume * 24) / 24h volume
        vol_accel: Optional[float] = None
        if newest.get("volume_1h_usd") and newest.get("volume_24h_usd"):
            try:
                v1h = Decimal(newest["volume_1h_usd"])
                v24h = Decimal(newest["volume_24h_usd"])
                if v24h > Decimal("0"):
                    ratio = float((v1h * Decimal("24")) / v24h)
                    vol_accel = round(ratio, 2)
            except Exception:
                vol_accel = None

        # Liquidity migration direction
        liq_direction = "INSUFFICIENT_DATA"
        liq_delta_usd: Optional[str] = None

        if obs_count >= 2 and newest.get("liquidity_usd") and oldest.get("liquidity_usd"):
            try:
                curr_liq = Decimal(newest["liquidity_usd"])
                prev_liq = Decimal(oldest["liquidity_usd"])
                delta_liq = curr_liq - prev_liq
                liq_delta_usd = format_decimal_exact(delta_liq)

                if prev_liq > Decimal("0"):
                    pct_change = (delta_liq / prev_liq) * Decimal("100")
                    if pct_change >= Decimal("5.0"):
                        liq_direction = "EXPANDING"
                    elif pct_change <= Decimal("-5.0"):
                        liq_direction = "CONTRACTING"
                    else:
                        liq_direction = "STABLE"
                else:
                    liq_direction = "EXPANDING" if curr_liq > Decimal("0") else "STABLE"
            except Exception:
                liq_direction = "INSUFFICIENT_DATA"

        metrics_dict = {
            "chain_id": chain_id,
            "token_address": token_address,
            "symbol": symbol,
            "metric_type": "LATEST_STATE_PROJECTION",
            "provider_price_change_5m_pct": provider_5m,
            "provider_price_change_1h_pct": provider_1h,
            "provider_price_change_24h_pct": provider_24h,
            "historical_velocity_5m_pct": hist_vel_5m,
            "historical_velocity_1h_pct": hist_vel_1h,
            "historical_velocity_24h_pct": hist_vel_24h,
            "historical_calculation_status": hist_calc_status,
            "volume_acceleration_ratio": vol_accel,
            "liquidity_migration_direction": liq_direction,
            "liquidity_delta_usd": liq_delta_usd,
            "observations_count": obs_count,
            "first_observed_at": oldest["observed_at"],
            "last_observed_at": newest["observed_at"]
        }

        self.store.upsert_momentum_metrics(metrics_dict)
        raw_persisted = self.store.get_momentum_metrics(chain_id, token_address)
        if raw_persisted:
            return TokenMomentumMetricsDTO(**raw_persisted)
        return None

    def get_momentum_leaderboard(
        self,
        chain_id: Optional[str] = None,
        sort_by: str = "historical_velocity_1h_pct",
        limit: int = 50
    ) -> MomentumLeaderboardResponse:
        """Returns ordered momentum leaderboards across monitored tokens."""
        rows = self.store.query_momentum_leaders(chain_id=chain_id, sort_by=sort_by, limit=limit)
        items = [TokenMomentumMetricsDTO(**r) for r in rows]
        return MomentumLeaderboardResponse(
            chain_id=chain_id,
            sort_by=sort_by,
            total_items=len(items),
            items=items
        )

    # -------------------------------------------------------------------------
    # Cross-Chain Comparative Market Analytics
    # -------------------------------------------------------------------------
    def get_cross_chain_market_summary(self, current_tokens: Optional[List[AggregatedTokenRanking]] = None) -> CrossChainMarketSummaryResponse:
        """
        Generates comparative market metrics across Solana, Base, Ethereum, and Arbitrum.
        Correctly reports average_quality_score as None (null) for chains without tokens.
        """
        chain_names = {
            "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp": "Solana",
            "solana:mainnet": "Solana",
            "eip155:8453": "Base",
            "eip155:1": "Ethereum",
            "eip155:42161": "Arbitrum"
        }

        # Monitored canonical chains
        canonical_chains = [
            "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp",
            "eip155:8453",
            "eip155:1",
            "eip155:42161"
        ]

        chain_data: Dict[str, Dict[str, Any]] = {
            cid: {
                "tokens": 0,
                "liquidity": Decimal("0"),
                "volume": Decimal("0"),
                "quality_scores": [],
                "top_token": None,
                "top_velocity": -999999.0
            }
            for cid in canonical_chains
        }

        # If current active tokens passed from screener, use them; otherwise fallback to store
        tokens_to_aggregate = current_tokens or []

        for t in tokens_to_aggregate:
            cid = t.chain_id
            # Normalize legacy alias
            if cid == "solana:mainnet":
                cid = "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"
            if cid not in chain_data:
                continue

            chain_data[cid]["tokens"] += 1
            if t.liquidity_usd:
                chain_data[cid]["liquidity"] += Decimal(str(t.liquidity_usd))
            if t.volume_24h_usd:
                chain_data[cid]["volume"] += Decimal(str(t.volume_24h_usd))
            if t.quality_score:
                chain_data[cid]["quality_scores"].append(float(t.quality_score))

            vel = t.price_change_1h_pct or 0.0
            if vel > chain_data[cid]["top_velocity"]:
                chain_data[cid]["top_velocity"] = vel
                chain_data[cid]["top_token"] = t.symbol

        total_agg_liq = sum(cd["liquidity"] for cd in chain_data.values())
        total_agg_vol = sum(cd["volume"] for cd in chain_data.values())
        total_discovered = sum(cd["tokens"] for cd in chain_data.values())

        chain_metrics_list = []
        for cid in canonical_chains:
            cd = chain_data[cid]
            scores = cd["quality_scores"]
            
            # Explicit null if no tokens discovered on chain, avoiding fabricated 0.0
            avg_quality = round(sum(scores) / len(scores), 1) if scores else (None if cd["tokens"] == 0 else 0.0)

            liq_share = float((cd["liquidity"] / total_agg_liq) * 100) if total_agg_liq > 0 else 0.0
            vol_share = float((cd["volume"] / total_agg_vol) * 100) if total_agg_vol > 0 else 0.0

            chain_metrics_list.append(CrossChainChainMetrics(
                chain_id=cid,
                chain_name=chain_names.get(cid, cid),
                token_count=cd["tokens"],
                total_liquidity_usd=format_decimal_exact(cd["liquidity"]) or "0.0",
                total_volume_24h_usd=format_decimal_exact(cd["volume"]) or "0.0",
                average_quality_score=avg_quality,
                liquidity_share_pct=round(liq_share, 2),
                volume_share_pct=round(vol_share, 2),
                top_momentum_token=cd["top_token"]
            ))

        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return CrossChainMarketSummaryResponse(
            timestamp=now_iso,
            total_monitored_chains=len(canonical_chains),
            total_discovered_tokens=total_discovered,
            total_aggregate_liquidity_usd=format_decimal_exact(total_agg_liq) or "0.0",
            total_aggregate_volume_24h_usd=format_decimal_exact(total_agg_vol) or "0.0",
            chains=chain_metrics_list
        )

    # -------------------------------------------------------------------------
    # Strategy Feature Vectors
    # -------------------------------------------------------------------------
    def generate_strategy_feature_vector(
        self,
        token: AggregatedTokenRanking,
        feature_version: str = "v1.4_research_vector"
    ) -> StrategyFeatureVectorDTO:
        """
        Generates a versioned, reproducible numeric feature vector for paper trading research.
        Preserves missing-value semantics and source provenance metadata.
        """
        momentum = self.store.get_momentum_metrics(token.chain_id, token.token_address)

        # Map liquidity migration direction to numeric factor
        liq_dir_num = 0
        if momentum:
            ld = momentum.get("liquidity_migration_direction")
            if ld == "EXPANDING":
                liq_dir_num = 1
            elif ld == "CONTRACTING":
                liq_dir_num = -1

        vector_data = {
            "quality_score": float(token.quality_score) if token.quality_score is not None else 0.0,
            "price_usd": float(token.price_usd) if token.price_usd is not None else None,
            "liquidity_usd": float(token.liquidity_usd) if token.liquidity_usd is not None else 0.0,
            "volume_24h_usd": float(token.volume_24h_usd) if token.volume_24h_usd is not None else 0.0,
            "volume_to_liquidity_ratio": round(float(token.volume_24h_usd / token.liquidity_usd), 2) if token.liquidity_usd and token.liquidity_usd > 0 else 0.0,
            "provider_price_change_5m_pct": float(token.price_change_5m_pct) if token.price_change_5m_pct is not None else None,
            "provider_price_change_1h_pct": float(token.price_change_1h_pct) if token.price_change_1h_pct is not None else None,
            "provider_price_change_24h_pct": float(token.price_change_24h_pct) if token.price_change_24h_pct is not None else None,
            "historical_velocity_1h_pct": float(momentum.get("historical_velocity_1h_pct")) if momentum and momentum.get("historical_velocity_1h_pct") is not None else None,
            "volume_acceleration_ratio": float(momentum.get("volume_acceleration_ratio")) if momentum and momentum.get("volume_acceleration_ratio") is not None else None,
            "liquidity_migration_numeric": liq_dir_num,
            "collision_candidates_count": token.collision_candidates_count,
            "anomaly_count": len(token.anomaly_flags),
            "canonical_candidate_numeric": 1 if token.canonical_candidate_flag else 0,
            "pair_age_hours": float(token.age_hours) if token.age_hours is not None else None,
            "txns_24h_total": token.txns_24h_total
        }

        obs_timestamp = normalize_utc_timestamp(token.fetched_at)
        src_prov = token.provider_source + (":" + ",".join(token.endpoint_sources) if token.endpoint_sources else "")

        feature_payload = {
            "chain_id": token.chain_id,
            "token_address": token.token_address,
            "symbol": token.symbol,
            "feature_version": feature_version,
            "observation_timestamp": obs_timestamp,
            "source_provenance": src_prov,
            "vector": vector_data
        }

        stored = self.store.store_strategy_feature_vector(feature_payload)
        return StrategyFeatureVectorDTO(
            feature_id=stored["feature_id"],
            chain_id=stored["chain_id"],
            token_address=stored["token_address"],
            symbol=stored["symbol"],
            feature_version=stored["feature_version"],
            observation_timestamp=stored["observation_timestamp"],
            source_provenance=stored["source_provenance"],
            calculation_version=feature_version,
            vector=stored["vector"],
            generated_at=stored["generated_at"]
        )

    # -------------------------------------------------------------------------
    # Watchlist Management & Filter Evaluation
    # -------------------------------------------------------------------------
    def create_watchlist(self, name: str, description: Optional[str] = None, criteria: Optional[Dict[str, Any]] = None) -> WatchlistResponse:
        created = self.store.create_watchlist(name, description, criteria)
        return WatchlistResponse(**created)

    def list_watchlists(self) -> List[WatchlistResponse]:
        items = self.store.get_watchlists()
        return [WatchlistResponse(**item) for item in items]

    def get_watchlist(self, watchlist_id: str) -> Optional[WatchlistResponse]:
        item = self.store.get_watchlist(watchlist_id)
        return WatchlistResponse(**item) if item else None

    def update_watchlist(
        self,
        watchlist_id: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        criteria: Optional[Dict[str, Any]] = None
    ) -> Optional[WatchlistResponse]:
        updated = self.store.update_watchlist(watchlist_id, name, description, criteria)
        return WatchlistResponse(**updated) if updated else None

    def delete_watchlist(self, watchlist_id: str) -> bool:
        return self.store.delete_watchlist(watchlist_id)

    # -------------------------------------------------------------------------
    # Bounded Retention Pruning
    # -------------------------------------------------------------------------
    def prune_history(
        self,
        retention_days: int = 14,
        max_per_token: int = 500,
        batch_size: int = 1000
    ) -> PruneAgedObservationsResponse:
        """Executes bounded retention pruning on research_history.db."""
        audit = self.store.prune_aged_observations(
            retention_days=retention_days,
            max_observations_per_token=max_per_token,
            batch_size=batch_size
        )
        return PruneAgedObservationsResponse(**audit)


# Global research analytics singleton
research_analytics_service = ResearchAnalyticsService()
