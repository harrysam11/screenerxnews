from typing import Optional
from app.adapters.base import BaseChainAdapter, ChainBlockHeader, RawTransactionData
from app.adapters.solana import SolanaChainAdapter
from app.adapters.evm import EvmChainAdapter
from app.adapters.evm_l2 import (
    ArbitrumChainAdapter,
    OptimismChainAdapter,
    PolygonChainAdapter,
    AvalancheChainAdapter
)
from app.adapters.sui import SuiChainAdapter
from app.adapters.aptos import AptosChainAdapter
from app.adapters.ton import TonChainAdapter
from app.adapters.tron import TronChainAdapter

def get_adapter_for_chain(chain_id: str) -> BaseChainAdapter:
    """
    Factory function returning the specialized blockchain adapter for a given CAIP-2 chain_id.
    """
    if chain_id.startswith("solana"):
        return SolanaChainAdapter(chain_id=chain_id, rpc_urls=["https://api.mainnet-beta.solana.com"])
    elif chain_id == "eip155:42161":
        return ArbitrumChainAdapter()
    elif chain_id == "eip155:10":
        return OptimismChainAdapter()
    elif chain_id == "eip155:137":
        return PolygonChainAdapter()
    elif chain_id == "eip155:43114":
        return AvalancheChainAdapter()
    elif chain_id.startswith("eip155"):
        return EvmChainAdapter(chain_id=chain_id, rpc_urls=["https://eth.llamarpc.com"])
    elif chain_id.startswith("sui"):
        return SuiChainAdapter()
    elif chain_id.startswith("aptos"):
        return AptosChainAdapter()
    elif chain_id.startswith("ton"):
        return TonChainAdapter()
    elif chain_id.startswith("tron"):
        return TronChainAdapter()
    else:
        raise ValueError(f"Unsupported chain_id: {chain_id}")

__all__ = [
    "BaseChainAdapter",
    "ChainBlockHeader",
    "RawTransactionData",
    "SolanaChainAdapter",
    "EvmChainAdapter",
    "ArbitrumChainAdapter",
    "OptimismChainAdapter",
    "PolygonChainAdapter",
    "AvalancheChainAdapter",
    "SuiChainAdapter",
    "AptosChainAdapter",
    "TonChainAdapter",
    "TronChainAdapter",
    "get_adapter_for_chain"
]
