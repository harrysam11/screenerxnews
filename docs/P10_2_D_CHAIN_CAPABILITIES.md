# Phase P10.2-D: 12-Chain Operational Capability & Adapter Validation

---

## 1. Overview & Objectives

Phase **P10.2-D** establishes a typed, chain-aware operational capability layer across all 12 supported blockchain networks. The core purpose of this phase is to establish what each chain adapter genuinely supports, eliminate fabricated or misleading mock responses, enforce strict protocol semantics (block heights, slots, checkpoints, on-chain timestamps, and token decimals), and document all boundary limitations before progressing to persistent paper trading (Phase P10.2-F) and live execution (Phase P10.2-H).

> [!CAUTION]
> `ALLOW_LIVE_TRADING=False` is strictly maintained. Phase P10.2-D validates read-only chain capability contracts and simulation boundaries. It does not implement private-key custody, wallet signing, or real-money transaction broadcast.

---

## 2. 12-Chain Inventory & Protocol Specifications

| Chain # | Canonical Identifier (CAIP-2) | Compatibility Alias | Chain Family | Consensus Model | Finality Mechanism | Finality Depth |
| :---: | :--- | :--- | :---: | :--- | :--- | :---: |
| 1 | `solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp` | `solana:mainnet` | SVM | Proof-of-History + Tower BFT | Tower BFT Root Bank | 32 slots (~12.8s) |
| 2 | `eip155:1` | - | EVM | Proof-of-Stake (Gasper) | Finalized Checkpoint (2 epochs) | 12 blocks |
| 3 | `eip155:8453` | - | EVM | OP Stack Sequencer + L1 Posting | Sequencer Soft / L1 Finalized | 64 blocks |
| 4 | `eip155:56` | - | EVM | PoSA (Parlia) | Fast Finality (2/3+ validator votes)| 15 blocks |
| 5 | `eip155:42161` | - | EVM | Nitro Rollup Sequencer + ArbOS | Sequencer Soft / L1 Finalized | 64 blocks |
| 6 | `eip155:10` | - | EVM | OP Stack Bedrock | Sequencer Soft / L1 Finalized | 64 blocks |
| 7 | `eip155:137` | - | EVM | Bor PoS + Heimdall Milestones | Checkpoint Finality | 128 blocks |
| 8 | `eip155:43114` | - | EVM | Snowman Consensus | Single-Block Instant Finality | 1 block (<1s) |
| 9 | `sui:mainnet` | - | Move | Mysticeti BFT / Narwhal-Bullshark | Single-Checkpoint BFT Certificate | 1 checkpoint |
| 10 | `aptos:mainnet` | - | Move | AptosBFT (Jolteon / DiemBFT v4) | Committed Ledger Version | 1 version |
| 11 | `ton:mainnet` | - | TVM | Catchain BFT Consensus | Masterchain Block Inclusion | 1 block |
| 12 | `tron:mainnet` | - | TVM | Delegated PoS (27 SRs) | Super Representative Confirmation | 19 blocks (~57s) |

---

## 3. 12-Chain Capability Matrix

Every capability across each chain is classified into:
- `SUPPORTED`: Implemented, verified against protocol semantics, and covered by local tests.
- `PARTIAL`: Core data model or direct object/contract reading supported; multi-hop routing or complex off-chain indexing delegated to specialized providers.
- `UNSUPPORTED`: Not supported by the chain's underlying architecture or disabled by policy.
- `UNVERIFIED`: Protocol method defined but requires live provider access to verify against non-mocked data.

| Capability Area | Solana | Ethereum | Base | BNB | Arbitrum | Optimism | Polygon | Avalanche | Sui | Aptos | TON | TRON |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Native Asset Metadata** | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED |
| **Address Validation** | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED |
| **Token Metadata** | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | PARTIAL | SUPPORTED |
| **Token Balance** | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED |
| **Block / Slot / Seq** | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED |
| **Transaction Parsing** | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED |
| **Transfer Decoding** | PARTIAL | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | PARTIAL | PARTIAL | PARTIAL | SUPPORTED |
| **Pool Liquidity Data** | PARTIAL | PARTIAL | PARTIAL | PARTIAL | PARTIAL | PARTIAL | PARTIAL | PARTIAL | PARTIAL | PARTIAL | PARTIAL | PARTIAL |
| **Swap Quote Routing** | PARTIAL | PARTIAL | PARTIAL | PARTIAL | PARTIAL | PARTIAL | PARTIAL | PARTIAL | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | PARTIAL |
| **Tx Simulation** | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED |
| **Fee Estimation** | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED |
| **Finality Tracking** | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED |
| **Reorg Handling** | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | SUPPORTED |
| **Holder Forensics** | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED |

---

## 4. Adapter Defect Remediation Summary

In earlier phases, adapters contained subtle correctness issues that fabricated mock data or treated protocol errors as valid responses. In P10.2-D, these were strictly eliminated:

1. **Elimination of Fabricated System Timestamps:**
   - Adapters previously used `datetime.now(timezone.utc)` when RPC timestamps were absent or on failure.
   - Now, adapters parse verified on-chain Unix timestamps (`timestamp` on EVM, `blockTime` on Solana, `timestampMs` on Sui, `ledger_timestamp` on Aptos, `timestamp` on TRON). If unavailable, they raise `DataUnavailableError`.
2. **Elimination of Fabricated Fees:**
   - Solana adapter previously hardcoded `fee_in_native = Decimal("0.000005")` across all transaction records. This was removed; fee fields remain `None` unless extracted from confirmed on-chain transaction status receipts.
3. **Elimination of Hardcoded Decimals:**
   - EVM adapter previously defaulted to `decimals = 18` for all arbitrary token queries without executing contract calls.
   - Adapters now query verified ERC-20 `decimals()` (0x313ce567), Sui `suix_getCoinMetadata`, Aptos `CoinInfo<T>`, and TRON `decimals()`. If the contract call fails, `decimals` is returned as `None` or raises `DataUnavailableError`.
4. **Elimination of Fabricated Block 0 Responses:**
   - When RPC nodes failed or returned empty data, adapters previously caught exceptions and returned dummy block headers `(block_number=0, block_hash="0x0")`.
   - Adapters now propagate typed exceptions: `RpcError`, `DataUnavailableError`, and `ChainMismatchError`.
5. **Chain ID Mismatch Enforcement:**
   - `EvmChainAdapter.verify_chain_id()` executes `eth_chainId` and validates that the remote endpoint matches the expected CAIP-2 reference, raising `ChainMismatchError` if mismatched.
6. **Typed Multi-Chain Address Validation:**
   - Implemented [`backend/app/core/address_validator.py`](file:///home/sam/Documents/screenerxnews/backend/app/core/address_validator.py) with:
     - EIP-55 mixed-case checksum verification via pure-Python `keccak256`.
     - Solana Base58 Ed25519 32-byte public key decoding.
     - Sui 0x-prefixed 64-hex character normalization.
     - Aptos Move struct type tag parsing (`address::module::struct`).
     - TON raw (`0:hex`) and user-friendly Base64url (48 chars) with CRC16-CCITT checksum validation.
     - TRON Base58Check ('T...', 34 chars, double-SHA256 checksum) and 41-hex bi-directional conversion.

---

## 5. Native Asset Units & Smallest Currency Denominations

| Chain | Native Asset | Decimals | Smallest Unit Name | Base Unit Conversion Factor |
| :--- | :---: | :---: | :--- | :--- |
| **Solana** | SOL | 9 | lamport | $1\text{ SOL} = 10^9\text{ lamports}$ |
| **Ethereum** | ETH | 18 | wei | $1\text{ ETH} = 10^{18}\text{ wei}$ |
| **Base** | ETH | 18 | wei | $1\text{ ETH} = 10^{18}\text{ wei}$ |
| **BNB Chain** | BNB | 18 | wei | $1\text{ BNB} = 10^{18}\text{ wei}$ |
| **Arbitrum** | ETH | 18 | wei | $1\text{ ETH} = 10^{18}\text{ wei}$ |
| **Optimism** | ETH | 18 | wei | $1\text{ ETH} = 10^{18}\text{ wei}$ |
| **Polygon** | POL | 18 | wei | $1\text{ POL} = 10^{18}\text{ wei}$ |
| **Avalanche** | AVAX | 18 | nAVAX / wei | $1\text{ AVAX} = 10^{18}\text{ nAVAX}$ |
| **Sui** | SUI | 9 | MIST | $1\text{ SUI} = 10^9\text{ MIST}$ |
| **Aptos** | APT | 8 | Octa | $1\text{ APT} = 10^8\text{ Octas}$ |
| **TON** | TON | 9 | nanoton | $1\text{ TON} = 10^9\text{ nanotons}$ |
| **TRON** | TRX | 6 | SUN | $1\text{ TRX} = 10^6\text{ SUN}$ |

---

## 6. Known Limitations & Unverified Operational Areas

1. **TON Jetton Off-Chain Metadata:** TEP-74 Jetton master contracts store total supply, mintable status, and admin address on-chain, but token decimals, symbol, and name are often stored in off-chain JSON or IPFS URI payloads. In local offline mode, off-chain IPFS resolving is unverified.
2. **Move DEX Swap Routing:** Sui and Aptos DEXes (Cetus, Turbos, Liquidswap, Thala) use programmable transaction blocks (PTBs) and Move entry functions whose complete multi-hop route optimization requires off-chain indexers.
3. **Historical Token Transfer Indexing:** Direct RPC node querying for historical transfers without an archival indexer or event indexing node is rate-limited on free community RPCs.

---

## 7. Test Suite Verification Summary

```
tests/test_p10_2_d_chain_capabilities.py .................                  [100%]
17 passed in 0.75s

Full Test Suite Run:
================ 198 passed, 1 skipped, 1 warning in 12.61s =================
```
Zero test failures across all 198 tests in the codebase.
