from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

class EvmBlockIndexer:
    """
    Ingests blocks, tracking parent hashes and detecting chain reorganizations.
    """
    def __init__(self):
        # In-memory chain block tracking per chain: {chain_id: {block_number: block_dict}}
        self.block_trees: Dict[str, Dict[int, Dict[str, Any]]] = {}

    def process_block(
        self,
        chain_id: str,
        block_number: int,
        block_hash: str,
        parent_hash: str,
        block_time: datetime
    ) -> Dict[str, Any]:
        chain_tree = self.block_trees.setdefault(chain_id, {})
        is_reorg = False
        reorg_details = None

        prev_block = chain_tree.get(block_number - 1)
        if prev_block:
            if prev_block["block_hash"] != parent_hash:
                is_reorg = True
                reorg_details = {
                    "chain_id": chain_id,
                    "fork_block": block_number - 1,
                    "expected_parent": prev_block["block_hash"],
                    "actual_parent": parent_hash
                }

        block_record = {
            "chain_id": chain_id,
            "block_number": block_number,
            "block_hash": block_hash.lower(),
            "parent_hash": parent_hash.lower(),
            "block_time": block_time,
            "is_canonical": not is_reorg
        }
        chain_tree[block_number] = block_record

        return {
            "block": block_record,
            "is_reorg_detected": is_reorg,
            "reorg_details": reorg_details
        }

evm_block_indexer = EvmBlockIndexer()
