from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Dict, List, Optional, Any
from pydantic import BaseModel, Field

# Primitives & Tri-State Enums (AUD-10)
class TriState(str, Enum):
    TRUE = "true"
    FALSE = "false"
    UNKNOWN = "unknown"

class Environment(str, Enum):
    RESEARCH = "RESEARCH"
    PAPER = "PAPER"
    LIVE = "LIVE"

class DataFreshness(str, Enum):
    REALTIME = "REALTIME"      # < 30s
    ACCEPTABLE = "ACCEPTABLE"  # 30s - 120s
    STALE = "STALE"            # > 120s
    DEGRADED = "DEGRADED"

class EvidenceStatus(str, Enum):
    VERIFIED = "VERIFIED"
    PROBABILISTIC = "PROBABILISTIC"
    INSUFFICIENT = "INSUFFICIENT"
    DISPUTED = "DISPUTED"
    UNVERIFIED = "UNVERIFIED"
    LIVE_SCAN_REQUIRED = "LIVE_SCAN_REQUIRED"

class RiskBandEnum(str, Enum):
    LOW = "LOW"
    MODERATE = "MODERATE"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"
    UNKNOWN = "UNKNOWN"

class DecimalProvenanceSource(str, Enum):
    AUTHENTICATED_RPC_EVIDENCE = "AUTHENTICATED_RPC_EVIDENCE"
    TRUSTED_PROVIDER_EVIDENCE = "TRUSTED_PROVIDER_EVIDENCE"
    ONCHAIN_RPC_MINT_DATA = "ONCHAIN_RPC_MINT_DATA"
    ONCHAIN_RPC_STATIC_CALL = "ONCHAIN_RPC_STATIC_CALL"
    RPC_NODE = "RPC_NODE"
    OFFICIAL_REGISTRY = "OFFICIAL_REGISTRY"
    OFFICIAL_SPL_MINT = "OFFICIAL_SPL_MINT"
    OFFICIAL_EIP20_REGISTRY = "OFFICIAL_EIP20_REGISTRY"
    CORE_TEST = "CORE_TEST"
    CONTROLLED_TEST_FIXTURE = "CONTROLLED_TEST_FIXTURE"
    SYNTHETIC_TEST_FIXTURE = "SYNTHETIC_TEST_FIXTURE"
    STATIC_SEED = "STATIC_SEED"
    SEED_REGISTRY = "SEED_REGISTRY"
    DISCOVERY_METADATA = "DISCOVERY_METADATA"
    UNVERIFIED = "UNVERIFIED"

AUTHORITATIVE_DECIMAL_PROVENANCE_SOURCES = {
    DecimalProvenanceSource.AUTHENTICATED_RPC_EVIDENCE.value,
    DecimalProvenanceSource.TRUSTED_PROVIDER_EVIDENCE.value,
    DecimalProvenanceSource.ONCHAIN_RPC_MINT_DATA.value,
    DecimalProvenanceSource.ONCHAIN_RPC_STATIC_CALL.value,
    DecimalProvenanceSource.RPC_NODE.value,
    DecimalProvenanceSource.OFFICIAL_REGISTRY.value,
    DecimalProvenanceSource.OFFICIAL_SPL_MINT.value,
    DecimalProvenanceSource.OFFICIAL_EIP20_REGISTRY.value,
    DecimalProvenanceSource.CORE_TEST.value,
}

UNAUTHORIZED_DECIMAL_PROVENANCE_SOURCES = {
    DecimalProvenanceSource.CONTROLLED_TEST_FIXTURE.value,
    DecimalProvenanceSource.SYNTHETIC_TEST_FIXTURE.value,
    DecimalProvenanceSource.STATIC_SEED.value,
    DecimalProvenanceSource.SEED_REGISTRY.value,
    DecimalProvenanceSource.DISCOVERY_METADATA.value,
    DecimalProvenanceSource.UNVERIFIED.value,
}

# Discovery Schemas
class TokenDiscoveryResponse(BaseModel):
    mint_address: str
    symbol: Optional[str] = None
    name: Optional[str] = None
    decimals: Optional[int] = None
    price_usd: Decimal
    liquidity_usd: Decimal
    volume_5m: Decimal
    volume_1h: Decimal
    volume_24h: Decimal
    price_change_5m: Optional[Decimal] = None
    price_change_1h: Optional[Decimal] = None
    buy_sell_ratio_1h: Optional[Decimal] = None
    risk_score: Optional[Decimal] = None
    risk_band: RiskBandEnum
    composite_opportunity_score: Optional[Decimal] = None
    lp_burned: TriState
    mint_revoked: TriState
    is_token_2022: TriState
    data_freshness: DataFreshness = DataFreshness.REALTIME

class CandidateRegistrationRequest(BaseModel):
    mint_address: str
    symbol: Optional[str] = None
    name: Optional[str] = None
    token_program: Optional[str] = None
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
    token_program: Optional[str] = None
    is_token_2022: TriState
    is_mint_revoked: TriState
    is_freeze_revoked: TriState
    is_honeypot: TriState
    transfer_fee_bps: Optional[int] = None
    lp_status: str
    lp_burned: TriState
    lp_burned_pct: Optional[Decimal] = None
    top10_concentration_pct: Optional[Decimal] = None
    holder_coverage_pct: Optional[Decimal] = None
    risk_score: Optional[Decimal] = None
    risk_level: str
    confidence: EvidenceStatus
    findings: List[FindingItem] = []
    scanned_at: datetime
    is_stale: bool = False

class FirstBuyerItem(BaseModel):
    buy_rank: int
    wallet_address: str
    sol_spent_lamports: int
    tokens_bought_raw: Decimal
    holding_pct: Decimal
    current_status: str
    funder_address: Optional[str] = None
    is_funder_shared: bool = False
    is_same_slot_as_pool: bool = False

class FirstBuyersForensicsResponse(BaseModel):
    mint_address: str
    pool_address: Optional[str] = None
    deployer_address: Optional[str] = None
    total_first_buyers_held_pct: Optional[Decimal] = None
    bundler_cluster_detected: Optional[bool] = None
    evidence_status: EvidenceStatus
    detector_version: str = "v1"
    buyers: List[FirstBuyerItem] = []

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
    composite_score: Optional[Decimal] = None
    market_momentum_score: Decimal
    security_safety_score: Decimal
    wallet_intelligence_score: Decimal
    narrative_sentiment_score: Decimal
    hard_reject_triggered: bool
    rejection_reasons: List[str] = []
    confidence_coverage_pct: Decimal
    evidence_status: EvidenceStatus

# Paper Trading Schemas (AUD-01, AUD-04 & Multi-Chain P7)
class PaperOrderRequest(BaseModel):
    account_id: str
    client_order_id: str = Field(..., description="Unique idempotency key")
    asset_id: str
    pool_id: str
    side: str = Field(..., pattern="^(BUY|SELL)$")
    input_mint: str = Field(..., description="Mint of token being sold")
    output_mint: str = Field(..., description="Mint of token being bought")
    input_amount_raw: Decimal = Field(..., gt=0, description="Exact raw units spent")
    max_slippage_bps: int = Field(default=150, ge=10, le=2000)
    chain_id: str = Field(default="solana:mainnet", description="CAIP-2 Chain Identifier")
    origin: Optional[str] = Field(default="MANUAL", description="Origin: MANUAL or STRATEGY_PROPOSAL")
    proposal_id: Optional[str] = Field(default=None, description="Optional proposal_id for strategy orders")

class PaperExecutionEventDTO(BaseModel):
    event_id: str
    event_type: str
    input_amount_raw: Decimal
    output_amount_raw: Decimal
    effective_price_sol: Decimal = Decimal("0")
    effective_price_native: Optional[Decimal] = None
    liquidity_impact_bps: int
    simulated_slippage_bps: int
    simulated_total_fee_lamports: int = 0
    simulated_total_fee_native: Optional[Decimal] = None
    occurred_at: datetime
    chain_id: str = "solana:mainnet"

class PaperOrderResponse(BaseModel):
    order_id: str
    client_order_id: str
    status: str
    side: str
    input_amount_raw: Decimal
    output_amount_raw: Optional[Decimal] = None
    effective_price_sol: Optional[Decimal] = None
    effective_price_native: Optional[Decimal] = None
    chain_id: str = "solana:mainnet"
    rejection_reason: Optional[str] = None
    execution_events: List[PaperExecutionEventDTO] = []
    created_at: datetime

class PaperPositionResponse(BaseModel):
    position_id: str
    mint_address: str
    asset_address: Optional[str] = None
    chain_id: str = "solana:mainnet"
    symbol: Optional[str] = None
    token_amount_raw: Decimal
    cost_basis_lamports: int = 0
    cost_basis_native: Optional[Decimal] = None
    current_market_price_sol: Decimal = Decimal("0")
    current_market_price_native: Optional[Decimal] = None
    unrealized_pnl_lamports: int = 0
    unrealized_pnl_native: Optional[Decimal] = None
    opened_at: datetime

class PaperAccountResponse(BaseModel):
    account_id: str
    account_name: str
    chain_id: str = "solana:mainnet"
    native_symbol: str = "SOL"
    current_virtual_lamports: int = 0
    allocated_virtual_lamports: int = 0
    total_equity_lamports: int = 0
    realized_pnl_lamports: int = 0
    unrealized_pnl_lamports: int = 0
    current_virtual_balance: Optional[Decimal] = None
    allocated_virtual_balance: Optional[Decimal] = None
    total_equity_native: Optional[Decimal] = None
    realized_pnl_native: Optional[Decimal] = None
    unrealized_pnl_native: Optional[Decimal] = None
    win_rate: Decimal
    profit_factor: Decimal

class IntentState(str, Enum):
    CREATED = "CREATED"
    POLICY_REJECTED = "POLICY_REJECTED"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    APPROVED = "APPROVED"
    SIGNED = "SIGNED"
    BROADCAST = "BROADCAST"
    CONFIRMED = "CONFIRMED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"

# Trade Approval Gateway Schemas (AUD-03 & AUD-05)
class TradeIntentRequest(BaseModel):
    asset_id: str
    pool_id: str
    wallet_address: str
    side: str = Field(..., pattern="^(BUY|SELL)$")
    input_mint: str
    output_mint: str
    input_amount_raw: Decimal
    max_slippage_bps: int = Field(default=150, ge=10, le=500)
    chain_id: str = Field(default="solana:mainnet", description="CAIP-2 Chain Identifier")
    destination_router: Optional[str] = None
    strategy_id: Optional[str] = None
    strategy_version: Optional[str] = None
    nonce: Optional[str] = None

class TradeIntentResponse(BaseModel):
    intent_id: str
    status: str
    wallet_address: str
    input_amount_raw: Decimal
    minimum_output_raw: Decimal
    estimated_price_impact_bps: int
    canonical_intent_digest: str
    transaction_message_hash: str
    expires_at: datetime
    policy_checks_passed: bool
    rejection_reasons: List[str] = []

class TradeApprovalRequest(BaseModel):
    transaction_message_hash: str
    signed_by_address: str
    signature: Optional[str] = None
    nonce: Optional[str] = None

