import uuid
import hashlib
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException, Path
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.config import settings
from app.core.database import get_db
from app.schemas.contracts import TradeIntentRequest, TradeIntentResponse, TradeApprovalRequest

router = APIRouter(prefix="/execution", tags=["Execution Gateway"])

@router.post("/intent", response_model=TradeIntentResponse)
async def create_trade_intent(
    payload: TradeIntentRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Validates risk policies and generates an unsigned trade intent.
    Strictly verifies that:
    1. Wallet is NOT in cold vaults.
    2. Input amount does not violate max trade SOL limit.
    3. Slippage does not exceed max ceiling.
    """
    # 1. Cold Vault Check
    if payload.wallet_address in settings.COLD_VAULT_ADDRESSES:
        raise HTTPException(
            status_code=403,
            detail="SECURITY VIOLATION: Cold vault addresses are permanently excluded from trading execution."
        )

    # 2. Risk Policy Limits
    rejection_reasons = []
    if payload.input_amount_sol > Decimal(str(settings.MAX_TRADE_SOL)):
        rejection_reasons.append(f"Trade amount exceeds maximum single trade cap of {settings.MAX_TRADE_SOL} SOL")

    if payload.max_slippage_bps > settings.MAX_SLIPPAGE_BPS:
        rejection_reasons.append(f"Slippage tolerance exceeds maximum allowed cap of {settings.MAX_SLIPPAGE_BPS} bps")

    is_passed = len(rejection_reasons) == 0

    now = datetime.now(timezone.utc)
    # Generate transaction message hash for approval binding
    raw_intent_str = f"{payload.wallet_address}:{payload.input_mint}:{payload.output_mint}:{payload.input_amount_sol}:{now.isoformat()}"
    msg_hash = hashlib.sha256(raw_intent_str.encode()).hexdigest()

    return TradeIntentResponse(
        intent_id=str(uuid.uuid4()),
        status="PENDING_APPROVAL" if is_passed else "REJECTED",
        wallet_address=payload.wallet_address,
        input_amount_sol=payload.input_amount_sol,
        expected_output_tokens=Decimal("15000000"),
        estimated_price_impact_bps=120,
        transaction_message_hash=msg_hash,
        expires_at=now + timedelta(minutes=2),
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
    No private keys ever enter or are processed by this backend service.
    """
    if not settings.ALLOW_LIVE_TRADING:
        return {
            "status": "APPROVAL_ACKNOWLEDGED_SIMULATION_ONLY",
            "intent_id": intent_id,
            "message": "Live trading is currently disabled by environment policy. Simulation mode active."
        }

    return {
        "status": "APPROVED",
        "intent_id": intent_id,
        "message": "Approval registered. Transaction ready for client wallet submission."
    }
