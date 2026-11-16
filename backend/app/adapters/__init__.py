from typing import Optional
from app.adapters.base import (
    BaseChainAdapter,
    ChainBlockHeader,
    RawTransactionData,
    ChainAdapterError,
    RpcError,
    DataUnavailableError,
    UnsupportedCapabilityError,
    ChainMismatchError
)
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

from app.adapters.trojan import TrojanReferenceAdapter, trojan_adapter
from app.adapters.banana_gun import BananaGunReferenceAdapter, banana_gun_adapter

def get_adapter_for_chain(chain_id: str) -> BaseChainAdapter:
    """
    Factory function returning the specialized blockchain adapter for a given CAIP-2 chain_id.
    Prefers user-configured active RPC endpoints from ProviderManager when available.
    """
    user_rpc = None
    try:
        from app.services.provider_manager import provider_manager
        user_rpc = provider_manager.get_active_rpc_endpoint(chain_id)
    except Exception:
        pass

    if chain_id.startswith("solana"):
        rpc_list = [user_rpc] if user_rpc else ["https://api.mainnet-beta.solana.com"]
        return SolanaChainAdapter(chain_id=chain_id, rpc_urls=rpc_list)
    elif chain_id == "eip155:42161":
        rpc_list = [user_rpc] if user_rpc else ["https://arb1.arbitrum.io/rpc"]
        return ArbitrumChainAdapter(rpc_urls=rpc_list)
    elif chain_id == "eip155:10":
        rpc_list = [user_rpc] if user_rpc else ["https://mainnet.optimism.io"]
        return OptimismChainAdapter(rpc_urls=rpc_list)
    elif chain_id == "eip155:137":
        rpc_list = [user_rpc] if user_rpc else ["https://polygon-rpc.com"]
        return PolygonChainAdapter(rpc_urls=rpc_list)
    elif chain_id == "eip155:43114":
        rpc_list = [user_rpc] if user_rpc else ["https://api.avax.network/ext/bc/C/rpc"]
        return AvalancheChainAdapter(rpc_urls=rpc_list)
    elif chain_id.startswith("eip155"):
        rpc_list = [user_rpc] if user_rpc else ["https://eth.llamarpc.com"]
        return EvmChainAdapter(chain_id=chain_id, rpc_urls=rpc_list)
    elif chain_id.startswith("sui"):
        rpc_list = [user_rpc] if user_rpc else ["https://fullnode.mainnet.sui.io:443"]
        return SuiChainAdapter(rpc_urls=rpc_list)
    elif chain_id.startswith("aptos"):
        rpc_list = [user_rpc] if user_rpc else ["https://fullnode.mainnet.aptoslabs.com/v1"]
        return AptosChainAdapter(rpc_urls=rpc_list)
    elif chain_id.startswith("ton"):
        rpc_list = [user_rpc] if user_rpc else ["https://toncenter.com/api/v2/jsonRPC"]
        return TonChainAdapter(rpc_urls=rpc_list)
    elif chain_id.startswith("tron"):
        rpc_list = [user_rpc] if user_rpc else ["https://api.trongrid.io"]
        return TronChainAdapter(rpc_urls=rpc_list)
    else:
        raise ValueError(f"Unsupported chain_id: {chain_id}")

__all__ = [
    "BaseChainAdapter",
    "ChainBlockHeader",
    "RawTransactionData",
    "ChainAdapterError",
    "RpcError",
    "DataUnavailableError",
    "UnsupportedCapabilityError",
    "ChainMismatchError",
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
    "TrojanReferenceAdapter",
    "trojan_adapter",
    "BananaGunReferenceAdapter",
    "banana_gun_adapter",
    "get_adapter_for_chain"
]
