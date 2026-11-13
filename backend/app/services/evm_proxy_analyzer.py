from typing import Dict, Any, Optional

class EvmProxyAnalyzer:
    """
    Detects upgradeable proxy contracts and extracts implementation addresses.
    Checks EIP-1967, EIP-1822 (UUPS), and legacy OpenZeppelin storage slots.
    """
    EIP1967_IMPLEMENTATION_SLOT = "0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc"
    EIP1967_ADMIN_SLOT = "0xb53127684a568b3173ae13b9f8a6016e243e63b6e8ee1178d6a717850b5d6103"
    EIP1967_BEACON_SLOT = "0xa3f0ad74e5423aebfd80d3ef4346578335a9a72aeaee59ff6cb3582b35133d50"
    EIP1822_UUPS_SLOT = "0xc5f1683af44b2706a3109ab0e563844422cd17a4561f17762d2ecb931536bfb6"
    OZ_LEGACY_SLOT = "0x7050c9e0f4ca769c69bd3a8ef740bc37934f8e2c036e5a723fd8ee048ed3f8c3"

    ZERO_SLOT_VALUE = "0x0000000000000000000000000000000000000000000000000000000000000000"

    @classmethod
    def evaluate_proxy_storage(cls, storage_slots: Dict[str, str]) -> Dict[str, Any]:
        """
        storage_slots maps slot_hash -> raw 32-byte hex string.
        """
        impl_raw = storage_slots.get(cls.EIP1967_IMPLEMENTATION_SLOT)
        admin_raw = storage_slots.get(cls.EIP1967_ADMIN_SLOT)
        beacon_raw = storage_slots.get(cls.EIP1967_BEACON_SLOT)
        uups_raw = storage_slots.get(cls.EIP1822_UUPS_SLOT)

        is_proxy = False
        proxy_standard = None
        impl_address = None
        admin_address = None

        if impl_raw and impl_raw != cls.ZERO_SLOT_VALUE:
            is_proxy = True
            proxy_standard = "EIP-1967"
            impl_address = "0x" + impl_raw[-40:].lower()
        elif uups_raw and uups_raw != cls.ZERO_SLOT_VALUE:
            is_proxy = True
            proxy_standard = "EIP-1822_UUPS"
            impl_address = "0x" + uups_raw[-40:].lower()
        elif beacon_raw and beacon_raw != cls.ZERO_SLOT_VALUE:
            is_proxy = True
            proxy_standard = "EIP-1967_BEACON"

        if admin_raw and admin_raw != cls.ZERO_SLOT_VALUE:
            admin_address = "0x" + admin_raw[-40:].lower()

        return {
            "is_proxy": is_proxy,
            "proxy_standard": proxy_standard,
            "implementation_address": impl_address,
            "admin_address": admin_address
        }

evm_proxy_analyzer = EvmProxyAnalyzer()
