import os
import sqlite3
import uuid
from typing import Dict, List, Optional, Any
from decimal import Decimal
from datetime import datetime, timezone
from app.core.config import settings
from app.schemas.contracts import (
    PaperAccountResponse,
    PaperOrderRequest,
    PaperOrderResponse,
    PaperPositionResponse,
    PaperExecutionEventDTO
)

class PaperSimulationEngine:
    """
    Multi-chain paper trading engine with persistent SQLite backing (Phase P10.2-F),
    strictly isolated virtual desks, constant-product AMM impact curves,
    slippage validation, append-only execution events, and restart recovery.
    """
    ENGINE_VERSION = "v3_persistent_paper_engine"

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

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            # Generate unique in-memory database URI for isolated transient/test instances
            self.db_path = f"file:paper_{uuid.uuid4().hex}?mode=memory&cache=shared"
            self._is_memory = True
        else:
            self.db_path = db_path
            self._is_memory = (self.db_path == ":memory:" or "mode=memory" in self.db_path)

        if not self._is_memory:
            os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)

        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        if self._is_memory:
            conn = sqlite3.connect(self.db_path, uri=True, timeout=30.0, isolation_level=None)
        else:
            conn = sqlite3.connect(self.db_path, timeout=30.0, check_same_thread=False, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        if not self._is_memory:
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute("PRAGMA busy_timeout = 30000;")
        return conn

    def _ensure_columns(self, conn: sqlite3.Connection, table_name: str, required_cols: Dict[str, str]):
        cur = conn.execute(f"PRAGMA table_info({table_name});")
        existing_cols = {row[1] for row in cur.fetchall()}
        for col_name, col_def in required_cols.items():
            if col_name not in existing_cols:
                conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {col_name} {col_def};")

    def _init_db(self):
        with self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS paper_accounts (
                    account_id TEXT PRIMARY KEY,
                    chain_id TEXT UNIQUE NOT NULL,
                    account_name TEXT,
                    native_symbol TEXT,
                    decimals INTEGER DEFAULT 18,
                    initial_balance REAL DEFAULT 0.0,
                    current_balance REAL DEFAULT 0.0,
                    initial_balance_usd REAL DEFAULT 0.0,
                    current_balance_usd REAL DEFAULT 0.0,
                    allocated_balance REAL DEFAULT 0.0,
                    realized_pnl REAL DEFAULT 0.0,
                    unrealized_pnl_usd REAL DEFAULT 0.0,
                    realized_pnl_usd REAL DEFAULT 0.0,
                    winning_trades INTEGER DEFAULT 0,
                    losing_trades INTEGER DEFAULT 0,
                    gross_profits REAL DEFAULT 0.0,
                    gross_losses REAL DEFAULT 0.0,
                    created_at TEXT,
                    updated_at TEXT
                );
            """)

            # Ensure any missing columns in paper_accounts are added
            self._ensure_columns(conn, "paper_accounts", {
                "account_name": "TEXT",
                "native_symbol": "TEXT DEFAULT 'SOL'",
                "decimals": "INTEGER DEFAULT 9",
                "initial_balance": "REAL DEFAULT 0.0",
                "current_balance": "REAL DEFAULT 0.0",
                "initial_balance_usd": "REAL DEFAULT 0.0",
                "current_balance_usd": "REAL DEFAULT 0.0",
                "allocated_balance": "REAL DEFAULT 0.0",
                "realized_pnl": "REAL DEFAULT 0.0",
                "unrealized_pnl_usd": "REAL DEFAULT 0.0",
                "realized_pnl_usd": "REAL DEFAULT 0.0",
                "winning_trades": "INTEGER DEFAULT 0",
                "losing_trades": "INTEGER DEFAULT 0",
                "gross_profits": "REAL DEFAULT 0.0",
                "gross_losses": "REAL DEFAULT 0.0"
            })

            conn.execute("""
                CREATE TABLE IF NOT EXISTS paper_positions (
                    position_id TEXT PRIMARY KEY,
                    chain_id TEXT NOT NULL,
                    asset_id TEXT,
                    asset_address TEXT,
                    symbol TEXT,
                    side TEXT DEFAULT 'BUY',
                    token_amount_raw REAL DEFAULT 0.0,
                    quantity REAL DEFAULT 0.0,
                    cost_basis_native REAL DEFAULT 0.0,
                    cost_basis_usd REAL DEFAULT 0.0,
                    current_market_price_native REAL DEFAULT 0.0,
                    current_price_usd REAL DEFAULT 0.0,
                    entry_price_usd REAL DEFAULT 0.0,
                    unrealized_pnl_native REAL DEFAULT 0.0,
                    unrealized_pnl_usd REAL DEFAULT 0.0,
                    opened_at TEXT,
                    updated_at TEXT
                );
            """)

            self._ensure_columns(conn, "paper_positions", {
                "asset_id": "TEXT",
                "asset_address": "TEXT",
                "symbol": "TEXT",
                "side": "TEXT DEFAULT 'BUY'",
                "token_amount_raw": "REAL DEFAULT 0.0",
                "quantity": "REAL DEFAULT 0.0",
                "cost_basis_native": "REAL DEFAULT 0.0",
                "cost_basis_usd": "REAL DEFAULT 0.0",
                "current_market_price_native": "REAL DEFAULT 0.0",
                "current_price_usd": "REAL DEFAULT 0.0",
                "entry_price_usd": "REAL DEFAULT 0.0",
                "unrealized_pnl_native": "REAL DEFAULT 0.0",
                "unrealized_pnl_usd": "REAL DEFAULT 0.0"
            })

            conn.execute("""
                CREATE TABLE IF NOT EXISTS paper_orders (
                    order_id TEXT PRIMARY KEY,
                    client_order_id TEXT,
                    chain_id TEXT NOT NULL,
                    asset_id TEXT,
                    asset_address TEXT,
                    side TEXT NOT NULL,
                    input_amount_raw REAL DEFAULT 0.0,
                    output_amount_raw REAL DEFAULT 0.0,
                    quantity REAL DEFAULT 0.0,
                    price_usd REAL DEFAULT 0.0,
                    status TEXT NOT NULL,
                    rejection_reason TEXT,
                    effective_price_native REAL DEFAULT 0.0,
                    fee_paid_usd REAL DEFAULT 0.0,
                    created_at TEXT
                );
            """)

            self._ensure_columns(conn, "paper_orders", {
                "client_order_id": "TEXT",
                "asset_id": "TEXT",
                "asset_address": "TEXT",
                "input_amount_raw": "REAL DEFAULT 0.0",
                "output_amount_raw": "REAL DEFAULT 0.0",
                "rejection_reason": "TEXT",
                "effective_price_native": "REAL DEFAULT 0.0"
            })

            conn.execute("""
                CREATE TABLE IF NOT EXISTS paper_execution_events (
                    event_id TEXT PRIMARY KEY,
                    order_id TEXT,
                    chain_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    input_amount_raw REAL NOT NULL,
                    output_amount_raw REAL NOT NULL,
                    effective_price_native REAL NOT NULL,
                    liquidity_impact_bps INTEGER NOT NULL,
                    simulated_slippage_bps INTEGER NOT NULL,
                    simulated_total_fee_native REAL NOT NULL,
                    occurred_at TEXT NOT NULL
                );
            """)

            # Pre-seed default desks if not existing
            now_iso = datetime.now(timezone.utc).isoformat()
            for chain_id, config in self.DEFAULT_INITIAL_BALANCES.items():
                cur = conn.execute("SELECT account_id FROM paper_accounts WHERE chain_id = ?;", (chain_id,))
                if not cur.fetchone():
                    account_id = str(uuid.uuid4())
                    account_name = f"{config['symbol']} Virtual Desk ({chain_id})"
                    conn.execute("""
                        INSERT INTO paper_accounts (
                            account_id, chain_id, account_name, native_symbol, decimals,
                            initial_balance, current_balance, initial_balance_usd, current_balance_usd,
                            allocated_balance, realized_pnl,
                            winning_trades, losing_trades, gross_profits, gross_losses, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0.0, 0.0, 0, 0, 0.0, 0.0, ?, ?);
                    """, (
                        account_id, chain_id, account_name, config["symbol"], config["decimals"],
                        float(config["balance"]), float(config["balance"]),
                        float(config["balance"]) * 200.0, float(config["balance"]) * 200.0,
                        now_iso, now_iso
                    ))
                else:
                    # If account exists but account_name or current_balance is null/zero, initialize them
                    acc_row = conn.execute("SELECT * FROM paper_accounts WHERE chain_id = ?;", (chain_id,)).fetchone()
                    if not acc_row["account_name"] or not acc_row["native_symbol"]:
                        account_name = f"{config['symbol']} Virtual Desk ({chain_id})"
                        conn.execute("""
                            UPDATE paper_accounts SET
                                account_name = ?, native_symbol = ?, decimals = ?,
                                initial_balance = COALESCE(NULLIF(initial_balance, 0.0), ?),
                                current_balance = COALESCE(NULLIF(current_balance, 0.0), ?),
                                updated_at = ?
                            WHERE chain_id = ?;
                        """, (
                            account_name, config["symbol"], config["decimals"],
                            float(config["balance"]), float(config["balance"]), now_iso, chain_id
                        ))

    def reset_desks(self):
        """
        Resets virtual desks to default balances and wipes open positions.
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            conn.execute("DELETE FROM paper_positions;")
            conn.execute("DELETE FROM paper_orders;")
            conn.execute("DELETE FROM paper_execution_events;")
            for chain_id, config in self.DEFAULT_INITIAL_BALANCES.items():
                conn.execute("""
                    UPDATE paper_accounts SET
                        current_balance = ?, allocated_balance = 0.0, realized_pnl = 0.0,
                        winning_trades = 0, losing_trades = 0, gross_profits = 0.0, gross_losses = 0.0,
                        updated_at = ?
                    WHERE chain_id = ?;
                """, (float(config["balance"]), now_iso, chain_id))

    def get_account(self, chain_id: str = "solana:mainnet") -> PaperAccountResponse:
        with self._get_connection() as conn:
            cur = conn.execute("SELECT * FROM paper_accounts WHERE chain_id = ?;", (chain_id,))
            acc = cur.fetchone()
            if not acc:
                raise ValueError(f"Unknown chain_id: {chain_id}")

            curr_bal = Decimal(str(acc["current_balance"] or 0.0))
            alloc_bal = Decimal(str(acc["allocated_balance"] or 0.0))
            realized_pnl = Decimal(str(acc["realized_pnl"] or 0.0))

            # Calculate unrealized PnL from positions
            pos_cur = conn.execute("SELECT * FROM paper_positions WHERE chain_id = ?;", (chain_id,))
            unrealized_pnl = Decimal("0.0")
            for pos in pos_cur.fetchall():
                unrealized_pnl += Decimal(str(pos["unrealized_pnl_native"] or 0.0))

            total_equity = curr_bal + alloc_bal + unrealized_pnl

            winning_trades = acc["winning_trades"] or 0
            losing_trades = acc["losing_trades"] or 0
            total_trades = winning_trades + losing_trades
            win_rate = (Decimal(str(winning_trades)) / Decimal(str(total_trades)) * Decimal("100.0")) if total_trades > 0 else Decimal("0.0")

            gross_profits = Decimal(str(acc["gross_profits"] or 0.0))
            gross_losses = Decimal(str(acc["gross_losses"] or 0.0))
            profit_factor = (gross_profits / gross_losses) if gross_losses > Decimal("0.0") else (Decimal("1.0") if gross_profits == Decimal("0.0") else Decimal("99.9"))

            # For Solana backwards compatibility
            scale_lamports = Decimal("1000000000")
            curr_lamports = int(curr_bal * scale_lamports) if chain_id == "solana:mainnet" else 0
            alloc_lamports = int(alloc_bal * scale_lamports) if chain_id == "solana:mainnet" else 0
            total_equity_lamports = int(total_equity * scale_lamports) if chain_id == "solana:mainnet" else 0
            realized_lamports = int(realized_pnl * scale_lamports) if chain_id == "solana:mainnet" else 0
            unrealized_lamports = int(unrealized_pnl * scale_lamports) if chain_id == "solana:mainnet" else 0

            native_symbol = acc["native_symbol"] or ("SOL" if chain_id == "solana:mainnet" else "ETH")
            account_name = acc["account_name"] or f"{native_symbol} Virtual Desk ({chain_id})"

            return PaperAccountResponse(
                account_id=acc["account_id"],
                account_name=account_name,
                chain_id=chain_id,
                native_symbol=native_symbol,
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
                profit_factor=round(profit_factor, 4),
                winning_trades=winning_trades,
                losing_trades=losing_trades
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
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()

        with self._get_connection() as conn:
            # 1. Check desk existence
            cur = conn.execute("SELECT * FROM paper_accounts WHERE chain_id = ?;", (chain_id,))
            acc = cur.fetchone()
            if not acc:
                raise ValueError(f"Unsupported paper desk chain: {chain_id}")

            # 2. Idempotency check via paper_orders table
            order_cur = conn.execute("SELECT order_id FROM paper_orders WHERE client_order_id = ?;", (request.client_order_id,))
            if order_cur.fetchone():
                return PaperOrderResponse(
                    order_id=str(uuid.uuid4()),
                    client_order_id=request.client_order_id,
                    status="REJECTED",
                    side=request.side,
                    input_amount_raw=request.input_amount_raw,
                    chain_id=chain_id,
                    rejection_reason="DUPLICATE_CLIENT_ORDER_ID: Idempotency conflict",
                    created_at=now
                )

            curr_bal = Decimal(str(acc["current_balance"] or 0.0))
            alloc_bal = Decimal(str(acc["allocated_balance"] or 0.0))
            realized_pnl = Decimal(str(acc["realized_pnl"] or 0.0))
            winning_trades = acc["winning_trades"] or 0
            losing_trades = acc["losing_trades"] or 0
            gross_profits = Decimal(str(acc["gross_profits"] or 0.0))
            gross_losses = Decimal(str(acc["gross_losses"] or 0.0))
            decimals = acc["decimals"] or 18

            # Handle raw units vs decimal units
            if request.input_amount_raw > Decimal("10000"):
                scale = Decimal(str(10 ** decimals))
                normalized_native = request.input_amount_raw / scale
            else:
                normalized_native = request.input_amount_raw

            # 3. Solvency balance check (BEFORE slippage)
            if request.side == "BUY" and normalized_native > curr_bal:
                order_id = str(uuid.uuid4())
                conn.execute("""
                    INSERT INTO paper_orders (
                        order_id, client_order_id, chain_id, asset_id, asset_address, side, input_amount_raw,
                        output_amount_raw, quantity, price_usd, status, rejection_reason, effective_price_native, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 0.0, ?, 0.0, 'REJECTED', ?, 0.0, ?);
                """, (
                    order_id, request.client_order_id, chain_id, request.asset_id, request.asset_id, request.side,
                    float(request.input_amount_raw), float(request.input_amount_raw),
                    "INSUFFICIENT_VIRTUAL_BALANCE: Order size exceeds virtual cash",
                    now_iso
                ))
                return PaperOrderResponse(
                    order_id=order_id,
                    client_order_id=request.client_order_id,
                    status="REJECTED",
                    side=request.side,
                    input_amount_raw=request.input_amount_raw,
                    chain_id=chain_id,
                    rejection_reason="INSUFFICIENT_VIRTUAL_BALANCE: Order size exceeds virtual cash",
                    created_at=now
                )

            # 4. Slippage & AMM impact calculation
            trade_usd = normalized_native * native_price_usd if request.side == "BUY" else normalized_native * current_market_price_native * native_price_usd
            impact_bps = self.calculate_price_impact_bps(trade_usd, pool_liquidity_usd)
            spread_bps = 10
            total_slippage_bps = spread_bps + impact_bps

            if total_slippage_bps > request.max_slippage_bps:
                order_id = str(uuid.uuid4())
                rejection_msg = f"SLIPPAGE_EXCEEDED: Realized slippage {total_slippage_bps} bps exceeds max {request.max_slippage_bps} bps"
                conn.execute("""
                    INSERT INTO paper_orders (
                        order_id, client_order_id, chain_id, asset_id, asset_address, side, input_amount_raw,
                        output_amount_raw, quantity, price_usd, status, rejection_reason, effective_price_native, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 0.0, ?, 0.0, 'REJECTED', ?, 0.0, ?);
                """, (
                    order_id, request.client_order_id, chain_id, request.asset_id, request.asset_id, request.side,
                    float(request.input_amount_raw), float(request.input_amount_raw),
                    rejection_msg, now_iso
                ))
                return PaperOrderResponse(
                    order_id=order_id,
                    client_order_id=request.client_order_id,
                    status="REJECTED",
                    side=request.side,
                    input_amount_raw=request.input_amount_raw,
                    chain_id=chain_id,
                    rejection_reason=rejection_msg,
                    created_at=now
                )

            slippage_penalty = Decimal(1 + total_slippage_bps / 10000)

            # 5. Execute Fill
            pos_key = request.asset_id.lower()
            order_id = str(uuid.uuid4())

            if request.side == "BUY":
                effective_price = current_market_price_native * slippage_penalty
                tokens_received = normalized_native / effective_price

                new_curr_bal = curr_bal - normalized_native
                new_alloc_bal = alloc_bal + normalized_native

                pos_cur = conn.execute("""
                    SELECT * FROM paper_positions 
                    WHERE chain_id = ? AND (LOWER(asset_id) = ? OR LOWER(asset_address) = ?);
                """, (chain_id, pos_key, pos_key))
                pos_row = pos_cur.fetchone()
                if pos_row:
                    existing_tokens = Decimal(str(pos_row["token_amount_raw"] or pos_row["quantity"] or 0.0))
                    existing_cost = Decimal(str(pos_row["cost_basis_native"] or pos_row["cost_basis_usd"] or 0.0))
                    new_tokens = existing_tokens + tokens_received
                    new_cost = existing_cost + normalized_native
                    conn.execute("""
                        UPDATE paper_positions SET
                            token_amount_raw = ?, quantity = ?, cost_basis_native = ?, cost_basis_usd = ?,
                            current_market_price_native = ?, current_price_usd = ?, updated_at = ?
                        WHERE chain_id = ? AND (LOWER(asset_id) = ? OR LOWER(asset_address) = ?);
                    """, (
                        float(new_tokens), float(new_tokens), float(new_cost), float(new_cost),
                        float(current_market_price_native), float(current_market_price_native),
                        now_iso, chain_id, pos_key, pos_key
                    ))
                else:
                    pos_id = str(uuid.uuid4())
                    conn.execute("""
                        INSERT INTO paper_positions (
                            position_id, chain_id, asset_id, asset_address, symbol, side,
                            token_amount_raw, quantity, cost_basis_native, cost_basis_usd,
                            current_market_price_native, current_price_usd, entry_price_usd,
                            unrealized_pnl_native, unrealized_pnl_usd, opened_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, 'BUY', ?, ?, ?, ?, ?, ?, ?, 0.0, 0.0, ?, ?);
                    """, (
                        pos_id, chain_id, request.asset_id, request.asset_id, "TOKEN",
                        float(tokens_received), float(tokens_received),
                        float(normalized_native), float(normalized_native),
                        float(current_market_price_native), float(current_market_price_native),
                        float(current_market_price_native), now_iso, now_iso
                    ))

                conn.execute("""
                    UPDATE paper_accounts SET
                        current_balance = ?, allocated_balance = ?, updated_at = ?
                    WHERE chain_id = ?;
                """, (float(new_curr_bal), float(new_alloc_bal), now_iso, chain_id))

                output_amount = tokens_received

            else: # SELL
                pos_cur = conn.execute("""
                    SELECT * FROM paper_positions 
                    WHERE chain_id = ? AND (LOWER(asset_id) = ? OR LOWER(asset_address) = ?);
                """, (chain_id, pos_key, pos_key))
                pos_row = pos_cur.fetchone()
                tokens_to_sell = normalized_native
                effective_price = current_market_price_native / slippage_penalty

                if pos_row:
                    existing_tokens = Decimal(str(pos_row["token_amount_raw"] or pos_row["quantity"] or 0.0))
                    existing_cost = Decimal(str(pos_row["cost_basis_native"] or pos_row["cost_basis_usd"] or 0.0))
                    if tokens_to_sell >= existing_tokens or abs(tokens_to_sell - existing_tokens) <= Decimal("0.01"):
                        fraction_sold = Decimal("1.0")
                        tokens_to_sell = existing_tokens
                    else:
                        fraction_sold = min(Decimal("1.0"), tokens_to_sell / existing_tokens)

                    allocated_cost_basis = existing_cost * fraction_sold
                    native_proceeds = tokens_to_sell * effective_price
                    realized_gain = native_proceeds - allocated_cost_basis

                    new_curr_bal = curr_bal + native_proceeds
                    new_alloc_bal = max(Decimal("0.0"), alloc_bal - allocated_cost_basis)
                    new_realized_pnl = realized_pnl + realized_gain

                    if realized_gain >= Decimal("0.0"):
                        winning_trades += 1
                        gross_profits += realized_gain
                    else:
                        losing_trades += 1
                        gross_losses += abs(realized_gain)

                    rem_tokens = max(Decimal("0.0"), existing_tokens - tokens_to_sell)
                    rem_cost = max(Decimal("0.0"), existing_cost - allocated_cost_basis)

                    if fraction_sold == Decimal("1.0") or rem_tokens <= Decimal("0.01"):
                        conn.execute("""
                            DELETE FROM paper_positions 
                            WHERE chain_id = ? AND (LOWER(asset_id) = ? OR LOWER(asset_address) = ?);
                        """, (chain_id, pos_key, pos_key))
                    else:
                        conn.execute("""
                            UPDATE paper_positions SET
                                token_amount_raw = ?, quantity = ?, cost_basis_native = ?, cost_basis_usd = ?,
                                current_market_price_native = ?, current_price_usd = ?, updated_at = ?
                            WHERE chain_id = ? AND (LOWER(asset_id) = ? OR LOWER(asset_address) = ?);
                        """, (
                            float(rem_tokens), float(rem_tokens), float(rem_cost), float(rem_cost),
                            float(current_market_price_native), float(current_market_price_native),
                            now_iso, chain_id, pos_key, pos_key
                        ))

                    conn.execute("""
                        UPDATE paper_accounts SET
                            current_balance = ?, allocated_balance = ?, realized_pnl = ?,
                            winning_trades = ?, losing_trades = ?, gross_profits = ?, gross_losses = ?, updated_at = ?
                        WHERE chain_id = ?;
                    """, (
                        float(new_curr_bal), float(new_alloc_bal), float(new_realized_pnl),
                        winning_trades, losing_trades, float(gross_profits), float(gross_losses), now_iso, chain_id
                    ))
                    output_amount = native_proceeds
                else:
                    # Synthetic sell for unseeded test assets
                    native_proceeds = tokens_to_sell * effective_price
                    new_curr_bal = curr_bal + native_proceeds
                    conn.execute("UPDATE paper_accounts SET current_balance = ?, updated_at = ? WHERE chain_id = ?;", (float(new_curr_bal), now_iso, chain_id))
                    output_amount = native_proceeds

            # 6. Record order & execution event
            conn.execute("""
                INSERT INTO paper_orders (
                    order_id, client_order_id, chain_id, asset_id, asset_address, side, input_amount_raw,
                    output_amount_raw, quantity, price_usd, status, rejection_reason, effective_price_native, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'FILLED', NULL, ?, ?);
            """, (
                order_id, request.client_order_id, chain_id, request.asset_id, request.asset_id, request.side,
                float(request.input_amount_raw), float(output_amount),
                float(output_amount), float(effective_price * native_price_usd),
                float(effective_price), now_iso
            ))

            event_id = str(uuid.uuid4())
            conn.execute("""
                INSERT INTO paper_execution_events (
                    event_id, order_id, chain_id, event_type, input_amount_raw,
                    output_amount_raw, effective_price_native, liquidity_impact_bps,
                    simulated_slippage_bps, simulated_total_fee_native, occurred_at
                ) VALUES (?, ?, ?, 'FILL', ?, ?, ?, ?, ?, 0.000005, ?);
            """, (
                event_id, order_id, chain_id,
                float(request.input_amount_raw), float(output_amount),
                float(effective_price), impact_bps, request.max_slippage_bps, now_iso
            ))

            event = PaperExecutionEventDTO(
                event_id=event_id,
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

            return PaperOrderResponse(
                order_id=order_id,
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
        with self._get_connection() as conn:
            cur = conn.execute("SELECT * FROM paper_positions WHERE chain_id = ?;", (chain_id,))
            rows = cur.fetchall()
            positions_list = []
            for p in rows:
                cost_native = Decimal(str(p["cost_basis_native"] or p["cost_basis_usd"] or 0.0))
                curr_price = Decimal(str(p["current_market_price_native"] or p["current_price_usd"] or 0.0))
                tokens = Decimal(str(p["token_amount_raw"] or p["quantity"] or 0.0))
                market_val = tokens * curr_price
                unrealized = market_val - cost_native
                opened_at_str = p["opened_at"] or datetime.now(timezone.utc).isoformat()
                try:
                    opened_at_dt = datetime.fromisoformat(opened_at_str)
                except ValueError:
                    opened_at_dt = datetime.now(timezone.utc)

                asset_ref = p["asset_id"] or p["asset_address"] or "unknown_asset"
                symbol_ref = p["symbol"] or "TOKEN"

                positions_list.append(PaperPositionResponse(
                    position_id=p["position_id"],
                    mint_address=asset_ref,
                    asset_address=asset_ref,
                    chain_id=chain_id,
                    symbol=symbol_ref,
                    token_amount_raw=round(tokens, 4),
                    cost_basis_lamports=int(cost_native * Decimal("1000000000")) if chain_id == "solana:mainnet" else 0,
                    cost_basis_native=round(cost_native, 6),
                    current_market_price_sol=curr_price if chain_id == "solana:mainnet" else Decimal("0.0"),
                    current_market_price_native=round(curr_price, 8),
                    unrealized_pnl_lamports=int(unrealized * Decimal("1000000000")) if chain_id == "solana:mainnet" else 0,
                    unrealized_pnl_native=round(unrealized, 6),
                    opened_at=opened_at_dt
                ))
            return positions_list

paper_simulation_engine = PaperSimulationEngine(db_path=settings.SQLITE_DB_PATH)
