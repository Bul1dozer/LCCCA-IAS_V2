"""
Authentication for LCCA-IAS.

A single Administrator account is supported for this MVP. Sessions are
managed via signed cookies (Starlette SessionMiddleware), so no token
handling is required on the frontend -- the browser cookie does the work.

Password hashing uses PBKDF2-HMAC-SHA256 with a per-password salt
(standard library only -- no extra native dependencies required).
For a production deployment, consider migrating to bcrypt/argon2.
"""

import hashlib
import hmac
import os
import secrets

from fastapi import Request, HTTPException, status, Depends
from sqlalchemy.orm import Session

from . import models
from .database import get_db

PBKDF2_ITERATIONS = 260_000


def hash_password(password: str, salt: str | None = None) -> str:
    """Return 'salt$hash' for storage."""
    if salt is None:
        salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), PBKDF2_ITERATIONS
    )
    return f"{salt}${digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        salt, _ = stored_hash.split("$")
    except ValueError:
        return False
    candidate = hash_password(password, salt)
    return hmac.compare_digest(candidate, stored_hash)


def get_current_user(request: Request, db: Session) -> models.User:
    """Raise 401 if not authenticated, otherwise return the User row."""
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return user


SECRET_KEY = os.environ.get("LCCA_SECRET_KEY", secrets.token_hex(32))

if "LCCA_SECRET_KEY" not in os.environ:
    import warnings
    warnings.warn(
        "LCCA_SECRET_KEY is not set — a random session secret was generated "
        "for this process. ALL active sessions will be invalidated on the "
        "next restart. Set LCCA_SECRET_KEY in your environment/.env for "
        "production deployments to avoid forcing every user to re-login "
        "after every deploy or restart.",
        RuntimeWarning,
    )


def require_admin(request: Request, db: Session = Depends(get_db)) -> models.User:
    """FastAPI dependency: ensures a logged-in Administrator session exists."""
    return get_current_user(request, db)


# ---------------------------------------------------------------------------
# Role-based access control (RBAC) — foundation for future granular roles.
#
# The current deployment seeds and operates a single Administrator account
# (see seed_data.py), so every existing router intentionally continues to
# use require_admin, which only checks "is this person logged in" rather
# than checking a specific role. The Role / UserRole tables already exist
# in the schema (models.py) for when the school introduces Finance Officer,
# Clerk, and Viewer accounts with differentiated permissions.
#
# require_role() below is ready to use as soon as multiple roles are
# actually provisioned: swap a router's `Depends(auth.require_admin)` for
# `Depends(auth.require_role("Finance Officer", "Administrator"))` to
# restrict that router to specific roles, without needing further changes
# to this module.
# ---------------------------------------------------------------------------
def require_role(*allowed_roles: str):
    """
    Returns a FastAPI dependency that ensures the current user is
    authenticated AND holds one of the given roles (case-insensitive).
    Administrator is always implicitly allowed, as the superuser role.

    Usage:
        dependencies=[Depends(auth.require_role("Finance Officer"))]
    """
    allowed_lower = {r.lower() for r in allowed_roles} | {"administrator"}

    def _dependency(request: Request, db: Session = Depends(get_db)) -> models.User:
        user = get_current_user(request, db)
        if user.role.lower() not in allowed_lower:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"This action requires one of the following roles: {', '.join(sorted(allowed_roles))}",
            )
        return user

    return _dependency
