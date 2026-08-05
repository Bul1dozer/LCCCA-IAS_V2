"""
Pytest fixtures for LCCA-IAS v3 test suite.
Uses an isolated SQLite database per test session.
"""
import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _resolve_test_db_path() -> str:
    env_path = os.environ.get("LCCA_TEST_DB_PATH")
    if env_path:
        return env_path

    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    fallback_dir = os.path.join(project_root, ".pytest_tmp")
    os.makedirs(fallback_dir, exist_ok=True)
    return os.path.join(fallback_dir, "lcca_test.db")


# Force a clean, isolated test database before any app modules import the engine
TEST_DB_PATH = _resolve_test_db_path()
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB_PATH}"

for candidate in (TEST_DB_PATH, TEST_DB_PATH + "-wal", TEST_DB_PATH + "-shm"):
    try:
        if os.path.exists(candidate):
            os.remove(candidate)
    except PermissionError:
        # Some environments block temp-file removal; ignore and let SQLite recreate.
        pass


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
