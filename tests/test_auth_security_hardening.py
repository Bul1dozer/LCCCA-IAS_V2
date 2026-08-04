import os
import subprocess
import sys
import uuid
from datetime import datetime

import pyotp
from fastapi.testclient import TestClient


def _unique(prefix):
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def _create_user(db_session, username=None, password="SecurePass123!", role="Administrator", active=True, email=None):
    from app import auth, models

    username = username or _unique("auth_user")
    user = models.User(
        username=username,
        password_hash=auth.hash_password(password),
        full_name=f"Test User {username}",
        email=email or f"{username}@example.test",
        role=role,
        is_active=active,
        created_at=datetime.utcnow(),
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _client():
    from app.main import app
    from app import auth

    if hasattr(auth.RATE_LIMITER, "clear_all"):
        auth.RATE_LIMITER.clear_all()
    return TestClient(app)


def _csrf(client):
    token = client.cookies.get("lcca_csrf")
    return {"X-CSRF-Token": token} if token else {}


def test_production_requires_explicit_session_secret():
    env = os.environ.copy()
    env["LCCA_ENV"] = "production"
    env.pop("LCCA_SECRET_KEY", None)
    result = subprocess.run(
        [sys.executable, "-c", "import app.auth"],
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )

    assert result.returncode != 0
    assert "LCCA_SECRET_KEY must be set" in (result.stderr + result.stdout)


def test_login_rate_limit_locks_repeated_failed_attempts(db_session):
    user = _create_user(db_session)
    client = _client()

    for _ in range(5):
        response = client.post("/api/auth/login", json={"username": user.username, "password": "wrong"})
        assert response.status_code == 401

    response = client.post("/api/auth/login", json={"username": user.username, "password": "wrong"})
    assert response.status_code == 429
    assert response.json()["detail"] == "Too many attempts. Try again later."


def test_totp_login_requires_second_factor_before_session_is_authenticated(db_session):
    from app import models

    user = _create_user(db_session)
    secret = pyotp.random_base32()
    user.totp_enabled = True
    db_session.add(models.TotpSecret(user_id=user.id, secret=secret))
    db_session.commit()

    client = _client()
    response = client.post("/api/auth/login", json={"username": user.username, "password": "SecurePass123!"})
    assert response.status_code == 200
    assert response.json()["requires_2fa"] is True

    response = client.get("/api/auth/me")
    assert response.status_code == 401

    code = pyotp.TOTP(secret).now()
    response = client.post("/api/auth/login/2fa", json={"code": code})
    assert response.status_code == 200
    assert response.json()["user"]["username"] == user.username

    response = client.get("/api/auth/me")
    assert response.status_code == 200
    assert response.json()["username"] == user.username


def test_forgot_password_hashes_token_and_hides_dev_token_by_default(db_session, monkeypatch):
    from app import models
    from app.routers import auth as auth_router

    user = _create_user(db_session, email=f"{_unique('reset')}@example.test")
    monkeypatch.setattr(auth_router, "DEV_RESET_TOKEN_ENABLED", False)

    client = _client()
    response = client.post(
        "/api/auth/forgot-password",
        json={"username": user.username, "email": user.email},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["token"] is None
    assert body["reset_link"] is None

    records = db_session.query(models.PasswordResetToken).filter_by(user_id=user.id, used=False).all()
    assert len(records) == 1
    assert len(records[0].token) == 64


def test_dev_reset_token_can_reset_password_without_storing_raw_token(db_session, monkeypatch):
    from app import auth, models
    from app.routers import auth as auth_router

    user = _create_user(db_session, email=f"{_unique('reset')}@example.test")
    monkeypatch.setattr(auth_router, "DEV_RESET_TOKEN_ENABLED", True)

    client = _client()
    response = client.post(
        "/api/auth/forgot-password",
        json={"username": user.username, "email": user.email},
    )
    assert response.status_code == 200
    token = response.json()["token"]
    assert token

    record = db_session.query(models.PasswordResetToken).filter_by(user_id=user.id, used=False).one()
    assert record.token == auth.reset_token_digest(token)
    assert record.token != token

    response = client.post(
        "/api/auth/reset-password",
        json={"token": token, "new_password": "NewSecure456!", "confirm_password": "NewSecure456!"},
        headers=_csrf(client),
    )
    assert response.status_code == 200

    response = client.post("/api/auth/login", json={"username": user.username, "password": "NewSecure456!"})
    assert response.status_code == 200


def test_change_password_rejects_reuse(db_session):
    user = _create_user(db_session)
    client = _client()
    login = client.post("/api/auth/login", json={"username": user.username, "password": "SecurePass123!"})
    assert login.status_code == 200

    response = client.post(
        "/api/auth/change-password",
        json={
            "current_password": "SecurePass123!",
            "new_password": "SecurePass123!",
            "confirm_password": "SecurePass123!",
        },
        headers=_csrf(client),
    )
    assert response.status_code == 400
    assert "different" in response.json()["detail"]


def test_disable_2fa_requires_password_and_current_code(db_session):
    from app import models

    user = _create_user(db_session)
    secret = pyotp.random_base32()
    user.totp_enabled = True
    db_session.add(models.TotpSecret(user_id=user.id, secret=secret))
    db_session.commit()

    client = _client()
    login = client.post("/api/auth/login", json={"username": user.username, "password": "SecurePass123!"})
    assert login.status_code == 200
    verify = client.post("/api/auth/login/2fa", json={"code": pyotp.TOTP(secret).now()})
    assert verify.status_code == 200

    response = client.post("/api/settings/2fa/disable", json={}, headers=_csrf(client))
    assert response.status_code == 400

    db_session.refresh(user)
    assert user.totp_enabled is True
    assert db_session.query(models.TotpSecret).filter_by(user_id=user.id).count() == 1

    response = client.post(
        "/api/settings/2fa/disable",
        json={"current_password": "SecurePass123!", "code": pyotp.TOTP(secret).now()},
        headers=_csrf(client),
    )
    assert response.status_code == 200

    db_session.refresh(user)
    assert user.totp_enabled is False
    assert db_session.query(models.TotpSecret).filter_by(user_id=user.id).count() == 0
