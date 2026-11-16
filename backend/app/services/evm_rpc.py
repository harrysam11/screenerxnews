import httpx
from typing import Dict, Any, List, Optional
from app.core.chains import SUPPORTED_CHAINS
from app.services.quota_manager import quota_manager
from app.services.evm_provider_manager import evm_provider_manager
from app.core.network import get_http_client, OfflineModeBlockedException

class EvmRpcClient:
    """
    Robust JSON-RPC client for EVM networks with chain ID validation and automatic failover.
    Supported EVM networks are dynamically derived from the canonical SUPPORTED_CHAINS registry.
    """
    EXPECTED_CHAIN_IDS: Dict[str, int] = {
        c: int(c.split(":")[1])
        for c in SUPPORTED_CHAINS
        if c.startswith("eip155:")
    }

    def __init__(self, chain_id: str):
        if chain_id not in self.EXPECTED_CHAIN_IDS:
            raise ValueError(
                f"Unsupported EVM chain ID: '{chain_id}'. "
                f"Registered EVM networks ({len(self.EXPECTED_CHAIN_IDS)}): {sorted(self.EXPECTED_CHAIN_IDS.keys())}"
            )
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
            async with get_http_client(timeout=10.0) as client:
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
                except OfflineModeBlockedException:
                    raise
                except (httpx.TimeoutException, httpx.RequestError):
                    evm_provider_manager.record_failure(self.chain_id, endpoint)

            attempts += 1
        return None

    async def validate_chain_id(self) -> bool:
        """
        Validates that the connected RPC returns the expected numeric chain ID.
        Rejects and marks failing endpoints when mismatched chain IDs are detected.
        """
        res = await self.call_rpc("eth_chainId", [])
        if not res:
            return False
        try:
            numeric_id = int(res, 16) if isinstance(res, str) else int(res)
            if numeric_id != self.expected_numeric_id:
                active_ep = evm_provider_manager.get_active_endpoint(self.chain_id)
                if active_ep:
                    evm_provider_manager.record_failure(self.chain_id, active_ep)
                return False
            return True
        except (ValueError, TypeError):
            active_ep = evm_provider_manager.get_active_endpoint(self.chain_id)
            if active_ep:
                evm_provider_manager.record_failure(self.chain_id, active_ep)
            return False

    async def call_verified_rpc(self, method: str, params: list, max_retries: int = 2) -> Optional[Any]:
        """
        Validates the configured RPC's eth_chainId against the requested CAIP-2 network.
        Rejects mismatched RPC endpoints before returning chain data.
        """
        is_valid = await self.validate_chain_id()
        if not is_valid:
            return None
        return await self.call_rpc(method, params, max_retries=max_retries)
