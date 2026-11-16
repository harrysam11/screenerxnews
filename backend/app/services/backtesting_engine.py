"""
P1.5-E Deterministic Backtesting Engine.
Executes historical strategy simulations on validated OHLCV candles.
Enforces closed-candle execution, signal-to-fill next-bar timing (no lookahead),
realistic slippage and trading fees, stop-loss and take-profit handling,
equity curve generation, and comprehensive financial performance metrics.
"""

from typing import List, Optional, Dict, Any, Tuple
from decimal import Decimal, getcontext, ROUND_HALF_UP
from datetime import datetime, timezone

from app.schemas.ohlcv import OHLCVCandleDTO, CandleBarStatus
from app.strategy_sdk.schemas import (
    DeclarativeStrategyDTO,
    EvaluationStatus
)
from app.strategy_sdk.evaluator import strategy_evaluator
from app.schemas.backtesting import (
    BacktestConfigDTO,
    BacktestTradeDTO,
    EquityPointDTO,
    BacktestResultDTO,
    TradeSide,
    TradeExitReason
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


class BacktestingEngine:
    """
    Deterministic chronological historical backtesting simulator.
    Strictly isolated from live trading, wallet keys, and operational storage.
    """

    @classmethod
    def run_backtest(
        cls,
        strategy: DeclarativeStrategyDTO,
        candles: List[OHLCVCandleDTO],
        config: BacktestConfigDTO
    ) -> BacktestResultDTO:
        """
        Runs candle-by-candle backtest simulation over closed candles.
        Signal evaluated on Bar t Close -> Filled at Bar t+1 Open (with slippage and fees).
        """
        # Filter closed candles only
        eval_candles = [c for c in candles if c.bar_status == CandleBarStatus.CLOSED]
        n_candles = len(eval_candles)
        data_quality_notes: List[str] = []

        if n_candles < 2:
            data_quality_notes.append("Insufficient candles for backtesting simulation (minimum 2 closed bars required).")
            return cls._empty_result(strategy, config, eval_candles, data_quality_notes)

        # Simulation state
        cash = config.initial_balance_usd
        peak_equity = config.initial_balance_usd
        max_drawdown_usd = DECIMAL_ZERO
        max_drawdown_pct = DECIMAL_ZERO

        trades: List[BacktestTradeDTO] = []
        equity_curve: List[EquityPointDTO] = []
        skipped_trades = 0

        fee_rate = Decimal(str(config.fee_rate_bps)) / DECIMAL_BPS_DIVISOR
        slippage_rate = Decimal(str(config.slippage_bps)) / DECIMAL_BPS_DIVISOR

        # Active position: None or Dict
        # {'trade_id', 'entry_bar', 'entry_time', 'entry_price', 'qty', 'size_usd', 'entry_fee', 'entry_slip'}
        active_pos: Optional[Dict[str, Any]] = None
        pending_entry: bool = False
        pending_exit: bool = False

        for i in range(n_candles):
            curr_candle = eval_candles[i]

            # -------------------------------------------------------------
            # 1. Process Pending Fills from Previous Bar's Close Signal
            # -------------------------------------------------------------
            if pending_exit and active_pos is not None:
                # Exit filled at current bar's Open price with slippage
                raw_exit_price = curr_candle.open_price
                effective_exit_price = raw_exit_price * (DECIMAL_ONE - slippage_rate) # selling at lower price
                gross_proceeds = active_pos["qty"] * effective_exit_price
                exit_fee = gross_proceeds * fee_rate
                exit_slippage_cost = active_pos["qty"] * (raw_exit_price - effective_exit_price)
                net_proceeds = gross_proceeds - exit_fee

                # Calculate trade PnL
                total_fees = active_pos["entry_fee"] + exit_fee
                total_slip = active_pos["entry_slip"] + exit_slippage_cost
                cost_basis = active_pos["size_usd"]
                gross_pnl = gross_proceeds - cost_basis
                net_pnl = net_proceeds - cost_basis
                ret_pct = (net_pnl / cost_basis) * DECIMAL_HUNDRED

                cash += net_proceeds

                trades.append(BacktestTradeDTO(
                    trade_id=active_pos["trade_id"],
                    side=TradeSide.BUY,
                    entry_bar_index=active_pos["entry_bar"],
                    entry_timestamp_utc=active_pos["entry_time"],
                    entry_price=quantize_dec(active_pos["entry_price"]),
                    exit_bar_index=i,
                    exit_timestamp_utc=curr_candle.timestamp_utc,
                    exit_price=quantize_dec(effective_exit_price),
                    exit_reason=TradeExitReason.SIGNAL,
                    quantity=quantize_dec(active_pos["qty"]),
                    size_usd=quantize_dec(cost_basis),
                    fees_paid_usd=quantize_dec(total_fees),
                    slippage_paid_usd=quantize_dec(total_slip),
                    gross_pnl_usd=quantize_dec(gross_pnl),
                    net_pnl_usd=quantize_dec(net_pnl),
                    return_pct=quantize_dec(ret_pct),
                    holding_period_bars=i - active_pos["entry_bar"]
                ))
                active_pos = None
                pending_exit = False

            elif pending_entry and active_pos is None:
                # Check cash availability
                order_size = min(config.position_size_usd, cash)
                if order_size >= Decimal("10.0"):
                    raw_entry_price = curr_candle.open_price
                    effective_entry_price = raw_entry_price * (DECIMAL_ONE + slippage_rate) # buying at higher price
                    entry_fee = order_size * fee_rate
                    net_capital_for_tokens = order_size - entry_fee
                    qty = net_capital_for_tokens / effective_entry_price
                    entry_slip_cost = qty * (effective_entry_price - raw_entry_price)

                    cash -= order_size
                    trade_id = f"tr_{curr_candle.timestamp_unix}_{i}"
                    active_pos = {
                        "trade_id": trade_id,
                        "entry_bar": i,
                        "entry_time": curr_candle.timestamp_utc,
                        "entry_price": effective_entry_price,
                        "raw_price": raw_entry_price,
                        "qty": qty,
                        "size_usd": order_size,
                        "entry_fee": entry_fee,
                        "entry_slip": entry_slip_cost
                    }
                else:
                    skipped_trades += 1
                pending_entry = False

            # -------------------------------------------------------------
            # 2. Intra-Bar Risk Trigger Checking (Stop Loss / Take Profit)
            # -------------------------------------------------------------
            if active_pos is not None:
                entry_p = active_pos["entry_price"]
                sl_hit = False
                tp_hit = False
                exit_price = DECIMAL_ZERO
                exit_reason = TradeExitReason.SIGNAL

                if config.stop_loss_pct:
                    sl_threshold = entry_p * (DECIMAL_ONE - (config.stop_loss_pct / DECIMAL_HUNDRED))
                    if curr_candle.low_price <= sl_threshold:
                        sl_hit = True
                        exit_price = sl_threshold * (DECIMAL_ONE - slippage_rate)
                        exit_reason = TradeExitReason.STOP_LOSS

                if not sl_hit and config.take_profit_pct:
                    tp_threshold = entry_p * (DECIMAL_ONE + (config.take_profit_pct / DECIMAL_HUNDRED))
                    if curr_candle.high_price >= tp_threshold:
                        tp_hit = True
                        exit_price = tp_threshold * (DECIMAL_ONE - slippage_rate)
                        exit_reason = TradeExitReason.TAKE_PROFIT

                if sl_hit or tp_hit:
                    gross_proceeds = active_pos["qty"] * exit_price
                    exit_fee = gross_proceeds * fee_rate
                    net_proceeds = gross_proceeds - exit_fee
                    cost_basis = active_pos["size_usd"]
                    gross_pnl = gross_proceeds - cost_basis
                    net_pnl = net_proceeds - cost_basis
                    ret_pct = (net_pnl / cost_basis) * DECIMAL_HUNDRED

                    cash += net_proceeds
                    trades.append(BacktestTradeDTO(
                        trade_id=active_pos["trade_id"],
                        side=TradeSide.BUY,
                        entry_bar_index=active_pos["entry_bar"],
                        entry_timestamp_utc=active_pos["entry_time"],
                        entry_price=quantize_dec(active_pos["entry_price"]),
                        exit_bar_index=i,
                        exit_timestamp_utc=curr_candle.timestamp_utc,
                        exit_price=quantize_dec(exit_price),
                        exit_reason=exit_reason,
                        quantity=quantize_dec(active_pos["qty"]),
                        size_usd=quantize_dec(cost_basis),
                        fees_paid_usd=quantize_dec(active_pos["entry_fee"] + exit_fee),
                        slippage_paid_usd=quantize_dec(active_pos["entry_slip"]),
                        gross_pnl_usd=quantize_dec(gross_pnl),
                        net_pnl_usd=quantize_dec(net_pnl),
                        return_pct=quantize_dec(ret_pct),
                        holding_period_bars=i - active_pos["entry_bar"]
                    ))
                    active_pos = None

            # -------------------------------------------------------------
            # 3. Bar Close Evaluation (Zero Lookahead)
            # -------------------------------------------------------------
            eval_slice = eval_candles[:i + 1]
            eval_res = strategy_evaluator.evaluate_strategy(
                strategy=strategy,
                candles=eval_slice,
                bar_index=i
            )

            if active_pos is None and not pending_entry:
                if eval_res.entry_matched:
                    pending_entry = True

            elif active_pos is not None and not pending_exit:
                if eval_res.exit_matched is True:
                    pending_exit = True

            # -------------------------------------------------------------
            # 4. Mark-to-Market Portfolio Equity Calculation
            # -------------------------------------------------------------
            unrealized_pnl = DECIMAL_ZERO
            if active_pos is not None:
                current_val = active_pos["qty"] * curr_candle.close_price
                unrealized_pnl = current_val - active_pos["size_usd"]
                total_equity = cash + current_val
            else:
                total_equity = cash

            if total_equity > peak_equity:
                peak_equity = total_equity
            dd_usd = peak_equity - total_equity
            dd_pct = (dd_usd / peak_equity) * DECIMAL_HUNDRED if peak_equity > DECIMAL_ZERO else DECIMAL_ZERO

            if dd_usd > max_drawdown_usd:
                max_drawdown_usd = dd_usd
            if dd_pct > max_drawdown_pct:
                max_drawdown_pct = dd_pct

            equity_curve.append(EquityPointDTO(
                bar_index=i,
                timestamp_utc=curr_candle.timestamp_utc,
                cash_balance_usd=quantize_dec(cash),
                unrealized_pnl_usd=quantize_dec(unrealized_pnl),
                total_equity_usd=quantize_dec(total_equity),
                drawdown_usd=quantize_dec(dd_usd),
                drawdown_pct=quantize_dec(dd_pct)
            ))

        # -----------------------------------------------------------------
        # 5. End of Simulation Reconciliation
        # -----------------------------------------------------------------
        if active_pos is not None:
            # Liquidate remaining position at final bar close
            final_bar = eval_candles[-1]
            raw_exit_p = final_bar.close_price
            effective_exit_p = raw_exit_p * (DECIMAL_ONE - slippage_rate)
            gross_proceeds = active_pos["qty"] * effective_exit_p
            exit_fee = gross_proceeds * fee_rate
            net_proceeds = gross_proceeds - exit_fee
            cost_basis = active_pos["size_usd"]
            gross_pnl = gross_proceeds - cost_basis
            net_pnl = net_proceeds - cost_basis
            ret_pct = (net_pnl / cost_basis) * DECIMAL_HUNDRED

            cash += net_proceeds
            trades.append(BacktestTradeDTO(
                trade_id=active_pos["trade_id"],
                side=TradeSide.BUY,
                entry_bar_index=active_pos["entry_bar"],
                entry_timestamp_utc=active_pos["entry_time"],
                entry_price=quantize_dec(active_pos["entry_price"]),
                exit_bar_index=n_candles - 1,
                exit_timestamp_utc=final_bar.timestamp_utc,
                exit_price=quantize_dec(effective_exit_p),
                exit_reason=TradeExitReason.END_OF_DATA,
                quantity=quantize_dec(active_pos["qty"]),
                size_usd=quantize_dec(cost_basis),
                fees_paid_usd=quantize_dec(active_pos["entry_fee"] + exit_fee),
                slippage_paid_usd=quantize_dec(active_pos["entry_slip"]),
                gross_pnl_usd=quantize_dec(gross_pnl),
                net_pnl_usd=quantize_dec(net_pnl),
                return_pct=quantize_dec(ret_pct),
                holding_period_bars=(n_candles - 1) - active_pos["entry_bar"]
            ))

        # Performance summary metrics
        total_trades = len(trades)
        winning_trades = sum(1 for t in trades if t.net_pnl_usd > DECIMAL_ZERO)
        losing_trades = sum(1 for t in trades if t.net_pnl_usd < DECIMAL_ZERO)
        win_rate = (Decimal(str(winning_trades)) / Decimal(str(total_trades))) * DECIMAL_HUNDRED if total_trades > 0 else DECIMAL_ZERO

        net_pnl_total = cash - config.initial_balance_usd
        total_return_pct = (net_pnl_total / config.initial_balance_usd) * DECIMAL_HUNDRED

        gross_profit = sum((t.net_pnl_usd for t in trades if t.net_pnl_usd > DECIMAL_ZERO), DECIMAL_ZERO)
        gross_loss = abs(sum((t.net_pnl_usd for t in trades if t.net_pnl_usd < DECIMAL_ZERO), DECIMAL_ZERO))
        profit_factor = quantize_dec(gross_profit / gross_loss) if gross_loss > DECIMAL_ZERO else (Decimal("999.0") if gross_profit > DECIMAL_ZERO else None)

        expectancy = quantize_dec(net_pnl_total / Decimal(str(total_trades))) if total_trades > 0 else DECIMAL_ZERO
        avg_trade = expectancy
        avg_win = quantize_dec(gross_profit / Decimal(str(winning_trades))) if winning_trades > 0 else DECIMAL_ZERO
        avg_loss = quantize_dec(-gross_loss / Decimal(str(losing_trades))) if losing_trades > 0 else DECIMAL_ZERO

        total_fees_paid = sum(t.fees_paid_usd for t in trades)
        total_slippage_paid = sum(t.slippage_paid_usd for t in trades)

        now_utc = datetime.now(timezone.utc).isoformat()
        backtest_id = f"bt_{int(datetime.now(timezone.utc).timestamp())}_{strategy.strategy.id}"

        return BacktestResultDTO(
            backtest_id=backtest_id,
            strategy_id=strategy.strategy.id,
            strategy_version=strategy.strategy.version,
            chain_id=config.chain_id,
            pool_address=config.pool_address,
            timeframe=config.timeframe,
            candles_evaluated=n_candles,
            initial_balance_usd=quantize_dec(config.initial_balance_usd),
            ending_balance_usd=quantize_dec(cash),
            net_pnl_usd=quantize_dec(net_pnl_total),
            total_return_pct=quantize_dec(total_return_pct),
            total_trades=total_trades,
            winning_trades=winning_trades,
            losing_trades=losing_trades,
            win_rate_pct=quantize_dec(win_rate),
            profit_factor=profit_factor,
            gross_profit_usd=quantize_dec(gross_profit),
            gross_loss_usd=quantize_dec(gross_loss),
            max_drawdown_usd=quantize_dec(max_drawdown_usd),
            max_drawdown_pct=quantize_dec(max_drawdown_pct),
            expectancy_usd=expectancy,
            average_trade_pnl_usd=avg_trade,
            average_win_usd=avg_win,
            average_loss_usd=avg_loss,
            total_fees_paid_usd=quantize_dec(total_fees_paid),
            total_slippage_paid_usd=quantize_dec(total_slippage_paid),
            skipped_trades_count=skipped_trades,
            data_quality_notes=data_quality_notes,
            trades=trades,
            equity_curve=equity_curve,
            run_at_utc=now_utc
        )

    @classmethod
    def _empty_result(cls, strategy, config, candles, notes) -> BacktestResultDTO:
        now_utc = datetime.now(timezone.utc).isoformat()
        return BacktestResultDTO(
            backtest_id=f"bt_empty_{int(datetime.now(timezone.utc).timestamp())}",
            strategy_id=strategy.strategy.id,
            strategy_version=strategy.strategy.version,
            chain_id=config.chain_id,
            pool_address=config.pool_address,
            timeframe=config.timeframe,
            candles_evaluated=len(candles),
            initial_balance_usd=quantize_dec(config.initial_balance_usd),
            ending_balance_usd=quantize_dec(config.initial_balance_usd),
            net_pnl_usd=DECIMAL_ZERO,
            total_return_pct=DECIMAL_ZERO,
            total_trades=0,
            winning_trades=0,
            losing_trades=0,
            win_rate_pct=DECIMAL_ZERO,
            profit_factor=None,
            gross_profit_usd=DECIMAL_ZERO,
            gross_loss_usd=DECIMAL_ZERO,
            max_drawdown_usd=DECIMAL_ZERO,
            max_drawdown_pct=DECIMAL_ZERO,
            expectancy_usd=DECIMAL_ZERO,
            average_trade_pnl_usd=DECIMAL_ZERO,
            average_win_usd=DECIMAL_ZERO,
            average_loss_usd=DECIMAL_ZERO,
            total_fees_paid_usd=DECIMAL_ZERO,
            total_slippage_paid_usd=DECIMAL_ZERO,
            skipped_trades_count=0,
            data_quality_notes=notes,
            trades=[],
            equity_curve=[],
            run_at_utc=now_utc
        )


# Global backtesting engine singleton
backtesting_engine = BacktestingEngine()
