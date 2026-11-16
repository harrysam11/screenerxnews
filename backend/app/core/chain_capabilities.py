from enum import Enum
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field

class ChainCapability(str, Enum):
    NATIVE_ASSET_METADATA = "NATIVE_ASSET_METADATA"
    ADDRESS_VALIDATION = "ADDRESS_VALIDATION"
    TOKEN_METADATA = "TOKEN_METADATA"
    TOKEN_BALANCE = "TOKEN_BALANCE"
    BLOCK_SLOT_SEQUENCE = "BLOCK_SLOT_SEQUENCE"
    TRANSACTION_PARSING = "TRANSACTION_PARSING"
    TOKEN_TRANSFER_DECODING = "TOKEN_TRANSFER_DECODING"
    POOL_LIQUIDITY_DATA = "POOL_LIQUIDITY_DATA"
    SWAP_QUOTE_ROUTING = "SWAP_QUOTE_ROUTING"
    TRANSACTION_SIMULATION = "TRANSACTION_SIMULATION"
    FEE_ESTIMATION = "FEE_ESTIMATION"
    FINALITY_TRACKING = "FINALITY_TRACKING"
    REORG_HANDLING = "REORG_HANDLING"
    HOLDER_FORENSICS = "HOLDER_FORENSICS"

class CapabilityStatus(str, Enum):
    SUPPORTED = "SUPPORTED"
    PARTIAL = "PARTIAL"
    UNSUPPORTED = "UNSUPPORTED"
    UNVERIFIED = "UNVERIFIED"

class CapabilityDescriptor(BaseModel):
    capability: ChainCapability
    status: CapabilityStatus
    description: str
    verification_evidence: Optional[str] = None
    limitations: Optional[str] = None

class NativeAssetSpec(BaseModel):
    symbol: str
    name: str
    decimals: int
    smallest_unit_name: str
    conversion_factor: int

class ChainCapabilityProfile(BaseModel):
    chain_id: str
    canonical_id: str
    chain_name: str
    chain_family: str  # "svm", "evm", "move", "tvm"
    consensus_type: str
    finality_type: str
    finality_blocks_or_slots: int
    reorg_possible: bool
    native_asset: NativeAssetSpec
    capabilities: Dict[ChainCapability, CapabilityDescriptor]

class ChainCapabilityRegistry:
    """
    Centralized, typed capability registry for all 12 supported blockchain networks (P10.2-D).
    Explicitly tracks genuine operational capabilities, partial support, and unsupported features.
    Guarantees no adapter fabricates data for unsupported operations.
    """
    def __init__(self):
        self._profiles: Dict[str, ChainCapabilityProfile] = {}
        self._initialize_all_12_chains()

    def _initialize_all_12_chains(self):
        # 1. Solana
        self._register_solana()
        # 2. Ethereum Mainnet
        self._register_ethereum()
        # 3. Base Mainnet
        self._register_base()
        # 4. BNB Smart Chain
        self._register_bnb()
        # 5. Arbitrum One
        self._register_arbitrum()
        # 6. OP Mainnet
        self._register_optimism()
        # 7. Polygon PoS
        self._register_polygon()
        # 8. Avalanche C-Chain
        self._register_avalanche()
        # 9. Sui Mainnet
        self._register_sui()
        # 10. Aptos Mainnet
        self._register_aptos()
        # 11. TON Mainnet
        self._register_ton()
        # 12. TRON Mainnet
        self._register_tron()

    def _register_solana(self):
        caps = {
            ChainCapability.NATIVE_ASSET_METADATA: CapabilityDescriptor(
                capability=ChainCapability.NATIVE_ASSET_METADATA,
                status=CapabilityStatus.SUPPORTED,
                description="Solana native SOL currency metadata (9 decimals, lamports).",
                verification_evidence="Verified in chains.py and SolanaChainAdapter"
            ),
            ChainCapability.ADDRESS_VALIDATION: CapabilityDescriptor(
                capability=ChainCapability.ADDRESS_VALIDATION,
                status=CapabilityStatus.SUPPORTED,
                description="Base58 public key validation (32 bytes Ed25519).",
                verification_evidence="Verified in AddressValidator.validate_solana_address"
            ),
            ChainCapability.TOKEN_METADATA: CapabilityDescriptor(
                capability=ChainCapability.TOKEN_METADATA,
                status=CapabilityStatus.SUPPORTED,
                description="SPL Token and Token-2022 mint info parsing via getAccountInfo (jsonParsed).",
                verification_evidence="Verified via SolanaRpcService.get_parsed_mint_info"
            ),
            ChainCapability.TOKEN_BALANCE: CapabilityDescriptor(
                capability=ChainCapability.TOKEN_BALANCE,
                status=CapabilityStatus.SUPPORTED,
                description="Token account balance retrieval via getTokenAccountBalance.",
                verification_evidence="Supported via Solana JSON-RPC getTokenAccountBalance"
            ),
            ChainCapability.BLOCK_SLOT_SEQUENCE: CapabilityDescriptor(
                capability=ChainCapability.BLOCK_SLOT_SEQUENCE,
                status=CapabilityStatus.SUPPORTED,
                description="Slot, block height, and block time retrieval with commitment semantics.",
                verification_evidence="Verified via getLatestBlockhash and getBlock"
            ),
            ChainCapability.TRANSACTION_PARSING: CapabilityDescriptor(
                capability=ChainCapability.TRANSACTION_PARSING,
                status=CapabilityStatus.SUPPORTED,
                description="Transaction retrieval via getTransaction with jsonParsed encoding.",
                verification_evidence="Supported via SolanaRpcService"
            ),
            ChainCapability.TOKEN_TRANSFER_DECODING: CapabilityDescriptor(
                capability=ChainCapability.TOKEN_TRANSFER_DECODING,
                status=CapabilityStatus.PARTIAL,
                description="Decodes standard SPL token transfers; Token-2022 extensions partially parsed.",
                limitations="Complex CPI inner instruction transfers require specialized parser."
            ),
            ChainCapability.POOL_LIQUIDITY_DATA: CapabilityDescriptor(
                capability=ChainCapability.POOL_LIQUIDITY_DATA,
                status=CapabilityStatus.PARTIAL,
                description="Raydium, Whirlpool, and Pump.fun pool account decoding.",
                limitations="Direct account parsing requires layout schemas; off-chain indexers used in research mode."
            ),
            ChainCapability.SWAP_QUOTE_ROUTING: CapabilityDescriptor(
                capability=ChainCapability.SWAP_QUOTE_ROUTING,
                status=CapabilityStatus.PARTIAL,
                description="Jupiter Aggregator API routing for market data research.",
                limitations="Execution routing only; live execution is disabled."
            ),
            ChainCapability.TRANSACTION_SIMULATION: CapabilityDescriptor(
                capability=ChainCapability.TRANSACTION_SIMULATION,
                status=CapabilityStatus.SUPPORTED,
                description="Preflight simulation via simulateTransaction without broadcasting.",
                verification_evidence="Supported natively by Solana RPC simulateTransaction"
            ),
            ChainCapability.FEE_ESTIMATION: CapabilityDescriptor(
                capability=ChainCapability.FEE_ESTIMATION,
                status=CapabilityStatus.SUPPORTED,
                description="Recent prioritization fees estimation via getRecentPrioritizationFees.",
                verification_evidence="Prioritization fee micro-lamport query supported"
            ),
            ChainCapability.FINALITY_TRACKING: CapabilityDescriptor(
                capability=ChainCapability.FINALITY_TRACKING,
                status=CapabilityStatus.SUPPORTED,
                description="Tracks processed (single cluster leader), confirmed (66%+ supermajority), and finalized (root bank, 31+ confirmed blocks).",
                verification_evidence="Explicit commitment levels enforced in RPC calls"
            ),
            ChainCapability.REORG_HANDLING: CapabilityDescriptor(
                capability=ChainCapability.REORG_HANDLING,
                status=CapabilityStatus.SUPPORTED,
                description="Fork choice tracking via Tower BFT roots; confirmed slots can fork before finality.",
                verification_evidence="Tower BFT finality root semantics documented"
            ),
            ChainCapability.HOLDER_FORENSICS: CapabilityDescriptor(
                capability=ChainCapability.HOLDER_FORENSICS,
                status=CapabilityStatus.SUPPORTED,
                description="Mint authority, freeze authority, Token-2022 extensions, and top token accounts.",
                verification_evidence="Verified in SolanaChainAdapter.inspect_token_security"
            ),
        }
        self._profiles["solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"] = ChainCapabilityProfile(
            chain_id="solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp",
            canonical_id="solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp",
            chain_name="Solana",
            chain_family="svm",
            consensus_type="Proof-of-History + Tower BFT",
            finality_type="Tower BFT Root (32 slots)",
            finality_blocks_or_slots=32,
            reorg_possible=True,  # slots can drop prior to root finality
            native_asset=NativeAssetSpec(
                symbol="SOL",
                name="Solana",
                decimals=9,
                smallest_unit_name="lamport",
                conversion_factor=1_000_000_000
            ),
            capabilities=caps
        )
        # Register legacy alias pointer
        self._profiles["solana:mainnet"] = self._profiles["solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"]

    def _build_evm_capabilities(
        self,
        chain_name: str,
        finality_type: str,
        finality_depth: int,
        reorg_possible: bool,
        is_l2: bool = False
    ) -> Dict[ChainCapability, CapabilityDescriptor]:
        return {
            ChainCapability.NATIVE_ASSET_METADATA: CapabilityDescriptor(
                capability=ChainCapability.NATIVE_ASSET_METADATA,
                status=CapabilityStatus.SUPPORTED,
                description=f"{chain_name} native currency metadata (18 decimals, wei).",
                verification_evidence="Verified in chains.py and EvmChainAdapter"
            ),
            ChainCapability.ADDRESS_VALIDATION: CapabilityDescriptor(
                capability=ChainCapability.ADDRESS_VALIDATION,
                status=CapabilityStatus.SUPPORTED,
                description="EIP-55 mixed-case checksum and 20-byte hex validation.",
                verification_evidence="Verified in AddressValidator.validate_evm_address"
            ),
            ChainCapability.TOKEN_METADATA: CapabilityDescriptor(
                capability=ChainCapability.TOKEN_METADATA,
                status=CapabilityStatus.SUPPORTED,
                description="ERC-20 decimals, symbol, name, and total supply via eth_call.",
                verification_evidence="Verified via ERC-20 4-byte selectors (0x313ce567, 0x95d89b41, 0x06fdde03)"
            ),
            ChainCapability.TOKEN_BALANCE: CapabilityDescriptor(
                capability=ChainCapability.TOKEN_BALANCE,
                status=CapabilityStatus.SUPPORTED,
                description="Native balance via eth_getBalance, token balance via balanceOf(address).",
                verification_evidence="Supported via standard EVM JSON-RPC eth_call"
            ),
            ChainCapability.BLOCK_SLOT_SEQUENCE: CapabilityDescriptor(
                capability=ChainCapability.BLOCK_SLOT_SEQUENCE,
                status=CapabilityStatus.SUPPORTED,
                description="Block number, block hash, parent hash, and Unix timestamp via eth_getBlockByNumber.",
                verification_evidence="Verified via eth_getBlockByNumber parsing"
            ),
            ChainCapability.TRANSACTION_PARSING: CapabilityDescriptor(
                capability=ChainCapability.TRANSACTION_PARSING,
                status=CapabilityStatus.SUPPORTED,
                description="Transaction payload, gas, status, and receipt lookup via eth_getTransactionByHash / eth_getTransactionReceipt.",
                verification_evidence="Supported via standard EVM JSON-RPC"
            ),
            ChainCapability.TOKEN_TRANSFER_DECODING: CapabilityDescriptor(
                capability=ChainCapability.TOKEN_TRANSFER_DECODING,
                status=CapabilityStatus.SUPPORTED,
                description="Decodes ERC-20 Transfer(address,address,uint256) topic 0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef.",
                verification_evidence="Standard ERC-20 log topic filter verified"
            ),
            ChainCapability.POOL_LIQUIDITY_DATA: CapabilityDescriptor(
                capability=ChainCapability.POOL_LIQUIDITY_DATA,
                status=CapabilityStatus.PARTIAL,
                description="Uniswap V2 / PancakeSwap pair getReserves() and Uniswap V3 slot0 decoding.",
                limitations="Direct pool calls supported; multi-DEX routing handled via discovery aggregators."
            ),
            ChainCapability.SWAP_QUOTE_ROUTING: CapabilityDescriptor(
                capability=ChainCapability.SWAP_QUOTE_ROUTING,
                status=CapabilityStatus.PARTIAL,
                description="Router getAmountsOut simulation on V2 pairs.",
                limitations="Simulated read-only quoting; live execution is disabled."
            ),
            ChainCapability.TRANSACTION_SIMULATION: CapabilityDescriptor(
                capability=ChainCapability.TRANSACTION_SIMULATION,
                status=CapabilityStatus.SUPPORTED,
                description="Preflight simulation and revert reason decoding via eth_call and eth_estimateGas.",
                verification_evidence="Supported via eth_call state override / simulation"
            ),
            ChainCapability.FEE_ESTIMATION: CapabilityDescriptor(
                capability=ChainCapability.FEE_ESTIMATION,
                status=CapabilityStatus.SUPPORTED,
                description="EIP-1559 base fee + maxPriorityFeePerGas (eth_maxPriorityFeePerGas) and L1 data calldata fees where applicable.",
                verification_evidence="Verified via eth_feeHistory and L2 GasOracle adapters"
            ),
            ChainCapability.FINALITY_TRACKING: CapabilityDescriptor(
                capability=ChainCapability.FINALITY_TRACKING,
                status=CapabilityStatus.SUPPORTED,
                description=f"{chain_name} finality semantics ({finality_type}).",
                verification_evidence=f"Depth of {finality_depth} blocks enforced"
            ),
            ChainCapability.REORG_HANDLING: CapabilityDescriptor(
                capability=ChainCapability.REORG_HANDLING,
                status=CapabilityStatus.SUPPORTED if reorg_possible else CapabilityStatus.UNSUPPORTED,
                description="Parent hash consistency verification and block reorg detection." if reorg_possible else "Deterministic consensus with zero block reorganizations.",
                verification_evidence="Reorg handling logic in EvmChainAdapter" if reorg_possible else "BFT finality"
            ),
            ChainCapability.HOLDER_FORENSICS: CapabilityDescriptor(
                capability=ChainCapability.HOLDER_FORENSICS,
                status=CapabilityStatus.SUPPORTED,
                description="Bytecode analysis, EIP-1967 proxy implementation lookup, and honeypot simulation checks.",
                verification_evidence="Verified in EvmChainAdapter.inspect_token_security"
            ),
        }

    def _register_ethereum(self):
        caps = self._build_evm_capabilities(
            chain_name="Ethereum",
            finality_type="Gasper PoS Finality (2 epochs, 64-95 slots)",
            finality_depth=12,
            reorg_possible=True
        )
        self._profiles["eip155:1"] = ChainCapabilityProfile(
            chain_id="eip155:1",
            canonical_id="eip155:1",
            chain_name="Ethereum",
            chain_family="evm",
            consensus_type="Proof-of-Stake (Gasper)",
            finality_type="Finalized Checkpoint (2 epochs)",
            finality_blocks_or_slots=12,
            reorg_possible=True,
            native_asset=NativeAssetSpec(
                symbol="ETH", name="Ether", decimals=18, smallest_unit_name="wei", conversion_factor=10**18
            ),
            capabilities=caps
        )

    def _register_base(self):
        caps = self._build_evm_capabilities(
            chain_name="Base",
            finality_type="Optimistic Rollup (L1 batch posted + challenge window)",
            finality_depth=64,
            reorg_possible=True,
            is_l2=True
        )
        self._profiles["eip155:8453"] = ChainCapabilityProfile(
            chain_id="eip155:8453",
            canonical_id="eip155:8453",
            chain_name="Base",
            chain_family="evm",
            consensus_type="OP Stack Sequencer + L1 Posting",
            finality_type="Sequencer Soft-Finality / L1 Finalized",
            finality_blocks_or_slots=64,
            reorg_possible=True,
            native_asset=NativeAssetSpec(
                symbol="ETH", name="Ether", decimals=18, smallest_unit_name="wei", conversion_factor=10**18
            ),
            capabilities=caps
        )

    def _register_bnb(self):
        caps = self._build_evm_capabilities(
            chain_name="BNB Smart Chain",
            finality_type="PoSA Fast Finality (2/3+ validator votes)",
            finality_depth=15,
            reorg_possible=True
        )
        self._profiles["eip155:56"] = ChainCapabilityProfile(
            chain_id="eip155:56",
            canonical_id="eip155:56",
            chain_name="BNB Chain",
            chain_family="evm",
            consensus_type="Proof-of-Staked-Authority (Parlia)",
            finality_type="Fast Finality (15 blocks)",
            finality_blocks_or_slots=15,
            reorg_possible=True,
            native_asset=NativeAssetSpec(
                symbol="BNB", name="BNB", decimals=18, smallest_unit_name="wei", conversion_factor=10**18
            ),
            capabilities=caps
        )

    def _register_arbitrum(self):
        caps = self._build_evm_capabilities(
            chain_name="Arbitrum One",
            finality_type="Arbitrum Nitro Sequencer / L1 Settlement",
            finality_depth=64,
            reorg_possible=True,
            is_l2=True
        )
        self._profiles["eip155:42161"] = ChainCapabilityProfile(
            chain_id="eip155:42161",
            canonical_id="eip155:42161",
            chain_name="Arbitrum One",
            chain_family="evm",
            consensus_type="Nitro Rollup Sequencer + ArbGasInfo",
            finality_type="Sequencer Soft-Finality / L1 Finalized",
            finality_blocks_or_slots=64,
            reorg_possible=True,
            native_asset=NativeAssetSpec(
                symbol="ETH", name="Ether", decimals=18, smallest_unit_name="wei", conversion_factor=10**18
            ),
            capabilities=caps
        )

    def _register_optimism(self):
        caps = self._build_evm_capabilities(
            chain_name="OP Mainnet",
            finality_type="OP Bedrock Sequencer / L1 Settlement",
            finality_depth=64,
            reorg_possible=True,
            is_l2=True
        )
        self._profiles["eip155:10"] = ChainCapabilityProfile(
            chain_id="eip155:10",
            canonical_id="eip155:10",
            chain_name="OP Mainnet",
            chain_family="evm",
            consensus_type="OP Stack Bedrock + GasPriceOracle",
            finality_type="Sequencer Soft-Finality / L1 Finalized",
            finality_blocks_or_slots=64,
            reorg_possible=True,
            native_asset=NativeAssetSpec(
                symbol="ETH", name="Ether", decimals=18, smallest_unit_name="wei", conversion_factor=10**18
            ),
            capabilities=caps
        )

    def _register_polygon(self):
        caps = self._build_evm_capabilities(
            chain_name="Polygon PoS",
            finality_type="Heimdall Checkpoint Finality",
            finality_depth=128,
            reorg_possible=True
        )
        self._profiles["eip155:137"] = ChainCapabilityProfile(
            chain_id="eip155:137",
            canonical_id="eip155:137",
            chain_name="Polygon PoS",
            chain_family="evm",
            consensus_type="Bor PoS + Heimdall Milestones",
            finality_type="Checkpoint Finality (128 blocks)",
            finality_blocks_or_slots=128,
            reorg_possible=True,
            native_asset=NativeAssetSpec(
                symbol="POL", name="Polygon Ecosystem Token", decimals=18, smallest_unit_name="wei", conversion_factor=10**18
            ),
            capabilities=caps
        )

    def _register_avalanche(self):
        caps = self._build_evm_capabilities(
            chain_name="Avalanche C-Chain",
            finality_type="Snowman Consensus Sub-Second Finality",
            finality_depth=1,
            reorg_possible=False
        )
        self._profiles["eip155:43114"] = ChainCapabilityProfile(
            chain_id="eip155:43114",
            canonical_id="eip155:43114",
            chain_name="Avalanche C-Chain",
            chain_family="evm",
            consensus_type="Snowman Consensus",
            finality_type="Single-Block Probabilistic-Zero Reorg",
            finality_blocks_or_slots=1,
            reorg_possible=False,
            native_asset=NativeAssetSpec(
                symbol="AVAX", name="Avalanche", decimals=18, smallest_unit_name="nAVAX", conversion_factor=10**18
            ),
            capabilities=caps
        )

    def _register_sui(self):
        caps = {
            ChainCapability.NATIVE_ASSET_METADATA: CapabilityDescriptor(
                capability=ChainCapability.NATIVE_ASSET_METADATA,
                status=CapabilityStatus.SUPPORTED,
                description="Sui native SUI currency metadata (9 decimals, MIST).",
                verification_evidence="Verified in chains.py and SuiChainAdapter"
            ),
            ChainCapability.ADDRESS_VALIDATION: CapabilityDescriptor(
                capability=ChainCapability.ADDRESS_VALIDATION,
                status=CapabilityStatus.SUPPORTED,
                description="Sui 32-byte hexadecimal address (0x prefixed, 66 hex characters).",
                verification_evidence="Verified in AddressValidator.validate_sui_address"
            ),
            ChainCapability.TOKEN_METADATA: CapabilityDescriptor(
                capability=ChainCapability.TOKEN_METADATA,
                status=CapabilityStatus.SUPPORTED,
                description="CoinMetadata object retrieval via suix_getCoinMetadata.",
                verification_evidence="Verified in SuiChainAdapter.get_token_metadata"
            ),
            ChainCapability.TOKEN_BALANCE: CapabilityDescriptor(
                capability=ChainCapability.TOKEN_BALANCE,
                status=CapabilityStatus.SUPPORTED,
                description="Sui coin balance lookup via suix_getBalance.",
                verification_evidence="Supported via suix_getBalance"
            ),
            ChainCapability.BLOCK_SLOT_SEQUENCE: CapabilityDescriptor(
                capability=ChainCapability.BLOCK_SLOT_SEQUENCE,
                status=CapabilityStatus.SUPPORTED,
                description="Checkpoint sequence number and digest via sui_getLatestCheckpointSequenceNumber.",
                verification_evidence="Verified in SuiChainAdapter.get_latest_block"
            ),
            ChainCapability.TRANSACTION_PARSING: CapabilityDescriptor(
                capability=ChainCapability.TRANSACTION_PARSING,
                status=CapabilityStatus.SUPPORTED,
                description="Transaction block query via sui_getTransactionBlock with showEffects=True.",
                verification_evidence="Supported via sui_getTransactionBlock"
            ),
            ChainCapability.TOKEN_TRANSFER_DECODING: CapabilityDescriptor(
                capability=ChainCapability.TOKEN_TRANSFER_DECODING,
                status=CapabilityStatus.PARTIAL,
                description="Move object transfer effects parsed from TransactionEffects.",
                limitations="Programmable Transaction Blocks (PTBs) with custom Move entry functions require specialized event decoders."
            ),
            ChainCapability.POOL_LIQUIDITY_DATA: CapabilityDescriptor(
                capability=ChainCapability.POOL_LIQUIDITY_DATA,
                status=CapabilityStatus.PARTIAL,
                description="Cetus / Turbos pool object state inspection.",
                limitations="Direct pool object fetching supported; routing requires protocol-specific math."
            ),
            ChainCapability.SWAP_QUOTE_ROUTING: CapabilityDescriptor(
                capability=ChainCapability.SWAP_QUOTE_ROUTING,
                status=CapabilityStatus.UNSUPPORTED,
                description="Live on-chain routing not supported without external aggregator SDK.",
                limitations="Unsupported in local offline core."
            ),
            ChainCapability.TRANSACTION_SIMULATION: CapabilityDescriptor(
                capability=ChainCapability.TRANSACTION_SIMULATION,
                status=CapabilityStatus.SUPPORTED,
                description="Dry run transaction execution via sui_dryRunTransactionBlock.",
                verification_evidence="Supported via sui_dryRunTransactionBlock"
            ),
            ChainCapability.FEE_ESTIMATION: CapabilityDescriptor(
                capability=ChainCapability.FEE_ESTIMATION,
                status=CapabilityStatus.SUPPORTED,
                description="Reference gas price query via suix_getReferenceGasPrice.",
                verification_evidence="Supported via suix_getReferenceGasPrice"
            ),
            ChainCapability.FINALITY_TRACKING: CapabilityDescriptor(
                capability=ChainCapability.FINALITY_TRACKING,
                status=CapabilityStatus.SUPPORTED,
                description="Mysticeti / Narwhal-Bullshark consensus providing instant single-checkpoint finality.",
                verification_evidence="Checkpoint certificate finality verified"
            ),
            ChainCapability.REORG_HANDLING: CapabilityDescriptor(
                capability=ChainCapability.REORG_HANDLING,
                status=CapabilityStatus.UNSUPPORTED,
                description="Sui uses Byzantine Fault Tolerant consensus with checkpoint certificates; reorgs are impossible by design.",
                limitations="Zero-reorg consensus architecture."
            ),
            ChainCapability.HOLDER_FORENSICS: CapabilityDescriptor(
                capability=ChainCapability.HOLDER_FORENSICS,
                status=CapabilityStatus.SUPPORTED,
                description="Evaluates 0x2::coin::TreasuryCap object ownership and package immutability.",
                verification_evidence="Verified in SuiChainAdapter.inspect_token_security"
            ),
        }
        self._profiles["sui:mainnet"] = ChainCapabilityProfile(
            chain_id="sui:mainnet",
            canonical_id="sui:mainnet",
            chain_name="Sui",
            chain_family="move",
            consensus_type="Mysticeti BFT / Narwhal-Bullshark",
            finality_type="Single-Checkpoint BFT Certificate",
            finality_blocks_or_slots=1,
            reorg_possible=False,
            native_asset=NativeAssetSpec(
                symbol="SUI", name="Sui", decimals=9, smallest_unit_name="MIST", conversion_factor=1_000_000_000
            ),
            capabilities=caps
        )

    def _register_aptos(self):
        caps = {
            ChainCapability.NATIVE_ASSET_METADATA: CapabilityDescriptor(
                capability=ChainCapability.NATIVE_ASSET_METADATA,
                status=CapabilityStatus.SUPPORTED,
                description="Aptos native APT currency metadata (8 decimals, Octas).",
                verification_evidence="Verified in chains.py and AptosChainAdapter"
            ),
            ChainCapability.ADDRESS_VALIDATION: CapabilityDescriptor(
                capability=ChainCapability.ADDRESS_VALIDATION,
                status=CapabilityStatus.SUPPORTED,
                description="Aptos 32-byte hexadecimal address (0x prefixed, 66 hex characters).",
                verification_evidence="Verified in AddressValidator.validate_aptos_address"
            ),
            ChainCapability.TOKEN_METADATA: CapabilityDescriptor(
                capability=ChainCapability.TOKEN_METADATA,
                status=CapabilityStatus.SUPPORTED,
                description="CoinInfo resource inspection at 0x1::coin::CoinInfo<CoinType>.",
                verification_evidence="Verified in AptosChainAdapter.get_token_metadata"
            ),
            ChainCapability.TOKEN_BALANCE: CapabilityDescriptor(
                capability=ChainCapability.TOKEN_BALANCE,
                status=CapabilityStatus.SUPPORTED,
                description="CoinStore resource lookup at 0x1::coin::CoinStore<CoinType>.",
                verification_evidence="Supported via accounts/{address}/resource REST endpoint"
            ),
            ChainCapability.BLOCK_SLOT_SEQUENCE: CapabilityDescriptor(
                capability=ChainCapability.BLOCK_SLOT_SEQUENCE,
                status=CapabilityStatus.SUPPORTED,
                description="Ledger version and block height query via Aptos Node REST API.",
                verification_evidence="Verified in AptosChainAdapter.get_latest_block"
            ),
            ChainCapability.TRANSACTION_PARSING: CapabilityDescriptor(
                capability=ChainCapability.TRANSACTION_PARSING,
                status=CapabilityStatus.SUPPORTED,
                description="Transaction lookup by hash or ledger version via transactions/by_hash/{hash}.",
                verification_evidence="Supported via Aptos REST API"
            ),
            ChainCapability.TOKEN_TRANSFER_DECODING: CapabilityDescriptor(
                capability=ChainCapability.TOKEN_TRANSFER_DECODING,
                status=CapabilityStatus.PARTIAL,
                description="Decodes 0x1::coin::DepositEvent and 0x1::coin::WithdrawEvent events.",
                limitations="Aptos Digital Asset / Fungible Asset standard events partially supported."
            ),
            ChainCapability.POOL_LIQUIDITY_DATA: CapabilityDescriptor(
                capability=ChainCapability.POOL_LIQUIDITY_DATA,
                status=CapabilityStatus.PARTIAL,
                description="Liquidswap / Thala liquidity pool resource inspection.",
                limitations="Direct resource reading supported; multi-hop quoting requires off-chain indexer."
            ),
            ChainCapability.SWAP_QUOTE_ROUTING: CapabilityDescriptor(
                capability=ChainCapability.SWAP_QUOTE_ROUTING,
                status=CapabilityStatus.UNSUPPORTED,
                description="Live on-chain routing not supported without external aggregator SDK.",
                limitations="Unsupported in local offline core."
            ),
            ChainCapability.TRANSACTION_SIMULATION: CapabilityDescriptor(
                capability=ChainCapability.TRANSACTION_SIMULATION,
                status=CapabilityStatus.SUPPORTED,
                description="Preflight simulation via transactions/simulate without signature.",
                verification_evidence="Supported via Aptos REST API transactions/simulate"
            ),
            ChainCapability.FEE_ESTIMATION: CapabilityDescriptor(
                capability=ChainCapability.FEE_ESTIMATION,
                status=CapabilityStatus.SUPPORTED,
                description="Gas price estimation via estimate_gas_price REST endpoint.",
                verification_evidence="Supported via estimate_gas_price"
            ),
            ChainCapability.FINALITY_TRACKING: CapabilityDescriptor(
                capability=ChainCapability.FINALITY_TRACKING,
                status=CapabilityStatus.SUPPORTED,
                description="AptosBFT (DiemBFT v4) state machine replication providing deterministic sub-second finality.",
                verification_evidence="Committed ledger version finality verified"
            ),
            ChainCapability.REORG_HANDLING: CapabilityDescriptor(
                capability=ChainCapability.REORG_HANDLING,
                status=CapabilityStatus.UNSUPPORTED,
                description="Aptos uses pipelined BFT consensus; once a ledger version is committed, reorganizations cannot occur.",
                limitations="Zero-reorg consensus architecture."
            ),
            ChainCapability.HOLDER_FORENSICS: CapabilityDescriptor(
                capability=ChainCapability.HOLDER_FORENSICS,
                status=CapabilityStatus.SUPPORTED,
                description="Evaluates MintCapability, FreezeCapability, and BurnCapability ownership.",
                verification_evidence="Verified in AptosChainAdapter.inspect_token_security"
            ),
        }
        self._profiles["aptos:mainnet"] = ChainCapabilityProfile(
            chain_id="aptos:mainnet",
            canonical_id="aptos:mainnet",
            chain_name="Aptos",
            chain_family="move",
            consensus_type="AptosBFT (Jolteon / DiemBFT v4)",
            finality_type="Deterministic BFT Ledger Version",
            finality_blocks_or_slots=1,
            reorg_possible=False,
            native_asset=NativeAssetSpec(
                symbol="APT", name="Aptos", decimals=8, smallest_unit_name="Octa", conversion_factor=100_000_000
            ),
            capabilities=caps
        )

    def _register_ton(self):
        caps = {
            ChainCapability.NATIVE_ASSET_METADATA: CapabilityDescriptor(
                capability=ChainCapability.NATIVE_ASSET_METADATA,
                status=CapabilityStatus.SUPPORTED,
                description="TON native Toncoin currency metadata (9 decimals, nanoTON).",
                verification_evidence="Verified in chains.py and TonChainAdapter"
            ),
            ChainCapability.ADDRESS_VALIDATION: CapabilityDescriptor(
                capability=ChainCapability.ADDRESS_VALIDATION,
                status=CapabilityStatus.SUPPORTED,
                description="Validates raw format (0:hex) and user-friendly Base64url (48 chars) with CRC16 checksum.",
                verification_evidence="Verified in AddressValidator.validate_ton_address"
            ),
            ChainCapability.TOKEN_METADATA: CapabilityDescriptor(
                capability=ChainCapability.TOKEN_METADATA,
                status=CapabilityStatus.PARTIAL,
                description="TEP-74 Jetton master get_jetton_data inspection for supply, mintable, and admin.",
                limitations="Decimals and symbol stored off-chain or in IPFS metadata payload."
            ),
            ChainCapability.TOKEN_BALANCE: CapabilityDescriptor(
                capability=ChainCapability.TOKEN_BALANCE,
                status=CapabilityStatus.SUPPORTED,
                description="Toncoin balance via getAddressInformation; Jetton balance via get_wallet_data.",
                verification_evidence="Supported via toncenter getAddressInformation"
            ),
            ChainCapability.BLOCK_SLOT_SEQUENCE: CapabilityDescriptor(
                capability=ChainCapability.BLOCK_SLOT_SEQUENCE,
                status=CapabilityStatus.SUPPORTED,
                description="Masterchain seqno, shard blocks, and root hash via getMasterchainInfo.",
                verification_evidence="Verified in TonChainAdapter.get_latest_block"
            ),
            ChainCapability.TRANSACTION_PARSING: CapabilityDescriptor(
                capability=ChainCapability.TRANSACTION_PARSING,
                status=CapabilityStatus.SUPPORTED,
                description="Account transaction query via getTransactions (in_msg, out_msgs, fee).",
                verification_evidence="Supported via getTransactions"
            ),
            ChainCapability.TOKEN_TRANSFER_DECODING: CapabilityDescriptor(
                capability=ChainCapability.TOKEN_TRANSFER_DECODING,
                status=CapabilityStatus.PARTIAL,
                description="Decodes Jetton transfer notification (op: 0x7362d09c) and Toncoin internal messages.",
                limitations="Complex TVM message tree routing requires cell bag-of-cells (BOC) parsing."
            ),
            ChainCapability.POOL_LIQUIDITY_DATA: CapabilityDescriptor(
                capability=ChainCapability.POOL_LIQUIDITY_DATA,
                status=CapabilityStatus.PARTIAL,
                description="DeDust / STON.fi pool contract state inspection.",
                limitations="Direct pool getter methods supported; routing requires protocol-specific BOC parsers."
            ),
            ChainCapability.SWAP_QUOTE_ROUTING: CapabilityDescriptor(
                capability=ChainCapability.SWAP_QUOTE_ROUTING,
                status=CapabilityStatus.UNSUPPORTED,
                description="Live multi-hop routing unsupported in local core without external aggregator.",
                limitations="Unsupported in local offline core."
            ),
            ChainCapability.TRANSACTION_SIMULATION: CapabilityDescriptor(
                capability=ChainCapability.TRANSACTION_SIMULATION,
                status=CapabilityStatus.SUPPORTED,
                description="TVM message emulation via runGetMethod or emulated cell execution.",
                verification_evidence="Supported via runGetMethod"
            ),
            ChainCapability.FEE_ESTIMATION: CapabilityDescriptor(
                capability=ChainCapability.FEE_ESTIMATION,
                status=CapabilityStatus.SUPPORTED,
                description="Fee estimation via estimateFee method on serialized message BOC.",
                verification_evidence="Supported via toncenter estimateFee"
            ),
            ChainCapability.FINALITY_TRACKING: CapabilityDescriptor(
                capability=ChainCapability.FINALITY_TRACKING,
                status=CapabilityStatus.SUPPORTED,
                description="Catchain consensus with masterchain block finality across workchains.",
                verification_evidence="Masterchain commit finality verified"
            ),
            ChainCapability.REORG_HANDLING: CapabilityDescriptor(
                capability=ChainCapability.REORG_HANDLING,
                status=CapabilityStatus.UNSUPPORTED,
                description="Masterchain blocks are produced via BFT Byzantine consensus; workchain states finalized upon masterchain inclusion.",
                limitations="Zero-reorg once committed to masterchain."
            ),
            ChainCapability.HOLDER_FORENSICS: CapabilityDescriptor(
                capability=ChainCapability.HOLDER_FORENSICS,
                status=CapabilityStatus.SUPPORTED,
                description="Jetton admin address renouncement and mintable flag verification.",
                verification_evidence="Verified in TonChainAdapter.inspect_token_security"
            ),
        }
        self._profiles["ton:mainnet"] = ChainCapabilityProfile(
            chain_id="ton:mainnet",
            canonical_id="ton:mainnet",
            chain_name="TON",
            chain_family="tvm",
            consensus_type="Catchain BFT Consensus",
            finality_type="Masterchain Block Finality",
            finality_blocks_or_slots=1,
            reorg_possible=False,
            native_asset=NativeAssetSpec(
                symbol="TON", name="Toncoin", decimals=9, smallest_unit_name="nanoton", conversion_factor=1_000_000_000
            ),
            capabilities=caps
        )

    def _register_tron(self):
        caps = {
            ChainCapability.NATIVE_ASSET_METADATA: CapabilityDescriptor(
                capability=ChainCapability.NATIVE_ASSET_METADATA,
                status=CapabilityStatus.SUPPORTED,
                description="TRON native TRX currency metadata (6 decimals, SUN).",
                verification_evidence="Verified in chains.py and TronChainAdapter"
            ),
            ChainCapability.ADDRESS_VALIDATION: CapabilityDescriptor(
                capability=ChainCapability.ADDRESS_VALIDATION,
                status=CapabilityStatus.SUPPORTED,
                description="Validates Base58Check (starts with 'T', 34 chars) and 41-hex format with double-SHA256 checksum.",
                verification_evidence="Verified in AddressValidator.validate_tron_address"
            ),
            ChainCapability.TOKEN_METADATA: CapabilityDescriptor(
                capability=ChainCapability.TOKEN_METADATA,
                status=CapabilityStatus.SUPPORTED,
                description="TRC-20 decimals, symbol, name, and total supply via triggerconstantcontract.",
                verification_evidence="Verified via triggerconstantcontract 4-byte selectors"
            ),
            ChainCapability.TOKEN_BALANCE: CapabilityDescriptor(
                capability=ChainCapability.TOKEN_BALANCE,
                status=CapabilityStatus.SUPPORTED,
                description="TRX balance via getaccount; TRC-20 balance via triggerconstantcontract balanceOf(address).",
                verification_evidence="Supported via wallet/getaccount and triggerconstantcontract"
            ),
            ChainCapability.BLOCK_SLOT_SEQUENCE: CapabilityDescriptor(
                capability=ChainCapability.BLOCK_SLOT_SEQUENCE,
                status=CapabilityStatus.SUPPORTED,
                description="Block number, blockID (hash), parentHash, and timestamp via wallet/getnowblock.",
                verification_evidence="Verified in TronChainAdapter.get_latest_block"
            ),
            ChainCapability.TRANSACTION_PARSING: CapabilityDescriptor(
                capability=ChainCapability.TRANSACTION_PARSING,
                status=CapabilityStatus.SUPPORTED,
                description="Transaction details and execution result via wallet/gettransactionbyid and gettransactioninfobyid.",
                verification_evidence="Supported via wallet/gettransactionbyid"
            ),
            ChainCapability.TOKEN_TRANSFER_DECODING: CapabilityDescriptor(
                capability=ChainCapability.TOKEN_TRANSFER_DECODING,
                status=CapabilityStatus.SUPPORTED,
                description="Decodes TRC-20 Transfer event log topic 0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef.",
                verification_evidence="Standard TRC-20 log topic filter verified"
            ),
            ChainCapability.POOL_LIQUIDITY_DATA: CapabilityDescriptor(
                capability=ChainCapability.POOL_LIQUIDITY_DATA,
                status=CapabilityStatus.PARTIAL,
                description="SunSwap V1/V2 pool reserves query via triggerconstantcontract.",
                limitations="Direct pool calls supported; multi-DEX routing handled via discovery aggregators."
            ),
            ChainCapability.SWAP_QUOTE_ROUTING: CapabilityDescriptor(
                capability=ChainCapability.SWAP_QUOTE_ROUTING,
                status=CapabilityStatus.PARTIAL,
                description="SunSwap router getAmountsOut simulation.",
                limitations="Simulated read-only quoting; live execution is disabled."
            ),
            ChainCapability.TRANSACTION_SIMULATION: CapabilityDescriptor(
                capability=ChainCapability.TRANSACTION_SIMULATION,
                status=CapabilityStatus.SUPPORTED,
                description="Preflight simulation and energy/bandwidth estimation via triggerconstantcontract.",
                verification_evidence="Supported via triggerconstantcontract"
            ),
            ChainCapability.FEE_ESTIMATION: CapabilityDescriptor(
                capability=ChainCapability.FEE_ESTIMATION,
                status=CapabilityStatus.SUPPORTED,
                description="Energy and bandwidth cost estimation for TRC-20 transfers.",
                verification_evidence="Supported via energy_used and net_used calculation"
            ),
            ChainCapability.FINALITY_TRACKING: CapabilityDescriptor(
                capability=ChainCapability.FINALITY_TRACKING,
                status=CapabilityStatus.SUPPORTED,
                description="DPoS consensus by 27 Super Representatives requiring 19 blocks (~57s) for irreversible finality.",
                verification_evidence="19-block SR confirmation depth enforced"
            ),
            ChainCapability.REORG_HANDLING: CapabilityDescriptor(
                capability=ChainCapability.REORG_HANDLING,
                status=CapabilityStatus.SUPPORTED,
                description="Reorg detection on blocks with fewer than 19 SR confirmations.",
                verification_evidence="19-block reorg window tracked"
            ),
            ChainCapability.HOLDER_FORENSICS: CapabilityDescriptor(
                capability=ChainCapability.HOLDER_FORENSICS,
                status=CapabilityStatus.SUPPORTED,
                description="TRC-20 contract owner renouncement and energy model evaluation.",
                verification_evidence="Verified in TronChainAdapter.inspect_token_security"
            ),
        }
        self._profiles["tron:mainnet"] = ChainCapabilityProfile(
            chain_id="tron:mainnet",
            canonical_id="tron:mainnet",
            chain_name="TRON",
            chain_family="tvm",
            consensus_type="Delegated Proof-of-Stake (27 SRs)",
            finality_type="Super Representative Confirmation (19 blocks)",
            finality_blocks_or_slots=19,
            reorg_possible=True,
            native_asset=NativeAssetSpec(
                symbol="TRX", name="TRON", decimals=6, smallest_unit_name="SUN", conversion_factor=1_000_000
            ),
            capabilities=caps
        )

    def get_profile(self, chain_id: str) -> Optional[ChainCapabilityProfile]:
        return self._profiles.get(chain_id)

    def get_capability_status(self, chain_id: str, capability: ChainCapability) -> CapabilityStatus:
        profile = self.get_profile(chain_id)
        if not profile:
            return CapabilityStatus.UNSUPPORTED
        desc = profile.capabilities.get(capability)
        return desc.status if desc else CapabilityStatus.UNSUPPORTED

    def list_all_chain_ids(self) -> List[str]:
        # Return unique canonical chain IDs (excluding alias solana:mainnet)
        seen = set()
        result = []
        for cid, p in self._profiles.items():
            if p.canonical_id not in seen:
                seen.add(p.canonical_id)
                result.append(p.canonical_id)
        return result

    def get_capability_matrix(self) -> Dict[str, Dict[str, str]]:
        matrix = {}
        for chain_id in self.list_all_chain_ids():
            prof = self._profiles[chain_id]
            matrix[chain_id] = {
                cap.value: desc.status.value
                for cap, desc in prof.capabilities.items()
            }
        return matrix

capability_registry = ChainCapabilityRegistry()
chain_capability_registry = capability_registry
