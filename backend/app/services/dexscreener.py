from typing import Dict, Any, List, Optional
import httpx
from app.core.config import settings

class DexScreenerService:
    def __init__(self, base_url: str = settings.DEX_SCREENER_API_BASE):
        self.base_url = base_url

    async def get_latest_token_profiles(self) -> List[Dict[str, Any]]:
        """
        Fetches latest surfaced token profiles.
        Endpoint: /token-profiles/latest/v1
        Rate limit: 60 RPM
        """
        url = f"{self.base_url}/token-profiles/latest/v1"
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                return [item for item in data if item.get("chainId") == "solana"]
            return []

    async def get_tokens_batch(self, mint_addresses: List[str]) -> List[Dict[str, Any]]:
        """
        Fetches token market data in batches up to 30 mints.
        Endpoint: /tokens/v1/solana/{mint1,mint2,...}
        Rate limit: 300 RPM
        """
        if not mint_addresses:
            return []
        joined = ",".join(mint_addresses[:30])
        url = f"{self.base_url}/tokens/v1/solana/{joined}"
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                return resp.json()
            return []

    async def search_pairs(self, query: str) -> List[Dict[str, Any]]:
        """
        Searches pairs by symbol or name.
        Endpoint: /latest/dex/search?q={query}
        """
        url = f"{self.base_url}/latest/dex/search"
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, params={"q": query})
            if resp.status_code == 200:
                data = resp.json()
                return [p for p in data.get("pairs", []) if p.get("chainId") == "solana"]
            return []

dex_screener_service = DexScreenerService()
