from typing import Dict, Any, Optional, List
import httpx
from app.core.config import settings

class SolanaRpcService:
    def __init__(self, rpc_url: str = settings.SOLANA_RPC_URL):
        self.rpc_url = rpc_url

    async def _call_rpc(self, method: str, params: list) -> Optional[Any]:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params
        }
        async with httpx.AsyncClient(timeout=12.0) as client:
            try:
                resp = await client.post(self.rpc_url, json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    return data.get("result")
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
