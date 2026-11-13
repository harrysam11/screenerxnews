import sys
import os
import pytest
from decimal import Decimal

sys.path.insert(0, os.path.abspath("./backend"))
from app.schemas.contracts import TriState, EvidenceStatus
from app.services.evm_proxy_analyzer import evm_proxy_analyzer, EvmProxyAnalyzer
from app.services.evm_permission_analyzer import evm_permission_analyzer, EvmPermissionAnalyzer
from app.services.evm_contract_analyzer import evm_contract_analyzer, EvmContractAnalyzer
from app.services.evm_sell_simulator import evm_sell_simulator, EvmSellSimulator
from app.services.evm_holder_analyzer import evm_holder_analyzer, EvmHolderAnalyzer
from app.services.evm_deployer_graph import evm_deployer_graph, EvmDeployerGraph
from app.services.evm_liquidity_forensics import evm_liquidity_forensics, EvmLiquidityForensics
from app.services.evm_wallet_forensics import evm_wallet_forensics, EvmWalletForensics


# =====================================================================
# 1. EVM Proxy Analyzer Tests
# =====================================================================
def test_proxy_analyzer_eip1967_detection():
    # Slot 0x3608... pointing to implementation
    slots = {
        EvmProxyAnalyzer.EIP1967_IMPLEMENTATION_SLOT: "0x0000000000000000000000001234567890123456789012345678901234567890",
        EvmProxyAnalyzer.EIP1967_ADMIN_SLOT: "0x000000000000000000000000abcdefabcdefabcdefabcdefabcdefabcdefabcd"
    }
    result = evm_proxy_analyzer.evaluate_proxy_storage(slots)
    assert result["is_proxy"] is True
    assert result["proxy_standard"] == "EIP-1967"
    assert result["implementation_address"] == "0x1234567890123456789012345678901234567890"
    assert result["admin_address"] == "0xabcdefabcdefabcdefabcdefabcdefabcdefabcd"


def test_proxy_analyzer_uups_detection():
    slots = {
        EvmProxyAnalyzer.EIP1822_UUPS_SLOT: "0x0000000000000000000000009999999999999999999999999999999999999999"
    }
    result = evm_proxy_analyzer.evaluate_proxy_storage(slots)
    assert result["is_proxy"] is True
    assert result["proxy_standard"] == "EIP-1822_UUPS"
    assert result["implementation_address"] == "0x9999999999999999999999999999999999999999"


def test_proxy_analyzer_non_proxy():
    slots = {
        EvmProxyAnalyzer.EIP1967_IMPLEMENTATION_SLOT: EvmProxyAnalyzer.ZERO_SLOT_VALUE,
        EvmProxyAnalyzer.EIP1822_UUPS_SLOT: EvmProxyAnalyzer.ZERO_SLOT_VALUE
    }
    result = evm_proxy_analyzer.evaluate_proxy_storage(slots)
    assert result["is_proxy"] is False
    assert result["proxy_standard"] is None
    assert result["implementation_address"] is None


# =====================================================================
# 2. EVM Permission & Bytecode Analyzer Tests
# =====================================================================
def test_permission_analyzer_selectors():
    # Construct synthetic bytecode containing owner, renounce, pause, mint selectors
    bytecode = (
        "0x608060405234801561001057600080fd5b50"
        "8da5cb5b" # owner()
        "715018a6" # renounceOwnership()
        "8456cb59" # pause()
        "40c10f19" # mint(address,uint256)
    )
    result = evm_permission_analyzer.analyze_bytecode(bytecode)
    assert result["has_owner"] is True
    assert result["can_renounce_ownership"] is True
    assert result["can_pause_transfers"] is True
    assert result["can_mint_additional_supply"] is True
    assert "owner()" in result["selectors_found"]
    assert "mint(address,uint256)" in result["selectors_found"]


def test_permission_analyzer_ownership_classification():
    # Renounced cases
    assert evm_permission_analyzer.classify_ownership(None) == "RENOUNCED"
    assert evm_permission_analyzer.classify_ownership("0x0000000000000000000000000000000000000000") == "RENOUNCED"
    assert evm_permission_analyzer.classify_ownership("0x000000000000000000000000000000000000dEaD") == "RENOUNCED"

    # Active owner
    assert evm_permission_analyzer.classify_ownership("0x1111111111111111111111111111111111111111") == "ACTIVE_OWNER"


# =====================================================================
# 3. Consolidated Contract Security Analyzer Tests
# =====================================================================
def test_contract_analyzer_high_risk_and_safe():
    # Safe scenario
    safe_eval = evm_contract_analyzer.evaluate_contract_security(
        chain_id="eip155:1",
        contract_address="0xabc1230000000000000000000000000000000001",
        bytecode_hex="0x6080604052",
        storage_slots={},
        owner_address="0x0000000000000000000000000000000000000000",
        buy_tax_bps=100,
        sell_tax_bps=200
    )
    assert safe_eval["is_high_risk"] is False
    assert safe_eval["ownership_state"] == "RENOUNCED"
    assert len(safe_eval["findings"]) == 0

    # Malicious scenario: Mintable + high sell tax (> 10%) + unrenounced
    malicious_eval = evm_contract_analyzer.evaluate_contract_security(
        chain_id="eip155:8453",
        contract_address="0xabc123000000000000000000000000000000002",
        bytecode_hex="0x40c10f19", # mint
        storage_slots={},
        owner_address="0x9999999999999999999999999999999999999999",
        buy_tax_bps=200,
        sell_tax_bps=1500 # 15%
    )
    assert malicious_eval["is_high_risk"] is True
    assert malicious_eval["ownership_state"] == "ACTIVE_OWNER"
    codes = [f["code"] for f in malicious_eval["findings"]]
    assert "MINTABLE_SUPPLY" in codes
    assert "EXCESSIVE_SELL_TAX" in codes
    assert "OWNERSHIP_NOT_RENOUNCED" in codes


# =====================================================================
# 4. Sell Simulator & Honeypot Classifier Tests
# =====================================================================
def test_sell_simulator_honeypot_revert():
    # Buy succeeds, sell reverts -> Honeypot
    res = evm_sell_simulator.evaluate_simulation_results(
        chain_id="eip155:1",
        token_address="0xTokenAddress1",
        buy_success=True,
        buy_input_native=Decimal("0.1"),
        buy_output_tokens=Decimal("1000"),
        expected_buy_output=Decimal("1000"),
        sell_success=False,
        sell_input_tokens=Decimal("1000"),
        sell_output_native=Decimal("0"),
        expected_sell_output=Decimal("0.095"),
        revert_reason="TRANSFER_FAILED: caller is blacklisted"
    )
    assert res["is_honeypot"] is True
    assert "CANNOT_SELL" in res["rejection_reason"]
    assert res["revert_reason"] == "TRANSFER_FAILED: caller is blacklisted"


def test_sell_simulator_excessive_sell_tax():
    # Buy succeeds, sell succeeds but 90% tax extracted -> Honeypot
    res = evm_sell_simulator.evaluate_simulation_results(
        chain_id="eip155:8453",
        token_address="0xTokenAddress2",
        buy_success=True,
        buy_input_native=Decimal("0.1"),
        buy_output_tokens=Decimal("1000"),
        expected_buy_output=Decimal("1000"),
        sell_success=True,
        sell_input_tokens=Decimal("1000"),
        sell_output_native=Decimal("0.01"), # 90% tax loss compared to expected 0.1
        expected_sell_output=Decimal("0.10")
    )
    assert res["is_honeypot"] is True
    assert res["sell_tax_pct"] == Decimal("90.00")
    assert "HONEYPOT_TAX" in res["rejection_reason"]


def test_sell_simulator_clean_trade():
    # Normal 2% tax
    res = evm_sell_simulator.evaluate_simulation_results(
        chain_id="eip155:56",
        token_address="0xTokenAddress3",
        buy_success=True,
        buy_input_native=Decimal("1.0"),
        buy_output_tokens=Decimal("980"),
        expected_buy_output=Decimal("1000"),
        sell_success=True,
        sell_input_tokens=Decimal("980"),
        sell_output_native=Decimal("0.96"),
        expected_sell_output=Decimal("0.98")
    )
    assert res["is_honeypot"] is False
    assert res["buy_tax_pct"] == Decimal("2.00")
    assert res["sell_tax_pct"] == Decimal("2.04")


# =====================================================================
# 5. Holder Analyzer Tests
# =====================================================================
def test_holder_analyzer_concentration_and_filtering():
    total_supply = 1_000_000_000
    pool_address = "0xPoolAddress1"
    balances = {
        "0x000000000000000000000000000000000000dead": 200_000_000, # 20% burned
        pool_address: 300_000_000,                                 # 30% in LP pool
        "0xHolder1": 100_000_000,                                   # 10%
        "0xHolder2": 50_000_000,                                    # 5%
        "0xHolder3": 50_000_000                                     # 5%
    }
    analysis = evm_holder_analyzer.analyze_holders(
        balances=balances,
        total_supply=total_supply,
        pool_address=pool_address
    )
    assert analysis["burned_supply_pct"] == Decimal("20.00")
    assert analysis["pool_supply_pct"] == Decimal("30.00")
    # Dead and pool addresses should not be counted in top holders
    assert len(analysis["top_holders"]) == 3
    # Top 10 sum = 100m + 50m + 50m = 200m -> 20%
    assert analysis["top10_concentration_pct"] == Decimal("20.00")


# =====================================================================
# 6. Deployer Graph & Mixer Lineage Tests
# =====================================================================
def test_deployer_graph_tornado_cash_detection():
    # Tornado Cash 1 ETH mixer
    mixer_eval = evm_deployer_graph.evaluate_deployer(
        chain_id="eip155:1",
        deployer_address="0xDev1111111111111111111111111111111111111",
        initial_funder_address="0x47ce0c6ed5b0ce3d3a51fdb1c52dc66a7c3c2936",
        prior_deployments=[]
    )
    assert mixer_eval["is_mixer_funded"] is True
    assert mixer_eval["risk_level"] == "CRITICAL"
    assert mixer_eval["funder_label"] == "TORNADO_CASH_MIXER"


def test_deployer_graph_serial_launcher():
    serial_eval = evm_deployer_graph.evaluate_deployer(
        chain_id="eip155:8453",
        deployer_address="0xDev2222222222222222222222222222222222222",
        initial_funder_address="0xNormalFunderAddress",
        prior_deployments=["0xTokenA", "0xTokenB", "0xTokenC"]
    )
    assert serial_eval["is_mixer_funded"] is False
    assert serial_eval["risk_level"] == "HIGH"
    assert serial_eval["funder_label"] == "SERIAL_LAUNCHER"


# =====================================================================
# 7. EVM Liquidity Forensics Tests
# =====================================================================
def test_liquidity_forensics_burned_and_locked():
    total_lp = 1_000_000
    # 98% burned to 0xdead
    burned_lp = {
        "0x000000000000000000000000000000000000dead": 980_000,
        "0xdev": 20_000
    }
    burned_res = evm_liquidity_forensics.evaluate_lp_safety(total_lp, burned_lp)
    assert burned_res["lp_status"] == "BURNED"
    assert burned_res["lp_burned"] == TriState.TRUE
    assert burned_res["is_removable"] is False

    # 95% locked in Uncx locker
    locked_lp = {
        "0x663a5c229c09b049e36dcc11a9b0d4a8eb9db214": 950_000, # Uncx
        "0xdev": 50_000
    }
    locked_res = evm_liquidity_forensics.evaluate_lp_safety(total_lp, locked_lp)
    assert locked_res["lp_status"] == "LOCKED"
    assert locked_res["lp_burned"] == TriState.FALSE
    assert locked_res["is_removable"] is False

    # Unlocked
    unlocked_lp = {
        "0xdev": 1_000_000
    }
    unlocked_res = evm_liquidity_forensics.evaluate_lp_safety(total_lp, unlocked_lp)
    assert unlocked_res["lp_status"] == "UNLOCKED"
    assert unlocked_res["is_removable"] is True


# =====================================================================
# 8. Wallet Forensics & Cluster Prevention Tests
# =====================================================================
def test_wallet_forensics_bundler_detection():
    # 3 buyers in exact pool creation block -> Bundler cluster
    swaps = [
        {"wallet": f"0xW{i}", "block_number": 100, "funder": f"0xF{i}"} for i in range(3)
    ] + [
        {"wallet": f"0xW{i}", "block_number": 102, "funder": f"0xF{i}"} for i in range(3, 6)
    ]
    res = evm_wallet_forensics.analyze_early_buyers(
        chain_id="eip155:1",
        pool_creation_block=100,
        deployer_address="0xDeployer",
        buyer_swaps=swaps
    )
    assert res["bundler_cluster_detected"] is True
    assert res["confidence"] == EvidenceStatus.PROBABILISTIC
    assert res["same_block_count"] == 3


def test_wallet_forensics_clean_buyers():
    # Distributed buyers over time, no shared funders
    swaps = [
        {"wallet": f"0xW{i}", "block_number": 100 + i, "funder": f"0xUnique{i}"} for i in range(1, 6)
    ]
    res = evm_wallet_forensics.analyze_early_buyers(
        chain_id="eip155:8453",
        pool_creation_block=100,
        deployer_address="0xDeployer",
        buyer_swaps=swaps
    )
    assert res["bundler_cluster_detected"] is False
    assert res["confidence"] == EvidenceStatus.PROBABILISTIC
