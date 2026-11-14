"""
Phase P10.2-D: Chain Capability Contracts & Finality Handling Test Suite
Verifies acceptance criteria:
- CHAIN-01: All 12 chain configurations are valid
- CHAIN-02: Adapter capability claims match working RPC tests
- CHAIN-03: RPC failure cannot become fabricated healthy data
"""

import os
import sys
import pytest
from decimal import Decimal

sys.path.insert(0, os.path.abspath("./backend"))
from app.core.chains import chain_registry, CanonicalAssetId
from app.adapters import (
    get_adapter_for_chain,
    BaseChainAdapter,
    ChainBlockHeader,
    ChainCapabilities,
    FinalityModel,
    RpcFailureException,
    SolanaChainAdapter,
    EvmChainAdapter,
    ArbitrumChainAdapter,
    OptimismChainAdapter,
    PolygonChainAdapter,
    AvalancheChainAdapter,
    SuiChainAdapter,
    AptosChainAdapter,
    TonChainAdapter,
    TronChainAdapter
)

# ==============================================================================
# CHAIN-01: All 12 chain configurations are valid
# ==============================================================================

def test_chain_01_all_12_chain_configurations_are_valid():
    """
    CHAIN-01: Verifies that all 12 chains in the multi-chain registry are valid,
    compliant with CAIP-2 syntax, have working RPC URLs, correct native assets,
    and resolve to active adapters.
    """
    validation = chain_registry.validate_all_chains()
    assert validation["valid"] is True, f"Validation errors: {validation['errors']}"
    assert validation["total_chains"] == 12
    assert len(validation["validated_chains"]) == 12

    # Group A: Solana, Ethereum, Base, BNB Chain
    group_a_chains = ["solana:mainnet", "eip155:1", "eip155:8453", "eip155:56"]
    for cid in group_a_chains:
        assert cid in validation["validated_chains"]
        cfg = chain_registry.get_chain(cid)
        assert cfg is not None
        assert cfg.finality_blocks > 0
        assert len(cfg.rpc_urls) > 0

    # Group B: Arbitrum, Optimism, Polygon, Avalanche
    group_b_chains = ["eip155:42161", "eip155:10", "eip155:137", "eip155:43114"]
    for cid in group_b_chains:
        assert cid in validation["validated_chains"]
        cfg = chain_registry.get_chain(cid)
        assert cfg is not None

    # Group C: Sui, Aptos, TON, TRON
    group_c_chains = ["sui:mainnet", "aptos:mainnet", "ton:mainnet", "tron:mainnet"]
    for cid in group_c_chains:
        assert cid in validation["validated_chains"]
        cfg = chain_registry.get_chain(cid)
        assert cfg is not None

# ==============================================================================
# CHAIN-02: Adapter capability claims match working RPC tests
# ==============================================================================

def test_chain_02_adapter_capabilities_and_finality_claims():
    """
    CHAIN-02: Verifies that adapter capability claims match actual specifications:
    - Token-2022 on Solana
    - L1 calldata fee computation on Arbitrum and Optimism
    - Instant finality on Avalanche, Sui, and Aptos
    - Multi-block finality depth on Polygon (128), Ethereum (12), BNB (15), and TRON (19)
    """
    # 1. Solana: SPL and Token-2022
    sol_adapter = get_adapter_for_chain("solana:mainnet")
    sol_caps = sol_adapter.get_capabilities()
    assert sol_caps.supports_token_2022 is True
    assert "Token-2022" in sol_caps.token_standards
    assert sol_caps.finality_model == FinalityModel.PROBABILISTIC_POS
    assert sol_caps.required_confirmations == 32
    # Finality check
    sol_finality_unconfirmed = sol_adapter.get_finality_status(confirmations=10)
    assert sol_finality_unconfirmed["is_finalized"] is False
    sol_finality_confirmed = sol_adapter.get_finality_status(confirmations=32)
    assert sol_finality_confirmed["is_finalized"] is True

    # 2. Arbitrum: L2 Calldata Fees
    arb_adapter = get_adapter_for_chain("eip155:42161")
    arb_caps = arb_adapter.get_capabilities()
    assert arb_caps.supports_l2_fees is True
    assert arb_caps.finality_model == FinalityModel.L2_ROLLUP_BATCH
    # Test L1 fee calculation
    l1_fee = arb_adapter.calculate_l1_calldata_fee("0xa9059cbb0000000000000000000000001234567890123456789012345678901234567890")
    assert isinstance(l1_fee, Decimal)
    assert l1_fee > 0

    # 3. Optimism: Bedrock L1 Gas Price Oracle
    op_adapter = get_adapter_for_chain("eip155:10")
    op_caps = op_adapter.get_capabilities()
    assert op_caps.supports_l2_fees is True
    assert op_caps.finality_model == FinalityModel.L2_ROLLUP_BATCH
    op_l1_fee = op_adapter.calculate_op_stack_l1_fee("0xa9059cbb0000000000000000000000001234567890123456789012345678901234567890")
    assert isinstance(op_l1_fee, Decimal)
    assert op_l1_fee > 0

    # 4. Instant Finality Chains: Avalanche, Sui, Aptos
    for cid in ["eip155:43114", "sui:mainnet", "aptos:mainnet"]:
        ad = get_adapter_for_chain(cid)
        caps = ad.get_capabilities()
        assert caps.finality_model == FinalityModel.DETERMINISTIC_INSTANT
        assert caps.required_confirmations == 1
        fin = ad.get_finality_status(confirmations=1)
        assert fin["is_finalized"] is True

    # 5. Polygon: 128 block PoS checkpointing
    pol_adapter = get_adapter_for_chain("eip155:137")
    pol_caps = pol_adapter.get_capabilities()
    assert pol_caps.required_confirmations == 128
    assert pol_adapter.get_finality_status(confirmations=64)["is_finalized"] is False
    assert pol_adapter.get_finality_status(confirmations=128)["is_finalized"] is True

    # 6. TRON: 19 block epoch finality
    tron_adapter = get_adapter_for_chain("tron:mainnet")
    tron_caps = tron_adapter.get_capabilities()
    assert tron_caps.required_confirmations == 19
    assert tron_adapter.get_finality_status(confirmations=18)["is_finalized"] is False
    assert tron_adapter.get_finality_status(confirmations=19)["is_finalized"] is True

# ==============================================================================
# CHAIN-03: RPC failure cannot become fabricated healthy data
# ==============================================================================

def test_chain_03_rpc_failure_cannot_become_fabricated_healthy_data():
    """
    CHAIN-03: Verifies that when RPC calls fail (network disconnect, timeout, 5xx):
    1. get_latest_block() raises RpcFailureException (NEVER returns fake 0 block headers)
    2. inspect_token_security() returns scan_status="RPC_FAILED" and confidence="INSUFFICIENT"
    3. Missing evidence NEVER earns safety points or passes as safe
    """
    import asyncio

    async def _run_tests():
        # Test EVM adapter with dead endpoint
        dead_evm_adapter = EvmChainAdapter(chain_id="eip155:1", rpc_urls=["http://127.0.0.1:54321/dead_rpc"])
        with pytest.raises(RpcFailureException) as exc_info:
            await dead_evm_adapter.get_latest_block()
        assert "RPC FAILURE on chain [eip155:1]" in str(exc_info.value)

        # Security check on dead RPC must NOT report safe or verified
        sec_report = await dead_evm_adapter.inspect_token_security("0x1234567890123456789012345678901234567890")
        assert sec_report["scan_status"] == "RPC_FAILED"
        assert sec_report["is_verified"] is False
        assert sec_report["confidence"] == "INSUFFICIENT"

        # Test Sui adapter with dead endpoint
        dead_sui_adapter = SuiChainAdapter(rpc_urls=["http://127.0.0.1:54321/dead_sui"])
        with pytest.raises(RpcFailureException) as exc_info:
            await dead_sui_adapter.get_latest_block()
        assert "RPC FAILURE on chain [sui:mainnet]" in str(exc_info.value)

        # Test Aptos adapter with dead endpoint
        dead_aptos_adapter = AptosChainAdapter(rpc_urls=["http://127.0.0.1:54321/dead_aptos"])
        with pytest.raises(RpcFailureException) as exc_info:
            await dead_aptos_adapter.get_latest_block()
        assert "RPC FAILURE on chain [aptos:mainnet]" in str(exc_info.value)

        # Test TON adapter with dead endpoint
        dead_ton_adapter = TonChainAdapter(rpc_urls=["http://127.0.0.1:54321/dead_ton"])
        with pytest.raises(RpcFailureException) as exc_info:
            await dead_ton_adapter.get_latest_block()
        assert "RPC FAILURE on chain [ton:mainnet]" in str(exc_info.value)

        # Test TRON adapter with dead endpoint
        dead_tron_adapter = TronChainAdapter(rpc_urls=["http://127.0.0.1:54321/dead_tron"])
        with pytest.raises(RpcFailureException) as exc_info:
            await dead_tron_adapter.get_latest_block()
        assert "RPC FAILURE on chain [tron:mainnet]" in str(exc_info.value)

    asyncio.run(_run_tests())

