import sys
import os
import pytest
from decimal import Decimal

sys.path.insert(0, os.path.abspath("./backend"))
from app.core.chains import chain_registry, CanonicalAssetId
from app.adapters.solana import SolanaChainAdapter
from app.adapters.evm import EvmChainAdapter

def test_chain_registry_initial_networks():
    active = chain_registry.list_active_chains()
    assert len(active) >= 4
    chain_ids = [c.id for c in active]
    assert "solana:mainnet" in chain_ids
    assert "eip155:1" in chain_ids
    assert "eip155:8453" in chain_ids
    assert "eip155:56" in chain_ids

    # Check Base config
    base_chain = chain_registry.get_chain("eip155:8453")
    assert base_chain is not None
    assert base_chain.name == "Base"
    assert base_chain.native_asset_symbol == "ETH"
    assert base_chain.finality_blocks == 64

def test_canonical_asset_id_parsing_and_formatting():
    # Solana: Case sensitive base58 address preserved
    sol_id_str = "solana:mainnet:DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
    sol_asset = CanonicalAssetId.parse(sol_id_str)
    assert sol_asset.chain_id == "solana:mainnet"
    assert sol_asset.address == "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
    assert str(sol_asset) == sol_id_str

    # EVM: Case insensitive hex address normalized to lowercase
    evm_id_str = "eip155:8453:0x4ed4E862860beD51a9570b96d89aF5E1B0Efefed"
    evm_asset = CanonicalAssetId.parse(evm_id_str)
    assert evm_asset.chain_id == "eip155:8453"
    assert str(evm_asset) == "eip155:8453:0x4ed4e862860bed51a9570b96d89af5e1b0efefed"

def test_cross_chain_address_isolation():
    # Same contract address on Ethereum vs Base represents two different canonical assets
    addr = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
    eth_asset = CanonicalAssetId(chain_id="eip155:1", address=addr)
    base_asset = CanonicalAssetId(chain_id="eip155:8453", address=addr)
    assert str(eth_asset) != str(base_asset)
    assert eth_asset != base_asset

def test_adapter_initialization_contracts():
    sol_adapter = SolanaChainAdapter()
    assert sol_adapter.chain_id == "solana:mainnet"
    assert len(sol_adapter.rpc_urls) > 0

    base_chain = chain_registry.get_chain("eip155:8453")
    evm_adapter = EvmChainAdapter(chain_id=base_chain.id, rpc_urls=base_chain.rpc_urls)
    assert evm_adapter.chain_id == "eip155:8453"
    assert evm_adapter.rpc_urls[0] == "https://mainnet.base.org"
