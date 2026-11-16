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


@dataclass(frozen=True)
class CAIP19AssetId:
    chain_id: str
    asset_namespace: str
    asset_reference: str

    def to_caip19(self) -> str:
        """Returns standard CAIP-19 formatted string: {chain_id}/{asset_namespace}:{asset_reference}"""
        return f"{self.chain_id}/{self.asset_namespace}:{self.asset_reference}"

    def to_canonical(self) -> CanonicalAssetId:
        return CanonicalAssetId(chain_id=self.chain_id, address=self.asset_reference)

    @classmethod
    def parse(cls, identifier: str) -> "CAIP19AssetId":
        """
        Parses standard CAIP-19 string:
        e.g. solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp/token:DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263
        or   eip155:1/erc20:0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48
        or   eip155:1/slip44:60
        """
        if not identifier or "/" not in identifier:
            raise ValueError(f"Invalid CAIP-19 identifier format (missing slash): {identifier}")
        chain_part, asset_part = identifier.split("/", 1)
        chain_id = normalize_chain_id(chain_part)
        if ":" not in asset_part:
            raise ValueError(f"Invalid CAIP-19 asset component (missing namespace colon): {asset_part}")
        ns, ref = asset_part.split(":", 1)
        if ns != "slip44":
            from app.core.address_validator import AddressValidator
            is_valid, canon_addr, err = AddressValidator.validate_address_for_chain(chain_id, ref)
            if not is_valid:
                raise ValueError(f"Invalid {chain_id} address format in CAIP-19 identifier: {err}")
            ref = canon_addr or ref
        return cls(chain_id=chain_id, asset_namespace=ns, asset_reference=ref)

    @classmethod
    def from_chain_and_address(cls, chain_id: str, address: str, is_native: bool = False) -> "CAIP19AssetId":
        norm_chain = normalize_chain_id(chain_id)
        if is_native or address.lower() in ("native", "sol", "eth", "bnb", "pol", "avax", "sui", "apt", "ton", "trx", "wsol", "weth", "wbnb"):
            slip44_map = {
                SOLANA_CAIP2_CANONICAL: "501",
                "eip155:1": "60",
                "eip155:8453": "60",
                "eip155:56": "714",
                "eip155:42161": "60",
                "eip155:10": "60",
                "eip155:137": "966",
                "eip155:43114": "9000",
                "sui:mainnet": "784",
                "aptos:mainnet": "637",
                "ton:mainnet": "607",
                "tron:mainnet": "195"
            }
            slip = slip44_map.get(norm_chain, "0")
            return cls(chain_id=norm_chain, asset_namespace="slip44", asset_reference=slip)

        from app.core.address_validator import AddressValidator
        is_valid, canon_addr, err = AddressValidator.validate_address_for_chain(norm_chain, address)
        if not is_valid:
            raise ValueError(f"Invalid {norm_chain} address format: {err}")
        clean_addr = canon_addr or address

        if norm_chain.startswith("eip155"):
            return cls(chain_id=norm_chain, asset_namespace="erc20", asset_reference=clean_addr.lower())
        elif norm_chain == SOLANA_CAIP2_CANONICAL:
            return cls(chain_id=norm_chain, asset_namespace="token", asset_reference=clean_addr)
        elif norm_chain.startswith("sui"):
            return cls(chain_id=norm_chain, asset_namespace="move", asset_reference=clean_addr)
        elif norm_chain.startswith("aptos"):
            return cls(chain_id=norm_chain, asset_namespace="move", asset_reference=clean_addr)
        elif norm_chain.startswith("ton"):
            return cls(chain_id=norm_chain, asset_namespace="jetton", asset_reference=clean_addr)
        elif norm_chain.startswith("tron"):
            return cls(chain_id=norm_chain, asset_namespace="trc20", asset_reference=clean_addr)
        else:
            return cls(chain_id=norm_chain, asset_namespace="token", asset_reference=clean_addr)


SOLANA_CAIP2_CANONICAL = "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"
SOLANA_LEGACY_ALIAS = "solana:mainnet"

SUPPORTED_CHAINS: List[str] = [
    SOLANA_CAIP2_CANONICAL,
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

def normalize_chain_id(chain_id: str) -> str:
    if not chain_id:
        return chain_id
    cid = str(chain_id).strip()
    if cid in (SOLANA_LEGACY_ALIAS, "solana", "solana:mainnet-beta", SOLANA_CAIP2_CANONICAL):
        return SOLANA_CAIP2_CANONICAL
    if cid in ("sui", "sui:mainnet"):
        return "sui:mainnet"
    if cid in ("aptos", "aptos:1", "aptos:mainnet"):
        return "aptos:mainnet"
    if cid in ("ton", "ton:-239", "ton:mainnet"):
        return "ton:mainnet"
    if cid in ("tron", "tron:0x2b6653dc", "tron:mainnet"):
        return "tron:mainnet"
    return cid

def to_caip2_chain_id(chain_id: str) -> str:
    return normalize_chain_id(chain_id)

class ChainRegistry:
    @classmethod
    def get_all(cls) -> List[str]:
        return list(SUPPORTED_CHAINS)

    def __init__(self):
        self._chains: Dict[str, ChainConfig] = {
            # Tier 1 (Baseline)
            "solana:mainnet": ChainConfig(
                id="solana:mainnet",
                namespace="solana",
                reference="5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp",
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
        if not chain_id:
            return None
        norm = normalize_chain_id(chain_id)
        if norm == SOLANA_CAIP2_CANONICAL or chain_id in (SOLANA_LEGACY_ALIAS, "solana", "solana:mainnet-beta"):
            return self._chains.get("solana:mainnet")
        return self._chains.get(chain_id) or self._chains.get(norm)


    def list_active_chains(self) -> List[ChainConfig]:
        return list(self._chains.values())

chain_registry = ChainRegistry()
