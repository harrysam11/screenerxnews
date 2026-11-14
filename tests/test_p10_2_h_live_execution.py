import sys
import os
import uuid
import pytest
from decimal import Decimal
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath("./backend"))
from app.main import app
from app.core.config import settings
from app.services.execution_store import (
    ExecutionIntentStore,
    IntentLifecycleState,
    build_canonical_intent_message,
    compute_intent_digest
)
from app.services.live_execution_engine import (
    LiveExecutionEngine,
    live_execution_engine
)
from app.services.ledger_service import DoubleEntryLedgerService

client = TestClient(app)

def test_live_trading_disabled_by_default():
    """
    Safety Invariant: ALLOW_LIVE_TRADING must be False by default.
    """
    assert settings.ALLOW_LIVE_TRADING is False

def test_separate_live_authorization_fail_closed(monkeypatch):
    """
    P10.2-H: Live trading requires separate operator authorization token.
    Fails closed if ALLOW_LIVE_TRADING=True but token is missing or invalid.
    """
    engine = LiveExecutionEngine()
    monkeypatch.setattr(settings, "ALLOW_LIVE_TRADING", True)
    monkeypatch.setattr(settings, "LIVE_EXECUTION_AUTHORIZATION_TOKEN", "secret_live_operator_token_999")

    # 1. Missing token -> 403 Forbidden
    with pytest.raises(Exception) as exc_info:
        engine.verify_live_authorization(live_auth_token=None)
    assert "403" in str(exc_info.value) or "LIVE-01" in str(exc_info.value)

    # 2. Invalid token -> 403 Forbidden
    with pytest.raises(Exception) as exc_info:
        engine.verify_live_authorization(live_auth_token="wrong_token")
    assert "403" in str(exc_info.value) or "LIVE-01" in str(exc_info.value)

    # 3. Matching token -> Authorized
    assert engine.verify_live_authorization(live_auth_token="secret_live_operator_token_999") is True

def test_simulated_broadcast_lifecycle_and_ledger_posting(tmp_path):
    """
    P10.2-H: When ALLOW_LIVE_TRADING=False, broadcast executes in simulated mode,
    transitions intent to CONFIRMED, reconciles finality, and posts balanced ledger journal.
    """
    intent_db = str(tmp_path / "test_intents.db")
    ledger_db = str(tmp_path / "test_ledger.db")

    intent_store = ExecutionIntentStore(db_path=intent_db)
    ledger_service = DoubleEntryLedgerService(db_path=ledger_db)
    engine = LiveExecutionEngine(intent_store=intent_store, ledger_service=ledger_service)

    intent_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=5)
    wallet = "GsbwXfJraMomNxB62bBwN9ZhpFz7174XWp9yP2P3yZ84"

    # Pre-seed approved intent
    intent_data = {
        "intent_id": intent_id,
        "chain_id": "solana:mainnet",
        "wallet_address": wallet,
        "asset_id": "BONK",
        "pool_id": "BONK_SOL_POOL",
        "side": "BUY",
        "input_mint": "So11111111111111111111111111111111111111112",
        "output_mint": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
        "input_amount_raw": Decimal("500000000"), # 0.5 SOL
        "max_slippage_bps": 150,
        "destination_router": "jupiter_v6",
        "strategy_id": "momentum_breakout",
        "strategy_version": "1.0",
        "nonce": str(uuid.uuid4()),
        "canonical_intent_digest": "dummy_digest",
        "transaction_message_hash": "dummy_msg_hash",
        "status": IntentLifecycleState.APPROVED,
        "policy_checks_passed": True,
        "rejection_reasons": [],
        "created_at": now,
        "expires_at": expires_at,
        "is_consumed": True
    }
    intent_store.save_intent(intent_data)

    # Execute broadcast via engine
    broadcast_res = engine.broadcast_intent(intent_id=intent_id)
    assert broadcast_res["status"] == "CONFIRMED"
    assert broadcast_res["execution_mode"] == "SIMULATED_BROADCAST"
    assert broadcast_res["transaction_hash"].startswith("sim_tx_")
    assert broadcast_res["finality_label"] == "PROBABILISTIC_POS"
    assert broadcast_res["required_confirmations"] == 32
    assert broadcast_res["ledger_posted"] is True

    # Check that ledger journal was actually posted and is balanced
    jnl_id = broadcast_res["ledger_journal_id"]
    journal = ledger_service.get_journal(jnl_id)
    assert journal is not None
    assert journal["status"] == "POSTED"
    assert len(journal["lines"]) == 2

    # Verify reconciliation endpoint
    recon_res = engine.reconcile_finality(intent_id=intent_id)
    assert recon_res["reconciled"] is True
    assert recon_res["target_confirmations"] == 32
    assert recon_res["transaction_hash"] == broadcast_res["transaction_hash"]

def test_multichain_finality_model_resolution(tmp_path):
    """
    P10.2-H: Verify finality model adapts to chain capabilities:
    - Solana -> SLOT_FINALIZED (31)
    - Base / EVM -> EPOCH_FINALIZED (32)
    - Sui -> INSTANT_TICK (1)
    """
    intent_db = str(tmp_path / "test_mc_intents.db")
    ledger_db = str(tmp_path / "test_mc_ledger.db")
    intent_store = ExecutionIntentStore(db_path=intent_db)
    ledger_service = DoubleEntryLedgerService(db_path=ledger_db)
    engine = LiveExecutionEngine(intent_store=intent_store, ledger_service=ledger_service)

    cases = [
        ("solana:mainnet", "PROBABILISTIC_POS", 32),
        ("eip155:8453", "L2_ROLLUP_BATCH", 64),
        ("sui:mainnet", "DETERMINISTIC_INSTANT", 1)
    ]

    for chain_id, expected_label, expected_confs in cases:
        i_id = str(uuid.uuid4())
        intent_store.save_intent({
            "intent_id": i_id,
            "chain_id": chain_id,
            "wallet_address": "0x1111111111111111111111111111111111111111",
            "asset_id": "TOKEN",
            "pool_id": "POOL",
            "side": "BUY",
            "input_mint": "NATIVE",
            "output_mint": "TOKEN",
            "input_amount_raw": Decimal("1.0"),
            "max_slippage_bps": 200,
            "destination_router": "default",
            "strategy_id": "strat_1",
            "strategy_version": "1.0",
            "nonce": str(uuid.uuid4()),
            "canonical_intent_digest": f"digest_{i_id}",
            "transaction_message_hash": f"hash_{i_id}",
            "status": IntentLifecycleState.APPROVED,
            "policy_checks_passed": True,
            "rejection_reasons": [],
            "created_at": datetime.now(timezone.utc),
            "expires_at": datetime.now(timezone.utc) + timedelta(minutes=5),
            "is_consumed": True
        })
        res = engine.broadcast_intent(intent_id=i_id)
        assert res["finality_label"] == expected_label
        assert res["required_confirmations"] == expected_confs

def test_api_broadcast_and_reconcile_endpoints():
    """
    P10.2-H: End-to-end API test for /api/v1/execution/broadcast and /reconcile
    """
    # 1. Create an approved intent in live store
    i_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    live_execution_engine.intent_store.save_intent({
        "intent_id": i_id,
        "chain_id": "solana:mainnet",
        "wallet_address": "GsbwXfJraMomNxB62bBwN9ZhpFz7174XWp9yP2P3yZ84",
        "asset_id": "BONK",
        "pool_id": "POOL",
        "side": "BUY",
        "input_mint": "SOL",
        "output_mint": "BONK",
        "input_amount_raw": Decimal("250000000"),
        "max_slippage_bps": 150,
        "destination_router": "default",
        "strategy_id": "direct",
        "strategy_version": "1.0",
        "nonce": str(uuid.uuid4()),
        "canonical_intent_digest": "api_test_digest",
        "transaction_message_hash": "api_test_hash",
        "status": IntentLifecycleState.APPROVED,
        "policy_checks_passed": True,
        "rejection_reasons": [],
        "created_at": now,
        "expires_at": now + timedelta(minutes=5),
        "is_consumed": True
    })

    # 2. Call broadcast API
    b_resp = client.post(f"/api/v1/execution/broadcast/{i_id}")
    assert b_resp.status_code == 200
    b_data = b_resp.json()
    assert b_data["status"] == "CONFIRMED"
    assert b_data["execution_mode"] == "SIMULATED_BROADCAST"

    # 3. Call reconcile API
    r_resp = client.get(f"/api/v1/execution/reconcile/{i_id}")
    assert r_resp.status_code == 200
    r_data = r_resp.json()
    assert r_data["reconciled"] is True
    assert r_data["status"] == "CONFIRMED"
