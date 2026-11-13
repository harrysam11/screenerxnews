import sys
import os
import pytest
from decimal import Decimal

sys.path.insert(0, os.path.abspath("./backend"))
from app.core.chains import chain_registry, CanonicalAssetId
from app.adapters import (
    get_adapter_for_chain,
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


# =====================================================================
# 1. Chain Registry & Canonical Identifiers
# =====================================================================
def test_all_12_chains_registered():
    expected_chains = [
        "solana:mainnet",
        "eip155:1",
        "eip155:8453",
        "eip155:56",
        "eip155:42161",
        "eip155:10",
        "eip155:137",
        "eip155:43114",
        "sui:mainnet",
        "aptos:mainnet",
        "ton:mainnet",
        "tron:mainnet"
    ]
    for cid in expected_chains:
        config = chain_registry.get_chain(cid)
        assert config is not None, f"Missing chain in registry: {cid}"
        assert config.id == cid
        assert config.native_asset_symbol is not None
        assert len(config.rpc_urls) > 0


def test_canonical_asset_id_formatting():
    # EVM should normalize to lowercase
    evm_id = CanonicalAssetId("eip155:42161", "0xABCDEF1234567890ABCDEF1234567890ABCDEF12")
    assert str(evm_id) == "eip155:42161:0xabcdef1234567890abcdef1234567890abcdef12"

    # Solana should preserve Base58 casing
    sol_id = CanonicalAssetId("solana:mainnet", "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263")
    assert str(sol_id) == "solana:mainnet:DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"

    # TRON should preserve Base58 format
    tron_id = CanonicalAssetId("tron:mainnet", "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t")
    assert str(tron_id) == "tron:mainnet:TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"

    # Parsing test
    parsed = CanonicalAssetId.parse("eip155:10:0x1234")
    assert parsed.chain_id == "eip155:10"
    assert parsed.address == "0x1234"


# =====================================================================
# 2. Tier 3 EVM Specialized Adapters (Arbitrum, OP, Polygon, Avalanche)
# =====================================================================
def test_arbitrum_adapter_l1_fee_calculation():
    adapter = ArbitrumChainAdapter()
    assert adapter.chain_id == "eip155:42161"

    # Hex payload with 2 zero bytes and 2 non-zero bytes (0x0000ffff)
    calldata = "0x0000ffff"
    # Gas = (2 * 4) + (2 * 16) = 8 + 32 = 40 units
    # At 20 Gwei (20,000,000,000 wei) = 800,000,000,000 wei = 0.0000008 ETH
    fee_eth = adapter.calculate_l1_calldata_fee(calldata, l1_base_fee_wei=20_000_000_000)
    assert fee_eth == Decimal("0.0000008")


def test_optimism_adapter_l1_fee_calculation():
    adapter = OptimismChainAdapter()
    assert adapter.chain_id == "eip155:10"

    # Hex payload with 2 zero bytes and 2 non-zero bytes (0x0000ffff)
    calldata = "0x0000ffff"
    # Gas units = 40 + 188 overhead = 228 units
    # At 20 Gwei with scalar 1.0 = 4,560,000,000,000 wei = 0.00000456 ETH
    fee_eth = adapter.calculate_op_stack_l1_fee(calldata, l1_base_fee_wei=20_000_000_000, scalar=Decimal("1.0"))
    assert fee_eth == Decimal("0.00000456")


def test_polygon_and_avalanche_finality_depth():
    poly = PolygonChainAdapter()
    assert poly.chain_id == "eip155:137"
    assert poly.finality_depth == 128

    avax = AvalancheChainAdapter()
    assert avax.chain_id == "eip155:43114"
    assert avax.finality_depth == 1


# =====================================================================
# 3. Tier 4 Non-EVM Adapters (Sui, Aptos, TON, TRON)
# =====================================================================
def test_sui_adapter_treasury_cap_security():
    adapter = SuiChainAdapter()
    assert adapter.chain_id == "sui:mainnet"

    # Burned TreasuryCap -> Mint revoked
    burned_sec = adapter.evaluate_treasury_cap(treasury_cap_owner="0x0000000000000000000000000000000000000000000000000000000000000000")
    assert burned_sec["is_mint_revoked"] is True

    # Active dev TreasuryCap -> Mint NOT revoked
    active_sec = adapter.evaluate_treasury_cap(treasury_cap_owner="0x1234567890abcdef1234567890abcdef12345678")
    assert active_sec["is_mint_revoked"] is False


def test_aptos_adapter_capability_security():
    adapter = AptosChainAdapter()
    assert adapter.chain_id == "aptos:mainnet"

    # With capabilities present
    with_caps = {
        "mint_capability": {"dummy": 1},
        "freeze_capability": {"dummy": 1}
    }
    sec_with = adapter.evaluate_coin_capabilities(with_caps)
    assert sec_with["is_mint_revoked"] is False
    assert sec_with["is_freeze_revoked"] is False

    # Without capabilities (burned / unpacked)
    sec_revoked = adapter.evaluate_coin_capabilities({})
    assert sec_revoked["is_mint_revoked"] is True
    assert sec_revoked["is_freeze_revoked"] is True


def test_ton_adapter_jetton_security():
    adapter = TonChainAdapter()
    assert adapter.chain_id == "ton:mainnet"

    # Mintable == 0 and admin == None -> Safe renounced jetton
    safe_eval = adapter.evaluate_jetton_data(total_supply=1000000, mintable=0, admin_address=None)
    assert safe_eval["is_mint_revoked"] is True
    assert safe_eval["is_ownership_renounced"] is True

    # Mintable == -1 and active admin -> Risky
    risky_eval = adapter.evaluate_jetton_data(total_supply=1000000, mintable=-1, admin_address="0:123456")
    assert risky_eval["is_mint_revoked"] is False
    assert risky_eval["is_ownership_renounced"] is False


def test_tron_adapter_trc20_security():
    adapter = TronChainAdapter()
    assert adapter.chain_id == "tron:mainnet"

    # Zero address owner
    safe_eval = adapter.evaluate_trc20_security(owner_address_hex="410000000000000000000000000000000000000000", has_mint_selector=False)
    assert safe_eval["is_ownership_renounced"] is True
    assert safe_eval["is_mint_revoked"] is True

    # Active owner
    active_eval = adapter.evaluate_trc20_security(owner_address_hex="411122334455667788990011223344556677889900", has_mint_selector=False)
    assert active_eval["is_ownership_renounced"] is False
    assert active_eval["is_mint_revoked"] is False


# =====================================================================
# 4. Adapter Factory Function
# =====================================================================
def test_get_adapter_factory():
    assert isinstance(get_adapter_for_chain("solana:mainnet"), SolanaChainAdapter)
    assert isinstance(get_adapter_for_chain("eip155:1"), EvmChainAdapter)
    assert isinstance(get_adapter_for_chain("eip155:42161"), ArbitrumChainAdapter)
    assert isinstance(get_adapter_for_chain("eip155:10"), OptimismChainAdapter)
    assert isinstance(get_adapter_for_chain("eip155:137"), PolygonChainAdapter)
    assert isinstance(get_adapter_for_chain("eip155:43114"), AvalancheChainAdapter)
    assert isinstance(get_adapter_for_chain("sui:mainnet"), SuiChainAdapter)
    assert isinstance(get_adapter_for_chain("aptos:mainnet"), AptosChainAdapter)
    assert isinstance(get_adapter_for_chain("ton:mainnet"), TonChainAdapter)
    assert isinstance(get_adapter_for_chain("tron:mainnet"), TronChainAdapter)

    with pytest.raises(ValueError):
        get_adapter_for_chain("unknown_chain:123")
