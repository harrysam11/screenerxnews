import httpx
from typing import Dict, Any, List, Optional
from decimal import Decimal
from datetime import datetime, timezone
from app.adapters.evm import EvmChainAdapter
from app.adapters.base import ChainBlockHeader, RawTransactionData

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

    def calculate_l1_calldata_fee(self, calldata_hex: str, l1_base_fee_wei: int = 20_000_000_000) -> Decimal:
        """
        Computes L1 posting cost based on non-zero (16 gas) and zero bytes (4 gas).
        """
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
        
        # Bedrock L1 gas: 16 per non-zero, 4 per zero + fixed overhead (approx 188)
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
