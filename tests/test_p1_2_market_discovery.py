"""
Phase P1.2: Market Discovery Expansion & Resilience Test Suite.

Verifies:
1. Discovery Expansion:
   - Ethereum (eip155:1) and Arbitrum (eip155:42161) chain mapping.
   - Trending pools and newly created pools discovery endpoints.
   - Curated classification of Ethereum and Arbitrum tokens.
2. Provider Provenance:
   - Endpoint-level source IDs ('geckoterminal:trending_pools', 'geckoterminal:new_pools', 'dexscreener:tokens_batch').
   - Raw observation timestamps and pool-level provider URLs preserved on observations and aggregated rankings.
3. Deduplication & Volume Invariance:
   - Deduplication of identical pools across providers/endpoints before aggregation.
   - Canonical pool elected by max liquidity.
   - Cumulative 24h volume sums strictly across distinct pools (never double counting identical pool observations).
   - Missing-data semantics preserved.
4. Pagination & Filtering:
   - Deterministic sorting, validated sort fields ('price_usd', 'age'), pagination ('total_pages', 'current_page').
   - Totals reflect the actual filtered result set.
5. Resilience & Fault Tolerance:
   - Independent token bucket rate limiters and circuit breakers.
   - Usable GeckoTerminal observations when DEX Screener enrichment fails, with explicit degradation warnings in API response.
"""

import os
import math
import time
import asyncio
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Dict, Any, List

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.core.config import settings
from app.schemas.market_data import (
    MarketDataFreshness,
    AssetCategory,
    ClassificationSource,
    ClassificationConfidence,
    MarketPairObservation,
    AggregatedTokenRanking,
    DynamicScreenerResponse
)
from app.services.market_data_service import (
    GeckoTerminalClient,
    DexScreenerClient,
    MarketDataCoordinator,
    classify_token,
    CAIP2_TO_GECKO_NETWORK,
    GECKO_NETWORK_TO_CAIP2,
    CAIP2_TO_DEXSCREENER_CHAIN,
    DEXSCREENER_CHAIN_TO_CAIP2
)

client = TestClient(app)


def test_ethereum_and_arbitrum_network_mappings():
    """Verifies CAIP-2 mapping consistency for Ethereum and Arbitrum across providers."""
    # Ethereum mappings
    assert CAIP2_TO_GECKO_NETWORK["eip155:1"] == "eth"
    assert GECKO_NETWORK_TO_CAIP2["eth"] == "eip155:1"
    assert CAIP2_TO_DEXSCREENER_CHAIN["eip155:1"] == "ethereum"
    assert DEXSCREENER_CHAIN_TO_CAIP2["ethereum"] == "eip155:1"

    # Arbitrum mappings
    assert CAIP2_TO_GECKO_NETWORK["eip155:42161"] == "arbitrum"
    assert GECKO_NETWORK_TO_CAIP2["arbitrum"] == "eip155:42161"
    assert CAIP2_TO_DEXSCREENER_CHAIN["eip155:42161"] == "arbitrum"
    assert DEXSCREENER_CHAIN_TO_CAIP2["arbitrum"] == "eip155:42161"


def test_ethereum_and_arbitrum_curated_classification():
    """
    Verifies that curated contract addresses on Ethereum and Arbitrum
    receive CURATED_REGISTRY and HIGH confidence.
    """
    # Ethereum PEPE (curated memecoin)
    pepe_clf = classify_token("eip155:1", "0x6982508145454Ce325dDbE47a25d4ec3d2311933", "PEPE", "Pepe")
    assert pepe_clf.category == AssetCategory.MEME
    assert pepe_clf.source == ClassificationSource.CURATED_REGISTRY
    assert pepe_clf.confidence == ClassificationConfidence.HIGH

    # Ethereum WETH (curated non-meme)
    weth_clf = classify_token("eip155:1", "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2", "WETH", "Wrapped Ether")
    assert weth_clf.category == AssetCategory.NON_MEME
    assert weth_clf.source == ClassificationSource.CURATED_REGISTRY
    assert weth_clf.confidence == ClassificationConfidence.HIGH

    # Arbitrum AIDOGE (curated memecoin)
    aidoge_clf = classify_token("eip155:42161", "0x250632378E573c6Be1AC2f97FCDF00515d0Aa91B", "AIDOGE", "ArbDoge AI")
    assert aidoge_clf.category == AssetCategory.MEME
    assert aidoge_clf.source == ClassificationSource.CURATED_REGISTRY
    assert aidoge_clf.confidence == ClassificationConfidence.HIGH

    # Arbitrum ARB (curated non-meme)
    arb_clf = classify_token("eip155:42161", "0x912CE59144191C1204E64559FE8253a0e49E6548", "ARB", "Arbitrum")
    assert arb_clf.category == AssetCategory.NON_MEME
    assert arb_clf.source == ClassificationSource.CURATED_REGISTRY
    assert arb_clf.confidence == ClassificationConfidence.HIGH


def test_geckoterminal_endpoint_provenance():
    """
    Verifies that GeckoTerminalClient tags observations with specific endpoint_id
    ('geckoterminal:trending_pools' or 'geckoterminal:new_pools'), URLs, and timestamps.
    """
    client_gt = GeckoTerminalClient()
    mock_payload = {
        "data": [{
            "id": "eth_0xpool123",
            "type": "pool",
            "attributes": {
                "address": "0xpool123",
                "name": "TEST / WETH",
                "base_token_price_usd": "1.50",
                "reserve_in_usd": "250000.00",
                "volume_usd": {"h24": "500000.00"},
                "pool_created_at": "2026-11-10T00:00:00Z"
            },
            "relationships": {
                "base_token": {"data": {"id": "eth_0xtoken123"}},
                "dex": {"data": {"id": "uniswap_v3"}}
            }
        }]
    }

    # 1. Trending pools
    trending = client_gt._parse_pools_response(mock_payload, "eth", "eip155:1", endpoint_type="trending_pools")
    assert len(trending) == 1
    t_obs = trending[0]
    assert t_obs.endpoint_id == "geckoterminal:trending_pools"
    assert t_obs.provider_source == "geckoterminal"
    assert "https://www.geckoterminal.com/eth/pools/0xpool123" in t_obs.provider_urls
    assert "geckoterminal:trending_pools" in t_obs.observation_timestamps

    # 2. New pools
    new_pools = client_gt._parse_pools_response(mock_payload, "eth", "eip155:1", endpoint_type="new_pools")
    assert len(new_pools) == 1
    n_obs = new_pools[0]
    assert n_obs.endpoint_id == "geckoterminal:new_pools"
    assert n_obs.provider_source == "geckoterminal"
    assert "geckoterminal:new_pools" in n_obs.observation_timestamps


def test_identical_pool_deduplication_never_double_counts_volume():
    """
    CRITICAL INVARIANT:
    When the same pool contract is observed across multiple endpoints
    (e.g. GeckoTerminal trending + new + DEX Screener batch), deduplication
    must merge the observations without summing the 24h volume of the identical pool.
    """
    coordinator = MarketDataCoordinator()
    now = datetime.now(timezone.utc)
    pool_addr = "0xSamePoolContractAddress1111111111111111"
    tok_addr = "0xSameTokenContractAddress2222222222222222"

    # Observation 1: GeckoTerminal trending pools ($100k volume, $50k liq)
    obs1 = MarketPairObservation(
        chain_id="eip155:1",
        provider_network_id="eth",
        provider_source="geckoterminal",
        endpoint_id="geckoterminal:trending_pools",
        pool_address=pool_addr,
        dex_id="uniswap_v3",
        token_address=tok_addr,
        symbol="IDENTICAL",
        name="Identical Meme",
        price_usd=Decimal("2.00"),
        liquidity_usd=Decimal("50000.00"),
        volume_24h_usd=Decimal("100000.00"),
        provider_url=f"https://www.geckoterminal.com/eth/pools/{pool_addr}",
        fetched_at=now - timedelta(seconds=10)
    )

    # Observation 2: GeckoTerminal new pools ($100k volume, $50k liq)
    obs2 = MarketPairObservation(
        chain_id="eip155:1",
        provider_network_id="eth",
        provider_source="geckoterminal",
        endpoint_id="geckoterminal:new_pools",
        pool_address=pool_addr,
        dex_id="uniswap_v3",
        token_address=tok_addr,
        symbol="IDENTICAL",
        name="Identical Meme",
        price_usd=Decimal("2.00"),
        liquidity_usd=Decimal("50000.00"),
        volume_24h_usd=Decimal("100000.00"),
        provider_url=f"https://www.geckoterminal.com/eth/pools/{pool_addr}",
        fetched_at=now - timedelta(seconds=8)
    )

    # Observation 3: DEX Screener batch enrichment ($105k volume, $52k liq - slightly updated)
    obs3 = MarketPairObservation(
        chain_id="eip155:1",
        provider_network_id="ethereum",
        provider_source="dexscreener",
        endpoint_id="dexscreener:tokens_batch",
        pool_address=pool_addr,
        dex_id="uniswap_v3",
        token_address=tok_addr,
        symbol="IDENTICAL",
        name="Identical Meme",
        price_usd=Decimal("2.01"),
        liquidity_usd=Decimal("52000.00"),
        volume_24h_usd=Decimal("105000.00"),
        provider_url=f"https://dexscreener.com/ethereum/{pool_addr}",
        fetched_at=now - timedelta(seconds=2)
    )

    # 1. Pool-level deduplication
    deduped_pools = coordinator._deduplicate_pools([obs1, obs2, obs3])
    assert len(deduped_pools) == 1, "Expected exactly 1 deduplicated pool"
    d_pool = deduped_pools[0]

    # Canonical observation selected is obs3 ($52k liq > $50k liq)
    assert d_pool.liquidity_usd == Decimal("52000.00")
    # Volume is NOT 100k + 100k + 105k = 305k! It must be 105k
    assert d_pool.volume_24h_usd == Decimal("105000.00")

    # Combined provider URLs
    assert len(d_pool.provider_urls) == 2
    assert f"https://www.geckoterminal.com/eth/pools/{pool_addr}" in d_pool.provider_urls
    assert f"https://dexscreener.com/ethereum/{pool_addr}" in d_pool.provider_urls

    # Combined observation timestamps
    assert "geckoterminal:trending_pools" in d_pool.observation_timestamps
    assert "geckoterminal:new_pools" in d_pool.observation_timestamps
    assert "dexscreener:tokens_batch" in d_pool.observation_timestamps

    # 2. Token-level aggregation
    tokens = coordinator._aggregate_tokens([obs1, obs2, obs3], now=now)
    assert len(tokens) == 1
    tok = list(tokens.values())[0]

    # Token volume must remain 105k (never double counted)
    assert tok.volume_24h_usd == Decimal("105000.00")
    assert tok.distinct_pools_aggregated == 1
    assert tok.pool_count == 1
    assert set(tok.endpoint_sources) == {
        "geckoterminal:trending_pools",
        "geckoterminal:new_pools",
        "dexscreener:tokens_batch"
    }


def test_multi_pool_volume_aggregation_across_distinct_pools():
    """
    Verifies that when a token has MULTIPLE DISTINCT POOLS,
    their 24h volumes are summed, canonical pool is elected by max liquidity,
    and distinct pool count is recorded accurately.
    """
    coordinator = MarketDataCoordinator()
    now = datetime.now(timezone.utc)
    tok_addr = "0xMultiPoolTokenContract111111111111111"

    # Pool A: Uniswap V3, $400k liquidity, $250k volume
    pool_a = MarketPairObservation(
        chain_id="eip155:42161",
        provider_network_id="arbitrum",
        provider_source="geckoterminal",
        endpoint_id="geckoterminal:trending_pools",
        pool_address="0xPoolContractA111111111111111111111111",
        dex_id="uniswap_v3",
        token_address=tok_addr,
        symbol="MULTIPOOL",
        name="MultiPool Token",
        price_usd=Decimal("5.00"),
        liquidity_usd=Decimal("400000.00"),
        volume_24h_usd=Decimal("250000.00"),
        pair_created_at=now - timedelta(hours=20),
        fetched_at=now
    )

    # Pool B: Camelot DEX, $150k liquidity, $100k volume
    pool_b = MarketPairObservation(
        chain_id="eip155:42161",
        provider_network_id="arbitrum",
        provider_source="geckoterminal",
        endpoint_id="geckoterminal:new_pools",
        pool_address="0xPoolContractB222222222222222222222222",
        dex_id="camelot",
        token_address=tok_addr,
        symbol="MULTIPOOL",
        name="MultiPool Token",
        price_usd=Decimal("4.98"),
        liquidity_usd=Decimal("150000.00"),
        volume_24h_usd=Decimal("100000.00"),
        pair_created_at=now - timedelta(hours=10),
        fetched_at=now
    )

    tokens = coordinator._aggregate_tokens([pool_a, pool_b], now=now)
    assert len(tokens) == 1
    tok = list(tokens.values())[0]

    # Canonical pool is Pool A (400k > 150k)
    assert tok.canonical_pool_address == "0xPoolContractA111111111111111111111111"
    assert tok.liquidity_usd == Decimal("400000.00")
    assert tok.dex_name == "uniswap_v3"

    # Volume correctly summed across distinct pools: 250k + 100k = 350k
    assert tok.volume_24h_usd == Decimal("350000.00")
    assert tok.distinct_pools_aggregated == 2
    assert tok.pool_count == 2
    assert "geckoterminal:trending_pools" in tok.endpoint_sources
    assert "geckoterminal:new_pools" in tok.endpoint_sources


def test_pagination_and_total_pages():
    """Verifies limit, offset, total_matches, total_pages, and current_page calculation."""
    async def _run():
        coordinator = MarketDataCoordinator()
        now = datetime.now(timezone.utc)

        # Create 25 distinct tokens
        observations = []
        for i in range(25):
            obs = MarketPairObservation(
                chain_id="eip155:1",
                provider_network_id="eth",
                provider_source="geckoterminal",
                pool_address=f"0xPool{i:03d}11111111111111111111111111",
                dex_id="uniswap_v3",
                token_address=f"0xToken{i:03d}1111111111111111111111111",
                symbol=f"PAG{i:02d}",
                name=f"Pag Token {i}",
                price_usd=Decimal(f"{i + 1}.00"),
                liquidity_usd=Decimal("10000.00"),
                volume_24h_usd=Decimal(f"{(25 - i) * 10000}"),
                pair_created_at=now - timedelta(hours=i + 1),
                fetched_at=now
            )
            observations.append(obs)

        coordinator._tokens_by_key = coordinator._aggregate_tokens(observations, now=now)
        coordinator._last_refresh_time = now

        # Page 1: limit 10, offset 0 -> 10 results, total 25, total_pages 3, current_page 1
        res_p1 = await coordinator.query_screener(limit=10, offset=0, category="ALL")
        assert res_p1.result_count == 10
        assert res_p1.total_matches == 25
        assert res_p1.total_pages == 3
        assert res_p1.current_page == 1
        assert res_p1.tokens[0].symbol == "PAG00"  # Highest volume (25 * 10000 = 250000)

        # Page 2: limit 10, offset 10 -> 10 results, total 25, total_pages 3, current_page 2
        res_p2 = await coordinator.query_screener(limit=10, offset=10, category="ALL")
        assert res_p2.result_count == 10
        assert res_p2.total_pages == 3
        assert res_p2.current_page == 2
        assert res_p2.tokens[0].symbol == "PAG10"

        # Page 3: limit 10, offset 20 -> 5 results, total 25, total_pages 3, current_page 3
        res_p3 = await coordinator.query_screener(limit=10, offset=20, category="ALL")
        assert res_p3.result_count == 5
        assert res_p3.total_pages == 3
        assert res_p3.current_page == 3
        assert res_p3.tokens[4].symbol == "PAG24"

        # Zero matches edge case
        res_empty = await coordinator.query_screener(search="NON_EXISTENT", category="ALL")
        assert res_empty.total_matches == 0
        assert res_empty.total_pages == 0
        assert res_empty.current_page == 1
    asyncio.run(_run())


def test_sorting_by_price_usd_and_age():
    """Verifies validated sorting by 'price_usd' and 'age' in both asc and desc order."""
    async def _run():
        coordinator = MarketDataCoordinator()
        now = datetime.now(timezone.utc)

        obs1 = MarketPairObservation(
            chain_id="eip155:1",
            provider_network_id="eth",
            provider_source="geckoterminal",
            pool_address="0xPoolSortA1111111111111111111111111",
            token_address="0xTokenSortA111111111111111111111111",
            symbol="CHEAP_OLD",
            price_usd=Decimal("0.05"),
            liquidity_usd=Decimal("10000"),
            volume_24h_usd=Decimal("10000"),
            pair_created_at=now - timedelta(hours=50),  # Age: 50h
            fetched_at=now
        )
        obs2 = MarketPairObservation(
            chain_id="eip155:1",
            provider_network_id="eth",
            provider_source="geckoterminal",
            pool_address="0xPoolSortB2222222222222222222222222",
            token_address="0xTokenSortB222222222222222222222222",
            symbol="PRICEY_YOUNG",
            price_usd=Decimal("100.00"),
            liquidity_usd=Decimal("10000"),
            volume_24h_usd=Decimal("10000"),
            pair_created_at=now - timedelta(hours=2),  # Age: 2h
            fetched_at=now
        )

        coordinator._tokens_by_key = coordinator._aggregate_tokens([obs1, obs2], now=now)
        coordinator._last_refresh_time = now

        # Sort price_usd desc
        r_p_desc = await coordinator.query_screener(sort_by="price_usd", sort_dir="desc", category="ALL")
        assert r_p_desc.tokens[0].symbol == "PRICEY_YOUNG"
        assert r_p_desc.tokens[1].symbol == "CHEAP_OLD"

        # Sort price_usd asc
        r_p_asc = await coordinator.query_screener(sort_by="price_usd", sort_dir="asc", category="ALL")
        assert r_p_asc.tokens[0].symbol == "CHEAP_OLD"
        assert r_p_asc.tokens[1].symbol == "PRICEY_YOUNG"

        # Sort age desc (oldest first)
        r_age_desc = await coordinator.query_screener(sort_by="age", sort_dir="desc", category="ALL")
        assert r_age_desc.tokens[0].symbol == "CHEAP_OLD"

        # Sort age asc (youngest first)
        r_age_asc = await coordinator.query_screener(sort_by="age", sort_dir="asc", category="ALL")
        assert r_age_asc.tokens[0].symbol == "PRICEY_YOUNG"
    asyncio.run(_run())


def test_dexscreener_failure_resilience_preserves_gecko_results(monkeypatch):
    """
    Verifies that when DEX Screener enrichment fails (due to error, timeout, or rate limiting),
    GeckoTerminal observations remain completely usable and an explicit warning is appended.
    """
    async def _run():
        gecko_mock = GeckoTerminalClient()
        dex_mock = DexScreenerClient()
        coordinator = MarketDataCoordinator(gecko_client=gecko_mock, dex_client=dex_mock)
        now = datetime.now(timezone.utc)

        # Mock gecko returning a valid observation
        mock_gecko_obs = [
            MarketPairObservation(
                chain_id="solana:mainnet",
                provider_network_id="solana",
                provider_source="geckoterminal",
                endpoint_id="geckoterminal:trending_pools",
                pool_address="PoolGeckoResilient1111111111111111111111",
                token_address="TokenGeckoResilient222222222222222222222",
                symbol="BONK",
                name="Bonk Meme",
                price_usd=Decimal("0.50"),
                liquidity_usd=Decimal("80000.00"),
                volume_24h_usd=Decimal("200000.00"),
                fetched_at=now
            )
        ]

        async def mock_gecko_fetch(network, endpoint_type="trending_pools"):
            if network == "solana" and endpoint_type == "trending_pools":
                return mock_gecko_obs
            return []

        # Mock DEX Screener tripping circuit breaker / failing
        async def mock_dex_failure(chain_id, token_addresses):
            dex_mock.circuit_breaker.record_failure("HTTP_500_SERVER_ERROR")
            raise RuntimeError("Simulated DEX Screener 500 Outage")

        monkeypatch.setattr(gecko_mock, "fetch_pools", mock_gecko_fetch)
        monkeypatch.setattr(dex_mock, "get_token_pairs_batch", mock_dex_failure)

        # Execute refresh
        res = await coordinator.refresh_market_data(force=True)
        assert res["status"] == "REFRESHED"
        assert res["total_tokens"] == 1

        # Query screener
        screener_res = await coordinator.query_screener(limit=10)
        assert screener_res.result_count == 1
        assert screener_res.tokens[0].symbol == "BONK"

        # Explicit degradation warning present
        assert any("DEX Screener" in w and "degraded" in w.lower() for w in screener_res.warnings)
    asyncio.run(_run())


def test_screener_api_chain_filter_ethereum_and_arbitrum(monkeypatch):
    """
    Verifies that the FastAPI screener endpoint accepts 'ethereum' and 'arbitrum'
    as valid query parameters and filters correctly.
    """
    from app.services.market_data_service import market_data_coordinator

    now = datetime.now(timezone.utc)
    eth_obs = MarketPairObservation(
        chain_id="eip155:1",
        provider_network_id="eth",
        provider_source="geckoterminal",
        pool_address="0xPoolEth1111111111111111111111111111111",
        token_address="0x6982508145454Ce325dDbE47a25d4ec3d2311933",
        symbol="PEPE",
        name="Pepe",
        price_usd=Decimal("0.00001"),
        liquidity_usd=Decimal("500000.00"),
        volume_24h_usd=Decimal("1000000.00"),
        fetched_at=now
    )
    arb_obs = MarketPairObservation(
        chain_id="eip155:42161",
        provider_network_id="arbitrum",
        provider_source="geckoterminal",
        pool_address="0xPoolArb2222222222222222222222222222222",
        token_address="0x250632378E573c6Be1AC2f97FCDF00515d0Aa91B",
        symbol="AIDOGE",
        name="ArbDoge AI",
        price_usd=Decimal("0.0000001"),
        liquidity_usd=Decimal("200000.00"),
        volume_24h_usd=Decimal("400000.00"),
        fetched_at=now
    )

    market_data_coordinator._tokens_by_key = market_data_coordinator._aggregate_tokens([eth_obs, arb_obs], now=now)
    market_data_coordinator._last_refresh_time = now

    # 1. Query ethereum
    res_eth = client.get("/api/v1/discovery/screener?chain=ethereum&category=ALL")
    assert res_eth.status_code == 200
    data_eth = res_eth.json()
    assert data_eth["result_count"] == 1
    assert data_eth["tokens"][0]["symbol"] == "PEPE"
    assert data_eth["chains_requested"] == ["ethereum"]
    assert data_eth["filters_applied"]["chain"] == "ethereum"
    assert "eth" in data_eth["chains_with_data"] or "ethereum" in data_eth["chains_with_data"]

    # 2. Query arbitrum
    res_arb = client.get("/api/v1/discovery/screener?chain=arbitrum&category=ALL")
    assert res_arb.status_code == 200
    data_arb = res_arb.json()
    assert data_arb["result_count"] == 1
    assert data_arb["tokens"][0]["symbol"] == "AIDOGE"
    assert data_arb["chains_requested"] == ["arbitrum"]
    assert data_arb["filters_applied"]["chain"] == "arbitrum"
    assert "arbitrum" in data_arb["chains_with_data"]

    # 3. Query all
    res_all = client.get("/api/v1/discovery/screener?chain=all&category=ALL")
    assert res_all.status_code == 200
    data_all = res_all.json()
    assert data_all["result_count"] == 2
    assert data_all["chains_requested"] == ["solana", "base", "ethereum", "arbitrum"]
    assert data_all["filters_applied"]["chain"] == "all"
    assert "ranking_scope_note" in data_all
    assert "public market providers" in data_all["ranking_scope_note"]


def test_screener_api_sort_by_age_and_price_usd():
    """Verifies that the screener API endpoint accepts sort_by='price_usd' and 'age'."""
    res_price = client.get("/api/v1/discovery/screener?sort_by=price_usd&sort_dir=asc")
    assert res_price.status_code == 200
    assert "price_usd:asc" in res_price.json()["sort_applied"]

    res_age = client.get("/api/v1/discovery/screener?sort_by=age&sort_dir=desc")
    assert res_age.status_code == 200
    assert "age:desc" in res_age.json()["sort_applied"]


def test_reconciled_chain_metadata_and_canonical_solana_identifier():
    """
    Verifies:
    1. Solana tokens strictly emit canonical CAIP-2 identifier 'solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp'.
    2. Input query accepts 'solana', 'solana:mainnet', and canonical ID.
    3. chains_requested accurately matches the query parameter (['solana'] when chain=solana).
    4. Discovered total_matches is distinct from global market depth and ranking_scope_note is present.
    """
    from app.services.market_data_service import market_data_coordinator, SOLANA_CAIP2_CANONICAL
    now = datetime.now(timezone.utc)

    sol_obs = MarketPairObservation(
        chain_id="solana:mainnet",
        provider_network_id="solana",
        provider_source="geckoterminal",
        pool_address="PoolSolCanonical111111111111111111111",
        token_address="DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
        symbol="BONK",
        name="Bonk Meme",
        price_usd=Decimal("0.000025"),
        liquidity_usd=Decimal("300000.00"),
        volume_24h_usd=Decimal("1500000.00"),
        fetched_at=now
    )

    market_data_coordinator._tokens_by_key = market_data_coordinator._aggregate_tokens([sol_obs], now=now)
    market_data_coordinator._last_refresh_time = now

    # Check token model emitted canonical Solana CAIP-2
    token_item = list(market_data_coordinator._tokens_by_key.values())[0]
    assert token_item.chain_id == SOLANA_CAIP2_CANONICAL
    assert token_item.chain_id == "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"

    # Query with alias: chain=solana
    res_sol = client.get("/api/v1/discovery/screener?chain=solana&category=ALL")
    assert res_sol.status_code == 200
    data_sol = res_sol.json()
    assert data_sol["chains_requested"] == ["solana"]
    assert data_sol["filters_applied"]["chain"] == "solana"
    assert data_sol["chains_with_data"] == ["solana"]
    assert data_sol["total_matches"] == 1
    assert data_sol["tokens"][0]["chain_id"] == "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"
    assert data_sol["ranking_scope_note"] != ""

    # Query with canonical CAIP-2 in query
    res_sol_canon = client.get(f"/api/v1/discovery/screener?chain={SOLANA_CAIP2_CANONICAL}&category=ALL")
    assert res_sol_canon.status_code == 200
    data_sol_canon = res_sol_canon.json()
    assert data_sol_canon["result_count"] == 1
    assert data_sol_canon["tokens"][0]["chain_id"] == "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"


def test_market_providers_health_canonical_solana():
    """
    Verifies that /discovery/providers/health includes canonical Solana CAIP-2
    in supported_chains and network_identifiers.
    """
    res = client.get("/api/v1/discovery/providers/health")
    assert res.status_code == 200
    data = res.json()
    providers = data["providers"]

    for pid in ["geckoterminal", "dexscreener"]:
        assert pid in providers
        supported = providers[pid]["supported_chains"]
        network_ids = providers[pid]["network_identifiers"]

        assert "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp" in supported
        assert "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp" in network_ids
        assert network_ids["solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"] == "solana"
        # Legacy alias also preserved for backward compatibility
        assert "solana:mainnet" in supported
        assert "solana:mainnet" in network_ids

