import httpx
from typing import Dict, Any, List, Optional
from decimal import Decimal
from datetime import datetime, timezone
from app.adapters.evm import EvmChainAdapter
from app.adapters.base import ChainBlockHeader, RawTransactionData, ChainCapabilities, FinalityModel

class ArbitrumChainAdapter(EvmChainAdapter):
    """
    Arbitrum One (eip155:42161) adapter with ArbOS / Nitro L1 data calldata fee calculation.
    ArbGasInfo precompile: 0x000000000000000000000000000000000000006c
    """
    ARB_GAS_INFO_PRECOMPILE = "0x000000000000000000000000000000000000006c"

    def __init__(self, rpc_urls: Optional[List[str]] = None):
        super().__init__(
            chain_id="eip155:42161",
            rpc_urls=rpc_urls or ["https://arb1.arbitrum.io/rpc"]
        )
        self.finality_depth = 64

    def get_capabilities(self) -> ChainCapabilities:
        return ChainCapabilities(
            chain_id=self.chain_id,
            name="Arbitrum One",
            group_tier="B",
            network_type="evm",
            native_token="ETH",
            native_decimals=18,
            token_standards=["ERC-20"],
            supports_token_2022=False,
            supports_l2_fees=True,
            supports_reorg_finality=True,
            finality_model=FinalityModel.L2_ROLLUP_BATCH,
            required_confirmations=64,
            has_smart_contract_bytecode=True,
            supports_events_log_bloom=True,
            supports_simulation=True
        )

    def calculate_l1_calldata_fee(self, calldata_hex: str, l1_base_fee_wei: int = 20_000_000_000) -> Decimal:
        clean_hex = calldata_hex[2:] if calldata_hex.startswith("0x") else calldata_hex
        data_bytes = bytes.fromhex(clean_hex) if clean_hex else b""
        
        zero_bytes = data_bytes.count(b"\x00")
        non_zero_bytes = len(data_bytes) - zero_bytes
        
        gas_units = (zero_bytes * 4) + (non_zero_bytes * 16)
        fee_wei = gas_units * l1_base_fee_wei
        return Decimal(str(fee_wei)) / Decimal("1000000000000000000")


class OptimismChainAdapter(EvmChainAdapter):
    """
    OP Mainnet (eip155:10) adapter with OP Stack Bedrock L1 GasPriceOracle.
    GasPriceOracle Address: 0x420000000000000000000000000000000000000F
    """
    GAS_PRICE_ORACLE = "0x420000000000000000000000000000000000000f"

    def __init__(self, rpc_urls: Optional[List[str]] = None):
        super().__init__(
            chain_id="eip155:10",
            rpc_urls=rpc_urls or ["https://mainnet.optimism.io"]
        )
        self.finality_depth = 64

    def get_capabilities(self) -> ChainCapabilities:
        return ChainCapabilities(
            chain_id=self.chain_id,
            name="Optimism",
            group_tier="B",
            network_type="evm",
            native_token="ETH",
            native_decimals=18,
            token_standards=["ERC-20"],
            supports_token_2022=False,
            supports_l2_fees=True,
            supports_reorg_finality=True,
            finality_model=FinalityModel.L2_ROLLUP_BATCH,
            required_confirmations=64,
            has_smart_contract_bytecode=True,
            supports_events_log_bloom=True,
            supports_simulation=True
        )

    def calculate_op_stack_l1_fee(
        self,
        calldata_hex: str,
        l1_base_fee_wei: int = 20_000_000_000,
        scalar: Decimal = Decimal("0.684")
    ) -> Decimal:
        clean_hex = calldata_hex[2:] if calldata_hex.startswith("0x") else calldata_hex
        data_bytes = bytes.fromhex(clean_hex) if clean_hex else b""
        
        zero_bytes = data_bytes.count(b"\x00")
        non_zero_bytes = len(data_bytes) - zero_bytes
        
        gas_units = (zero_bytes * 4) + (non_zero_bytes * 16) + 188
        fee_wei = Decimal(str(gas_units * l1_base_fee_wei)) * scalar
        return fee_wei / Decimal("1000000000000000000")


class PolygonChainAdapter(EvmChainAdapter):
    """
    Polygon PoS (eip155:137) adapter with Heimdall/Bor checkpointing and 128-block finality depth.
    """
    def __init__(self, rpc_urls: Optional[List[str]] = None):
        super().__init__(
            chain_id="eip155:137",
            rpc_urls=rpc_urls or ["https://polygon-rpc.com"]
        )
        self.finality_depth = 128

    def get_capabilities(self) -> ChainCapabilities:
        return ChainCapabilities(
            chain_id=self.chain_id,
            name="Polygon PoS",
            group_tier="B",
            network_type="evm",
            native_token="POL",
            native_decimals=18,
            token_standards=["ERC-20"],
            supports_token_2022=False,
            supports_l2_fees=False,
            supports_reorg_finality=True,
            finality_model=FinalityModel.PROBABILISTIC_POS,
            required_confirmations=128,
            has_smart_contract_bytecode=True,
            supports_events_log_bloom=True,
            supports_simulation=True
        )


class AvalancheChainAdapter(EvmChainAdapter):
    """
    Avalanche C-Chain (eip155:43114) adapter with Snowman consensus and single-block finality.
    """
    def __init__(self, rpc_urls: Optional[List[str]] = None):
        super().__init__(
            chain_id="eip155:43114",
            rpc_urls=rpc_urls or ["https://api.avax.network/ext/bc/C/rpc"]
        )
        self.finality_depth = 1

    def get_capabilities(self) -> ChainCapabilities:
        return ChainCapabilities(
            chain_id=self.chain_id,
            name="Avalanche C-Chain",
            group_tier="B",
            network_type="evm",
            native_token="AVAX",
            native_decimals=18,
            token_standards=["ERC-20"],
            supports_token_2022=False,
            supports_l2_fees=False,
            supports_reorg_finality=False,
            finality_model=FinalityModel.DETERMINISTIC_INSTANT,
            required_confirmations=1,
            has_smart_contract_bytecode=True,
            supports_events_log_bloom=True,
            supports_simulation=True
        )
