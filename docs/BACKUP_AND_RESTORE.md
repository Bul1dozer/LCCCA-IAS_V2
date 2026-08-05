# Backup and Restore Guide

Financial systems need boring, predictable backups. Backups are part of the system, not an optional extra.

## Backup Policy

Recommended minimum:

- Daily database backup.
- Weekly offsite copy.
- Keep at least 30 daily backups.
- Keep at least 12 monthly backups.
- Test restore at least once per term.

For an on-site school server, use:

- Local backup folder on the server.
- External USB drive or NAS.
- Offsite cloud storage controlled by the school.

## PostgreSQL Backup

Create a backup folder:

```bash
mkdir -p /var/backups/lcca-ias
```

Manual backup:

```bash
pg_dump "$DATABASE_URL" > /var/backups/lcca-ias/lcca_ias_$(date +%Y%m%d_%H%M%S).sql
```

Compressed backup:

```bash
pg_dump "$DATABASE_URL" | gzip > /var/backups/lcca-ias/lcca_ias_$(date +%Y%m%d_%H%M%S).sql.gz
```

Automated cron example:

```cron
15 22 * * * pg_dump "$DATABASE_URL" | gzip > /var/backups/lcca-ias/lcca_ias_$(date +\%Y\%m\%d_\%H\%M\%S).sql.gz
```

## SQLite Backup

Stop the app first if possible.

```bash
sqlite3 lcca.db ".backup '/var/backups/lcca-ias/lcca_ias_$(date +%Y%m%d_%H%M%S).db'"
```

If copying files directly while WAL mode is active, copy:

- `lcca.db`
- `lcca.db-wal`
- `lcca.db-shm`

Prefer the SQLite `.backup` command.

## PostgreSQL Restore

1. Stop the app.

2. Create a restore target.

```bash
createdb lcca_ias_restore
```

3. Restore.

```bash
psql "$RESTORE_DATABASE_URL" < backup.sql
```

For gzipped backups:

```bash
gunzip -c backup.sql.gz | psql "$RESTORE_DATABASE_URL"
```

4. Start the app against the restored database.

5. Verify:

- Admin login works.
- Learners load.
- Parents load.
- Invoices load.
- Ledger reconciliation is zero.
- Latest parent invoice totals match expected records.

## Restore Drill

Once per term:

1. Restore the latest backup to a test database.
2. Start the app on a test port.
3. Confirm login.
4. Open dashboard.
5. Generate a test report.
6. Do not send real email.

## Rollback Procedure

If a release causes a financial or login issue:

1. Stop the app.
2. Preserve logs.
3. Back up the current broken database state.
4. Restore the last known good database backup if needed.
5. Roll back the app code to the previous commit.
6. Start on a private test port first.
7. Verify login, invoices, payments, and reports.
8. Reopen user access only after verification.

