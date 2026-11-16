# P10.2-B Architecture Specification: SQLite-First Lightweight Core

## 1. System Overview

The Multi-Chain Memecoin Intelligence, Forensics & Trading OS is designed around a **genuinely local-first, lightweight core**. While previous versions assumed heavy infrastructure (Docker Compose, PostgreSQL daemon, Redis caching, n8n, Windmill, Hermes), Phase P10.2-B establishes a minimal operational mode capable of running on modest personal hardware and mobile Linux environments (such as Android Termux).

```text
                    USER INTERFACE (SPA)
                             |
                             v
                        FASTAPI CORE
                             |
            +----------------+----------------+
            |                |                |
            v                v                v
         Research        Strategies       Risk Engine
            |                |                |
            +----------------+----------------+
                             |
                             v
                      APPLICATION CORE
                             |
            +----------------+----------------+
            |                |                |
            v                v                v
         SQLite          Event Bus        Scheduler
            |                |                |
            +----------------+----------------+
                             |
                             v
                    PROVIDER INTERFACES
                             |
            +----------------+----------------+
            |                |                |
            v                v                v
           RPC           Data APIs       Local Dossiers
                             |
                             v
                       CHAIN ADAPTERS
                             |
            +----------------+----------------+
            |                |                |
            v                v                v
          Solana            EVM           Non-EVM
```

---

## 2. Default & Optional Infrastructure Separation

| Component | Default Status | Failure / Missing Behavior |
| :--- | :--- | :--- |
| **SQLite Backend** | **Default Required** | Core fails if data directory is unwritable |
| **PostgreSQL** | *Optional* | Disabled by default; configured via `DATABASE_BACKEND=postgresql` |
| **Redis** | *Optional* | Not required; in-process memory caching used |
| **Docker** | *Optional* | Not required; pure Python native execution |
| **Windmill** | *Optional* | Disabled by default; bypassed for core research & paper trading |
| **n8n** | *Optional* | Disabled by default; bypassed for local notifications |
| **Hermes AI** | *Optional* | Disabled by default; algorithmic scoring operates offline |
| **Cloud AI** | *Disabled* | Strictly forbidden; zero telemetry or external model dependencies |

---

## 3. Subsystem Storage & Restart Persistence

| Subsystem | Storage Engine | File Location | Restart Persistence |
| :--- | :--- | :--- | :--- |
| **Execution Intents** | SQLite | `data/execution_intents.db` | Fully Persistent |
| **Ledger Journals & Lines** | SQLite | `data/ledger.db` | Fully Persistent, Trigger-Enforced |
| **Paper Accounts & Orders** | SQLite | `data/memecoin_os.db` | Fully Persistent |
| **Tokens & Pools Metadata** | SQLite | `data/memecoin_os.db` | Fully Persistent |
| **Security Forensics Scans** | SQLite | `data/memecoin_os.db` | Fully Persistent |
| **Audit Logs & Settings** | SQLite | `data/memecoin_os.db` | Fully Persistent |
| **Provider Vault Credentials** | SQLite (AES-256-GCM) | `data/vault.db` | Fully Persistent & Encrypted |
