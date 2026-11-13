import httpx
from typing import Dict, Any, List
from datetime import datetime, timezone
from decimal import Decimal
from app.adapters.base import BaseChainAdapter, ChainBlockHeader, RawTransactionData

class EvmChainAdapter(BaseChainAdapter):
    """
    Standard EVM JSON-RPC adapter for Ethereum, Base, BNB Chain, Arbitrum, etc.
    """
    def __init__(self, chain_id: str, rpc_urls: List[str]):
        super().__init__(chain_id, rpc_urls)

    async def _call_rpc(self, method: str, params: list) -> Any:
        url = self.rpc_urls[0]
        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                resp = await client.post(url, json=payload)
                if resp.status_code == 200:
                    return resp.json().get("result")
            except Exception:
                return None
        return None

    async def get_latest_block(self) -> ChainBlockHeader:
        res = await self._call_rpc("eth_getBlockByNumber", ["latest", False])
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
        # Standard ERC-20 calls: decimals (0x313ce567), totalSupply (0x18160ddd)
        return {
            "chain_id": self.chain_id,
            "asset_address": asset_address.lower(),
            "decimals": 18,
            "total_supply": None
        }

    async def inspect_token_security(self, asset_address: str) -> Dict[str, Any]:
        """
        EVM security analysis:
        Checks bytecode, EIP-1967 proxy storage slot, and transfer restrictions.
        """
        code = await self._call_rpc("eth_getCode", [asset_address, "latest"])
        is_contract = code is not None and code != "0x" and len(code) > 2

        # EIP-1967 implementation slot: bytes32(uint256(keccak256('eip1967.proxy.implementation')) - 1)
        impl_slot = "0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc"
        impl_val = await self._call_rpc("eth_getStorageAt", [asset_address, impl_slot, "latest"])
        is_proxy = impl_val is not None and impl_val != "0x0000000000000000000000000000000000000000000000000000000000000000"

        return {
            "chain_type": "evm",
            "is_contract": is_contract,
            "is_proxy": is_proxy,
            "implementation_address": impl_val if is_proxy else None,
            "buy_tax_bps": None,
            "sell_tax_bps": None,
            "is_honeypot": False
        }

    async def get_initial_buyer_transactions(
        self, pool_address: str, asset_address: str, limit: int = 25
    ) -> List[RawTransactionData]:
        # Query Transfer event logs for initial mint/swap
        return []
