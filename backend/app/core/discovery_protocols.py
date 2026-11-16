from typing import Dict, List, Optional, Any
from pydantic import BaseModel, Field

class ProtocolDescriptor(BaseModel):
    protocol_id: str
    name: str
    protocol_type: str  # "amm_v2", "clmm_v3", "bonding_curve", "orderbook", "hybrid"
    chain_id: str
    factory_address: Optional[str] = None
    program_id: Optional[str] = None
    event_topic_or_type: Optional[str] = None
    fee_tier_bps: int = 30
    is_active: bool = True
    support_status: str = "SUPPORTED"  # "SUPPORTED", "PARTIAL", "UNSUPPORTED"
    notes: Optional[str] = None

class ProtocolRegistry:
    """
    Centralized Protocol and Factory Registry for Real Multi-Chain Discovery (P10.2-E).
    Explicitly tracks supported DEX factories, event signatures, and protocol limitations.
    """
    # Standard EVM Event Topic Hashes
    UNISWAP_V2_PAIR_CREATED_TOPIC = "0x0d3648abd0acd5c52c6f8a5350ce05f7e5204bbc856521c132a7f43fde1e84f3"
    UNISWAP_V3_POOL_CREATED_TOPIC = "0x783cca1c0412dd0d695e784568c96da2e9c22ff989357a2e8b1d9b2b4e6b7118"

    def __init__(self):
        self._protocols: Dict[str, List[ProtocolDescriptor]] = {}
        self._init_protocols()

    def _init_protocols(self):
        # 1. Solana Protocols
        self._protocols["solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"] = [
            ProtocolDescriptor(
                protocol_id="raydium_amm_v4",
                name="Raydium AMM V4",
                protocol_type="amm_v2",
                chain_id="solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp",
                program_id="675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8",
                event_topic_or_type="initialize2",
                fee_tier_bps=25,
                support_status="SUPPORTED",
                notes="Standard constant product AMM pool creation"
            ),
            ProtocolDescriptor(
                protocol_id="raydium_cpmm",
                name="Raydium CPMM",
                protocol_type="amm_v2",
                chain_id="solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp",
                program_id="CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C",
                event_topic_or_type="initialize",
                fee_tier_bps=25,
                support_status="SUPPORTED"
            ),
            ProtocolDescriptor(
                protocol_id="pump_fun",
                name="Pump.fun Bonding Curve",
                protocol_type="bonding_curve",
                chain_id="solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp",
                program_id="6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",
                event_topic_or_type="Create",
                fee_tier_bps=100,
                support_status="SUPPORTED",
                notes="Solana bonding curve token and virtual pool initialization"
            ),
            ProtocolDescriptor(
                protocol_id="orca_whirlpool",
                name="Orca Whirlpool",
                protocol_type="clmm_v3",
                chain_id="solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp",
                program_id="whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc",
                event_topic_or_type="initialize_pool",
                fee_tier_bps=30,
                support_status="PARTIAL",
                notes="Concentrated liquidity pool decoding"
            )
        ]
        self._protocols["solana:mainnet"] = self._protocols["solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"]

        # 2. Ethereum Mainnet (eip155:1)
        self._protocols["eip155:1"] = [
            ProtocolDescriptor(
                protocol_id="uniswap_v2",
                name="Uniswap V2",
                protocol_type="amm_v2",
                chain_id="eip155:1",
                factory_address="0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f",
                event_topic_or_type=self.UNISWAP_V2_PAIR_CREATED_TOPIC,
                fee_tier_bps=30,
                support_status="SUPPORTED"
            ),
            ProtocolDescriptor(
                protocol_id="uniswap_v3",
                name="Uniswap V3",
                protocol_type="clmm_v3",
                chain_id="eip155:1",
                factory_address="0x1F98431c8aD98523631AE4a59f267346ea31F984",
                event_topic_or_type=self.UNISWAP_V3_POOL_CREATED_TOPIC,
                fee_tier_bps=30,
                support_status="SUPPORTED"
            ),
            ProtocolDescriptor(
                protocol_id="sushiswap_v2",
                name="SushiSwap V2",
                protocol_type="amm_v2",
                chain_id="eip155:1",
                factory_address="0xC0AEe478e3658e2610c5F7A4A2E1777cE9e4f2Ac",
                event_topic_or_type=self.UNISWAP_V2_PAIR_CREATED_TOPIC,
                fee_tier_bps=30,
                support_status="SUPPORTED"
            )
        ]

        # 3. Base (eip155:8453)
        self._protocols["eip155:8453"] = [
            ProtocolDescriptor(
                protocol_id="aerodrome_v2",
                name="Aerodrome Finance",
                protocol_type="amm_v2",
                chain_id="eip155:8453",
                factory_address="0x420DD381b31aEf6683db6B902084cB0FFECe40Da",
                event_topic_or_type=self.UNISWAP_V2_PAIR_CREATED_TOPIC,
                fee_tier_bps=30,
                support_status="SUPPORTED"
            ),
            ProtocolDescriptor(
                protocol_id="uniswap_v3_base",
                name="Uniswap V3 (Base)",
                protocol_type="clmm_v3",
                chain_id="eip155:8453",
                factory_address="0x33128a8fC17869897dcE68Ed026d694621f6FDfD",
                event_topic_or_type=self.UNISWAP_V3_POOL_CREATED_TOPIC,
                fee_tier_bps=30,
                support_status="SUPPORTED"
            )
        ]

        # 4. BNB Chain (eip155:56)
        self._protocols["eip155:56"] = [
            ProtocolDescriptor(
                protocol_id="pancakeswap_v2",
                name="PancakeSwap V2",
                protocol_type="amm_v2",
                chain_id="eip155:56",
                factory_address="0xcA143Ce32Fe78f1f7019d7d551a6402fc5350c73",
                event_topic_or_type=self.UNISWAP_V2_PAIR_CREATED_TOPIC,
                fee_tier_bps=25,
                support_status="SUPPORTED"
            ),
            ProtocolDescriptor(
                protocol_id="pancakeswap_v3",
                name="PancakeSwap V3",
                protocol_type="clmm_v3",
                chain_id="eip155:56",
                factory_address="0x0BFbCF9fa4f9C56B0F40a671Ad40E0805A091865",
                event_topic_or_type=self.UNISWAP_V3_POOL_CREATED_TOPIC,
                fee_tier_bps=25,
                support_status="SUPPORTED"
            )
        ]

        # 5. Arbitrum One (eip155:42161)
        self._protocols["eip155:42161"] = [
            ProtocolDescriptor(
                protocol_id="camelot_v2",
                name="Camelot DEX V2",
                protocol_type="amm_v2",
                chain_id="eip155:42161",
                factory_address="0x6EcCab422D763aC031210895C8ed7841892ab7c0",
                event_topic_or_type=self.UNISWAP_V2_PAIR_CREATED_TOPIC,
                fee_tier_bps=30,
                support_status="SUPPORTED"
            ),
            ProtocolDescriptor(
                protocol_id="uniswap_v3_arbitrum",
                name="Uniswap V3 (Arbitrum)",
                protocol_type="clmm_v3",
                chain_id="eip155:42161",
                factory_address="0x1F98431c8aD98523631AE4a59f267346ea31F984",
                event_topic_or_type=self.UNISWAP_V3_POOL_CREATED_TOPIC,
                fee_tier_bps=30,
                support_status="SUPPORTED"
            )
        ]

        # 6. OP Mainnet (eip155:10)
        self._protocols["eip155:10"] = [
            ProtocolDescriptor(
                protocol_id="velodrome_v2",
                name="Velodrome Finance V2",
                protocol_type="amm_v2",
                chain_id="eip155:10",
                factory_address="0xF1046053aa5682b4F9a81b5481394DA16BE5FF5a",
                event_topic_or_type=self.UNISWAP_V2_PAIR_CREATED_TOPIC,
                fee_tier_bps=30,
                support_status="SUPPORTED"
            ),
            ProtocolDescriptor(
                protocol_id="uniswap_v3_optimism",
                name="Uniswap V3 (Optimism)",
                protocol_type="clmm_v3",
                chain_id="eip155:10",
                factory_address="0x1F98431c8aD98523631AE4a59f267346ea31F984",
                event_topic_or_type=self.UNISWAP_V3_POOL_CREATED_TOPIC,
                fee_tier_bps=30,
                support_status="SUPPORTED"
            )
        ]

        # 7. Polygon PoS (eip155:137)
        self._protocols["eip155:137"] = [
            ProtocolDescriptor(
                protocol_id="quickswap_v2",
                name="QuickSwap V2",
                protocol_type="amm_v2",
                chain_id="eip155:137",
                factory_address="0x5757371414417b8C6CAad45bAeF941aBc7d3Ab32",
                event_topic_or_type=self.UNISWAP_V2_PAIR_CREATED_TOPIC,
                fee_tier_bps=30,
                support_status="SUPPORTED"
            ),
            ProtocolDescriptor(
                protocol_id="uniswap_v3_polygon",
                name="Uniswap V3 (Polygon)",
                protocol_type="clmm_v3",
                chain_id="eip155:137",
                factory_address="0x1F98431c8aD98523631AE4a59f267346ea31F984",
                event_topic_or_type=self.UNISWAP_V3_POOL_CREATED_TOPIC,
                fee_tier_bps=30,
                support_status="SUPPORTED"
            )
        ]

        # 8. Avalanche C-Chain (eip155:43114)
        self._protocols["eip155:43114"] = [
            ProtocolDescriptor(
                protocol_id="traderjoe_v1",
                name="Trader Joe V1",
                protocol_type="amm_v2",
                chain_id="eip155:43114",
                factory_address="0x9Ad6C38BE942060D00956147074350661A288167",
                event_topic_or_type=self.UNISWAP_V2_PAIR_CREATED_TOPIC,
                fee_tier_bps=30,
                support_status="SUPPORTED"
            ),
            ProtocolDescriptor(
                protocol_id="pangolin",
                name="Pangolin DEX",
                protocol_type="amm_v2",
                chain_id="eip155:43114",
                factory_address="0xefa94DE7a4656D787667C749f7E1223D71E9FD88",
                event_topic_or_type=self.UNISWAP_V2_PAIR_CREATED_TOPIC,
                fee_tier_bps=30,
                support_status="SUPPORTED"
            )
        ]

        # 9. Sui (sui:mainnet)
        self._protocols["sui:mainnet"] = [
            ProtocolDescriptor(
                protocol_id="cetus_clmm",
                name="Cetus CLMM",
                protocol_type="clmm_v3",
                chain_id="sui:mainnet",
                program_id="0x1eabed72c53feb3805120a081dc15963c204dc8d091542592abaf7a35689b2fb",
                event_topic_or_type="0x1eabed72c53feb3805120a081dc15963c204dc8d091542592abaf7a35689b2fb::factory::CreatePoolEvent",
                fee_tier_bps=25,
                support_status="SUPPORTED"
            ),
            ProtocolDescriptor(
                protocol_id="turbos_finance",
                name="Turbos Finance",
                protocol_type="clmm_v3",
                chain_id="sui:mainnet",
                program_id="0x91bfbc386a41afcfd9b2533058d7e915a1d3829089cc268ff4333d54d6339ca1",
                event_topic_or_type="0x91bfbc386a41afcfd9b2533058d7e915a1d3829089cc268ff4333d54d6339ca1::pool_factory::PoolCreatedEvent",
                fee_tier_bps=30,
                support_status="SUPPORTED"
            )
        ]

        # 10. Aptos (aptos:mainnet)
        self._protocols["aptos:mainnet"] = [
            ProtocolDescriptor(
                protocol_id="liquidswap",
                name="Liquidswap (Pontem)",
                protocol_type="amm_v2",
                chain_id="aptos:mainnet",
                program_id="0x190d44266241744264b964a37b8f09863167a12d3e70cda39376cfb4e3561e12",
                event_topic_or_type="0x190d44266241744264b964a37b8f09863167a12d3e70cda39376cfb4e3561e12::liquidity_pool::CreatePoolEvent",
                fee_tier_bps=30,
                support_status="SUPPORTED"
            )
        ]

        # 11. TON (ton:mainnet)
        self._protocols["ton:mainnet"] = [
            ProtocolDescriptor(
                protocol_id="dedust",
                name="DeDust DEX",
                protocol_type="amm_v2",
                chain_id="ton:mainnet",
                factory_address="EQB-ajMyi5-blko9VV0oxGXSnGDeAjm0MoQehCeUF5noO--9",
                event_topic_or_type="create_pool",
                fee_tier_bps=40,
                support_status="PARTIAL",
                notes="Direct Jetton pair factory monitoring"
            ),
            ProtocolDescriptor(
                protocol_id="ston_fi",
                name="STON.fi DEX",
                protocol_type="amm_v2",
                chain_id="ton:mainnet",
                factory_address="EQB3ncyBUTjZUA3FAPKDjAMvdJWPRZDyOiYeeArqbmwptWnB",
                event_topic_or_type="create_pool",
                fee_tier_bps=30,
                support_status="PARTIAL",
                notes="TON router pool creation"
            )
        ]

        # 12. TRON (tron:mainnet)
        self._protocols["tron:mainnet"] = [
            ProtocolDescriptor(
                protocol_id="sunswap_v2",
                name="SunSwap V2",
                protocol_type="amm_v2",
                chain_id="tron:mainnet",
                factory_address="416b23d9a04a3744959db6d8ef956973ba08154be0",  # TKWJdrQqeacjhipQnTmstX8jxQBbVNnBRj
                event_topic_or_type=self.UNISWAP_V2_PAIR_CREATED_TOPIC,
                fee_tier_bps=30,
                support_status="SUPPORTED",
                notes="Standard TRON TRC-20 SunSwap pair factory"
            )
        ]

    def list_protocols_for_chain(self, chain_id: str) -> List[ProtocolDescriptor]:
        return self._protocols.get(chain_id, [])

    def get_protocol(self, chain_id: str, protocol_id: str) -> Optional[ProtocolDescriptor]:
        for p in self.list_protocols_for_chain(chain_id):
            if p.protocol_id == protocol_id:
                return p
        return None

    def identify_evm_factory(self, chain_id: str, factory_address: str) -> Optional[ProtocolDescriptor]:
        clean_addr = factory_address.lower()
        for p in self.list_protocols_for_chain(chain_id):
            if p.factory_address and p.factory_address.lower() == clean_addr:
                return p
        return None

    def get_coverage_matrix(self) -> Dict[str, Any]:
        coverage = {}
        for chain_id, protocols in self._protocols.items():
            coverage[chain_id] = [
                {
                    "protocol_id": p.protocol_id,
                    "name": p.name,
                    "type": p.protocol_type,
                    "status": p.support_status,
                    "factory_or_program": p.factory_address or p.program_id
                }
                for p in protocols
            ]
        return coverage

protocol_registry = ProtocolRegistry()
