import httpx
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
from decimal import Decimal
from app.adapters.base import BaseChainAdapter, ChainBlockHeader, RawTransactionData

class AptosChainAdapter(BaseChainAdapter):
    """
    Aptos (aptos:mainnet) Move Resource-Centric Adapter.
    Evaluates 0x1::coin::CoinInfo<CoinType>, MintCapability, and FreezeCapability.
    """
    def __init__(self, rpc_urls: Optional[List[str]] = None):
        super().__init__(
            chain_id="aptos:mainnet",
            rpc_urls=rpc_urls or ["https://fullnode.mainnet.aptoslabs.com/v1"]
        )

    async def _call_rest(self, endpoint: str) -> Any:
        url = f"{self.rpc_urls[0].rstrip('/')}/{endpoint.lstrip('/')}"
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                resp = await client.get(url)
                if resp.status_code == 200:
                    return resp.json()
            except Exception:
                return None
        return None

    async def get_latest_block(self) -> ChainBlockHeader:
        res = await self._call_rest("/")
        block_height = int(res.get("block_height", 0)) if res else 0
        return ChainBlockHeader(
            chain_id=self.chain_id,
            block_number=block_height,
            block_hash=res.get("ledger_hash", "0x0") if res else "0x0",
            parent_hash="",
            block_time=datetime.now(timezone.utc)
        )

    async def get_token_metadata(self, coin_type: str) -> Dict[str, Any]:
        creator = coin_type.split("::")[0] if "::" in coin_type else coin_type
        res = await self._call_rest(f"accounts/{creator}/resource/0x1::coin::CoinInfo<{coin_type}>")
        if res and "data" in res:
            data = res["data"]
            return {
                "chain_id": self.chain_id,
                "asset_address": coin_type,
                "symbol": data.get("symbol"),
                "name": data.get("name"),
                "decimals": data.get("decimals", 8)
            }
        return {
            "chain_id": self.chain_id,
            "asset_address": coin_type,
            "decimals": 8,
            "symbol": None,
            "name": None
        }

    def evaluate_coin_capabilities(self, coin_info_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        In Aptos, MintCapability and FreezeCapability are stored optionally in CoinInfo or account resources.
        If mint_capability is null/empty/destroyed, token supply is fixed.
        """
        # Aptos data contains optional 'mint_capability' object
        has_mint_cap = coin_info_data.get("mint_capability") is not None
        has_freeze_cap = coin_info_data.get("freeze_capability") is not None
        
        return {
            "chain_type": "move_aptos",
            "is_mint_revoked": not has_mint_cap,
            "is_freeze_revoked": not has_freeze_cap,
            "has_mint_capability": has_mint_cap,
            "has_freeze_capability": has_freeze_cap
        }

    async def inspect_token_security(self, coin_type: str) -> Dict[str, Any]:
        creator = coin_type.split("::")[0] if "::" in coin_type else coin_type
        res = await self._call_rest(f"accounts/{creator}/resource/0x1::coin::CoinInfo<{coin_type}>")
        if res and "data" in res:
            return self.evaluate_coin_capabilities(res["data"])
        return self.evaluate_coin_capabilities({})

    async def get_initial_buyer_transactions(
        self, pool_address: str, asset_address: str, limit: int = 25
    ) -> List[RawTransactionData]:
        return []
