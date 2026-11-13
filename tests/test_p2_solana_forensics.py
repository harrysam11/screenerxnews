import sys
import os
import pytest
from decimal import Decimal

sys.path.insert(0, os.path.abspath("./backend"))
from app.services.first_buyers_decoder import first_buyers_decoder
from app.services.wallet_cluster_engine import wallet_cluster_engine, WalletClusterEngine
from app.schemas.contracts import EvidenceStatus

def test_first_buyers_decoder_empty():
    result = first_buyers_decoder.analyze_buyers(
        pool_address="Pool111111111111111111111111111111111111111",
        deployer_address="Deployer111111111111111111111111111111111111",
        creation_slot=1000,
        raw_transactions=[]
    )
    assert result["total_held_pct"] == Decimal("0.00")
    assert result["bundler_cluster_detected"] is False
    assert result["confidence"] == EvidenceStatus.INSUFFICIENT
    assert len(result["buyers"]) == 0

def test_first_buyers_decoder_shared_funder_bundler():
    creation_slot = 1000
    shared_funder = "SharedRootFunder11111111111111111111111111111"
    raw_txs = [
        {
            "tx_signature": f"sig_{i}",
            "slot": creation_slot + i,
            "wallet_address": f"Buyer_{i}",
            "sol_spent_lamports": 500000000,
            "tokens_bought_raw": Decimal("1000000"),
            "funder_address": shared_funder,
            "holding_pct": Decimal("1.50")
        }
        for i in range(1, 6) # 5 buyers sharing same funder
    ]

    result = first_buyers_decoder.analyze_buyers(
        pool_address="Pool111111111111111111111111111111111111111",
        deployer_address="Deployer111111111111111111111111111111111111",
        creation_slot=creation_slot,
        raw_transactions=raw_txs
    )
    assert result["bundler_cluster_detected"] is True
    assert result["shared_funder_count"] == 5
    assert result["total_held_pct"] == Decimal("7.50")
    assert result["confidence"] == EvidenceStatus.PROBABILISTIC

def test_first_buyers_decoder_same_slot_clustering():
    creation_slot = 2000
    # 4 distinct buyers in the same creation slot with independent funders
    raw_txs = [
        {
            "tx_signature": f"sig_{i}",
            "slot": creation_slot, # exact same slot
            "wallet_address": f"Sniper_{i}",
            "sol_spent_lamports": 1000000000,
            "tokens_bought_raw": Decimal("5000000"),
            "funder_address": f"IndependentFunder_{i}",
            "holding_pct": Decimal("2.00")
        }
        for i in range(1, 5)
    ]

    result = first_buyers_decoder.analyze_buyers(
        pool_address="Pool111111111111111111111111111111111111111",
        deployer_address="Deployer111111111111111111111111111111111111",
        creation_slot=creation_slot,
        raw_transactions=raw_txs
    )
    assert result["bundler_cluster_detected"] is True
    assert result["same_slot_count"] == 4

def test_wallet_cluster_engine_evaluation():
    deployer = "DeployerWallet1111111111111111111111111111111"
    funder_map = {
        "Wallet_A": deployer,
        "Wallet_B": deployer,
        "Wallet_C": "IndependentFunder1111111111111111111111111"
    }
    slot_map = {
        "Wallet_A": 500,
        "Wallet_B": 500,
        "Wallet_C": 500
    }

    clusters = wallet_cluster_engine.evaluate_cluster(
        funder_map=funder_map,
        slot_map=slot_map,
        deployer_address=deployer
    )
    assert len(clusters) == 2
    types = [c["cluster_type"] for c in clusters]
    assert "DEPLOYER_FUNDING_TREE" in types
    assert "SAME_SLOT_BUNDLER" in types

def test_wallet_classification():
    # Deployer classification
    assert wallet_cluster_engine.classify_wallet(
        wallet_address="0x1",
        sol_balance_lamports=1000000000,
        realized_pnl_usd=Decimal("0"),
        win_rate=Decimal("0"),
        is_deployer=True,
        is_in_bundler_cluster=False
    ) == "DEPLOYER"

    # Bundler candidate classification
    assert wallet_cluster_engine.classify_wallet(
        wallet_address="0x2",
        sol_balance_lamports=1000000000,
        realized_pnl_usd=Decimal("0"),
        win_rate=Decimal("0"),
        is_deployer=False,
        is_in_bundler_cluster=True
    ) == "BUNDLER_CANDIDATE"

    # Whale classification
    assert wallet_cluster_engine.classify_wallet(
        wallet_address="0x3",
        sol_balance_lamports=600_000_000_000, # 600 SOL
        realized_pnl_usd=Decimal("100"),
        win_rate=Decimal("50"),
        is_deployer=False,
        is_in_bundler_cluster=False
    ) == "WHALE"

    # Smart Money classification
    assert wallet_cluster_engine.classify_wallet(
        wallet_address="0x4",
        sol_balance_lamports=10_000_000_000,
        realized_pnl_usd=Decimal("25000"),
        win_rate=Decimal("72.5"),
        is_deployer=False,
        is_in_bundler_cluster=False
    ) == "SMART_MONEY"
