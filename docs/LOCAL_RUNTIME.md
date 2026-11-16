# Local Runtime Operation Guide

## 1. Starting the Application

To start the local-first application on desktop Linux or Termux:

```bash
# Option A: Standard Uvicorn startup
PYTHONPATH=backend uvicorn app.main:app --host 0.0.0.0 --port 8000

# Option B: Using the Termux launcher
./scripts/termux_start.sh
```

---

## 2. Health & Component Status Endpoint

The core application provides a component health verification endpoint:
`GET /api/v1/health`

Example Response:
```json
{
  "status": "healthy",
  "database": {
    "backend": "sqlite",
    "connected": true,
    "path": "./data/memecoin_os.db"
  },
  "optional_components": {
    "docker": false,
    "postgresql": false,
    "redis": false,
    "n8n": false,
    "windmill": false,
    "hermes": false,
    "local_ai": false
  },
  "live_trading_allowed": false
}
```

---

## 3. Offline vs. Online Research

1. **Offline Mode:**
   - Functions with zero network connectivity.
   - Queries locally cached tokens, pools, forensic reports, and paper trading state.
   - Timestamps reflect the original observation time.
2. **Online Mode:**
   - Ingests candidates and queries RPCs using user-configured providers.
   - Enforces rate limits and quota monitoring.
