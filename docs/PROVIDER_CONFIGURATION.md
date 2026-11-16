# Provider Configuration Guide

## Overview

The Provider Manager allows operators to configure, prioritize, and toggle external intelligence and market data providers without code changes or restarts.

---

## Supported Provider Categories

1. **Market Data & Discovery**:
   - `DEX Screener`: Multi-chain trending pools, liquidity, volume, and pair profiles.
   - `CoinGecko Free API`: Token prices, market capitalizations, and coin metadata.
2. **Security & Forensics**:
   - `GoPlus Security`: Contract security analysis, honeypot detection, and transfer fee audits.
   - `RugCheck XYZ`: Solana-specific token safety analysis and mint authority audits.
3. **Custom Providers**:
   - User-defined REST endpoints, self-hosted indexers, or private nodes.

---

## Registering a Provider via API

Providers are registered via `POST /api/v1/providers`:

```json
{
  "id": "prov-custom-solana",
  "name": "Custom Solana Node",
  "provider_type": "rpc",
  "tier": "free",
  "is_enabled": true,
  "is_paid": false,
  "priority": 10,
  "capabilities": ["CHAIN_RPC"],
  "chains": ["solana:mainnet"],
  "auth_type": "none",
  "rate_limit_rpm": 120,
  "daily_request_budget": 50000,
  "daily_spend_budget_usd": 0.0,
  "timeout_seconds": 5.0,
  "max_retries": 3
}
```

---

## Enabling and Disabling Providers

Providers default to disabled (`is_enabled: 0`) when initially created to prevent unreviewed network traffic:

- **Enable Provider**:
  ```bash
  curl -X POST http://localhost:8000/api/v1/providers/prov-custom-solana/enable
  ```

- **Disable Provider**:
  ```bash
  curl -X POST http://localhost:8000/api/v1/providers/prov-custom-solana/disable
  ```

Disabling a provider immediately ceases all outbound requests to that provider and logs a `PROVIDER_DISABLED` audit event.
