import sys
import os
import pytest
from decimal import Decimal

sys.path.insert(0, os.path.abspath("./backend"))
from app.services.solana_instruction_decoder import solana_instruction_decoder
from app.services.solana_transaction_parser import solana_transaction_parser
from app.services.deployer_analyzer import deployer_analyzer
from app.services.liquidity_event_analyzer import liquidity_event_analyzer
from app.services.forensic_evidence import forensic_evidence_report
from app.services.first_buyers_decoder import first_buyers_decoder
from app.services.wallet_cluster_engine import wallet_cluster_engine
from app.schemas.contracts import TriState, EvidenceStatus

def test_missing_transaction_history():
    # Scenario: RPC returns empty signature history for a pool
    result = first_buyers_decoder.analyze_buyers(
        pool_address="EmptyPool11111111111111111111111111111111111",
        deployer_address="Deployer111111111111111111111111111111111111",
        creation_slot=5000,
        raw_transactions=[]
    )
    assert result["confidence"] == EvidenceStatus.INSUFFICIENT
    assert result["total_held_pct"] == Decimal("0.00")
    assert len(result["buyers"]) == 0

def test_failed_transactions_filtered():
    # Scenario: Transaction has an error (e.g. InstructionError / custom program error)
    mock_tx = {
        "slot": 100,
        "blockTime": 1700000000,
        "transaction": {
            "signatures": ["FailedSignature1111111111111111111111111111111111111111111111111111111111111111"],
            "message": {"accountKeys": ["Signer11111111111111111111111111111111111111111"]}
        },
        "meta": {
            "fee": 5000,
            "err": {"InstructionError": [0, "Custom"]},
            "innerInstructions": []
        }
    }
    parsed = solana_transaction_parser.parse_transaction(mock_tx)
    assert parsed is not None
    assert parsed["is_success"] is False
    assert parsed["error"] is not None

def test_token_2022_instruction_decoding():
    # Scenario: Instruction targeting Token-2022 program
    ix = {
        "programId": "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",
        "parsed": {
            "type": "transferChecked",
            "info": {
                "source": "SourceATA111111111111111111111111111111111111",
                "destination": "DestATA111111111111111111111111111111111111",
                "amount": "1000000"
            }
        }
    }
    decoded = solana_instruction_decoder.decode_instruction(ix)
    assert decoded["program"] == "TOKEN_2022"
    assert decoded["is_token_program"] is True
    assert decoded["type"] == "transferChecked"

def test_deployer_analyzer_massive_retention():
    # Scenario: Deployer holds 45% of total supply
    res = deployer_analyzer.analyze_deployer(
        deployer_address="GreedyDev11111111111111111111111111111111111",
        initial_supply_raw=Decimal("1000000000"),
        current_balance_raw=Decimal("450000000"),
        funder_address="Funder111111111111111111111111111111111111",
        previous_launches_count=1
    )
    assert res["risk_level"] == "HIGH"
    assert res["category"] == "MASSIVE_SUPPLY_RETENTION"
    assert res["current_holding_pct"] == Decimal("45.00")

def test_liquidity_event_analyzer_burned_and_locked():
    # Scenario 1: 100% burned
    burn_res = liquidity_event_analyzer.analyze_lp_status(
        total_lp_minted_raw=Decimal("1000000"),
        burned_lp_amount_raw=Decimal("1000000"),
        locked_lp_amount_raw=Decimal("0")
    )
    assert burn_res["lp_status"] == "BURNED"
    assert burn_res["lp_burned"] == TriState.TRUE
    assert burn_res["is_removable"] is False

    # Scenario 2: Unlocked / Rug pull danger
    unlocked_res = liquidity_event_analyzer.analyze_lp_status(
        total_lp_minted_raw=Decimal("1000000"),
        burned_lp_amount_raw=Decimal("0"),
        locked_lp_amount_raw=Decimal("0")
    )
    assert unlocked_res["lp_status"] == "UNLOCKED"
    assert unlocked_res["lp_burned"] == TriState.FALSE
    assert unlocked_res["is_removable"] is True

def test_false_positive_cluster_distinction():
    # Scenario: Buyers funded by a major CEX hot wallet vs Deployer funding tree
    deployer = "DeployerWallet1111111111111111111111111111111"
    cex_hot_wallet = "BinanceHotWallet11111111111111111111111111"

    # CEX funded wallets
    cex_funder_map = {"Buyer_A": cex_hot_wallet, "Buyer_B": cex_hot_wallet}
    clusters_cex = wallet_cluster_engine.evaluate_cluster(
        funder_map=cex_funder_map,
        slot_map={},
        deployer_address=deployer
    )
    assert len(clusters_cex) == 1
    assert clusters_cex[0]["cluster_type"] == "SHARED_ROOT_FUNDER"
    assert clusters_cex[0]["evidence"]["is_deployer_root"] is False

    # Deployer funded wallets (Direct insider funding ring)
    dev_funder_map = {"Buyer_C": deployer, "Buyer_D": deployer}
    clusters_dev = wallet_cluster_engine.evaluate_cluster(
        funder_map=dev_funder_map,
        slot_map={},
        deployer_address=deployer
    )
    assert len(clusters_dev) == 1
    assert clusters_dev[0]["cluster_type"] == "DEPLOYER_FUNDING_TREE"
    assert clusters_dev[0]["evidence"]["is_deployer_root"] is True
    assert clusters_dev[0]["confidence"] > clusters_cex[0]["confidence"]

def test_forensic_evidence_report_builder():
    report = forensic_evidence_report.build_report(
        asset_id="asset-1",
        mint_address="Mint111111111111111111111111111111111111111",
        pool_address="Pool111111111111111111111111111111111111111",
        slot_range={"start_slot": 1000, "end_slot": 1050},
        first_buyers={"total_held_pct": Decimal("5.50"), "bundler_cluster_detected": False, "confidence": EvidenceStatus.VERIFIED},
        deployer_analysis={"risk_level": "LOW", "current_holding_pct": Decimal("0.00")},
        clusters=[],
        liquidity_analysis={"lp_status": "BURNED", "burn_pct": Decimal("100.00")}
    )
    assert report["evidence_status"] == "VERIFIED"
    assert report["first_buyers_held_pct"] == Decimal("5.50")
    assert report["reproducibility"]["parser_version"] == "v2_solana_parser"
