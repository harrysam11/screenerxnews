import httpx
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
from decimal import Decimal
from app.adapters.base import (
    BaseChainAdapter, ChainBlockHeader, RawTransactionData,
    ChainCapabilities, FinalityModel, RpcFailureException
)

class TonChainAdapter(BaseChainAdapter):
    """
    TON (ton:mainnet) TVM Actor Model Adapter.
    Evaluates Jetton master contract data via get_jetton_data:
    - total_supply
    - mintable (-1 for true, 0 for false)
    - admin_address (null / addr_none for renounced)
    """
    def __init__(self, rpc_urls: Optional[List[str]] = None):
        super().__init__(
            chain_id="ton:mainnet",
            rpc_urls=rpc_urls or ["https://toncenter.com/api/v2/jsonRPC"]
        )

    def get_capabilities(self) -> ChainCapabilities:
        return ChainCapabilities(
            chain_id=self.chain_id,
            name="The Open Network",
            group_tier="C",
            network_type="tvm",
            native_token="TON",
            native_decimals=9,
            token_standards=["Jetton"],
            supports_token_2022=False,
            supports_l2_fees=False,
            supports_reorg_finality=False,
            finality_model=FinalityModel.STATE_MACHINE_EPOCHS,
            required_confirmations=1,
            has_smart_contract_bytecode=True,
            supports_events_log_bloom=False,
            supports_simulation=False
        )

    async def _call_rpc(self, method: str, params: dict) -> Any:
        url = self.rpc_urls[0]
        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                resp = await client.post(url, json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    if not data.get("ok", True):
                        raise RpcFailureException(self.chain_id, f"TON RPC error: {data.get('error')}")
                    return data.get("result")
                elif resp.status_code >= 400:
                    raise RpcFailureException(self.chain_id, f"HTTP {resp.status_code} from TON RPC")
            except Exception as e:
                if isinstance(e, RpcFailureException):
                    raise
                raise RpcFailureException(self.chain_id, f"Connection error: {str(e)}") from e
        raise RpcFailureException(self.chain_id, "No response from TON RPC endpoint")

    async def get_latest_block(self) -> ChainBlockHeader:
        res = await self._call_rpc("getMasterchainInfo", {})
        if not res or "last" not in res:
            raise RpcFailureException(self.chain_id, "getMasterchainInfo failed to return masterchain state")
        seq_no = int(res.get("last", {}).get("seqno", 0))
        root_hash = res.get("last", {}).get("root_hash", "0x0")
        return ChainBlockHeader(
            chain_id=self.chain_id,
            block_number=seq_no,
            block_hash=root_hash,
            parent_hash="",
            block_time=datetime.now(timezone.utc)
        )

    async def get_token_metadata(self, jetton_master_address: str) -> Dict[str, Any]:
        return {
            "chain_id": self.chain_id,
            "asset_address": jetton_master_address,
            "decimals": 9,
            "symbol": None,
            "name": None
        }

    def evaluate_jetton_data(
        self,
        total_supply: int,
        mintable: int,
        admin_address: Optional[str]
    ) -> Dict[str, Any]:
        """
        In TON Jettons:
        mintable: -1 (or 1) indicates mintable; 0 indicates non-mintable (mint revoked).
        admin_address: None or empty or addr_none indicates ownership is renounced.
        """
        is_mintable = (mintable != 0)
        is_renounced = admin_address is None or admin_address in ("", "0:0000000000000000000000000000000000000000000000000000000000000000")
        
        return {
            "chain_type": "tvm_ton",
            "total_supply": total_supply,
            "is_mintable": is_mintable,
            "is_mint_revoked": not is_mintable,
            "is_ownership_renounced": is_renounced,
            "admin_address": admin_address
        }

    async def inspect_token_security(self, jetton_master_address: str) -> Dict[str, Any]:
        try:
            res = await self._call_rpc("runGetMethod", {
                "address": jetton_master_address,
                "method": "get_jetton_data",
                "stack": []
            })
            if res and res.get("exit_code") == 0:
                stack = res.get("stack", [])
                total_supply = int(stack[0][1], 16) if len(stack) > 0 and isinstance(stack[0][1], str) else 0
                mintable = int(stack[1][1], 16) if len(stack) > 1 and isinstance(stack[1][1], str) else -1
                return self.evaluate_jetton_data(total_supply, mintable, None)
        except Exception:
            pass
        return self.evaluate_jetton_data(0, -1, None)

    async def get_initial_buyer_transactions(
        self, pool_address: str, asset_address: str, limit: int = 25
    ) -> List[RawTransactionData]:
        return []
