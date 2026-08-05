# QA Acceptance Summary

This summary records the current verified behavior from the stabilization phase.

## Current Status

Status: ready for controlled pilot / acceptance deployment.

Not yet recommended for broad public production until final hosting, backup, monitoring, and operational ownership are confirmed.

## Verified Regression Tests

Latest safe test suite:

```bash
unset LCCCA_IAS_RUN_E2E
PYTHONPYCACHEPREFIX=/tmp/lcca-pycache /tmp/lcca-ias-runtime/bin/python -m pytest -q -rs tests
```

Result:

```text
71 passed, 1 skipped
```

The default safe suite skips `tests/test_v2_features.py::test_v2_live_e2e` because it requires an explicitly opted-in live server.

Explicit skipped live-E2E review:

```bash
unset LCCCA_IAS_RUN_E2E
PYTHONPYCACHEPREFIX=/tmp/lcca-pycache /tmp/lcca-ias-runtime/bin/python -m pytest -q -rs \
  tests/e2e_financial_tests.py tests/test_v2_features.py
```

Result:

```text
2 skipped
```

Both skipped tests are intentionally environment-dependent and require `LCCCA_IAS_RUN_E2E=1` plus disposable live-server configuration.

## Browser Acceptance Evidence

Disposable database used:

```text
/tmp/lcca_acceptance_8000.db
```

Verified browser flow:

- Login as administrator.
- Create one parent with employer details.
- Create three learners.
- Confirm unique 10-digit learner codes.
- Assign physical addresses.
- Assign grade-specific fees.
- Open invoice interface.
- Select parent aggregated invoice mode.
- Generate one combined invoice.
- Refresh invoice page and confirm invoice remains.
- Attempt duplicate generation and confirm no duplicate invoice is created.
- Open invoice listing and confirm Parent and Linked Learners are primary columns.
- Expand an invoice and confirm learner-specific line items are visible.

Observed parent invoice:

```text
Invoice: INV-2026-000011
Parent: Acceptance Parent
Billing period: 2026-08
Current charges: 5473.45
Outstanding balance: 5473.45
```

Database evidence:

```text
Invoices for parent: 1
Invoice item rows: 9
Invoice ledger debit rows: 3
Item total: 5473.45
Ledger debit total: 5473.45
```

The nine invoice items come from three linked learners, each with the test fee plus seeded mandatory monthly fees.

Browser smoke screenshot:

```text
/tmp/lcca_rc_invoice_listing_smoke.png
```

## Requirements Traceability

| Requirement | Status | Evidence |
|---|---|---|
| Permanent unique 10-digit learner number | Pass | Learner API/model tests and browser acceptance codes |
| Learners filtered/grouped by grade | Pass | `/api/learners?grade=...`, `/api/learners/by-grade` |
| Fee catalogue assignable by grade | Pass | Fee item grade filter and learner assignment tests |
| Learner physical address | Pass | Learner schema/model/API acceptance data |
| Parent employer details | Pass | Parent schema/model/API acceptance data |
| One aggregated invoice per parent | Pass | `/api/parents/{id}/generate-invoice` |
| Invoice tab organized by parent | Pass | Invoice table now uses Parent and Linked Learners columns |
| Learner-specific invoice item breakdown | Pass | `InvoiceItem.learner_id` and DB evidence |
| Exact totals and ledger reconciliation | Pass | Financial regression tests and DB evidence |
| Duplicate invoice prevention | Pass | Regression tests and browser duplicate attempt |

## Known Limitations

- Docker Compose should be updated with Redis before being treated as final production compose.
- Long-term schema changes should use Alembic migrations.
- Production hosting choice still needs approval: LAN-only, Tailscale/VPN, or Cloudflare Tunnel.
