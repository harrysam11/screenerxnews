from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional
from decimal import Decimal
from datetime import datetime
from pydantic import BaseModel

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
