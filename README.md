# Solana Memecoin Intelligence, Forensics & Trading OS

A local-first, self-hosted Solana Memecoin Research, On-Chain Forensics, Narrative Intelligence, and Paper Trading Operating System.

## Architecture & Technology Stack

* **Workflow Orchestration**: Self-hosted [n8n Community Edition](https://n8n.io)
* **Application Core**: Python 3.11+ / FastAPI (REST + WebSockets)
* **Storage & Relational Database**: PostgreSQL 15+ (`pgcrypto`, `uuid-ossp`)
* **Security & Contract Forensics**: GoPlus Solana API, RugCheck, native Solana RPC Token-2022 parser
* **Market Data & Discovery**: DEX Screener API, Solana RPC
* **Quoting & Execution Gateway**: Jupiter Swap V2 API + External Client Wallet Signing
* **Simulated Execution**: Dedicated isolated Paper Trading desk (100 SOL virtual balance)

---

## Strict Security & Isolation Invariants

1. **Zero Private Key Storage**: No private keys or seed phrases are stored on disk, inside PostgreSQL, or within n8n workflows.
2. **Cold Vault Permanent Exclusion**: Cold storage addresses are explicitly banned at both the database and API layer from signing or execution gateway flows.
3. **Double-Entry Financial Accounting**: Exact raw token and lamport amounts are recorded in an immutable ledger with balanced debit/credit lines. No IEEE floats are used for balances.
4. **Isolated Paper Desk**: Paper accounts, positions, and simulated fills live in completely isolated database tables (`paper_*`) and cannot affect live ledgers.

---

## Directory Layout

```text
├── docker-compose.yml           # Local PostgreSQL 15 & n8n Community services
├── migrations/
│   └── 001_initial_schema.sql   # Complete production SQL schema & indexes
├── requirements.txt             # Python dependencies
├── .env.example                 # Template environment variables
├── backend/
│   └── app/
│       ├── main.py              # FastAPI application entrypoint
│       ├── core/                # Configuration and database connection pool
│       ├── schemas/             # Pydantic DTO contracts
│       ├── models/              # SQLAlchemy async ORM models
│       └── api/v1/              # Modular API endpoints
│           ├── discovery.py     # Token discovery & filtering
│           ├── forensics.py     # Rug-pull & first 10 buyers analysis
│           ├── news.py          # News & narrative ingestion
│           ├── scoring.py       # 4-factor opportunity score
│           ├── paper.py         # Paper trading desk & position simulator
│           ├── execution.py     # Live execution gateway (intent & approval)
│           └── portfolio.py     # Double-entry ledger & portfolio
└── n8n_workflows/               # Sample n8n JSON workflow templates
    ├── WF-01_token_discovery.json
    ├── WF-03_security_scanner.json
    └── WF-20_simulated_execution.json
```

---

## Master Specification Document

For the comprehensive specification covering the 25 n8n workflows, database schemas, risk formulas, and API mappings, see the master specification artifact generated in this workspace.
