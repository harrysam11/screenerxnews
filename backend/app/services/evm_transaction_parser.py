from typing import Dict, Any, List, Optional
from decimal import Decimal
from datetime import datetime, timezone

class EvmTransactionParser:
    """
    Parses EVM transaction receipts and decodes ERC-20 log transfers.
    """
    ERC20_TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
    ERC20_APPROVAL_TOPIC = "0x8c5be1e5ebec7d5bd14f71427d1e84f3dd0314c0f7b2291e5b200ac8c7c3b925"

    @classmethod
    def parse_receipt(cls, chain_id: str, tx_receipt: Dict[str, Any]) -> Dict[str, Any]:
        tx_hash = tx_receipt.get("transactionHash", "").lower()
        block_number = int(tx_receipt.get("blockNumber", "0x0"), 16)
        from_address = tx_receipt.get("from", "").lower()
        to_address = tx_receipt.get("to", "").lower() if tx_receipt.get("to") else None
        contract_address = tx_receipt.get("contractAddress", "").lower() if tx_receipt.get("contractAddress") else None

        status_raw = tx_receipt.get("status", "0x0")
        is_success = (status_raw == "0x1")

        gas_used = int(tx_receipt.get("gasUsed", "0x0"), 16)
        effective_gas_price = int(tx_receipt.get("effectiveGasPrice", "0x0"), 16)
        fee_native = Decimal(str(gas_used * effective_gas_price)) / Decimal("1000000000000000000")

        # Parse logs
        logs = tx_receipt.get("logs", [])
        transfers = []
        for log in logs:
            topics = log.get("topics", [])
            if topics and topics[0] == cls.ERC20_TRANSFER_TOPIC and len(topics) >= 3:
                emitter = log.get("address", "").lower()
                sender = "0x" + topics[1][-40:].lower()
                recipient = "0x" + topics[2][-40:].lower()
                raw_data = log.get("data", "0x0")
                amount = int(raw_data, 16) if raw_data != "0x" else 0
                transfers.append({
                    "token_address": emitter,
                    "from": sender,
                    "to": recipient,
                    "amount_raw": amount,
                    "log_index": int(log.get("logIndex", "0x0"), 16)
                })

        return {
            "chain_id": chain_id,
            "tx_hash": tx_hash,
            "block_number": block_number,
            "from_address": from_address,
            "to_address": to_address,
            "contract_address": contract_address,
            "is_success": is_success,
            "fee_native": fee_native,
            "transfers": transfers
        }

evm_transaction_parser = EvmTransactionParser()
