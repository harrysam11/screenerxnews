from typing import Dict, List, Optional, Any

class EvmPoolDiscovery:
    """
    Extensible DEX Factory Registry for EVM networks.
    """
    FACTORIES: Dict[str, Dict[str, str]] = {
        "eip155:1": {
            "0x5c69bee701ef814a2b6a3edd4b1652cb9cc5aa6f": "uniswap_v2",
            "0x1f98431ca8ad98523631ae4a59f267346ea31f984": "uniswap_v3",
            "0xc0aee478e3658e2610c5f7a4a2e1777ce9e4f2ac": "sushiswap_v2"
        },
        "eip155:8453": {
            "0x33128a8fc17869897dce68ed026d694621f6fdfd": "uniswap_v3",
            "0x420dd381b31aef6683db6b902084cb0ffece40da": "aerodrome_v2"
        },
        "eip155:56": {
            "0xca143ce32fe78f1f7019d7d551a6402fc5350c73": "pancakeswap_v2",
            "0x0bfbcf9fa4f9c56b0f40a671ad40e0805a091865": "pancakeswap_v3"
        }
    }

    @classmethod
    def identify_factory(cls, chain_id: str, factory_address: str) -> Optional[str]:
        chain_factories = cls.FACTORIES.get(chain_id, {})
        return chain_factories.get(factory_address.lower())

    @classmethod
    def is_known_factory(cls, chain_id: str, address: str) -> bool:
        return cls.identify_factory(chain_id, address) is not None

evm_pool_discovery = EvmPoolDiscovery()
