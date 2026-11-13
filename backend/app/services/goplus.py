from typing import Dict, Any, Optional
import httpx
from app.core.config import settings

class GoPlusService:
    def __init__(self, base_url: str = settings.GOPLUS_API_BASE):
        self.base_url = base_url

    async def get_token_security(self, mint_address: str) -> Optional[Dict[str, Any]]:
        """
        Fetches Solana token security report.
        Documented rate limit: 30 RPM
        """
        url = f"{self.base_url}/api/v1/solana/token_security"
        params = {"contract_addresses": mint_address}
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                resp = await client.get(url, params=params)
                if resp.status_code == 200:
                    data = resp.json()
                    result = data.get("result", {})
                    return result.get(mint_address)
            except Exception:
                return None
        return None

goplus_service = GoPlusService()
