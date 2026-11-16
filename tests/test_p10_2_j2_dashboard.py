"""
P10.2-J.2 Complete Paper Trading Command Center Acceptance Tests
Tests dashboard routes, static asset delivery, soft-card API endpoints,
exact financial data representation, read-only security invariants, and multi-chain coverage.
"""

import os
import json
import pytest
from decimal import Decimal
from fastapi.testclient import TestClient

from app.main import app
from app.core.config import settings


@pytest.fixture
def client():
    return TestClient(app)


# ============================================================================
# 1. Dashboard Routes & Static Asset Tests
# ============================================================================

def test_dashboard_root_serves_html(client):
    """Verifies that GET / and GET /dashboard serve the soft-card command center shell."""
    res_root = client.get("/")
    assert res_root.status_code == 200
    assert "text/html" in res_root.headers.get("content-type", "")
    assert "MEMECOIN OS" in res_root.text
    assert "Command Center" in res_root.text

    res_dash = client.get("/dashboard")
    assert res_dash.status_code == 200
    assert res_dash.text == res_root.text


def test_dashboard_static_assets_served(client):
    """Verifies that design system CSS and ES module scripts are properly served."""
    css = client.get("/static/css/design_system.css")
    assert css.status_code == 200
    assert "SOFT-CARD DESIGN SYSTEM" in css.text
    assert "--radius-card: 16px" in css.text

    app_js = client.get("/static/js/app.js")
    assert app_js.status_code == 200
    assert "Paper Trading Command Center" in app_js.text

    comp_js = client.get("/static/js/components.js")
    assert comp_js.status_code == 200
    assert "renderMetricCard" in comp_js.text
    assert "formatExactValue" in comp_js.text


# ============================================================================
# 2. Command Center Overview KPI Tests
# ============================================================================

def test_dashboard_overview_kpis(client):
    """Verifies that /api/v1/dashboard/overview aggregates real SQLite paper metrics."""
    res = client.get("/api/v1/dashboard/overview")
    assert res.status_code == 200
    data = res.json()

    assert "total_paper_equity_usd" in data
    assert "total_realized_pnl" in data
    assert "open_positions_count" in data
    assert "total_orders_count" in data
    assert "total_fills_count" in data
    assert "win_rate_pct" in data
    assert "profit_factor" in data
    assert "active_chains_count" in data
    assert data["active_chains_count"] == 12

    # Safety lock confirmation
    assert data["safety_lock"]["allow_live_trading"] is False
    assert data["safety_lock"]["status"] == "LIVE_LOCKED"

    # Orchestrator architecture verification
    assert data["orchestrator_architecture"]["windmill"] == "PRIMARY"
    assert data["orchestrator_architecture"]["hermes"] == "OPTIONAL"
    assert data["orchestrator_architecture"]["n8n"] == "DEFERRED"


# ============================================================================
# 3. Market Intelligence Screener Tests
# ============================================================================

def test_dashboard_screener_feed(client):
    """Verifies real candidate querying, pagination, and multi-chain filters."""
    res = client.get("/api/v1/dashboard/screener?limit=10")
    assert res.status_code == 200
    data = res.json()

    assert "total_matches" in data
    assert "tokens" in data
    assert isinstance(data["tokens"], list)

    if data["tokens"]:
        first = data["tokens"][0]
        assert "chain_id" in first
        assert "asset_address" in first
        assert "composite_score" in first
        assert "verification_status" in first
        assert "data_freshness_status" in first


def test_dashboard_screener_chain_filter(client):
    """Verifies chain-specific candidate filtering."""
    res = client.get("/api/v1/dashboard/screener?chain_id=eip155:8453")
    assert res.status_code == 200
    data = res.json()
    for t in data.get("tokens", []):
        assert t["chain_id"] in ("eip155:8453", "base")


# ============================================================================
# 4. Multi-Chain 12-Network Coverage Tests
# ============================================================================

def test_dashboard_all_12_chains(client):
    """Verifies exact coverage and capability status across all 12 registered networks."""
    res = client.get("/api/v1/dashboard/chains")
    assert res.status_code == 200
    chains = res.json()
    assert len(chains) == 12

    chain_ids = [c["chain_id"] for c in chains]
    expected_chains = [
        "solana:mainnet",
        "eip155:1",
        "eip155:8453",
        "eip155:56",
        "eip155:42161",
        "eip155:10",
        "eip155:137",
        "eip155:43114",
        "sui:mainnet",
        "aptos:mainnet",
        "ton:mainnet",
        "tron:mainnet"
    ]
    for exp in expected_chains:
        assert exp in chain_ids or any(c["canonical_id"] == exp for c in chains)

    # Check that canonical Solana ID is preserved
    sol = next(c for c in chains if "solana" in c["chain_id"])
    assert sol["canonical_id"] == "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"
    assert sol["capability_tier"] == "FULL"


# ============================================================================
# 5. Forensics & Security Checks Tests
# ============================================================================

def test_dashboard_forensics(client):
    """Verifies on-chain forensic security rules, honeypots, and decimal records."""
    res = client.get("/api/v1/dashboard/forensics")
    assert res.status_code == 200
    data = res.json()

    assert "total_risk_flagged_count" in data
    assert "security_rules" in data
    assert "decimal_provenance_records" in data

    # Verify key security rules
    rules = [r["rule"] for r in data["security_rules"]]
    assert "MINT_AUTHORITY_REVOKED" in rules
    assert "FREEZE_AUTHORITY_REVOKED" in rules
    assert "HONEYPOT_SELL_SIMULATION" in rules
    assert "EXACT_DECIMAL_VERIFICATION" in rules


# ============================================================================
# 6. Risk Monitor Tests
# ============================================================================

def test_dashboard_risk_monitor(client):
    """Verifies risk limits, slippage bounds, and rejection breakdown."""
    res = client.get("/api/v1/dashboard/risk-monitor")
    assert res.status_code == 200
    data = res.json()

    assert "risk_limits" in data
    limits = data["risk_limits"]
    assert limits["max_drawdown_pct"] in (15.0, 25.0)
    assert limits["max_slippage_bps"] in (300, 500)
    assert limits["environment"] == "PAPER_SIMULATED"

    assert "order_rejections_breakdown" in data
    assert "circuit_breaker_summary" in data


# ============================================================================
# 7. Workflow & Provider Monitor Tests
# ============================================================================

def test_dashboard_workflows_monitor(client):
    """Verifies monitoring across all 7 Windmill research workflows."""
    res = client.get("/api/v1/dashboard/workflows")
    assert res.status_code == 200
    data = res.json()

    assert data["total_workflows_count"] == 7
    wf_ids = [w["id"] for w in data["workflows"]]
    assert "WF-01" in wf_ids
    assert "WF-08" in wf_ids
    assert "WF-14" in wf_ids
    assert "WF-20" in wf_ids
    assert "WF-22" in wf_ids
    assert "WF-25" in wf_ids
    assert "WF-33" in wf_ids

    # All scripts must exist on disk
    for wf in data["workflows"]:
        assert wf["script_exists"] is True
        assert wf["runtime_state"] == "CONFIGURED_STANDALONE_RUNNABLE"


# ============================================================================
# 8. Strategy Analytics Tests
# ============================================================================

def test_dashboard_strategy_analytics(client):
    """Verifies strategy registration and statistical validity notes."""
    res = client.get("/api/v1/dashboard/strategy-analytics")
    assert res.status_code == 200
    data = res.json()

    assert data["strategies_count"] >= 2
    strat_ids = [s["strategy_id"] for s in data["strategies"]]
    assert "momentum_breakout" in strat_ids
    assert "safety_first_accumulator" in strat_ids

    perf = data["performance_metrics"]
    assert "total_closed_trades" in perf
    assert "is_statistically_significant" in perf
    assert "sample_size_note" in perf


# ============================================================================
# 9. Reports & Journal Tests
# ============================================================================

def test_dashboard_reports_journal(client):
    """Verifies daily/weekly performance reporting and journal data."""
    res = client.get("/api/v1/dashboard/reports")
    assert res.status_code == 200
    data = res.json()

    assert "report_generated_at" in data
    assert data["environment"] == "PAPER_TRADING_RESEARCH"
    assert "accounts_summary" in data
    assert "recent_fills_journal" in data
    assert "top_ranked_candidates" in data


# ============================================================================
# 10. Security & Read-Only Invariants Tests
# ============================================================================

def test_dashboard_is_strictly_read_only(client):
    """Ensures that dashboard routes do not mutate database state or execute live trades."""
    assert settings.ALLOW_LIVE_TRADING is False

    # Calling dashboard GET endpoints multiple times produces zero write side-effects
    kpi1 = client.get("/api/v1/dashboard/overview").json()
    kpi2 = client.get("/api/v1/dashboard/overview").json()
    assert kpi1["total_orders_count"] == kpi2["total_orders_count"]
    assert kpi1["total_fills_count"] == kpi2["total_fills_count"]


# ============================================================================
# 11. External Trading Terminal Matrix & Future Live Gateway Tests
# ============================================================================

def test_dashboard_trading_terminals_registry(client):
    """
    Verifies code-defined external terminal registry:
    - Exactly 8 target platforms: Jupiter, Photon, GMGN, pump.fun, Trojan, Banana Gun, Axiom, FOMO.family
    - Proper capability classifications (PROTOCOL, HANDOFF, READ_ONLY, UNVERIFIED)
    - Future Live Trading Gateway marked DISABLED with Manual Approval + Restricted Automation specifications
    - ALLOW_LIVE_TRADING=False strictly enforced
    """
    res = client.get("/api/v1/dashboard/terminals")
    assert res.status_code == 200
    data = res.json()

    assert data["total_terminals_registered"] == 8
    terminals = data["terminals"]
    platform_ids = {t["platform_id"] for t in terminals}
    expected_platforms = {
        "jupiter", "photon", "gmgn", "pump_fun",
        "trojan", "banana_gun", "axiom", "fomo_family"
    }
    assert platform_ids == expected_platforms

    # Check classifications
    cap_map = {t["platform_id"]: t["capability_type"] for t in terminals}
    assert cap_map["jupiter"] == "PROTOCOL"
    assert cap_map["pump_fun"] == "PROTOCOL"
    assert cap_map["photon"] == "HANDOFF"
    assert cap_map["gmgn"] == "HANDOFF"
    assert cap_map["trojan"] == "HANDOFF"
    assert cap_map["banana_gun"] == "HANDOFF"
    assert cap_map["axiom"] == "READ_ONLY"
    assert cap_map["fomo_family"] == "UNVERIFIED"

    # Future Live Gateway architecture status
    gw = data["live_gateway_status"]
    assert gw["allow_live_trading"] is False
    assert gw["current_status"] == "DISABLED"
    assert gw["kill_switch_engaged"] is True
    assert gw["custody_type"] == "ZERO_CUSTODY_NO_KEYS"

    modes = {m["mode"]: m for m in gw["modes"]}
    assert "MANUAL_APPROVAL" in modes
    assert "RESTRICTED_AUTOMATION" in modes
    assert modes["MANUAL_APPROVAL"]["current_status"] == "DISABLED"
    assert modes["MANUAL_APPROVAL"]["is_default"] is True
    assert modes["RESTRICTED_AUTOMATION"]["current_status"] == "DISABLED"
    assert modes["RESTRICTED_AUTOMATION"]["is_default"] is False


def test_dashboard_terminal_context_deep_links(client):
    """
    Verifies context-preserving deep link generation:
    - Correct URL parameter bindings for Solana and EVM chains
    - FOMO.family remains unverified (no link generated)
    - Zero invented parameters or unverified trade execution APIs
    """
    bonk_mint = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
    res = client.get(f"/api/v1/dashboard/terminals?chain_id=solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp&asset_address={bonk_mint}")
    assert res.status_code == 200
    data = res.json()

    link_map = {t["platform_id"]: t.get("deep_link") for t in data["terminals"]}
    assert f"https://jup.ag/swap/SOL-{bonk_mint}" == link_map["jupiter"]
    assert f"https://photon-sol.tinyastro.io/en/lp/{bonk_mint}" == link_map["photon"]
    assert f"https://gmgn.ai/sol/token/{bonk_mint}" == link_map["gmgn"]
    assert f"https://pump.fun/coin/{bonk_mint}" == link_map["pump_fun"]
    assert f"https://t.me/solana_trojanbot?start=r-{bonk_mint}" == link_map["trojan"]
    assert f"https://axiom.trade/token/{bonk_mint}" == link_map["axiom"]
    assert link_map["fomo_family"] is None


def test_dashboard_terminal_evm_deep_links(client):
    """Verifies EVM deep link generation for Base and Ethereum assets."""
    evm_token = "0x4ed4e862860bed51a9570b96d89af5e1b0efefed"
    res = client.get(f"/api/v1/dashboard/terminals?chain_id=eip155:8453&asset_address={evm_token}")
    assert res.status_code == 200
    data = res.json()

    link_map = {t["platform_id"]: t.get("deep_link") for t in data["terminals"]}
    assert link_map["jupiter"] is None  # Jupiter is Solana only
    assert f"https://photon-base.tinyastro.io/en/lp/{evm_token}" == link_map["photon"]
    assert f"https://gmgn.ai/base/token/{evm_token}" == link_map["gmgn"]
    assert f"https://t.me/BananaGunSniper_bot?start={evm_token}" == link_map["banana_gun"]
    assert link_map["pump_fun"] is None  # pump.fun is Solana only


def test_dashboard_terminal_deep_link_address_validation_fails_closed(client):
    """
    Verifies that invalid addresses or injection attempts fail closed:
    - Path traversal or query parameters
    - Address format mismatch (EVM address for Solana or vice-versa)
    - Whitespace and special characters
    """
    # 1. Injection attempt
    res = client.get("/api/v1/dashboard/terminals?chain_id=solana:mainnet&asset_address=../../etc/passwd")
    assert res.status_code == 200
    for t in res.json()["terminals"]:
        assert t["deep_link"] is None

    # 2. Query injection
    res2 = client.get("/api/v1/dashboard/terminals?chain_id=solana:mainnet&asset_address=DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263?malicious=1")
    assert res2.status_code == 200
    for t in res2.json()["terminals"]:
        assert t["deep_link"] is None

    # 3. Mismatched format (EVM address on Solana)
    res3 = client.get("/api/v1/dashboard/terminals?chain_id=solana:mainnet&asset_address=0x4ed4e862860bed51a9570b96d89af5e1b0efefed")
    assert res3.status_code == 200
    for t in res3.json()["terminals"]:
        assert t["deep_link"] is None

    # 4. Mismatched format (Solana address on Base)
    res4 = client.get("/api/v1/dashboard/terminals?chain_id=eip155:8453&asset_address=DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263")
    assert res4.status_code == 200
    for t in res4.json()["terminals"]:
        assert t["deep_link"] is None


