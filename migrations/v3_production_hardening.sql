-- LCCA-IAS v3 Production Hardening Migration
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS invoice_counter (
    id            INTEGER PRIMARY KEY,
    year          INTEGER NOT NULL,
    last_sequence INTEGER NOT NULL DEFAULT 0
);
INSERT OR IGNORE INTO invoice_counter (id, year, last_sequence)
VALUES (1, CAST(strftime('%Y', 'now') AS INTEGER), 0);

CREATE TABLE IF NOT EXISTS ledger_entries (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    learner_id       INTEGER NOT NULL REFERENCES learners(id),
    transaction_type TEXT NOT NULL,
    reference_type   TEXT,
    reference_id     INTEGER,
    amount           NUMERIC(12,2) NOT NULL CHECK(amount > 0),
    dc_indicator     TEXT NOT NULL CHECK(dc_indicator IN ('DR','CR')),
    transaction_date DATE NOT NULL,
    created_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by       TEXT NOT NULL DEFAULT 'migration',
    notes            TEXT,
    is_voided        INTEGER NOT NULL DEFAULT 0,
    voided_at        DATETIME,
    voided_by        TEXT
);
CREATE INDEX IF NOT EXISTS ix_ledger_learner_date ON ledger_entries(learner_id, transaction_date);

ALTER TABLE learners ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1;
ALTER TABLE learners ADD COLUMN deleted_at DATETIME;
ALTER TABLE learners ADD COLUMN deleted_by TEXT;
ALTER TABLE parents ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1;
ALTER TABLE parents ADD COLUMN deleted_at DATETIME;
ALTER TABLE parents ADD COLUMN deleted_by TEXT;
ALTER TABLE payments ADD COLUMN payment_date DATE;
ALTER TABLE payments ADD COLUMN created_by TEXT DEFAULT 'migration';
ALTER TABLE payments ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1;
ALTER TABLE payments ADD COLUMN reversed_at DATETIME;
ALTER TABLE payments ADD COLUMN reversed_by TEXT;
ALTER TABLE payments ADD COLUMN reversal_reason TEXT;
UPDATE payments SET payment_date = date_paid WHERE payment_date IS NULL;
ALTER TABLE invoices ADD COLUMN created_by TEXT DEFAULT 'migration';
ALTER TABLE invoices ADD COLUMN voided_at DATETIME;
ALTER TABLE invoices ADD COLUMN voided_by TEXT;
ALTER TABLE invoices ADD COLUMN void_reason TEXT;
ALTER TABLE invoices ADD COLUMN reversed_by_invoice_id INTEGER REFERENCES invoices(id);

INSERT INTO ledger_entries (learner_id, transaction_type, reference_type, reference_id, amount, dc_indicator, transaction_date, created_by, notes)
SELECT learner_id, 'invoice', 'Invoice', id, CASE WHEN current_charges > 0 THEN current_charges ELSE 0 END, 'DR', issue_date, 'migration', 'Backfilled invoice ' || invoice_number
FROM invoices WHERE status NOT IN ('Void','Reversed') AND current_charges > 0;

INSERT INTO ledger_entries (learner_id, transaction_type, reference_type, reference_id, amount, dc_indicator, transaction_date, created_by, notes)
SELECT learner_id, 'payment', 'Payment', id, amount_paid, 'CR', date_paid, 'migration', 'Backfilled payment'
FROM payments WHERE amount_paid > 0;

UPDATE learners SET balance = (
    SELECT COALESCE(SUM(CASE WHEN dc_indicator='DR' AND is_voided=0 THEN amount ELSE 0 END),0)
         - COALESCE(SUM(CASE WHEN dc_indicator='CR' AND is_voided=0 THEN amount ELSE 0 END),0)
    FROM ledger_entries WHERE learner_id = learners.id
);
