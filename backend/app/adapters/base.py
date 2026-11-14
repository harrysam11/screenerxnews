from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional
from decimal import Decimal
from datetime import datetime
from enum import Enum
from pydantic import BaseModel

class FinalityModel(str, Enum):
    DETERMINISTIC_INSTANT = "deterministic_instant"  # Sui, Aptos, Avalanche
    PROBABILISTIC_POS = "probabilistic_pos"          # Ethereum, Polygon, BNB, Solana
    L2_ROLLUP_BATCH = "l2_rollup_batch"              # Base, Arbitrum, Optimism
    STATE_MACHINE_EPOCHS = "state_machine_epochs"   # TON, TRON

class ChainCapabilities(BaseModel):
    chain_id: str
    name: str
    group_tier: str  # "A" (Immediate), "B" (Expansion EVM), "C" (Non-EVM)
    network_type: str  # "solana", "evm", "move", "tvm"
    native_token: str
    native_decimals: int
    token_standards: List[str]
    supports_token_2022: bool = False
    supports_l2_fees: bool = False
    supports_reorg_finality: bool = True
    finality_model: FinalityModel
    required_confirmations: int
    has_smart_contract_bytecode: bool = True
    supports_events_log_bloom: bool = True
    supports_simulation: bool = True

class RpcFailureException(Exception):
    """
    Raised when an RPC request fails, times out, or returns invalid data (CHAIN-03).
    Prevents silent fabrication of healthy or zeroed state.
    """
    def __init__(self, chain_id: str, message: str, raw_error: Optional[Any] = None):
        super().__init__(f"RPC FAILURE on chain [{chain_id}]: {message}")
        self.chain_id = chain_id
        self.message = message
        self.raw_error = raw_error

class ChainBlockHeader(BaseModel):
    chain_id: str
    block_number: int
    block_hash: str
    parent_hash: str
    block_time: datetime

class RawTransactionData(BaseModel):
    chain_id: str
    tx_hash: str
    slot_or_block: int
    block_time: datetime
    signer_address: str
    success: bool
    fee_in_native: Decimal
    raw_payload: Dict[str, Any]

class BaseChainAdapter(ABC):
    """
    Standard interface implemented by every blockchain adapter (Solana, EVM, Move, TON).
    """
    def __init__(self, chain_id: str, rpc_urls: List[str]):
        self.chain_id = chain_id
        self.rpc_urls = rpc_urls

    @abstractmethod
    def get_capabilities(self) -> ChainCapabilities:
        """Returns the formal capability contract for this network."""
        pass

    def get_finality_status(self, confirmations: int) -> Dict[str, Any]:
        """
        Determines finality status based on confirmation depth and finality model.
        """
        caps = self.get_capabilities()
        req = caps.required_confirmations
        is_finalized = confirmations >= req
        return {
            "chain_id": self.chain_id,
            "finality_model": caps.finality_model.value,
            "required_confirmations": req,
            "current_confirmations": confirmations,
            "is_finalized": is_finalized,
            "reorg_risk": "NONE" if is_finalized else ("LOW" if confirmations >= req // 2 else "HIGH")
        }

    @abstractmethod
    async def get_latest_block(self) -> ChainBlockHeader:
        """Returns the current block height and canonical block hash."""
        pass

    @abstractmethod
    async def get_token_metadata(self, asset_address: str) -> Dict[str, Any]:
        """Fetches symbol, name, decimals, and total supply."""
        pass

    @abstractmethod
    async def inspect_token_security(self, asset_address: str) -> Dict[str, Any]:
        """
        Extracts chain-specific security findings:
        - Solana: mint/freeze authority, Token-2022 extensions
        - EVM: owner, proxy implementation, transfer taxes, honeypot simulation
        """
        pass

    @abstractmethod
    async def get_initial_buyer_transactions(
        self, pool_address: str, asset_address: str, limit: int = 25
    ) -> List[RawTransactionData]:
        """Reconstructs the earliest pool swaps for first-buyer forensics."""
        pass
