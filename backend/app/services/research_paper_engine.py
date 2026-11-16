"""
P1.5-F Strategy Paper Trading Engine.
Simulates order execution, virtual balances, positions, and risk management
for declarative strategies in research_history.db.
Completely isolated from live trading, wallet keys, and operational database.
"""

import uuid
from typing import List, Dict, Any, Optional
from decimal import Decimal, getcontext, ROUND_HALF_UP
from datetime import datetime, timezone

from app.schemas.ohlcv import OHLCVCandleDTO
from app.strategy_sdk.manager import strategy_manager
from app.services.research_analytics_service import research_analytics_service
from app.schemas.research_paper import (
    PaperOrderStatus,
    PaperOrderSide,
    StrategyPaperAccountDTO,
    StrategyPaperOrderDTO,
    StrategyPaperPositionDTO,
    StrategyPaperStepResponse
)

getcontext().prec = 28

DECIMAL_ZERO = Decimal("0")
DECIMAL_ONE = Decimal("1")
DECIMAL_HUNDRED = Decimal("100")
DECIMAL_BPS_DIVISOR = Decimal("10000")
DEFAULT_PLACES = Decimal("0.00000001")


def quantize_dec(val: Optional[Decimal], places: Decimal = DEFAULT_PLACES) -> Optional[Decimal]:
    if val is None:
        return None
    return val.quantize(places, rounding=ROUND_HALF_UP)


class ResearchPaperTradingEngine:
    """
    Simulated broker and portfolio engine for research strategies.
    Zero real execution capability.
    """

    @property
    def store(self):
        return research_analytics_service.store

    def create_account(self, account_id: str, name: str, initial_balance: Decimal = Decimal("10000.0")) -> StrategyPaperAccountDTO:
        """Initializes a virtual trading account."""
        self.store.create_strategy_paper_account(
            account_id=account_id,
            name=name,
            initial_balance_usd=str(quantize_dec(initial_balance))
        )
        return self.get_account(account_id)

    def get_account(self, account_id: str, current_prices: Optional[Dict[str, Decimal]] = None) -> Optional[StrategyPaperAccountDTO]:
        """Retrieves an account with mark-to-market positions and equity."""
        raw_acc = self.store.get_strategy_paper_account(account_id)
        if not raw_acc:
            return None

        cash = Decimal(raw_acc["cash_balance_usd"])
        init_bal = Decimal(raw_acc["initial_balance_usd"])
        realized_pnl = Decimal(raw_acc["realized_pnl_usd"])

        # Fetch open positions
        raw_positions = self.store.get_strategy_paper_positions(account_id, status="OPEN")
        open_positions: List[StrategyPaperPositionDTO] = []
        total_unrealized_pnl = DECIMAL_ZERO
        total_pos_value = DECIMAL_ZERO

        for rp in raw_positions:
            qty = Decimal(rp["quantity"])
            entry_p = Decimal(rp["entry_price"])
            cost = Decimal(rp["cost_basis_usd"])
            curr_p = (current_prices.get(rp["pool_address"]) if current_prices else None) or entry_p
            curr_val = qty * curr_p
            unrealized = curr_val - cost
            total_unrealized_pnl += unrealized
            total_pos_value += curr_val

            open_positions.append(StrategyPaperPositionDTO(
                position_id=rp["position_id"],
                account_id=rp["account_id"],
                strategy_id=rp["strategy_id"],
                chain_id=rp["chain_id"],
                pool_address=rp["pool_address"],
                side=PaperOrderSide(rp["side"]),
                quantity=quantize_dec(qty),
                entry_price=quantize_dec(entry_p),
                current_price=quantize_dec(curr_p),
                cost_basis_usd=quantize_dec(cost),
                unrealized_pnl_usd=quantize_dec(unrealized),
                realized_pnl_usd=quantize_dec(Decimal(rp["realized_pnl_usd"])),
                status=rp["status"],
                opened_at=rp["opened_at"],
                closed_at=rp.get("closed_at")
            ))

        total_equity = cash + total_pos_value

        # Fetch recent orders
        raw_orders = self.store.get_strategy_paper_orders(account_id, limit=20)
        recent_orders = [
            StrategyPaperOrderDTO(
                order_id=ro["order_id"],
                account_id=ro["account_id"],
                strategy_id=ro["strategy_id"],
                chain_id=ro["chain_id"],
                pool_address=ro["pool_address"],
                side=PaperOrderSide(ro["side"]),
                quantity=quantize_dec(Decimal(ro["quantity"])),
                price=quantize_dec(Decimal(ro["price"])),
                fee_usd=quantize_dec(Decimal(ro["fee_usd"])),
                slippage_usd=quantize_dec(Decimal(ro["slippage_usd"])),
                status=PaperOrderStatus(ro["status"]),
                idempotency_key=ro["idempotency_key"],
                created_at=ro["created_at"],
                filled_at=ro.get("filled_at")
            )
            for ro in raw_orders
        ]

        return StrategyPaperAccountDTO(
            account_id=raw_acc["account_id"],
            name=raw_acc["name"],
            cash_balance_usd=quantize_dec(cash),
            initial_balance_usd=quantize_dec(init_bal),
            realized_pnl_usd=quantize_dec(realized_pnl),
            unrealized_pnl_usd=quantize_dec(total_unrealized_pnl),
            total_equity_usd=quantize_dec(total_equity),
            open_positions=open_positions,
            recent_orders=recent_orders,
            status=raw_acc["status"],
            created_at=raw_acc["created_at"],
            updated_at=raw_acc["updated_at"]
        )

    def list_accounts(self) -> List[Dict[str, Any]]:
        """Lists all virtual paper accounts."""
        return self.store.list_strategy_paper_accounts()

    def step_strategy_paper_trading(
        self,
        account_id: str,
        strategy_id: str,
        candles: List[OHLCVCandleDTO],
        position_size_usd: Decimal = Decimal("1000.0"),
        fee_rate_bps: int = 30,
        slippage_bps: int = 20
    ) -> StrategyPaperStepResponse:
        """
        Evaluates strategy conditions on latest candle and executes simulated order if triggered.
        """
        acc = self.get_account(account_id)
        if not acc:
            raise ValueError(f"Paper account '{account_id}' not found.")

        if not candles:
            return StrategyPaperStepResponse(
                account_id=account_id,
                strategy_id=strategy_id,
                evaluation_matched_entry=False,
                evaluation_matched_exit=False,
                order_executed=False,
                account_summary=acc,
                explanation="No candles provided for strategy evaluation"
            )

        latest_candle = candles[-1]
        eval_res = strategy_manager.evaluate_strategy(
            strategy_id=strategy_id,
            candles=candles
        )

        order_executed = False
        executed_order_dto: Optional[StrategyPaperOrderDTO] = None
        explanation = eval_res.explanation

        fee_rate = Decimal(str(fee_rate_bps)) / DECIMAL_BPS_DIVISOR
        slippage_rate = Decimal(str(slippage_bps)) / DECIMAL_BPS_DIVISOR
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        # Find any existing open position in this pool
        existing_pos = next((p for p in acc.open_positions if p.pool_address.lower() == latest_candle.pool_address.lower()), None)

        # 1. Entry Trigger Check (Buy)
        if eval_res.entry_matched and existing_pos is None:
            order_size = min(position_size_usd, acc.cash_balance_usd)
            if order_size >= Decimal("10.0"):
                raw_price = latest_candle.close_price
                fill_price = raw_price * (DECIMAL_ONE + slippage_rate)
                fee = order_size * fee_rate
                net_capital = order_size - fee
                qty = net_capital / fill_price
                slip_cost = qty * (fill_price - raw_price)

                order_id = f"ord_{int(datetime.now(timezone.utc).timestamp())}_{uuid.uuid4().hex[:6]}"
                idempotency_key = f"{account_id}:{strategy_id}:{latest_candle.timestamp_unix}:BUY"

                # Save order
                self.store.save_strategy_paper_order({
                    "order_id": order_id,
                    "account_id": account_id,
                    "strategy_id": strategy_id,
                    "chain_id": latest_candle.chain_id,
                    "pool_address": latest_candle.pool_address,
                    "side": "BUY",
                    "quantity": qty,
                    "price": fill_price,
                    "fee_usd": fee,
                    "slippage_usd": slip_cost,
                    "status": "FILLED",
                    "idempotency_key": idempotency_key,
                    "created_at": now_str,
                    "filled_at": now_str
                })

                # Create position
                pos_id = f"pos_{uuid.uuid4().hex[:8]}"
                self.store.save_or_update_strategy_paper_position({
                    "position_id": pos_id,
                    "account_id": account_id,
                    "strategy_id": strategy_id,
                    "chain_id": latest_candle.chain_id,
                    "pool_address": latest_candle.pool_address,
                    "side": "BUY",
                    "quantity": qty,
                    "entry_price": fill_price,
                    "cost_basis_usd": order_size,
                    "realized_pnl_usd": DECIMAL_ZERO,
                    "status": "OPEN",
                    "opened_at": now_str
                })

                # Update cash
                new_cash = acc.cash_balance_usd - order_size
                self.store.update_strategy_paper_account_balances(account_id, str(new_cash), str(acc.realized_pnl_usd))

                order_executed = True
                explanation = f"Simulated BUY order filled: {quantize_dec(qty)} tokens @ ${quantize_dec(fill_price)}"
                executed_order_dto = StrategyPaperOrderDTO(
                    order_id=order_id,
                    account_id=account_id,
                    strategy_id=strategy_id,
                    chain_id=latest_candle.chain_id,
                    pool_address=latest_candle.pool_address,
                    side=PaperOrderSide.BUY,
                    quantity=quantize_dec(qty),
                    price=quantize_dec(fill_price),
                    fee_usd=quantize_dec(fee),
                    slippage_usd=quantize_dec(slip_cost),
                    status=PaperOrderStatus.FILLED,
                    idempotency_key=idempotency_key,
                    created_at=now_str,
                    filled_at=now_str
                )

        # 2. Exit Trigger Check (Sell)
        elif eval_res.exit_matched and existing_pos is not None:
            raw_price = latest_candle.close_price
            fill_price = raw_price * (DECIMAL_ONE - slippage_rate)
            gross_proceeds = existing_pos.quantity * fill_price
            fee = gross_proceeds * fee_rate
            net_proceeds = gross_proceeds - fee
            slip_cost = existing_pos.quantity * (raw_price - fill_price)

            realized_pnl = net_proceeds - existing_pos.cost_basis_usd
            new_cash = acc.cash_balance_usd + net_proceeds
            new_total_realized = acc.realized_pnl_usd + realized_pnl

            order_id = f"ord_{int(datetime.now(timezone.utc).timestamp())}_{uuid.uuid4().hex[:6]}"
            idempotency_key = f"{account_id}:{strategy_id}:{latest_candle.timestamp_unix}:SELL"

            # Save order
            self.store.save_strategy_paper_order({
                "order_id": order_id,
                "account_id": account_id,
                "strategy_id": strategy_id,
                "chain_id": latest_candle.chain_id,
                "pool_address": latest_candle.pool_address,
                "side": "SELL",
                "quantity": existing_pos.quantity,
                "price": fill_price,
                "fee_usd": fee,
                "slippage_usd": slip_cost,
                "status": "FILLED",
                "idempotency_key": idempotency_key,
                "created_at": now_str,
                "filled_at": now_str
            })

            # Close position
            self.store.save_or_update_strategy_paper_position({
                "position_id": existing_pos.position_id,
                "account_id": account_id,
                "strategy_id": strategy_id,
                "chain_id": latest_candle.chain_id,
                "pool_address": latest_candle.pool_address,
                "quantity": DECIMAL_ZERO,
                "entry_price": existing_pos.entry_price,
                "cost_basis_usd": DECIMAL_ZERO,
                "realized_pnl_usd": realized_pnl,
                "status": "CLOSED",
                "opened_at": existing_pos.opened_at,
                "closed_at": now_str
            })

            # Update cash & realized PnL
            self.store.update_strategy_paper_account_balances(account_id, str(new_cash), str(new_total_realized))

            order_executed = True
            explanation = f"Simulated SELL order filled: {quantize_dec(existing_pos.quantity)} tokens @ ${quantize_dec(fill_price)}, Realized PnL: ${quantize_dec(realized_pnl)}"
            executed_order_dto = StrategyPaperOrderDTO(
                order_id=order_id,
                account_id=account_id,
                strategy_id=strategy_id,
                chain_id=latest_candle.chain_id,
                pool_address=latest_candle.pool_address,
                side=PaperOrderSide.SELL,
                quantity=quantize_dec(existing_pos.quantity),
                price=quantize_dec(fill_price),
                fee_usd=quantize_dec(fee),
                slippage_usd=quantize_dec(slip_cost),
                status=PaperOrderStatus.FILLED,
                idempotency_key=idempotency_key,
                created_at=now_str,
                filled_at=now_str
            )

        updated_acc = self.get_account(account_id, current_prices={latest_candle.pool_address: latest_candle.close_price})

        return StrategyPaperStepResponse(
            account_id=account_id,
            strategy_id=strategy_id,
            evaluation_matched_entry=eval_res.entry_matched,
            evaluation_matched_exit=bool(eval_res.exit_matched),
            order_executed=order_executed,
            order=executed_order_dto,
            account_summary=updated_acc,
            explanation=explanation
        )


# Global singleton instance
research_paper_engine = ResearchPaperTradingEngine()
