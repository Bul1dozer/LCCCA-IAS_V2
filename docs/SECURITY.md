# Security Guide

This guide lists the security controls and required production configuration for LCCCA-IAS.

## Required Production Environment

Set:

```bash
LCCA_ENV=production
LCCA_SECRET_KEY=change-me
LCCA_TOTP_ENCRYPTION_KEY=change-me-fernet-key
DATABASE_URL=postgresql://...
LCCA_CORS_ORIGINS=https://your-app-url
LCCA_SESSION_COOKIE_SECURE=true
LCCA_RATE_LIMIT_BACKEND=redis
LCCA_RATE_LIMIT_REDIS_URL=redis://...
```

Production startup should fail if:

- `LCCA_SECRET_KEY` is missing.
- `LCCA_TOTP_ENCRYPTION_KEY` is missing.
- Redis rate limiting is required but not configured.
- Trusted CORS origins are missing.

## Generating Secrets

Session secret:

```bash
python - <<'PY'
import secrets
print(secrets.token_urlsafe(48))
PY
```

TOTP encryption key:

```bash
python - <<'PY'
from cryptography.fernet import Fernet
print(Fernet.generate_key().decode())
PY
```

Store these in `.env` or a proper secret manager. Do not commit them.

## Passwords

Passwords are hashed with PBKDF2.

Rules currently enforced:

- Minimum length: 8 characters.
- Current-password reuse rejected.
- Password reset token single-use.
- Password reset token expiry.
- Generic reset response for unknown accounts.

Recommended operational policy:

- Change the seeded admin password immediately.
- Use unique admin accounts per staff member.
- Disable accounts when staff leave.
- Enable TOTP for all administrators.

## TOTP

TOTP setup:

1. User requests setup.
2. App generates a TOTP secret.
3. Secret is encrypted before storage.
4. User scans QR code.
5. User verifies one valid OTP.
6. Future password login requires OTP.

Security notes:

- Production requires `LCCA_TOTP_ENCRYPTION_KEY`.
- Existing plaintext TOTP secrets are decrypted through a migration path and re-saved encrypted after successful verification.
- The setup endpoint returns the secret only during setup so the user can configure an authenticator.
- Normal login and status endpoints do not return the secret.

## CSRF Protection

Browser state-changing `/api` requests require the CSRF cookie/header pair.

Covered mutation examples:

- Invoice generation
- Payments
- Fee changes
- Learner updates
- Parent updates
- Password changes
- Password reset completion
- 2FA setup/verify/disable
- Month-end trigger
- Imports
- Bank reconciliation mutations

The frontend automatically sends `X-CSRF-Token` for same-origin `/api` mutations.

## Rate Limiting

Development/test may use in-memory rate limiting.

Production should use Redis:

```bash
LCCA_RATE_LIMIT_BACKEND=redis
LCCA_RATE_LIMIT_REDIS_URL=redis://localhost:6379/0
```

Protected areas:

- Login attempts
- Password reset requests
- TOTP login verification attempts

## Email and Reset Tokens

Password reset:

- Raw reset tokens are not exposed in production API responses.
- Stored tokens are HMAC digests, not raw tokens.
- Previous unused tokens are invalidated when a new reset is requested.
- Test/development can use a mail sink.
- SMTP can send real email when configured.

Production should use school-controlled SMTP or another approved provider.

## Network Rules

- Do not expose PostgreSQL publicly.
- Do not expose Redis publicly.
- Put the app behind HTTPS for remote access.
- Use Tailscale, VPN, or Cloudflare Access for staff-only remote access.
- Keep operating system updates current.

## Audit Logging

The system records audit events for critical actions including:

- Login/logout
- Password changes
- Password reset requests/completion
- Parent/learner updates
- Invoice generation
- Payment changes
- 2FA enable/disable

Operational recommendation:

- Review audit logs after month-end.
- Review failed login patterns weekly.
- Preserve audit logs during incident investigation.

