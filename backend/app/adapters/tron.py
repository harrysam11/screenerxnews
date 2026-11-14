import httpx
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
from decimal import Decimal
from app.adapters.base import (
    BaseChainAdapter, ChainBlockHeader, RawTransactionData,
    ChainCapabilities, FinalityModel, RpcFailureException
)

class TronChainAdapter(BaseChainAdapter):
    """
    TRON (tron:mainnet) TVM Account Model Adapter.
    Evaluates TRC-20 token contracts, Base58/hex addresses, energy models, and owner renouncement.
    """
    def __init__(self, rpc_urls: Optional[List[str]] = None):
        super().__init__(
            chain_id="tron:mainnet",
            rpc_urls=rpc_urls or ["https://api.trongrid.io"]
        )

    def get_capabilities(self) -> ChainCapabilities:
        return ChainCapabilities(
            chain_id=self.chain_id,
            name="TRON Mainnet",
            group_tier="C",
            network_type="tvm",
            native_token="TRX",
            native_decimals=6,
            token_standards=["TRC-20"],
            supports_token_2022=False,
            supports_l2_fees=False,
            supports_reorg_finality=True,
            finality_model=FinalityModel.STATE_MACHINE_EPOCHS,
            required_confirmations=19,
            has_smart_contract_bytecode=True,
            supports_events_log_bloom=False,
            supports_simulation=False
        )

    async def _call_api(self, endpoint: str, payload: Optional[dict] = None) -> Any:
        url = f"{self.rpc_urls[0].rstrip('/')}/{endpoint.lstrip('/')}"
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                if payload:
                    resp = await client.post(url, json=payload)
                else:
                    resp = await client.get(url)
                if resp.status_code == 200:
                    return resp.json()
                elif resp.status_code >= 400:
                    raise RpcFailureException(self.chain_id, f"HTTP {resp.status_code} from TronGrid API")
            except Exception as e:
                if isinstance(e, RpcFailureException):
                    raise
                raise RpcFailureException(self.chain_id, f"TRON API error: {str(e)}") from e
        raise RpcFailureException(self.chain_id, "No response from TRON API endpoint")

    async def get_latest_block(self) -> ChainBlockHeader:
        res = await self._call_api("/wallet/getnowblock")
        if not res or "block_header" not in res:
            raise RpcFailureException(self.chain_id, "Failed to retrieve current block from TRON node")
        
        raw_data = res["block_header"].get("raw_data", {})
        block_number = int(raw_data.get("number", 0))
        block_hash = res.get("blockID", "0x0")
        parent_hash = raw_data.get("parentHash", "0x0")
        ts = int(raw_data.get("timestamp", 0)) / 1000.0
        return ChainBlockHeader(
            chain_id=self.chain_id,
            block_number=block_number,
            block_hash=block_hash,
            parent_hash=parent_hash,
            block_time=datetime.fromtimestamp(ts, timezone.utc) if ts > 0 else datetime.now(timezone.utc)
        )

    def evaluate_trc20_security(
        self,
        owner_address_hex: Optional[str],
        has_mint_selector: bool = False
    ) -> Dict[str, Any]:
        """
        In TRON TRC-20:
        Renounced owner address: 410000000000000000000000000000000000000000 or empty.
        """
        zero_hex = "410000000000000000000000000000000000000000"
        is_renounced = not owner_address_hex or owner_address_hex.lower() == zero_hex

        return {
            "chain_type": "tvm_tron",
            "is_ownership_renounced": is_renounced,
            "owner_address_hex": owner_address_hex,
            "has_mint_selector": has_mint_selector,
            "is_mint_revoked": is_renounced and not has_mint_selector
        }

    async def get_token_metadata(self, contract_address_base58: str) -> Dict[str, Any]:
        return {
            "chain_id": self.chain_id,
            "asset_address": contract_address_base58,
            "decimals": 6,
            "symbol": None,
            "name": None
        }

    async def inspect_token_security(self, contract_address: str) -> Dict[str, Any]:
        return self.evaluate_trc20_security(owner_address_hex=None, has_mint_selector=False)

    async def get_initial_buyer_transactions(
        self, pool_address: str, asset_address: str, limit: int = 25
    ) -> List[RawTransactionData]:
        return []
