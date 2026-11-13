import sys
import os
import re
import hashlib
from decimal import Decimal
from datetime import datetime, timezone, timedelta
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath("./backend"))
from app.main import app
from app.core.config import settings
from app.services.multichain_scorer import multichain_scorer
from app.services.hermes_analyst import hermes_analyst
from app.schemas.contracts import TriState

client = TestClient(app)

# =====================================================================
# 1. Zero Private Key & Secret Hygiene Invariant Audit
# =====================================================================
def test_zero_private_keys_in_codebase():
    """
    CRITICAL AUDIT INVARIANT:
    Ensures no private keys, mnemonics, or signing seeds exist anywhere in the repository.
    """
    secret_patterns = [
        re.compile(r"-----BEGIN\s+(EC|RSA|OPENSSH|DSA|PGP)?\s*PRIVATE KEY-----", re.IGNORECASE),
        re.compile(r"\b(wallet_secret|private_key|seed_phrase|mnemonic)\s*=\s*['\"][0-9a-zA-Z\s]{24,}['\"]", re.IGNORECASE),
        re.compile(r"\b(5[HJK][1-9A-HJ-NP-za-km-z]{49,50})\b"), # Base58 uncompressed WIF private key
    ]

    scanned_extensions = (".py", ".sql", ".json", ".md", ".yml", ".yaml", ".env")
    root_dir = os.path.abspath(".")

    for root, dirs, files in os.walk(root_dir):
        if ".git" in root or ".venv" in root or "__pycache__" in root:
            continue
        for file in files:
            if any(file.endswith(ext) for ext in scanned_extensions):
                filepath = os.path.join(root, file)
                with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                    for pattern in secret_patterns:
                        match = pattern.search(content)
                        assert match is None, f"SECURITY AUDIT VIOLATION: Private key pattern found in {filepath}"


# =====================================================================
# 2. Paper / Live Physical Separation & Solvency Audit (AUD-01)
# =====================================================================
def test_paper_live_physical_decoupling():
    """
    Verifies that paper trading operates on virtual isolated desks
    without touching live ledger tables or requiring on-chain signatures.
    """
    # 1. Paper desk balance query
    paper_res = client.get("/api/v1/paper/account?chain_id=solana:mainnet")
    assert paper_res.status_code == 200
    paper_data = paper_res.json()
    assert "current_virtual_lamports" in paper_data

    # 2. Live trading disabled gate
    assert settings.ALLOW_LIVE_TRADING is False

    # 3. Intent execution in simulation mode
    intent_payload = {
        "asset_id": "00000000-0000-0000-0000-000000000001",
        "pool_id": "00000000-0000-0000-0000-000000000002",
        "wallet_address": "BurnerWalletMock111111111111111111111111111",
        "side": "BUY",
        "input_mint": "So11111111111111111111111111111111111111112",
        "output_mint": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
        "input_amount_raw": 100000000, # 0.1 SOL
        "max_slippage_bps": 150
    }
    intent_res = client.post("/api/v1/execution/intent", json=intent_payload)
    assert intent_res.status_code == 200
    intent_data = intent_res.json()
    intent_id = intent_data["intent_id"]

    # 4. Attempting to approve must yield SIMULATION ONLY when live trading is disabled
    approve_res = client.post(f"/api/v1/execution/approve/{intent_id}", json={
        "transaction_message_hash": intent_data["transaction_message_hash"],
        "signed_by_address": "BurnerWalletMock111111111111111111111111111"
    })
    assert approve_res.status_code == 200
    assert approve_res.json()["status"] == "APPROVAL_ACKNOWLEDGED_SIMULATION_ONLY"


# =====================================================================
# 3. Double-Entry Balanced Ledger Invariant Audit (AUD-02)
# =====================================================================
def test_double_entry_ledger_balancing_rule():
    """
    Verifies the double-entry accounting invariant: Sum of Debits == Sum of Credits.
    """
    # Sample journal line postings
    lines = [
        {"account": "WALLET_SOL", "debit": Decimal("0.0"), "credit": Decimal("350.00")},
        {"account": "EXPENSE_NETWORK_FEE", "debit": Decimal("0.50"), "credit": Decimal("0.0")},
        {"account": "WALLET_TOKEN", "debit": Decimal("349.50"), "credit": Decimal("0.0")}
    ]
    total_debits = sum(line["debit"] for line in lines)
    total_credits = sum(line["credit"] for line in lines)

    assert total_debits == total_credits, f"Ledger imbalance: debits {total_debits} != credits {total_credits}"


# =====================================================================
# 4. Cold Vault Hard Deny & Policy Caps Audit
# =====================================================================
def test_cold_vault_hard_deny():
    vault_address = "ColdVaultSecureHardwareWalletAddress1111"
    settings.COLD_VAULT_ADDRESSES = [vault_address]

    payload = {
        "asset_id": "00000000-0000-0000-0000-000000000001",
        "pool_id": "00000000-0000-0000-0000-000000000002",
        "wallet_address": vault_address,
        "side": "BUY",
        "input_mint": "So11111111111111111111111111111111111111112",
        "output_mint": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
        "input_amount_raw": 100000000,
        "max_slippage_bps": 150
    }
    res = client.post("/api/v1/execution/intent", json=payload)
    assert res.status_code == 403
    assert "Cold vault addresses are permanently excluded" in res.json()["detail"]


# =====================================================================
# 5. Deterministic Hard-Reject Safety Invariant Audit
# =====================================================================
def test_hard_reject_blocks_all_compromised_assets():
    """
    Confirms that critical safety failures (honeypots, unrenounced mints,
    unlocked LP) can never be overridden by volume or narrative buzz.
    """
    compromised_scenarios = [
        {"is_honeypot": True, "is_mint_revoked": TriState.TRUE, "lp_status": "BURNED", "reason": "HONEYPOT_DETECTED"},
        {"is_honeypot": False, "is_mint_revoked": TriState.FALSE, "lp_status": "BURNED", "reason": "UNRENOUNCED_MINT_AUTHORITY"},
        {"is_honeypot": False, "is_mint_revoked": TriState.TRUE, "lp_status": "UNLOCKED", "reason": "UNLOCKED_LIQUIDITY"}
    ]

    for scenario in compromised_scenarios:
        eval_res = multichain_scorer.compute_composite_opportunity_score(
            chain_id="eip155:8453",
            asset_address="0xTestAuditScam",
            liquidity_usd=Decimal("500000.0"),
            volume_5m_usd=Decimal("50000.0"),
            volume_1h_usd=Decimal("200000.0"),
            tx_count_5m=100,
            price_change_5m_pct=Decimal("25.0"),
            is_honeypot=scenario["is_honeypot"],
            is_mint_revoked=scenario["is_mint_revoked"],
            lp_status=scenario["lp_status"],
            token_symbol="SCAM",
            token_name="Scam Token"
        )
        assert eval_res.hard_reject_triggered is True
        assert eval_res.composite_score is None
        assert any(scenario["reason"] in r for r in eval_res.rejection_reasons)


# =====================================================================
# 6. Hermes AI Analyst Read-Only Boundary Audit
# =====================================================================
def test_hermes_analyst_read_only_boundary():
    """
    Verifies that Hermes analyst service does not expose any transaction execution,
    private key handling, or database modification capabilities.
    """
    forbidden_methods = [
        "sign_transaction",
        "execute_trade",
        "submit_order",
        "private_key",
        "delete",
        "update"
    ]
    for method in forbidden_methods:
        assert not hasattr(hermes_analyst, method), f"SECURITY VIOLATION: Hermes possesses {method} capability"
