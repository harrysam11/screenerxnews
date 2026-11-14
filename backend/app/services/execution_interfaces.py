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

# =====================================================================
# Cryptographic Primitives: Keccak-256 and Secp256k1 (Zero Dependencies)
# =====================================================================

def keccak256(data: bytes) -> bytes:
    """Pure-Python Keccak-256 hash algorithm (Ethereum standard)."""
    RC = [
        0x0000000000000001, 0x0000000000008082, 0x800000000000808A, 0x8000000080008000,
        0x000000000000808B, 0x0000000080000001, 0x8000000080008081, 0x8000000000008009,
        0x000000000000008A, 0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
        0x000000008000808B, 0x800000000000008B, 0x8000000000008089, 0x8000000000008003,
        0x8000000000008002, 0x8000000000000080, 0x000000000000800A, 0x800000008000000A,
        0x8000000080008081, 0x8000000000008080, 0x0000000080000001, 0x8000000080008008
    ]
    ROTC = [
        [0, 36, 3, 41, 18], [1, 44, 10, 45, 2], [62, 6, 43, 15, 61],
        [28, 55, 25, 21, 56], [27, 20, 39, 8, 14]
    ]
    rate = 136
    state = [[0]*5 for _ in range(5)]
    padded = bytearray(data)
    padded.append(0x01)
    while len(padded) % rate != (rate - 1):
        padded.append(0x00)
    padded.append(0x80)

    for block_start in range(0, len(padded), rate):
        block = padded[block_start:block_start+rate]
        for i in range(17):
            state[i % 5][i // 5] ^= int.from_bytes(block[i*8:(i+1)*8], 'little')
        for round_idx in range(24):
            C = [state[x][0] ^ state[x][1] ^ state[x][2] ^ state[x][3] ^ state[x][4] for x in range(5)]
            D = [C[(x-1)%5] ^ (((C[(x+1)%5] << 1) & 0xffffffffffffffff) | (C[(x+1)%5] >> 63)) for x in range(5)]
            for x in range(5):
                for y in range(5):
                    state[x][y] ^= D[x]
            B = [[0]*5 for _ in range(5)]
            for x in range(5):
                for y in range(5):
                    r = ROTC[x][y]
                    B[y][(2*x + 3*y)%5] = ((state[x][y] << r) & 0xffffffffffffffff) | (state[x][y] >> (64 - r) if r else 0)
            for x in range(5):
                for y in range(5):
                    state[x][y] = B[x][y] ^ ((~B[(x+1)%5][y]) & B[(x+2)%5][y]) & 0xffffffffffffffff
            state[0][0] ^= RC[round_idx]

    out = bytearray()
    for i in range(4):
        out.extend(state[i % 5][i // 5].to_bytes(8, 'little'))
    return bytes(out)

# Secp256k1 Curve Parameters
_SECP_P = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC2F
_SECP_A = 0
_SECP_B = 7
_SECP_GX = 0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798
_SECP_GY = 0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8
_SECP_N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141

def _inv(k: int, m: int) -> int:
    return pow(k, m - 2, m)

def _point_add(p1: Optional[tuple], p2: Optional[tuple]) -> Optional[tuple]:
    if p1 is None: return p2
    if p2 is None: return p1
    x1, y1 = p1
    x2, y2 = p2
    if x1 == x2:
        if (y1 + y2) % _SECP_P == 0:
            return None
        lam = (3 * x1 * x1 + _SECP_A) * _inv(2 * y1, _SECP_P) % _SECP_P
    else:
        lam = (y2 - y1) * _inv(x2 - x1, _SECP_P) % _SECP_P
    x3 = (lam * lam - x1 - x2) % _SECP_P
    y3 = (lam * (x1 - x3) - y1) % _SECP_P
    return (x3, y3)

def _point_mul(k: int, p: Optional[tuple]) -> Optional[tuple]:
    res = None
    cur = p
    while k > 0:
        if k & 1:
            res = _point_add(res, cur)
        cur = _point_add(cur, cur)
        k >>= 1
    return res

_SECP_G = (_SECP_GX, _SECP_GY)

def ec_recover_evm_address(msghash: bytes, r: int, s: int, v: int) -> Optional[str]:
    """
    Recovers Ethereum 0x address from (r, s, v) and message hash according to SEC 1 and EIP-2.
    - Strict EIP-2 low-s malleability protection (s <= N // 2).
    - Full recovery ID support (recid 0, 1, 2, 3) including x = r + n coordinate.
    """
    if not (1 <= r < _SECP_N):
        return None
    # EIP-2 low-s malleability constraint
    if not (1 <= s <= _SECP_N // 2):
        return None

    # Normalize recovery ID (v): 27/28 (standard), 0/1 (raw), or EIP-155 (v >= 35)
    if v >= 35:
        recid = (v - 35) % 2
    elif v >= 27:
        recid = v - 27
    else:
        recid = v

    if recid not in (0, 1, 2, 3):
        return None

    # Handle the second possible recovery-point x-coordinate: r + n
    x = r + _SECP_N if (recid & 2) != 0 else r
    if x >= _SECP_P:
        return None

    y_sq = (pow(x, 3, _SECP_P) + _SECP_B) % _SECP_P
    y = pow(y_sq, (_SECP_P + 1) // 4, _SECP_P)
    # Cryptographic check: Ensure y_sq is indeed a quadratic residue modulo P
    if (y * y) % _SECP_P != y_sq:
        return None

    if (y % 2) != (recid & 1):
        y = _SECP_P - y

    R = (x, y)
    sR = _point_mul(s, R)
    z = int.from_bytes(msghash, 'big')
    zG = _point_mul(z, _SECP_G)
    if zG is None or sR is None:
        return None
    neg_zG = (zG[0], (_SECP_P - zG[1]) % _SECP_P)
    diff = _point_add(sR, neg_zG)
    if diff is None:
        return None
    r_inv = _inv(r, _SECP_N)
    pub = _point_mul(r_inv, diff)
    if pub is None:
        return None

    # Curve point validation & subgroup order requirement (n * Q = O)
    px, py = pub
    if not (0 <= px < _SECP_P and 0 <= py < _SECP_P):
        return None
    if (py * py - (pow(px, 3, _SECP_P) + _SECP_B)) % _SECP_P != 0:
        return None
    if _point_mul(_SECP_N, pub) is not None:
        return None

    pub_bytes = px.to_bytes(32, 'big') + py.to_bytes(32, 'big')
    addr_bytes = keccak256(pub_bytes)[-20:]
    return '0x' + addr_bytes.hex()


class ChainSignatureVerifier(SignatureVerifier):
    """
    Cryptographic signature verification for Solana (Ed25519) and EVM (Secp256k1).
    Validates cryptographic authenticity against single, unambiguous specifications:
    - Solana: Ed25519 signature over exact canonical message bytes.
    - EVM: EIP-191 Personal Sign prefix and Keccak-256 digest over exact canonical message bytes.
    """
    def verify(self, chain_id: str, signer_address: str, message_bytes: bytes, signature_bytes: bytes) -> bool:
        if not signer_address or not message_bytes or not signature_bytes:
            return False

        if chain_id.startswith("solana:") or chain_id == "solana:mainnet":
            try:
                import nacl.signing
                import nacl.exceptions
                if len(signature_bytes) != 64:
                    return False
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
                if len(signature_bytes) != 65:
                    return False
                r = int.from_bytes(signature_bytes[0:32], 'big')
                s = int.from_bytes(signature_bytes[32:64], 'big')
                v = signature_bytes[64]

                # Explicit, unambiguous signing protocol: EIP-191 Personal Sign
                eip191_prefix = f"\x19Ethereum Signed Message:\n{len(message_bytes)}".encode('utf-8')
                msghash = keccak256(eip191_prefix + message_bytes)

                recovered = ec_recover_evm_address(msghash, r, s, v)
                target_addr = signer_address.lower().strip()
                return recovered is not None and recovered.lower() == target_addr
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
