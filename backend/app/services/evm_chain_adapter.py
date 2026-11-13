from typing import Dict, Any, List
from datetime import datetime, timezone
from decimal import Decimal
from app.adapters.base import BaseChainAdapter, ChainBlockHeader, RawTransactionData
from app.services.evm_rpc import EvmRpcClient
from app.services.evm_provider_manager import evm_provider_manager

class EvmChainAdapterService(BaseChainAdapter):
    """
    Service adapter wrapping EVM JSON-RPC calls for Ethereum, Base, and BNB Chain.
    """
    def __init__(self, chain_id: str):
        endpoints = [ep.url for ep in evm_provider_manager.providers.get(chain_id, [])]
        super().__init__(chain_id, endpoints)
        self.rpc_client = EvmRpcClient(chain_id)

    async def get_latest_block(self) -> ChainBlockHeader:
        res = await self.rpc_client.call_rpc("eth_getBlockByNumber", ["latest", False])
        if not res:
            return ChainBlockHeader(
                chain_id=self.chain_id,
                block_number=0,
                block_hash="0x0",
                parent_hash="0x0",
                block_time=datetime.now(timezone.utc)
            )
        return ChainBlockHeader(
            chain_id=self.chain_id,
            block_number=int(res.get("number", "0x0"), 16),
            block_hash=res.get("hash", "0x0"),
            parent_hash=res.get("parentHash", "0x0"),
            block_time=datetime.fromtimestamp(int(res.get("timestamp", "0x0"), 16), timezone.utc)
        )

    async def get_token_metadata(self, asset_address: str) -> Dict[str, Any]:
        return {
            "chain_id": self.chain_id,
            "asset_address": asset_address.lower(),
            "decimals": 18,
            "total_supply": None
        }

    async def inspect_token_security(self, asset_address: str) -> Dict[str, Any]:
        code = await self.rpc_client.call_rpc("eth_getCode", [asset_address, "latest"])
        is_contract = code is not None and code != "0x" and len(code) > 2

        impl_slot = "0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc"
        impl_val = await self.rpc_client.call_rpc("eth_getStorageAt", [asset_address, impl_slot, "latest"])
        is_proxy = impl_val is not None and impl_val != "0x0000000000000000000000000000000000000000000000000000000000000000"

        return {
            "chain_type": "evm",
            "chain_id": self.chain_id,
            "is_contract": is_contract,
            "is_proxy": is_proxy,
            "implementation_address": impl_val if is_proxy else None
        }

    async def get_initial_buyer_transactions(
        self, pool_address: str, asset_address: str, limit: int = 25
    ) -> List[RawTransactionData]:
        return []
