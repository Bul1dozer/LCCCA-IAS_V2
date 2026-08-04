# Final Stabilization Report

Date: 2026-08-04

Branch: `codex/requirements-security-hardening`

## Commit History

| Commit | Summary |
|---|---|
| `fe2b23b` | chore: ignore generated artifacts |
| `d986326` | financial: enforce invoice integrity and parent routing |
| `f080f0c` | security: harden authentication sessions and recovery |
| `e28b73a` | tests: add security financial and e2e regression coverage |

No push or deployment was performed.

## Git Issue

Git status and diff commands previously hung because the Codex desktop app spawned read-only Git status processes while the repository contained generated artifacts and local runtime files.

Resolution:

- Confirmed no active lock file was in use before cleanup.
- Updated ignores for generated artifacts.
- Removed tracked generated artifacts from the committed state.
- Used temporary Git index operations to create logical commits without staging secrets or runtime data.

## Data Safety

No production data was used.

Acceptance and E2E work used disposable SQLite databases, including:

```text
/tmp/lcca_acceptance_8000.db
```

No real SMTP account, real parent record, real learner record, reset token, TOTP secret, or database backup was committed.

## Test Commands

Complete safe test suite:

```bash
unset LCCCA_IAS_RUN_E2E
PYTHONPYCACHEPREFIX=/tmp/lcca-pycache /tmp/lcca-ias-runtime/bin/python -m pytest -q -rs tests
```

Result:

```text
71 passed, 1 skipped
```

Default skipped test:

```text
tests/test_v2_features.py::test_v2_live_e2e
Reason: Set LCCCA_IAS_RUN_E2E=1 and LCCCA_IAS_BASE_URL to run live E2E tests
Classification: intentionally environment-dependent
```

Explicit live-E2E skip review:

```bash
unset LCCCA_IAS_RUN_E2E
PYTHONPYCACHEPREFIX=/tmp/lcca-pycache /tmp/lcca-ias-runtime/bin/python -m pytest -q -rs \
  tests/e2e_financial_tests.py tests/test_v2_features.py
```

Result:

```text
2 skipped
```

Skipped tests:

```text
tests/e2e_financial_tests.py::test_live_financial_integrity_smoke
Reason: Set LCCCA_IAS_RUN_E2E=1 to run live-server E2E tests.
Classification: intentionally environment-dependent

tests/test_v2_features.py::test_v2_live_e2e
Reason: Set LCCCA_IAS_RUN_E2E=1 and LCCCA_IAS_BASE_URL to run live E2E tests
Classification: intentionally environment-dependent
```

## Requirements Traceability

| Requirement | Status | Implementation Evidence | Test Evidence |
|---|---|---|---|
| Permanent unique 10-digit learner number | Pass | Learner model/API | Model/API tests and browser acceptance |
| Learners filtered and grouped by grade | Pass | Learner list endpoints and UI filters | V2 feature tests and browser acceptance |
| Fee catalogue assignable by grade | Pass | Fee item model/API and learner fee assignment | Fee engine tests |
| Learner physical address | Pass | Learner model/schema/API | Browser acceptance |
| Parent employer details | Pass | Parent model/schema/API | Browser acceptance |
| One aggregated invoice per parent | Pass | `POST /api/parents/{parent_id}/generate-invoice` | Financial integrity tests and browser acceptance |
| Invoice tab organized primarily by parent | Pass | Parent and Linked Learners invoice-table columns | Invoice listing tests |
| Learner-specific invoice item breakdown | Pass | `InvoiceItem.learner_id` | Financial integrity tests |
| Exact financial totals and ledger reconciliation | Pass | Decimal-based invoice and ledger posting | Ledger and financial E2E tests |
| Duplicate invoice prevention | Pass | Parent invoice duplicate checks | Duplicate and partial duplicate tests |

## Browser Acceptance Evidence

Disposable scenario:

- One temporary administrator account
- One temporary parent with employer details
- Three linked learners
- Grade-specific fee items
- Parent aggregated invoice generation
- Duplicate generation attempt

Observed request:

```text
POST /api/parents/{parent_id}/generate-invoice
```

Observed behavior:

- No `learner_id=null` validation error.
- One combined parent invoice created.
- Three learners represented through invoice items.
- Duplicate attempt did not create duplicate charges or ledger entries.
- Invoice listing displayed Parent and Linked Learners as primary columns.
- Expanding a listing row displayed learner-specific line items.

Database evidence:

```text
Invoices for parent: 1
Invoice item rows: 9
Invoice ledger debit rows: 3
Item total: 5473.45
Ledger debit total: 5473.45
```

Screenshot evidence:

```text
/tmp/lcca_acceptance_invoice_ui.png
/tmp/lcca_rc_invoice_listing_smoke.png
```

## Security Evidence

Password change:

- Incorrect current password rejected.
- Weak new password rejected.
- Mismatched confirmation rejected.
- Current-password reuse rejected.
- Successful password change invalidated the old password.
- New password worked.
- Unauthenticated change request rejected.
- Audit event created.

Password reset:

- Reset request returned a generic response.
- Development/test mail sink captured the reset email.
- Raw tokens were not exposed in production response behavior.
- Stored reset tokens were hashed.
- Expired tokens failed.
- Reused tokens failed.
- Old password failed after reset.
- New password worked.

TOTP:

- Setup returned a valid QR/otpauth flow.
- Invalid OTP did not enable 2FA.
- Valid OTP enabled 2FA.
- Password login did not grant a full session before OTP verification.
- Invalid OTP rejected during login.
- Valid OTP completed login.
- 2FA disable required current password and valid OTP.
- TOTP secret was encrypted at rest.

CSRF:

- Browser state-changing `/api` requests require the CSRF cookie/header pair.
- Frontend same-origin API requests send `X-CSRF-Token`.
- Mutation tests send valid CSRF tokens.

Rate limiting:

- Development/test may use memory backend.
- Production requires Redis/shared backend configuration.
- Login and password reset throttling are covered by tests.

## Financial Reconciliation

Verified parent invoice acceptance:

```text
Invoice total: 5473.45
Invoice item total: 5473.45
Ledger debit total: 5473.45
Outstanding balance: 5473.45
```

Financial regression coverage includes:

- Three-learner parent invoice
- Duplicate request
- Partial duplicate request
- Failure before commit
- Failure after flush before commit
- Response failure after commit
- Decimal precision
- Zero and negative charge handling
- Authorization for parent invoice generation

## Migrations

No formal Alembic migrations were introduced.

Current limitation:

- The application still uses SQLAlchemy `Base.metadata.create_all`.
- Long-term production schema evolution should use Alembic.

## Production Environment Variables

Required:

```bash
LCCA_ENV=production
LCCA_SECRET_KEY=...
LCCA_TOTP_ENCRYPTION_KEY=...
DATABASE_URL=postgresql://...
LCCA_CORS_ORIGINS=https://your-app-url
LCCA_SESSION_COOKIE_SECURE=true
LCCA_RATE_LIMIT_BACKEND=redis
LCCA_RATE_LIMIT_REDIS_URL=redis://...
```

Recommended:

```bash
LCCA_SMTP_HOST=...
LCCA_SMTP_PORT=587
LCCA_SMTP_USERNAME=...
LCCA_SMTP_PASSWORD=...
LCCA_SMTP_FROM=...
LCCA_PUBLIC_BASE_URL=https://your-app-url
```

## Deployment Checklist

- Choose hosting model: LAN, Tailscale/VPN, Cloudflare Tunnel, or HTTPS reverse proxy.
- Use PostgreSQL before entering real financial data.
- Use Redis for production rate limiting.
- Configure `.env` from `.env.example`.
- Generate strong session and TOTP encryption secrets.
- Confirm `/health`.
- Change seeded admin password.
- Enable TOTP for administrators.
- Configure SMTP or approved mail provider.
- Run a test parent invoice.
- Confirm invoice totals equal ledger debit totals.
- Configure daily backups.
- Perform a restore test.
- Restrict database and Redis to private access only.

## Rollback Plan

Before any production upgrade:

1. Stop the app.
2. Take a database backup.
3. Record the current commit hash.
4. Deploy the new code to a staging copy first.
5. Verify login, invoice generation, ledger totals, password reset, and 2FA.

If rollback is required:

1. Stop the app.
2. Restore the previous code commit.
3. Restore the database backup if financial writes occurred during the failed upgrade.
4. Start the app.
5. Verify `/health`, login, invoice totals, and ledger totals.

## Remaining Defects

Critical: none known from the targeted acceptance run.

High: none known from the targeted acceptance run.

Medium:

- Docker Compose should be updated with Redis before final production compose use.
- Alembic migrations are recommended before repeated production schema changes.

Low:

- More screenshots can be added to the operations guide for non-technical staff training.

## Readiness Statement

The system is documented and suitable for controlled pilot or acceptance deployment once hosting, backup ownership, and operational procedures are approved.

It should not be declared broad public production-ready until production hosting, backups, monitoring, and operational ownership are finalized.
