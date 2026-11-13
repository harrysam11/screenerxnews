import uuid
import hashlib
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.config import settings
from app.core.database import get_db
from app.schemas.contracts import (
    TradeIntentRequest,
    TradeIntentResponse,
    TradeApprovalRequest,
    IntentState
)
from app.services.execution_store import execution_intent_store, IntentLifecycleState

router = APIRouter(prefix="/execution", tags=["Execution Gateway"])

@router.post("/intent", response_model=TradeIntentResponse)
async def create_trade_intent(
    payload: TradeIntentRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Validates risk policies and generates an unsigned trade intent with canonical digest.
    Persists intent state in the durable execution store.
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
    # Canonical intent digest binds exact parameters
    raw_intent_str = (
        f"{payload.chain_id}:{payload.wallet_address}:{payload.input_mint}:{payload.output_mint}:"
        f"{payload.input_amount_raw}:{payload.max_slippage_bps}:{payload.destination_router or ''}:{now.isoformat()}"
    )
    canonical_digest = hashlib.sha256(raw_intent_str.encode()).hexdigest()
    msg_hash = hashlib.sha256(canonical_digest.encode()).hexdigest()

    intent_id = str(uuid.uuid4())
    expires_at = now + timedelta(minutes=2)
    lifecycle_status = IntentLifecycleState.AWAITING_APPROVAL if is_passed else IntentLifecycleState.POLICY_REJECTED

    intent_record = {
        "intent_id": intent_id,
        "chain_id": payload.chain_id,
        "wallet_address": payload.wallet_address,
        "asset_id": payload.asset_id,
        "pool_id": payload.pool_id,
        "side": payload.side,
        "input_mint": payload.input_mint,
        "output_mint": payload.output_mint,
        "input_amount_raw": payload.input_amount_raw,
        "max_slippage_bps": payload.max_slippage_bps,
        "destination_router": payload.destination_router or "default",
        "strategy_id": payload.strategy_id or "direct",
        "strategy_version": payload.strategy_version or "1.0",
        "nonce": payload.nonce or str(uuid.uuid4()),
        "canonical_intent_digest": canonical_digest,
        "transaction_message_hash": msg_hash,
        "status": lifecycle_status,
        "policy_checks_passed": is_passed,
        "rejection_reasons": rejection_reasons,
        "created_at": now,
        "expires_at": expires_at,
        "is_consumed": False
    }

    # Persist in durable SQLite intent store
    execution_intent_store.save_intent(intent_record)

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
    Enforces existence, TTL expiry, exact digest verification, wallet binding, and atomic consumption.
    No private keys ever enter or are processed by this backend service.
    """
    # Atomic database approval with zero-trust checks
    result = execution_intent_store.atomic_approve_intent(
        intent_id=intent_id,
        signed_by_address=payload.signed_by_address,
        transaction_message_hash=payload.transaction_message_hash,
        signature_hex=payload.signature
    )

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
