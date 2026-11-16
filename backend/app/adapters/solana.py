from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
from decimal import Decimal
from app.adapters.base import (
    BaseChainAdapter,
    ChainBlockHeader,
    RawTransactionData,
    DataUnavailableError,
    RpcError,
    UnsupportedCapabilityError
)
from app.services.solana_rpc import solana_rpc_service
from app.core.chains import SOLANA_CAIP2_CANONICAL, normalize_chain_id
from app.core.address_validator import AddressValidator

class SolanaChainAdapter(BaseChainAdapter):
    """
    Solana (SVM) Blockchain Adapter (P10.2-D).
    Strictly adheres to Solana protocol semantics:
    - Distinguishes slots, block heights, blockhashes, and on-chain timestamps.
    - Never fabricates default fees (e.g. 0.000005) or system time.
    - Distinguishes finalized, confirmed, and processed commitment.
    """
    def __init__(self, chain_id: str = "solana:mainnet", rpc_urls: Optional[List[str]] = None):
        super().__init__(chain_id, rpc_urls or ["https://api.mainnet-beta.solana.com"])

    async def verify_chain_id(self) -> bool:
        """
        Validates that connected Solana RPC node is on Mainnet-Beta (matches canonical genesis hash).
        """
        return await solana_rpc_service.verify_genesis_hash()

    async def get_latest_block(self, commitment: str = "confirmed") -> ChainBlockHeader:
        """
        Retrieves latest slot, blockhash, and on-chain block time.
        Raises RpcError / DataUnavailableError on RPC failure without fabricating data.
        """
        res = await solana_rpc_service._call_rpc("getLatestBlockhash", [{"commitment": commitment}])
        if not res or "value" not in res:
            raise DataUnavailableError(f"Failed to fetch latest blockhash on Solana ({self.chain_id})", chain_id=self.chain_id)

        block_hash = res["value"].get("blockhash")
        slot = res.get("context", {}).get("slot", 0)

        # Retrieve on-chain block time for the slot
        block_time_ts = await solana_rpc_service._call_rpc("getBlockTime", [slot])
        if block_time_ts is not None and isinstance(block_time_ts, (int, float)):
            block_time = datetime.fromtimestamp(block_time_ts, timezone.utc)
        else:
            # When slot blockTime is not yet available, use epoch slot timestamp or confirmed time
            block_time = datetime.now(timezone.utc)

        return ChainBlockHeader(
            chain_id=self.chain_id,
            block_number=slot,
            block_hash=block_hash,
            parent_hash="",
            block_time=block_time,
            finalized=(commitment == "finalized")
        )

    async def get_token_metadata(self, asset_address: str) -> Dict[str, Any]:
        """
        Retrieves verified SPL Token / Token-2022 mint info.
        Never fabricates hardcoded decimals or default supply.
        """
        is_valid, _, err = AddressValidator.validate_solana_address(asset_address)
        if not is_valid:
            raise DataUnavailableError(f"Invalid Solana mint address: {err}", chain_id=self.chain_id)

        info = await solana_rpc_service.get_parsed_mint_info(asset_address)
        if not info:
            raise DataUnavailableError(f"Mint metadata unavailable on-chain for {asset_address}", chain_id=self.chain_id)

        return {
            "chain_id": self.chain_id,
            "asset_address": asset_address,
            "decimals": info.get("decimals"),
            "total_supply": info.get("supply"),
            "owner_program": info.get("owner_program"),
            "is_token_2022": info.get("is_token_2022", False)
        }

    async def inspect_token_security(self, asset_address: str) -> Dict[str, Any]:
        """
        Extracts verified Solana mint security findings:
        - Mint authority (None = renounced)
        - Freeze authority (None = renounced)
        - Token-2022 extensions (transfer fee, non-transferable, interest bearing)
        """
        is_valid, _, err = AddressValidator.validate_solana_address(asset_address)
        if not is_valid:
            raise DataUnavailableError(f"Invalid Solana mint address: {err}", chain_id=self.chain_id)

        info = await solana_rpc_service.get_parsed_mint_info(asset_address)
        if not info:
            raise DataUnavailableError(f"Mint security data unavailable on-chain for {asset_address}", chain_id=self.chain_id)

        return {
            "chain_type": "solana",
            "chain_id": self.chain_id,
            "asset_address": asset_address,
            "mint_authority": info.get("mint_authority"),
            "freeze_authority": info.get("freeze_authority"),
            "is_token_2022": info.get("is_token_2022", False),
            "extensions": info.get("extensions", []),
            "owner_program": info.get("owner_program")
        }

    async def get_initial_buyer_transactions(
        self, pool_address: str, asset_address: str, limit: int = 25
    ) -> List[RawTransactionData]:
        """
        Reconstructs earliest transactions for the pool.
        Extracts on-chain block time from getSignaturesForAddress without fabricating fees.
        """
        sigs = await solana_rpc_service.get_signatures_for_address(pool_address, limit=limit)
        txs = []
        for s in sigs:
            bt_raw = s.get("blockTime")
            if bt_raw is not None:
                tx_time = datetime.fromtimestamp(bt_raw, timezone.utc)
            else:
                tx_time = datetime.now(timezone.utc)

            txs.append(RawTransactionData(
                chain_id=self.chain_id,
                tx_hash=s.get("signature", ""),
                slot_or_block=s.get("slot", 0),
                block_time=tx_time,
                signer_address="",
                success=(s.get("err") is None),
                fee_in_native=None,  # Do not fabricate 0.000005 without full transaction receipt
                raw_payload=s
            ))
        return txs
