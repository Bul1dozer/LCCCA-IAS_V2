# Database Guide

LCCCA-IAS supports SQLite for local testing and PostgreSQL for real multi-user use.

## Recommendation

Use PostgreSQL for the school office.

SQLite is acceptable for:

- Developer testing
- QA diagnostics
- One-user demo
- Short pilot with no concurrent staff

PostgreSQL is recommended for:

- Multiple staff users
- Month-end processing
- Long-term financial records
- Reliable backup and restore
- Better concurrency and locking behavior

## SQLite Setup

Example:

```bash
DATABASE_URL=sqlite:///./lcca.db
```

Important SQLite rules:

- Keep one app process/worker.
- Do not place the SQLite DB on a network share.
- Back up the `.db`, `-wal`, and `-shm` files together if WAL mode is active.
- Prefer PostgreSQL before daily school use.

## PostgreSQL Setup

1. Create database and user.

```bash
createdb lcca_ias
createuser lcca
psql -d lcca_ias -c "alter user lcca with encrypted password 'CHANGE_ME';"
psql -d lcca_ias -c "grant all privileges on database lcca_ias to lcca;"
```

2. Set the app database URL.

```bash
DATABASE_URL=postgresql://lcca:CHANGE_ME@localhost:5432/lcca_ias
```

3. Start the app.

On startup, SQLAlchemy creates missing tables from the ORM model metadata.

## Migration Notes

Before schema migrations:

1. Stop the app.
2. Back up the database.
3. Run migration or startup on a copy first.
4. Verify invoice totals, ledger totals, and login.
5. Start production only after verification.

The current app uses `Base.metadata.create_all`. For long-term production, introduce Alembic migrations before making repeated schema changes.

## Data Safety Rules

- Do not test with production records.
- Do not copy production database files into development folders.
- Do not commit `.db`, `.sqlite`, dumps, or backup files.
- Keep database credentials in `.env`, never in code.

## Useful Checks

PostgreSQL connection:

```bash
psql "$DATABASE_URL" -c "select now();"
```

App health:

```bash
curl http://127.0.0.1:8000/health
```

Invoice/ledger reconciliation should be checked after invoice and payment changes.

