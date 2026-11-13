-- ============================================================================
-- SOLANA MEMECOIN OS - LEDGER INTEGRITY & APPEND-ONLY HARDENING (MIGRATION 004)
-- PostgreSQL 14+ compatible
-- Phase P10.1 Audit Remediation: DEF-01
-- ============================================================================

-- 1. Remove ON DELETE CASCADE on ledger lines; enforce RESTRICT
ALTER TABLE live_ledger_lines 
    DROP CONSTRAINT IF EXISTS live_ledger_lines_journal_id_fkey,
    ADD CONSTRAINT live_ledger_lines_journal_id_fkey 
        FOREIGN KEY (journal_id) REFERENCES live_ledger_journals(id) ON DELETE RESTRICT;

-- 2. Append-Only Trigger: Block UPDATE and DELETE on journals and lines
CREATE OR REPLACE FUNCTION trg_enforce_append_only_ledger()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'FINANCIAL INTEGRITY VIOLATION: Ledger records are strictly append-only. Modification or deletion is forbidden.';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_prevent_journal_mutation ON live_ledger_journals;
CREATE TRIGGER trg_prevent_journal_mutation
    BEFORE UPDATE OR DELETE ON live_ledger_journals
    FOR EACH ROW EXECUTE FUNCTION trg_enforce_append_only_ledger();

DROP TRIGGER IF EXISTS trg_prevent_line_mutation ON live_ledger_lines;
CREATE TRIGGER trg_prevent_line_mutation
    BEFORE UPDATE OR DELETE ON live_ledger_lines
    FOR EACH ROW EXECUTE FUNCTION trg_enforce_append_only_ledger();

-- 3. Deferred Journal Balance Verification Trigger
CREATE OR REPLACE FUNCTION trg_verify_journal_balance()
RETURNS TRIGGER AS $$
DECLARE
    v_journal_id UUID;
    v_total_debit NUMERIC;
    v_total_credit NUMERIC;
BEGIN
    v_journal_id := NEW.journal_id;
    
    SELECT 
        COALESCE(SUM(debit_amount_raw * usd_rate), 0),
        COALESCE(SUM(credit_amount_raw * usd_rate), 0)
    INTO v_total_debit, v_total_credit
    FROM live_ledger_lines
    WHERE journal_id = v_journal_id;

    IF v_total_debit <> v_total_credit THEN
        RAISE EXCEPTION 'DOUBLE-ENTRY BALANCE VIOLATION: Journal % is unbalanced. Total Debits (%) != Total Credits (%).',
            v_journal_id, v_total_debit, v_total_credit;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_check_journal_balance ON live_ledger_lines;
CREATE CONSTRAINT TRIGGER trg_check_journal_balance
    AFTER INSERT OR UPDATE ON live_ledger_lines
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION trg_verify_journal_balance();
