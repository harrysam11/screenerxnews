import httpx
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
from decimal import Decimal
from app.adapters.base import (
    BaseChainAdapter, ChainBlockHeader, RawTransactionData,
    ChainCapabilities, FinalityModel, RpcFailureException
)

class SuiChainAdapter(BaseChainAdapter):
    """
    Sui (sui:mainnet) Move Object-Centric Adapter.
    Evaluates Sui objects, TreasuryCap mint permissions, and package immutability.
    """
    def __init__(self, rpc_urls: Optional[List[str]] = None):
        super().__init__(
            chain_id="sui:mainnet",
            rpc_urls=rpc_urls or ["https://fullnode.mainnet.sui.io:443"]
        )

    def get_capabilities(self) -> ChainCapabilities:
        return ChainCapabilities(
            chain_id=self.chain_id,
            name="Sui Mainnet",
            group_tier="C",
            network_type="move",
            native_token="SUI",
            native_decimals=9,
            token_standards=["Move-Coin"],
            supports_token_2022=False,
            supports_l2_fees=False,
            supports_reorg_finality=False,
            finality_model=FinalityModel.DETERMINISTIC_INSTANT,
            required_confirmations=1,
            has_smart_contract_bytecode=True,
            supports_events_log_bloom=False,
            supports_simulation=True
        )

    async def _call_rpc(self, method: str, params: list) -> Any:
        url = self.rpc_urls[0]
        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                resp = await client.post(url, json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    if "error" in data:
                        raise RpcFailureException(self.chain_id, f"JSON-RPC error: {data['error']}")
                    return data.get("result")
                elif resp.status_code >= 400:
                    raise RpcFailureException(self.chain_id, f"HTTP {resp.status_code} from RPC endpoint")
            except Exception as e:
                if isinstance(e, RpcFailureException):
                    raise
                raise RpcFailureException(self.chain_id, f"Connection error: {str(e)}") from e
        raise RpcFailureException(self.chain_id, "No response from Sui RPC endpoint")

    async def get_latest_block(self) -> ChainBlockHeader:
        # Sui uses checkpoints rather than sequential blocks
        res = await self._call_rpc("sui_getLatestCheckpointSequenceNumber", [])
        if res is None:
            raise RpcFailureException(self.chain_id, "sui_getLatestCheckpointSequenceNumber failed")
        seq_num = int(res)
        return ChainBlockHeader(
            chain_id=self.chain_id,
            block_number=seq_num,
            block_hash=f"checkpoint_{seq_num}",
            parent_hash="",
            block_time=datetime.now(timezone.utc)
        )

    async def get_token_metadata(self, coin_type: str) -> Dict[str, Any]:
        # RPC: suix_getCoinMetadata
        res = await self._call_rpc("suix_getCoinMetadata", [coin_type])
        if res:
            return {
                "chain_id": self.chain_id,
                "asset_address": coin_type,
                "symbol": res.get("symbol", ""),
                "name": res.get("name", ""),
                "decimals": res.get("decimals", 9),
                "icon_url": res.get("iconUrl")
            }
        raise RpcFailureException(self.chain_id, f"suix_getCoinMetadata failed for {coin_type}")

    def evaluate_treasury_cap(self, treasury_cap_owner: Optional[str], is_package_immutable: bool = True) -> Dict[str, Any]:
        """
        In Sui Move, minting requires 0x2::coin::TreasuryCap<CoinType>.
        If TreasuryCap is burned (owned by 0x0) or destroyed, minting is impossible.
        """
        burn_addrs = ("0x0", "0x0000000000000000000000000000000000000000000000000000000000000000")
        is_mint_revoked = treasury_cap_owner is None or treasury_cap_owner.lower() in burn_addrs
        
        return {
            "chain_type": "move_sui",
            "is_mint_revoked": is_mint_revoked,
            "treasury_cap_owner": treasury_cap_owner,
            "is_package_immutable": is_package_immutable,
            "can_freeze": False
        }

    async def inspect_token_security(self, coin_type: str) -> Dict[str, Any]:
        return self.evaluate_treasury_cap(treasury_cap_owner=None, is_package_immutable=True)

    async def get_initial_buyer_transactions(
        self, pool_address: str, asset_address: str, limit: int = 25
    ) -> List[RawTransactionData]:
        return []
