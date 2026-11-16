# Offline vs. Online Operational Modes & Network Isolation (P10.2-C.2)

---

## 1. Overview

The Memecoin OS implements strict network boundary isolation. Operators can switch between fully offline research, online research, and persistent paper trading.

In P10.2-C.2, isolation was hardened with **SEC-02: Centralized Global Offline Enforcement**, ensuring 100% of outbound HTTP, JSON-RPC, and discovery calls pass through a centralized transport layer that prevents network socket creation while in offline mode.

---

## 2. Operational Modes Matrix

| Mode | `OPERATING_MODE` | Outbound Network Requests | Paper Trading Execution | Live Trading Execution |
| :--- | :--- | :--- | :--- | :--- |
| **Offline Research** | `offline_research` | **STRICTLY BLOCKED** | Local SQLite Simulation | Strictly Prohibited |
| **Online Research** | `online_research` | Allowed (User-Configured) | Local SQLite Simulation | Strictly Prohibited |
| **Paper Trading** | `paper_trading` | Allowed (User-Configured) | Local SQLite Simulation | Strictly Prohibited |
| **Live Trading** | `live_trading` | Allowed (User-Configured) | Disabled | **Requires Hardware Signer & Approval** |

---

## 3. SEC-02: Centralized Global Offline Enforcement

### The `OfflineEnforcingAsyncTransport`

All HTTP and RPC communication in the application is constructed via `backend/app/core/network.py:get_http_client()`.

```python
from app.core.network import get_http_client, OfflineModeBlockedException

async def fetch_remote_data():
    async with get_http_client() as client:
        # If OPERATING_MODE=offline_research or settings.is_offline=True,
        # handle_async_request() intercepts the call BEFORE any socket is created.
        response = await client.get("https://api.dexscreener.com/latest/dex/search?q=SOL")
```

### Enforced Outbound Services

Every service and network adapter routes through this centralized transport:
- `backend/app/services/dexscreener.py` (Market data)
- `backend/app/services/goplus.py` (Security forensics)
- `backend/app/services/rugcheck.py` (Token risk analysis)
- `backend/app/services/evm_rpc.py` (EVM JSON-RPC queries)
- `backend/app/services/solana_rpc.py` (Solana JSON-RPC queries)
- `backend/app/adapters/evm.py` (EVM chain adapter)
- `backend/app/adapters/solana.py` (Solana chain adapter)
- `backend/app/adapters/sui.py` (Sui chain adapter)
- `backend/app/adapters/aptos.py` (Aptos chain adapter)
- `backend/app/adapters/ton.py` (TON chain adapter)
- `backend/app/adapters/tron.py` (TRON chain adapter)
- `backend/app/api/v1/providers.py:test_rpc_endpoint` (RPC health check probes)

### Behavior on Interception

When an outbound call is attempted during offline mode:
1. `OfflineModeBlockedException` is raised immediately.
2. No TCP socket or TLS handshake is initiated.
3. The Provider Manager records an `OFFLINE_BLOCKED` audit event in `provider_audit_log`.
4. The client receives an explicit structured response:
   ```json
   {
     "status": "OFFLINE_BLOCKED",
     "success": false,
     "message": "Outbound network calls are disabled in offline mode.",
     "data": null
   }
   ```

---

## 4. Switching Modes at Runtime

Operators can switch modes dynamically via the web dashboard or REST API:

```bash
# Switch to offline research mode
curl -X POST http://127.0.0.1:8000/api/v1/providers/mode/set \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $ADMIN_API_TOKEN" \
  -d '{"mode": "offline_research"}'

# Switch to online research mode
curl -X POST http://127.0.0.1:8000/api/v1/providers/mode/set \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $ADMIN_API_TOKEN" \
  -d '{"mode": "online_research"}'
```

Attempting to switch to `live_trading` while `ALLOW_LIVE_TRADING=False` raises an immediate `PermissionError` (HTTP 400).
