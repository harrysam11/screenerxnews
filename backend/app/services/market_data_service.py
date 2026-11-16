import asyncio
import logging
import math
import random
import time
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation
from typing import Dict, List, Optional, Any, Tuple, Union, NamedTuple


from app.core.config import settings
from app.core.chains import normalize_chain_id
from app.core.network import get_http_client, OfflineModeBlockedException
from app.schemas.market_data import (
    MarketDataFreshness,
    AssetCategory,
    ClassificationSource,
    ClassificationConfidence,
    MarketPairObservation,
    AggregatedTokenRanking,
    DynamicScreenerResponse,
    ProviderStatusDTO,
    MarketProvidersHealthResponse
)

logger = logging.getLogger(__name__)

SOLANA_CAIP2_CANONICAL = "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"
SOLANA_LEGACY_ALIAS = "solana:mainnet"

# CAIP-2 to Provider network identifier maps
CAIP2_TO_GECKO_NETWORK = {
    SOLANA_CAIP2_CANONICAL: "solana",
    SOLANA_LEGACY_ALIAS: "solana",
    "eip155:8453": "base",
    "eip155:1": "eth",
    "eip155:42161": "arbitrum"
}

GECKO_NETWORK_TO_CAIP2 = {
    "solana": SOLANA_CAIP2_CANONICAL,
    "base": "eip155:8453",
    "eth": "eip155:1",
    "arbitrum": "eip155:42161"
}

CAIP2_TO_DEXSCREENER_CHAIN = {
    SOLANA_CAIP2_CANONICAL: "solana",
    SOLANA_LEGACY_ALIAS: "solana",
    "eip155:8453": "base",
    "eip155:1": "ethereum",
    "eip155:42161": "arbitrum"
}

DEXSCREENER_CHAIN_TO_CAIP2 = {
    "solana": SOLANA_CAIP2_CANONICAL,
    "base": "eip155:8453",
    "ethereum": "eip155:1",
    "arbitrum": "eip155:42161"
}

# ---------------------------------------------------------------------------
# Token Classification Definitions
# ---------------------------------------------------------------------------

KNOWN_NON_MEME_SYMBOLS = {
    # Major native / wrapped native
    "SOL", "WSOL", "ETH", "WETH", "BTC", "WBTC", "TBTC", "CBBTC", "BNB", "WBNB",
    "AVAX", "WAVAX", "MATIC", "POL", "WMATIC", "ARB", "OP", "SUI", "APT", "TON",
    # Stablecoins
    "USDC", "USDT", "DAI", "EURC", "USDS", "FDUSD", "PYUSD", "FRAX", "BUSD",
    "TUSD", "USDG", "GUSD", "USD0", "USDE", "SUSDE", "USDBC", "CUSD", "CEUR",
    # Major DeFi & Infrastructure
    "UNI", "AAVE", "LINK", "JUP", "RAY", "ORCA", "AERO", "VELO", "MORPHO",
    "MKR", "MAKER", "COMP", "SNX", "CRV", "LDO", "RENDER", "HNT", "MOBILE",
    "JTO", "PYTH", "PENDLE", "ENA", "EIGEN", "SEI", "INJ", "TIA", "DYDX",
    # Tokenized Real-World Assets & Commodities
    "XAU", "XAUT", "PAXG", "XAG", "USDY", "BUIDL"
}

KNOWN_NON_MEME_ADDRESS_REGISTRY = {
    # Solana
    "So11111111111111111111111111111111111111112".lower(): "Wrapped SOL",
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v".lower(): "USDC (Solana)",
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB".lower(): "USDT (Solana)",
    "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN".lower(): "Jupiter (JUP)",
    "4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R".lower(): "Raydium (RAY)",
    "rndrizKT3MK1iimdxRdWabcF7Zg7AR5T4nud4EkHBof".lower(): "Render (RENDER)",
    "jtojtomepa8beP8AuQc6eXt5FriJwfFMwQx2v2f9mCL".lower(): "Jito (JTO)",
    "HZ1JovNiDcZvKhVkfdnfga7avMzsR88YP2hJ45Lnj758".lower(): "Pyth Network (PYTH)",
    # Base
    "0x4200000000000000000000000000000000000006".lower(): "Wrapped Ether (WETH - Base)",
    "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913".lower(): "USD Coin (USDC - Base)",
    "0xd9aAEc86B65D86f6A7B5B1b0c42FFA531710b6CA".lower(): "USD Base Coin (USDbC)",
    "0xcbB7C0000aB88B473b1f5aFd9ef808440eed33Bf".lower(): "Coinbase Wrapped BTC (cbBTC)",
    "0x940181a94A35A4569E4529A3CDfB74e38FD98631".lower(): "Aerodrome Finance (AERO)",
    "0x50c5725949A6F0c72E6C4a641F24049A917DB0Cb".lower(): "Dai Stablecoin (DAI - Base)",
    # Ethereum (eip155:1)
    "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2".lower(): "Wrapped Ether (WETH - Ethereum)",
    "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48".lower(): "USD Coin (USDC - Ethereum)",
    "0xdAC17F958D2ee523a2206206994597C13D831ec7".lower(): "Tether USD (USDT - Ethereum)",
    "0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599".lower(): "Wrapped BTC (WBTC - Ethereum)",
    "0x6B175474E89094C44Da98b954EedeAC495271d0F".lower(): "Dai Stablecoin (DAI - Ethereum)",
    # Arbitrum (eip155:42161)
    "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1".lower(): "Wrapped Ether (WETH - Arbitrum)",
    "0xaf88d065e77c8cC2239327C5EDb3A432268e5831".lower(): "USD Coin (USDC - Arbitrum)",
    "0xFF970A61A04b1cA14834A43f5dE4533eBDDB5CC8".lower(): "USD Coin Bridged (USDC.e - Arbitrum)",
    "0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9".lower(): "Tether USD (USDT - Arbitrum)",
    "0x912CE59144191C1204E64559FE8253a0e49E6548".lower(): "Arbitrum (ARB - Arbitrum)",
}
KNOWN_NON_MEME_ADDRESSES = set(KNOWN_NON_MEME_ADDRESS_REGISTRY.keys())

TOKENIZED_EQUITY_PREFIXES = {
    "NVDA", "AAPL", "TSLA", "MSFT", "AMZN", "GOOG", "GOOGL", "META",
    "SPY", "QQQ", "COIN", "PLTR", "AMD", "NFLX", "BRK", "DIS", "BA"
}

KNOWN_MEME_SYMBOLS = {
    "BONK", "WIF", "PEPE", "DOGE", "SHIB", "FLOKI", "POPCAT", "BRETT",
    "TOSHI", "DEGEN", "MOODENG", "GOAT", "PNUT", "ACT", "SPX", "GIGA",
    "FARTCOIN", "BOME", "MEW", "SLERF", "PONKE", "WEN", "NEIRO", "TURBO",
    "CHILLGUY", "ZEREBRO", "AI16Z", "GRIFFAIN", "FART", "MICHI", "LOCKIN",
    "RETARDIO", "BODEN", "TREMP", "TRUMP", "MELANIA", "DOGGO", "SAMO",
    "MYRO", "SILLY", "KEYCAT", "MOG", "HIGHER", "NORMIE", "SKI", "SKIETH",
    "BENJI", "BOYS", "TYBG", "BOB", "WOJAK", "CHAD", "BULL", "BEAR",
    "PUPS", "MOTHER", "DADDY", "IGGY", "BILLY", "FWOG", "AIDOGE"
}

KNOWN_MEME_ADDRESS_REGISTRY = {
    # Solana
    "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263".lower(): "Bonk (BONK)",
    "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm".lower(): "dogwifhat (WIF)",
    "ukHH6c7mMyiWCf1b9pnWe25TSpkDDt3H5pQZgZ74J82".lower(): "BOOK OF MEME (BOME)",
    "MEW1gQWJ3nEXg2qgERiKu7FAFj79PHvQVREQUzScPP5".lower(): "cat in a dogs world (MEW)",
    "7GCihgDB8fe6KNjn2MYtkzZcRjQy3t9GHdC8uHYmW2hr".lower(): "POPCAT",
    # Base
    "0x532f27101965dd16442e59d40670faf5ebb142e4".lower(): "Brett (BRETT)",
    "0xAC1Bd2486aAf3B5C0fc3Fd868558b082a531B2B4".lower(): "Toshi (TOSHI)",
    "0x4ed4E862860be51a91bD929D1c8B3985ee120423".lower(): "Degen (DEGEN)",
    # Ethereum (eip155:1)
    "0x6982508145454Ce325dDbE47a25d4ec3d2311933".lower(): "Pepe (PEPE)",
    "0x95aD61b0a150d79219dCF64E1E6Cc01f0B64C4cE".lower(): "Shiba Inu (SHIB)",
    "0xcf0C122c6b73380ea4829999a748a1cd3E62f55F".lower(): "Floki (FLOKI)",
    # Arbitrum (eip155:42161)
    "0x250632378E573c6Be1AC2f97FCDF00515d0Aa91B".lower(): "ArbDoge AI (AIDOGE)",
}
KNOWN_MEME_ADDRESSES = set(KNOWN_MEME_ADDRESS_REGISTRY.keys())


class TokenClassification(NamedTuple):
    category: AssetCategory
    source: ClassificationSource
    confidence: ClassificationConfidence
    reason: str

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, (AssetCategory, str)):
            return self.category == other
        if isinstance(other, tuple):
            return tuple(self) == other
        return False


def classify_token(
    chain_id: str,
    token_address: str,
    symbol: Optional[str] = None,
    name: Optional[str] = None
) -> TokenClassification:
    """
    Deterministically classifies a discovered token into MEME, NON_MEME, or UNKNOWN
    with provenance tracking (source, confidence, reason).
    Quote-token identity MUST NOT be checked here (base token only).

    Precedence:
    1. Curated address registry matches (chain + contract-address preferred over ticker).
    2. Solana pump.fun address heuristic (mint ending in 'pump').
    3. Tokenized equity ticker heuristic (e.g. NVDAc, AAPLc).
    4. Curated ticker symbol registry matches.
    5. Meme keyword patterns in name/symbol.
    6. Default fallback to UNKNOWN (not automatically NON_MEME).
    """
    clean_addr = (token_address or "").strip().lower()
    clean_sym = (symbol or "").strip().upper()
    clean_name = (name or "").strip().lower()

    # 1. Address-level curated registry matches (prefer contract address over ticker)
    if clean_addr in KNOWN_NON_MEME_ADDRESS_REGISTRY:
        label = KNOWN_NON_MEME_ADDRESS_REGISTRY[clean_addr]
        return TokenClassification(
            category=AssetCategory.NON_MEME,
            source=ClassificationSource.CURATED_REGISTRY,
            confidence=ClassificationConfidence.HIGH,
            reason=f"Curated contract address match ({label}) in non-meme registry"
        )
    if clean_addr in KNOWN_MEME_ADDRESS_REGISTRY:
        label = KNOWN_MEME_ADDRESS_REGISTRY[clean_addr]
        return TokenClassification(
            category=AssetCategory.MEME,
            source=ClassificationSource.CURATED_REGISTRY,
            confidence=ClassificationConfidence.HIGH,
            reason=f"Curated contract address match ({label}) in memecoin registry"
        )

    # 2. Solana pump.fun heuristic (mint address ends with 'pump') - EXPLICITLY HEURISTIC
    if (chain_id.startswith("solana") or "solana" in chain_id.lower()) and clean_addr.endswith("pump"):
        return TokenClassification(
            category=AssetCategory.MEME,
            source=ClassificationSource.HEURISTIC,
            confidence=ClassificationConfidence.MEDIUM,
            reason="Heuristic match: Solana pump.fun mint address pattern (*pump)"
        )

    # 3. Tokenized equity heuristic (e.g. NVDAc, AAPLc, TSLAx) - EXPLICITLY HEURISTIC
    for eq in TOKENIZED_EQUITY_PREFIXES:
        if clean_sym in (f"{eq}C", f"{eq}X", f"{eq}.C", f"{eq}.X"):
            return TokenClassification(
                category=AssetCategory.NON_MEME,
                source=ClassificationSource.HEURISTIC,
                confidence=ClassificationConfidence.MEDIUM,
                reason=f"Heuristic match: Tokenized equity symbol pattern ({clean_sym})"
            )

    # 4. Ticker-only registry match (EXPLICITLY HEURISTIC / MEDIUM confidence - tickers can be spoofed or duplicated)
    if clean_sym in KNOWN_NON_MEME_SYMBOLS:
        return TokenClassification(
            category=AssetCategory.NON_MEME,
            source=ClassificationSource.HEURISTIC,
            confidence=ClassificationConfidence.MEDIUM,
            reason=f"Unverified contract address; ticker symbol match only ({clean_sym})"
        )
    if clean_sym in KNOWN_MEME_SYMBOLS:
        return TokenClassification(
            category=AssetCategory.MEME,
            source=ClassificationSource.HEURISTIC,
            confidence=ClassificationConfidence.MEDIUM,
            reason=f"Unverified contract address; ticker symbol match only ({clean_sym})"
        )

    # 5. Meme heuristic in name / symbol
    meme_keywords = ["pepe", "doge", "shib", "inu", "wif", "wojak", "bonk", "frog"]
    for kw in meme_keywords:
        if kw in clean_sym.lower() or kw in clean_name:
            return TokenClassification(
                category=AssetCategory.MEME,
                source=ClassificationSource.HEURISTIC,
                confidence=ClassificationConfidence.MEDIUM,
                reason=f"Heuristic match: Name/symbol contains meme keyword '{kw}'"
            )

    # 6. Default to UNKNOWN (do NOT assume unknown is a non-meme or meme)
    return TokenClassification(
        category=AssetCategory.UNKNOWN,
        source=ClassificationSource.DEFAULT,
        confidence=ClassificationConfidence.LOW,
        reason="Unclassified asset (no curated registry or heuristic match)"
    )


class TokenBucketRateLimiter:
    """
    Thread-safe / asyncio-safe token-bucket rate limiter.
    """
    def __init__(self, requests_per_minute: int = 10, rate_per_minute: Optional[int] = None, burst: Optional[int] = None):
        rpm = rate_per_minute if rate_per_minute is not None else requests_per_minute
        cap = float(burst) if burst is not None else float(rpm)
        self.capacity = cap
        self.tokens = cap
        self.refill_rate = float(rpm) / 60.0
        self.last_update = time.monotonic()
        self._lock = asyncio.Lock()


    async def acquire(self) -> bool:
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self.last_update
            self.last_update = now
            self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
            if self.tokens >= 1.0:
                self.tokens -= 1.0
                return True
            return False

    def get_remaining(self) -> int:
        now = time.monotonic()
        elapsed = now - self.last_update
        current_tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
        return max(0, int(current_tokens))


class CircuitBreaker:
    """
    3-state circuit breaker: CLOSED (normal), OPEN (broken), HALF_OPEN (probing).
    """
    def __init__(self, failure_threshold: int = 3, reset_timeout_seconds: float = 60.0, cooldown_seconds: Optional[float] = None):
        self.failure_threshold = failure_threshold
        self.reset_timeout = cooldown_seconds if cooldown_seconds is not None else reset_timeout_seconds

        self.state = "CLOSED"
        self.consecutive_failures = 0
        self.last_state_change = time.monotonic()
        self.last_failure_reason: Optional[str] = None

    def can_execute(self) -> bool:
        if self.state == "CLOSED":
            return True
        now = time.monotonic()
        if self.state == "OPEN":
            if now - self.last_state_change >= self.reset_timeout:
                self.state = "HALF_OPEN"
                self.last_state_change = now
                logger.info("Circuit breaker transitioning from OPEN to HALF_OPEN")
                return True
            return False
        if self.state == "HALF_OPEN":
            return True
        return False

    def record_success(self):
        self.consecutive_failures = 0
        if self.state != "CLOSED":
            logger.info("Circuit breaker reset to CLOSED following successful probe")
            self.state = "CLOSED"
            self.last_state_change = time.monotonic()
            self.last_failure_reason = None

    def record_failure(self, reason: str):
        self.consecutive_failures += 1
        self.last_failure_reason = reason
        now = time.monotonic()
        if self.state == "HALF_OPEN" or self.consecutive_failures >= self.failure_threshold:
            self.state = "OPEN"
            self.last_state_change = now
            logger.warning(f"Circuit breaker tripped to OPEN (consecutive failures: {self.consecutive_failures}, reason: {reason})")


def _to_decimal(val: Any) -> Optional[Decimal]:
    if val is None:
        return None
    try:
        s = str(val).strip()
        if not s or s.lower() in ("null", "none", "nan"):
            return None
        return Decimal(s)
    except (InvalidOperation, TypeError, ValueError):
        return None


class GeckoTerminalClient:
    """
    GeckoTerminal public API client.
    Base URL: https://api.geckoterminal.com/api/v2
    Accept: application/json;version=20230302
    Conservative rate budget: 10 requests / minute
    """
    BASE_URL = "https://api.geckoterminal.com/api/v2"
    ACCEPT_HEADER = "application/json;version=20230302"
    USER_AGENT = "MemecoinOS-Intelligence/1.0"

    def __init__(self, base_url: Optional[str] = None):
        self.base_url = base_url or self.BASE_URL
        self.rate_limiter = TokenBucketRateLimiter(requests_per_minute=10)
        self.circuit_breaker = CircuitBreaker(failure_threshold=3, reset_timeout_seconds=60.0)
        self.last_successful_fetch: Optional[datetime] = None
        self.last_attempted_fetch: Optional[datetime] = None
        self.last_latency_ms: Optional[float] = None
        self.error_category: Optional[str] = None

    async def fetch_pools(self, network: str, endpoint_type: str = "trending_pools") -> List[MarketPairObservation]:
        """
        Fetches pools for a given provider network ('solana' or 'base').
        endpoint_type can be 'trending_pools' or 'pools'.
        """
        self.last_attempted_fetch = datetime.now(timezone.utc)
        caip2 = GECKO_NETWORK_TO_CAIP2.get(network, f"unknown:{network}")

        if not self.circuit_breaker.can_execute():
            self.error_category = "CIRCUIT_BREAKER_OPEN"
            logger.warning(f"GeckoTerminal circuit breaker is OPEN for {network}")
            return []

        if not await self.rate_limiter.acquire():
            self.error_category = "RATE_LIMIT_EXHAUSTED"
            logger.warning("GeckoTerminal client rate limit budget exhausted (10 RPM)")
            return []

        url = f"{self.base_url}/networks/{network}/{endpoint_type}?page=1"
        headers = {
            "Accept": self.ACCEPT_HEADER,
            "User-Agent": self.USER_AGENT
        }

        start_t = time.monotonic()
        max_retries = 2
        for attempt in range(max_retries + 1):
            try:
                async with get_http_client(timeout=10.0) as client:
                    resp = await client.get(url, headers=headers)
                    self.last_latency_ms = (time.monotonic() - start_t) * 1000.0

                    if resp.status_code == 200:
                        self.circuit_breaker.record_success()
                        self.last_successful_fetch = datetime.now(timezone.utc)
                        self.error_category = None
                        data = resp.json()
                        return self._parse_pools_response(data, network, caip2, endpoint_type=endpoint_type)
                    elif resp.status_code == 429:
                        self.error_category = "HTTP_429_RATE_LIMITED"
                        if attempt < max_retries:
                            backoff = (0.5 * (2 ** attempt)) + random.uniform(0.1, 0.4)
                            await asyncio.sleep(backoff)
                            continue
                        self.circuit_breaker.record_failure("HTTP_429_RATE_LIMITED")
                        return []
                    elif resp.status_code >= 500:
                        self.error_category = f"HTTP_{resp.status_code}_SERVER_ERROR"
                        if attempt < max_retries:
                            backoff = (0.5 * (2 ** attempt)) + random.uniform(0.1, 0.4)
                            await asyncio.sleep(backoff)
                            continue
                        self.circuit_breaker.record_failure(f"HTTP_{resp.status_code}")
                        return []
                    else:
                        self.error_category = f"HTTP_{resp.status_code}"
                        self.circuit_breaker.record_failure(f"HTTP_{resp.status_code}")
                        return []
            except OfflineModeBlockedException:
                self.error_category = "OFFLINE_MODE_BLOCKED"
                return []
            except Exception as e:
                self.error_category = type(e).__name__
                if attempt < max_retries:
                    backoff = (0.5 * (2 ** attempt)) + random.uniform(0.1, 0.4)
                    await asyncio.sleep(backoff)
                    continue
                self.circuit_breaker.record_failure(str(e))
                return []
        return []

    async def fetch_ohlcv_candles(
        self,
        network: str,
        pool_address: str,
        timeframe: str = "15m",
        before_timestamp: Optional[int] = None,
        limit: int = 100,
        currency: str = "usd"
    ) -> Dict[str, Any]:
        """
        Fetches historical OHLCV candlestick series for a liquidity pool.
        Endpoint: /networks/{network}/pools/{pool_address}/ohlcv/{timeframe}
        Distinguishes OFFLINE / PROVIDER_UNAVAILABLE states from empty market data.
        """
        self.last_attempted_fetch = datetime.now(timezone.utc)
        caip2 = GECKO_NETWORK_TO_CAIP2.get(network, f"unknown:{network}")

        if not self.circuit_breaker.can_execute():
            self.error_category = "CIRCUIT_BREAKER_OPEN"
            logger.warning("GeckoTerminal circuit breaker is OPEN for OHLCV on %s:%s", network, pool_address)
            return {"status": "PROVIDER_UNAVAILABLE", "error": "CIRCUIT_BREAKER_OPEN", "candles": []}

        if not await self.rate_limiter.acquire():
            self.error_category = "RATE_LIMIT_EXHAUSTED"
            logger.warning("GeckoTerminal client rate limit budget exhausted for OHLCV")
            return {"status": "PROVIDER_UNAVAILABLE", "error": "RATE_LIMIT_EXHAUSTED", "candles": []}

        # Resolve timeframe & aggregate query parameters
        from app.schemas.ohlcv import Timeframe
        try:
            tf_obj = Timeframe(timeframe)
            gt_tf = tf_obj.gecko_timeframe
            gt_agg = tf_obj.gecko_aggregate
        except Exception:
            gt_tf = "minute"
            gt_agg = 15

        url = f"{self.base_url}/networks/{network}/pools/{pool_address}/ohlcv/{gt_tf}"
        params: Dict[str, Any] = {
            "aggregate": gt_agg,
            "currency": currency,
            "limit": min(max(limit, 1), 1000)
        }
        if before_timestamp:
            params["before_timestamp"] = before_timestamp

        headers = {
            "Accept": self.ACCEPT_HEADER,
            "User-Agent": self.USER_AGENT
        }

        start_t = time.monotonic()
        max_retries = 2
        for attempt in range(max_retries + 1):
            try:
                async with get_http_client(timeout=10.0) as client:
                    resp = await client.get(url, params=params, headers=headers)
                    self.last_latency_ms = (time.monotonic() - start_t) * 1000.0

                    if resp.status_code == 200:
                        self.circuit_breaker.record_success()
                        self.last_successful_fetch = datetime.now(timezone.utc)
                        self.error_category = None
                        data = resp.json()
                        raw_list = data.get("data", {}).get("attributes", {}).get("ohlcv_list", [])
                        parsed = []
                        for bar in raw_list:
                            # Format: [timestamp, open, high, low, close, volume]
                            if isinstance(bar, (list, tuple)) and len(bar) >= 6:
                                b_ts = int(bar[0])
                                dt_start = datetime.fromtimestamp(b_ts, tz=timezone.utc)
                                dt_end = dt_start + timedelta(seconds=tf_obj.seconds)
                                parsed.append({
                                    "chain_id": caip2,
                                    "pool_address": pool_address.lower(),
                                    "timeframe": timeframe,
                                    "timestamp_utc": dt_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                    "timestamp_unix": b_ts,
                                    "end_timestamp_utc": dt_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                    "open_price": str(bar[1]),
                                    "high_price": str(bar[2]),
                                    "low_price": str(bar[3]),
                                    "close_price": str(bar[4]),
                                    "volume": str(bar[5]),
                                    "price_currency": currency.upper(),
                                    "volume_currency": currency.upper(),
                                    "provider": "geckoterminal",
                                    "provider_version": "v2_20230302"
                                })
                        return {"status": "OPERATIONAL", "candles": parsed}
                    elif resp.status_code == 429:
                        self.error_category = "HTTP_429_RATE_LIMITED"
                        if attempt < max_retries:
                            backoff = (0.5 * (2 ** attempt)) + random.uniform(0.1, 0.4)
                            await asyncio.sleep(backoff)
                            continue
                        self.circuit_breaker.record_failure("HTTP_429_RATE_LIMITED")
                        return {"status": "PROVIDER_UNAVAILABLE", "error": "HTTP_429_RATE_LIMITED", "candles": []}
                    elif resp.status_code == 404:
                        return {"status": "POOL_NOT_FOUND", "candles": []}
                    else:
                        self.error_category = f"HTTP_{resp.status_code}"
                        self.circuit_breaker.record_failure(f"HTTP_{resp.status_code}")
                        return {"status": "PROVIDER_UNAVAILABLE", "error": f"HTTP_{resp.status_code}", "candles": []}
            except OfflineModeBlockedException:
                self.error_category = "OFFLINE_MODE_BLOCKED"
                return {"status": "OFFLINE", "candles": []}
            except Exception as e:
                self.error_category = type(e).__name__
                if attempt < max_retries:
                    backoff = (0.5 * (2 ** attempt)) + random.uniform(0.1, 0.4)
                    await asyncio.sleep(backoff)
                    continue
                self.circuit_breaker.record_failure(str(e))
                return {"status": "PROVIDER_UNAVAILABLE", "error": str(e), "candles": []}
        return {"status": "PROVIDER_UNAVAILABLE", "error": "MAX_RETRIES_EXCEEDED", "candles": []}

    def _parse_pools_response(self, data: Dict[str, Any], network: str, caip2: str, endpoint_type: str = "trending_pools") -> List[MarketPairObservation]:
        results: List[MarketPairObservation] = []
        if not isinstance(data, dict):
            return results
        now = datetime.now(timezone.utc)
        pool_list = data.get("data", [])
        if not isinstance(pool_list, list):
            return results

        endpoint_id = f"geckoterminal:{endpoint_type}"

        for item in pool_list:
            try:
                attrs = item.get("attributes", {})
                rels = item.get("relationships", {})

                pool_address = attrs.get("address")
                if not pool_address:
                    continue

                # Parse base token and quote token addresses
                base_token_id = rels.get("base_token", {}).get("data", {}).get("id", "")
                quote_token_id = rels.get("quote_token", {}).get("data", {}).get("id", "")
                dex_id = rels.get("dex", {}).get("data", {}).get("id", "dex")

                prefix = f"{network}_"
                token_address = base_token_id[len(prefix):] if base_token_id.startswith(prefix) else base_token_id
                quote_address = quote_token_id[len(prefix):] if quote_token_id.startswith(prefix) else quote_token_id

                if not token_address:
                    continue

                # Parse name and symbols
                name = attrs.get("name", "")
                sym = None
                quote_sym = None
                if "/" in name:
                    parts = name.split("/")
                    sym = parts[0].strip()
                    quote_sym = parts[1].strip()
                elif name:
                    sym = name.strip()

                price_usd = _to_decimal(attrs.get("base_token_price_usd"))
                price_native = _to_decimal(attrs.get("base_token_price_native_currency"))
                raw_liq = attrs.get("reserve_in_usd")
                liquidity_usd = _to_decimal(raw_liq)

                v_map = attrs.get("volume_usd") or {}
                vol_5m = _to_decimal(v_map.get("m5"))
                vol_1h = _to_decimal(v_map.get("h1"))
                vol_6h = _to_decimal(v_map.get("h6"))
                vol_24h = _to_decimal(v_map.get("h24"))

                pc_map = attrs.get("price_change_percentage") or {}
                pc_5m = _to_decimal(pc_map.get("m5"))
                pc_1h = _to_decimal(pc_map.get("h1"))
                pc_6h = _to_decimal(pc_map.get("h6"))
                pc_24h = _to_decimal(pc_map.get("h24"))

                tx_map = attrs.get("transactions") or {}
                tx_5m = tx_map.get("m5") or {}
                tx_1h = tx_map.get("h1") or {}
                tx_24h = tx_map.get("h24") or {}

                fdv_usd = _to_decimal(attrs.get("fdv_usd"))
                mcap_usd = _to_decimal(attrs.get("market_cap_usd"))

                # Parse pool creation timestamp
                created_str = attrs.get("pool_created_at")
                pair_created_at = None
                if created_str:
                    try:
                        pair_created_at = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
                    except Exception:
                        pass

                missing_fields = []
                if price_usd is None:
                    missing_fields.append("price_usd")
                if price_native is None:
                    missing_fields.append("price_native")
                if liquidity_usd is None:
                    missing_fields.append("liquidity_usd")
                if vol_24h is None:
                    missing_fields.append("volume_24h")
                    missing_fields.append("volume_24h_usd")
                if pc_24h is None:
                    missing_fields.append("price_change_24h")
                if pair_created_at is None:
                    missing_fields.append("pair_created_at")

                clf = classify_token(caip2, token_address, sym, name)
                pool_url = f"https://www.geckoterminal.com/{network}/pools/{pool_address}"

                obs = MarketPairObservation(
                    chain_id=caip2,
                    provider_network_id=network,
                    token_address=token_address,
                    pair_address=pool_address,
                    name=name,
                    symbol=sym,
                    dex_name=dex_id,
                    quote_token_address=quote_address or None,
                    quote_token_symbol=quote_sym,
                    asset_category=clf.category,
                    classification_source=clf.source,
                    confidence=clf.confidence,
                    classification_reason=clf.reason,
                    price_usd=price_usd,
                    price_native=price_native,
                    liquidity_usd=liquidity_usd,
                    volume_5m=vol_5m,
                    volume_1h=vol_1h,
                    volume_6h=vol_6h,
                    volume_24h=vol_24h,
                    txns_5m_buys=tx_5m.get("buys"),
                    txns_5m_sells=tx_5m.get("sells"),
                    txns_1h_buys=tx_1h.get("buys"),
                    txns_1h_sells=tx_1h.get("sells"),
                    txns_24h_buys=tx_24h.get("buys"),
                    txns_24h_sells=tx_24h.get("sells"),
                    price_change_5m_pct=pc_5m,
                    price_change_1h_pct=pc_1h,
                    price_change_6h_pct=pc_6h,
                    price_change_24h_pct=pc_24h,
                    fdv_usd=fdv_usd,
                    market_cap_usd=mcap_usd,
                    pair_created_at=pair_created_at,
                    provider_source="geckoterminal",
                    endpoint_id=endpoint_id,
                    provider_url=pool_url,
                    provider_urls=[pool_url],
                    observation_timestamps={endpoint_id: now},
                    fetched_at=now,
                    observed_at=now,
                    cached_at=now,
                    cache_age_seconds=0.0,
                    freshness_status=MarketDataFreshness.FRESH,
                    missing_fields=missing_fields,
                    is_valid=True
                )
                results.append(obs)
            except Exception as e:
                logger.debug(f"Failed to parse pool item from GeckoTerminal: {e}")
                continue

        return results

    # Backward-compatible alias for unit test assertions
    _parse_gecko_pools = _parse_pools_response



class DexScreenerClient:
    """
    DEX Screener public API client for enrichment.
    Base URL: https://api.dexscreener.com
    Rate limit budget: 60 requests / minute
    """
    BASE_URL = "https://api.dexscreener.com"
    USER_AGENT = "MemecoinOS-Intelligence/1.0"

    def __init__(self, base_url: Optional[str] = None):
        self.base_url = base_url or self.BASE_URL
        self.rate_limiter = TokenBucketRateLimiter(requests_per_minute=60)
        self.circuit_breaker = CircuitBreaker(failure_threshold=3, reset_timeout_seconds=60.0)
        self.last_successful_fetch: Optional[datetime] = None
        self.last_attempted_fetch: Optional[datetime] = None
        self.last_latency_ms: Optional[float] = None
        self.error_category: Optional[str] = None

    async def get_token_pairs_batch(self, chain_id: str, token_addresses: List[str]) -> List[MarketPairObservation]:
        """
        Enriches a batch of token addresses on 'solana' or 'base'.
        Endpoint: /tokens/v1/{chainId}/{tokenAddresses}
        """
        if not token_addresses:
            return []

        self.last_attempted_fetch = datetime.now(timezone.utc)
        caip2 = DEXSCREENER_CHAIN_TO_CAIP2.get(chain_id) or GECKO_NETWORK_TO_CAIP2.get(chain_id, f"unknown:{chain_id}")

        if not self.circuit_breaker.can_execute():
            self.error_category = "CIRCUIT_BREAKER_OPEN"
            return []

        if not await self.rate_limiter.acquire():
            self.error_category = "RATE_LIMIT_EXHAUSTED"
            return []

        joined_addrs = ",".join(token_addresses[:30])
        url = f"{self.base_url}/tokens/v1/{chain_id}/{joined_addrs}"
        headers = {"User-Agent": self.USER_AGENT}

        start_t = time.monotonic()
        try:
            async with get_http_client(timeout=10.0) as client:
                resp = await client.get(url, headers=headers)
                self.last_latency_ms = (time.monotonic() - start_t) * 1000.0

                if resp.status_code == 200:
                    self.circuit_breaker.record_success()
                    self.last_successful_fetch = datetime.now(timezone.utc)
                    self.error_category = None
                    pairs_data = resp.json()
                    return self._parse_dexscreener_pairs(pairs_data, chain_id, caip2)
                else:
                    self.error_category = f"HTTP_{resp.status_code}"
                    self.circuit_breaker.record_failure(f"HTTP_{resp.status_code}")
                    return []
        except OfflineModeBlockedException:
            self.error_category = "OFFLINE_MODE_BLOCKED"
            return []
        except Exception as e:
            self.error_category = type(e).__name__
            self.circuit_breaker.record_failure(str(e))
            return []

    def _parse_dexscreener_pairs(self, pairs: List[Dict[str, Any]], chain_id: str, caip2: str) -> List[MarketPairObservation]:
        results: List[MarketPairObservation] = []
        now = datetime.now(timezone.utc)
        if not isinstance(pairs, list):
            return results

        for p in pairs:
            try:
                base_tok = p.get("baseToken") or {}
                quote_tok = p.get("quoteToken") or {}
                tok_addr = base_tok.get("address")
                pair_addr = p.get("pairAddress")
                if not (tok_addr and pair_addr):
                    continue

                price_usd = _to_decimal(p.get("priceUsd"))
                if price_usd is None:
                    continue

                price_native = _to_decimal(p.get("priceNative"))
                liq_obj = p.get("liquidity") or {}
                liquidity_usd = _to_decimal(liq_obj.get("usd")) or Decimal("0.0")

                vol_obj = p.get("volume") or {}
                vol_5m = _to_decimal(vol_obj.get("m5"))
                vol_1h = _to_decimal(vol_obj.get("h1"))
                vol_6h = _to_decimal(vol_obj.get("h6"))
                vol_24h = _to_decimal(vol_obj.get("h24"))

                pc_obj = p.get("priceChange") or {}
                pc_5m = _to_decimal(pc_obj.get("m5"))
                pc_1h = _to_decimal(pc_obj.get("h1"))
                pc_6h = _to_decimal(pc_obj.get("h6"))
                pc_24h = _to_decimal(pc_obj.get("h24"))

                tx_obj = p.get("txns") or {}
                tx_5m = tx_obj.get("m5") or {}
                tx_1h = tx_obj.get("h1") or {}
                tx_24h = tx_obj.get("h24") or {}

                pair_created_ms = p.get("pairCreatedAt")
                pair_created_at = None
                if pair_created_ms and isinstance(pair_created_ms, (int, float)):
                    pair_created_at = datetime.fromtimestamp(pair_created_ms / 1000.0, timezone.utc)

                clf = classify_token(caip2, tok_addr, base_tok.get("symbol"), base_tok.get("name"))
                endpoint_id = "dexscreener:tokens_batch"
                ds_url = p.get("url")
                ds_urls = [ds_url] if ds_url else []

                obs = MarketPairObservation(
                    chain_id=caip2,
                    provider_network_id=chain_id,
                    token_address=tok_addr,
                    pair_address=pair_addr,
                    name=base_tok.get("name"),
                    symbol=base_tok.get("symbol"),
                    dex_name=p.get("dexId", "unknown"),
                    quote_token_address=quote_tok.get("address"),
                    quote_token_symbol=quote_tok.get("symbol"),
                    asset_category=clf.category,
                    classification_source=clf.source,
                    confidence=clf.confidence,
                    classification_reason=clf.reason,
                    price_usd=price_usd,
                    price_native=price_native,
                    liquidity_usd=liquidity_usd,
                    volume_5m=vol_5m,
                    volume_1h=vol_1h,
                    volume_6h=vol_6h,
                    volume_24h=vol_24h,
                    txns_5m_buys=tx_5m.get("buys"),
                    txns_5m_sells=tx_5m.get("sells"),
                    txns_1h_buys=tx_1h.get("buys"),
                    txns_1h_sells=tx_1h.get("sells"),
                    txns_24h_buys=tx_24h.get("buys"),
                    txns_24h_sells=tx_24h.get("sells"),
                    price_change_5m_pct=pc_5m,
                    price_change_1h_pct=pc_1h,
                    price_change_6h_pct=pc_6h,
                    price_change_24h_pct=pc_24h,
                    fdv_usd=_to_decimal(p.get("fdv")),
                    market_cap_usd=_to_decimal(p.get("marketCap")),
                    pair_created_at=pair_created_at,
                    provider_source="dexscreener",
                    endpoint_id=endpoint_id,
                    provider_url=ds_url,
                    provider_urls=ds_urls,
                    observation_timestamps={endpoint_id: now},
                    fetched_at=now,
                    observed_at=now,
                    cached_at=now,
                    cache_age_seconds=0.0,
                    freshness_status=MarketDataFreshness.FRESH,
                    missing_fields=[],
                    is_valid=True
                )
                results.append(obs)
            except Exception as e:
                logger.debug(f"Failed to parse DEX Screener pair: {e}")
                continue

        return results


class NormalizedTokenDict(dict):
    """
    Dictionary keyed by (chain_id, token_address) that normalizes chain_id
    and token_address for transparent canonical and legacy alias lookups.
    """
    def __getitem__(self, key):
        if isinstance(key, tuple) and len(key) == 2:
            chain, addr = key
            norm_key = (normalize_chain_id(str(chain)), str(addr).lower())
            if super().__contains__(norm_key):
                return super().__getitem__(norm_key)
        return super().__getitem__(key)

    def __setitem__(self, key, value):
        if isinstance(key, tuple) and len(key) == 2:
            chain, addr = key
            norm_key = (normalize_chain_id(str(chain)), str(addr).lower())
            return super().__setitem__(norm_key, value)
        return super().__setitem__(key, value)

    def __contains__(self, key):
        if isinstance(key, tuple) and len(key) == 2:
            chain, addr = key
            norm_key = (normalize_chain_id(str(chain)), str(addr).lower())
            if super().__contains__(norm_key):
                return True
        return super().__contains__(key)

    def get(self, key, default=None):
        if isinstance(key, tuple) and len(key) == 2:
            chain, addr = key
            norm_key = (normalize_chain_id(str(chain)), str(addr).lower())
            if super().__contains__(norm_key):
                return super().__getitem__(norm_key)
        return super().get(key, default)


class MarketDataCoordinator:
    """
    Central Market Data Coordinator & In-Memory Screener Engine (Phase P1.2).
    - Multi-chain discovery: Solana, Base, Ethereum, Arbitrum.
    - Multi-endpoint discovery: Trending pools and newly created pools.
    - Cross-provider pool deduplication and multi-pool token aggregation.
    - Preserves endpoint-level source IDs, provider URLs, and observation timestamps.
    - Canonical pool elected by MAX liquidity; volume aggregated strictly across distinct pools.
    - Resilient degradation: GeckoTerminal discovery continues if DEX Screener fails.
    - Fast, deterministic filtered/sorted screener rankings with pagination.
    """
    def __init__(
        self,
        gecko_client: Optional[GeckoTerminalClient] = None,
        dex_client: Optional[DexScreenerClient] = None,
        cache_ttl_seconds: float = 60.0
    ):
        self.gecko = gecko_client or GeckoTerminalClient()
        self.dexscreener = dex_client or DexScreenerClient()
        self.cache_ttl_seconds = cache_ttl_seconds
        
        # In-memory storage
        self._pools_by_key: Dict[Tuple[str, str], MarketPairObservation] = {}
        self._tokens_by_key: Dict[Tuple[str, str], AggregatedTokenRanking] = NormalizedTokenDict()
        self._last_refresh_time: Optional[datetime] = None
        self._enrichment_warning: Optional[str] = None
        self._lock = asyncio.Lock()

    def get_freshness_status(self) -> MarketDataFreshness:
        if not self._last_refresh_time:
            return MarketDataFreshness.UNAVAILABLE
        age = (datetime.now(timezone.utc) - self._last_refresh_time).total_seconds()
        if age < 60.0:
            return MarketDataFreshness.FRESH
        else:
            return MarketDataFreshness.STALE

    def get_cache_age_seconds(self) -> float:
        if not self._last_refresh_time:
            return 999999.0
        return max(0.0, (datetime.now(timezone.utc) - self._last_refresh_time).total_seconds())

    def _deduplicate_pools(self, pools: List[MarketPairObservation]) -> List[MarketPairObservation]:
        """
        Deduplicates identical pools across providers/endpoints before token aggregation.
        Deduplication key: (chain_id, pair_address.lower())

        When duplicate observations of the SAME pool exist:
        - Retains canonical observation with highest valid liquidity.
        - Merges provider URLs without duplicates.
        - Merges raw observation timestamps across endpoints.
        - NEVER sums volume across duplicate observations of the same pool.
        """
        pool_map: Dict[Tuple[str, str], MarketPairObservation] = {}
        for p in pools:
            if not p.pair_address:
                continue
            key = (p.chain_id, p.pair_address.lower())
            if key not in pool_map:
                pool_map[key] = p
            else:
                existing = pool_map[key]
                # Merge provider URLs
                combined_urls = list(dict.fromkeys(existing.provider_urls + p.provider_urls))
                if p.provider_url and p.provider_url not in combined_urls:
                    combined_urls.append(p.provider_url)
                if existing.provider_url and existing.provider_url not in combined_urls:
                    combined_urls.append(existing.provider_url)

                # Merge observation timestamps across endpoints
                combined_timestamps = {**existing.observation_timestamps, **p.observation_timestamps}

                # Elect canonical observation based on highest valid liquidity, tie-break by completeness
                pref_p = False
                p_liq = p.liquidity_usd if p.liquidity_usd is not None else Decimal("-1")
                ex_liq = existing.liquidity_usd if existing.liquidity_usd is not None else Decimal("-1")
                if p_liq > ex_liq:
                    pref_p = True
                elif p_liq == ex_liq and len(p.missing_fields) < len(existing.missing_fields):
                    pref_p = True

                base_obs = p if pref_p else existing
                other_obs = existing if pref_p else p

                updated_data = base_obs.model_dump()
                updated_data["provider_urls"] = combined_urls
                updated_data["observation_timestamps"] = combined_timestamps

                # Fill missing attributes in base_obs from other_obs
                for attr in [
                    "price_usd", "price_native", "volume_24h", "volume_1h", "volume_5m",
                    "price_change_5m_pct", "price_change_1h_pct", "price_change_24h_pct",
                    "fdv_usd", "market_cap_usd", "pair_created_at"
                ]:
                    if updated_data.get(attr) is None and getattr(other_obs, attr) is not None:
                        updated_data[attr] = getattr(other_obs, attr)

                pool_map[key] = MarketPairObservation(**updated_data)

        return list(pool_map.values())

    def _aggregate_tokens(self, pools: List[MarketPairObservation], now: Optional[datetime] = None) -> Dict[Tuple[str, str], AggregatedTokenRanking]:
        """
        Groups pools by (chain_id, token_address).
        First deduplicates identical pool observations across endpoints/providers.
        Selects canonical pool based on MAX liquidity to avoid distorted micro-pool pricing.
        Sums 24h volume strictly across DISTINCT pools of that token without double-counting.
        """
        if now is None:
            now = datetime.now(timezone.utc)

        # 1. Deduplicate identical pools across providers first
        distinct_pools = self._deduplicate_pools(pools)

        # 2. Group distinct pools by (chain_id, token_address)
        grouped: Dict[Tuple[str, str], List[MarketPairObservation]] = {}
        for p in distinct_pools:
            norm_cid = normalize_chain_id(p.chain_id)
            tok_key = (norm_cid, p.token_address.lower())
            if tok_key not in grouped:
                grouped[tok_key] = []
            grouped[tok_key].append(p)

        aggregated_map = NormalizedTokenDict()
        for tok_key, pool_list in grouped.items():
            # Sort distinct pools by liquidity DESC, tie-break by volume DESC, then pair_address
            pool_list.sort(
                key=lambda x: (
                    x.liquidity_usd if x.liquidity_usd is not None else Decimal("-1"),
                    x.volume_24h if x.volume_24h is not None else Decimal("-1"),
                    x.pair_address or ""
                ),
                reverse=True
            )
            canonical = pool_list[0]

            # Sum 24h volume strictly across distinct pools
            total_vol_24h = Decimal("0.0")
            for p in pool_list:
                if p.volume_24h is not None:
                    total_vol_24h += p.volume_24h

            # Calculate age in hours if creation timestamp available
            age_hours = None
            if canonical.pair_created_at:
                age_hours = round((now - canonical.pair_created_at).total_seconds() / 3600.0, 2)

            # Observation freshness from actual observation/fetch timestamps
            tok_fetched_at = canonical.fetched_at or now
            tok_cache_age = max(0.0, round((now - tok_fetched_at).total_seconds(), 1))
            if canonical.fetched_at is None:
                tok_freshness = MarketDataFreshness.UNAVAILABLE
            elif tok_cache_age < 60.0:
                tok_freshness = MarketDataFreshness.FRESH
            else:
                tok_freshness = MarketDataFreshness.STALE

            tok_category = canonical.asset_category
            if not tok_category or tok_category == AssetCategory.UNKNOWN:
                clf = classify_token(canonical.chain_id, canonical.token_address, canonical.symbol, canonical.name)
            else:
                clf = TokenClassification(
                    canonical.asset_category,
                    getattr(canonical, "classification_source", ClassificationSource.DEFAULT),
                    getattr(canonical, "confidence", ClassificationConfidence.LOW),
                    getattr(canonical, "classification_reason", "Classified observation")
                )

            # Provenance unions across all distinct pools
            endpoint_sources = list(dict.fromkeys(
                src
                for p in pool_list
                for src in ([p.endpoint_id] if p.endpoint_id else []) + list(p.observation_timestamps.keys())
                if src
            ))
            if not endpoint_sources and canonical.provider_source:
                endpoint_sources = [canonical.provider_source]

            all_provider_urls = list(dict.fromkeys(
                url for p in pool_list for url in p.provider_urls if url
            ))
            if canonical.provider_url and canonical.provider_url not in all_provider_urls:
                all_provider_urls.append(canonical.provider_url)

            all_obs_timestamps: Dict[str, datetime] = {}
            for p in pool_list:
                all_obs_timestamps.update(p.observation_timestamps)
            if not all_obs_timestamps and canonical.fetched_at:
                all_obs_timestamps[canonical.provider_source] = canonical.fetched_at

            tok_chain_id = normalize_chain_id(canonical.chain_id)

            agg = AggregatedTokenRanking(
                chain_id=tok_chain_id,
                provider_network_id=canonical.provider_network_id,
                token_address=canonical.token_address,
                symbol=canonical.symbol or "UNKNOWN",
                name=canonical.name or canonical.symbol or "Unknown Token",
                canonical_pool_address=canonical.pair_address,
                dex_name=canonical.dex_name,
                canonical_dex_id=canonical.dex_name,
                quote_token_symbol=canonical.quote_token_symbol or "QUOTE",
                asset_category=clf.category,
                classification_source=clf.source,
                confidence=clf.confidence,
                classification_reason=clf.reason,
                price_usd=canonical.price_usd if canonical.price_usd is not None else Decimal("0.0"),
                price_native=canonical.price_native,
                liquidity_usd=canonical.liquidity_usd if canonical.liquidity_usd is not None else Decimal("0.0"),
                volume_24h_usd=total_vol_24h,
                volume_1h_usd=canonical.volume_1h,
                volume_5m_usd=canonical.volume_5m,
                price_change_5m_pct=canonical.price_change_5m_pct,
                price_change_1h_pct=canonical.price_change_1h_pct,
                price_change_24h_pct=canonical.price_change_24h_pct,
                fdv_usd=canonical.fdv_usd,
                market_cap_usd=canonical.market_cap_usd,
                txns_24h_total=(canonical.txns_24h_buys or 0) + (canonical.txns_24h_sells or 0) if (canonical.txns_24h_buys or canonical.txns_24h_sells) else None,
                pair_created_at=canonical.pair_created_at,
                age_hours=age_hours,
                pool_count=len(pool_list),
                multi_pool_count=len(pool_list),
                distinct_pools_aggregated=len(pool_list),
                pool_selection_methodology="MAX_LIQUIDITY_CANONICAL_POOL",
                provider_source=canonical.provider_source,
                endpoint_sources=endpoint_sources,
                canonical_pool_url=canonical.provider_url,
                provider_urls=all_provider_urls,
                observation_timestamps=all_obs_timestamps,
                fetched_at=tok_fetched_at,
                cache_age_seconds=tok_cache_age,
                freshness_status=tok_freshness
            )
            aggregated_map[tok_key] = agg

        # Phase P1.3: Apply Market Intelligence (Disambiguation, Anomalies, Quality Scoring, Alerts)
        from app.services.market_intelligence_service import market_intelligence_service
        raw_tokens = list(aggregated_map.values())
        disambiguated = market_intelligence_service.disambiguate_cross_chain_identities(raw_tokens)

        for tok in disambiguated:
            tok_norm_cid = normalize_chain_id(tok.chain_id)
            tok_pools = grouped.get((tok_norm_cid, tok.token_address.lower()), [])

            # Anomaly indicators
            flags, details = market_intelligence_service.detect_anomalies(tok, tok_pools)
            tok.anomaly_flags = flags
            tok.anomaly_details = details

            # Explainable quality score
            q_breakdown = market_intelligence_service.evaluate_quality_score(tok, flags)
            tok.quality_score = q_breakdown.composite_score
            tok.quality_factors = q_breakdown.model_dump()

            # New-pair monitoring & alerts
            is_new, alerts = market_intelligence_service.evaluate_discovery_alerts(tok, flags)
            tok.is_new_pair = is_new
            tok.discovery_alerts = alerts

            # Save back into map
            aggregated_map[(tok_norm_cid, tok.token_address.lower())] = tok

        return aggregated_map

    async def refresh_market_data(self, force: bool = False) -> Dict[str, Any]:
        """
        Polls GeckoTerminal for Solana, Base, Ethereum, and Arbitrum (both trending and newly created pools),
        enriches with DEX Screener batch observations, deduplicates identical pools,
        aggregates by token, and updates in-memory cache.
        """
        async with self._lock:
            now = datetime.now(timezone.utc)
            if not force and self._last_refresh_time:
                age = (now - self._last_refresh_time).total_seconds()
                if age < self.cache_ttl_seconds:
                    return {
                        "status": "CACHE_HIT",
                        "cache_age_seconds": round(age, 2),
                        "total_pools": len(self._pools_by_key),
                        "total_tokens": len({(t.chain_id, t.token_address.lower()) for t in self._tokens_by_key.values()})
                    }

            # Fetch pools for discovery networks (trending + new) concurrently
            discovery_networks = ["solana", "base", "eth", "arbitrum"]
            discovery_tasks = []
            for net in discovery_networks:
                discovery_tasks.append(self.gecko.fetch_pools(net, endpoint_type="trending_pools"))
                discovery_tasks.append(self.gecko.fetch_pools(net, endpoint_type="new_pools"))

            results = await asyncio.gather(*discovery_tasks, return_exceptions=True)
            raw_gecko_pools: List[MarketPairObservation] = []
            for r in results:
                if isinstance(r, list):
                    raw_gecko_pools.extend(r)

            if not raw_gecko_pools and not self._pools_by_key:
                return {
                    "status": "PROVIDER_UNAVAILABLE",
                    "error": "Failed to fetch market data from public providers",
                    "total_pools": 0,
                    "total_tokens": 0
                }

            # Group discovered token addresses by chain for DEX Screener enrichment
            tokens_by_chain: Dict[str, List[str]] = {
                "solana": [],
                "base": [],
                "ethereum": [],
                "arbitrum": []
            }
            for p in raw_gecko_pools:
                ds_chain = CAIP2_TO_DEXSCREENER_CHAIN.get(p.chain_id)
                if ds_chain and ds_chain in tokens_by_chain:
                    if p.token_address not in tokens_by_chain[ds_chain]:
                        tokens_by_chain[ds_chain].append(p.token_address)

            raw_dex_pools: List[MarketPairObservation] = []
            self._enrichment_warning = None
            dex_tasks = []
            for ds_chain, addrs in tokens_by_chain.items():
                if addrs:
                    dex_tasks.append(self.dexscreener.get_token_pairs_batch(ds_chain, addrs[:30]))

            if dex_tasks:
                dex_results = await asyncio.gather(*dex_tasks, return_exceptions=True)
                for r in dex_results:
                    if isinstance(r, list):
                        raw_dex_pools.extend(r)
                    elif isinstance(r, Exception):
                        self._enrichment_warning = f"DEX Screener enrichment degraded ({type(r).__name__}); serving GeckoTerminal discovery."

            if self.dexscreener.circuit_breaker.state == "OPEN":
                self._enrichment_warning = "DEX Screener circuit breaker is OPEN; serving GeckoTerminal discovery."
            elif self.dexscreener.error_category:
                self._enrichment_warning = f"DEX Screener reporting {self.dexscreener.error_category}; serving GeckoTerminal discovery."

            all_raw = raw_gecko_pools + raw_dex_pools
            if all_raw:
                distinct_pools = self._deduplicate_pools(all_raw)
                self._pools_by_key = {
                    (p.chain_id, p.pair_address.lower()): p
                    for p in distinct_pools if p.pair_address
                }
                self._tokens_by_key = self._aggregate_tokens(distinct_pools, now)
                self._last_refresh_time = now

                # Phase P1.3: Capture in-memory point-in-time observation snapshot (max 50)
                from app.services.market_intelligence_service import market_intelligence_service
                market_intelligence_service.capture_snapshot(
                    list(self._tokens_by_key.values()),
                    len(self._pools_by_key)
                )

                # Phase P1.4: Persist historical token observations into dedicated research_history.db
                try:
                    from app.services.research_analytics_service import research_analytics_service
                    research_analytics_service.record_token_observations(list(self._tokens_by_key.values()))
                except Exception as _re_err:
                    logger.warning("Failed to record research history observations: %s", _re_err)

            return {
                "status": "REFRESHED",
                "timestamp": now.isoformat(),
                "total_pools": len(self._pools_by_key),
                "total_tokens": len({(t.chain_id, t.token_address.lower()) for t in self._tokens_by_key.values()}),
                "solana_pools_count": sum(1 for p in self._pools_by_key.values() if p.provider_network_id == "solana"),
                "base_pools_count": sum(1 for p in self._pools_by_key.values() if p.provider_network_id == "base"),
                "ethereum_pools_count": sum(1 for p in self._pools_by_key.values() if p.provider_network_id in ("eth", "ethereum")),
                "arbitrum_pools_count": sum(1 for p in self._pools_by_key.values() if p.provider_network_id == "arbitrum")
            }

    async def fetch_and_aggregate_market(self, force_refresh: bool = False) -> List[AggregatedTokenRanking]:
        await self.refresh_market_data(force=force_refresh)
        unique_tokens: Dict[Tuple[str, str], AggregatedTokenRanking] = {}
        for t in self._tokens_by_key.values():
            k = (t.chain_id, t.token_address.lower())
            if k not in unique_tokens:
                unique_tokens[k] = t
        return list(unique_tokens.values())

    async def query_screener(
        self,
        chain: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        sort_by: str = "volume_24h",
        sort_direction: str = "desc",
        sort_dir: Optional[str] = None,
        min_liquidity: Optional[Decimal] = None,
        min_volume_24h: Optional[Decimal] = None,
        min_age_hours: Optional[float] = None,
        max_age_hours: Optional[float] = None,
        min_price_change_24h: Optional[Union[float, Decimal]] = None,
        max_price_change_24h: Optional[Union[float, Decimal]] = None,
        search: Optional[str] = None,
        memecoin_only: bool = True,
        category: Optional[str] = None,
        min_quality_score: Optional[Union[float, Decimal]] = None,
        has_anomalies: Optional[bool] = None,
        new_pairs_only: Optional[bool] = None,
        force_refresh: bool = False
    ) -> DynamicScreenerResponse:
        """
        Executes dynamic screener query against normalized in-memory market observations.
        """
        # Trigger refresh if empty or expired or forced
        if force_refresh or not self._tokens_by_key:
            await self.refresh_market_data(force=force_refresh)

        effective_dir = (sort_dir or sort_direction or "desc").lower()
        is_reverse = (effective_dir != "asc")

        now = datetime.now(timezone.utc)
        freshness = self.get_freshness_status()
        cache_age = self.get_cache_age_seconds()

        # Deduplicate values in case aliased keys point to same AggregatedTokenRanking
        unique_tokens: Dict[Tuple[str, str], AggregatedTokenRanking] = {}
        for t in self._tokens_by_key.values():
            k = (t.chain_id, t.token_address.lower())
            if k not in unique_tokens:
                unique_tokens[k] = t
        tokens = list(unique_tokens.values())

        # 1. Filter by chain
        norm_chain = chain.lower() if chain else "all"
        if norm_chain != "all":
            if norm_chain in ("solana", "solana:mainnet", "solana:5eykt4usfv8p8njdtrepy1vzqkqzkvdp"):
                tokens = [t for t in tokens if t.provider_network_id == "solana"]
                chains_requested = ["solana"]
            elif norm_chain in ("base", "eip155:8453"):
                tokens = [t for t in tokens if t.provider_network_id == "base"]
                chains_requested = ["base"]
            elif norm_chain in ("eth", "ethereum", "eip155:1"):
                tokens = [t for t in tokens if t.provider_network_id in ("eth", "ethereum") or t.chain_id == "eip155:1"]
                chains_requested = ["ethereum"]
            elif norm_chain in ("arbitrum", "eip155:42161"):
                tokens = [t for t in tokens if t.provider_network_id == "arbitrum" or t.chain_id == "eip155:42161"]
                chains_requested = ["arbitrum"]
            else:
                norm_cid = normalize_chain_id(norm_chain)
                tokens = [t for t in tokens if t.chain_id == norm_cid or t.chain_id == norm_chain or t.provider_network_id == norm_chain]
                chains_requested = [norm_chain]
        else:
            chains_requested = ["solana", "base", "ethereum", "arbitrum"]

        chains_with_data = sorted(list({t.provider_network_id for t in tokens}))

        # 2. Determine effective category and memecoin_only filter
        if category is not None and category.strip():
            cat_upper = category.strip().upper()
            if cat_upper in ("MEME", "NON_MEME", "UNKNOWN", "ALL"):
                effective_category = cat_upper
            else:
                effective_category = "ALL"
            effective_memecoin_only = (effective_category == "MEME")
        else:
            if memecoin_only:
                effective_category = "MEME"
                effective_memecoin_only = True
            else:
                effective_category = "ALL"
                effective_memecoin_only = False

        if effective_category != "ALL":
            tokens = [
                t for t in tokens
                if (t.asset_category.value if hasattr(t.asset_category, "value") else str(t.asset_category)) == effective_category
            ]

        # 3. Filter by minimum liquidity
        if min_liquidity is not None and min_liquidity > Decimal("0.0"):
            tokens = [t for t in tokens if t.liquidity_usd >= min_liquidity]

        # 4. Filter by minimum 24h volume
        if min_volume_24h is not None and min_volume_24h > Decimal("0.0"):
            tokens = [t for t in tokens if t.volume_24h_usd >= min_volume_24h]

        # 5. Filter by age
        if min_age_hours is not None:
            tokens = [t for t in tokens if t.age_hours is not None and t.age_hours >= min_age_hours]
        if max_age_hours is not None:
            tokens = [t for t in tokens if t.age_hours is not None and t.age_hours <= max_age_hours]

        # 6. Filter by 24h price change
        if min_price_change_24h is not None:
            dec_min = Decimal(str(min_price_change_24h))
            tokens = [t for t in tokens if t.price_change_24h_pct is not None and t.price_change_24h_pct >= dec_min]
        if max_price_change_24h is not None:
            dec_max = Decimal(str(max_price_change_24h))
            tokens = [t for t in tokens if t.price_change_24h_pct is not None and t.price_change_24h_pct <= dec_max]

        # 7. Search filter (symbol, name, token address)
        if search and search.strip():
            q = search.strip().lower()
            tokens = [t for t in tokens if (q in t.symbol.lower() or q in t.name.lower() or q in t.token_address.lower())]

        # 8. Filter by minimum quality score (Phase P1.3)
        if min_quality_score is not None:
            dec_min_q = Decimal(str(min_quality_score))
            tokens = [t for t in tokens if t.quality_score is not None and t.quality_score >= dec_min_q]

        # 9. Filter by anomalies (Phase P1.3)
        if has_anomalies is not None:
            if has_anomalies:
                tokens = [t for t in tokens if t.anomaly_flags and any(f != "INSUFFICIENT_DATA" for f in t.anomaly_flags)]
            else:
                tokens = [t for t in tokens if not t.anomaly_flags or all(f == "INSUFFICIENT_DATA" for f in t.anomaly_flags)]

        # 10. Filter by newly created pairs (Phase P1.3)
        if new_pairs_only:
            tokens = [t for t in tokens if t.is_new_pair]

        # 11. Deterministic Sorting
        def sort_key(item: AggregatedTokenRanking):
            primary: Any = Decimal("-999999999")
            if sort_by == "volume_24h":
                primary = item.volume_24h_usd if item.volume_24h_usd is not None else Decimal("0.0")
            elif sort_by == "liquidity":
                primary = item.liquidity_usd if item.liquidity_usd is not None else Decimal("0.0")
            elif sort_by == "quality_score":
                primary = item.quality_score if item.quality_score is not None else Decimal("-1.0")
            elif sort_by == "price_change_5m":
                primary = item.price_change_5m_pct if item.price_change_5m_pct is not None else (Decimal("-999999") if is_reverse else Decimal("999999"))
            elif sort_by == "price_change_1h":
                primary = item.price_change_1h_pct if item.price_change_1h_pct is not None else (Decimal("-999999") if is_reverse else Decimal("999999"))
            elif sort_by == "price_change_24h":
                primary = item.price_change_24h_pct if item.price_change_24h_pct is not None else (Decimal("-999999") if is_reverse else Decimal("999999"))
            elif sort_by == "price_usd":
                primary = item.price_usd if item.price_usd is not None else Decimal("0.0")
            elif sort_by == "age":
                primary = Decimal(str(item.age_hours)) if item.age_hours is not None else (Decimal("-1.0") if is_reverse else Decimal("999999999"))
            # Deterministic tie-breaker using token address
            return (primary, item.token_address)

        tokens.sort(key=sort_key, reverse=is_reverse)

        total_matches = len(tokens)
        validated_limit = max(1, min(100, limit))
        validated_offset = max(0, offset)
        paginated = tokens[validated_offset: validated_offset + validated_limit]

        total_pages = math.ceil(total_matches / validated_limit) if total_matches > 0 else 0
        current_page = (validated_offset // validated_limit) + 1 if total_matches > 0 else 1

        # Warnings
        warnings = []
        if freshness == MarketDataFreshness.STALE:
            warnings.append(f"Market observation cache is stale ({round(cache_age)}s old). Fresh live data pending next polling interval.")
        if self._enrichment_warning:
            warnings.append(self._enrichment_warning)
        if not tokens:
            warnings.append("No tokens matched the selected filter criteria.")

        health_summary = self.get_providers_health().model_dump()

        return DynamicScreenerResponse(
            data_source="GECKOTERMINAL_PUBLIC_API + DEXSCREENER_ENRICHMENT",
            chains_requested=chains_requested,
            chains_with_data=chains_with_data,
            fetched_at=self._last_refresh_time or now,
            cache_age_seconds=round(cache_age, 1),
            freshness_status=freshness,
            provider_health=health_summary["providers"],
            result_count=len(paginated),
            total_matches=total_matches,
            total_pages=total_pages,
            current_page=current_page,
            limit=validated_limit,
            offset=validated_offset,
            memecoin_only=effective_memecoin_only,
            category_applied=effective_category,
            filters_applied={
                "chain": norm_chain,
                "memecoin_only": effective_memecoin_only,
                "category": effective_category,
                "min_liquidity": float(min_liquidity) if min_liquidity else None,
                "min_volume_24h": float(min_volume_24h) if min_volume_24h else None,
                "min_age_hours": min_age_hours,
                "max_age_hours": max_age_hours,
                "search": search,
                "min_quality_score": float(min_quality_score) if min_quality_score is not None else None,
                "has_anomalies": has_anomalies,
                "new_pairs_only": new_pairs_only
            },
            sort_applied=f"{sort_by}:{effective_dir}",
            warnings=warnings,
            tokens=paginated
        )

    def get_providers_health(self) -> MarketProvidersHealthResponse:
        now = datetime.now(timezone.utc)

        # Truthful GeckoTerminal provider status (Phase P1.3)
        if self.gecko.last_attempted_fetch is None:
            gecko_status = "UNKNOWN"
        elif self.gecko.circuit_breaker.state == "OPEN":
            gecko_status = "UNAVAILABLE"
        elif self.gecko.last_successful_fetch is None:
            gecko_status = "UNAVAILABLE"
        elif self.gecko.error_category:
            gecko_status = "DEGRADED"
        elif self.gecko.rate_limiter.get_remaining() <= 0:
            gecko_status = "RATE_LIMITED"
        else:
            gecko_status = "OPERATIONAL"

        # Truthful DEX Screener provider status (Phase P1.3)
        if self.dexscreener.last_attempted_fetch is None:
            dex_status = "UNKNOWN"
        elif self.dexscreener.circuit_breaker.state == "OPEN":
            dex_status = "UNAVAILABLE"
        elif self.dexscreener.last_successful_fetch is None:
            dex_status = "UNAVAILABLE"
        elif self.dexscreener.error_category or (self._enrichment_warning and "DEX Screener" in self._enrichment_warning):
            dex_status = "DEGRADED"
        elif self.dexscreener.rate_limiter.get_remaining() <= 0:
            dex_status = "RATE_LIMITED"
        else:
            dex_status = "OPERATIONAL"

        # Truthful Provider Freshness
        if self.gecko.last_successful_fetch:
            gt_age = (now - self.gecko.last_successful_fetch).total_seconds()
            gecko_cache_age = round(max(0.0, gt_age), 1)
            gecko_freshness = MarketDataFreshness.FRESH if gt_age < 60.0 else MarketDataFreshness.STALE
        else:
            gecko_cache_age = None
            gecko_freshness = MarketDataFreshness.UNAVAILABLE

        if self.dexscreener.last_successful_fetch:
            ds_age = (now - self.dexscreener.last_successful_fetch).total_seconds()
            dex_cache_age = round(max(0.0, ds_age), 1)
            dex_freshness = MarketDataFreshness.FRESH if ds_age < 60.0 else MarketDataFreshness.STALE
        else:
            dex_cache_age = None
            dex_freshness = MarketDataFreshness.UNAVAILABLE

        return MarketProvidersHealthResponse(
            timestamp=now,
            providers={
                "geckoterminal": ProviderStatusDTO(
                    provider_id="geckoterminal",
                    name="GeckoTerminal Public API v2",
                    status=gecko_status,
                    circuit_breaker_state=self.gecko.circuit_breaker.state,
                    supported_chains=[SOLANA_CAIP2_CANONICAL, "eip155:8453", "eip155:1", "eip155:42161", SOLANA_LEGACY_ALIAS],
                    network_identifiers={
                        SOLANA_CAIP2_CANONICAL: "solana",
                        SOLANA_LEGACY_ALIAS: "solana",
                        "eip155:8453": "base",
                        "eip155:1": "eth",
                        "eip155:42161": "arbitrum"
                    },
                    last_successful_fetch=self.gecko.last_successful_fetch,
                    last_attempted_fetch=self.gecko.last_attempted_fetch,
                    latency_ms=round(self.gecko.last_latency_ms, 2) if self.gecko.last_latency_ms else None,
                    rate_limit_budget_remaining=self.gecko.rate_limiter.get_remaining(),
                    error_category=self.gecko.error_category,
                    cache_age_seconds=gecko_cache_age,
                    freshness_status=gecko_freshness
                ),
                "dexscreener": ProviderStatusDTO(
                    provider_id="dexscreener",
                    name="DEX Screener Public API",
                    status=dex_status,
                    circuit_breaker_state=self.dexscreener.circuit_breaker.state,
                    supported_chains=[SOLANA_CAIP2_CANONICAL, "eip155:8453", "eip155:1", "eip155:42161", SOLANA_LEGACY_ALIAS],
                    network_identifiers={
                        SOLANA_CAIP2_CANONICAL: "solana",
                        SOLANA_LEGACY_ALIAS: "solana",
                        "eip155:8453": "base",
                        "eip155:1": "ethereum",
                        "eip155:42161": "arbitrum"
                    },
                    last_successful_fetch=self.dexscreener.last_successful_fetch,
                    last_attempted_fetch=self.dexscreener.last_attempted_fetch,
                    latency_ms=round(self.dexscreener.last_latency_ms, 2) if self.dexscreener.last_latency_ms else None,
                    rate_limit_budget_remaining=self.dexscreener.rate_limiter.get_remaining(),
                    error_category=self.dexscreener.error_category,
                    cache_age_seconds=dex_cache_age,
                    freshness_status=dex_freshness
                )
            }
        )

# Global singleton
market_data_coordinator = MarketDataCoordinator()
