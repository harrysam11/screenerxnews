#!/usr/bin/env python3
"""
Termux / Lightweight Core Benchmark Harness (Phase P10.2-B: TERMUX-02)
Measures cold startup time, memory footprint (RSS), SQLite read/write latency,
and API endpoint responsiveness on self-hosted / constrained hardware.
"""

import os
import sys
import time
import json
import sqlite3
import tempfile
from decimal import Decimal

# Ensure backend is on python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../backend")))

def get_current_rss_mb() -> float:
    """
    Reads resident set size (RSS) directly from /proc/self/status.
    100% Linux and Termux compatible without external libraries.
    """
    try:
        with open("/proc/self/status", "r") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    parts = line.split()
                    kb = float(parts[1])
                    return round(kb / 1024.0, 2)
    except Exception:
        pass

    try:
        import resource
        usage = resource.getrusage(resource.RUSAGE_SELF)
        # On Linux ru_maxrss is in kilobytes
        return round(usage.ru_maxrss / 1024.0, 2)
    except Exception:
        return 0.0

def run_benchmarks() -> dict:
    results = {
        "benchmark_timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "platform": sys.platform,
        "python_version": sys.version.split()[0],
        "metrics": {},
        "budget_checks": {}
    }

    rss_baseline = get_current_rss_mb()

    # 1. Benchmark: Cold Startup & App Import Latency
    t0 = time.perf_counter()
    from app.main import app
    from app.core.config import settings
    from app.db.sqlite_migrator import SqliteMigrator
    from fastapi.testclient import TestClient
    t1 = time.perf_counter()
    startup_latency_ms = round((t1 - t0) * 1000, 2)
    startup_latency_sec = round(t1 - t0, 3)

    rss_after_import = get_current_rss_mb()

    # 2. Benchmark: SQLite Migration & Schema Initialization Latency
    with tempfile.NamedTemporaryFile(suffix=".db") as tmp_db:
        t0 = time.perf_counter()
        migrator = SqliteMigrator(tmp_db.name)
        migrator.apply_migrations()
        t1 = time.perf_counter()
        migration_latency_ms = round((t1 - t0) * 1000, 2)

        # 3. Benchmark: SQLite Write & Read Throughput
        conn = sqlite3.connect(tmp_db.name, timeout=10.0)
        conn.execute("PRAGMA journal_mode = WAL;")

        # Write 250 records
        t0 = time.perf_counter()
        conn.execute("BEGIN IMMEDIATE;")
        for i in range(250):
            conn.execute(
                "INSERT INTO system_audit_log (event_type, details, actor, created_at) VALUES (?, ?, ?, datetime('now'));",
                ("BENCHMARK_EVENT", f"Metric point {i}", "termux_harness")
            )
        conn.execute("COMMIT;")
        t1 = time.perf_counter()
        write_total_ms = (t1 - t0) * 1000
        write_per_op_ms = round(write_total_ms / 250.0, 4)

        # Read 250 records
        t0 = time.perf_counter()
        cursor = conn.execute("SELECT id, event_type, details FROM system_audit_log LIMIT 250;")
        rows = cursor.fetchall()
        t1 = time.perf_counter()
        read_total_ms = (t1 - t0) * 1000
        read_per_op_ms = round(read_total_ms / len(rows), 4) if rows else 0.0
        conn.close()

    # 4. Benchmark: API Endpoint Latency (FastAPI TestClient)
    client = TestClient(app)

    # Warmup
    _ = client.get("/health")

    # Measure /health
    t0 = time.perf_counter()
    res_health = client.get("/health")
    t1 = time.perf_counter()
    health_latency_ms = round((t1 - t0) * 1000, 2)

    # Measure /api/v1/system/status
    t0 = time.perf_counter()
    res_status = client.get("/api/v1/system/status")
    t1 = time.perf_counter()
    status_latency_ms = round((t1 - t0) * 1000, 2)

    # Measure /api/v1/discovery/tokens
    t0 = time.perf_counter()
    res_disc = client.get("/api/v1/discovery/tokens")
    t1 = time.perf_counter()
    discovery_latency_ms = round((t1 - t0) * 1000, 2)

    rss_final = get_current_rss_mb()

    # Metrics Summary
    results["metrics"] = {
        "rss_baseline_mb": rss_baseline,
        "rss_final_mb": rss_final,
        "cold_start_sec": startup_latency_sec,
        "cold_start_ms": startup_latency_ms,
        "migration_ms": migration_latency_ms,
        "sqlite_write_per_op_ms": write_per_op_ms,
        "sqlite_read_per_op_ms": read_per_op_ms,
        "api_health_latency_ms": health_latency_ms,
        "api_system_status_latency_ms": status_latency_ms,
        "api_discovery_latency_ms": discovery_latency_ms
    }

    # Budgets & Assertions (Termux constraints)
    BUDGET_MAX_RSS_MB = 150.0       # Max 150 MB for mobile Termux
    BUDGET_MAX_STARTUP_SEC = 2.0    # Sub-2s cold startup
    BUDGET_MAX_SQLITE_WRITE_MS = 5.0 # Max 5ms per write
    BUDGET_MAX_API_LATENCY_MS = 50.0 # Max 50ms per local request

    results["budget_checks"] = {
        "rss_within_budget": rss_final < BUDGET_MAX_RSS_MB,
        "startup_within_budget": startup_latency_sec < BUDGET_MAX_STARTUP_SEC,
        "sqlite_write_within_budget": write_per_op_ms < BUDGET_MAX_SQLITE_WRITE_MS,
        "api_latency_within_budget": health_latency_ms < BUDGET_MAX_API_LATENCY_MS,
        "all_budgets_passed": (
            rss_final < BUDGET_MAX_RSS_MB and
            startup_latency_sec < BUDGET_MAX_STARTUP_SEC and
            write_per_op_ms < BUDGET_MAX_SQLITE_WRITE_MS and
            health_latency_ms < BUDGET_MAX_API_LATENCY_MS
        )
    }

    return results

if __name__ == "__main__":
    report = run_benchmarks()
    print("=" * 68)
    print(" TERMUX & LIGHTWEIGHT CORE RESOURCE BENCHMARK (P10.2-B)")
    print("=" * 68)
    metrics = report["metrics"]
    budgets = report["budget_checks"]

    print(f"Memory RSS Footprint:      {metrics['rss_final_mb']} MB (Budget: < 150 MB) -> {'PASS' if budgets['rss_within_budget'] else 'FAIL'}")
    print(f"Cold Start Time:           {metrics['cold_start_sec']} s (Budget: < 2.0 s)  -> {'PASS' if budgets['startup_within_budget'] else 'FAIL'}")
    print(f"Schema Migration Latency:  {metrics['migration_ms']} ms")
    print(f"SQLite Write Latency:      {metrics['sqlite_write_per_op_ms']} ms/op (Budget: < 5.0 ms) -> {'PASS' if budgets['sqlite_write_within_budget'] else 'FAIL'}")
    print(f"SQLite Read Latency:       {metrics['sqlite_read_per_op_ms']} ms/op")
    print(f"API /health Latency:       {metrics['api_health_latency_ms']} ms (Budget: < 50.0 ms) -> {'PASS' if budgets['api_latency_within_budget'] else 'FAIL'}")
    print(f"API /discovery Latency:    {metrics['api_discovery_latency_ms']} ms")
    print("=" * 68)
    print(f"Overall Result:            {'ALL BUDGETS PASSED' if budgets['all_budgets_passed'] else 'BUDGET EXCEEDED'}")
    print("=" * 68)
    print("\nStructured JSON Report:")
    print(json.dumps(report, indent=2))
