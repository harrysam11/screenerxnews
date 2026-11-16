# Phase P10.2-F: Persistent Multi-Chain Paper Trading Engine

**Repository:** `harrysam11/screenerxnews`  
**Phase:** P10.2-F  
**Status:** Implemented & Verified  
**Baseline:** P10.2-E (`feat/p10.2-c.2-security-remediation`)  
**Live Trading Flag:** `ALLOW_LIVE_TRADING=False` (Strictly Enforced)

---

## 1. Executive Summary

Phase P10.2-F delivers a persistent, restart-safe, multi-chain paper trading engine that replaces legacy ephemeral in-memory state with a transactional SQLite-backed core. Backed by discovered assets, DEX pools, and market-data observations from Phase P10.2-E, the engine enables full-fidelity simulation across all 12 registered networks without requiring external API dependencies or incurring real capital risk.

All paper trading operations are executed within isolated, chain-specific virtual desks and conform strictly to double-entry ledger invariants, durable risk controls, and automated portfolio reconciliation.

---

## 2. Paper Trading Architecture

### 2.1 Complete Order Lifecycle

The engine implements an end-to-end simulated trading pipeline:

```
[Strategy Signal / User Request]
              │
              ▼
    [Solvency Validation] ──(Insufficient Balance)──► [REJECTED]
              │
              ▼
   [Idempotency Check] ────(Duplicate Client ID)───► [REJECTED]
              │
              ▼
   [Pool Invalidation Check] (Reorged / Inactive)──► [REJECTED]
              │
              ▼
   [Market Data Freshness] ─(Snapshot Age > 300s)──► [REJECTED]
              │
              ▼
    [Durable Risk Engine] ──(Risk Limit Exceeded)──► [REJECTED]
              │
              ▼
    [Order Type Handler]
       ├─ LIMIT (Price unmet) ─────────────────────► [SUBMITTED]
       │                                                 │ (Cancel Request)
       │                                                 ▼
       │                                            [CANCELLED]
       ▼
  [Market Fill Execution]
       ├─ Price impact & simulated slippage calculation
       ├─ Position cost basis and token balance update
       ├─ Account free & locked cash update
       ├─ Record Order (`paper_orders_v2`) & Fill (`paper_fills`)
       ├─ Post Double-Entry Journal (`DoubleEntryLedgerService`)
       ▼
   [Portfolio Reconciliation]
       └─ Ending Equity == Free Cash + Positions Market Value
```

---

## 3. Database Schema (Migration 7)

Migration `007_persistent_multichain_paper_trading` introduces 9 dedicated relational tables within the canonical SQLite database (`memecoin_os.db`):

| Table Name | Primary Key | Description |
|---|---|---|
| `paper_portfolios` | `portfolio_id` | Top-level portfolio containers grouping multi-chain simulated desks. |
| `paper_account_balances` | `id` | Exact raw unit balances per asset, chain, and account with locked & free allocations. |
| `paper_orders_v2` | `order_id` | Persistent order registry tracking status, requested/filled quantities, and slippage. |
| `paper_fills` | `fill_id` | Individual execution fill events linked to orders with executed price and fees. |
| `paper_positions_v2` | `position_id` | Open and closed positions with average cost basis, token quantities, and PnL. |
| `paper_risk_limits` | `id` | Global and chain-specific risk limits (order value, position size, concurrent positions). |
| `paper_portfolio_valuations` | `id` | Point-in-time valuation snapshots across all active trading desks. |
| `paper_reconciliation_records` | `id` | Audit trails of periodic portfolio reconciliation checks and balance assertions. |
| `paper_audit_log` | `id` | Structured audit log of simulated desk lifecycle events. |

### 3.1 Initial Desk Provisioning (12 Chains)

All 12 registered networks are pre-seeded with virtual capital during Migration 7 and guaranteed on service startup:

| Chain ID | CAIP-2 Identity | Native Token | Initial Allocation | Native USD Rate | Initial Virtual Equity |
|---|---|---|---|---|---|
| Solana | `solana:mainnet` | SOL | 100.0 SOL | $200.00 | $20,000.00 |
| Base | `eip155:8453` | ETH | 10.0 ETH | $3,000.00 | $30,000.00 |
| Ethereum | `eip155:1` | ETH | 10.0 ETH | $3,000.00 | $30,000.00 |
| BNB Chain | `eip155:56` | BNB | 50.0 BNB | $600.00 | $30,000.00 |
| Arbitrum One | `eip155:42161` | ETH | 10.0 ETH | $3,000.00 | $30,000.00 |
| Optimism | `eip155:10` | ETH | 10.0 ETH | $3,000.00 | $30,000.00 |
| Polygon PoS | `eip155:137` | POL | 10,000.0 POL | $0.50 | $5,000.00 |
| Avalanche C-Chain | `eip155:43114` | AVAX | 500.0 AVAX | $35.00 | $17,500.00 |
| Sui | `sui:mainnet` | SUI | 10,000.0 SUI | $3.00 | $30,000.00 |
| Aptos | `aptos:mainnet` | APT | 1,000.0 APT | $10.00 | $10,000.00 |
| TON | `ton:mainnet` | TON | 2,000.0 TON | $5.00 | $10,000.00 |
| TRON | `tron:mainnet` | TRX | 50,000.0 TRX | $0.20 | $10,000.00 |

---

## 4. Arithmetic & Accounting Invariants

### 4.1 Decimal-Safe Fixed-Point Math

- All token quantities, balances, and prices use Python `Decimal` objects.
- Binary floating-point operations (`float`) are strictly forbidden for balance, position, and ledger calculations.
- Token decimals are respected: EVM tokens default to 18 decimals, Solana to 9 decimals.
- Exact raw unit conservation guarantees that no balance drift occurs over repeated buy and sell cycles.

### 4.2 AMM Price Impact & Slippage Modeling

Execution price simulates constant-product AMM mechanics:

$$\text{Price Impact (bps)} = \min\left(2000, \max\left(5, \left\lfloor \frac{\text{Trade USD}}{\text{Pool Liquidity USD}} \times 10000 \right\rfloor\right)\right)$$

$$\text{Effective BUY Price} = \text{Market Price} \times \left(1 + \frac{\text{Total Slippage Bps}}{10000}\right)$$

$$\text{Effective SELL Price} = \frac{\text{Market Price}}{1 + \frac{\text{Total Slippage Bps}}{10000}}$$

Orders exceeding `max_slippage_bps` are rejected immediately prior to balance deductions.

---

## 5. Double-Entry Financial Ledger Integration

Every simulated fill automatically writes a balanced financial journal entry via `DoubleEntryLedgerService`:

- **BUY Journal:**
  - `CREDIT` Cash Account (`WALLET_SOL` / `WALLET_NATIVE`): Amount in native token @ USD rate.
  - `DEBIT` Token Account (`WALLET_TOKEN`): Total USD value @ $1.00 USD rate.
- **Trigger Compliance:**
  - Passes SQLite triggers `trg_prevent_direct_posted_insert`, `trg_enforce_valid_status_transition`, and `trg_check_journal_post_invariants`.
  - Exact integer unit balance is preserved: $\sum (\text{debit\_units} - \text{credit\_units}) = 0$.

---

## 6. Durable Risk Controls

Risk boundaries are stored in `paper_risk_limits` and evaluated synchronously within the execution transaction:

1. **Max Single Order Value:** Rejects orders exceeding `max_order_value_usd` (default: $50,000.00).
2. **Max Position Size:** Restricts individual position accumulations to `max_position_size_usd` (default: $100,000.00).
3. **Max Concurrent Positions:** Restricts open positions per desk to `max_concurrent_positions` (default: 20).
4. **Stale Market Data Guard:** Rejects execution if the latest snapshot in `market_data_snapshots` is older than `stale_data_threshold_seconds` (default: 300s).
5. **Reorg Invalidation Guard:** Immediately rejects orders attempting to execute against pools marked `status != 'ACTIVE'` or `is_canonical = 0`.
6. **Dynamic Reconfiguration:** Limits can be updated at runtime via `PUT /api/v1/paper/risk` globally or per chain.

---

## 7. Portfolio Reconciliation

The engine supports automated mathematical reconciliation:

$$\text{Ending Equity} = \text{Free Cash (USD)} + \sum (\text{Position Tokens} \times \text{Current Price} \times \text{Native USD Rate})$$

$$\text{Discrepancy} = |\text{Ending Equity} - \text{Calculated Equity}|$$

- If $\text{Discrepancy} = 0$, `is_balanced = 1`.
- Every reconciliation execution generates an audit record in `paper_reconciliation_records`.

---

## 8. REST API Reference

All responses carry the mandatory simulated environment marker: `"environment": "PAPER_SIMULATED"`.

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/v1/paper/account` | Account details for a single chain desk. |
| `GET` | `/api/v1/paper/accounts` | Overview of all 12 chain desks with valuations and PnL. |
| `POST` | `/api/v1/paper/order` | Submits a market or limit paper order. |
| `GET` | `/api/v1/paper/orders` | Historical and open orders with status and chain filtering. |
| `POST` | `/api/v1/paper/orders/{order_id}/cancel` | Cancels an open or submitted limit order. |
| `GET` | `/api/v1/paper/positions` | Active open positions for a specified chain desk. |
| `GET` | `/api/v1/paper/fills` | Execution fill audit log with pagination. |
| `GET` | `/api/v1/paper/valuation` | Aggregated portfolio valuation across all 12 networks. |
| `GET` | `/api/v1/paper/risk` | Active risk parameters and limits. |
| `PUT` | `/api/v1/paper/risk` | Dynamically updates risk management guardrails. |
| `POST` | `/api/v1/paper/reconcile` | Runs portfolio reconciliation and records results. |

---

## 9. Android Termux & Linux Workstation Support

- Powered exclusively by standard library `sqlite3` and pure-Python fixed-point math.
- Zero reliance on Docker, Redis, PostgreSQL, or external cloud services.
- Configured with `PRAGMA journal_mode = WAL` and `PRAGMA busy_timeout = 30000` to prevent database locking on mobile flash storage and concurrent threads.
