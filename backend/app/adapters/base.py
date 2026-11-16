from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional
from decimal import Decimal
from datetime import datetime
from pydantic import BaseModel, Field

# =============================================================================
# Typed Adapter Exceptions (P10.2-D Correctness)
# =============================================================================

class ChainAdapterError(Exception):
    """Base exception for all chain adapter failures."""
    def __init__(self, message: str, chain_id: Optional[str] = None, error_code: str = "ADAPTER_ERROR"):
        super().__init__(message)
        self.message = message
        self.chain_id = chain_id
        self.error_code = error_code

class RpcError(ChainAdapterError):
    """Raised when an upstream JSON-RPC / REST call returns an explicit error or fails."""
    def __init__(self, message: str, chain_id: Optional[str] = None, status_code: Optional[int] = None):
        super().__init__(message, chain_id=chain_id, error_code="RPC_ERROR")
        self.status_code = status_code

class DataUnavailableError(ChainAdapterError):
    """Raised when on-chain data is not found or cannot be retrieved without fabricating defaults."""
    def __init__(self, message: str, chain_id: Optional[str] = None):
        super().__init__(message, chain_id=chain_id, error_code="DATA_UNAVAILABLE")

class UnsupportedCapabilityError(ChainAdapterError):
    """Raised when an operation is not supported by the underlying blockchain protocol or adapter."""
    def __init__(self, message: str, chain_id: Optional[str] = None, capability: Optional[str] = None):
        super().__init__(message, chain_id=chain_id, error_code="UNSUPPORTED_CAPABILITY")
        self.capability = capability

class ChainMismatchError(ChainAdapterError):
    """Raised when the connected RPC node's on-chain network ID does not match expected chain_id."""
    def __init__(self, expected_chain_id: str, actual_chain_id: str):
        super().__init__(
            f"Chain ID mismatch: expected {expected_chain_id}, but RPC node reports {actual_chain_id}",
            chain_id=expected_chain_id,
            error_code="CHAIN_MISMATCH"
        )
        self.expected_chain_id = expected_chain_id
        self.actual_chain_id = actual_chain_id

# =============================================================================
# Core Data Models
# =============================================================================

class ChainBlockHeader(BaseModel):
    chain_id: str
    block_number: int
    block_hash: str
    parent_hash: str
    block_time: datetime
    finalized: bool = True

class RawTransactionData(BaseModel):
    chain_id: str
    tx_hash: str
    slot_or_block: int
    block_time: datetime
    signer_address: str
    success: bool
    fee_in_native: Optional[Decimal] = None
    raw_payload: Dict[str, Any] = Field(default_factory=dict)

class BaseChainAdapter(ABC):
    """
    Standard interface implemented by every blockchain adapter (Solana, EVM, Move, TON).
    Guarantees no fabricated responses: when data is missing or unsupported, raises typed exceptions.
    """
    def __init__(self, chain_id: str, rpc_urls: List[str]):
        self.chain_id = chain_id
        self.rpc_urls = rpc_urls

    @abstractmethod
    async def get_latest_block(self) -> ChainBlockHeader:
        """Returns the verified current block height, block hash, and on-chain timestamp."""
        pass

    @abstractmethod
    async def get_token_metadata(self, asset_address: str) -> Dict[str, Any]:
        """Fetches symbol, name, decimals, and total supply from verified on-chain data."""
        pass

    @abstractmethod
    async def inspect_token_security(self, asset_address: str) -> Dict[str, Any]:
        """
        Extracts chain-specific security findings:
        - Solana: mint/freeze authority, Token-2022 extensions
        - EVM: owner, proxy implementation, transfer restrictions
        - Move: MintCapability / FreezeCapability / TreasuryCap
        - TVM: Jetton mintable, admin address, TRC-20 owner
        """
        pass

    @abstractmethod
    async def get_initial_buyer_transactions(
        self, pool_address: str, asset_address: str, limit: int = 25
    ) -> List[RawTransactionData]:
        """Reconstructs the earliest pool swaps for first-buyer forensics."""
        pass
