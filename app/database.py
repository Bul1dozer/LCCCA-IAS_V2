"""
Database connection setup for LCCA-IAS v3.

SQLite by default (zero-config for demos), PostgreSQL-ready via DATABASE_URL.

SQLite concurrency — important production note:
  SQLAlchemy's StaticPool shares exactly ONE underlying DBAPI connection
  across every thread in the process. FastAPI/Starlette dispatches each
  synchronous request handler to a worker thread pool, so under genuine
  concurrent requests, multiple threads can end up issuing statements on
  that single shared connection/cursor simultaneously. This corrupts
  in-flight result-set iteration (observed as spurious IndexError /
  'cannot commit transaction - SQL statements in progress' errors) even
  though WAL mode and busy_timeout are configured correctly — those
  settings govern *separate connections* contending for the database
  file lock, not concurrent use of a *single shared* connection object.

  The robust fix for SQLite is to serialize ALL database session usage
  for the lifetime of each request behind a process-wide lock. This is
  exactly what a single SQLite writer can sustain correctly, and it is
  what get_db() below does whenever SQLite is the configured backend.

  On PostgreSQL (DATABASE_URL set to a postgres:// URL), each request
  gets its own real connection from the pool, true concurrent writes are
  supported via the database server's own row-level locking, and this
  lock becomes a complete no-op passthrough — the architecture scales
  naturally to a real multi-connection backend without any code changes
  in the application layer.
"""

import os
import threading
from contextlib import contextmanager
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker, declarative_base

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./lcca.db")
IS_SQLITE = DATABASE_URL.startswith("sqlite")

# Process-wide write/session serialization lock — SQLite only.
#
# IMPORTANT: this uses threading.Semaphore(1), NOT threading.RLock().
# FastAPI's generator-based dependencies (like get_db below) are entered
# and exited via anyio.to_thread.run_sync(), which is free to run the
# generator's setup (before yield) and teardown (after yield) on
# *different* worker threads from the pool. threading.Lock/RLock track
# the OS thread that acquired them and raise "cannot release un-acquired
# lock" if release() is called from a different thread — exactly the
# failure this caused in production testing. Semaphore has no owner-
# thread affinity: any thread may call release(), making it the correct
# primitive for a lock whose acquire/release straddle a generator yield
# that crosses thread-pool boundaries.
_sqlite_session_semaphore = threading.Semaphore(1)


@contextmanager
def write_lock():
    """
    Serializes a critical section against the shared SQLite connection.
    No-op on PostgreSQL. Used internally by ledger.next_invoice_number()
    for an extra-tight critical section, in addition to the full-request
    serialization performed by get_db() below.
    """
    if IS_SQLITE:
        _sqlite_session_semaphore.acquire()
        try:
            yield
        finally:
            _sqlite_session_semaphore.release()
    else:
        yield


if DATABASE_URL.startswith("sqlite"):
    from sqlalchemy.pool import StaticPool
    engine = create_engine(
        DATABASE_URL,
        connect_args={"check_same_thread": False, "timeout": 30},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_conn, _rec):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA busy_timeout=30000")
        cur.execute("PRAGMA cache_size=-32000")
        cur.close()
else:
    engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_size=10, max_overflow=20)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def ensure_schema():
    """Apply lightweight column migrations for existing SQLite databases."""
    if not IS_SQLITE:
        return

    with engine.begin() as conn:
        def table_columns(table_name: str) -> set[str]:
            rows = conn.exec_driver_sql(f'PRAGMA table_info("{table_name}")').fetchall()
            return {row[1] for row in rows}

        def add_column(table_name: str, column_def: str) -> None:
            column_name = column_def.split()[0]
            if column_name in table_columns(table_name):
                return
            conn.execute(text(f'ALTER TABLE "{table_name}" ADD COLUMN {column_def}'))

        add_column("users", "totp_enabled BOOLEAN NOT NULL DEFAULT 0")
        add_column("users", "last_login DATETIME")
        add_column("users", "password_reset_token VARCHAR(255)")
        add_column("users", "password_reset_expires_at DATETIME")
        add_column("learners", "learner_id VARCHAR(10)")
        add_column("learners", "physical_address TEXT")
        add_column("parents", "employer_name VARCHAR(150)")
        add_column("parents", "employer_address TEXT")
        add_column("parents", "employer_phone VARCHAR(30)")
        add_column("parents", "position VARCHAR(100)")
        add_column("invoices", "parent_id INTEGER")


def get_db():
    """
    FastAPI dependency yielding a database session.

    On SQLite, the entire request's session lifetime is serialized behind
    a process-wide semaphore. This is the only fully correct way to use a
    single shared StaticPool connection under FastAPI's threaded request
    dispatch — see the module docstring above for the full rationale.
    On PostgreSQL this is a no-op and each request gets independent true
    concurrency via the connection pool.
    """
    if IS_SQLITE:
        _sqlite_session_semaphore.acquire()
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()
            _sqlite_session_semaphore.release()
    else:
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()


@contextmanager
def get_session():
    """
    Context-manager equivalent of get_db() for use OUTSIDE FastAPI's
    dependency-injection system — e.g. the month-end scheduler's
    background tasks, which open their own SessionLocal() instances
    directly. Applies the same SQLite serialization guarantee.

    Usage:
        with get_session() as db:
            ...
    """
    if IS_SQLITE:
        _sqlite_session_semaphore.acquire()
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()
            _sqlite_session_semaphore.release()
    else:
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()
