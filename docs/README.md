# LCCCA-IAS Documentation

This folder contains the operational documentation for the LCCCA-IAS school invoice automation system.

Recommended reading order:

1. [Setup Guide](SETUP_GUIDE.md)
2. [Deployment Guide](DEPLOYMENT.md)
3. [Database Guide](DATABASE.md)
4. [Backup and Restore Guide](BACKUP_AND_RESTORE.md)
5. [Security Guide](SECURITY.md)
6. [Operations Guide](OPERATIONS.md)
7. [QA Acceptance Summary](QA_ACCEPTANCE_SUMMARY.md)
8. [Final Stabilization Report](FINAL_STABILIZATION_REPORT.md)

## Recommended Setup

For a real school office, run LCCCA-IAS on a dedicated local computer or mini-server, with staff accessing the web app through the local network or a secure private tunnel.

Recommended production-style stack:

- Application: FastAPI/Uvicorn
- Database: PostgreSQL
- Rate limiting: Redis
- Web access: Caddy, Nginx, Tailscale, VPN, or Cloudflare Tunnel
- Backups: daily PostgreSQL dump plus offsite copy
- Power: UPS for the server and network equipment

SQLite is acceptable for a short local pilot, but PostgreSQL is recommended before multiple staff members use the system daily.
