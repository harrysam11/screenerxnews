# P10.2-I: Canonical Chain Identity & Multi-Chain Research Documentation

## 1. Overview & Architecture

Phase P10.2-I establishes definitive canonical chain identity across the entire system, resolves legacy test fixtures, normalizes Solana to its true CAIP-2 identifier (`solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp`), and introduces local, multi-chain search and read-only research utilities.

### Operating Boundary
- **`ALLOW_LIVE_TRADING=False`**: Strictly enforced across all environments.
- **Standalone Core**: Independent source of truth without cloud or vendor dependencies.
- **Windmill PRIMARY**: Workflow orchestration for scheduled sync and batch jobs.
- **Hermes OPTIONAL**: Read-only message-bus adapter.
- **Read-Only Reference Integrations**: Trojan and Banana Gun adapters are strictly link-out and metadata reference only. They contain zero order placement, signing, broadcast, or key-handling capabilities.

---

## 2. Canonical Chain Identity & CAIP-2 / CAIP-19 Rules

### Supported Chains (All 12 Blockchains)
All chains are represented canonically by their CAIP-2 network identifiers:

| Chain Name | Namespace | Reference | Canonical CAIP-2 Identifier | Compatibility Aliases |
| :--- | :--- | :--- | :--- | :--- |
| Solana | `solana` | `5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp` | `solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp` | `solana:mainnet`, `solana`, `solana:mainnet-beta` |
| Ethereum Mainnet | `eip155` | `1` | `eip155:1` | `eth`, `ethereum` |
| Base | `eip155` | `8453` | `eip155:8453` | `base` |
| BNB Smart Chain | `eip155` | `56` | `eip155:56` | `bsc`, `binance` |
| Arbitrum One | `eip155` | `42161` | `eip155:42161` | `arbitrum`, `arb` |
| OP Mainnet | `eip155` | `10` | `eip155:10` | `optimism`, `op` |
| Polygon PoS | `eip155` | `137` | `eip155:137` | `polygon`, `poly` |
| Avalanche C-Chain | `eip155` | `43114` | `eip155:43114` | `avalanche`, `avax` |
| Sui Mainnet | `sui` | `mainnet` | `sui:mainnet` | `sui` |
| Aptos Mainnet | `aptos` | `mainnet` | `aptos:mainnet` | `aptos`, `aptos:1` |
| The Open Network | `ton` | `mainnet` | `ton:mainnet` | `ton`, `ton:-239` |
| TRON Mainnet | `tron` | `mainnet` | `tron:mainnet` | `tron`, `tron:0x2b6653dc` |

### CAIP-19 Asset Identifiers
Asset identifiers conform strictly to CAIP-19 format:
`{chain_id}/{asset_namespace}:{asset_reference}`

#### Standard CASA Namespaces:
- **Solana SPL Token (`token`)**: `solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp/token:DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263`
- **EVM ERC-20 (`erc20`)**: `eip155:1/erc20:0x6b175474e89094c44da98b954eedeac495271d0f` (always lowercase hex)
- **Native Gas Asset (`slip44`)**: `eip155:1/slip44:60` or `solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp/slip44:501`

#### Project-Specific CAIP-19 Extension Namespaces:
Certain non-EVM non-Solana chains do not yet have formalized CASA CAIP-19 namespace standards. The system implements deterministic conventions:
- **Move Framework Tokens (`move`)**: Used for Sui and Aptos struct types (e.g., `sui:mainnet/move:0x2::sui::SUI`, `aptos:mainnet/move:0x1::aptos_coin::AptosCoin`).
- **TON Jettons (`jetton`)**: Used for TON TEP-74 fungible tokens (e.g., `ton:mainnet/jetton:EQ...`).
- **TRON TRC-20 (`trc20`)**: Used for TRON TRC-20 smart contract tokens (e.g., `tron:mainnet/trc20:TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t`).

All asset identifiers validate against `AddressValidator` to ensure checksum and encoding rules are enforced before constructing CAIP-19 identifiers.

---

## 3. Data Classification Scheme & Confidence Semantics

Every piece of data returned by search and research utilities includes explicit classification:

### Data Classifications (`DataClassification`)
1. `ONCHAIN_VERIFIED`: Confirmed by RPC state call or cryptographic proof from a trusted local node.
2. `PROVIDER_REPORTED`: Reported by external discovery or aggregator provider (e.g. DexScreener).
3. `DERIVED_SNAPSHOT`: Calculated internally from historical snapshots and pool reserves.
4. `EXTERNAL_REFERENCE`: Formed from third-party reference services.
5. `CACHED`: Locally stored record within freshness TTL.
6. `STALE`: Locally stored record older than acceptable freshness threshold.
7. `UNAVAILABLE`: Data not found or unreachable.
8. `UNVERIFIED`: Metadata exists without authoritative decimal/contract verification.

### Forensic Safety Statuses (`ForensicSafetyStatus`)
1. `VERIFIED_SAFE_FOR_THIS_CHECK`: Scanned and passed all explicit safety checks.
2. `RISK_DETECTED`: Security scan detected honeypot, blacklisting, high tax, or malicious logic.
3. `PARTIAL`: Incomplete scan coverage.
4. `UNSUPPORTED`: Chain or contract standard not supported by available scanners.
5. `UNVERIFIED`: No security verification performed.
6. `STALE`: Previous security scan has expired.

---

## 4. Multi-Chain Search & Research REST APIs

### 1. Multi-Chain Search (`GET /api/v1/search`)
Query parameters:
- `q`: Search query (token symbol, name, contract/mint address, or pool address).
- `chain_id` *(optional)*: Restrict search to a specific chain (canonical CAIP-2 or alias).
- `limit` *(optional, default 20)*: Maximum items to return.

Response includes ranked tokens, matching pools, exact symbol matches first, followed by prefix and substring matches.

### 2. Asset Detail (`GET /api/v1/assets/{chain_id}/{asset_reference}`)
Retrieves comprehensive asset details including CAIP-19 identifier, verified decimals, metadata provenance, and data classification.

### 3. Market Data (`GET /api/v1/assets/{asset_id}/market`)
Retrieves latest price, reserves, 24h volume, and exact FDV calculated strictly from verified circulating/total supply and price without fabricated defaults.

### 4. Liquidity Pools (`GET /api/v1/assets/{asset_id}/pools`)
Returns all pools associated with the asset across supported DEX protocols, with reserve ratios and canonical pool flags.

### 5. Forensics (`GET /api/v1/assets/{asset_id}/forensics`)
Returns honeypot checks, tax rates, mint/freeze authorities, and overall safety classification.

### 6. Reference Integrations (`GET /api/v1/integrations`)
Returns status and metadata links for third-party reference adapters (Trojan, Banana Gun).

---

## 5. Security Invariants & Provenance Architecture

### Explorer URL Sanitizer & Parser Scope
- `ExplorerSanitizer` operates strictly as a URL string parser and validator for client-submitted search inputs.
- It does **not** perform server-side HTTP fetching or act as an active server-side SSRF firewall.
- If future features introduce server-side HTTP URL dereferencing, an active SSRF defense architecture must be implemented covering socket-level connection-time IP validation, DNS rebinding prevention, redirection following guards, and RFC 1918/6598/4193 CIDR block enforcement.
- Input validation rules:
  - Only HTTPS scheme is permitted.
  - Hostnames must match known trusted blockchain explorers (e.g., `solscan.io`, `etherscan.io`, `basescan.org`, `bscscan.com`, `arbiscan.io`, `optimistic.etherscan.io`, `polygonscan.com`, `snowtrace.io`, `suiscan.xyz`, `explorer.aptoslabs.com`, `tonscan.org`, `tronscan.org`).
  - Private IP literals (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `127.0.0.0/8`, loopback, and link-local) are strictly rejected.
  - Credential components (`user:pass@host`) are stripped or rejected.

### Reference Adapters Guardrail
- `TrojanReferenceAdapter` & `BananaGunReferenceAdapter`:
  - `supports_execution = False`
  - `supports_signing = False`
  - Any call to place orders, sign transactions, or broadcast raises `PermissionError`.
  - Zero private keys or signing credentials stored or accessed.

### Test Fixture Isolation & Trust Policy (Phase P10.2-I.2)
- **Structural Removal from Production Interfaces**: Production services (`PersistentPaperService`, `PaperSimulationEngine`) do not expose test registration methods.
- **Isolated Test Helper**: Test asset registration is located exclusively in `tests/fixtures/test_asset_helper.py` and requires explicit `APP_ENV == "test"`. Calls when `APP_ENV` is unset, `"development"`, or `"production"` fail closed with `PermissionError`.
- **Integrity vs Authenticity**: Storing a SHA-256 hash establishes byte-level content integrity; it does **not** prove on-chain authenticity, mint authority, or provider provenance.
- **Strict Decimal Provenance Trust Policy**:
  - Authoritative sources: `AUTHENTICATED_RPC_EVIDENCE`, `TRUSTED_PROVIDER_EVIDENCE`, `ONCHAIN_RPC_MINT_DATA`, `ONCHAIN_RPC_STATIC_CALL`, `RPC_NODE`, `CORE_TEST`.
  - Non-authoritative sources: `CONTROLLED_TEST_FIXTURE`, `SYNTHETIC_TEST_FIXTURE`, `STATIC_SEED`, `SEED_REGISTRY`, `DISCOVERY_METADATA`, `UNVERIFIED`.
  - Non-authoritative sources can **never** independently authorize paper orders. Static seeds (including BONK) and test fixtures remain `UNVERIFIED` and cannot authorize paper execution without authentic provider RPC evidence.
