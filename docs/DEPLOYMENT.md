# Deployment Guide

This guide explains how to run LCCCA-IAS safely for a local pilot, a school office network, or a container-based deployment.

## Decision Summary

Recommended for LCCCA:

| Scenario | Recommended Setup | Use When |
|---|---|---|
| Quick demo or QA | Local computer, SQLite | Testing only, one user |
| Controlled pilot | Dedicated local computer, PostgreSQL, Redis | School office trial |
| Daily school use | Dedicated mini-server, PostgreSQL, Redis, private tunnel/VPN | Recommended |
| Public internet access | Reverse proxy, HTTPS, strict env vars, backups, monitoring | Only after acceptance |

Do not expose the database directly to the network. Staff should access only the web app.

## Architecture

```text
Staff browsers
  -> http://school-server:8000 or https://ias.school-domain.example
  -> LCCCA-IAS FastAPI app
  -> PostgreSQL database on private localhost/LAN
  -> Redis for rate limiting
```

## Option A: Local Pilot With SQLite

Use this only for development, QA, or a short one-person pilot.

1. Create a Python environment.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

2. Create a local environment file.

```bash
cp .env.example .env
```

Edit `.env` and set at least:

```bash
LCCA_SECRET_KEY=replace-with-a-long-random-secret
LCCA_TOTP_ENCRYPTION_KEY=replace-with-a-fernet-key
DATABASE_URL=sqlite:///./lcca.db
```

3. Start the app.

```bash
source .venv/bin/activate
set -a
source .env
set +a
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

4. Open:

```text
http://127.0.0.1:8000
```

Default seeded login:

```text
Username: admin
Password: admin123
```

Change the admin password before any real use.

## Option B: Dedicated Local School Server

This is the recommended school setup.

Hardware:

- Mini PC, office desktop, or small server
- 8 GB RAM minimum, 16 GB preferred
- SSD storage
- UPS battery backup
- Wired Ethernet preferred

Software:

- macOS, Linux, or Windows with WSL2
- PostgreSQL
- Redis
- Python 3.11 or 3.12
- Optional: Caddy or Nginx for HTTPS/reverse proxy

Example app command:

```bash
source .venv/bin/activate
set -a
source .env
set +a
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Staff can then open:

```text
http://SERVER-IP:8000
```

or, with local DNS:

```text
http://lcca-ias.local:8000
```

## Option C: Docker Compose

Use Docker when you want a repeatable service setup.

1. Create `.env`.

```bash
cp .env.example .env
```

2. Edit `.env`.

Set:

```bash
LCCA_SECRET_KEY=...
LCCA_TOTP_ENCRYPTION_KEY=...
POSTGRES_PASSWORD=...
DATABASE_URL=postgresql://lcca:${POSTGRES_PASSWORD}@postgres:5432/lcca_ias
LCCA_RATE_LIMIT_BACKEND=redis
LCCA_RATE_LIMIT_REDIS_URL=redis://redis:6379/0
```

3. Start services.

```bash
docker compose --profile postgres up -d
```

Current note: if the compose file does not yet include Redis, add Redis before using this as the final production compose stack.

## Network Access Recommendations

Best choices:

1. Tailscale
   - Best for staff-only access.
   - Simple, private, and safer than public exposure.

2. Cloudflare Tunnel
   - Good if the school wants a public URL with access policies.
   - Keep authentication and HTTPS enabled.

3. Local LAN only
   - Good if all users are in the office.
   - Requires stable server IP or local DNS.

Avoid:

- Opening PostgreSQL to the internet.
- Running the production system from a normal daily-use laptop.
- Public HTTP without HTTPS.
- Ngrok for permanent school production use.

## Environment Variables

See [.env.example](../.env.example).

Minimum production variables:

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

## First-Day Checklist

- Install dependencies.
- Configure `.env`.
- Start PostgreSQL.
- Start Redis.
- Start LCCCA-IAS.
- Open `/health`.
- Log in as admin.
- Change admin password.
- Configure SMTP or test mail sink.
- Enable TOTP for admin.
- Run a test parent invoice.
- Confirm backups are running.

