import pytest
import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch, MagicMock

from app.core.config import settings
from app.core.chains import SOLANA_CAIP2_CANONICAL, SOLANA_LEGACY_ALIAS, chain_registry
from app.services.provider_manager import ALL_12_CHAINS
from app.core.chain_capabilities import (
    ChainCapability,
    CapabilityStatus,
    capability_registry,
    ChainCapabilityRegistry
)
from app.core.address_validator import AddressValidator, AddressValidationError
from app.core.network import OfflineModeBlockedException
from app.adapters import (
    get_adapter_for_chain,
    BaseChainAdapter,
    ChainBlockHeader,
    RawTransactionData,
    ChainAdapterError,
    RpcError,
    DataUnavailableError,
    UnsupportedCapabilityError,
    ChainMismatchError,
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

# =============================================================================
# 1. Capability Registry Completeness & Uniqueness Across All 12 Chains
# =============================================================================

def test_d_01_capability_registry_12_chains_coverage():
    all_chains = capability_registry.list_all_chain_ids()
    assert len(all_chains) == 12

    expected_canonical_ids = {
        "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp",
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
    }
    assert set(all_chains) == expected_canonical_ids

    # Verify each chain has all 14 capability areas explicitly mapped
    for chain_id in all_chains:
        profile = capability_registry.get_profile(chain_id)
        assert profile is not None
        assert profile.chain_name
        assert profile.chain_family in ("svm", "evm", "move", "tvm")
        assert len(profile.capabilities) == len(ChainCapability)

        for cap in ChainCapability:
            assert cap in profile.capabilities
            desc = profile.capabilities[cap]
            assert desc.status in (
                CapabilityStatus.SUPPORTED,
                CapabilityStatus.PARTIAL,
                CapabilityStatus.UNSUPPORTED,
                CapabilityStatus.UNVERIFIED
            )
            assert len(desc.description) > 0

def test_d_02_solana_canonical_and_alias_resolution():
    canonical_prof = capability_registry.get_profile(SOLANA_CAIP2_CANONICAL)
    legacy_prof = capability_registry.get_profile(SOLANA_LEGACY_ALIAS)

    assert canonical_prof is not None
    assert legacy_prof is not None
    assert canonical_prof.canonical_id == SOLANA_CAIP2_CANONICAL
    assert legacy_prof.canonical_id == SOLANA_CAIP2_CANONICAL

# =============================================================================
# 2. Address Validation & Normalization for All Chain Families
# =============================================================================

def test_d_03_evm_address_validation_and_eip55_checksum():
    # Known Ethereum Foundation donation address
    valid_lower = "0xde0b295669a9fd93d5f28d9ec85e40f4cb697bae"
    expected_eip55 = "0xde0B295669a9FD93d5F28D9Ec85E40f4cb697BAe"

    # 1. EIP-55 checksum calculation
    checksummed = AddressValidator.to_eip55_checksum(valid_lower)
    assert checksummed == expected_eip55

    # 2. Validation with strict checksum
    ok, norm, csum = AddressValidator.validate_evm_address(expected_eip55, strict_checksum=True)
    assert ok is True
    assert norm == valid_lower
    assert csum == expected_eip55

    # 3. Checksum mismatch in strict mode
    bad_casing = "0xde0B295669A9FD93d5F28D9Ec85E40f4cb697BAe" # invalid casing on 'A9'
    ok_bad, _, err = AddressValidator.validate_evm_address(bad_casing, strict_checksum=True)
    assert ok_bad is False
    assert "checksum mismatch" in err

    # 4. Invalid length and characters
    ok_len, _, _ = AddressValidator.validate_evm_address("0x12345")
    assert ok_len is False
    ok_char, _, _ = AddressValidator.validate_evm_address("0xde0b295669a9fd93d5f28d9ec85e40f4cb697bzz")
    assert ok_char is False

def test_d_04_solana_address_validation():
    # Valid Solana mint (USDC)
    usdc_mint = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
    ok, norm, _ = AddressValidator.validate_solana_address(usdc_mint)
    assert ok is True
    assert norm == usdc_mint

    # Invalid Solana addresses
    ok_short, _, _ = AddressValidator.validate_solana_address("too_short")
    assert ok_short is False
    ok_b58, _, _ = AddressValidator.validate_solana_address("0OIl_invalid_base58_chars_!@#$1234567890123456")
    assert ok_b58 is False

def test_d_05_move_address_validation_sui_and_aptos():
    # Sui address normalization to 66 chars with leading zeros
    sui_short = "0x2"
    ok, norm_sui, _ = AddressValidator.validate_sui_address(sui_short)
    assert ok is True
    assert len(norm_sui) == 66
    assert norm_sui == "0x" + "0" * 63 + "2"

    # Aptos address normalization
    aptos_short = "0x1"
    ok_apt, norm_apt, _ = AddressValidator.validate_aptos_address(aptos_short)
    assert ok_apt is True
    assert norm_apt == "0x" + "0" * 63 + "1"

    # Aptos Move type tag validation
    type_tag = "0x1::aptos_coin::AptosCoin"
    ok_tag, norm_tag, _ = AddressValidator.validate_aptos_address(type_tag)
    assert ok_tag is True
    assert norm_tag.startswith("0x" + "0" * 63 + "1::aptos_coin::AptosCoin")

def test_d_06_ton_address_validation_raw_and_user_friendly():
    # 1. Valid raw TON address
    raw_ton = "0:5b38da6a701c568545dcfcb03fcb875f56beddc4ff2995543c7b7d6056bdf3b5"
    ok_raw, norm_raw, _ = AddressValidator.validate_ton_address(raw_ton)
    assert ok_raw is True
    assert norm_raw == raw_ton

    # 2. Invalid workchain
    ok_bad_wc, _, err = AddressValidator.validate_ton_address("2:5b38da6a701c568545dcfcb03fcb875f56beddc4ff2995543c7b7d6056bdf3b5")
    assert ok_bad_wc is False
    assert "Invalid TON workchain" in err

    # 3. User-friendly address CRC16 validation
    # Construct a valid user-friendly payload: flag(0x11) + wc(0x00) + 32-byte-hash + CRC16
    payload_34 = bytes([0x11, 0x00]) + bytes.fromhex("5b38da6a701c568545dcfcb03fcb875f56beddc4ff2995543c7b7d6056bdf3b5")
    crc = AddressValidator._crc16_ccitt(payload_34)
    import base64
    b64_addr = base64.urlsafe_b64encode(payload_34 + crc.to_bytes(2, "big")).decode("utf-8")

    ok_uf, norm_uf, _ = AddressValidator.validate_ton_address(b64_addr)
    assert ok_uf is True
    assert norm_uf == raw_ton

    # Tampered checksum
    bad_b64 = b64_addr[:-2] + "AA"
    ok_tampered, _, err_t = AddressValidator.validate_ton_address(bad_b64)
    assert ok_tampered is False

def test_d_07_tron_address_validation_and_conversion():
    # Known TRON USDT contract: TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t
    usdt_tron = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
    ok, b58_norm, hex41 = AddressValidator.validate_tron_address(usdt_tron)
    assert ok is True
    assert b58_norm == usdt_tron
    assert hex41.startswith("41")
    assert len(hex41) == 42

    # Bi-directional conversion
    converted_b58 = AddressValidator.tron_hex_to_base58(hex41)
    assert converted_b58 == usdt_tron

    # Invalid TRON prefix or checksum
    ok_bad, _, _ = AddressValidator.validate_tron_address("WR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t")
    assert ok_bad is False

# =============================================================================
# 3. Chain ID Verification & Network Identity Mismatch (EVM)
# =============================================================================

def test_d_08_evm_chain_id_verification_and_mismatch():
    adapter = EvmChainAdapter("eip155:1", ["https://eth.llamarpc.com"])

    # 1. Matching Chain ID
    adapter._call_rpc = AsyncMock(return_value="0x1") # 1 in hex
    res = asyncio.run(adapter.verify_chain_id())
    assert res is True

    # 2. Mismatched Chain ID (Endpoint returns 8453 for Base when configured for Ethereum 1)
    adapter._call_rpc = AsyncMock(return_value="0x2105") # 8453 in hex
    with pytest.raises(ChainMismatchError) as exc_info:
        asyncio.run(adapter.verify_chain_id())
    assert exc_info.value.expected_chain_id == "eip155:1"
    assert exc_info.value.actual_chain_id == "eip155:8453"

# =============================================================================
# 4. Verified Block/Slot/Time Semantics (No Fabricated Defaults)
# =============================================================================

def test_d_09_solana_block_and_slot_semantics():
    adapter = SolanaChainAdapter("solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp", ["https://api.mainnet-beta.solana.com"])

    # Synthetic protocol fixture matching Solana JSON-RPC getLatestBlockhash & getBlockTime
    async def mock_call(method, params):
        if method == "getLatestBlockhash":
            return {
                "context": {"slot": 250123456},
                "value": {"blockhash": "4vJ9JU1bJJE96RtDg5Jn2Hq4EHg4p8Qv2fJ3kL5mN7pQ"}
            }
        elif method == "getBlockTime":
            return 1700000000
        return None

    with patch("app.services.solana_rpc.solana_rpc_service._call_rpc", side_effect=mock_call):
        hdr = asyncio.run(adapter.get_latest_block(commitment="finalized"))
        assert hdr.block_number == 250123456
        assert hdr.block_hash == "4vJ9JU1bJJE96RtDg5Jn2Hq4EHg4p8Qv2fJ3kL5mN7pQ"
        assert hdr.block_time == datetime.fromtimestamp(1700000000, timezone.utc)
        assert hdr.finalized is True

def test_d_10_evm_block_header_parsing_and_error_handling():
    adapter = EvmChainAdapter("eip155:1", ["https://eth.llamarpc.com"])

    # Synthetic protocol fixture for eth_getBlockByNumber
    adapter._call_rpc = AsyncMock(return_value={
        "number": "0x123456",
        "hash": "0xabcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
        "parentHash": "0x1111111111111111111111111111111111111111111111111111111111111111",
        "timestamp": "0x6553f100" # 1700000000
    })

    hdr = asyncio.run(adapter.get_latest_block())
    assert hdr.block_number == 1193046
    assert hdr.block_hash.startswith("0xabcdef")
    assert hdr.block_time == datetime.fromtimestamp(1700000000, timezone.utc)

    # RPC failure must raise DataUnavailableError rather than return block 0
    adapter._call_rpc = AsyncMock(return_value=None)
    with pytest.raises(DataUnavailableError):
        asyncio.run(adapter.get_latest_block())

def test_d_11_sui_checkpoint_header_parsing():
    adapter = SuiChainAdapter(["https://fullnode.mainnet.sui.io:443"])

    async def mock_sui(method, params):
        if method == "sui_getLatestCheckpointSequenceNumber":
            return "45000000"
        elif method == "sui_getCheckpoint":
            return {
                "sequenceNumber": "45000000",
                "digest": "6bK3R2fL9mP7qT5vX8wY1zN4cJ2hE5dA7sF9gH2jK4mP",
                "timestampMs": "1700000000000"
            }
        return None

    adapter._call_rpc = AsyncMock(side_effect=mock_sui)
    hdr = asyncio.run(adapter.get_latest_block())
    assert hdr.block_number == 45000000
    assert hdr.block_hash == "6bK3R2fL9mP7qT5vX8wY1zN4cJ2hE5dA7sF9gH2jK4mP"
    assert hdr.block_time == datetime.fromtimestamp(1700000000, timezone.utc)

def test_d_12_tron_nowblock_header_parsing():
    adapter = TronChainAdapter(["https://api.trongrid.io"])

    adapter._call_api = AsyncMock(return_value={
        "blockID": "0000000003b879e8abcd1234abcd1234abcd1234abcd1234abcd1234abcd1234",
        "block_header": {
            "raw_data": {
                "number": 62421480,
                "timestamp": 1700000000000,
                "parentHash": "0000000003b879e7abcd1234abcd1234abcd1234abcd1234abcd1234abcd1234"
            }
        }
    })

    hdr = asyncio.run(adapter.get_latest_block())
    assert hdr.block_number == 62421480
    assert hdr.block_time == datetime.fromtimestamp(1700000000, timezone.utc)

# =============================================================================
# 5. Token Metadata & Verified Decimals (No Hardcoded 18/9 Defaults)
# =============================================================================

def test_d_13_evm_token_metadata_verified_decimals():
    adapter = EvmChainAdapter("eip155:1", ["https://eth.llamarpc.com"])

    # When eth_call returns 6 decimals (e.g. USDC on Ethereum)
    # 6 formatted as 32-byte hex: 0x0000...0006
    hex_6_decimals = "0x" + "0" * 63 + "6"
    adapter._call_rpc = AsyncMock(return_value=hex_6_decimals)

    meta = asyncio.run(adapter.get_token_metadata("0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"))
    assert meta["decimals"] == 6 # Verified 6, not hardcoded 18!

    # When eth_call fails, decimals must be None, NOT fabricated 18
    adapter._call_rpc = AsyncMock(return_value=None)
    meta_fail = asyncio.run(adapter.get_token_metadata("0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"))
    assert meta_fail["decimals"] is None

def test_d_14_solana_token_metadata_verified():
    adapter = SolanaChainAdapter("solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp", ["https://api.mainnet-beta.solana.com"])

    mock_mint = {
        "decimals": 6,
        "supply": "1000000000000",
        "mint_authority": None,
        "freeze_authority": None,
        "is_token_2022": False,
        "owner_program": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
    }

    with patch("app.services.solana_rpc.solana_rpc_service.get_parsed_mint_info", return_value=mock_mint):
        meta = asyncio.run(adapter.get_token_metadata("EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"))
        assert meta["decimals"] == 6
        assert meta["total_supply"] == "1000000000000"

    # When mint info is missing, raises DataUnavailableError
    with patch("app.services.solana_rpc.solana_rpc_service.get_parsed_mint_info", return_value=None):
        with pytest.raises(DataUnavailableError):
            asyncio.run(adapter.get_token_metadata("EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"))

# =============================================================================
# 6. Finality Depth & Consensus Specifications
# =============================================================================

def test_d_15_consensus_and_finality_depths():
    # 1. Solana: Tower BFT Root (32 slots)
    sol_prof = capability_registry.get_profile("solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp")
    assert sol_prof.finality_blocks_or_slots == 32
    assert sol_prof.reorg_possible is True

    # 2. Avalanche: Snowman Consensus (1 block, 0 reorg)
    avax_prof = capability_registry.get_profile("eip155:43114")
    assert avax_prof.finality_blocks_or_slots == 1
    assert avax_prof.reorg_possible is False

    # 3. Polygon: 128 blocks depth
    poly_prof = capability_registry.get_profile("eip155:137")
    assert poly_prof.finality_blocks_or_slots == 128
    assert poly_prof.reorg_possible is True

    # 4. TRON: 19 Super Representative confirmations (~57s)
    tron_prof = capability_registry.get_profile("tron:mainnet")
    assert tron_prof.finality_blocks_or_slots == 19
    assert tron_prof.reorg_possible is True

    # 5. Sui & Aptos: Instant single-checkpoint / committed ledger version finality
    sui_prof = capability_registry.get_profile("sui:mainnet")
    aptos_prof = capability_registry.get_profile("aptos:mainnet")
    assert sui_prof.finality_blocks_or_slots == 1
    assert sui_prof.reorg_possible is False
    assert aptos_prof.finality_blocks_or_slots == 1
    assert aptos_prof.reorg_possible is False

# =============================================================================
# 7. Native Asset Units & Conversion Factors
# =============================================================================

def test_d_16_native_asset_specifications():
    # Verify exact native asset smallest units and conversion factors across chains
    sol = capability_registry.get_profile("solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp").native_asset
    assert sol.symbol == "SOL"
    assert sol.decimals == 9
    assert sol.smallest_unit_name == "lamport"
    assert sol.conversion_factor == 10**9

    eth = capability_registry.get_profile("eip155:1").native_asset
    assert eth.symbol == "ETH"
    assert eth.decimals == 18
    assert eth.smallest_unit_name == "wei"
    assert eth.conversion_factor == 10**18

    sui = capability_registry.get_profile("sui:mainnet").native_asset
    assert sui.symbol == "SUI"
    assert sui.decimals == 9
    assert sui.smallest_unit_name == "MIST"
    assert sui.conversion_factor == 10**9

    apt = capability_registry.get_profile("aptos:mainnet").native_asset
    assert apt.symbol == "APT"
    assert apt.decimals == 8
    assert apt.smallest_unit_name == "Octa"
    assert apt.conversion_factor == 10**8

    ton = capability_registry.get_profile("ton:mainnet").native_asset
    assert ton.symbol == "TON"
    assert ton.decimals == 9
    assert ton.smallest_unit_name == "nanoton"
    assert ton.conversion_factor == 10**9

    trx = capability_registry.get_profile("tron:mainnet").native_asset
    assert trx.symbol == "TRX"
    assert trx.decimals == 6
    assert trx.smallest_unit_name == "SUN"
    assert trx.conversion_factor == 10**6

# =============================================================================
# 8. Factory Instantiation & Offline Isolation
# =============================================================================

def test_d_17_adapter_factory_and_offline_isolation():
    # 1. Factory returns correct specialized adapter for each chain
    for chain_id in ALL_12_CHAINS:
        adapter = get_adapter_for_chain(chain_id)
        assert isinstance(adapter, BaseChainAdapter)

    # 2. In offline mode, un-mocked network requests are intercepted before transport
    old_mode = settings.OPERATING_MODE
    try:
        settings.OPERATING_MODE = "offline_research"
        for chain_id in ALL_12_CHAINS:
            adapter = get_adapter_for_chain(chain_id)
            with pytest.raises(OfflineModeBlockedException):
                asyncio.run(adapter.get_latest_block())
    finally:
        settings.OPERATING_MODE = old_mode
