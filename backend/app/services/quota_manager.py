import time
import asyncio
from typing import Dict, Optional

class TokenBucket:
    def __init__(self, capacity: int, fill_rate_per_sec: float):
        self.capacity = capacity
        self.fill_rate = fill_rate_per_sec
        self.tokens = float(capacity)
        self.last_update = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, tokens: int = 1) -> bool:
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self.last_update
            self.last_update = now
            self.tokens = min(float(self.capacity), self.tokens + elapsed * self.fill_rate)

            if self.tokens >= tokens:
                self.tokens -= tokens
                return True
            return False

    async def wait_for_token(self, tokens: int = 1, timeout: float = 10.0) -> bool:
        start = time.monotonic()
        while time.monotonic() - start < timeout:
            if await self.acquire(tokens):
                return True
            await asyncio.sleep(0.05)
        return False

class ApiQuotaManager:
    """
    Centralized rate limiter for all external provider integrations.
    Ceilings from specification:
    - DEX Screener Profiles/Boosts: 60 RPM (1 req/sec)
    - DEX Screener Pairs/Search: 300 RPM (5 req/sec)
    - GoPlus Solana Security: 30 RPM (0.5 req/sec)
    - Solana RPC (Public Shared): Conservative 10 RPS
    - Jupiter Quoting (Free Tier): 1 RPS
    """
    def __init__(self):
        self.buckets: Dict[str, TokenBucket] = {
            "dexscreener_profiles": TokenBucket(capacity=10, fill_rate_per_sec=1.0),
            "dexscreener_search": TokenBucket(capacity=30, fill_rate_per_sec=5.0),
            "goplus_security": TokenBucket(capacity=5, fill_rate_per_sec=0.5),
            "solana_rpc": TokenBucket(capacity=20, fill_rate_per_sec=10.0),
            "jupiter_quote": TokenBucket(capacity=2, fill_rate_per_sec=1.0),
        }
        self.backoff_until: Dict[str, float] = {}

    def is_cooling_down(self, provider: str) -> bool:
        cooldown = self.backoff_until.get(provider, 0)
        return time.monotonic() < cooldown

    def trigger_cooldown(self, provider: str, duration_sec: float = 30.0):
        self.backoff_until[provider] = time.monotonic() + duration_sec

    async def acquire_permit(self, provider: str, timeout: float = 5.0) -> bool:
        if self.is_cooling_down(provider):
            return False

        bucket = self.buckets.get(provider)
        if not bucket:
            return True
        return await bucket.wait_for_token(1, timeout=timeout)

quota_manager = ApiQuotaManager()
