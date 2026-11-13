import uuid
from datetime import datetime
from sqlalchemy import (
    Column, String, Boolean, Integer, SmallInteger, BigInteger, Numeric,
    DateTime, ForeignKey, Text, UniqueConstraint
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from app.core.database import Base

class Asset(Base):
    __tablename__ = "assets"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    mint_address = Column(String, unique=True, nullable=False, index=True)
    token_program = Column(String, nullable=False, default="TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
    name = Column(String, nullable=True)
    symbol = Column(String, nullable=True)
    decimals = Column(SmallInteger, nullable=False, default=9)
    total_supply = Column(Numeric(40, 0), nullable=True)
    metadata_uri = Column(String, nullable=True)
    is_token_2022 = Column(Boolean, nullable=False, default=False)
    token_2022_extensions = Column(JSONB, default=list)
    first_seen_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    pools = relationship("Pool", back_populates="asset", cascade="all, delete-orphan")
    security_scans = relationship("SecurityScan", back_populates="asset", cascade="all, delete-orphan")

class Pool(Base):
    __tablename__ = "pools"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    asset_id = Column(UUID(as_uuid=True), ForeignKey("assets.id", ondelete="CASCADE"), nullable=False, index=True)
    pool_address = Column(String, unique=True, nullable=False, index=True)
    dex_program = Column(String, nullable=False)
    base_mint = Column(String, nullable=False)
    quote_mint = Column(String, nullable=False)
    created_slot = Column(BigInteger, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=True)
    lp_status = Column(String, default="unknown", nullable=False)
    lp_burn_pct = Column(Numeric(5, 2), default=0.00)
    lp_locked_pct = Column(Numeric(5, 2), default=0.00)
    lp_lock_expiry = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow)

    asset = relationship("Asset", back_populates="pools")
    snapshots = relationship("MarketSnapshot", back_populates="pool", cascade="all, delete-orphan")

class MarketSnapshot(Base):
    __tablename__ = "market_snapshots"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    asset_id = Column(UUID(as_uuid=True), ForeignKey("assets.id", ondelete="CASCADE"), nullable=False)
    pool_id = Column(UUID(as_uuid=True), ForeignKey("pools.id", ondelete="CASCADE"), nullable=True, index=True)
    captured_at = Column(DateTime(timezone=True), nullable=False)
    price_usd = Column(Numeric(30, 12), nullable=False)
    price_sol = Column(Numeric(30, 12), nullable=True)
    market_cap_usd = Column(Numeric(30, 4), nullable=True)
    fdv_usd = Column(Numeric(30, 4), nullable=True)
    liquidity_usd = Column(Numeric(30, 4), nullable=True)
    liquidity_sol = Column(Numeric(30, 4), nullable=True)
    volume_5m = Column(Numeric(30, 4), default=0)
    volume_1h = Column(Numeric(30, 4), default=0)
    volume_6h = Column(Numeric(30, 4), default=0)
    volume_24h = Column(Numeric(30, 4), default=0)
    price_change_5m = Column(Numeric(8, 4), nullable=True)
    price_change_1h = Column(Numeric(8, 4), nullable=True)
    price_change_24h = Column(Numeric(8, 4), nullable=True)
    buys_5m = Column(Integer, default=0)
    sells_5m = Column(Integer, default=0)
    buys_1h = Column(Integer, default=0)
    sells_1h = Column(Integer, default=0)
    source = Column(String, default="dexscreener", nullable=False)

    pool = relationship("Pool", back_populates="snapshots")

class SecurityScan(Base):
    __tablename__ = "security_scans"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    asset_id = Column(UUID(as_uuid=True), ForeignKey("assets.id", ondelete="CASCADE"), nullable=False, index=True)
    provider = Column(String, nullable=False)
    risk_score = Column(Numeric(5, 2), nullable=False)
    risk_level = Column(String, nullable=False)
    confidence = Column(String, nullable=False)
    mint_authority = Column(String, nullable=True)
    freeze_authority = Column(String, nullable=True)
    lp_burned = Column(Boolean, default=False)
    lp_locked = Column(Boolean, default=False)
    is_honeypot = Column(Boolean, default=False)
    transfer_fee_bps = Column(Integer, default=0)
    top10_holder_pct = Column(Numeric(5, 2), nullable=True)
    scanned_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    raw_report = Column(JSONB, nullable=False)

    asset = relationship("Asset", back_populates="security_scans")
    findings = relationship("SecurityFinding", back_populates="scan", cascade="all, delete-orphan")

class SecurityFinding(Base):
    __tablename__ = "security_findings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    scan_id = Column(UUID(as_uuid=True), ForeignKey("security_scans.id", ondelete="CASCADE"), nullable=False)
    finding_code = Column(String, nullable=False)
    severity = Column(String, nullable=False)
    description = Column(Text, nullable=False)
    evidence = Column(JSONB, nullable=True)

    scan = relationship("SecurityScan", back_populates="findings")

# Paper Trading Models
class PaperAccount(Base):
    __tablename__ = "paper_accounts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_name = Column(String, unique=True, nullable=False, default="Default Paper Desk")
    initial_virtual_sol = Column(Numeric(30, 9), nullable=False, default=100.0)
    current_virtual_sol = Column(Numeric(30, 9), nullable=False, default=100.0)
    allocated_virtual_sol = Column(Numeric(30, 9), nullable=False, default=0.0)
    realized_pnl_sol = Column(Numeric(30, 9), nullable=False, default=0.0)
    unrealized_pnl_sol = Column(Numeric(30, 9), nullable=False, default=0.0)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow)

    positions = relationship("PaperPosition", back_populates="account", cascade="all, delete-orphan")
    orders = relationship("PaperOrder", back_populates="account", cascade="all, delete-orphan")

class PaperPosition(Base):
    __tablename__ = "paper_positions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id = Column(UUID(as_uuid=True), ForeignKey("paper_accounts.id", ondelete="CASCADE"), nullable=False)
    asset_id = Column(UUID(as_uuid=True), ForeignKey("assets.id"), nullable=False)
    pool_id = Column(UUID(as_uuid=True), ForeignKey("pools.id"), nullable=False)
    status = Column(String, nullable=False, default="OPEN")
    token_amount_raw = Column(Numeric(40, 0), nullable=False)
    cost_basis_sol = Column(Numeric(30, 9), nullable=False)
    cost_basis_usd = Column(Numeric(30, 4), nullable=False)
    average_entry_price_sol = Column(Numeric(30, 12), nullable=False)
    current_market_price_sol = Column(Numeric(30, 12), nullable=False)
    unrealized_pnl_sol = Column(Numeric(30, 9), default=0.0)
    take_profit_price_sol = Column(Numeric(30, 12), nullable=True)
    stop_loss_price_sol = Column(Numeric(30, 12), nullable=True)
    opened_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    closed_at = Column(DateTime(timezone=True), nullable=True)

    account = relationship("PaperAccount", back_populates="positions")

class PaperOrder(Base):
    __tablename__ = "paper_orders"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id = Column(UUID(as_uuid=True), ForeignKey("paper_accounts.id", ondelete="CASCADE"), nullable=False)
    asset_id = Column(UUID(as_uuid=True), ForeignKey("assets.id"), nullable=False)
    pool_id = Column(UUID(as_uuid=True), ForeignKey("pools.id"), nullable=False)
    side = Column(String, nullable=False)
    order_type = Column(String, default="MARKET", nullable=False)
    input_amount_sol = Column(Numeric(30, 9), nullable=True)
    output_tokens_requested = Column(Numeric(40, 0), nullable=True)
    simulated_slippage_bps = Column(Integer, default=100, nullable=False)
    simulated_price_impact_bps = Column(Integer, default=150, nullable=False)
    status = Column(String, default="SUBMITTED", nullable=False)
    rejection_reason = Column(Text, nullable=True)
    snapshot_price_sol = Column(Numeric(30, 12), nullable=False)
    simulated_fill_price_sol = Column(Numeric(30, 12), nullable=True)
    simulated_fee_sol = Column(Numeric(30, 9), default=0.0005)
    submitted_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    filled_at = Column(DateTime(timezone=True), nullable=True)

    account = relationship("PaperAccount", back_populates="orders")
