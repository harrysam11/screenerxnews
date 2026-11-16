# Resource Configuration & Tuning Guide

## 1. Resource Budgets for Lightweight / Mobile Environments

| Resource | Mobile / Termux Target | Measured Reality (Linux/x86_64) |
| :--- | :--- | :--- |
| **Startup Duration** | < 2.0 seconds | **0.62 seconds** |
| **Idle RAM (RSS)** | < 150 MB | **77.48 MB** |
| **Active Trade Latency** | < 5.0 ms / op | **0.031 ms / op** |
| **Database Connections** | Max 5 active threads | 1 per service instance + WAL |

---

## 2. Configuration Parameters

In `.env` or application config:
- `SQLITE_BUSY_TIMEOUT_MS=30000`: Maximum wait time before failing locked queries.
- `MAX_CONCURRENT_RPC_REQUESTS=5`: Throttles network concurrency on low-bandwidth connections.
- `ENABLE_HIGH_FREQUENCY_SCAN=false`: Disables continuous discovery polling to preserve CPU and battery.
