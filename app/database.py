"""
Database setup — SQLite default, PostgreSQL-ready via DATABASE_URL.

Critical SQLite concurrency note:
  StaticPool shares ONE DBAPI connection across all threads. FastAPI's
  anyio threadpool may dispatch setup and teardown of a generator
  dependency on DIFFERENT OS threads, so threading.Lock/RLock will raise
  "cannot release un-acquired lock". threading.Semaphore(1) has no
  owner-thread restriction and is the correct primitive here.
"""
import os, threading
from contextlib import contextmanager
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, declarative_base

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./lcca.db")
IS_SQLITE = DATABASE_URL.startswith("sqlite")

_sem = threading.Semaphore(1)

@contextmanager
def write_lock():
    if IS_SQLITE:
        _sem.acquire()
        try:
            yield
        finally:
            _sem.release()
    else:
        yield

if IS_SQLITE:
    from sqlalchemy.pool import StaticPool
    engine = create_engine(
        DATABASE_URL,
        connect_args={"check_same_thread": False, "timeout": 30},
        poolclass=StaticPool,
    )
    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        for pragma in ["PRAGMA journal_mode=WAL", "PRAGMA synchronous=NORMAL",
                       "PRAGMA busy_timeout=30000", "PRAGMA cache_size=-32000"]:
            cur.execute(pragma)
        cur.close()
else:
    engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_size=10, max_overflow=20)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db():
    if IS_SQLITE:
        _sem.acquire()
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()
            _sem.release()
    else:
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

@contextmanager
def get_session():
    if IS_SQLITE:
        _sem.acquire()
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()
            _sem.release()
    else:
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()
