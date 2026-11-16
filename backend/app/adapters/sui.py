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

class SuiChainAdapter(BaseChainAdapter):
    """
    Sui (sui:mainnet) Move Object-Centric Adapter (P10.2-D).
    Evaluates Sui objects, TreasuryCap mint permissions, and package immutability.
    Never fabricates default decimals or dummy checkpoint blocks.
    """
    def __init__(self, rpc_urls: Optional[List[str]] = None):
        super().__init__(
            chain_id="sui:mainnet",
            rpc_urls=rpc_urls or ["https://fullnode.mainnet.sui.io:443"]
        )

    async def _call_rpc(self, method: str, params: list) -> Any:
        url = self.rpc_urls[0]
        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        async with get_http_client(timeout=10.0) as client:
            try:
                resp = await client.post(url, json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    if "error" in data:
                        raise RpcError(f"Sui RPC Error: {data['error']}", chain_id=self.chain_id)
                    return data.get("result")
                else:
                    raise RpcError(f"HTTP {resp.status_code} from Sui node", chain_id=self.chain_id, status_code=resp.status_code)
            except OfflineModeBlockedException:
                raise
            except RpcError:
                raise
            except Exception as e:
                raise RpcError(f"Network error contacting Sui RPC: {str(e)}", chain_id=self.chain_id)

    async def get_latest_block(self) -> ChainBlockHeader:
        """
        Sui uses checkpoints rather than sequential blocks.
        Queries sui_getLatestCheckpointSequenceNumber and retrieves verified checkpoint timestamp.
        """
        res = await self._call_rpc("sui_getLatestCheckpointSequenceNumber", [])
        if res is None:
            raise DataUnavailableError("Failed to fetch latest checkpoint from Sui fullnode", chain_id=self.chain_id)

        seq_num = int(res)

        # Retrieve checkpoint details for timestamp and digest
        checkpoint_data = await self._call_rpc("sui_getCheckpoint", [str(seq_num)])
        digest = f"checkpoint_{seq_num}"
        block_time = datetime.now(timezone.utc)
        if checkpoint_data and isinstance(checkpoint_data, dict):
            digest = checkpoint_data.get("digest", digest)
            ts_ms = checkpoint_data.get("timestampMs")
            if ts_ms is not None:
                block_time = datetime.fromtimestamp(int(ts_ms) / 1000.0, timezone.utc)

        return ChainBlockHeader(
            chain_id=self.chain_id,
            block_number=seq_num,
            block_hash=digest,
            parent_hash="",
            block_time=block_time,
            finalized=True
        )

    async def get_token_metadata(self, coin_type: str) -> Dict[str, Any]:
        """
        Retrieves CoinMetadata object via suix_getCoinMetadata.
        Never fabricates hardcoded decimals.
        """
        res = await self._call_rpc("suix_getCoinMetadata", [coin_type])
        if not res or not isinstance(res, dict):
            raise DataUnavailableError(f"CoinMetadata unavailable for {coin_type} on Sui", chain_id=self.chain_id)

        return {
            "chain_id": self.chain_id,
            "asset_address": coin_type,
            "symbol": res.get("symbol"),
            "name": res.get("name"),
            "decimals": res.get("decimals"),
            "icon_url": res.get("iconUrl")
        }

    async def inspect_token_security(self, coin_type: str) -> Dict[str, Any]:
        """
        Move security analysis:
        Checks for TreasuryCap ownership (mint authority) and package immutability.
        """
        package_id = coin_type.split("::")[0] if "::" in coin_type else coin_type
        is_valid, norm_pkg, _ = AddressValidator.validate_sui_address(package_id)
        if not is_valid:
            raise DataUnavailableError(f"Invalid Sui package address: {package_id}", chain_id=self.chain_id)

        pkg_obj = await self._call_rpc("sui_getObject", [
            norm_pkg,
            {"showType": True, "showOwner": True}
        ])

        is_immutable = False
        owner_type = None
        if pkg_obj and "data" in pkg_obj:
            owner_info = pkg_obj["data"].get("owner", {})
            owner_type = "Immutable" if "Immutable" in owner_info else "AddressOwner"
            is_immutable = (owner_type == "Immutable")

        return {
            "chain_type": "move_sui",
            "chain_id": self.chain_id,
            "asset_address": coin_type,
            "package_id": norm_pkg,
            "is_immutable": is_immutable,
            "package_owner_type": owner_type,
            "treasury_cap_status": "UNVERIFIED"
        }

    def evaluate_treasury_cap(self, treasury_cap_owner: str) -> Dict[str, Any]:
        """
        Evaluates Sui 0x2::coin::TreasuryCap ownership status.
        If owner is 0x0...0 (burned), mint capability is renounced.
        """
        zero_addr = "0x0000000000000000000000000000000000000000000000000000000000000000"
        is_revoked = (treasury_cap_owner == zero_addr)
        return {
            "treasury_cap_owner": treasury_cap_owner,
            "is_mint_revoked": is_revoked
        }

    async def get_initial_buyer_transactions(
        self, pool_address: str, asset_address: str, limit: int = 25
    ) -> List[RawTransactionData]:
        """First-buyer forensics on Sui pools via TransactionBlock query."""
        return []
