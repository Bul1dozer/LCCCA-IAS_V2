-- LCCA-IAS v4 Migration — V2 Feature Additions
-- Run this against an existing v3 database BEFORE deploying v4 code.
-- Safe to re-run: all statements use IF NOT EXISTS / ADD COLUMN (SQLite allows duplicate-safe ALTER).

PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- 1. Learner: physical address
ALTER TABLE learners ADD COLUMN physical_address TEXT;

-- 2. Parent: employer details
ALTER TABLE parents ADD COLUMN employer_name TEXT;
ALTER TABLE parents ADD COLUMN employer_address TEXT;
ALTER TABLE parents ADD COLUMN employer_phone TEXT;
ALTER TABLE parents ADD COLUMN occupation TEXT;

-- 3. Invoice: parent_id for parent-centric billing
--    learner_id becomes optional (NULL for multi-learner parent invoices)
ALTER TABLE invoices ADD COLUMN parent_id INTEGER REFERENCES parents(id);
CREATE INDEX IF NOT EXISTS ix_invoices_parent_id ON invoices(parent_id);
-- Note: on PostgreSQL also run:
-- ALTER TABLE invoices ALTER COLUMN learner_id DROP NOT NULL;

-- 4. InvoiceItem: learner_id to tag which child each line belongs to
ALTER TABLE invoice_items ADD COLUMN learner_id INTEGER REFERENCES learners(id);

-- 5. Password reset tokens
CREATE TABLE IF NOT EXISTS password_reset_tokens (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id            INTEGER NOT NULL REFERENCES users(id),
    token              TEXT    NOT NULL UNIQUE,
    expires_at         DATETIME NOT NULL,
    used               INTEGER NOT NULL DEFAULT 0,
    created_at         DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    requested_from_ip  TEXT
);
CREATE INDEX IF NOT EXISTS ix_prt_token   ON password_reset_tokens(token);
CREATE INDEX IF NOT EXISTS ix_prt_user_id ON password_reset_tokens(user_id);

-- 6. Verify
SELECT 'v4 migration complete. New columns: learners.physical_address, '
    || 'parents.employer_*, invoices.parent_id, invoice_items.learner_id, '
    || 'table: password_reset_tokens' AS status;
