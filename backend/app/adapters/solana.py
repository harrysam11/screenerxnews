from typing import Dict, Any, List
from datetime import datetime, timezone
from decimal import Decimal
from app.adapters.base import (
    BaseChainAdapter, ChainBlockHeader, RawTransactionData,
    ChainCapabilities, FinalityModel, RpcFailureException
)
from app.services.solana_rpc import solana_rpc_service

class SolanaChainAdapter(BaseChainAdapter):
    def __init__(self, chain_id: str = "solana:mainnet", rpc_urls: List[str] = None):
        super().__init__(chain_id, rpc_urls or ["https://api.mainnet-beta.solana.com"])

    def get_capabilities(self) -> ChainCapabilities:
        return ChainCapabilities(
            chain_id=self.chain_id,
            name="Solana Mainnet",
            group_tier="A",
            network_type="solana",
            native_token="SOL",
            native_decimals=9,
            token_standards=["SPL", "Token-2022"],
            supports_token_2022=True,
            supports_l2_fees=False,
            supports_reorg_finality=True,
            finality_model=FinalityModel.PROBABILISTIC_POS,
            required_confirmations=32,
            has_smart_contract_bytecode=False,
            supports_events_log_bloom=False,
            supports_simulation=True
        )

    async def get_latest_block(self) -> ChainBlockHeader:
        # Calls Solana getLatestBlockhash
        res = await solana_rpc_service._call_rpc("getLatestBlockhash", [{"commitment": "confirmed"}])
        if not res:
            raise RpcFailureException(self.chain_id, "Solana getLatestBlockhash returned empty or failed response")

        block_hash = res.get("value", {}).get("blockhash")
        slot = res.get("context", {}).get("slot", 0)
        if not block_hash:
            raise RpcFailureException(self.chain_id, "Missing blockhash in Solana RPC response")

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
            raise RpcFailureException(self.chain_id, f"Failed to retrieve mint account data for {asset_address}")
        return {
            "decimals": info.get("decimals"),
            "total_supply": info.get("supply"),
            "owner_program": info.get("owner_program"),
            "is_verified": True
        }

    async def inspect_token_security(self, asset_address: str) -> Dict[str, Any]:
        info = await solana_rpc_service.get_parsed_mint_info(asset_address)
        if not info:
            return {
                "chain_type": "solana",
                "scan_status": "RPC_FAILED",
                "is_verified": False,
                "confidence": "INSUFFICIENT",
                "mint_authority": None,
                "freeze_authority": None,
                "is_token_2022": False,
                "extensions": []
            }
        return {
            "chain_type": "solana",
            "scan_status": "VERIFIED",
            "is_verified": True,
            "confidence": "VERIFIED",
            "mint_authority": info.get("mint_authority"),
            "freeze_authority": info.get("freeze_authority"),
            "is_token_2022": info.get("is_token_2022", False),
            "extensions": info.get("extensions", [])
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
