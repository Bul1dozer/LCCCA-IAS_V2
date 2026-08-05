# LCCA-IAS
### Life Changing Christian Church Academy — School Fees Management & Invoice Automation System

Production-grade school financial management platform with dynamic per-learner billing,
automated month-end processing, bank reconciliation, bulk data import, real SMTP delivery,
SMS notifications, TOTP two-factor authentication, and immutable audit logging.

---

## Documentation

Start here for setup and operations:

- [Documentation index](docs/README.md)
- [Setup guide](docs/SETUP_GUIDE.md)
- [Deployment guide](docs/DEPLOYMENT.md)
- [Database guide](docs/DATABASE.md)
- [Backup and restore guide](docs/BACKUP_AND_RESTORE.md)
- [Security guide](docs/SECURITY.md)
- [Operations guide](docs/OPERATIONS.md)
- [QA acceptance summary](docs/QA_ACCEPTANCE_SUMMARY.md)
- [Final stabilization report](docs/FINAL_STABILIZATION_REPORT.md)

Recommended school setup: a dedicated local computer or mini-server running PostgreSQL and Redis, accessed by staff through the LAN, Tailscale/VPN, or a controlled HTTPS tunnel. SQLite is suitable for local testing and short single-user pilots only.

---

## Quick Start

```bash
pip install -r requirements.txt
python3 -m app.generate_logo        # generate school crest once
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Open **http://localhost:8000** — Login: `admin` / `admin123`

The system seeds 12 learners, 11 parents, 14 fee items, transport routes,
invoices, and payment records on first run. No manual setup required.

---

## What's New in v2

| Feature | Detail |
|---|
| **Dynamic Fee Engine** | Per-learner billing profiles replacing the fixed fee-structure model |
| **Fee Catalogue** | 14 seeded items across monthly/term/annual/once-off frequencies |
| **Learner-Specific Billing** | Every active learner has a unique monthly total (proven by test suite) |
| **Transport Routes** | Route-based pricing with per-learner custom amount overrides |
| **Month-End Automation** | APScheduler-based (1st of month, 00:05 Windhoek time) + manual trigger |
| **Real SMTP Email** | Gmail/M365/any SMTP — configured via Settings page |
| **SMS Notifications** | Africa's Talking and BulkSMS providers |
| **TOTP 2FA** | Google Authenticator compatible, setup/verify/disable in Settings |
| **Immutable Audit Log** | Every create/update/delete/login/export captured atomically |
| **Bulk CSV/Excel Import** | Learners, parents, payments with row-level validation reports |
| **Bank Reconciliation** | CSV bank statement import with auto-match + manual match |
| **25-table schema** | Roles, RBAC foundation, notification logs, import jobs, month-end runs |
| **9 Grade Levels** | Baby Class through Grade 9, expandable without schema changes |
| **14 Navigable Pages** | All v1 pages + Fee Catalogue, Month-End, Imports, Recon, Audit, Settings |

---

## Architecture

```
lccca-ias/
├── app/
│   ├── main.py              FastAPI app + scheduler lifecycle
│   ├── database.py          SQLAlchemy (SQLite/WAL default, PostgreSQL-ready)
│   ├── models.py            25-table ORM schema
│   ├── schemas.py           Pydantic request/response models
│   ├── auth.py              PBKDF2 password hashing, session auth
│   ├── audit.py             Immutable audit log writer
│   ├── fee_engine.py        Dynamic per-learner billing engine
│   ├── email_engine.py      SMTP/simulated email + SMS stub
│   ├── scheduler.py         APScheduler month-end automation
│   ├── pdf_generator.py     ReportLab branded PDFs (4 report types + invoice + receipt)
│   ├── seed_data.py         Realistic v2 demo dataset
│   └── routers/
│       ├── auth.py          Login/logout/me
│       ├── learners.py      CRUD + auto mandatory-fee assignment
│       ├── parents.py       CRUD + learner linking
│       ├── fees.py          Legacy fee structures (v1 compat)
│       ├── fee_items.py     Fee catalogue + learner profiles + transport routes
│       ├── payments.py      CRUD + receipt PDF + balance management
│       ├── invoices.py      Generate (v2 profile or v1 fallback) + PDF + send
│       ├── reports.py       4 PDF reports + audit trail
│       ├── email_logs.py    Email send history
│       ├── dashboard.py     KPIs + chart data + activity feed
│       ├── audit_router.py  Searchable, paginated audit log
│       ├── month_end.py     Manual trigger + run history
│       ├── imports.py       CSV/Excel bulk import for learners/parents/payments
│       ├── recon.py         Bank statement import + auto/manual matching
│       └── settings.py      SMTP, SMS, TOTP 2FA, notification logs
├── templates/               16 Jinja2 page shells
├── static/
│   ├── css/lcca.css         Navy/gold academic design system
│   ├── js/lcca-common.js    Shared API client, toasts, formatters
│   └── img/logo.png         School crest
└── requirements.txt
```

---

## Switching to PostgreSQL

```bash
pip install psycopg2-binary
export DATABASE_URL="postgresql://lcca_user:pass@localhost:5432/lcca_db"
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

No code changes required. SQLAlchemy abstracts all dialect differences.

---

## Configuring Real Email Delivery

1. Go to **Settings → Email (SMTP)**
2. Enter your SMTP credentials:
   - **Gmail:** host `smtp.gmail.com`, port `587`, use an App Password
   - **M365:** host `smtp.office365.com`, port `587`
3. Click **Save**, then **Send Test Email** to verify

Until SMTP is configured, all email sends are **simulated** — logged and visible
in Email Logs, but not actually dispatched.

---

## Two-Factor Authentication

1. Go to **Settings → Two-Factor Auth → Set Up 2FA**
2. Scan the QR code with Google Authenticator or Authy
3. Enter the 6-digit code and click **Verify & Enable**

---

## Month-End Automation

The scheduler runs automatically on the **1st of each month at 00:05 Windhoek time**.
To run manually: go to **Month-End** and click **Run Month-End**, selecting a billing period.

The engine:
1. Finds all active learners with monthly fee items assigned
2. Generates one invoice per learner per period (skips if already invoiced)
3. Sends email notification (real SMTP if configured, simulated otherwise)
4. Records the full run log for audit purposes

---

## Future Architecture (Prepared)

The schema and auth layer are pre-scaffolded for:

| Module | Status |
|---|
| Parent Portal | Schema ready (`Role`: Parent), UI pending |
| Accountant Role | Schema ready, permissions defined |
| Principal Dashboard | Schema ready (read-only role) |
| WhatsApp Business | SMS engine stubbed for WhatsApp API |
| Online Payments | Invoice model ready for payment gateway reference fields |
| Multi-School | Database column scaffolding (`school_id` can be added without redesign) |
| AI Analytics | Monthly billing data in structured form, ready for ML pipelines |

---

## School Grades Supported

Baby Class, Toddler Class, Grade 0, Grade 1–9
(Expandable via `VALID_GRADES` in `routers/learners.py` — no DB migration required)
