-- ============================================================================
-- SOLANA MEMECOIN OS - LEDGER INTEGRITY & APPEND-ONLY HARDENING (MIGRATION 004)
-- PostgreSQL 14+ compatible
-- Phase P10.2-A Remediation: LEDGER-01 through LEDGER-08
-- ============================================================================

-- 1. Remove ON DELETE CASCADE on ledger lines; enforce RESTRICT
ALTER TABLE live_ledger_lines 
    DROP CONSTRAINT IF EXISTS live_ledger_lines_journal_id_fkey,
    ADD CONSTRAINT live_ledger_lines_journal_id_fkey 
        FOREIGN KEY (journal_id) REFERENCES live_ledger_journals(id) ON DELETE RESTRICT;

-- 2. Append-Only Triggers: Block UPDATE, DELETE and TRUNCATE on journals and lines
CREATE OR REPLACE FUNCTION trg_enforce_append_only_ledger()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'FINANCIAL INTEGRITY VIOLATION (LEDGER-05/06): Ledger records are strictly append-only. Modification or deletion is forbidden.';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_prevent_journal_mutation ON live_ledger_journals;
CREATE TRIGGER trg_prevent_journal_mutation
    BEFORE UPDATE OR DELETE ON live_ledger_journals
    FOR EACH ROW EXECUTE FUNCTION trg_enforce_append_only_ledger();

DROP TRIGGER IF EXISTS trg_prevent_journal_truncate ON live_ledger_journals;
CREATE TRIGGER trg_prevent_journal_truncate
    BEFORE TRUNCATE ON live_ledger_journals
    FOR EACH STATEMENT EXECUTE FUNCTION trg_enforce_append_only_ledger();

DROP TRIGGER IF EXISTS trg_prevent_line_mutation ON live_ledger_lines;
CREATE TRIGGER trg_prevent_line_mutation
    BEFORE UPDATE OR DELETE ON live_ledger_lines
    FOR EACH ROW EXECUTE FUNCTION trg_enforce_append_only_ledger();

DROP TRIGGER IF EXISTS trg_prevent_line_truncate ON live_ledger_lines;
CREATE TRIGGER trg_prevent_line_truncate
    BEFORE TRUNCATE ON live_ledger_lines
    FOR EACH STATEMENT EXECUTE FUNCTION trg_enforce_append_only_ledger();

-- 3. Deferred Journal Line Count Verification Trigger (LEDGER-03 & LEDGER-04)
CREATE OR REPLACE FUNCTION trg_verify_journal_line_count()
RETURNS TRIGGER AS $$
DECLARE
    v_line_count INTEGER;
BEGIN
    SELECT COUNT(*) INTO v_line_count
    FROM live_ledger_lines
    WHERE journal_id = NEW.id;

    IF v_line_count < 2 THEN
        RAISE EXCEPTION 'FINANCIAL INTEGRITY VIOLATION (LEDGER-03/04): Journal % must contain at least 2 lines (found %).',
            NEW.id, v_line_count;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_check_journal_line_count ON live_ledger_journals;
CREATE CONSTRAINT TRIGGER trg_check_journal_line_count
    AFTER INSERT OR UPDATE ON live_ledger_journals
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION trg_verify_journal_line_count();

-- 4. Deferred Journal Balance Verification Trigger (LEDGER-01 & LEDGER-02)
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
        RAISE EXCEPTION 'DOUBLE-ENTRY BALANCE VIOLATION (LEDGER-02): Journal % is unbalanced. Total Debits (%) != Total Credits (%).',
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
