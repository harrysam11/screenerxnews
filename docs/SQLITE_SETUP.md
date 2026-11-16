# SQLite Setup & Configuration Guide

## 1. Engine Configuration

The application defaults to SQLite in `backend/app/core/config.py`:

```env
DATABASE_BACKEND=sqlite
DATABASE_URL=sqlite:///./data/memecoin_os.db
```

### Key Pragmas Applied Automatically:
- `PRAGMA journal_mode = WAL;` — Write-Ahead Logging allows simultaneous reads while a write transaction is executing.
- `PRAGMA foreign_keys = ON;` — Ensures relational integrity across tables.
- `PRAGMA busy_timeout = 30000;` — Waits up to 30 seconds for lock release during transient write contention instead of immediate failure.

---

## 2. Schema Provisioning

On application startup, `SqliteMigrator` runs automatically:
1. Inspects `PRAGMA user_version`.
2. Creates the tracking table `schema_migrations`.
3. Sequentially provisions the 14 core entity tables (assets, pools, security scans, paper accounts, orders, positions, events, and ledger triggers).
4. Records version completion in `schema_migrations`.

---

## 3. Preserving PostgreSQL Support

To run against PostgreSQL instead of SQLite:
1. Set `DATABASE_BACKEND=postgresql` in `.env`.
2. Configure `DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/dbname`.
3. Apply migrations using the SQL scripts in `migrations/`.
