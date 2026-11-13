import httpx
from typing import Dict, Any, List, Optional
from app.services.quota_manager import quota_manager
from app.services.evm_provider_manager import evm_provider_manager

class EvmRpcClient:
    """
    Robust JSON-RPC client for EVM networks with chain ID validation and automatic failover.
    """
    EXPECTED_CHAIN_IDS = {
        "eip155:1": 1,
        "eip155:8453": 8453,
        "eip155:56": 56
    }

    def __init__(self, chain_id: str):
        if chain_id not in self.EXPECTED_CHAIN_IDS:
            raise ValueError(f"Unsupported EVM chain ID: {chain_id}")
        self.chain_id = chain_id
        self.expected_numeric_id = self.EXPECTED_CHAIN_IDS[chain_id]

    async def call_rpc(self, method: str, params: list, max_retries: int = 2) -> Optional[Any]:
        permit = await quota_manager.acquire_permit("solana_rpc", timeout=5.0)
        if not permit:
            return None

        attempts = 0
        while attempts <= max_retries:
            endpoint = evm_provider_manager.get_active_endpoint(self.chain_id)
            if not endpoint:
                return None

            payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
            async with httpx.AsyncClient(timeout=10.0) as client:
                try:
                    resp = await client.post(endpoint, json=payload)
                    if resp.status_code == 200:
                        data = resp.json()
                        evm_provider_manager.record_success(self.chain_id, endpoint)
                        return data.get("result")
                    elif resp.status_code == 429:
                        evm_provider_manager.record_failure(self.chain_id, endpoint, is_rate_limit=True)
                    else:
                        evm_provider_manager.record_failure(self.chain_id, endpoint)
                except (httpx.TimeoutException, httpx.RequestError):
                    evm_provider_manager.record_failure(self.chain_id, endpoint)

            attempts += 1
        return None

    async def validate_chain_id(self) -> bool:
        """
        Validates that the connected RPC returns the expected numeric chain ID.
        """
        res = await self.call_rpc("eth_chainId", [])
        if not res:
            return False
        numeric_id = int(res, 16)
        return numeric_id == self.expected_numeric_id
