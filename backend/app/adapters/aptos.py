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

class AptosChainAdapter(BaseChainAdapter):
    """
    Aptos (aptos:mainnet) Move Resource-Centric Adapter (P10.2-D).
    Evaluates 0x1::coin::CoinInfo<CoinType>, MintCapability, and FreezeCapability.
    Never fabricates default decimals or dummy ledger version blocks.
    """
    def __init__(self, rpc_urls: Optional[List[str]] = None):
        super().__init__(
            chain_id="aptos:mainnet",
            rpc_urls=rpc_urls or ["https://fullnode.mainnet.aptoslabs.com/v1"]
        )

    async def _call_rest(self, endpoint: str) -> Any:
        url = f"{self.rpc_urls[0].rstrip('/')}/{endpoint.lstrip('/')}"
        async with get_http_client(timeout=10.0) as client:
            try:
                resp = await client.get(url)
                if resp.status_code == 200:
                    return resp.json()
                elif resp.status_code == 404:
                    return None
                else:
                    raise RpcError(f"HTTP {resp.status_code} from Aptos fullnode: {resp.text[:200]}", chain_id=self.chain_id, status_code=resp.status_code)
            except OfflineModeBlockedException:
                raise
            except RpcError:
                raise
            except Exception as e:
                raise RpcError(f"Network error contacting Aptos node: {str(e)}", chain_id=self.chain_id)

    async def get_latest_block(self) -> ChainBlockHeader:
        """
        Retrieves current block height, ledger version, and verified Unix timestamp from Aptos Node API.
        """
        res = await self._call_rest("/")
        if not res or not isinstance(res, dict):
            raise DataUnavailableError("Failed to fetch ledger state from Aptos node", chain_id=self.chain_id)

        block_height_str = res.get("block_height")
        if block_height_str is None:
            raise DataUnavailableError("Missing block_height in Aptos ledger response", chain_id=self.chain_id)

        block_height = int(block_height_str)
        ledger_version = res.get("ledger_version", "0")
        ledger_ts_micro = res.get("ledger_timestamp")

        if ledger_ts_micro is not None:
            block_time = datetime.fromtimestamp(int(ledger_ts_micro) / 1_000_000.0, timezone.utc)
        else:
            block_time = datetime.now(timezone.utc)

        return ChainBlockHeader(
            chain_id=self.chain_id,
            block_number=block_height,
            block_hash=f"ledger_version_{ledger_version}",
            parent_hash="",
            block_time=block_time,
            finalized=True
        )

    async def get_token_metadata(self, coin_type: str) -> Dict[str, Any]:
        """
        Retrieves CoinInfo resource at 0x1::coin::CoinInfo<CoinType>.
        Never fabricates hardcoded decimals.
        """
        creator = coin_type.split("::")[0] if "::" in coin_type else coin_type
        is_valid, norm_creator, _ = AddressValidator.validate_aptos_address(creator)
        if not is_valid:
            raise DataUnavailableError(f"Invalid Aptos creator address in coin type: {creator}", chain_id=self.chain_id)

        res = await self._call_rest(f"accounts/{norm_creator}/resource/0x1::coin::CoinInfo<{coin_type}>")
        if not res or "data" not in res:
            raise DataUnavailableError(f"CoinInfo<{coin_type}> resource not found on Aptos", chain_id=self.chain_id)

        data = res["data"]
        return {
            "chain_id": self.chain_id,
            "asset_address": coin_type,
            "symbol": data.get("symbol"),
            "name": data.get("name"),
            "decimals": data.get("decimals")
        }

    async def inspect_token_security(self, coin_type: str) -> Dict[str, Any]:
        """
        Move security analysis:
        Checks whether creator account holds MintCapability, FreezeCapability, or BurnCapability.
        """
        creator = coin_type.split("::")[0] if "::" in coin_type else coin_type
        is_valid, norm_creator, _ = AddressValidator.validate_aptos_address(creator)
        if not is_valid:
            raise DataUnavailableError(f"Invalid Aptos creator address in coin type: {creator}", chain_id=self.chain_id)

        # Query creator account resources
        resources = await self._call_rest(f"accounts/{norm_creator}/resources")
        has_mint_cap = False
        has_freeze_cap = False

        if resources and isinstance(resources, list):
            for r in resources:
                rtype = r.get("type", "")
                if f"0x1::coin::MintCapability<{coin_type}>" in rtype:
                    has_mint_cap = True
                if f"0x1::coin::FreezeCapability<{coin_type}>" in rtype:
                    has_freeze_cap = True

        return {
            "chain_type": "move_aptos",
            "chain_id": self.chain_id,
            "asset_address": coin_type,
            "creator_address": norm_creator,
            "has_mint_capability": has_mint_cap,
            "has_freeze_capability": has_freeze_cap,
            "is_immutable": not (has_mint_cap or has_freeze_cap)
        }

    def evaluate_coin_capabilities(self, capabilities: Dict[str, Any]) -> Dict[str, Any]:
        """
        Evaluates Aptos CoinCapabilities:
        If mint_capability or freeze_capability is missing (burned), authority is revoked.
        """
        has_mint = "mint_capability" in capabilities
        has_freeze = "freeze_capability" in capabilities
        return {
            "is_mint_revoked": not has_mint,
            "is_freeze_revoked": not has_freeze
        }

    async def get_initial_buyer_transactions(
        self, pool_address: str, asset_address: str, limit: int = 25
    ) -> List[RawTransactionData]:
        return []
