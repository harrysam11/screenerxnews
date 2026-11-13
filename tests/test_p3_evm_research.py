import sys
import os
import pytest
from decimal import Decimal
from datetime import datetime, timezone

sys.path.insert(0, os.path.abspath("./backend"))
from app.services.evm_rpc import EvmRpcClient
from app.services.evm_provider_manager import evm_provider_manager
from app.services.evm_transaction_parser import evm_transaction_parser
from app.services.evm_log_indexer import evm_log_indexer
from app.services.evm_pool_discovery import evm_pool_discovery
from app.services.evm_token_discovery import evm_token_discovery
from app.services.evm_block_indexer import evm_block_indexer
from app.services.evm_market_enricher import evm_market_enricher

def test_invalid_chain_id_rejected():
    # Test 1: Invalid chain ID raises ValueError
    with pytest.raises(ValueError):
        EvmRpcClient("eip155:9999999")

def test_provider_manager_cooldown_and_failover():
    # Test 2 & 3: RPC failure and HTTP 429 triggers cooldown and rotates to backup
    chain = "eip155:8453"
    primary_ep = evm_provider_manager.get_active_endpoint(chain)
    assert primary_ep == "https://mainnet.base.org"

    # Simulate HTTP 429 on primary
    evm_provider_manager.record_failure(chain, primary_ep, is_rate_limit=True)
    assert evm_provider_manager.providers[chain][0].cooldown_until > 0

    # Secondary should now be active
    secondary_ep = evm_provider_manager.get_active_endpoint(chain)
    assert secondary_ep == "https://base.llamarpc.com"

    # Reset
    evm_provider_manager.record_success(chain, primary_ep)

def test_erc20_receipt_and_transfer_parsing():
    # Test 4: Receipt parsing extracts ERC-20 transfers
    mock_receipt = {
        "transactionHash": "0xabc123",
        "blockNumber": "0x100",
        "from": "0x1111111111111111111111111111111111111111",
        "to": "0x2222222222222222222222222222222222222222",
        "status": "0x1",
        "gasUsed": "0x5208", # 21000
        "effectiveGasPrice": "0x3b9aca00", # 1 Gwei
        "logs": [
            {
                "address": "0x3333333333333333333333333333333333333333",
                "topics": [
                    "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef", # Transfer
                    "0x0000000000000000000000001111111111111111111111111111111111111111",
                    "0x0000000000000000000000002222222222222222222222222222222222222222"
                ],
                "data": "0x0000000000000000000000000000000000000000000000000de0b6b3a7640000", # 1 ether
                "logIndex": "0x0"
            }
        ]
    }
    parsed = evm_transaction_parser.parse_receipt("eip155:8453", mock_receipt)
    assert parsed["is_success"] is True
    assert len(parsed["transfers"]) == 1
    tr = parsed["transfers"][0]
    assert tr["amount_raw"] == 1000000000000000000
    assert tr["from"] == "0x1111111111111111111111111111111111111111"

def test_evm_log_indexer_pair_created():
    # Test 5 & 8: Log indexer parses Uniswap V2 PairCreated event
    mock_log = {
        "address": "0x5c69bee701ef814a2b6a3edd4b1652cb9cc5aa6f", # Uniswap V2 Factory
        "topics": [
            "0x0d3648bd0f6ba80134a33ba9275ac585d9d315f0ad8355cddefde31afa28d0e9",
            "0x000000000000000000000000c02aaa39b223fe8d0a0e5c4f27ead9083c756cc2", # WETH
            "0x000000000000000000000000a0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"  # USDC
        ],
        "data": "0x000000000000000000000000b4e16d0168e52d35cacd2c6185b44281ec28c9dc0000000000000000000000000000000000000000000000000000000000000001",
        "transactionHash": "0x987654321",
        "blockNumber": "0x12345"
    }
    event = evm_log_indexer.parse_log_event(mock_log)
    assert event is not None
    assert event["event_type"] == "PAIR_CREATED"
    assert event["pool_address"] == "0xb4e16d0168e52d35cacd2c6185b44281ec28c9dc"

def test_block_reorganization_detection():
    # Test 6: Reorg detection when parent hash does not match previous block
    chain = "eip155:8453"
    t1 = datetime.now(timezone.utc)

    # Ingest block 100
    res1 = evm_block_indexer.process_block(
        chain_id=chain,
        block_number=100,
        block_hash="0xhash100",
        parent_hash="0xhash99",
        block_time=t1
    )
    assert res1["is_reorg_detected"] is False

    # Ingest block 101 with unexpected parent hash (orphan fork)
    res2 = evm_block_indexer.process_block(
        chain_id=chain,
        block_number=101,
        block_hash="0xhash101_fork",
        parent_hash="0xDIFFERENT_HASH",
        block_time=t1
    )
    assert res2["is_reorg_detected"] is True
    assert res2["reorg_details"]["fork_block"] == 100

def test_malformed_and_nonstandard_erc20_metadata():
    # Test 7 & 9: Non-standard / malformed token metadata normalization
    res = evm_token_discovery.normalize_token_metadata(
        chain_id="eip155:1",
        contract_address="0xABCDEF1234567890ABCDEF1234567890ABCDEF12",
        name="   Non-Standard Token   ",
        symbol=None,
        decimals=255 # invalid decimals
    )
    assert res["symbol"] == "UNKNOWN"
    assert res["name"] == "Non-Standard Token"
    assert res["decimals"] is None
    assert res["is_verified"] is False

def test_market_enricher_onchain_reserves():
    # Test 10: Constant product reserves price calculation
    snapshot = evm_market_enricher.enrich_pool_reserves(
        chain_id="eip155:8453",
        asset_id="asset-1",
        pool_id="pool-1",
        reserve0=1000000000000000000, # 1 Base Token (18 dec)
        reserve1=3000000000,          # 3,000 Quote Tokens (6 dec USDC)
        decimals0=18,
        decimals1=6,
        block_number=123456,
        block_hash="0xhash123"
    )
    assert snapshot["price_in_quote"] == Decimal("3000")
    assert snapshot["data_quality"] == "COMPLETE"
    assert snapshot["is_canonical"] is True
