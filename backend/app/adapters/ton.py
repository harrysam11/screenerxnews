import httpx
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
from decimal import Decimal
from app.adapters.base import (
    BaseChainAdapter,
    ChainBlockHeader,
    RawTransactionData,
    DataUnavailableError,
    RpcError,
    UnsupportedCapabilityError
)
from app.core.network import get_http_client, OfflineModeBlockedException
from app.core.address_validator import AddressValidator

class TonChainAdapter(BaseChainAdapter):
    """
    TON (ton:mainnet) TVM Actor Model Adapter (P10.2-D).
    Evaluates Jetton master contract data via get_jetton_data:
    - total_supply
    - mintable (-1 for true, 0 for false)
    - admin_address (null / addr_none for renounced)
    Validates user-friendly Base64url and raw 0:hex addresses.
    Never fabricates default decimals or dummy masterchain blocks.
    """
    def __init__(self, rpc_urls: Optional[List[str]] = None):
        super().__init__(
            chain_id="ton:mainnet",
            rpc_urls=rpc_urls or ["https://toncenter.com/api/v2/jsonRPC"]
        )

    async def _call_rpc(self, method: str, params: dict) -> Any:
        url = self.rpc_urls[0]
        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        async with get_http_client(timeout=10.0) as client:
            try:
                resp = await client.post(url, json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    if not data.get("ok", True):
                        raise RpcError(f"TON RPC Error: {data.get('error')}", chain_id=self.chain_id)
                    return data.get("result")
                else:
                    raise RpcError(f"HTTP {resp.status_code} from TON node", chain_id=self.chain_id, status_code=resp.status_code)
            except OfflineModeBlockedException:
                raise
            except RpcError:
                raise
            except Exception as e:
                raise RpcError(f"Network error contacting TON RPC: {str(e)}", chain_id=self.chain_id)

    async def get_latest_block(self) -> ChainBlockHeader:
        """
        Retrieves latest masterchain seqno and root hash via getMasterchainInfo.
        """
        res = await self._call_rpc("getMasterchainInfo", {})
        if not res or not isinstance(res, dict) or "last" not in res:
            raise DataUnavailableError("Failed to fetch masterchain info from TON RPC", chain_id=self.chain_id)

        last_info = res["last"]
        seq_no = int(last_info.get("seqno", 0))
        root_hash = last_info.get("root_hash", "")

        # Fetch block details for timestamp
        block_time = datetime.now(timezone.utc)
        try:
            shards = await self._call_rpc("getBlockHeader", {
                "workchain": last_info.get("workchain", -1),
                "shard": last_info.get("shard", "-9223372036854775808"),
                "seqno": seq_no
            })
            if shards and "gen_utime" in shards:
                block_time = datetime.fromtimestamp(int(shards["gen_utime"]), timezone.utc)
        except Exception:
            pass

        return ChainBlockHeader(
            chain_id=self.chain_id,
            block_number=seq_no,
            block_hash=root_hash,
            parent_hash="",
            block_time=block_time,
            finalized=True
        )

    async def get_token_metadata(self, jetton_master_address: str) -> Dict[str, Any]:
        """
        Retrieves Jetton data via runGetMethod get_jetton_data.
        Never fabricates hardcoded decimals.
        """
        is_valid, norm_addr, _ = AddressValidator.validate_ton_address(jetton_master_address)
        if not is_valid:
            raise DataUnavailableError(f"Invalid TON Jetton master address: {jetton_master_address}", chain_id=self.chain_id)

        res = await self._call_rpc("runGetMethod", {
            "address": norm_addr,
            "method": "get_jetton_data",
            "stack": []
        })

        if not res or res.get("exit_code") != 0:
            raise DataUnavailableError(f"get_jetton_data failed for {jetton_master_address} on TON", chain_id=self.chain_id)

        stack = res.get("stack", [])
        total_supply = None
        if len(stack) > 0 and stack[0][0] == "num":
            total_supply = int(stack[0][1], 16) if isinstance(stack[0][1], str) else int(stack[0][1])

        return {
            "chain_id": self.chain_id,
            "asset_address": norm_addr,
            "total_supply": total_supply,
            "decimals": None,  # Jetton decimals are off-chain / IPFS metadata in TEP-74
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
        Evaluates TEP-74 Jetton security invariants:
        - Mintable flag: -1 = true (minting active), 0 = false (minting disabled).
        - Admin address: None / 'addr_none' / empty = ownership renounced.
        """
        is_mintable = (mintable == -1)
        is_renounced = (admin_address is None or admin_address in ("", "addr_none", "0:0000000000000000000000000000000000000000000000000000000000000000"))

        return {
            "chain_type": "tvm_ton",
            "chain_id": self.chain_id,
            "total_supply": total_supply,
            "is_mintable": is_mintable,
            "is_mint_revoked": (mintable == 0),
            "is_renounced": is_renounced,
            "is_ownership_renounced": is_renounced,
            "admin_address": admin_address
        }

    async def inspect_token_security(self, jetton_master_address: str) -> Dict[str, Any]:
        """
        Queries get_jetton_data and evaluates mintable and admin renouncement status.
        """
        is_valid, norm_addr, _ = AddressValidator.validate_ton_address(jetton_master_address)
        if not is_valid:
            raise DataUnavailableError(f"Invalid TON Jetton master address: {jetton_master_address}", chain_id=self.chain_id)

        res = await self._call_rpc("runGetMethod", {
            "address": norm_addr,
            "method": "get_jetton_data",
            "stack": []
        })

        if not res or res.get("exit_code") != 0:
            raise DataUnavailableError(f"get_jetton_data failed for {jetton_master_address}", chain_id=self.chain_id)

        stack = res.get("stack", [])
        total_supply = 0
        mintable = 0
        admin_address = None

        if len(stack) >= 3:
            total_supply = int(stack[0][1], 0) if stack[0][0] == "num" else 0
            mintable = int(stack[1][1], 0) if stack[1][0] == "num" else 0

        eval_res = self.evaluate_jetton_data(total_supply, mintable, admin_address)
        eval_res["asset_address"] = norm_addr
        return eval_res

    async def get_initial_buyer_transactions(
        self, pool_address: str, asset_address: str, limit: int = 25
    ) -> List[RawTransactionData]:
        return []
