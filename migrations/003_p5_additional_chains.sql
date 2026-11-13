-- ============================================================================
-- SOLANA MEMECOIN OS - ADDITIONAL NETWORK ADAPTERS (MIGRATION 003)
-- Phase P5: Tier 3 EVM (Arbitrum, Optimism, Polygon, Avalanche)
--           Tier 4 Non-EVM (Sui, Aptos, TON, TRON)
-- ============================================================================

INSERT INTO chains (id, namespace, reference, name, environment, native_asset_symbol, native_asset_decimals, finality_blocks, rpc_urls, explorer_base_url)
VALUES
    -- Tier 3 EVM L2s & Sidechains
    ('eip155:42161', 'eip155', '42161', 'Arbitrum One', 'mainnet', 'ETH', 18, 64, '["https://arb1.arbitrum.io/rpc"]'::jsonb, 'https://arbiscan.io'),
    ('eip155:10', 'eip155', '10', 'OP Mainnet', 'mainnet', 'ETH', 18, 64, '["https://mainnet.optimism.io"]'::jsonb, 'https://optimistic.etherscan.io'),
    ('eip155:137', 'eip155', '137', 'Polygon PoS', 'mainnet', 'POL', 18, 128, '["https://polygon-rpc.com"]'::jsonb, 'https://polygonscan.com'),
    ('eip155:43114', 'eip155', '43114', 'Avalanche C-Chain', 'mainnet', 'AVAX', 18, 1, '["https://api.avax.network/ext/bc/C/rpc"]'::jsonb, 'https://snowtrace.io'),
    
    -- Tier 4 Non-EVM Chains
    ('sui:mainnet', 'sui', 'mainnet', 'Sui', 'mainnet', 'SUI', 9, 1, '["https://fullnode.mainnet.sui.io:443"]'::jsonb, 'https://suiscan.xyz'),
    ('aptos:mainnet', 'aptos', 'mainnet', 'Aptos', 'mainnet', 'APT', 8, 1, '["https://fullnode.mainnet.aptoslabs.com/v1"]'::jsonb, 'https://explorer.aptoslabs.com'),
    ('ton:mainnet', 'ton', 'mainnet', 'TON', 'mainnet', 'TON', 9, 1, '["https://toncenter.com/api/v2/jsonRPC"]'::jsonb, 'https://tonscan.org'),
    ('tron:mainnet', 'tron', 'mainnet', 'TRON', 'mainnet', 'TRX', 6, 19, '["https://api.trongrid.io"]'::jsonb, 'https://tronscan.org')
ON CONFLICT (id) DO NOTHING;

INSERT INTO chain_networks (chain_id, network_type, consensus_mechanism, block_time_target_ms, supports_eip1559)
VALUES
    ('eip155:42161', 'evm_l2_rollup', 'nitro_fraud_proof', 250, TRUE),
    ('eip155:10', 'evm_l2_rollup', 'op_stack_optimistic', 2000, TRUE),
    ('eip155:137', 'evm_sidechain', 'pos_heimdall_bor', 2000, TRUE),
    ('eip155:43114', 'evm_subnet', 'snowman_avalanche', 1000, TRUE),
    ('sui:mainnet', 'move_object', 'narwhal_bullshark_pos', 400, FALSE),
    ('aptos:mainnet', 'move_resource', 'aptos_bft_pos', 400, FALSE),
    ('ton:mainnet', 'tvm_actor', 'catchain_pos', 3000, FALSE),
    ('tron:mainnet', 'tvm_account', 'dpos', 3000, FALSE)
ON CONFLICT (chain_id) DO NOTHING;
