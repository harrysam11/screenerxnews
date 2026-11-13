from typing import Dict, Any, List, Optional
from decimal import Decimal
from datetime import datetime, timezone
from app.services.solana_instruction_decoder import solana_instruction_decoder

class SolanaTransactionParser:
    """
    Parses full Solana transaction receipts into balance deltas and swap actions.
    Preserves raw integer amounts and lamports.
    """
    PARSER_VERSION = "v2_solana_parser"

    @classmethod
    def parse_transaction(cls, tx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        meta = tx.get("meta")
        transaction = tx.get("transaction", {})
        if not meta or not transaction:
            return None

        slot = tx.get("slot", 0)
        block_time = tx.get("blockTime")
        dt = datetime.fromtimestamp(block_time, timezone.utc) if block_time else datetime.now(timezone.utc)

        signatures = transaction.get("signatures", [])
        signature = signatures[0] if signatures else ""
        fee_lamports = meta.get("fee", 5000)
        err = meta.get("err")
        is_success = (err is None)

        # Extract account keys
        account_keys = transaction.get("message", {}).get("accountKeys", [])
        signer = account_keys[0] if account_keys else ""
        if isinstance(signer, dict):
            signer = signer.get("pubkey", "")

        # Extract pre and post balances
        pre_balances = meta.get("preBalances", [])
        post_balances = meta.get("postBalances", [])
        pre_token_balances = meta.get("preTokenBalances", [])
        post_token_balances = meta.get("postTokenBalances", [])

        # Parse instructions
        inner_ix = meta.get("innerInstructions", [])
        transfers = solana_instruction_decoder.extract_transfers(inner_ix)

        return {
            "signature": signature,
            "slot": slot,
            "block_time": dt,
            "signer": signer,
            "fee_lamports": fee_lamports,
            "is_success": is_success,
            "error": str(err) if err else None,
            "pre_balances": pre_balances,
            "post_balances": post_balances,
            "pre_token_balances": pre_token_balances,
            "post_token_balances": post_token_balances,
            "transfers": transfers,
            "parser_version": cls.PARSER_VERSION
        }

solana_transaction_parser = SolanaTransactionParser()
