# RPC Configuration Guide (12 Chains) & Canonical CAIP-2 (P10.2-C.2)

---

## 1. Overview

The Multi-Chain Memecoin OS supports first-class RPC configuration across 12 blockchain networks. Each chain supports multiple registered RPC endpoints with priority ordering, health-check monitoring, and automated circuit-breaker failover.

In P10.2-C.2, **SEC-03: Canonical CAIP-2 Chain Identity** standardized the Solana mainnet identifier to `solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp`, while providing transparent backward compatibility for `solana:mainnet`.

---

## 2. Supported Blockchain Networks (CAIP-2 Identifiers)

| Canonical Chain ID | Alias / Reference | Network Name | Type | Default Public RPC |
| :--- | :--- | :--- | :--- | :--- |
| `solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp` | `solana:mainnet` | Solana Mainnet | SVM | `https://api.mainnet-beta.solana.com` |
| `eip155:1` | - | Ethereum Mainnet | EVM | `https://eth.llamarpc.com` |
| `eip155:8453` | - | Base Mainnet | EVM | `https://mainnet.base.org` |
| `eip155:56` | - | BNB Smart Chain | EVM | `https://bsc-dataseed.binance.org` |
| `eip155:42161` | - | Arbitrum One | EVM | `https://arb1.arbitrum.io/rpc` |
| `eip155:10` | - | OP Mainnet | EVM | `https://mainnet.optimism.io` |
| `eip155:137` | - | Polygon PoS | EVM | `https://polygon-rpc.com` |
| `eip155:43114` | - | Avalanche C-Chain | EVM | `https://api.avax.network/ext/bc/C/rpc` |
| `sui:mainnet` | - | Sui Mainnet | Move | `https://fullnode.mainnet.sui.io:443` |
| `aptos:mainnet` | - | Aptos Mainnet | Move | `https://fullnode.aptoslabs.com/v1` |
| `ton:mainnet` | - | The Open Network | TVM | `https://toncenter.com/api/v2/jsonRPC` |
| `tron:mainnet` | - | TRON Mainnet | TVM | `https://api.trongrid.io` |

---

## 3. SEC-03: Canonical Identity & Alias Resolution

To prevent partition bugs between services using standard CAIP-2 hashes and human-readable aliases:
- `backend/app/core/chains.py` exposes `normalize_chain_id(chain_id)` and `to_caip2_chain_id(chain_id)`.
- `ChainRegistry.get_chain("solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp")` returns the Solana metadata object.
- `ChainRegistry.get_chain("solana:mainnet")` resolves identically.
- `ProviderManager.get_active_rpc_endpoint(...)` accepts either format and resolves the active RPC node identically.

---

## 4. Adding a Custom RPC Endpoint

Users can register private nodes (e.g., Alchemy, QuickNode, Infura, local validators) via `POST /api/v1/providers/rpc-endpoints`:

```json
{
  "id": "rpc-sol-triton-private",
  "chain_id": "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp",
  "name": "Triton RPC Node",
  "endpoint_url": "https://solana-mainnet.rpcpool.com/YOUR_API_KEY",
  "ws_url": "wss://solana-mainnet.rpcpool.com/YOUR_API_KEY",
  "priority": 1,
  "is_enabled": true,
  "is_paid": true,
  "daily_budget": 500000
}
```

> [!NOTE]
> All URLs submitted through the API are automatically sanitized prior to persistent storage. Query keys (`?key=...`) and path tokens (`/v2/<token>`) are stripped from public database tables and logs. Raw keys are injected at runtime from the encrypted credential vault.

---

## 5. Testing RPC Endpoints

Test node latency and connectivity via `POST /api/v1/providers/rpc-endpoints/{id}/test`:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/providers/rpc-endpoints/rpc-sol-triton-private/test \
  -H "X-API-Key: $ADMIN_API_TOKEN"
```

Response:
```json
{
  "status": "SUCCESS",
  "success": true,
  "status_code": 200,
  "latency_ms": 38.45,
  "checked_at": "2026-11-14T18:20:00Z"
}
```
In offline mode, testing endpoints is immediately intercepted and blocked to prevent accidental socket establishment.
