from typing import Dict, Any, List
from datetime import datetime, timezone
from decimal import Decimal
from app.adapters.base import BaseChainAdapter, ChainBlockHeader, RawTransactionData
from app.services.solana_rpc import solana_rpc_service

class SolanaChainAdapter(BaseChainAdapter):
    def __init__(self, chain_id: str = "solana:mainnet", rpc_urls: List[str] = None):
        super().__init__(chain_id, rpc_urls or ["https://api.mainnet-beta.solana.com"])

    async def get_latest_block(self) -> ChainBlockHeader:
        # Calls Solana getLatestBlockhash
        res = await solana_rpc_service._call_rpc("getLatestBlockhash", [{"commitment": "confirmed"}])
        block_hash = res.get("value", {}).get("blockhash", "0x0") if res else "0x0"
        slot = res.get("context", {}).get("slot", 0) if res else 0
        return ChainBlockHeader(
            chain_id=self.chain_id,
            block_number=slot,
            block_hash=block_hash,
            parent_hash="",
            block_time=datetime.now(timezone.utc)
        )

    async def get_token_metadata(self, asset_address: str) -> Dict[str, Any]:
        info = await solana_rpc_service.get_parsed_mint_info(asset_address)
        if not info:
            return {"decimals": None, "total_supply": None}
        return {
            "decimals": info.get("decimals"),
            "total_supply": info.get("supply"),
            "owner_program": info.get("owner_program")
        }

    async def inspect_token_security(self, asset_address: str) -> Dict[str, Any]:
        info = await solana_rpc_service.get_parsed_mint_info(asset_address)
        return {
            "chain_type": "solana",
            "mint_authority": info.get("mint_authority") if info else None,
            "freeze_authority": info.get("freeze_authority") if info else None,
            "is_token_2022": info.get("is_token_2022", False) if info else False,
            "extensions": info.get("extensions", []) if info else []
        }

    async def get_initial_buyer_transactions(
        self, pool_address: str, asset_address: str, limit: int = 25
    ) -> List[RawTransactionData]:
        sigs = await solana_rpc_service.get_signatures_for_address(pool_address, limit=limit)
        txs = []
        for s in sigs:
            txs.append(RawTransactionData(
                chain_id=self.chain_id,
                tx_hash=s.get("signature", ""),
                slot_or_block=s.get("slot", 0),
                block_time=datetime.now(timezone.utc),
                signer_address="",
                success=s.get("err") is None,
                fee_in_native=Decimal("0.000005"),
                raw_payload=s
            ))
        return txs
