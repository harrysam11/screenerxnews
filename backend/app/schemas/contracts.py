from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Dict, List, Optional, Any
from pydantic import BaseModel, Field

class TokenProgramEnum(str, Enum):
    SPL_TOKEN = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
    TOKEN_2022 = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"

class RiskBandEnum(str, Enum):
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    CRITICAL = "critical"

# Discovery Schemas
class TokenDiscoveryResponse(BaseModel):
    mint_address: str
    symbol: Optional[str] = None
    name: Optional[str] = None
    price_usd: Decimal
    liquidity_usd: Decimal
    volume_5m: Decimal
    volume_1h: Decimal
    volume_24h: Decimal
    price_change_5m: Optional[Decimal] = None
    price_change_1h: Optional[Decimal] = None
    buy_sell_ratio_1h: Decimal
    risk_score: Decimal
    risk_band: RiskBandEnum
    composite_opportunity_score: Decimal
    lp_burned: bool
    mint_revoked: bool
    is_token_2022: bool

class CandidateRegistrationRequest(BaseModel):
    mint_address: str
    symbol: Optional[str] = None
    name: Optional[str] = None
    token_program: str = TokenProgramEnum.SPL_TOKEN.value
    metadata_uri: Optional[str] = None
    pool_address: Optional[str] = None
    dex_program: Optional[str] = None
    quote_mint: Optional[str] = None

# Forensics Schemas
class FindingItem(BaseModel):
    code: str
    severity: str
    description: str
    evidence: Optional[Dict[str, Any]] = None

class TokenSecurityReport(BaseModel):
    mint_address: str
    token_program: str
    is_token_2022: bool
    mint_authority: Optional[str] = None
    freeze_authority: Optional[str] = None
    is_honeypot: bool
    transfer_fee_bps: int
    lp_status: str
    lp_burned_pct: Decimal
    top10_concentration_pct: Decimal
    risk_score: Decimal
    confidence: str
    findings: List[FindingItem] = []
    scanned_at: datetime

class FirstBuyerItem(BaseModel):
    buy_rank: int
    wallet_address: str
    sol_spent: Decimal
    tokens_bought: Decimal
    holding_pct: Decimal
    current_status: str
    funder_address: Optional[str] = None
    linked_to_deployer: bool = False
    same_slot_cluster: bool = False

class FirstBuyersForensicsResponse(BaseModel):
    mint_address: str
    pool_address: str
    deployer_address: Optional[str] = None
    total_first_buyers_held_pct: Decimal
    bundler_cluster_detected: bool
    buyers: List[FirstBuyerItem]

# News & Narrative Schemas
class NewsFeedItem(BaseModel):
    id: str
    title: str
    url: str
    source_name: str
    published_at: datetime
    category: str
    sentiment_score: Decimal
    verification_status: str
    affected_tokens: List[str] = []
    affected_sectors: List[str] = []

# Opportunity Scoring
class OpportunityScoreBreakdown(BaseModel):
    mint_address: str
    composite_score: Decimal
    market_momentum_score: Decimal
    security_safety_score: Decimal
    wallet_intelligence_score: Decimal
    narrative_sentiment_score: Decimal
    hard_reject_triggered: bool
    rejection_reasons: List[str] = []
    confidence_coverage_pct: Decimal

# Paper Trading Schemas
class PaperOrderRequest(BaseModel):
    asset_id: str
    pool_id: str
    side: str = Field(..., pattern="^(BUY|SELL)$")
    amount_sol: Decimal = Field(..., gt=0, le=10)
    max_slippage_bps: int = Field(default=150, ge=10, le=2000)

class PaperOrderResponse(BaseModel):
    order_id: str
    status: str
    side: str
    amount_sol: Decimal
    fill_price_sol: Decimal
    tokens_received: Decimal
    simulated_slippage_bps: int
    simulated_fee_sol: Decimal
    filled_at: datetime

class PaperPositionResponse(BaseModel):
    position_id: str
    mint_address: str
    symbol: Optional[str] = None
    token_amount: Decimal
    cost_basis_sol: Decimal
    current_value_sol: Decimal
    unrealized_pnl_sol: Decimal
    unrealized_pnl_pct: Decimal
    average_entry_price_sol: Decimal
    current_market_price_sol: Decimal
    opened_at: datetime

class PaperAccountResponse(BaseModel):
    account_id: str
    account_name: str
    virtual_sol_balance: Decimal
    allocated_sol: Decimal
    total_equity_sol: Decimal
    realized_pnl_sol: Decimal
    unrealized_pnl_sol: Decimal
    win_rate: Decimal
    profit_factor: Decimal

# Trade Approval Gateway Schemas
class TradeIntentRequest(BaseModel):
    asset_id: str
    pool_id: str
    wallet_address: str
    side: str = Field(..., pattern="^(BUY|SELL)$")
    input_mint: str
    output_mint: str
    input_amount_sol: Decimal
    max_slippage_bps: int = Field(default=150, ge=10, le=500)

class TradeIntentResponse(BaseModel):
    intent_id: str
    status: str
    wallet_address: str
    input_amount_sol: Decimal
    expected_output_tokens: Decimal
    estimated_price_impact_bps: int
    transaction_message_hash: str
    expires_at: datetime
    policy_checks_passed: bool
    rejection_reasons: List[str] = []

class TradeApprovalRequest(BaseModel):
    transaction_message_hash: str
    signed_by_address: str
