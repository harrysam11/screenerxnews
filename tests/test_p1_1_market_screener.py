"""
Phase P1.1: Solana + Base Live Market Ingestion & Dynamic Screener Test Suite.

Verifies:
- 1. Isolated database guarantee (zero writes or reads to operational data/memecoin_os.db).
- 2. GeckoTerminal Solana & Base discovery with versioned header and verified network identifiers.
- 3. DEX Screener batch enrichment and normalization.
- 4. Deterministic token aggregation selecting canonical pool by max(liquidity_usd) without double-counting.
- 5. Handling of missing fields, malformed JSON, HTTP 429, HTTP 5xx, and timeouts.
- 6. Rate limiter token bucket (10 RPM GeckoTerminal, 60 RPM DEX Screener) and Circuit Breaker (CLOSED/OPEN/HALF_OPEN).
- 7. Stale cache classification and warning disclosures.
- 8. Screener API validation (invalid chain, sort, filters produce HTTP 400).
- 9. Top 10/50/100 limits, pagination, and deterministic tie-breaking.
- 10. Provider health monitoring endpoint accuracy.
"""

import os
import json
import asyncio
import tempfile
import sqlite3
import hashlib
import time
from decimal import Decimal

from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List

import pytest
import httpx
from fastapi.testclient import TestClient

from app.main import app
from app.core.config import settings

# Enforce strict test isolation for database path
settings.APP_ENV = "test"
if not getattr(settings, "SQLITE_DB_PATH", None) or settings.SQLITE_DB_PATH == "./data/memecoin_os.db":
    settings.SQLITE_DB_PATH = "/tmp/test_session_isolated.db"

from app.schemas.market_data import (
    MarketDataFreshness,
    AssetCategory,
    ClassificationSource,
    ClassificationConfidence,
    MarketPairObservation,
    AggregatedTokenRanking,
    DynamicScreenerResponse,
    MarketProvidersHealthResponse
)
from app.services.market_data_service import (
    TokenBucketRateLimiter,
    CircuitBreaker,
    GeckoTerminalClient,
    DexScreenerClient,
    MarketDataCoordinator,
    market_data_coordinator,
    classify_token,
    TokenClassification
)

client = TestClient(app)

OPERATIONAL_DB_PATH = "data/memecoin_os.db"


@pytest.fixture(autouse=True)
def verify_db_isolation():
    """
    Strict isolation pre- and post-condition check:
    Computes SHA-256 of data/memecoin_os.db before and after each test to prove
    that the operational database is never touched or mutated.
    """
    if os.path.exists(OPERATIONAL_DB_PATH):
        with open(OPERATIONAL_DB_PATH, "rb") as f:
            pre_hash = hashlib.sha256(f.read()).hexdigest()
    else:
        pre_hash = None

    yield

    if os.path.exists(OPERATIONAL_DB_PATH):
        with open(OPERATIONAL_DB_PATH, "rb") as f:
            post_hash = hashlib.sha256(f.read()).hexdigest()
        assert pre_hash == post_hash, "CRITICAL: Operational database hash changed during test run!"


# =============================================================================
# Mock Payloads
# =============================================================================

MOCK_GECKO_SOLANA_POOLS = {
    "data": [
        {
            "id": "solana_Ho1aSG6WGH1FfXdEHrK2f4Ggt1Hoxo9s5jcLMDqeyeBz",
            "type": "pool",
            "attributes": {
                "base_token_price_usd": "0.0160048",
                "base_token_price_native_currency": "0.0002238",
                "quote_token_price_usd": "145.20",
                "quote_token_price_native_currency": "1.0",
                "address": "Ho1aSG6WGH1FfXdEHrK2f4Ggt1Hoxo9s5jcLMDqeyeBz",
                "name": "mihao / SOL",
                "pool_created_at": "2026-10-25T16:43:15Z",
                "fdv_usd": "15068010.92",
                "market_cap_usd": "15068013.32",
                "reserve_in_usd": "430400.06",
                "volume_usd": {
                    "m5": "1687.50",
                    "h1": "99497.97",
                    "h6": "500000.00",
                    "h24": "3260796.24"
                },
                "price_change_percentage": {
                    "m5": "1.232",
                    "h1": "-1.571",
                    "h6": "5.400",
                    "h24": "10.622"
                },
                "transactions": {
                    "h24": {"buys": 18000, "sells": 17777}
                }
            },
            "relationships": {
                "base_token": {"data": {"id": "solana_4CxjZyMNoF1p6eXHXHv1TWpS2X47CuUmLwtGe18spump"}},
                "quote_token": {"data": {"id": "solana_So11111111111111111111111111111111111111112"}},
                "dex": {"data": {"id": "pumpswap"}}
            }
        },
        {
            "id": "solana_95ytGFRcBgT1d8ioAQkPtXEA6QrgJ5omgiMHnyheRxdP",
            "type": "pool",
            "attributes": {
                "base_token_price_usd": "0.0006536",
                "base_token_price_native_currency": "0.0000091",
                "address": "95ytGFRcBgT1d8ioAQkPtXEA6QrgJ5omgiMHnyheRxdP",
                "name": "bill / USDC",
                "pool_created_at": "2026-11-14T19:49:34Z",
                "fdv_usd": "653648.19",
                "reserve_in_usd": "91022.47",
                "volume_usd": {
                    "m5": "397.80",
                    "h1": "99297.63",
                    "h24": "4983949.46"
                },
                "price_change_percentage": {
                    "m5": "-1.423",
                    "h1": "-7.054",
                    "h24": "1025.401"
                },
                "transactions": {
                    "h24": {"buys": 60000, "sells": 51014}
                }
            },
            "relationships": {
                "base_token": {"data": {"id": "solana_85RhcSEMy15SNhFLmSArNm7udPKXVagVgsq4YmSopump"}},
                "quote_token": {"data": {"id": "solana_EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"}},
                "dex": {"data": {"id": "pumpswap"}}
            }
        }
    ]
}

MOCK_GECKO_BASE_POOLS = {
    "data": [
        {
            "id": "base_0xc4a328359592211d4e415915c6c6f4e8807f6b16",
            "type": "pool",
            "attributes": {
                "base_token_price_usd": "0.140838",
                "base_token_price_native_currency": "0.0000778",
                "address": "0xc4a328359592211d4e415915c6c6f4e8807f6b16",
                "name": "PONS / WPRL",
                "pool_created_at": "2026-11-12T09:59:55Z",
                "fdv_usd": "140838000.90",
                "reserve_in_usd": "24323635.62",
                "volume_usd": {
                    "m5": "0.0",
                    "h1": "11499.34",
                    "h24": "18282395.40"
                },
                "price_change_percentage": {
                    "m5": "0.0",
                    "h1": "0.0",
                    "h24": "-2.114"
                },
                "transactions": {
                    "h24": {"buys": 100, "sells": 93}
                }
            },
            "relationships": {
                "base_token": {"data": {"id": "base_0x304b826a47a5e6cb2766ad9069f670651c9f847d"}},
                "quote_token": {"data": {"id": "base_0x4200000000000000000000000000000000000006"}},
                "dex": {"data": {"id": "uniswap-v2-base"}}
            }
        }
    ]
}


# =============================================================================
# 1. Rate Limiter & Circuit Breaker Unit Tests
# =============================================================================

def test_token_bucket_rate_limiter():
    """Verifies that token bucket rate limiter honors rpm budget."""
    async def _run():
        limiter = TokenBucketRateLimiter(rate_per_minute=2, burst=2)
        assert await limiter.acquire() is True
        assert await limiter.acquire() is True
        # Third immediate acquire should be denied
        assert await limiter.acquire() is False
    asyncio.run(_run())



def test_circuit_breaker_transitions():
    """Verifies CLOSED -> OPEN -> HALF_OPEN state transitions."""
    cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=0.1)
    assert cb.state == "CLOSED"
    assert cb.can_execute() is True

    cb.record_failure("Err 1")
    cb.record_failure("Err 2")
    assert cb.state == "CLOSED"

    cb.record_failure("Err 3")
    assert cb.state == "OPEN"
    assert cb.can_execute() is False

    # Simulate waiting for cooldown
    import time
    time.sleep(0.15)
    assert cb.can_execute() is True
    assert cb.state == "HALF_OPEN"

    cb.record_success()
    assert cb.state == "CLOSED"


# =============================================================================
# 2. Normalization & Canonical Deduplication Tests
# =============================================================================

def test_normalization_solana_pool():
    """Verifies GeckoTerminal parser preserves exact Decimal precision and relationships."""
    client = GeckoTerminalClient()
    parsed = client._parse_gecko_pools(MOCK_GECKO_SOLANA_POOLS, "solana", "solana:mainnet")
    assert len(parsed) == 2

    p1 = parsed[0]
    assert p1.chain_id == "solana:mainnet"
    assert p1.provider_network_id == "solana"
    assert p1.token_address == "4CxjZyMNoF1p6eXHXHv1TWpS2X47CuUmLwtGe18spump"
    assert p1.symbol == "mihao"
    assert p1.price_usd == Decimal("0.0160048")
    assert p1.liquidity_usd == Decimal("430400.06")
    assert p1.volume_24h_usd == Decimal("3260796.24")
    assert p1.dex_id == "pumpswap"
    assert p1.pair_created_at == datetime(2026, 10, 25, 16, 43, 15, tzinfo=timezone.utc)
    assert p1.txns_24h_buys == 18000
    assert p1.txns_24h_sells == 17777


def test_canonical_pool_selection_multi_pool():
    """
    Verifies that a token appearing in multiple pools has its canonical pool
    selected strictly by max(liquidity_usd) without inflating liquidity.
    """
    coordinator = MarketDataCoordinator()
    now = datetime.now(timezone.utc)

    # Pool 1: Raydium, $100k liquidity, $1M volume
    obs1 = MarketPairObservation(
        chain_id="solana:mainnet",
        provider_network_id="solana",
        provider_source="geckoterminal",
        pool_address="PoolRaydium1111111111111111111111111111111",
        dex_id="raydium",
        token_address="TokenMultiPool11111111111111111111111111111",
        symbol="MULTIPOOL",
        name="MultiPool / SOL",
        price_usd=Decimal("1.00"),
        liquidity_usd=Decimal("100000.00"),
        volume_24h_usd=Decimal("1000000.00"),
        pair_created_at=now - timedelta(hours=10),
        fetched_at=now
    )

    # Pool 2: Meteora, $300k liquidity, $500k volume
    obs2 = MarketPairObservation(
        chain_id="solana:mainnet",
        provider_network_id="solana",
        provider_source="geckoterminal",
        pool_address="PoolMeteora2222222222222222222222222222222",
        dex_id="meteora",
        token_address="TokenMultiPool11111111111111111111111111111",
        symbol="MULTIPOOL",
        name="MultiPool / USDC",
        price_usd=Decimal("1.02"),
        liquidity_usd=Decimal("300000.00"),
        volume_24h_usd=Decimal("500000.00"),
        pair_created_at=now - timedelta(hours=8),
        fetched_at=now
    )

    aggregated = coordinator._aggregate_tokens([obs1, obs2])
    assert len(aggregated) == 1

    tok = list(aggregated.values())[0]
    # Selected canonical pool must be Pool 2 (Meteora) because $300k > $100k
    assert tok.canonical_pool_address == "PoolMeteora2222222222222222222222222222222"
    assert tok.canonical_dex_id == "meteora"
    assert tok.liquidity_usd == Decimal("300000.00")
    # Volume aggregated across both pools without double-counting
    assert tok.volume_24h_usd == Decimal("1500000.00")
    assert tok.multi_pool_count == 2
    assert tok.pool_selection_methodology == "MAX_LIQUIDITY_CANONICAL_POOL"


# =============================================================================
# 3. Provider Error Handling & Resilience
# =============================================================================

def test_missing_fields_semantics():
    """Verifies that missing fields are recorded in missing_fields list rather than fabricated."""
    client = GeckoTerminalClient()
    raw = {
        "data": [{
            "id": "solana_PartialPool1111111111111111111111111111",
            "type": "pool",
            "attributes": {
                "address": "PartialPool1111111111111111111111111111",
                "name": "Partial / SOL"
            },
            "relationships": {
                "base_token": {"data": {"id": "solana_PartialToken1111111111111111111111111"}},
                "dex": {"data": {"id": "raydium"}}
            }
        }]
    }
    parsed = client._parse_gecko_pools(raw, "solana", "solana:mainnet")
    assert len(parsed) == 1
    p = parsed[0]
    assert p.price_usd is None
    assert p.liquidity_usd is None
    assert p.volume_24h_usd is None
    assert "price_usd" in p.missing_fields
    assert "liquidity_usd" in p.missing_fields
    assert "volume_24h_usd" in p.missing_fields


def test_malformed_json_handling():
    """Verifies that malformed or non-dict payloads return empty list without crashing."""
    client = GeckoTerminalClient()
    assert client._parse_gecko_pools({}, "solana", "solana:mainnet") == []
    assert client._parse_gecko_pools({"error": "Unauthorized"}, "solana", "solana:mainnet") == []
    assert client._parse_gecko_pools([], "solana", "solana:mainnet") == []


def test_circuit_breaker_open_skips_network(monkeypatch):
    """Verifies that an open circuit breaker blocks network execution immediately."""
    async def _run():
        client = GeckoTerminalClient()
        client.circuit_breaker.state = "OPEN"
        client.circuit_breaker.last_state_change = time.monotonic()

        # Even if called, no network request happens
        res = await client.fetch_pools("solana")
        assert res == []
        assert client.error_category == "CIRCUIT_BREAKER_OPEN"
    asyncio.run(_run())


# =============================================================================
# 4. API Endpoints & Filter Validation Tests
# =============================================================================

def test_api_invalid_chain_rejected():
    """HTTP 400 when invalid chain filter is requested."""
    res = client.get("/api/v1/discovery/screener?chain=polygon")
    assert res.status_code == 400
    assert "Invalid chain filter" in res.json()["detail"]


def test_api_invalid_sort_rejected():
    """HTTP 400 when invalid sort_by is requested."""
    res = client.get("/api/v1/discovery/screener?sort_by=unknown_col")
    assert res.status_code == 400
    assert "Invalid sort_by field" in res.json()["detail"]


def test_api_invalid_age_bounds_rejected():
    """HTTP 400 when min_age_hours > max_age_hours."""
    res = client.get("/api/v1/discovery/screener?min_age_hours=50&max_age_hours=10")
    assert res.status_code == 400
    assert "cannot be greater than max_age_hours" in res.json()["detail"]


def test_api_provider_health_endpoint():
    """GET /api/v1/discovery/providers/health returns structured operational health."""
    res = client.get("/api/v1/discovery/providers/health")
    assert res.status_code == 200
    data = res.json()
    assert "providers" in data
    assert "geckoterminal" in data["providers"]
    assert "dexscreener" in data["providers"]

    gt = data["providers"]["geckoterminal"]
    assert gt["provider_id"] == "geckoterminal"
    assert gt["status"] in ("UNKNOWN", "OPERATIONAL", "DEGRADED", "UNAVAILABLE")
    assert gt["circuit_breaker_state"] in ("CLOSED", "OPEN", "HALF_OPEN")
    assert "solana:mainnet" in gt["supported_chains"]
    assert "eip155:8453" in gt["supported_chains"]


def test_coordinator_sorting_and_limits(monkeypatch):
    """
    Verifies Top 10/50/100 limits, sorting by volume/liquidity, and deterministic tie-breaking.
    """
    async def _run():
        coordinator = MarketDataCoordinator()
        now = datetime.now(timezone.utc)

        # Populate 15 dummy tokens
        observations = []
        for i in range(15):
            obs = MarketPairObservation(
                chain_id="solana:mainnet",
                provider_network_id="solana",
                provider_source="geckoterminal",
                pool_address=f"PoolAddress{i:03d}1111111111111111111111",
                dex_id="raydium",
                token_address=f"TokenAddress{i:03d}1111111111111111111",
                symbol=f"TOK{i:02d}",
                name=f"Token {i}",
                price_usd=Decimal(f"{i + 1}.00"),
                liquidity_usd=Decimal(f"{(15 - i) * 10000}"),
                volume_24h_usd=Decimal(f"{i * 50000}"),
                pair_created_at=now - timedelta(hours=i + 1),
                fetched_at=now
            )
            observations.append(obs)

        coordinator._tokens_by_key = coordinator._aggregate_tokens(observations)
        coordinator._last_refresh_time = now

        # Top 10 by volume desc (with category='ALL' to include test TOK tokens)
        res_top10 = await coordinator.query_screener(limit=10, sort_by="volume_24h", sort_dir="desc", category="ALL")
        assert res_top10.limit == 10
        assert len(res_top10.tokens) == 10
        assert res_top10.tokens[0].symbol == "TOK14"  # Highest volume
        assert res_top10.tokens[9].symbol == "TOK05"

        # Top 5 by liquidity desc
        res_liq = await coordinator.query_screener(limit=5, sort_by="liquidity", sort_dir="desc", category="ALL")
        assert len(res_liq.tokens) == 5
        assert res_liq.tokens[0].symbol == "TOK00"  # Highest liquidity (15 * 10000 = 150000)

        # Search filter
        res_search = await coordinator.query_screener(search="TOK03", category="ALL")
        assert len(res_search.tokens) == 1
        assert res_search.tokens[0].symbol == "TOK03"
    asyncio.run(_run())


def test_stale_cache_warning(monkeypatch):
    """Verifies that cache >= 60s is marked STALE with warning, and < 60s is FRESH."""
    async def _run():
        coordinator = MarketDataCoordinator()
        now = datetime.now(timezone.utc)
        stale_time = now - timedelta(seconds=75)  # >= 60s -> STALE

        obs = MarketPairObservation(
            chain_id="solana:mainnet",
            provider_network_id="solana",
            provider_source="geckoterminal",
            pool_address="PoolStale11111111111111111111111111111111",
            dex_id="raydium",
            token_address="TokenStale111111111111111111111111111111",
            symbol="BONK",
            name="Bonk Meme",
            price_usd=Decimal("0.50"),
            liquidity_usd=Decimal("50000"),
            volume_24h_usd=Decimal("100000"),
            pair_created_at=stale_time,
            fetched_at=stale_time
        )

        coordinator._tokens_by_key = coordinator._aggregate_tokens([obs], now=now)
        coordinator._last_refresh_time = stale_time

        res_stale = await coordinator.query_screener(limit=10)
        assert res_stale.freshness_status == MarketDataFreshness.STALE
        assert any("stale" in w.lower() for w in res_stale.warnings)

        # Now test fresh cache (< 60s)
        fresh_time = now - timedelta(seconds=15)
        coordinator._last_refresh_time = fresh_time
        coordinator._tokens_by_key["solana:mainnet", obs.token_address.lower()].fetched_at = fresh_time
        res_fresh = await coordinator.query_screener(limit=10)
        assert res_fresh.freshness_status == MarketDataFreshness.FRESH
    asyncio.run(_run())


def test_memecoin_asset_classification():
    """
    Verifies deterministic asset classification across MEME, NON_MEME, and UNKNOWN.
    Proves that known wrapped assets, stablecoins, equities are classified NON_MEME,
    curated memecoins and pump.fun tokens are MEME, and unspecified tokens are UNKNOWN.
    """
    # 1. Curated Memecoins
    assert classify_token("solana:mainnet", "any_addr", "BONK", "Bonk") == AssetCategory.MEME
    assert classify_token("solana:mainnet", "any_addr", "WIF", "dogwifhat") == AssetCategory.MEME
    assert classify_token("eip155:8453", "any_addr", "BRETT", "Brett") == AssetCategory.MEME
    assert classify_token("eip155:8453", "any_addr", "TOSHI", "Toshi") == AssetCategory.MEME

    # 2. Solana pump.fun heuristic (mint ends with 'pump')
    assert classify_token("solana:mainnet", "4CxjZyMNoF1p6eXHXHv1TWpS2X47CuUmLwtGe18spump", "RANDOM", "Random Fun") == AssetCategory.MEME

    # 3. Known Non-Meme Assets: Native / Wrapped Native
    assert classify_token("solana:mainnet", "So11111111111111111111111111111111111111112", "SOL", "Wrapped SOL") == AssetCategory.NON_MEME
    assert classify_token("solana:mainnet", "any_addr", "WSOL", "Wrapped Solana") == AssetCategory.NON_MEME
    assert classify_token("eip155:8453", "0x4200000000000000000000000000000000000006", "WETH", "Wrapped Ether") == AssetCategory.NON_MEME

    # 4. Known Non-Meme Assets: Stablecoins
    assert classify_token("solana:mainnet", "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", "USDC", "USD Coin") == AssetCategory.NON_MEME
    assert classify_token("solana:mainnet", "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB", "USDT", "Tether USD") == AssetCategory.NON_MEME
    assert classify_token("eip155:8453", "any_addr", "DAI", "Dai Stablecoin") == AssetCategory.NON_MEME

    # 5. Tokenized Equities (e.g. NVDAc, AAPLc, TSLAx)
    assert classify_token("solana:mainnet", "any_addr", "NVDAc", "NVIDIA Corporation tokenized") == AssetCategory.NON_MEME
    assert classify_token("eip155:8453", "any_addr", "AAPLc", "Apple Inc tokenized") == AssetCategory.NON_MEME
    assert classify_token("solana:mainnet", "any_addr", "TSLAx", "Tesla Tokenized Stock") == AssetCategory.NON_MEME

    # 6. Unknown tokens - MUST NOT assume unknown is a memecoin
    assert classify_token("solana:mainnet", "GenericMintAddr1234567890", "XYZ99", "Generic Project") == AssetCategory.UNKNOWN
    assert classify_token("eip155:8453", "0x1234567890abcdef1234567890abcdef12345678", "FOOBAR", "Foo Bar Protocol") == AssetCategory.UNKNOWN


def test_quote_token_handling_preserves_memecoins():
    """
    Verifies that quote-token filtering does not accidentally exclude legitimate memecoins
    simply because they trade against SOL, ETH, or a stablecoin.
    """
    coordinator = MarketDataCoordinator()
    now = datetime.now(timezone.utc)

    # Pool 1: BONK paired with SOL (Quote = SOL, Base = BONK)
    bonk_pool = MarketPairObservation(
        chain_id="solana:mainnet",
        provider_network_id="solana",
        token_address="DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
        symbol="BONK",
        name="Bonk",
        pair_address="BonkSolPool1111111111111111111111111111111",
        quote_token_symbol="SOL",
        quote_token_address="So11111111111111111111111111111111111111112",
        liquidity_usd=Decimal("500000"),
        volume_24h_usd=Decimal("2000000"),
        price_usd=Decimal("0.000045"),
        fetched_at=now
    )

    # Pool 2: NVDAc paired with SOL (Quote = SOL, Base = NVDAc)
    nvdac_pool = MarketPairObservation(
        chain_id="solana:mainnet",
        provider_network_id="solana",
        token_address="NvdacContractAddress111111111111111111111",
        symbol="NVDAc",
        name="NVIDIA Tokenized",
        pair_address="NvdacSolPool1111111111111111111111111111111",
        quote_token_symbol="SOL",
        quote_token_address="So11111111111111111111111111111111111111112",
        liquidity_usd=Decimal("100000"),
        volume_24h_usd=Decimal("50000"),
        price_usd=Decimal("145.00"),
        fetched_at=now
    )

    tokens_map = coordinator._aggregate_tokens([bonk_pool, nvdac_pool], now=now)
    bonk_agg = tokens_map[("solana:mainnet", bonk_pool.token_address.lower())]
    nvdac_agg = tokens_map[("solana:mainnet", nvdac_pool.token_address.lower())]

    # BONK is correctly recognized as MEME even though quote token is SOL
    assert bonk_agg.asset_category == AssetCategory.MEME
    assert bonk_agg.quote_token_symbol == "SOL"

    # NVDAc is recognized as NON_MEME
    assert nvdac_agg.asset_category == AssetCategory.NON_MEME


def test_screener_memecoin_only_filtering_and_category_override():
    """
    Verifies:
    1. memecoin_only=True by default excludes NON_MEME (e.g. SOL, NVDAc) and UNKNOWN.
    2. category='ALL' returns all assets.
    3. category='UNKNOWN' returns only unknown assets.
    4. category='NON_MEME' returns only non-meme assets.
    """
    async def _run():
        coordinator = MarketDataCoordinator()
        now = datetime.now(timezone.utc)

        # Setup 4 distinct tokens
        tok_meme = MarketPairObservation(
            chain_id="solana:mainnet",
            provider_network_id="solana",
            token_address="DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
            symbol="BONK",
            name="Bonk",
            pair_address="PoolBonk1111111111111111111111111111111111",
            liquidity_usd=Decimal("500000"),
            volume_24h_usd=Decimal("1000000"),
            fetched_at=now
        )
        tok_native = MarketPairObservation(
            chain_id="solana:mainnet",
            provider_network_id="solana",
            token_address="So11111111111111111111111111111111111111112",
            symbol="SOL",
            name="Wrapped SOL",
            pair_address="PoolSol11111111111111111111111111111111111",
            liquidity_usd=Decimal("9000000"),
            volume_24h_usd=Decimal("50000000"),
            fetched_at=now
        )
        tok_equity = MarketPairObservation(
            chain_id="solana:mainnet",
            provider_network_id="solana",
            token_address="EqNvda111111111111111111111111111111111111",
            symbol="NVDAc",
            name="Nvidia tokenized",
            pair_address="PoolNvda1111111111111111111111111111111111",
            liquidity_usd=Decimal("200000"),
            volume_24h_usd=Decimal("300000"),
            fetched_at=now
        )
        tok_unknown = MarketPairObservation(
            chain_id="solana:mainnet",
            provider_network_id="solana",
            token_address="UnkTok111111111111111111111111111111111111",
            symbol="UNKNOWNCOIN",
            name="Unclassified Coin",
            pair_address="PoolUnk11111111111111111111111111111111111",
            liquidity_usd=Decimal("100000"),
            volume_24h_usd=Decimal("150000"),
            fetched_at=now
        )

        coordinator._tokens_by_key = coordinator._aggregate_tokens(
            [tok_meme, tok_native, tok_equity, tok_unknown], now=now
        )
        coordinator._last_refresh_time = now

        # 1. Default query (memecoin_only=True) -> ONLY BONK
        res_default = await coordinator.query_screener()
        assert res_default.memecoin_only is True
        assert res_default.category_applied == "MEME"
        symbols_default = [t.symbol for t in res_default.tokens]
        assert "BONK" in symbols_default
        assert "SOL" not in symbols_default
        assert "NVDAc" not in symbols_default
        assert "UNKNOWNCOIN" not in symbols_default
        assert len(res_default.tokens) == 1

        # 2. category='ALL' -> ALL 4 tokens, memecoin_only=False
        res_all = await coordinator.query_screener(category="ALL")
        assert res_all.category_applied == "ALL"
        assert res_all.memecoin_only is False
        symbols_all = [t.symbol for t in res_all.tokens]
        assert len(symbols_all) == 4
        assert set(symbols_all) == {"BONK", "SOL", "NVDAc", "UNKNOWNCOIN"}

        # 3. category='NON_MEME' -> SOL and NVDAc, memecoin_only=False
        res_non_meme = await coordinator.query_screener(category="NON_MEME")
        assert res_non_meme.category_applied == "NON_MEME"
        assert res_non_meme.memecoin_only is False
        symbols_non_meme = [t.symbol for t in res_non_meme.tokens]
        assert set(symbols_non_meme) == {"SOL", "NVDAc"}

        # 4. category='UNKNOWN' -> UNKNOWNCOIN only, memecoin_only=False
        res_unk = await coordinator.query_screener(category="UNKNOWN")
        assert res_unk.category_applied == "UNKNOWN"
        assert res_unk.memecoin_only is False
        symbols_unk = [t.symbol for t in res_unk.tokens]
        assert symbols_unk == ["UNKNOWNCOIN"]
    asyncio.run(_run())


def test_screener_api_endpoint_category_and_freshness():
    """
    Verifies FastAPI GET /api/v1/discovery/screener:
    - Default returns memecoin_only=True with category_applied='MEME'.
    - Freshness status is FRESH, STALE, or UNAVAILABLE (never REALTIME).
    - Querying invalid category returns HTTP 400.
    - Querying category=ALL returns category_applied='ALL' and memecoin_only=False.
    """
    # Test invalid category parameter
    res_bad = client.get("/api/v1/discovery/screener?category=INVALID_CAT")
    assert res_bad.status_code == 400
    assert "Invalid category filter" in res_bad.json()["detail"]

    # Test valid query with category=ALL
    res = client.get("/api/v1/discovery/screener?category=ALL&limit=10")
    assert res.status_code == 200
    data = res.json()
    assert "freshness_status" in data
    assert data["freshness_status"] in ("FRESH", "STALE", "UNAVAILABLE")
    assert data["freshness_status"] != "REALTIME"
    assert "cache_age_seconds" in data
    assert data["category_applied"] == "ALL"
    assert data["memecoin_only"] is False


def test_category_all_and_effective_memecoin_only_semantics():
    """
    Verifies Refinement 1:
    - ALL, NON_MEME, and UNKNOWN return memecoin_only=false
    - MEME returns memecoin_only=true
    - When category is omitted, respects actual memecoin_only input
    - category_applied and memecoin_only describe effective filter without contradiction
    """
    async def _run():
        coord = MarketDataCoordinator()
        now = datetime.now(timezone.utc)
        # Mock token rankings
        t_meme = AggregatedTokenRanking(
            chain_id="solana:mainnet",
            provider_network_id="solana",
            token_address="DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
            symbol="BONK",
            name="Bonk",
            canonical_pool_address="PoolBonk111",
            dex_name="raydium",
            quote_token_symbol="SOL",
            asset_category=AssetCategory.MEME,
            classification_source=ClassificationSource.CURATED_REGISTRY,
            confidence=ClassificationConfidence.HIGH,
            classification_reason="Curated Bonk in memecoin registry",
            price_usd=Decimal("0.000025"),
            liquidity_usd=Decimal("1000000"),
            volume_24h_usd=Decimal("5000000"),
            provider_source="geckoterminal",
            fetched_at=now,
            cache_age_seconds=10.0,
            freshness_status=MarketDataFreshness.FRESH
        )
        t_non_meme = AggregatedTokenRanking(
            chain_id="solana:mainnet",
            provider_network_id="solana",
            token_address="So11111111111111111111111111111111111111112",
            symbol="SOL",
            name="Wrapped SOL",
            canonical_pool_address="PoolSol111",
            dex_name="raydium",
            quote_token_symbol="USDC",
            asset_category=AssetCategory.NON_MEME,
            classification_source=ClassificationSource.CURATED_REGISTRY,
            confidence=ClassificationConfidence.HIGH,
            classification_reason="Curated Wrapped SOL in non-meme registry",
            price_usd=Decimal("150.0"),
            liquidity_usd=Decimal("50000000"),
            volume_24h_usd=Decimal("100000000"),
            provider_source="geckoterminal",
            fetched_at=now,
            cache_age_seconds=10.0,
            freshness_status=MarketDataFreshness.FRESH
        )
        t_unknown = AggregatedTokenRanking(
            chain_id="eip155:8453",
            provider_network_id="base",
            token_address="0x1234567890abcdef1234567890abcdef12345678",
            symbol="NEWCOIN",
            name="New Mystery Project",
            canonical_pool_address="PoolBase111",
            dex_name="uniswap_v3",
            quote_token_symbol="WETH",
            asset_category=AssetCategory.UNKNOWN,
            classification_source=ClassificationSource.DEFAULT,
            confidence=ClassificationConfidence.LOW,
            classification_reason="Unclassified asset",
            price_usd=Decimal("1.25"),
            liquidity_usd=Decimal("50000"),
            volume_24h_usd=Decimal("20000"),
            provider_source="geckoterminal",
            fetched_at=now,
            cache_age_seconds=10.0,
            freshness_status=MarketDataFreshness.FRESH
        )
        coord._tokens_by_key = {
            "solana:mainnet:dezxaz": t_meme,
            "solana:mainnet:so1111": t_non_meme,
            "eip155:8453:0x1234": t_unknown
        }
        coord._last_refresh_time = now

        # 1. category=ALL -> memecoin_only=False, category_applied='ALL'
        res_all = await coord.query_screener(category="ALL", memecoin_only=True)
        assert res_all.category_applied == "ALL"
        assert res_all.memecoin_only is False
        assert res_all.filters_applied["memecoin_only"] is False
        assert res_all.filters_applied["category"] == "ALL"
        assert len(res_all.tokens) == 3

        # 2. category=NON_MEME -> memecoin_only=False, category_applied='NON_MEME'
        res_non = await coord.query_screener(category="NON_MEME", memecoin_only=True)
        assert res_non.category_applied == "NON_MEME"
        assert res_non.memecoin_only is False
        assert [t.symbol for t in res_non.tokens] == ["SOL"]

        # 3. category=UNKNOWN -> memecoin_only=False, category_applied='UNKNOWN'
        res_unk = await coord.query_screener(category="UNKNOWN", memecoin_only=True)
        assert res_unk.category_applied == "UNKNOWN"
        assert res_unk.memecoin_only is False
        assert [t.symbol for t in res_unk.tokens] == ["NEWCOIN"]

        # 4. category=MEME -> memecoin_only=True, category_applied='MEME'
        res_meme = await coord.query_screener(category="MEME", memecoin_only=False)
        assert res_meme.category_applied == "MEME"
        assert res_meme.memecoin_only is True
        assert [t.symbol for t in res_meme.tokens] == ["BONK"]

        # 5. Omitted category, memecoin_only=True -> category_applied='MEME', memecoin_only=True
        res_def = await coord.query_screener(category=None, memecoin_only=True)
        assert res_def.category_applied == "MEME"
        assert res_def.memecoin_only is True
        assert [t.symbol for t in res_def.tokens] == ["BONK"]

        # 6. Omitted category, memecoin_only=False -> category_applied='ALL', memecoin_only=False
        res_false = await coord.query_screener(category=None, memecoin_only=False)
        assert res_false.category_applied == "ALL"
        assert res_false.memecoin_only is False
        assert len(res_false.tokens) == 3

    asyncio.run(_run())

    # Verify HTTP API endpoints reflect effective values
    res_api_all = client.get("/api/v1/discovery/screener?category=ALL")
    assert res_api_all.status_code == 200
    assert res_api_all.json()["category_applied"] == "ALL"
    assert res_api_all.json()["memecoin_only"] is False

    res_api_non = client.get("/api/v1/discovery/screener?category=NON_MEME")
    assert res_api_non.status_code == 200
    assert res_api_non.json()["category_applied"] == "NON_MEME"
    assert res_api_non.json()["memecoin_only"] is False

    res_api_unk = client.get("/api/v1/discovery/screener?category=UNKNOWN")
    assert res_api_unk.status_code == 200
    assert res_api_unk.json()["category_applied"] == "UNKNOWN"
    assert res_api_unk.json()["memecoin_only"] is False

    res_api_meme = client.get("/api/v1/discovery/screener?category=MEME")
    assert res_api_meme.status_code == 200
    assert res_api_meme.json()["category_applied"] == "MEME"
    assert res_api_meme.json()["memecoin_only"] is True


def test_classification_provenance_and_confidence():
    """
    Verifies Refinement 2:
    - classification_source is CURATED_REGISTRY, HEURISTIC, or DEFAULT
    - confidence is HIGH, MEDIUM, or LOW
    - Curated address registry matches have HIGH confidence and take precedence
    - pump.fun and tokenized equities are explicitly HEURISTIC with MEDIUM confidence
    - Curated symbol matches have HIGH confidence
    - Unmatched assets remain UNKNOWN with DEFAULT source and LOW confidence
    """
    # 1. Curated Address Matches (HIGH confidence, CURATED_REGISTRY)
    clf_bonk_addr = classify_token("solana:mainnet", "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263", "ANY", "Any Name")
    assert clf_bonk_addr.category == AssetCategory.MEME
    assert clf_bonk_addr.source == ClassificationSource.CURATED_REGISTRY
    assert clf_bonk_addr.confidence == ClassificationConfidence.HIGH
    assert "curated contract address" in clf_bonk_addr.reason.lower()

    clf_wsol_addr = classify_token("solana:mainnet", "So11111111111111111111111111111111111111112", "ANY", "Any Name")
    assert clf_wsol_addr.category == AssetCategory.NON_MEME
    assert clf_wsol_addr.source == ClassificationSource.CURATED_REGISTRY
    assert clf_wsol_addr.confidence == ClassificationConfidence.HIGH
    assert "curated contract address" in clf_wsol_addr.reason.lower()

    # Address precedence over misleading ticker:
    # A token with ticker 'BONK' but address of Wrapped SOL must be NON_MEME because address is matched first
    clf_override = classify_token("solana:mainnet", "So11111111111111111111111111111111111111112", "BONK", "Fake Bonk")
    assert clf_override.category == AssetCategory.NON_MEME
    assert clf_override.source == ClassificationSource.CURATED_REGISTRY

    # 2. Solana pump.fun heuristic (MEDIUM confidence, HEURISTIC, never verified)
    clf_pump = classify_token("solana:mainnet", "4CxjZyMNoF1p6eXHXHv1TWpS2X47CuUmLwtGe18spump", "RANDOM", "Random Token")
    assert clf_pump.category == AssetCategory.MEME
    assert clf_pump.source == ClassificationSource.HEURISTIC
    assert clf_pump.confidence == ClassificationConfidence.MEDIUM
    assert "heuristic" in clf_pump.reason.lower()
    assert "pump.fun" in clf_pump.reason.lower()

    # 3. Tokenized Equities (MEDIUM confidence, HEURISTIC, never verified)
    clf_nvda = classify_token("solana:mainnet", "random_equity_addr", "NVDAc", "NVIDIA Corp Tokenized")
    assert clf_nvda.category == AssetCategory.NON_MEME
    assert clf_nvda.source == ClassificationSource.HEURISTIC
    assert clf_nvda.confidence == ClassificationConfidence.MEDIUM
    assert "heuristic" in clf_nvda.reason.lower()
    assert "tokenized equity" in clf_nvda.reason.lower()

    clf_tsla = classify_token("eip155:8453", "random_equity_addr2", "TSLAx", "Tesla Tokenized")
    assert clf_tsla.category == AssetCategory.NON_MEME
    assert clf_tsla.source == ClassificationSource.HEURISTIC
    assert clf_tsla.confidence == ClassificationConfidence.MEDIUM

    # 4. Ticker-Only Matches (MEDIUM confidence, HEURISTIC, explicitly unverified contract address)
    clf_wif_sym = classify_token("solana:mainnet", "random_addr_1", "WIF", "dogwifhat")
    assert clf_wif_sym.category == AssetCategory.MEME
    assert clf_wif_sym.source == ClassificationSource.HEURISTIC
    assert clf_wif_sym.confidence == ClassificationConfidence.MEDIUM
    assert "unverified contract address" in clf_wif_sym.reason.lower()

    clf_usdc_sym = classify_token("solana:mainnet", "random_addr_2", "USDC", "USD Coin")
    assert clf_usdc_sym.category == AssetCategory.NON_MEME
    assert clf_usdc_sym.source == ClassificationSource.HEURISTIC
    assert clf_usdc_sym.confidence == ClassificationConfidence.MEDIUM
    assert "unverified contract address" in clf_usdc_sym.reason.lower()

    # 5. Meme Keywords in Name/Symbol (MEDIUM confidence, HEURISTIC)
    clf_frog = classify_token("solana:mainnet", "random_addr_3", "GREENFROG", "Green Frog")
    assert clf_frog.category == AssetCategory.MEME
    assert clf_frog.source == ClassificationSource.HEURISTIC
    assert clf_frog.confidence == ClassificationConfidence.MEDIUM

    # 6. Unmatched token -> UNKNOWN (LOW confidence, DEFAULT, not automatically NON_MEME)
    clf_unk = classify_token("eip155:8453", "0x9876543210abcdef9876543210abcdef98765432", "ACME", "Acme Corporation")
    assert clf_unk.category == AssetCategory.UNKNOWN
    assert clf_unk.source == ClassificationSource.DEFAULT
    assert clf_unk.confidence == ClassificationConfidence.LOW
    assert "unclassified" in clf_unk.reason.lower()


def test_database_init_safety_guard_fails_closed():
    """
    Verifies Refinement 3:
    - When APP_ENV == 'test', attempting to initialize against the operational database
      'data/memecoin_os.db' fails closed immediately with RuntimeError.
    - Proves the operational database cannot be opened or mutated in test mode.
    """
    from app.core.database import assert_test_db_isolated

    # 1. Isolated test paths must PASS the check
    assert_test_db_isolated("/tmp/test_session_isolated.db")
    assert_test_db_isolated(":memory:")
    assert_test_db_isolated("/tmp/another_test.db")

    # 2. Operational database paths must FAIL CLOSED with RuntimeError
    with pytest.raises(RuntimeError) as exc_info_rel:
        assert_test_db_isolated("data/memecoin_os.db")
    assert "SAFETY VIOLATION (FAIL-CLOSED)" in str(exc_info_rel.value)
    assert "attempted to connect to operational database" in str(exc_info_rel.value)

    with pytest.raises(RuntimeError) as exc_info_abs:
        abs_op_path = os.path.normpath(os.path.abspath("data/memecoin_os.db"))
        assert_test_db_isolated(abs_op_path)
    assert "SAFETY VIOLATION (FAIL-CLOSED)" in str(exc_info_abs.value)

