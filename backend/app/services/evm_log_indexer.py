from typing import Dict, Any, List, Optional
from decimal import Decimal

class EvmLogIndexer:
    """
    Parses DEX factory and pool events on EVM networks.
    Distinguishes AMM swaps from simple token transfers.
    """
    PAIR_CREATED_V2 = "0x0d3648bd0f6ba80134a33ba9275ac585d9d315f0ad8355cddefde31afa28d0e9"
    POOL_CREATED_V3 = "0x783cca1c0412dd0d695e784568c96da2e9c22ff989357a2e8b1d9b2b4e6b7118"
    SWAP_V2 = "0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822"
    SWAP_V3 = "0xc42079f94a6350d7e6235f29174924f9b50abce7768056977a438b8834e6154e"

    @classmethod
    def parse_log_event(cls, log: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        topics = log.get("topics", [])
        if not topics:
            return None

        event_sig = topics[0].lower()
        emitter = log.get("address", "").lower()
        tx_hash = log.get("transactionHash", "").lower()
        block_num = int(log.get("blockNumber", "0x0"), 16) if isinstance(log.get("blockNumber"), str) else log.get("blockNumber", 0)

        if event_sig == cls.PAIR_CREATED_V2 and len(topics) >= 3:
            token0 = "0x" + topics[1][-40:].lower()
            token1 = "0x" + topics[2][-40:].lower()
            data = log.get("data", "")
            pool_address = "0x" + data[26:66].lower() if len(data) >= 66 else ""
            return {
                "event_type": "PAIR_CREATED",
                "dex_version": "v2",
                "factory_address": emitter,
                "token0": token0,
                "token1": token1,
                "pool_address": pool_address,
                "tx_hash": tx_hash,
                "block_number": block_num
            }

        elif event_sig == cls.SWAP_V2:
            return {
                "event_type": "SWAP",
                "dex_version": "v2",
                "pool_address": emitter,
                "tx_hash": tx_hash,
                "block_number": block_num
            }

        elif event_sig == cls.SWAP_V3:
            return {
                "event_type": "SWAP",
                "dex_version": "v3",
                "pool_address": emitter,
                "tx_hash": tx_hash,
                "block_number": block_num
            }

        return None

evm_log_indexer = EvmLogIndexer()
