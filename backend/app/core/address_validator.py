import re
import base64
import hashlib
from typing import Tuple, Optional, Dict, Any
from app.services.execution_interfaces import keccak256, b58decode, b58encode

class AddressValidationError(ValueError):
    """Raised when an address fails chain-specific format or checksum validation."""
    pass

class AddressValidator:
    """
    Typed, chain-aware address validation and normalization for all 12 chains (P10.2-D).
    Guarantees deterministic checksum validation, length checks, and canonical formatting.
    """

    # =========================================================================
    # EVM Address Validation & EIP-55 Checksum
    # =========================================================================

    @staticmethod
    def to_eip55_checksum(address: str) -> str:
        """
        Computes EIP-55 mixed-case checksum for a 20-byte EVM address.
        """
        clean = address.lower()
        if clean.startswith("0x"):
            clean = clean[2:]
        if len(clean) != 40 or not all(c in "0123456789abcdef" for c in clean):
            raise AddressValidationError(f"Invalid EVM address hex length or characters: {address}")

        hash_hex = keccak256(clean.encode("ascii")).hex()
        checksummed = []
        for i, ch in enumerate(clean):
            if ch in "0123456789":
                checksummed.append(ch)
            else:
                # If i-th hex digit of hash is >= 8, uppercase the character
                if int(hash_hex[i], 16) >= 8:
                    checksummed.append(ch.upper())
                else:
                    checksummed.append(ch.lower())
        return "0x" + "".join(checksummed)

    @classmethod
    def validate_evm_address(cls, address: str, strict_checksum: bool = False) -> Tuple[bool, str, Optional[str]]:
        """
        Validates EVM address. Returns (is_valid, normalized_lowercase, checksummed_address).
        If strict_checksum is True and mixed case is present, validates that it matches EIP-55.
        """
        if not address or not isinstance(address, str):
            return False, "", "Empty address"

        clean = address.strip()
        if not clean.startswith("0x") and len(clean) == 40:
            clean = "0x" + clean

        if len(clean) != 42 or not clean.startswith("0x"):
            return False, "", f"Invalid EVM address length (expected 42 chars with 0x): {len(clean)}"

        raw_hex = clean[2:]
        if not all(c in "0123456789abcdefABCDEF" for c in raw_hex):
            return False, "", "Non-hexadecimal characters present in address"

        checksummed = cls.to_eip55_checksum(clean)
        normalized = clean.lower()

        # If address has mixed casing, check if checksum matches
        has_upper = any(c.isupper() for c in raw_hex)
        has_lower = any(c.islower() for c in raw_hex)
        if strict_checksum and has_upper and has_lower:
            if clean != checksummed:
                return False, normalized, "EIP-55 checksum mismatch"

        return True, normalized, checksummed

    # =========================================================================
    # Solana Address Validation
    # =========================================================================

    @staticmethod
    def validate_solana_address(address: str) -> Tuple[bool, str, Optional[str]]:
        """
        Validates Solana Base58 public key (32 bytes Ed25519).
        Length typically 32 to 44 characters.
        """
        if not address or not isinstance(address, str):
            return False, "", "Empty address"

        clean = address.strip()
        if len(clean) < 32 or len(clean) > 44:
            return False, "", f"Invalid Solana address length: {len(clean)}"

        try:
            raw_bytes = b58decode(clean)
            if len(raw_bytes) != 32:
                return False, "", f"Decoded Solana address is {len(raw_bytes)} bytes; expected exactly 32 bytes"
            return True, clean, clean
        except Exception as e:
            return False, "", f"Base58 decoding error: {str(e)}"

    # =========================================================================
    # Move Addresses (Sui & Aptos)
    # =========================================================================

    @staticmethod
    def validate_sui_address(address: str) -> Tuple[bool, str, Optional[str]]:
        """
        Validates Sui 32-byte hexadecimal address (0x prefixed, up to 64 hex chars).
        Normalizes to 0x followed by 64 lowercase hex characters (padded with leading zeros).
        """
        if not address or not isinstance(address, str):
            return False, "", "Empty address"

        clean = address.strip()
        if not clean.startswith("0x"):
            return False, "", "Sui address must begin with 0x prefix"

        hex_part = clean[2:]
        if len(hex_part) == 0 or len(hex_part) > 64:
            return False, "", f"Invalid Sui hex length: {len(hex_part)} (max 64)"

        if not all(c in "0123456789abcdefABCDEF" for c in hex_part):
            return False, "", "Non-hexadecimal characters in Sui address"

        canonical = "0x" + hex_part.lower().rjust(64, "0")
        return True, canonical, canonical

    @classmethod
    def validate_sui_move_type_tag(cls, type_tag: str) -> Tuple[bool, str, Optional[str]]:
        """
        Validates Sui Move type tag grammar (structs or vector<T>):
        Format: <package_address>::<module_name>::<struct_name>[<type_arg1, type_arg2, ...>]
        Format: vector<T> (where T is primitive, struct, or another vector)
        Example: 0x2::sui::SUI
        Example: 0x1eab::factory::Pool<0x2::sui::SUI, 0x5d4b::coin::COIN>
        Example: vector<u8>
        Example: vector<0x2::sui::SUI>
        Example: 0x2::coin::Coin<vector<u8>>
        Normalizes package address and any internal type argument addresses to 64-hex canonical format.
        """
        if not type_tag or not isinstance(type_tag, str):
            return False, "", "Empty Sui Move type tag"

        clean = type_tag.strip()
        move_primitives = {"u8", "u16", "u32", "u64", "u128", "u256", "bool", "address"}

        # Handle top-level vector<T>
        if clean.startswith("vector<"):
            if not clean.endswith(">"):
                return False, "", "Mismatched generic angle brackets in Sui Move vector tag"
            depth = 0
            closed_at = -1
            for i in range(6, len(clean)):
                if clean[i] == "<":
                    depth += 1
                elif clean[i] == ">":
                    depth -= 1
                    if depth == 0:
                        closed_at = i
                        break
                    elif depth < 0:
                        return False, "", "Mismatched closing bracket in Sui Move vector tag"
            if depth != 0 or closed_at != len(clean) - 1:
                return False, "", "Malformed Sui Move vector tag"

            inner_t = clean[7:-1].strip()
            if not inner_t:
                return False, "", "Empty type argument in Sui Move vector tag"

            if inner_t in move_primitives:
                canon_inner = inner_t
            else:
                ok_inner, canon_inner, err_inner = cls.validate_sui_move_type_tag(inner_t)
                if not ok_inner:
                    return False, "", f"Invalid vector type argument '{inner_t}': {err_inner}"

            canonical_tag = f"vector<{canon_inner}>"
            return True, canonical_tag, canonical_tag

        if not clean.startswith("0x") or "::" not in clean:
            return False, "", "Sui Move struct tag must begin with 0x package address and contain '::'"

        # Handle generics if present
        generics_part = None
        if "<" in clean:
            if not clean.endswith(">"):
                return False, "", "Mismatched generic angle brackets in Sui Move type tag"
            first_angle = clean.index("<")
            generics_part = clean[first_angle + 1:-1].strip()
            base_part = clean[:first_angle].strip()
        else:
            base_part = clean

        parts = base_part.split("::")
        if len(parts) != 3:
            return False, "", f"Sui Move struct tag must have exactly 3 components (package::module::struct), got {len(parts)}"

        pkg_addr, module_name, struct_name = parts[0], parts[1], parts[2]

        # Validate package address using strict account validator
        ok, canon_pkg, err = cls.validate_sui_address(pkg_addr)
        if not ok:
            return False, "", f"Invalid Sui package address in Move type tag: {err}"

        # Validate Move identifiers (alphanumeric + underscore, starts with alpha/underscore)
        ident_pattern = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
        if not ident_pattern.match(module_name):
            return False, "", f"Invalid Sui Move module identifier: '{module_name}'"
        if not ident_pattern.match(struct_name):
            return False, "", f"Invalid Sui Move struct identifier: '{struct_name}'"

        if generics_part:
            # Parse comma-separated arguments at top bracket level
            args = []
            curr = []
            depth = 0
            for char in generics_part:
                if char == "<":
                    depth += 1
                    curr.append(char)
                elif char == ">":
                    depth -= 1
                    if depth < 0:
                        return False, "", "Mismatched closing bracket in type arguments"
                    curr.append(char)
                elif char == "," and depth == 0:
                    arg_str = "".join(curr).strip()
                    if not arg_str:
                        return False, "", "Empty type argument in generics"
                    args.append(arg_str)
                    curr = []
                else:
                    curr.append(char)
            if depth != 0:
                return False, "", "Unclosed generic bracket in type arguments"
            last_arg = "".join(curr).strip()
            if not last_arg:
                return False, "", "Empty trailing type argument in generics"
            args.append(last_arg)

            canon_args = []
            for a in args:
                if a in move_primitives:
                    canon_args.append(a)
                elif a.startswith("vector<") or "::" in a:
                    ok_sub, canon_sub, err_sub = cls.validate_sui_move_type_tag(a)
                    if not ok_sub:
                        return False, "", f"Invalid generic type argument '{a}': {err_sub}"
                    canon_args.append(canon_sub)
                else:
                    return False, "", f"Unknown Move type argument: '{a}'"

            canonical_tag = f"{canon_pkg}::{module_name}::{struct_name}<{', '.join(canon_args)}>"
        else:
            canonical_tag = f"{canon_pkg}::{module_name}::{struct_name}"

        return True, canonical_tag, canonical_tag

    @staticmethod
    def validate_aptos_address(address: str) -> Tuple[bool, str, Optional[str]]:
        """
        Validates Aptos 32-byte hexadecimal address or Move type tag (0x...::module::struct).
        Normalizes account address to 0x followed by 64 lowercase hex characters.
        """
        if not address or not isinstance(address, str):
            return False, "", "Empty address"

        clean = address.strip()

        # If it's a Move struct type tag e.g. 0x1::coin::CoinInfo<0x1::aptos_coin::AptosCoin>
        if "::" in clean:
            parts = clean.split("::", 2)
            account_part = parts[0]
            ok, norm_acc, _ = AddressValidator.validate_aptos_address(account_part)
            if not ok:
                return False, "", f"Invalid Aptos module account address in type tag: {account_part}"
            normalized_tag = f"{norm_acc}::{parts[1]}"
            if len(parts) > 2:
                normalized_tag += f"::{parts[2]}"
            return True, normalized_tag, normalized_tag

        if not clean.startswith("0x"):
            return False, "", "Aptos address must begin with 0x prefix"

        hex_part = clean[2:]
        if len(hex_part) == 0 or len(hex_part) > 64:
            return False, "", f"Invalid Aptos hex length: {len(hex_part)} (max 64)"

        if not all(c in "0123456789abcdefABCDEF" for c in hex_part):
            return False, "", "Non-hexadecimal characters in Aptos address"

        canonical = "0x" + hex_part.lower().rjust(64, "0")
        return True, canonical, canonical

    # =========================================================================
    # TON Address Validation
    # =========================================================================

    @staticmethod
    def _crc16_ccitt(data: bytes) -> int:
        """Computes CRC16-XMODEM/CCITT checksum used by TON user-friendly addresses."""
        poly = 0x1021
        reg = 0
        for byte in data:
            mask = 0x80
            while mask > 0:
                reg <<= 1
                if byte & mask:
                    reg += 1
                mask >>= 1
                if reg > 0xffff:
                    reg &= 0xffff
                    reg ^= poly
        return reg

    @classmethod
    def validate_ton_address(cls, address: str) -> Tuple[bool, str, Optional[str]]:
        """
        Validates TON address:
        1. Raw format: <workchain>:<64 hex chars> (e.g. 0:1234... or -1:1234...)
        2. User-friendly format: Base64 / Base64url (48 chars, starts with E or U or k)
           Contains flags (1 byte), workchain (1 byte), 32-byte hash, and 2-byte CRC16 checksum.
        """
        if not address or not isinstance(address, str):
            return False, "", "Empty address"

        clean = address.strip()

        # Check raw format: workchain:hex
        if ":" in clean:
            parts = clean.split(":")
            if len(parts) != 2:
                return False, "", "Invalid TON raw address format"
            wc_str, hex_str = parts[0], parts[1]
            try:
                wc = int(wc_str)
                if wc not in (0, -1):
                    return False, "", f"Invalid TON workchain ID: {wc} (expected 0 or -1)"
            except ValueError:
                return False, "", f"Non-integer workchain ID: {wc_str}"

            if len(hex_str) != 64 or not all(c in "0123456789abcdefABCDEF" for c in hex_str):
                return False, "", f"Invalid TON raw hex length or characters: {len(hex_str)}"

            raw_canonical = f"{wc}:{hex_str.lower()}"
            return True, raw_canonical, clean

        # Check user-friendly format (Base64url or Base64, 48 characters)
        if len(clean) == 48:
            try:
                # Replace Base64url characters with standard Base64
                b64_std = clean.replace("-", "+").replace("_", "/")
                # Add padding if necessary
                padded = b64_std + "=" * ((4 - len(b64_std) % 4) % 4)
                decoded = base64.b64decode(padded)
                if len(decoded) != 36:
                    return False, "", f"Decoded TON user-friendly address is {len(decoded)} bytes (expected 36)"

                payload = decoded[:34]
                crc_provided = int.from_bytes(decoded[34:36], "big")
                crc_computed = cls._crc16_ccitt(payload)
                if crc_provided != crc_computed:
                    return False, "", f"TON user-friendly CRC16 checksum mismatch"

                wc_byte = payload[1]
                wc = -1 if wc_byte == 255 else wc_byte
                account_hex = payload[2:34].hex()
                raw_canonical = f"{wc}:{account_hex}"
                return True, raw_canonical, clean
            except Exception as e:
                return False, "", f"TON user-friendly address decode error: {str(e)}"

        return False, "", f"Unrecognized TON address format (length {len(clean)})"

    # =========================================================================
    # TRON Address Validation
    # =========================================================================

    @staticmethod
    def tron_base58_to_hex(address: str) -> str:
        """Converts TRON Base58Check address to 41-hex format."""
        decoded = b58decode(address)
        if len(decoded) != 25:
            raise AddressValidationError(f"Invalid TRON Base58Check decoded length: {len(decoded)}")
        payload = decoded[:21]
        checksum = decoded[21:25]
        expected_checksum = hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]
        if checksum != expected_checksum:
            raise AddressValidationError("TRON Base58Check checksum mismatch")
        if payload[0] != 0x41:
            raise AddressValidationError(f"Invalid TRON address prefix byte: {hex(payload[0])} (expected 0x41)")
        return "41" + payload[1:].hex()

    @staticmethod
    def tron_hex_to_base58(hex_addr: str) -> str:
        """Converts TRON 41-hex address to Base58Check format."""
        clean = hex_addr.lower().replace("0x", "")
        if len(clean) != 42 or not clean.startswith("41"):
            raise AddressValidationError(f"Invalid TRON 41-hex address: {hex_addr}")
        payload = bytes.fromhex(clean)
        checksum = hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]
        return b58encode(payload + checksum)

    @classmethod
    def validate_tron_address(cls, address: str) -> Tuple[bool, str, Optional[str]]:
        """
        Validates TRON address:
        1. Base58Check format: 34 characters, starts with 'T'.
        2. 41-hex format: 42 characters, starts with '41' (or 0x41).
        Returns (is_valid, base58check_address, hex41_address).
        """
        if not address or not isinstance(address, str):
            return False, "", "Empty address"

        clean = address.strip()

        # Check Base58Check (starts with 'T', length 34)
        if clean.startswith("T") and len(clean) == 34:
            try:
                hex41 = cls.tron_base58_to_hex(clean)
                return True, clean, hex41
            except Exception as e:
                return False, "", str(e)

        # Check 41-hex format
        clean_hex = clean.lower().replace("0x", "")
        if len(clean_hex) == 42 and clean_hex.startswith("41"):
            try:
                b58 = cls.tron_hex_to_base58(clean_hex)
                return True, b58, clean_hex
            except Exception as e:
                return False, "", str(e)

        return False, "", f"Invalid TRON address format (expected Base58Check starting with T or 41-hex)"

    # =========================================================================
    # Universal Multi-Chain Dispatcher
    # =========================================================================

    @classmethod
    def validate_address_for_chain(
        cls,
        chain_id: str,
        address: str,
        strict_checksum: bool = False
    ) -> Tuple[bool, str, Optional[str]]:
        """
        Validates and normalizes address according to chain protocol standards.
        Returns: (is_valid: bool, canonical_normalized_address: str, error_or_preview: Optional[str])
        """
        if chain_id.startswith("solana"):
            return cls.validate_solana_address(address)
        elif chain_id.startswith("eip155"):
            return cls.validate_evm_address(address, strict_checksum=strict_checksum)
        elif chain_id.startswith("sui"):
            if "::" in address or address.strip().startswith("vector<"):
                return cls.validate_sui_move_type_tag(address)
            return cls.validate_sui_address(address)
        elif chain_id.startswith("aptos"):
            return cls.validate_aptos_address(address)
        elif chain_id.startswith("ton"):
            return cls.validate_ton_address(address)
        elif chain_id.startswith("tron"):
            return cls.validate_tron_address(address)
        else:
            return False, "", f"Unsupported chain_id for address validation: {chain_id}"

    @classmethod
    def is_valid_format_for_any_chain(cls, address: str) -> bool:
        """Checks if the string matches the valid address format for at least one supported chain."""
        if not address or not isinstance(address, str):
            return False
        clean = address.strip()
        for chain_prefix in ("solana:mainnet", "eip155:1", "sui:mainnet", "aptos:mainnet", "ton:mainnet", "tron:mainnet"):
            is_valid, _, _ = cls.validate_address_for_chain(chain_prefix, clean)
            if is_valid:
                return True
        return False

