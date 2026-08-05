# Operations Guide

This guide is for day-to-day administrators.

## Start the App

Local pilot:

```bash
source .venv/bin/activate
set -a
source .env
set +a
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

School LAN:

```bash
source .venv/bin/activate
set -a
source .env
set +a
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Health check:

```bash
curl http://127.0.0.1:8000/health
```

## Daily Checks

Before using the system:

- Confirm the app opens.
- Confirm dashboard loads.
- Confirm latest backup completed.
- Confirm no failed background month-end run.
- Confirm email delivery status if invoices will be sent.

## Learner and Parent Workflow

1. Create parent.
2. Add employer details.
3. Create learner.
4. Confirm learner code is 10 digits.
5. Link learner to parent.
6. Assign fee items to learner.
7. Confirm learner appears under the parent detail view.

## Fee Workflow

1. Open Fee Catalogue.
2. Create fee item.
3. Set grade applicability.
4. Assign fee to learner or auto-assign mandatory fees.
5. Confirm assignment before invoice generation.

## Parent Invoice Workflow

1. Open Invoices.
2. Click Generate Invoice.
3. Choose Parent aggregated linked learners.
4. Select parent.
5. Enter billing period.
6. Enter due date.
7. Generate invoice.
8. Confirm one invoice appears for the parent.
9. Confirm all linked learners are represented in invoice items/PDF.

Duplicate behavior:

- If the same parent/period is generated again, already invoiced learners are skipped.
- If all linked learners were already invoiced, the endpoint returns an error instead of duplicating charges.

## Month-End Workflow

1. Open Month-End.
2. Select billing period.
3. Trigger month-end.
4. Wait for completion.
5. Review run log.
6. Check invoice counts.
7. Check dashboard reconciliation.

Month-end should not be run repeatedly without reviewing the previous result.

## Password Reset Workflow

1. User requests password reset.
2. User receives reset email or test mail sink message.
3. User opens reset link.
4. User sets new password.
5. Token becomes invalid after use.

Production reminder:

- Do not enable dev token exposure.
- Use real SMTP or approved mail provider.

## 2FA Workflow

1. User opens Settings.
2. User selects Set Up 2FA.
3. User scans QR code.
4. User enters current OTP.
5. 2FA becomes enabled.
6. Next login requires password plus OTP.

Disable 2FA only when necessary, and require current password plus valid OTP.

## Common Troubleshooting

App will not start:

- Check `.env`.
- Check `DATABASE_URL`.
- Check PostgreSQL/Redis are running.
- Check `LCCA_SECRET_KEY`.
- Check `LCCA_TOTP_ENCRYPTION_KEY`.

Login fails:

- Confirm user is active.
- Confirm password.
- Check if 2FA is enabled.
- Check rate limiting.

Invoice does not generate:

- Confirm learner is active.
- Confirm learner is linked to parent.
- Confirm fee items are assigned.
- Confirm billing period has not already been invoiced.

Email does not send:

- Check SMTP settings.
- Send test email from Settings.
- Check notification logs.
- Check spam folder.

