import os
import sqlite3
import threading
from typing import Dict, Any, List, Optional
from decimal import Decimal
from datetime import datetime, timezone

class LedgerError(Exception):
    """Base error for financial ledger violations."""
    pass

class UnbalancedJournalError(LedgerError):
    """Raised when a journal's debits do not equal its credits or line count < 2."""
    pass

class DuplicatePostingError(LedgerError):
    """Raised when a journal with the same idempotency key is posted twice."""
    pass

class ImmutableLedgerError(LedgerError):
    """Raised when an attempt is made to update or delete a posted ledger line or journal."""
    pass

class DoubleEntryLedgerService:
    """
    ACID-compliant double-entry ledger engine enforcing:
    - At least 2 ledger lines per journal.
    - Sum of Debits == Sum of Credits per currency / valuation rate.
    - Exact integer and Decimal precision without floating point drift.
    - Append-only immutability (UPDATE and DELETE prohibited).
    - Idempotency key deduplication.
    - Atomic rollback on partial failure.
    """
    VALID_ACCOUNT_CODES = {
        "WALLET_SOL", "WALLET_TOKEN", "EXPENSE_NETWORK_FEE", "EXPENSE_DEX_FEE", "REALIZED_PNL"
    }

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../data"))
            os.makedirs(base_dir, exist_ok=True)
            self.db_path = os.path.join(base_dir, "ledger.db")
        else:
            self.db_path = db_path

        self._lock = threading.Lock()
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    def _init_db(self):
        with self._lock:
            with self._get_connection() as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS ledger_journals (
                        id TEXT PRIMARY KEY,
                        transaction_signature TEXT UNIQUE,
                        order_id TEXT,
                        idempotency_key TEXT NOT NULL UNIQUE,
                        journal_type TEXT NOT NULL,
                        description TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'DRAFT',
                        posted_at TEXT NOT NULL
                    );
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS ledger_lines (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        journal_id TEXT NOT NULL REFERENCES ledger_journals(id) ON DELETE RESTRICT,
                        account_code TEXT NOT NULL,
                        wallet_address TEXT NOT NULL,
                        mint_address TEXT NOT NULL,
                        debit_amount_raw TEXT NOT NULL,
                        credit_amount_raw TEXT NOT NULL,
                        usd_rate TEXT NOT NULL,
                        debit_usd_units INTEGER NOT NULL DEFAULT 0,
                        credit_usd_units INTEGER NOT NULL DEFAULT 0,
                        CHECK (debit_usd_units >= 0 AND credit_usd_units >= 0),
                        CHECK (CAST(debit_amount_raw AS NUMERIC) >= 0 AND CAST(credit_amount_raw AS NUMERIC) >= 0)
                    );
                """)
                # Database triggers enforcing invariants and immutability (LEDGER-01 through LEDGER-06)
                # 1. Prevent direct insert of POSTED journal without lines
                conn.execute("""
                    CREATE TRIGGER IF NOT EXISTS trg_prevent_direct_posted_insert
                    BEFORE INSERT ON ledger_journals
                    WHEN NEW.status = 'POSTED'
                    BEGIN
                        SELECT RAISE(ABORT, 'FINANCIAL INTEGRITY VIOLATION (LEDGER-03): Direct INSERT with status=POSTED forbidden. Journals must be created in DRAFT.');
                    END;
                """)
                # 2. Status transition MUST be strictly DRAFT -> POSTED. Status rollback or invalid transitions are aborted.
                conn.execute("""
                    CREATE TRIGGER IF NOT EXISTS trg_enforce_valid_status_transition
                    BEFORE UPDATE OF status ON ledger_journals
                    WHEN NOT (OLD.status = 'DRAFT' AND NEW.status = 'POSTED')
                    BEGIN
                        SELECT RAISE(ABORT, 'FINANCIAL INTEGRITY VIOLATION: Status rollback or invalid transition forbidden. State POSTED is irreversible.');
                    END;
                """)
                # 3. On status transition to POSTED, verify lines >= 2 and exact integer balance (debit_usd_units == credit_usd_units)
                conn.execute("""
                    CREATE TRIGGER IF NOT EXISTS trg_check_journal_post_invariants
                    BEFORE UPDATE OF status ON ledger_journals
                    WHEN NEW.status = 'POSTED' AND OLD.status = 'DRAFT'
                    BEGIN
                        SELECT RAISE(ABORT, 'FINANCIAL INTEGRITY VIOLATION (LEDGER-03): Journal has fewer than 2 lines.')
                        WHERE (SELECT COUNT(*) FROM ledger_lines WHERE journal_id = NEW.id) < 2;

                        SELECT RAISE(ABORT, 'FINANCIAL INTEGRITY VIOLATION (LEDGER-01): Journal debits and credits do not balance in exact integer units.')
                        WHERE (
                            SELECT COALESCE(SUM(debit_usd_units - credit_usd_units), 0)
                            FROM ledger_lines WHERE journal_id = NEW.id
                        ) != 0;
                    END;
                """)
                # 4. Once a journal is POSTED, ANY update whatsoever is permanently blocked!
                conn.execute("""
                    CREATE TRIGGER IF NOT EXISTS trg_prevent_posted_journal_update
                    BEFORE UPDATE ON ledger_journals
                    WHEN OLD.status = 'POSTED'
                    BEGIN
                        SELECT RAISE(ABORT, 'FINANCIAL INTEGRITY VIOLATION (LEDGER-05): Posted ledger journals cannot be modified or reverted.');
                    END;
                """)
                # 5. Prevent deleting POSTED journals
                conn.execute("""
                    CREATE TRIGGER IF NOT EXISTS trg_prevent_posted_journal_delete
                    BEFORE DELETE ON ledger_journals
                    WHEN OLD.status = 'POSTED'
                    BEGIN
                        SELECT RAISE(ABORT, 'FINANCIAL INTEGRITY VIOLATION (LEDGER-06): Posted ledger journals cannot be deleted.');
                    END;
                """)
                # 6. Prevent inserting lines into already POSTED journals
                conn.execute("""
                    CREATE TRIGGER IF NOT EXISTS trg_prevent_line_insert_on_posted
                    BEFORE INSERT ON ledger_lines
                    BEGIN
                        SELECT RAISE(ABORT, 'FINANCIAL INTEGRITY VIOLATION: Cannot insert lines into an already POSTED journal.')
                        WHERE (SELECT status FROM ledger_journals WHERE id = NEW.journal_id) = 'POSTED';
                    END;
                """)
                # 7. Line immutability: lines can never be updated or deleted
                conn.execute("""
                    CREATE TRIGGER IF NOT EXISTS trg_prevent_line_update
                    BEFORE UPDATE ON ledger_lines
                    BEGIN
                        SELECT RAISE(ABORT, 'FINANCIAL INTEGRITY VIOLATION (LEDGER-05): Posted ledger lines cannot be modified.');
                    END;
                """)
                conn.execute("""
                    CREATE TRIGGER IF NOT EXISTS trg_prevent_line_delete
                    BEFORE DELETE ON ledger_lines
                    BEGIN
                        SELECT RAISE(ABORT, 'FINANCIAL INTEGRITY VIOLATION (LEDGER-06): Posted ledger lines cannot be deleted.');
                    END;
                """)
                conn.commit()

    def post_journal(
        self,
        journal_id: str,
        idempotency_key: str,
        journal_type: str,
        description: str,
        lines: List[Dict[str, Any]],
        transaction_signature: Optional[str] = None,
        order_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Atomically posts a double-entry balanced journal.
        - LEDGER-01: Balanced journal succeeds.
        - LEDGER-02: Unbalanced journal fails.
        - LEDGER-03: Empty journal fails (< 2 lines).
        - LEDGER-04: Single-line journal fails (< 2 lines).
        - LEDGER-07: Atomic rollback on failure.
        - LEDGER-08: Duplicate posting rejected.
        """
        # 1. LEDGER-03 & LEDGER-04: Validate Line Count (>= 2 lines required)
        if not lines or len(lines) < 2:
            raise UnbalancedJournalError(
                f"LEDGER VIOLATION (LEDGER-03/04): Every posted journal must contain at least 2 lines (received {len(lines) if lines else 0})."
            )

        # 2. LEDGER-02: Validate Double-Entry Balance
        total_debits_usd = Decimal("0.0")
        total_credits_usd = Decimal("0.0")

        parsed_lines = []
        for line in lines:
            account = line.get("account_code")
            if account not in self.VALID_ACCOUNT_CODES:
                raise LedgerError(f"Invalid account code: {account}")

            debit = Decimal(str(line.get("debit_amount_raw", 0)))
            credit = Decimal(str(line.get("credit_amount_raw", 0)))
            usd_rate = Decimal(str(line.get("usd_rate", 1.0)))

            if debit < Decimal("0") or credit < Decimal("0"):
                raise LedgerError("Debits and credits must be non-negative")
            if debit == Decimal("0") and credit == Decimal("0"):
                raise LedgerError("Ledger line must have at least one non-zero side")

            total_debits_usd += debit * usd_rate
            total_credits_usd += credit * usd_rate

            usd_units_scale = Decimal("100000000")
            parsed_lines.append({
                "account_code": account,
                "wallet_address": line["wallet_address"],
                "mint_address": line["mint_address"],
                "debit_amount_raw": str(debit),
                "credit_amount_raw": str(credit),
                "usd_rate": str(usd_rate),
                "debit_usd_units": int(debit * usd_rate * usd_units_scale),
                "credit_usd_units": int(credit * usd_rate * usd_units_scale)
            })

        if total_debits_usd != total_credits_usd:
            raise UnbalancedJournalError(
                f"DOUBLE-ENTRY BALANCE VIOLATION (LEDGER-02): Total debits ({total_debits_usd}) != Total credits ({total_credits_usd})."
            )

        now_iso = datetime.now(timezone.utc).isoformat()

        # 3. Atomic Database Insertion with Rollback Protection
        with self._lock:
            conn = self._get_connection()
            try:
                # Check idempotency duplicate
                cur = conn.execute("SELECT id FROM ledger_journals WHERE idempotency_key = ?", (idempotency_key,))
                if cur.fetchone():
                    raise DuplicatePostingError(
                        f"DUPLICATE POSTING VIOLATION (LEDGER-08): Idempotency key '{idempotency_key}' already consumed."
                    )

                conn.execute("BEGIN TRANSACTION;")
                conn.execute("""
                    INSERT INTO ledger_journals (
                        id, transaction_signature, order_id, idempotency_key, journal_type, description, status, posted_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 'DRAFT', ?)
                """, (
                    journal_id, transaction_signature, order_id, idempotency_key, journal_type, description, now_iso
                ))

                for pl in parsed_lines:
                    conn.execute("""
                        INSERT INTO ledger_lines (
                            journal_id, account_code, wallet_address, mint_address,
                            debit_amount_raw, credit_amount_raw, usd_rate,
                            debit_usd_units, credit_usd_units
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        journal_id, pl["account_code"], pl["wallet_address"], pl["mint_address"],
                        pl["debit_amount_raw"], pl["credit_amount_raw"], pl["usd_rate"],
                        pl["debit_usd_units"], pl["credit_usd_units"]
                    ))

                # Database trigger fires on UPDATE to POSTED: verifies lines >= 2 and exact integer balance == 0
                conn.execute("""
                    UPDATE ledger_journals
                    SET status = 'POSTED'
                    WHERE id = ?
                """, (journal_id,))

                conn.commit()
                return {
                    "journal_id": journal_id,
                    "idempotency_key": idempotency_key,
                    "status": "POSTED",
                    "lines_count": len(parsed_lines),
                    "total_usd": str(total_debits_usd),
                    "posted_at": now_iso
                }
            except Exception as e:
                conn.rollback()
                if "UNIQUE constraint failed" in str(e):
                    raise DuplicatePostingError(f"Duplicate posting rejected: {e}")
                raise
            finally:
                conn.close()

    def get_journal(self, journal_id: str) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            cur = conn.execute("SELECT * FROM ledger_journals WHERE id = ?", (journal_id,))
            j_row = cur.fetchone()
            if not j_row:
                return None
            journal = dict(j_row)
            l_cur = conn.execute("SELECT * FROM ledger_lines WHERE journal_id = ? ORDER BY id ASC", (journal_id,))
            journal["lines"] = [dict(r) for r in l_cur.fetchall()]
            return journal

    def mutate_journal(self, journal_id: str, new_description: str) -> None:
        """Attempts to mutate a posted journal, triggering immutability violation (LEDGER-05)."""
        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute("UPDATE ledger_journals SET description = ? WHERE id = ?", (new_description, journal_id))
                conn.commit()
            except sqlite3.DatabaseError as e:
                raise ImmutableLedgerError(f"LEDGER-05 VIOLATION: {e}")
            finally:
                conn.close()

    def delete_journal(self, journal_id: str) -> None:
        """Attempts to delete a posted journal, triggering immutability violation (LEDGER-06)."""
        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute("DELETE FROM ledger_journals WHERE id = ?", (journal_id,))
                conn.commit()
            except sqlite3.DatabaseError as e:
                raise ImmutableLedgerError(f"LEDGER-06 VIOLATION: {e}")
            finally:
                conn.close()

double_entry_ledger_service = DoubleEntryLedgerService()
