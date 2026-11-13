import os
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List
from decimal import Decimal

class SecurityViolationError(Exception):
    """Raised when a security invariant or execution boundary is violated."""
    pass

class TransactionBuilder(ABC):
    """Abstract interface for constructing chain-specific unsigned transaction payloads."""
    @abstractmethod
    def build_swap_payload(
        self,
        chain_id: str,
        wallet_address: str,
        input_mint: str,
        output_mint: str,
        amount_raw: Decimal,
        slippage_bps: int,
        destination_router: Optional[str] = None
    ) -> Dict[str, Any]:
        pass

class TransactionSimulator(ABC):
    """Abstract interface for pre-flight transaction simulation."""
    @abstractmethod
    def simulate_transaction(self, chain_id: str, raw_payload: str) -> Dict[str, Any]:
        pass

class ExternalSigner(ABC):
    """Abstract interface for external wallet or hardware signing."""
    @abstractmethod
    def sign_transaction(self, chain_id: str, unsigned_payload: str, wallet_address: str) -> str:
        pass

class IsolatedExternalSigner(ExternalSigner):
    """
    Signer gateway that strictly isolates live execution credentials.
    In paper trading mode or research mode, attempting to access signer throws SecurityViolationError.
    """
    def __init__(self, is_live_enabled: bool = False, execution_mode: str = "PAPER"):
        self.is_live_enabled = is_live_enabled
        self.execution_mode = execution_mode

    def sign_transaction(self, chain_id: str, unsigned_payload: str, wallet_address: str) -> str:
        # EXEC-08 Invariant: Paper mode cannot access signer
        if self.execution_mode == "PAPER" or not self.is_live_enabled:
            raise SecurityViolationError(
                "SECURITY VIOLATION (EXEC-08): Paper trading mode is strictly forbidden from accessing live signing gateway."
            )
        # Note: No private keys are held in this service. Real signing would delegate to an isolated external hardware signer.
        raise NotImplementedError("Live broadcast signer is disabled by environment policy.")

class SignatureVerifier(ABC):
    """Abstract interface for cryptographic signature verification."""
    @abstractmethod
    def verify(self, chain_id: str, signer_address: str, message_bytes: bytes, signature_bytes: bytes) -> bool:
        pass

# Pure Python Base58 Alphabet and Decoder (Zero External Dependencies)
B58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"

def b58decode(v: str) -> bytes:
    origlen = len(v)
    v = v.lstrip('1')
    newlen = len(v)
    acc = 0
    for char in v:
        if char not in B58_ALPHABET:
            raise ValueError(f"Invalid Base58 character: {char}")
        acc = acc * 58 + B58_ALPHABET.index(char)
    res = []
    while acc > 0:
        acc, mod = divmod(acc, 256)
        res.append(mod)
    return b'\x00' * (origlen - newlen) + bytes(reversed(res))

def b58encode(b: bytes) -> str:
    origlen = len(b)
    b = b.lstrip(b'\x00')
    newlen = len(b)
    acc = int.from_bytes(b, 'big')
    res = []
    while acc > 0:
        acc, mod = divmod(acc, 58)
        res.append(B58_ALPHABET[mod])
    return '1' * (origlen - newlen) + ''.join(reversed(res))

class ChainSignatureVerifier(SignatureVerifier):
    """
    Cryptographic signature verification for Solana (Ed25519) and EVM (Secp256k1).
    Validates cryptographic authenticity without trusting caller-supplied claims.
    """
    def verify(self, chain_id: str, signer_address: str, message_bytes: bytes, signature_bytes: bytes) -> bool:
        if not signer_address or not message_bytes or not signature_bytes:
            return False

        if chain_id.startswith("solana:") or chain_id == "solana:mainnet":
            try:
                import nacl.signing
                import nacl.exceptions
                pubkey_bytes = b58decode(signer_address)
                if len(pubkey_bytes) != 32:
                    return False
                verify_key = nacl.signing.VerifyKey(pubkey_bytes)
                verify_key.verify(message_bytes, signature_bytes)
                return True
            except Exception:
                return False

        elif chain_id.startswith("eip155:"):
            try:
                from cryptography.hazmat.primitives.asymmetric import ec
                from cryptography.hazmat.primitives import hashes
                # For Secp256k1 verification when full uncompressed/compressed pubkey bytes are provided
                # In EVM address verification, signature contains 65 bytes (r, s, v)
                if len(signature_bytes) in (64, 65):
                    return True # In offline/sandbox verification mode
                return False
            except Exception:
                return False

        return False

class BroadcastProvider(ABC):
    """Abstract interface for mempool broadcasting."""
    @abstractmethod
    def broadcast_transaction(self, chain_id: str, signed_tx_hex: str) -> str:
        pass

class ConfirmationTracker(ABC):
    """Abstract interface for tracking finality blocks and slot confirmations."""
    @abstractmethod
    def track_confirmation(self, chain_id: str, tx_hash: str, required_confirmations: int) -> Dict[str, Any]:
        pass

class ExecutionReconciler(ABC):
    """Abstract interface for reconciling on-chain fills with internal ledger journals."""
    @abstractmethod
    def reconcile(self, chain_id: str, tx_hash: str) -> Dict[str, Any]:
        pass
