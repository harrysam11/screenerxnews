from typing import Dict, Any, Optional, List
from decimal import Decimal
from datetime import datetime, timezone
from app.schemas.contracts import (
    TokenSecurityReport, FindingItem, TriState, EvidenceStatus
)

class SecurityNormalizer:
    """
    Normalizes multi-provider security inputs (GoPlus, RugCheck, Solana RPC)
    into a standardized TokenSecurityReport with tri-state evidence and confidence levels.
    """
    @staticmethod
    def normalize(
        mint_address: str,
        rpc_mint_info: Optional[Dict[str, Any]],
        goplus_report: Optional[Dict[str, Any]],
        rugcheck_report: Optional[Dict[str, Any]],
        top_holders: Optional[List[Dict[str, Any]]] = None
    ) -> TokenSecurityReport:
        findings: List[FindingItem] = []
        now = datetime.now(timezone.utc)

        # 1. Native RPC Authority Verification
        if rpc_mint_info:
            is_token_2022 = TriState.TRUE if rpc_mint_info.get("is_token_2022") else TriState.FALSE
            mint_auth = rpc_mint_info.get("mint_authority")
            freeze_auth = rpc_mint_info.get("freeze_authority")
            is_mint_revoked = TriState.TRUE if mint_auth is None else TriState.FALSE
            is_freeze_revoked = TriState.TRUE if freeze_auth is None else TriState.FALSE

            if is_mint_revoked == TriState.FALSE:
                findings.append(FindingItem(
                    code="ACTIVE_MINT_AUTHORITY",
                    severity="CRITICAL_FAIL",
                    description=f"Token has active mint authority: {mint_auth}"
                ))
            if is_freeze_revoked == TriState.FALSE:
                findings.append(FindingItem(
                    code="ACTIVE_FREEZE_AUTHORITY",
                    severity="CRITICAL_FAIL",
                    description=f"Token has active freeze authority: {freeze_auth}"
                ))

            # Inspect Token-2022 Extensions
            extensions = rpc_mint_info.get("extensions", [])
            for ext in extensions:
                ext_type = ext.get("extension")
                if ext_type in ["permanentDelegate", "transferHook"]:
                    findings.append(FindingItem(
                        code=f"TOKEN_2022_{ext_type.upper()}",
                        severity="CRITICAL_FAIL",
                        description=f"High-risk Token-2022 extension detected: {ext_type}"
                    ))
        else:
            is_token_2022 = TriState.UNKNOWN
            is_mint_revoked = TriState.UNKNOWN
            is_freeze_revoked = TriState.UNKNOWN

        # 2. GoPlus & RugCheck Liquidity and Honeypot Analysis
        lp_burned = TriState.UNKNOWN
        lp_burned_pct = None
        is_honeypot = TriState.UNKNOWN
        transfer_fee_bps = None

        if goplus_report:
            if "transfer_fee" in goplus_report:
                try:
                    transfer_fee_bps = int(float(goplus_report["transfer_fee"]) * 100)
                    if transfer_fee_bps > 500: # > 5% tax
                        findings.append(FindingItem(
                            code="HIGH_TRANSFER_TAX",
                            severity="CRITICAL_FAIL",
                            description=f"Excessive transfer tax configured: {transfer_fee_bps / 100}%"
                        ))
                except (ValueError, TypeError):
                    pass

        if rugcheck_report:
            markets = rugcheck_report.get("markets", [])
            if markets and isinstance(markets, list):
                first_market = markets[0]
                lp_info = first_market.get("lp", {})
                lp_burned_pct_val = lp_info.get("lpBurnedPct")
                if lp_burned_pct_val is not None:
                    lp_burned_pct = Decimal(str(lp_burned_pct_val))
                    lp_burned = TriState.TRUE if lp_burned_pct >= 95 else TriState.FALSE

            # Add RugCheck identified risks
            for r in rugcheck_report.get("risks", []):
                findings.append(FindingItem(
                    code=r.get("name", "RUGCHECK_RISK"),
                    severity="WARNING" if r.get("level") == "warn" else "CRITICAL_FAIL",
                    description=r.get("description", "")
                ))

        # 3. Holder Concentration Calculation
        top10_concentration_pct = None
        holder_coverage_pct = None
        if top_holders and len(top_holders) > 0:
            total_supply = rpc_mint_info.get("supply") if rpc_mint_info else None
            try:
                top10_amount = sum(int(acc.get("amount", 0)) for acc in top_holders[:10])
                if total_supply and int(total_supply) > 0:
                    top10_concentration_pct = Decimal(str(round((top10_amount / int(total_supply)) * 100, 2)))
                    total_inspected_amount = sum(int(acc.get("amount", 0)) for acc in top_holders)
                    holder_coverage_pct = Decimal(str(round((total_inspected_amount / int(total_supply)) * 100, 2)))
            except (ValueError, TypeError, ZeroDivisionError):
                pass

        # 4. Confidence & Risk Score Synthesis
        critical_count = sum(1 for f in findings if f.severity == "CRITICAL_FAIL")
        warning_count = sum(1 for f in findings if f.severity == "WARNING")

        if rpc_mint_info is None and goplus_report is None:
            confidence = EvidenceStatus.INSUFFICIENT
            risk_score = None
            risk_level = "UNKNOWN"
        else:
            confidence = EvidenceStatus.VERIFIED if rpc_mint_info is not None else EvidenceStatus.PROBABILISTIC
            if critical_count > 0:
                risk_score = Decimal("85.00") + Decimal(str(min(critical_count * 5, 15)))
                risk_level = "CRITICAL"
            elif warning_count > 0:
                risk_score = Decimal("40.00") + Decimal(str(min(warning_count * 10, 35)))
                risk_level = "HIGH"
            else:
                risk_score = Decimal("12.50")
                risk_level = "LOW"

        return TokenSecurityReport(
            mint_address=mint_address,
            token_program=rpc_mint_info.get("owner_program") if rpc_mint_info else None,
            is_token_2022=is_token_2022,
            is_mint_revoked=is_mint_revoked,
            is_freeze_revoked=is_freeze_revoked,
            is_honeypot=is_honeypot,
            transfer_fee_bps=transfer_fee_bps,
            lp_status="burned" if lp_burned == TriState.TRUE else "unverified",
            lp_burned=lp_burned,
            lp_burned_pct=lp_burned_pct,
            top10_concentration_pct=top10_concentration_pct,
            holder_coverage_pct=holder_coverage_pct,
            risk_score=risk_score,
            risk_level=risk_level,
            confidence=confidence,
            findings=findings,
            scanned_at=now,
            is_stale=False
        )

security_normalizer = SecurityNormalizer()
