import sys
import os
import pytest
import asyncio
from decimal import Decimal

sys.path.insert(0, os.path.abspath("./backend"))
from app.services.quota_manager import quota_manager, TokenBucket
from app.services.security_normalizer import security_normalizer
from app.services.market_enricher import market_enricher
from app.schemas.contracts import TriState, EvidenceStatus

def test_quota_manager_permit_and_cooldown():
    async def _run():
        # Test token bucket acquisition
        bucket = TokenBucket(capacity=2, fill_rate_per_sec=1.0)
        assert await bucket.acquire(1) is True
        assert await bucket.acquire(1) is True
        assert await bucket.acquire(1) is False # Bucket exhausted

        # Test cooldown handling
        provider = "test_provider"
        assert quota_manager.is_cooling_down(provider) is False
        quota_manager.trigger_cooldown(provider, duration_sec=1.0)
        assert quota_manager.is_cooling_down(provider) is True
        assert await quota_manager.acquire_permit(provider) is False

    asyncio.run(_run())

def test_security_normalizer_insufficient_evidence():
    mint = "TestMint11111111111111111111111111111111111"
    # When all sources fail / are missing
    report = security_normalizer.normalize(
        mint_address=mint,
        rpc_mint_info=None,
        goplus_report=None,
        rugcheck_report=None
    )
    assert report.confidence == EvidenceStatus.INSUFFICIENT
    assert report.risk_score is None
    assert report.risk_level == "UNKNOWN"
    assert report.is_mint_revoked == TriState.UNKNOWN
    assert report.is_freeze_revoked == TriState.UNKNOWN

def test_security_normalizer_critical_active_authorities():
    mint = "TestMint11111111111111111111111111111111111"
    mock_rpc_info = {
        "mint_address": mint,
        "owner_program": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
        "is_token_2022": False,
        "decimals": 9,
        "mint_authority": "DangerousDevWallet111111111111111111111111",
        "freeze_authority": "DangerousDevWallet111111111111111111111111",
        "supply": 1000000000000000,
        "extensions": []
    }
    report = security_normalizer.normalize(
        mint_address=mint,
        rpc_mint_info=mock_rpc_info,
        goplus_report=None,
        rugcheck_report=None
    )
    assert report.confidence == EvidenceStatus.VERIFIED
    assert report.is_mint_revoked == TriState.FALSE
    assert report.is_freeze_revoked == TriState.FALSE
    assert report.risk_level == "CRITICAL"
    assert report.risk_score >= Decimal("85.00")
    codes = [f.code for f in report.findings]
    assert "ACTIVE_MINT_AUTHORITY" in codes
    assert "ACTIVE_FREEZE_AUTHORITY" in codes

def test_security_normalizer_token_2022_permanent_delegate():
    mint = "Token2022Mint111111111111111111111111111111"
    mock_rpc_info = {
        "mint_address": mint,
        "owner_program": "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",
        "is_token_2022": True,
        "decimals": 6,
        "mint_authority": None, # Revoked
        "freeze_authority": None, # Revoked
        "supply": 1000000000,
        "extensions": [
            {"extension": "permanentDelegate", "state": {"delegate": "MaliciousScammer111111111111111111111111"}}
        ]
    }
    report = security_normalizer.normalize(
        mint_address=mint,
        rpc_mint_info=mock_rpc_info,
        goplus_report=None,
        rugcheck_report=None
    )
    assert report.is_token_2022 == TriState.TRUE
    assert report.risk_level == "CRITICAL"
    codes = [f.code for f in report.findings]
    assert "TOKEN_2022_PERMANENTDELEGATE" in codes

def test_market_enricher_provenance():
    pair_mock = {
        "pairAddress": "PairAddress1111111111111111111111111111111",
        "priceUsd": "0.00045",
        "liquidity": {"usd": 55000.0},
        "volume": {"m5": 1200.0, "h1": 15000.0, "h24": 120000.0},
        "txns": {"h1": {"buys": 45, "sells": 20}}
    }
    snapshot = market_enricher.create_snapshot(
        asset_id="asset-1",
        pool_id="pool-1",
        pair_data=pair_mock,
        request_id="test-req-001"
    )
    assert snapshot["request_id"] == "test-req-001"
    assert snapshot["source"] == "dexscreener"
    assert snapshot["data_quality"] == "COMPLETE"
    assert snapshot["is_stale"] is False
    assert snapshot["buys_1h"] == 45
    assert snapshot["sells_1h"] == 20
    assert snapshot["observed_at"] is not None
