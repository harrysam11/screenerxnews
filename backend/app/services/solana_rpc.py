import asyncio
from typing import Dict, Any, Optional, List
import httpx
from app.core.config import settings
from app.core.network import get_http_client, OfflineModeBlockedException

class SolanaRpcService:
    SOLANA_MAINNET_GENESIS_HASH = "5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"

    def __init__(self, rpc_url: str = settings.SOLANA_RPC_URL):
        self._rpc_url = self._normalize_url(rpc_url)
        self._verified_endpoints: Dict[str, str] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def _normalize_url(url: str) -> str:
        if not url:
            return ""
        return url.strip().rstrip("/")

    @property
    def rpc_url(self) -> str:
        return self._rpc_url

    @rpc_url.setter
    def rpc_url(self, value: str) -> None:
        self._rpc_url = self._normalize_url(value)

    def set_rpc_url(self, rpc_url: str) -> None:
        self.rpc_url = rpc_url

    @property
    def _genesis_verified(self) -> bool:
        return self.is_endpoint_verified(self._rpc_url)

    @_genesis_verified.setter
    def _genesis_verified(self, val: bool) -> None:
        if not val:
            self._verified_endpoints.pop(self._rpc_url, None)

    def is_endpoint_verified(self, endpoint_url: Optional[str] = None) -> bool:
        target = self._normalize_url(endpoint_url or self._rpc_url)
        return self._verified_endpoints.get(target) == self.SOLANA_MAINNET_GENESIS_HASH

    async def verify_genesis_hash(self, endpoint_url: Optional[str] = None) -> bool:
        """
        Validates connected Solana RPC node is on Mainnet-Beta by querying getGenesisHash.
        Matches canonical CAIP-2 identifier solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp.
        Safe against concurrent requests and endpoint switching.
        """
        target = self._normalize_url(endpoint_url or self._rpc_url)
        async with self._lock:
            if self._verified_endpoints.get(target) == self.SOLANA_MAINNET_GENESIS_HASH:
                return True

            if target == self._rpc_url:
                result = await self._call_rpc("getGenesisHash", [])
            else:
                result = await self._call_rpc("getGenesisHash", [], target_url=target)
            if not result:
                raise RuntimeError(f"getGenesisHash returned null or empty from Solana endpoint: {target}")
            if result != self.SOLANA_MAINNET_GENESIS_HASH:
                raise RuntimeError(
                    f"Solana genesis hash mismatch for endpoint {target}: got {result}, expected canonical {self.SOLANA_MAINNET_GENESIS_HASH}"
                )
            self._verified_endpoints[target] = result
            return True

    async def _call_rpc(self, method: str, params: list, target_url: Optional[str] = None) -> Optional[Any]:
        target = self._normalize_url(target_url or self._rpc_url)
        if method != "getGenesisHash" and not self.is_endpoint_verified(target):
            await self.verify_genesis_hash(target)

        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params
        }
        async with get_http_client(timeout=12.0) as client:
            try:
                resp = await client.post(target, json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    return data.get("result")
            except OfflineModeBlockedException:
                raise
            except Exception:
                return None
        return None

    async def get_parsed_mint_info(self, mint_address: str) -> Optional[Dict[str, Any]]:
        """
        Retrieves parsed mint info including mint & freeze authority and Token-2022 extensions.
        Method: getAccountInfo with encoding jsonParsed
        """
        result = await self._call_rpc("getAccountInfo", [
            mint_address,
            {"encoding": "jsonParsed", "commitment": "confirmed"}
        ])
        if not result or not result.get("value"):
            return None

        val = result["value"]
        parsed = val.get("data", {}).get("parsed", {})
        info = parsed.get("info", {})
        owner = val.get("owner")

        is_token_2022 = (owner == "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb")
        extensions = info.get("extensions", [])

        return {
            "mint_address": mint_address,
            "owner_program": owner,
            "is_token_2022": is_token_2022,
            "decimals": info.get("decimals"),
            "mint_authority": info.get("mintAuthority"),
            "freeze_authority": info.get("freezeAuthority"),
            "supply": info.get("supply"),
            "extensions": extensions
        }

    async def get_top_holders(self, mint_address: str) -> List[Dict[str, Any]]:
        """
        Retrieves the 20 largest token accounts.
        Method: getTokenLargestAccounts
        """
        result = await self._call_rpc("getTokenLargestAccounts", [
            mint_address,
            {"commitment": "confirmed"}
        ])
        if not result or not result.get("value"):
            return []
        return result["value"]

    async def get_signatures_for_address(self, address: str, limit: int = 25) -> List[Dict[str, Any]]:
        """
        Retrieves signatures for reconstructing pool deployment and first buyers.
        Method: getSignaturesForAddress
        """
        result = await self._call_rpc("getSignaturesForAddress", [
            address,
            {"limit": limit}
        ])
        return result or []

solana_rpc_service = SolanaRpcService()
