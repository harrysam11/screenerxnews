# Quota & Budget Control Architecture (P10.2-C.2)

---

## 1. Overview

The Provider Manager implements an atomic two-phase reservation model to guarantee that concurrent asynchronous requests cannot race to exceed rate limits, daily request budgets, or monetary spend ceilings (USD).

---

## 2. SEC-01: Atomic Reservation State Machine

Prior implementations used a check-then-dispatch pattern vulnerable to concurrency race conditions. Under P10.2-C.2, capacity is reserved atomically in SQLite before any network socket or adapter logic is invoked.

### State Transitions

```
                    +---------------+
                    |  (Incoming)   |
                    +-------+-------+
                            |
                            v [BEGIN IMMEDIATE]
                    +---------------+
                    |   RESERVED    |
                    +-------+-------+
                            |
           +----------------+----------------+
           |                |                |
           v (Success)      v (Network Err)  v (Crash/Timeout)
    +---------------++---------------++---------------+
    |   COMMITTED   ||    FAILED     ||    EXPIRED    |
    +---------------+|  or RELEASED  |+---------------+
                     +---------------+
```

### Invariant Calculation Under Lock

Inside a transaction started with `BEGIN IMMEDIATE;`:
1. `committed_requests`: Count of `COMMITTED` requests for `provider_id` today.
2. `committed_spend`: Sum of actual cost USD for `COMMITTED` requests today.
3. `active_reserved_requests`: Count of in-flight `RESERVED` rows created within the reservation TTL (default 60 seconds).
4. `active_reserved_spend`: Sum of `estimated_cost_usd` for in-flight `RESERVED` rows.

The check enforces:
$$\text{Total In-Flight} = \text{committed} + \text{active\_reserved} + \text{new\_request}$$
If $\text{Total In-Flight} > \text{limit}$, the reservation fails immediately with `DAILY_REQUEST_BUDGET_EXCEEDED` or `DAILY_SPEND_BUDGET_EXCEEDED`.

---

## 3. Idempotency & Safe Reconciliation

- Each reservation generates a unique `reservation_id` (e.g., `res-9a8b7c6d...`) and tracks an optional `idempotency_key`.
- Upon successful execution, the worker calls `commit_reservation(reservation_id, actual_cost_usd)`. This transitions the status to `COMMITTED` and appends a row to `provider_usage_records`.
- If an outbound request is rejected before execution (e.g. circuit breaker trip or client abort), `release_reservation(reservation_id)` releases the hold.
- If an outbound request fails during transmission, `fail_reservation(reservation_id, error_message)` marks the reservation `FAILED`.
- Stale reservations exceeding the TTL are treated as `EXPIRED` and ignored during capacity calculations.

---

## 4. Circuit Breakers and Cooldown Transitions

The health state machine operates as follows:
- **HEALTHY (`healthy`)**: All requests succeed. Consecutive failures counter is 0.
- **DEGRADED (`degraded`)**: Transient network failure observed. Consecutive failures counter is incremented.
- **COOLDOWN (`cooldown`)**:
  - Triggered when consecutive failures reach `settings.PROVIDER_CIRCUIT_BREAKER_FAILURES` (default: 3) or upon receiving an HTTP 429.
  - Outbound traffic to this provider is paused until `cooldown_until` expires (`settings.PROVIDER_COOLDOWN_SECONDS`, default: 60s; 120s on HTTP 429).
  - Outgoing requests are automatically routed to an authorized fallback provider if configured.
- **HALF-OPEN RECOVERY**: Once `cooldown_until` has passed, the provider is probed. A single successful request resets `consecutive_failures = 0` and restores state to `healthy`.

---

## 5. Budget Exhaustion Protection Invariant

> [!IMPORTANT]
> A budget exhaustion event on a free or standard provider **never** triggers an unapproved paid fallback. Fallback providers marked with `is_paid = 1` are only selected if explicitly configured and authorized in `provider_fallback_rules`.
