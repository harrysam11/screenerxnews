import time
from typing import Dict, List, Optional, Any
from pydantic import BaseModel

class RpcEndpoint(BaseModel):
    url: str
    priority: int = 1
    weight: int = 100
    is_active: bool = True
    consecutive_failures: int = 0
    cooldown_until: float = 0.0

class EvmProviderManager:
    """
    Manages multi-RPC fallback, health monitoring, and cooldowns for EVM chains:
    - Ethereum (1)
    - Base (8453)
    - BNB Chain (56)
    """
    def __init__(self):
        self.providers: Dict[str, List[RpcEndpoint]] = {
            "eip155:1": [
                RpcEndpoint(url="https://eth.llamarpc.com", priority=1),
                RpcEndpoint(url="https://rpc.ankr.com/eth", priority=2)
            ],
            "eip155:8453": [
                RpcEndpoint(url="https://mainnet.base.org", priority=1),
                RpcEndpoint(url="https://base.llamarpc.com", priority=2)
            ],
            "eip155:56": [
                RpcEndpoint(url="https://bsc-dataseed.binance.org", priority=1),
                RpcEndpoint(url="https://binance.llamarpc.com", priority=2)
            ]
        }

    def get_active_endpoint(self, chain_id: str) -> Optional[str]:
        try:
            from app.services.provider_manager import provider_manager
            db_endpoint = provider_manager.get_active_rpc_endpoint(chain_id)
            if db_endpoint:
                return db_endpoint
        except Exception:
            pass

        endpoints = self.providers.get(chain_id, [])
        now = time.monotonic()
        for ep in sorted(endpoints, key=lambda x: x.priority):
            if ep.is_active and now >= ep.cooldown_until:
                return ep.url
        return endpoints[0].url if endpoints else None

    def record_failure(self, chain_id: str, url: str, is_rate_limit: bool = False):
        endpoints = self.providers.get(chain_id, [])
        for ep in endpoints:
            if ep.url == url:
                ep.consecutive_failures += 1
                cooldown = 60.0 if is_rate_limit else 15.0
                ep.cooldown_until = time.monotonic() + cooldown

    def record_success(self, chain_id: str, url: str):
        endpoints = self.providers.get(chain_id, [])
        for ep in endpoints:
            if ep.url == url:
                ep.consecutive_failures = 0
                ep.cooldown_until = 0.0

evm_provider_manager = EvmProviderManager()
