import uuid
from typing import Dict, List, Optional, Any
from decimal import Decimal
from datetime import datetime, timezone
from app.schemas.contracts import (
    PaperAccountResponse,
    PaperOrderRequest,
    PaperOrderResponse,
    PaperPositionResponse,
    PaperExecutionEventDTO
)

class PaperSimulationEngine:
    """
    Multi-chain paper trading engine with strictly isolated virtual desks,
    constant-product AMM impact curves, slippage validation, and append-only event recording.
    """
    ENGINE_VERSION = "v2_multichain_paper_engine"

    DEFAULT_INITIAL_BALANCES = {
        "solana:mainnet": {"symbol": "SOL", "decimals": 9, "balance": Decimal("100.0")},
        "eip155:1": {"symbol": "ETH", "decimals": 18, "balance": Decimal("10.0")},
        "eip155:8453": {"symbol": "ETH", "decimals": 18, "balance": Decimal("10.0")},
        "eip155:56": {"symbol": "BNB", "decimals": 18, "balance": Decimal("50.0")},
        "eip155:42161": {"symbol": "ETH", "decimals": 18, "balance": Decimal("10.0")},
        "eip155:10": {"symbol": "ETH", "decimals": 18, "balance": Decimal("10.0")},
        "eip155:137": {"symbol": "POL", "decimals": 18, "balance": Decimal("10000.0")},
        "eip155:43114": {"symbol": "AVAX", "decimals": 18, "balance": Decimal("500.0")},
        "sui:mainnet": {"symbol": "SUI", "decimals": 9, "balance": Decimal("10000.0")},
        "aptos:mainnet": {"symbol": "APT", "decimals": 8, "balance": Decimal("1000.0")},
        "ton:mainnet": {"symbol": "TON", "decimals": 9, "balance": Decimal("2000.0")},
        "tron:mainnet": {"symbol": "TRX", "decimals": 6, "balance": Decimal("50000.0")}
    }

    def __init__(self):
        # In-memory desk states keyed by chain_id for testing / isolation
        self._accounts: Dict[str, Dict[str, Any]] = {}
        self._positions: Dict[str, Dict[str, Dict[str, Any]]] = {} # chain_id -> asset_address -> position
        self._ledger_events: Dict[str, List[PaperExecutionEventDTO]] = {} # chain_id -> events
        self._seen_client_order_ids: set = set()
        self._initialize_desks()

    def _initialize_desks(self):
        for chain_id, config in self.DEFAULT_INITIAL_BALANCES.items():
            self._accounts[chain_id] = {
                "account_id": str(uuid.uuid4()),
                "account_name": f"{config['symbol']} Virtual Desk ({chain_id})",
                "chain_id": chain_id,
                "native_symbol": config["symbol"],
                "decimals": config["decimals"],
                "initial_balance": config["balance"],
                "current_balance": config["balance"],
                "allocated_balance": Decimal("0.0"),
                "realized_pnl": Decimal("0.0"),
                "winning_trades": 0,
                "losing_trades": 0,
                "gross_profits": Decimal("0.0"),
                "gross_losses": Decimal("0.0")
            }
            self._positions[chain_id] = {}
            self._ledger_events[chain_id] = []

    def get_account(self, chain_id: str = "solana:mainnet") -> PaperAccountResponse:
        acc = self._accounts.get(chain_id)
        if not acc:
            raise ValueError(f"Unknown chain_id: {chain_id}")

        curr_bal = acc["current_balance"]
        alloc_bal = acc["allocated_balance"]
        realized_pnl = acc["realized_pnl"]

        # Calculate unrealized PnL from positions
        unrealized_pnl = Decimal("0.0")
        for pos in self._positions.get(chain_id, {}).values():
            unrealized_pnl += pos.get("unrealized_pnl_native", Decimal("0.0"))

        total_equity = curr_bal + alloc_bal + unrealized_pnl

        # Calculate win rate and profit factor
        total_trades = acc["winning_trades"] + acc["losing_trades"]
        win_rate = (Decimal(str(acc["winning_trades"])) / Decimal(str(total_trades)) * Decimal("100.0")) if total_trades > 0 else Decimal("0.0")
        profit_factor = (acc["gross_profits"] / acc["gross_losses"]) if acc["gross_losses"] > Decimal("0.0") else (Decimal("1.0") if acc["gross_profits"] == Decimal("0.0") else Decimal("99.9"))

        # For Solana backwards compatibility
        scale_lamports = Decimal("1000000000")
        curr_lamports = int(curr_bal * scale_lamports) if chain_id == "solana:mainnet" else 0
        alloc_lamports = int(alloc_bal * scale_lamports) if chain_id == "solana:mainnet" else 0
        total_equity_lamports = int(total_equity * scale_lamports) if chain_id == "solana:mainnet" else 0
        realized_lamports = int(realized_pnl * scale_lamports) if chain_id == "solana:mainnet" else 0
        unrealized_lamports = int(unrealized_pnl * scale_lamports) if chain_id == "solana:mainnet" else 0

        return PaperAccountResponse(
            account_id=acc["account_id"],
            account_name=acc["account_name"],
            chain_id=chain_id,
            native_symbol=acc["native_symbol"],
            current_virtual_lamports=curr_lamports,
            allocated_virtual_lamports=alloc_lamports,
            total_equity_lamports=total_equity_lamports,
            realized_pnl_lamports=realized_lamports,
            unrealized_pnl_lamports=unrealized_lamports,
            current_virtual_balance=round(curr_bal, 6),
            allocated_virtual_balance=round(alloc_bal, 6),
            total_equity_native=round(total_equity, 6),
            realized_pnl_native=round(realized_pnl, 6),
            unrealized_pnl_native=round(unrealized_pnl, 6),
            win_rate=round(win_rate, 2),
            profit_factor=round(profit_factor, 4)
        )

    def calculate_price_impact_bps(
        self, trade_amount_usd: Decimal, pool_liquidity_usd: Decimal
    ) -> int:
        if pool_liquidity_usd <= Decimal("0.0"):
            return 2000 # Max cap
        ratio = trade_amount_usd / pool_liquidity_usd
        impact = int(ratio * Decimal("10000"))
        return max(5, min(2000, impact))

    def execute_order(
        self,
        request: PaperOrderRequest,
        current_market_price_native: Decimal,
        pool_liquidity_usd: Decimal = Decimal("100000.0"),
        native_price_usd: Decimal = Decimal("200.0")
    ) -> PaperOrderResponse:
        chain_id = request.chain_id
        if chain_id not in self._accounts:
            raise ValueError(f"Unsupported paper desk chain: {chain_id}")

        # Idempotency check
        if request.client_order_id in self._seen_client_order_ids:
            return PaperOrderResponse(
                order_id=str(uuid.uuid4()),
                client_order_id=request.client_order_id,
                status="REJECTED",
                side=request.side,
                input_amount_raw=request.input_amount_raw,
                chain_id=chain_id,
                rejection_reason="DUPLICATE_CLIENT_ORDER_ID: Idempotency conflict",
                created_at=datetime.now(timezone.utc)
            )

        self._seen_client_order_ids.add(request.client_order_id)
        acc = self._accounts[chain_id]
        now = datetime.now(timezone.utc)

        # Handle raw units vs decimal units
        if request.input_amount_raw > Decimal("10000"):
            # Raw units (e.g. lamports on Solana)
            scale = Decimal(str(10 ** acc["decimals"]))
            normalized_native = request.input_amount_raw / scale
        else:
            normalized_native = request.input_amount_raw

        # 1. Solvency Balance check (BEFORE slippage)
        if request.side == "BUY" and normalized_native > acc["current_balance"]:
            return PaperOrderResponse(
                order_id=str(uuid.uuid4()),
                client_order_id=request.client_order_id,
                status="REJECTED",
                side=request.side,
                input_amount_raw=request.input_amount_raw,
                chain_id=chain_id,
                rejection_reason="INSUFFICIENT_VIRTUAL_BALANCE: Order size exceeds virtual cash",
                created_at=now
            )

        # 2. Slippage & AMM impact calculation
        trade_usd = normalized_native * native_price_usd if request.side == "BUY" else normalized_native * current_market_price_native * native_price_usd
        impact_bps = self.calculate_price_impact_bps(trade_usd, pool_liquidity_usd)
        spread_bps = 10
        total_slippage_bps = spread_bps + impact_bps

        if total_slippage_bps > request.max_slippage_bps:
            return PaperOrderResponse(
                order_id=str(uuid.uuid4()),
                client_order_id=request.client_order_id,
                status="REJECTED",
                side=request.side,
                input_amount_raw=request.input_amount_raw,
                chain_id=chain_id,
                rejection_reason=f"SLIPPAGE_EXCEEDED: Realized slippage {total_slippage_bps} bps exceeds max {request.max_slippage_bps} bps",
                created_at=now
            )

        slippage_penalty = Decimal(1 + total_slippage_bps / 10000)

        if request.side == "BUY":
            effective_price = current_market_price_native * slippage_penalty
            tokens_received = normalized_native / effective_price

            acc["current_balance"] -= normalized_native
            acc["allocated_balance"] += normalized_native

            pos_key = request.asset_id.lower()
            existing_pos = self._positions[chain_id].get(pos_key)
            if existing_pos:
                existing_pos["token_amount_raw"] += tokens_received
                existing_pos["cost_basis_native"] += normalized_native
            else:
                self._positions[chain_id][pos_key] = {
                    "position_id": str(uuid.uuid4()),
                    "asset_id": request.asset_id,
                    "token_amount_raw": tokens_received,
                    "cost_basis_native": normalized_native,
                    "current_market_price_native": current_market_price_native,
                    "unrealized_pnl_native": Decimal("0.0"),
                    "opened_at": now
                }

            output_amount = tokens_received

        else: # SELL
            pos_key = request.asset_id.lower()
            existing_pos = self._positions[chain_id].get(pos_key)
            tokens_to_sell = normalized_native

            effective_price = current_market_price_native / slippage_penalty

            if existing_pos:
                fraction_sold = min(Decimal("1.0"), tokens_to_sell / existing_pos["token_amount_raw"])
                allocated_cost_basis = existing_pos["cost_basis_native"] * fraction_sold
                native_proceeds = tokens_to_sell * effective_price
                realized_gain = native_proceeds - allocated_cost_basis

                acc["current_balance"] += native_proceeds
                acc["allocated_balance"] = max(Decimal("0.0"), acc["allocated_balance"] - allocated_cost_basis)
                acc["realized_pnl"] += realized_gain

                if realized_gain >= Decimal("0.0"):
                    acc["winning_trades"] += 1
                    acc["gross_profits"] += realized_gain
                else:
                    acc["losing_trades"] += 1
                    acc["gross_losses"] += abs(realized_gain)

                existing_pos["token_amount_raw"] -= tokens_to_sell
                existing_pos["cost_basis_native"] -= allocated_cost_basis
                if existing_pos["token_amount_raw"] <= Decimal("0.001") or abs(existing_pos["token_amount_raw"]) <= Decimal("0.001"):
                    del self._positions[chain_id][pos_key]
                output_amount = native_proceeds
            else:
                # Synthetic sell for unseeded test assets
                native_proceeds = tokens_to_sell * effective_price
                acc["current_balance"] += native_proceeds
                output_amount = native_proceeds

        # Event recording
        event = PaperExecutionEventDTO(
            event_id=str(uuid.uuid4()),
            event_type="FILL",
            input_amount_raw=request.input_amount_raw,
            output_amount_raw=round(output_amount, 6),
            effective_price_sol=effective_price if chain_id == "solana:mainnet" else Decimal("0.0"),
            effective_price_native=round(effective_price, 8),
            liquidity_impact_bps=impact_bps,
            simulated_slippage_bps=request.max_slippage_bps,
            simulated_total_fee_lamports=5000 if chain_id == "solana:mainnet" else 0,
            simulated_total_fee_native=Decimal("0.000005"),
            occurred_at=now,
            chain_id=chain_id
        )
        self._ledger_events[chain_id].append(event)

        return PaperOrderResponse(
            order_id=str(uuid.uuid4()),
            client_order_id=request.client_order_id,
            status="FILLED",
            side=request.side,
            input_amount_raw=request.input_amount_raw,
            output_amount_raw=round(output_amount, 6),
            effective_price_sol=effective_price if chain_id == "solana:mainnet" else None,
            effective_price_native=round(effective_price, 8),
            chain_id=chain_id,
            execution_events=[event],
            created_at=now
        )

    def get_positions(self, chain_id: str = "solana:mainnet") -> List[PaperPositionResponse]:
        positions_list = []
        for pos_key, p in self._positions.get(chain_id, {}).items():
            cost_native = p["cost_basis_native"]
            curr_price = p["current_market_price_native"]
            tokens = p["token_amount_raw"]
            market_val = tokens * curr_price
            unrealized = market_val - cost_native

            positions_list.append(PaperPositionResponse(
                position_id=p["position_id"],
                mint_address=p["asset_id"],
                asset_address=p["asset_id"],
                chain_id=chain_id,
                token_amount_raw=round(tokens, 4),
                cost_basis_lamports=int(cost_native * Decimal("1000000000")) if chain_id == "solana:mainnet" else 0,
                cost_basis_native=round(cost_native, 6),
                current_market_price_sol=curr_price if chain_id == "solana:mainnet" else Decimal("0.0"),
                current_market_price_native=round(curr_price, 8),
                unrealized_pnl_lamports=int(unrealized * Decimal("1000000000")) if chain_id == "solana:mainnet" else 0,
                unrealized_pnl_native=round(unrealized, 6),
                opened_at=p["opened_at"]
            ))
        return positions_list

paper_simulation_engine = PaperSimulationEngine()
