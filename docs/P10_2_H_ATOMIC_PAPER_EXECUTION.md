# P10.2-H — Atomic Paper Execution & Strategy Authorization Bridge

**Repository:** `harrysam11/screenerxnews`  
**Branch:** `feat/p10.2-c.2-security-remediation`  
**Phase:** P10.2-H  
**Status:** Implemented & Verified  
**Live Trading Boundary:** `ALLOW_LIVE_TRADING=False` strictly enforced  
**Orchestration Architecture:** Core Standalone; Windmill PRIMARY; Hermes OPTIONAL read-only; n8n DEFERRED.

---

## 1. Overview

P10.2-H closes the integration gap between the Strategy SDK (P10.2-G) and Persistent Paper Trading (P10.2-F) by introducing an enforceable, auditable, and atomic authorization bridge.

Strategies produce proposed trade signals or intents only. Under P10.2-H, strategy proposals serve as strict authorization records. A strategy can never directly execute orders, modify balances, write to the double-entry financial ledger, or bypass core risk controls.

### Execution Path
```
Trusted Built-in Strategy
       │
       ▼
Immutable Context Snapshot
       │
       ▼
Deterministic Signal Generation
       │
       ▼
Persistent Strategy Proposal (PROPOSED_ONLY)
       │
       ▼
Core Authorization Bridge (`execute_strategy_proposal`)
       ├── Account Ownership & Authorization Hash Validation
       ├── Signal & Proposal Expiry Checks
       ├── Strategy Enablement Check
       ├── Trusted Decimal Provenance Verification
       ├── Real-time Account Solvency Validation
       └── Durable Risk Limits Validation
       │
       ▼
Atomic SQLite Immediate Transaction
       ├── AMM Constant-Product Fill Simulation (x * y = k)
       ├── Insert Paper Order (`origin = 'STRATEGY_PROPOSAL'`, `proposal_id`)
       ├── Insert Paper Fill (`fill_price_native`, `slippage_bps`)
       ├── Update Paper Position & Account Balance
       ├── Transition Proposal (`CONSUMED`, `order_id`, `consumed_at`)
       └── Post Double-Entry Ledger Journal & Lines (In-Transaction)
```

---

## 2. Core Components & Deliverables

### H-01: Atomic Proposal-to-Paper-Order Bridge
- **Service Method:** `PersistentPaperService.execute_strategy_proposal(proposal_id, account_id)`.
- **Database-Level Uniqueness:**
  - `paper_orders_v2.proposal_id` with `UNIQUE` partial index (`WHERE proposal_id IS NOT NULL`).
  - `strategy_proposals.order_id` with `UNIQUE` partial index (`WHERE order_id IS NOT NULL`).
- **Idempotency:** Re-executing an already-consumed proposal retrieves the existing order and fill without re-executing or double-posting ledger entries.
- **Rollback Atomicity:** If any step fails (e.g., ledger constraint, AMM slippage, balance shortage), the entire transaction rolls back via `ROLLBACK`. The proposal remains `PROPOSED_ONLY` and zero orders are created.

### H-02: Separation of Manual vs Strategy-Originated Orders
- Manual paper order submission (`POST /api/v1/paper/order` and `PersistentPaperService.execute_order(...)`) explicitly rejects any payload specifying `proposal_id` or `origin == 'STRATEGY_PROPOSAL'`.
- All manual paper orders are stamped with `origin = 'MANUAL'`.
- Strategy-originated orders are stamped with `origin = 'STRATEGY_PROPOSAL'` and require a valid, non-expired, unconsumed proposal in `strategy_proposals`.

### H-03: Historical Strategy Configuration Snapshots & Deterministic Replay
- Evaluated runs in `strategy_runs` persist:
  - `config_snapshot_json`: Full JSON snapshot of strategy parameters, risk rules, and lookback configuration at execution time.
  - `config_hash`: SHA-256 digest of `config_snapshot_json`.
  - `sdk_version`: Active Strategy SDK version.
- `StrategyService.replay_evaluation(run_id)` verifies:
  1. Active strategy version matches historical run version.
  2. SHA-256 hash of historical configuration snapshot matches stored `config_hash`.
  3. SHA-256 hash of historical input context matches stored `input_dataset_hash`.
  4. Parameters are reconstructed strictly from `config_snapshot_json` (active DB parameter alterations are ignored).
  5. Evaluates output with `"is_replay": True`.

### H-04: API Endpoint Protection
- Protected routes in `backend/app/api/v1/strategies.py`:
  - `GET /api/v1/strategies/{strategy_id}/signals`
  - `GET /api/v1/strategies/{strategy_id}/runs`
  - `POST /api/v1/strategies/proposals/{proposal_id}/execute`
  - `GET /api/v1/strategies/proposals/reconcile`
- Security dependency: `verify_strategy_access`:
  - Validates client IP against loopback interfaces (`127.0.0.1`, `::1`, `localhost`, `::ffff:127.0.0.1`).
  - Supports constant-time `secrets.compare_digest` token validation against `ADMIN_API_TOKEN` if non-loopback.
  - Rejects unauthorized requests with HTTP 403 Forbidden.

### H-05: Trusted Token Decimal Provenance
- Table: `token_decimal_verifications`:
  - Columns: `verification_id`, `chain_id`, `asset_address`, `decimals`, `symbol`, `verification_source`, `verification_method`, `raw_metadata_hash`, `status`, `verified_at`, `created_at`.
  - Constraint: `UNIQUE(chain_id, asset_address)`.
- Verification Authority:
  - Untrusted ingestion/discovery events (e.g. DexScreener scraped payload) do NOT establish decimal verification authority.
  - Conflicting decimal metadata raises `ValueError` and fails closed (silent overwriting of verified decimals is strictly forbidden).
  - Exact integer scaling for 5, 6, 8, 9, and 18 decimal assets without heuristic thresholds.

### H-06: Schema Migration 10
- Implemented in `backend/app/db/sqlite_migrator.py`:
  - Version: 10 (`010_atomic_paper_execution_and_decimal_provenance`).
  - Table: `token_decimal_verifications`.
  - Columns added: `paper_orders_v2` (`origin`, `proposal_id`, `authorization_hash`).
  - Columns added: `strategy_proposals` (`order_id`, `consumed_at`, `authorization_hash`).
  - Columns added: `strategy_runs` (`config_snapshot_json`, `config_hash`, `sdk_version`).
  - Unique partial indexes on `paper_orders_v2(proposal_id)` and `strategy_proposals(order_id)`.
  - Seed verified token metadata for multichain test assets.

### H-07: Reconciliation & Audit Trail
- Method: `PersistentPaperService.reconcile_strategy_proposals()`.
- Route: `GET /api/v1/strategies/proposals/reconcile`.
- Audits and detects:
  - Consumed proposals with missing orders (`orphan_proposals`).
  - Strategy-originated orders with missing proposals (`orphan_strategy_orders`).
  - Account mismatch between proposal and order (`account_mismatches`).
  - Orders missing balanced double-entry ledger entries (`orders_missing_ledger`).

### H-08: Windmill Primary Workflow Script (WF-22)
- Script: `windmill_scripts/wf22_execute_strategy_proposal.py`.
- Primary workflow that orchestrates proposal execution by calling Core authorization bridge.
- Strict isolation: script never executes SQL or posts ledger entries directly.
- Standardized status responses: `SUCCESS`, `FORBIDDEN`, `EXPIRED`, `RISK_REJECTED`, `INVALID_REQUEST`, `FAILED`.

---

## 3. Test Verification Matrix

All 14 tests in `tests/test_p10_2_h_paper_authorization.py` passed:

| Test ID | Focus Area | Result |
| :--- | :--- | :--- |
| `test_h06_schema_migration_10_applied` | Schema Migration 10, tables, and columns | PASSED |
| `test_h05_decimal_provenance_and_conflict_rejection` | Decimal authority, fail-closed on conflict | PASSED |
| `test_h05_multichain_decimal_scaling` | Multi-chain decimal scaling (6, 8, 9, 18) | PASSED |
| `test_h01_successful_proposal_consumption_and_atomic_ledger` | Atomic proposal execution & ledger posting | PASSED |
| `test_h01_repeated_proposal_execution_idempotency` | Idempotent duplicate proposal execution | PASSED |
| `test_h01_cross_account_proposal_execution_rejection` | Account ownership isolation | PASSED |
| `test_h01_expired_proposal_rejection` | Proposal expiry validation & status transition | PASSED |
| `test_h01_transaction_atomicity_on_ledger_failure` | Rollback atomicity on simulated failure | PASSED |
| `test_h02_manual_orders_cannot_impersonate_strategy` | Reject manual orders with proposal_id or strategy origin | PASSED |
| `test_h03_historical_configuration_snapshot_and_replay_determinism` | Parameter snapshot persistence & replay determinism | PASSED |
| `test_h03_corrupted_config_hash_rejection` | Corrupted config hash detection & rejection | PASSED |
| `test_h04_protected_signals_and_runs_endpoints` | API auth & loopback verification on runs/signals | PASSED |
| `test_h07_strategy_proposal_reconciliation` | Proposal-to-order reconciliation & ledger check | PASSED |
| `test_h08_windmill_wf22_workflow` | Windmill WF-22 workflow status categorization | PASSED |

**Full Suite Result:** 259 passed, 1 skipped in 28.54s (100% passing).
