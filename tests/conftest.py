"""
Pytest fixtures for LCCA-IAS v3 test suite.
Uses an isolated in-memory SQLite database per test session.
"""
import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Force a clean, isolated test database before any app modules import the engine
TEST_DB_PATH = "/tmp/lcca_test.db"
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB_PATH}"

if os.path.exists(TEST_DB_PATH):
    os.remove(TEST_DB_PATH)
for ext in ("-wal", "-shm"):
    p = TEST_DB_PATH + ext
    if os.path.exists(p):
        os.remove(p)


@pytest.fixture(scope="session")
def db_engine():
    from app.database import engine, Base
    from app import models  # noqa: F401 ensure models are registered
    Base.metadata.create_all(bind=engine)
    yield engine


@pytest.fixture()
def db_session(db_engine):
    from app.database import SessionLocal
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture()
def sample_learner(db_session):
    from app import models
    from decimal import Decimal
    from datetime import date
    import uuid
    unique_code = f"TEST-{uuid.uuid4().hex[:10].upper()}"
    learner = models.Learner(
        learner_code=unique_code,
        full_name="Pytest Learner",
        grade="Grade 5",
        class_name="5A",
        date_of_admission=date(2026, 1, 1),
        status="Active",
        balance=Decimal("0.00"),
    )
    db_session.add(learner)
    db_session.commit()
    db_session.refresh(learner)
    return learner
