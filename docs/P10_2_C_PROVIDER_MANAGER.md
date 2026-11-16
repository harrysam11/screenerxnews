# Phase P10.2-C: User-Controlled API, RPC & Provider Manager
## Security Hardening & Architecture Specification (P10.2-C.2)

---

## 1. Architecture Overview

Phase P10.2-C and P10.2-C.2 establish a lightweight, local-first provider management system that gives the user complete sovereignty over external data sources, RPC nodes, API credentials, and network boundaries.

The system enforces six core security and financial-integrity guarantees:
1. **Zero Hardcoded Providers**: No single external provider or commercial node is mandatory. The system operates in fully offline mode, with public community nodes, or with user-owned private RPCs and API keys.
2. **SEC-01 (Critical): Atomic Two-Phase Quota & Spend Reservation**: Request quotas and monetary budgets are guarded by an atomic SQLite reservation state machine under `BEGIN IMMEDIATE;`. Concurrent requests cannot race to exceed daily limits or USD spend ceilings.
3. **SEC-02 (Critical): Centralized Global Offline Enforcement**: Network transport is guarded by a centralized HTTP transport factory (`OfflineEnforcingAsyncTransport`). In offline mode, network socket creation is intercepted and rejected before any bytes leave the host.
4. **SEC-03 (High): Canonical CAIP-2 Chain Identity**: Solana is canonically addressed via CAIP-2 identifier `solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp`, with transparent backward-compatible alias resolution for `solana:mainnet`.
5. **SEC-04 (Medium): Unique Persistent Cryptographic Salt**: Each installation generates an independent, high-entropy 32-byte cryptographic salt stored with `0600` permissions alongside derivation metadata (`.vault_meta.json`), with dual-salt fallback for legacy secrets.
6. **SEC-05 (Medium): Vault Backup & Recovery Isolation**: Database backups never bundle plaintext master keys. Key recovery is managed exclusively via user-passphrase encrypted recovery packages (`.vaultpkg`).
7. **SEC-06 (Medium): API Access Protection**: Provider configuration and credential endpoints are restricted to local loopback (`127.0.0.1`) or require mandatory bearer token authentication (`ADMIN_API_TOKEN` / `X-API-Key`).

---

## 2. Subsystem Architecture

```
+-----------------------------------------------------------------------------------------+
|                                      FastAPI Layer                                      |
|  /api/v1/providers  |  /rpc-endpoints  |  /credentials  |  /mode  | (Admin Auth Guard)  |
+--------------------------------------------+--------------------------------------------+
                                             |
                                             v
+-----------------------------------------------------------------------------------------+
|                               Provider Manager Service                                  |
|  - Operating Mode & Offline State Check                                                 |
|  - SEC-01: Atomic Reservation Lifecycle (RESERVED -> COMMITTED / RELEASED / FAILED)     |
|  - Capability Matrix Validation & Provider Resolution                                   |
|  - Circuit Breakers & Cooldown State Machine                                            |
|  - SEC-02: Centralized Network Dispatch via OfflineEnforcingAsyncTransport               |
+-----------------------------+-------------------------------+---------------------------+
                              |                               |
                              v                               v
+---------------------------------------+   +---------------------------------------------+
|       Credential Vault Service        |   |          Canonical SQLite Storage           |
|  - SEC-04: Unique Salt (0600)         |   |  - providers                                |
|  - PBKDF2HMAC (100k rounds, SHA-256)  |   |  - provider_credentials                     |
|  - AES-Fernet Ciphertext              |   |  - chain_rpc_endpoints (12 Chains)          |
|  - Secret Masking (sk-l...6789)       |   |  - provider_usage_records                   |
|  - Key Fingerprints & Zero-Plaintext  |   |  - provider_reservations (SEC-01)           |
|  - SEC-05: Encrypted Recovery Package |   |  - provider_fallback_rules                  |
|    (.vaultpkg)                        |   |  - provider_audit_log                       |
+---------------------------------------+   +---------------------------------------------+
```

---

## 3. Capabilities Taxonomy

The engine standardizes capabilities across all providers:
- `CHAIN_RPC`: JSON-RPC node access for block indexing, contract calls, and balances.
- `MARKET_DATA`: OHLCV pricing, volume, market cap, and liquidity metrics.
- `PRICE_LIQUIDITY`: Real-time orderbook and pool liquidity depth.
- `TOKEN_METADATA`: Token name, symbol, decimals, program extensions, and supply.
- `SECURITY_FORENSICS`: Honeypot detection, mint authority status, and holder concentration.
- `MARKET_DISCOVERY`: Newly created pools, trending pairs, and narrative surges.

Requests for unsupported capabilities return an explicit `UNSUPPORTED` status code rather than crashing or returning ambiguous data.

---

## 4. Remediation Specifications Matrix (P10.2-C.2)

| Issue Code | Severity | Description | Remediated In | Invariant Enforced |
| :--- | :--- | :--- | :--- | :--- |
| **SEC-01** | Critical | Concurrent requests bypass quotas | `backend/app/services/provider_manager.py` | Atomic reservation before dispatch under `BEGIN IMMEDIATE;` |
| **SEC-02** | Critical | Direct HTTP calls bypass offline mode | `backend/app/core/network.py` | Centralized transport blocks all outbound socket creation |
| **SEC-03** | High | Solana CAIP-2 identity mismatch | `backend/app/core/chains.py` | Canonical `solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp` with alias support |
| **SEC-04** | Medium | Static KDF salt vulnerability | `backend/app/services/credential_vault.py` | Unique 32-byte salt with `0600` permissions and dual-salt fallback |
| **SEC-05** | Medium | Plaintext vault key risk in backups | `backend/app/db/sqlite_backup.py` | Plain DB backup never bundles keys; encrypted recovery packages only |
| **SEC-06** | Medium | Unauthenticated configuration API | `backend/app/api/v1/providers.py` | Mandated loopback binding or `ADMIN_API_TOKEN` / `X-API-Key` |

---

## 5. Live Trading Safeguards

> [!CAUTION]
> `ALLOW_LIVE_TRADING=False` is hardcoded across all system configurations and environments. Under no circumstances may provider management, RPC activation, or API key configuration initiate real-money execution. All trading execution operates solely in simulated paper trading mode.
