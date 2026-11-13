from typing import Dict, List, Optional
from dataclasses import dataclass
from pydantic import BaseModel

class ChainConfig(BaseModel):
    id: str # CAIP-2: e.g. 'solana:mainnet', 'eip155:1', 'sui:mainnet'
    namespace: str
    reference: str
    name: str
    environment: str
    native_asset_symbol: str
    native_asset_decimals: int
    finality_blocks: int
    rpc_urls: List[str]
    explorer_base_url: Optional[str] = None

@dataclass(frozen=True)
class CanonicalAssetId:
    chain_id: str
    address: str

    def __str__(self) -> str:
        if self.chain_id.startswith("eip155") or self.chain_id in ("sui:mainnet", "aptos:mainnet"):
            addr = self.address.lower()
        else:
            addr = self.address
        return f"{self.chain_id}:{addr}"

    @classmethod
    def parse(cls, identifier: str) -> "CanonicalAssetId":
        parts = identifier.split(":")
        if len(parts) >= 3:
            # Handles namespace:reference:address (e.g. eip155:1:0x...)
            chain_id = f"{parts[0]}:{parts[1]}"
            address = ":".join(parts[2:])
            return cls(chain_id=chain_id, address=address)
        raise ValueError(f"Invalid canonical asset identifier format: {identifier}")

class ChainRegistry:
    def __init__(self):
        self._chains: Dict[str, ChainConfig] = {
            # Tier 1 (Baseline)
            "solana:mainnet": ChainConfig(
                id="solana:mainnet",
                namespace="solana",
                reference="mainnet",
                name="Solana",
                environment="mainnet",
                native_asset_symbol="SOL",
                native_asset_decimals=9,
                finality_blocks=32,
                rpc_urls=["https://api.mainnet-beta.solana.com"],
                explorer_base_url="https://solscan.io"
            ),
            # Tier 2 (Priority EVM)
            "eip155:1": ChainConfig(
                id="eip155:1",
                namespace="eip155",
                reference="1",
                name="Ethereum",
                environment="mainnet",
                native_asset_symbol="ETH",
                native_asset_decimals=18,
                finality_blocks=12,
                rpc_urls=["https://eth.llamarpc.com"],
                explorer_base_url="https://etherscan.io"
            ),
            "eip155:8453": ChainConfig(
                id="eip155:8453",
                namespace="eip155",
                reference="8453",
                name="Base",
                environment="mainnet",
                native_asset_symbol="ETH",
                native_asset_decimals=18,
                finality_blocks=64,
                rpc_urls=["https://mainnet.base.org"],
                explorer_base_url="https://basescan.org"
            ),
            "eip155:56": ChainConfig(
                id="eip155:56",
                namespace="eip155",
                reference="56",
                name="BNB Chain",
                environment="mainnet",
                native_asset_symbol="BNB",
                native_asset_decimals=18,
                finality_blocks=15,
                rpc_urls=["https://bsc-dataseed.binance.org"],
                explorer_base_url="https://bscscan.com"
            ),
            # Tier 3 (Secondary EVM L2s & Sidechains)
            "eip155:42161": ChainConfig(
                id="eip155:42161",
                namespace="eip155",
                reference="42161",
                name="Arbitrum One",
                environment="mainnet",
                native_asset_symbol="ETH",
                native_asset_decimals=18,
                finality_blocks=64,
                rpc_urls=["https://arb1.arbitrum.io/rpc"],
                explorer_base_url="https://arbiscan.io"
            ),
            "eip155:10": ChainConfig(
                id="eip155:10",
                namespace="eip155",
                reference="10",
                name="OP Mainnet",
                environment="mainnet",
                native_asset_symbol="ETH",
                native_asset_decimals=18,
                finality_blocks=64,
                rpc_urls=["https://mainnet.optimism.io"],
                explorer_base_url="https://optimistic.etherscan.io"
            ),
            "eip155:137": ChainConfig(
                id="eip155:137",
                namespace="eip155",
                reference="137",
                name="Polygon PoS",
                environment="mainnet",
                native_asset_symbol="POL",
                native_asset_decimals=18,
                finality_blocks=128,
                rpc_urls=["https://polygon-rpc.com"],
                explorer_base_url="https://polygonscan.com"
            ),
            "eip155:43114": ChainConfig(
                id="eip155:43114",
                namespace="eip155",
                reference="43114",
                name="Avalanche C-Chain",
                environment="mainnet",
                native_asset_symbol="AVAX",
                native_asset_decimals=18,
                finality_blocks=1,
                rpc_urls=["https://api.avax.network/ext/bc/C/rpc"],
                explorer_base_url="https://snowtrace.io"
            ),
            # Tier 4 (Non-EVM Adapters)
            "sui:mainnet": ChainConfig(
                id="sui:mainnet",
                namespace="sui",
                reference="mainnet",
                name="Sui",
                environment="mainnet",
                native_asset_symbol="SUI",
                native_asset_decimals=9,
                finality_blocks=1,
                rpc_urls=["https://fullnode.mainnet.sui.io:443"],
                explorer_base_url="https://suiscan.xyz"
            ),
            "aptos:mainnet": ChainConfig(
                id="aptos:mainnet",
                namespace="aptos",
                reference="mainnet",
                name="Aptos",
                environment="mainnet",
                native_asset_symbol="APT",
                native_asset_decimals=8,
                finality_blocks=1,
                rpc_urls=["https://fullnode.mainnet.aptoslabs.com/v1"],
                explorer_base_url="https://explorer.aptoslabs.com"
            ),
            "ton:mainnet": ChainConfig(
                id="ton:mainnet",
                namespace="ton",
                reference="mainnet",
                name="TON",
                environment="mainnet",
                native_asset_symbol="TON",
                native_asset_decimals=9,
                finality_blocks=1,
                rpc_urls=["https://toncenter.com/api/v2/jsonRPC"],
                explorer_base_url="https://tonscan.org"
            ),
            "tron:mainnet": ChainConfig(
                id="tron:mainnet",
                namespace="tron",
                reference="mainnet",
                name="TRON",
                environment="mainnet",
                native_asset_symbol="TRX",
                native_asset_decimals=6,
                finality_blocks=19,
                rpc_urls=["https://api.trongrid.io"],
                explorer_base_url="https://tronscan.org"
            )
        }

    def get_chain(self, chain_id: str) -> Optional[ChainConfig]:
        if chain_id == "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp":
            chain_id = "solana:mainnet"
        return self._chains.get(chain_id)

    def list_active_chains(self) -> List[ChainConfig]:
        return list(self._chains.values())

chain_registry = ChainRegistry()
