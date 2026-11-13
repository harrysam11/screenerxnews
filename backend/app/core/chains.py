from typing import Dict, List, Optional
from dataclasses import dataclass
from pydantic import BaseModel

class ChainConfig(BaseModel):
    id: str # CAIP-2: e.g. 'solana:mainnet', 'eip155:1'
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
        addr = self.address.lower() if self.chain_id.startswith("eip155") else self.address
        return f"{self.chain_id}:{addr}"

    @classmethod
    def parse(cls, identifier: str) -> "CanonicalAssetId":
        parts = identifier.split(":")
        if len(parts) == 3:
            return cls(chain_id=f"{parts[0]}:{parts[1]}", address=parts[2])
        raise ValueError(f"Invalid canonical asset identifier format: {identifier}")

class ChainRegistry:
    def __init__(self):
        self._chains: Dict[str, ChainConfig] = {
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
            )
        }

    def get_chain(self, chain_id: str) -> Optional[ChainConfig]:
        return self._chains.get(chain_id)

    def list_active_chains(self) -> List[ChainConfig]:
        return list(self._chains.values())

chain_registry = ChainRegistry()
