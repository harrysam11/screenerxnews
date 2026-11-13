from typing import Dict, Any, List, Optional
from decimal import Decimal
from datetime import datetime, timezone
from app.core.chains import chain_registry
from app.services.evm_provider_manager import evm_provider_manager
from app.services.multichain_scorer import multichain_scorer
from app.services.paper_simulation_engine import paper_simulation_engine
from app.services.narrative_correlator import narrative_correlator

class DashboardService:
    """
    Aggregates multi-chain intelligence, market data, and paper desk performance
    into actionable analyst dashboard views.
    """
    DASHBOARD_VERSION = "v2_multichain_dashboard"

    @classmethod
    def get_overview_kpis(cls) -> Dict[str, Any]:
        # Calculate aggregated paper desk equity in USD
        sol_acc = paper_simulation_engine.get_account("solana:mainnet")
        base_acc = paper_simulation_engine.get_account("eip155:8453")
        bnb_acc = paper_simulation_engine.get_account("eip155:56")

        # Mock reference prices for valuation
        sol_val_usd = (sol_acc.total_equity_native or Decimal("100.0")) * Decimal("200.0")
        base_val_usd = (base_acc.total_equity_native or Decimal("10.0")) * Decimal("2800.0")
        bnb_val_usd = (bnb_acc.total_equity_native or Decimal("50.0")) * Decimal("600.0")
        total_paper_usd = sol_val_usd + base_val_usd + bnb_val_usd

        active_chains = chain_registry.list_active_chains()

        return {
            "tracked_tokens_count": 1420,
            "high_conviction_count": 18,
            "scams_blocked_count": 342,
            "total_paper_equity_usd": round(total_paper_usd, 2),
            "active_chains_count": len(active_chains),
            "dual_orchestrator_status": {
                "windmill": {"status": "ONLINE", "worker_threads": 4, "queued_jobs": 0},
                "n8n": {"status": "ONLINE", "active_workflows": 6, "webhook_listener": "ACTIVE"},
                "hermes": {"status": "READ_ONLY", "mode": "ANALYST_STANDBY"}
            },
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

    @classmethod
    def get_screener_feed(
        cls,
        chain_id: Optional[str] = None,
        min_liquidity: Optional[Decimal] = None,
        min_score: Optional[Decimal] = None,
        sector: Optional[str] = None,
        limit: int = 20,
        offset: int = 0
    ) -> Dict[str, Any]:
        # Catalog of multi-chain screened tokens
        catalog = [
            {
                "chain_id": "solana:mainnet",
                "asset_address": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
                "symbol": "BONK",
                "name": "Bonk",
                "liquidity_usd": Decimal("450000.0"),
                "volume_24h_usd": Decimal("12500000.0"),
                "composite_score": Decimal("88.5"),
                "hard_reject_triggered": False,
                "is_mint_revoked": True,
                "is_lp_burned": True,
                "sector": "ANIMALS"
            },
            {
                "chain_id": "eip155:8453",
                "asset_address": "0x4ed4e862860bed51a9570b96d89af5e1b0efefed",
                "symbol": "DEGEN",
                "name": "Degen",
                "liquidity_usd": Decimal("680000.0"),
                "volume_24h_usd": Decimal("8400000.0"),
                "composite_score": Decimal("86.0"),
                "hard_reject_triggered": False,
                "is_mint_revoked": True,
                "is_lp_burned": True,
                "sector": "MEME"
            },
            {
                "chain_id": "eip155:42161",
                "asset_address": "0xSafeArbToken1234567890abcdef12345678",
                "symbol": "ARBAPE",
                "name": "Arbitrum Ape",
                "liquidity_usd": Decimal("180000.0"),
                "volume_24h_usd": Decimal("950000.0"),
                "composite_score": Decimal("82.0"),
                "hard_reject_triggered": False,
                "is_mint_revoked": True,
                "is_lp_burned": True,
                "sector": "ANIMALS"
            },
            {
                "chain_id": "eip155:1",
                "asset_address": "0xHoneypotScamContract1234567890abcdef",
                "symbol": "TRAP",
                "name": "Trap Coin",
                "liquidity_usd": Decimal("120000.0"),
                "volume_24h_usd": Decimal("300000.0"),
                "composite_score": None,
                "hard_reject_triggered": True,
                "rejection_reasons": ["HONEYPOT_DETECTED: Cannot sell"],
                "is_mint_revoked": False,
                "is_lp_burned": False,
                "sector": "MEME"
            }
        ]

        filtered = []
        for t in catalog:
            if chain_id and t["chain_id"] != chain_id:
                continue
            if min_liquidity and t["liquidity_usd"] < min_liquidity:
                continue
            if min_score and (t["composite_score"] is None or t["composite_score"] < min_score):
                continue
            if sector and t.get("sector") != sector.upper():
                continue
            filtered.append(t)

        total_matches = len(filtered)
        paginated = filtered[offset: offset + limit]

        return {
            "total_matches": total_matches,
            "limit": limit,
            "offset": offset,
            "tokens": paginated
        }

    @classmethod
    def get_chains_matrix(cls) -> List[Dict[str, Any]]:
        chains = chain_registry.list_active_chains()
        matrix = []
        for c in chains:
            active_rpc = c.rpc_urls[0] if c.rpc_urls else None
            matrix.append({
                "chain_id": c.id,
                "name": c.name,
                "namespace": c.namespace,
                "native_asset": c.native_asset_symbol,
                "finality_blocks": c.finality_blocks,
                "rpc_url": active_rpc,
                "explorer_base_url": c.explorer_base_url,
                "status": "OPERATIONAL"
            })
        return matrix

    @classmethod
    def get_narratives_overview(cls) -> Dict[str, Any]:
        return {
            "trending_sectors": [
                {"sector": "AI", "sentiment_score": 0.85, "velocity_rank": 1, "top_token": "DEEPSEEK"},
                {"sector": "ANIMALS", "sentiment_score": 0.65, "velocity_rank": 2, "top_token": "BONK"},
                {"sector": "POLITICS", "sentiment_score": 0.40, "velocity_rank": 3, "top_token": "TRUMP"},
                {"sector": "DEPIN", "sentiment_score": 0.70, "velocity_rank": 4, "top_token": "HNT"}
            ],
            "recent_headlines": [
                {"title": "OpenAI and DeepSeek announce decentralized model weights", "sector": "AI", "sentiment": 0.90},
                {"title": "Base network daily volume breaks all-time high", "sector": "MEME", "sentiment": 0.75}
            ]
        }

dashboard_service = DashboardService()
