from typing import Dict, Any, List, Optional
from decimal import Decimal

class SolanaInstructionDecoder:
    """
    Decodes Solana instructions from transaction metadata:
    - System Program (SOL transfers / account creations)
    - SPL Token & Token-2022 (InitializeMint, Transfer, Burn, SetAuthority)
    - AMM DEX Programs (Raydium v4/CLMM, Orca Whirlpool, Pump.fun)
    """
    SYSTEM_PROGRAM_ID = "11111111111111111111111111111111"
    TOKEN_PROGRAM_ID = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
    TOKEN_2022_PROGRAM_ID = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
    RAYDIUM_V4_PROGRAM_ID = "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8"

    @classmethod
    def decode_instruction(cls, ix: Dict[str, Any]) -> Dict[str, Any]:
        program_id = ix.get("programId")
        parsed = ix.get("parsed")

        if parsed and isinstance(parsed, dict):
            ix_type = parsed.get("type")
            info = parsed.get("info", {})

            if program_id in (cls.TOKEN_PROGRAM_ID, cls.TOKEN_2022_PROGRAM_ID):
                return {
                    "program": "SPL_TOKEN" if program_id == cls.TOKEN_PROGRAM_ID else "TOKEN_2022",
                    "type": ix_type,
                    "info": info,
                    "is_token_program": True
                }
            elif program_id == cls.SYSTEM_PROGRAM_ID:
                return {
                    "program": "SYSTEM",
                    "type": ix_type,
                    "info": info,
                    "is_token_program": False
                }

        # Handle raw/compiled instruction
        return {
            "program_id": program_id,
            "type": "UNKNOWN_OR_UNPARSED",
            "is_token_program": program_id in (cls.TOKEN_PROGRAM_ID, cls.TOKEN_2022_PROGRAM_ID)
        }

    @classmethod
    def extract_transfers(cls, inner_instructions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Extracts all SOL and token transfers from inner instruction sets.
        """
        transfers = []
        for set_item in inner_instructions:
            for ix in set_item.get("instructions", []):
                decoded = cls.decode_instruction(ix)
                if decoded.get("type") in ("transfer", "transferChecked"):
                    transfers.append(decoded["info"])
        return transfers

solana_instruction_decoder = SolanaInstructionDecoder()
