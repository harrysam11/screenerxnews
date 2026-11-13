from typing import Dict, Any, Optional, List
from decimal import Decimal
from datetime import datetime, timezone
from app.schemas.contracts import (
    OpportunityScoreBreakdown,
    EvidenceStatus,
    TriState,
    TokenSecurityReport
)
from app.services.narrative_correlator import narrative_correlator

class MultiChainScorer:
    """
    Explainable, chain-calibrated composite opportunity scoring engine.
    Formula:
        O = 0.35 * M + 0.30 * S + 0.20 * W + 0.15 * N
    """
    ENGINE_VERSION = "v2_multichain_composite_scorer"

    # Chain-calibrated market liquidity thresholds (USD)
    CHAIN_LIQUIDITY_CALIBRATION = {
        "solana:mainnet": {"min_liquidity": Decimal("10000"), "target_liquidity": Decimal("50000")},
        "eip155:1": {"min_liquidity": Decimal("50000"), "target_liquidity": Decimal("200000")},
        "eip155:8453": {"min_liquidity": Decimal("20000"), "target_liquidity": Decimal("100000")},
        "eip155:56": {"min_liquidity": Decimal("20000"), "target_liquidity": Decimal("100000")},
        "eip155:42161": {"min_liquidity": Decimal("20000"), "target_liquidity": Decimal("100000")},
        "eip155:10": {"min_liquidity": Decimal("20000"), "target_liquidity": Decimal("100000")},
        "eip155:137": {"min_liquidity": Decimal("15000"), "target_liquidity": Decimal("75000")},
        "eip155:43114": {"min_liquidity": Decimal("20000"), "target_liquidity": Decimal("100000")},
        "sui:mainnet": {"min_liquidity": Decimal("15000"), "target_liquidity": Decimal("75000")},
        "aptos:mainnet": {"min_liquidity": Decimal("15000"), "target_liquidity": Decimal("75000")},
        "ton:mainnet": {"min_liquidity": Decimal("15000"), "target_liquidity": Decimal("75000")},
        "tron:mainnet": {"min_liquidity": Decimal("15000"), "target_liquidity": Decimal("75000")}
    }

    @classmethod
    def evaluate_market_momentum(
        cls,
        chain_id: str,
        liquidity_usd: Decimal,
        volume_5m_usd: Decimal,
        volume_1h_usd: Decimal,
        tx_count_5m: int,
        price_change_5m_pct: Decimal
    ) -> Decimal:
        calib = cls.CHAIN_LIQUIDITY_CALIBRATION.get(
            chain_id,
            {"min_liquidity": Decimal("20000"), "target_liquidity": Decimal("100000")}
        )
        min_liq = calib["min_liquidity"]
        target_liq = calib["target_liquidity"]

        # Liquidity score component (0 to 40 pts)
        if liquidity_usd <= Decimal("0"):
            liq_score = Decimal("0.0")
        elif liquidity_usd >= target_liq:
            liq_score = Decimal("40.0")
        elif liquidity_usd < min_liq:
            liq_score = (liquidity_usd / min_liq) * Decimal("15.0")
        else:
            pct = (liquidity_usd - min_liq) / (target_liq - min_liq)
            liq_score = Decimal("15.0") + (pct * Decimal("25.0"))

        # Volume velocity (0 to 30 pts)
        vol_score = Decimal("0.0")
        if volume_5m_usd > Decimal("5000"):
            vol_score += Decimal("15.0")
        elif volume_5m_usd > Decimal("1000"):
            vol_score += Decimal("8.0")

        if volume_1h_usd > Decimal("20000"):
            vol_score += Decimal("15.0")
        elif volume_1h_usd > Decimal("5000"):
            vol_score += Decimal("8.0")

        # Transaction frequency (0 to 15 pts)
        tx_score = min(Decimal("15.0"), Decimal(str(tx_count_5m * 0.5)))

        # Momentum / price change (0 to 15 pts)
        if Decimal("0.0") <= price_change_5m_pct <= Decimal("50.0"):
            mom_score = Decimal("15.0")
        elif price_change_5m_pct > Decimal("50.0"):
            mom_score = Decimal("8.0") # High blowoff risk
        elif Decimal("-10.0") <= price_change_5m_pct < Decimal("0.0"):
            mom_score = Decimal("5.0")
        else:
            mom_score = Decimal("0.0")

        total_market = liq_score + vol_score + tx_score + mom_score
        return min(Decimal("100.0"), max(Decimal("0.0"), total_market))

    @classmethod
    def evaluate_security_and_safety(
        cls,
        security_report: Optional[TokenSecurityReport],
        is_honeypot: bool = False,
        sell_tax_bps: Optional[int] = None,
        is_mint_revoked: Optional[TriState] = None,
        is_freeze_revoked: Optional[TriState] = None,
        lp_status: Optional[str] = None,
        is_proxy: bool = False
    ) -> Dict[str, Any]:
        rejection_reasons = []
        hard_reject = False

        if security_report:
            if security_report.is_honeypot == TriState.TRUE:
                hard_reject = True
                rejection_reasons.append("HONEYPOT_DETECTED: Sell simulation reverted or blocked")

            if security_report.is_mint_revoked == TriState.FALSE:
                hard_reject = True
                rejection_reasons.append("UNRENOUNCED_MINT_AUTHORITY: Unconstrained token minting detected")

            if security_report.transfer_fee_bps and security_report.transfer_fee_bps > 1500:
                hard_reject = True
                rejection_reasons.append(f"EXCESSIVE_SELL_TAX: {security_report.transfer_fee_bps / 100}% fee exceeds 15% threshold")

            if security_report.lp_status == "UNLOCKED":
                hard_reject = True
                rejection_reasons.append("UNLOCKED_LIQUIDITY: LP tokens are not burned or locked")

            # Check for critical fail findings
            for f in security_report.findings:
                if f.severity == "CRITICAL_FAIL" and f.code not in rejection_reasons:
                    hard_reject = True
                    rejection_reasons.append(f"{f.code}: {f.description}")

            is_mint_revoked = security_report.is_mint_revoked
            is_freeze_revoked = security_report.is_freeze_revoked
            lp_status = security_report.lp_status
        else:
            if is_honeypot:
                hard_reject = True
                rejection_reasons.append("HONEYPOT_DETECTED: Sell simulation reverted or blocked")
            if is_mint_revoked == TriState.FALSE:
                hard_reject = True
                rejection_reasons.append("UNRENOUNCED_MINT_AUTHORITY: Unconstrained token minting detected")
            if sell_tax_bps and sell_tax_bps > 1500:
                hard_reject = True
                rejection_reasons.append(f"EXCESSIVE_SELL_TAX: {sell_tax_bps / 100}% exceeds 15% threshold")
            if lp_status == "UNLOCKED":
                hard_reject = True
                rejection_reasons.append("UNLOCKED_LIQUIDITY: LP tokens are not burned or locked")

        # Compute security score (0-100)
        score = Decimal("0.0")
        if is_mint_revoked == TriState.TRUE:
            score += Decimal("35.0")
        if is_freeze_revoked == TriState.TRUE:
            score += Decimal("15.0")
        if lp_status in ("BURNED", "LOCKED"):
            score += Decimal("30.0")
        if not is_proxy:
            score += Decimal("10.0")
        if sell_tax_bps is None or sell_tax_bps <= 500:
            score += Decimal("10.0")

        return {
            "security_score": score,
            "hard_reject_triggered": hard_reject,
            "rejection_reasons": rejection_reasons
        }

    @classmethod
    def evaluate_wallet_intelligence(
        cls,
        top10_concentration_pct: Optional[Decimal],
        bundler_cluster_detected: bool,
        is_mixer_funded: bool,
        prior_deployments_count: int
    ) -> Decimal:
        base_score = Decimal("40.0")

        # Top 10 concentration (up to 30 pts)
        if top10_concentration_pct is not None:
            if top10_concentration_pct <= Decimal("15.0"):
                base_score += Decimal("30.0")
            elif top10_concentration_pct <= Decimal("25.0"):
                base_score += Decimal("20.0")
            elif top10_concentration_pct <= Decimal("40.0"):
                base_score += Decimal("10.0")
            else:
                base_score += Decimal("0.0") # Heavy whale concentration
        else:
            base_score += Decimal("10.0") # Neutral assumption

        # Penalties
        if bundler_cluster_detected:
            base_score -= Decimal("35.0")

        if is_mixer_funded:
            base_score -= Decimal("40.0")

        if prior_deployments_count >= 3:
            base_score -= Decimal("20.0")

        return max(Decimal("0.0"), min(Decimal("100.0"), base_score))

    @classmethod
    def compute_composite_opportunity_score(
        cls,
        chain_id: str,
        asset_address: str,
        # Market inputs
        liquidity_usd: Decimal,
        volume_5m_usd: Decimal,
        volume_1h_usd: Decimal,
        tx_count_5m: int,
        price_change_5m_pct: Decimal,
        # Security inputs
        security_report: Optional[TokenSecurityReport] = None,
        is_honeypot: bool = False,
        sell_tax_bps: Optional[int] = None,
        is_mint_revoked: Optional[TriState] = None,
        is_freeze_revoked: Optional[TriState] = None,
        lp_status: Optional[str] = None,
        is_proxy: bool = False,
        # Wallet inputs
        top10_concentration_pct: Optional[Decimal] = None,
        bundler_cluster_detected: bool = False,
        is_mixer_funded: bool = False,
        prior_deployments_count: int = 0,
        # Narrative inputs
        token_symbol: str = "",
        token_name: str = "",
        token_sectors: Optional[List[str]] = None,
        news_items: Optional[List[Dict[str, Any]]] = None
    ) -> OpportunityScoreBreakdown:
        # 1. Market Momentum
        m_score = cls.evaluate_market_momentum(
            chain_id=chain_id,
            liquidity_usd=liquidity_usd,
            volume_5m_usd=volume_5m_usd,
            volume_1h_usd=volume_1h_usd,
            tx_count_5m=tx_count_5m,
            price_change_5m_pct=price_change_5m_pct
        )

        # 2. Security & Safety
        sec_eval = cls.evaluate_security_and_safety(
            security_report=security_report,
            is_honeypot=is_honeypot,
            sell_tax_bps=sell_tax_bps,
            is_mint_revoked=is_mint_revoked,
            is_freeze_revoked=is_freeze_revoked,
            lp_status=lp_status,
            is_proxy=is_proxy
        )
        s_score = sec_eval["security_score"]
        hard_reject = sec_eval["hard_reject_triggered"]
        rejection_reasons = sec_eval["rejection_reasons"]

        # 3. Wallet Intelligence
        w_score = cls.evaluate_wallet_intelligence(
            top10_concentration_pct=top10_concentration_pct,
            bundler_cluster_detected=bundler_cluster_detected,
            is_mixer_funded=is_mixer_funded,
            prior_deployments_count=prior_deployments_count
        )

        # 4. Narrative Sentiment
        narr_eval = narrative_correlator.calculate_narrative_score(
            token_symbol=token_symbol,
            token_name=token_name,
            token_sectors=token_sectors or [],
            news_items=news_items or []
        )
        n_score = Decimal(str(narr_eval["narrative_score"]))

        # Composite calculation (Weights: 0.35 M + 0.30 S + 0.20 W + 0.15 N)
        if hard_reject:
            composite = None
        else:
            composite = (
                (Decimal("0.35") * m_score) +
                (Decimal("0.30") * s_score) +
                (Decimal("0.20") * w_score) +
                (Decimal("0.15") * n_score)
            )
            composite = round(composite, 2)

        # Calculate confidence coverage
        coverage_pct = Decimal("0.0")
        if liquidity_usd > Decimal("0"):
            coverage_pct += Decimal("25.0")
        if security_report is not None or (is_mint_revoked is not None and lp_status is not None):
            coverage_pct += Decimal("35.0")
        elif is_mint_revoked is not None:
            coverage_pct += Decimal("20.0")
        if top10_concentration_pct is not None:
            coverage_pct += Decimal("25.0")
        if news_items and len(news_items) > 0:
            coverage_pct += Decimal("15.0")

        # Evidence Status Classification
        if coverage_pct >= Decimal("80.0"):
            evidence_status = EvidenceStatus.VERIFIED
        elif coverage_pct >= Decimal("45.0"):
            evidence_status = EvidenceStatus.PROBABILISTIC
        else:
            evidence_status = EvidenceStatus.INSUFFICIENT

        return OpportunityScoreBreakdown(
            mint_address=asset_address,
            composite_score=composite,
            market_momentum_score=round(m_score, 1),
            security_safety_score=round(s_score, 1),
            wallet_intelligence_score=round(w_score, 1),
            narrative_sentiment_score=round(n_score, 1),
            hard_reject_triggered=hard_reject,
            rejection_reasons=rejection_reasons,
            confidence_coverage_pct=round(coverage_pct, 1),
            evidence_status=evidence_status
        )

multichain_scorer = MultiChainScorer()
