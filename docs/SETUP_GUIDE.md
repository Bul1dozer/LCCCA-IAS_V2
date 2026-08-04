# Setup Guide

This guide is the recommended starting point for installing and running LCCCA-IAS.

## Recommended Choice

For daily school use, run the system on a dedicated local computer or mini-server at the school.

Use:

- PostgreSQL for the database
- Redis for production rate limiting
- A wired network connection
- A UPS battery backup
- Staff access through the school LAN, Tailscale/VPN, or Cloudflare Tunnel with access controls

Do not expose PostgreSQL or Redis directly to the internet.

## Setup Options

| Option | Best For | Database | Remote Access |
|---|---|---|---|
| Local test | Development and QA | SQLite | None |
| School pilot | One office machine, limited users | PostgreSQL | LAN |
| Daily school use | Multiple staff users | PostgreSQL | LAN plus private tunnel/VPN |
| Public URL | Staff access away from school | PostgreSQL | HTTPS reverse proxy or Cloudflare Tunnel |

SQLite is useful for testing, but PostgreSQL is the recommended database before real financial records are entered.

## Option 1: Local Test Setup

Use this for QA or demonstrations only.

1. Create a virtual environment.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

2. Create the environment file.

```bash
cp .env.example .env
```

3. Edit `.env`.

Minimum local settings:

```bash
LCCA_ENV=development
DATABASE_URL=sqlite:///./lcca.db
LCCA_SESSION_COOKIE_SECURE=false
LCCA_RATE_LIMIT_BACKEND=memory
```

4. Start the app.

```bash
set -a
source .env
set +a
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

5. Open:

```text
http://127.0.0.1:8000
```

Default seeded login:

```text
Username: admin
Password: admin123
```

Change the admin password before any real use.

## Option 2: School LAN Setup

Use this when staff are using the system from computers on the school network.

1. Prepare a dedicated computer or mini-server.

Recommended minimum:

- 8 GB RAM
- SSD storage
- Wired Ethernet
- UPS backup
- macOS, Linux, or Windows with WSL2

2. Install services.

Install:

- Python 3.11 or 3.12
- PostgreSQL
- Redis

3. Create the PostgreSQL database.

```bash
createdb lcca_ias
createuser lcca
psql -d lcca_ias -c "alter user lcca with encrypted password 'CHANGE_ME';"
psql -d lcca_ias -c "grant all privileges on database lcca_ias to lcca;"
```

4. Create `.env`.

```bash
cp .env.example .env
```

5. Set production-style values.

```bash
LCCA_ENV=production
LCCA_SECRET_KEY=replace-with-long-random-secret
LCCA_TOTP_ENCRYPTION_KEY=replace-with-fernet-key
DATABASE_URL=postgresql://lcca:CHANGE_ME@localhost:5432/lcca_ias
LCCA_CORS_ORIGINS=http://SERVER-IP:8000
LCCA_SESSION_COOKIE_SECURE=false
LCCA_RATE_LIMIT_BACKEND=redis
LCCA_RATE_LIMIT_REDIS_URL=redis://localhost:6379/0
```

For plain local LAN HTTP, `LCCA_SESSION_COOKIE_SECURE=false` is required because browsers only send secure cookies over HTTPS. When HTTPS is added, set it to `true`.

6. Start the app on the LAN.

```bash
source .venv/bin/activate
set -a
source .env
set +a
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

7. Staff open:

```text
http://SERVER-IP:8000
```

## Option 3: Remote Staff Access

Recommended approaches:

1. Tailscale or VPN
2. Cloudflare Tunnel with access controls
3. HTTPS reverse proxy with a controlled domain

Tailscale/VPN is the simplest staff-only option. Cloudflare Tunnel is useful when the school wants a stable URL without opening inbound firewall ports.

Ngrok is useful for a short demo, but it is not recommended as the permanent production access method.

For any remote setup:

- Use HTTPS.
- Keep app authentication enabled.
- Enable TOTP for administrator accounts.
- Restrict access to staff.
- Do not expose the database or Redis.
- Confirm backups before entering real financial data.

## Option 4: Docker Compose

Use Docker when the school wants repeatable service startup.

1. Create `.env`.

```bash
cp .env.example .env
```

2. Edit `.env` with production values.

3. Start the compose stack.

```bash
docker compose --profile postgres up -d
```

Current note: the compose file should include Redis before being treated as the final production compose stack.

## First-Run Checklist

- Open `/health`.
- Log in as `admin`.
- Change the seeded admin password.
- Create named admin accounts for real users.
- Enable TOTP for administrators.
- Configure SMTP or the approved mail provider.
- Confirm password reset email delivery.
- Create a test parent and two learners.
- Assign test fees.
- Generate one parent aggregated invoice.
- Confirm invoice total and ledger debit total match.
- Configure daily backups.
- Perform a restore test before real use.

## Documentation Map

- [Deployment Guide](DEPLOYMENT.md): hosting choices, LAN and tunnel recommendations
- [Database Guide](DATABASE.md): SQLite vs PostgreSQL setup
- [Backup and Restore Guide](BACKUP_AND_RESTORE.md): backup commands and restore drills
- [Security Guide](SECURITY.md): secrets, TOTP, CSRF, rate limiting, and reset tokens
- [Operations Guide](OPERATIONS.md): daily workflows
- [QA Acceptance Summary](QA_ACCEPTANCE_SUMMARY.md): verified tests and remaining limitations
