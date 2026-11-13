import uuid
import hashlib
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.config import settings
from app.core.database import get_db
from app.schemas.contracts import TradeIntentRequest, TradeIntentResponse, TradeApprovalRequest

router = APIRouter(prefix="/execution", tags=["Execution Gateway"])

# In-memory intent state registry with TTL and replay protection
_INTENTS_REGISTRY: dict[str, dict] = {}

@router.post("/intent", response_model=TradeIntentResponse)
async def create_trade_intent(
    payload: TradeIntentRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Validates risk policies and generates an unsigned trade intent with canonical digest.
    """
    # 1. Cold Vault Check (Hard Deny)
    if payload.wallet_address in settings.COLD_VAULT_ADDRESSES:
        raise HTTPException(
            status_code=403,
            detail="SECURITY VIOLATION: Cold vault addresses are permanently excluded from trading execution."
        )

    # 2. Risk Policy Limits (Raw lamports / token units)
    rejection_reasons = []
    # 0.5 SOL max in lamports = 500,000,000 lamports
    max_trade_lamports = Decimal(str(int(settings.MAX_TRADE_SOL * 1_000_000_000)))
    if payload.input_amount_raw > max_trade_lamports:
        rejection_reasons.append(f"Trade amount exceeds maximum single trade cap of {settings.MAX_TRADE_SOL} SOL")

    if payload.max_slippage_bps > settings.MAX_SLIPPAGE_BPS:
        rejection_reasons.append(f"Slippage tolerance exceeds maximum allowed cap of {settings.MAX_SLIPPAGE_BPS} bps")

    is_passed = len(rejection_reasons) == 0

    now = datetime.now(timezone.utc)
    # Canonical intent digest
    raw_intent_str = f"{payload.wallet_address}:{payload.input_mint}:{payload.output_mint}:{payload.input_amount_raw}:{payload.max_slippage_bps}:{now.isoformat()}"
    canonical_digest = hashlib.sha256(raw_intent_str.encode()).hexdigest()
    msg_hash = hashlib.sha256(canonical_digest.encode()).hexdigest()

    intent_id = str(uuid.uuid4())
    expires_at = now + timedelta(minutes=2)

    # Register in intent tracking registry
    _INTENTS_REGISTRY[intent_id] = {
        "intent_id": intent_id,
        "wallet_address": payload.wallet_address,
        "transaction_message_hash": msg_hash,
        "canonical_intent_digest": canonical_digest,
        "expires_at": expires_at,
        "is_consumed": False,
        "policy_checks_passed": is_passed
    }

    return TradeIntentResponse(
        intent_id=intent_id,
        status="PENDING_APPROVAL" if is_passed else "REJECTED",
        wallet_address=payload.wallet_address,
        input_amount_raw=payload.input_amount_raw,
        minimum_output_raw=payload.input_amount_raw * Decimal("1500"),
        estimated_price_impact_bps=120,
        canonical_intent_digest=canonical_digest,
        transaction_message_hash=msg_hash,
        expires_at=expires_at,
        policy_checks_passed=is_passed,
        rejection_reasons=rejection_reasons
    )

@router.post("/approve/{intent_id}")
async def approve_trade_intent(
    intent_id: str,
    payload: TradeApprovalRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Registers client-side signature approval hash.
    Enforces existence, TTL expiry, canonical digest verification, and one-time consumption (replay defense).
    No private keys ever enter or are processed by this backend service.
    """
    intent = _INTENTS_REGISTRY.get(intent_id)
    if not intent:
        raise HTTPException(
            status_code=404,
            detail="SECURITY VIOLATION: Trade intent not found or never issued."
        )

    # Check TTL expiration
    if datetime.now(timezone.utc) > intent["expires_at"]:
        raise HTTPException(
            status_code=410,
            detail="SECURITY VIOLATION: Trade intent has expired. Re-approval rejected."
        )

    # Check replay attack (one-time consumption)
    if intent["is_consumed"]:
        raise HTTPException(
            status_code=409,
            detail="SECURITY VIOLATION: Trade approval already consumed (Replay attack prevented)."
        )

    # Check transaction message hash match
    if payload.transaction_message_hash != intent["transaction_message_hash"]:
        raise HTTPException(
            status_code=400,
            detail="SECURITY VIOLATION: Transaction message hash does not match canonical intent digest."
        )

    # Check Cold Vault blacklist
    if payload.signed_by_address in settings.COLD_VAULT_ADDRESSES:
        raise HTTPException(
            status_code=403,
            detail="SECURITY VIOLATION: Cold vault signatures are permanently forbidden."
        )

    # Check Burner Wallet whitelist if configured
    if settings.BURNER_WALLET_ADDRESSES and payload.signed_by_address not in settings.BURNER_WALLET_ADDRESSES:
        raise HTTPException(
            status_code=403,
            detail="SECURITY VIOLATION: Signer is not an authorized burner wallet address."
        )

    # Mark consumed atomically
    intent["is_consumed"] = True

    if not settings.ALLOW_LIVE_TRADING:
        return {
            "status": "APPROVAL_ACKNOWLEDGED_SIMULATION_ONLY",
            "intent_id": intent_id,
            "message": "Live trading is currently disabled by environment policy. Simulation mode active."
        }

    return {
        "status": "APPROVED",
        "intent_id": intent_id,
        "is_consumed": True,
        "message": "Approval registered. Transaction ready for client wallet submission."
    }
