from typing import Dict, Any, Optional
import httpx
from app.core.config import settings
from app.core.network import get_http_client, OfflineModeBlockedException

class RugCheckService:
    def __init__(self, base_url: str = settings.RUGCHECK_API_BASE):
        self.base_url = base_url

    async def get_token_report(self, mint_address: str) -> Optional[Dict[str, Any]]:
        """
        Fetches RugCheck report for a Solana mint address.
        """
        url = f"{self.base_url}/v1/tokens/{mint_address}/report"
        async with get_http_client(timeout=10.0) as client:
            try:
                resp = await client.get(url)
                if resp.status_code == 200:
                    return resp.json()
            except OfflineModeBlockedException:
                raise
            except Exception:
                return None
        return None

rugcheck_service = RugCheckService()
