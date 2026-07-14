"""Authentication helpers for LCCA-IAS."""
import hashlib, hmac, os, secrets, warnings
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session
from .database import get_db
from . import models

SECRET_KEY = os.environ.get("LCCA_SECRET_KEY", secrets.token_hex(32))
if "LCCA_SECRET_KEY" not in os.environ:
    warnings.warn(
        "LCCA_SECRET_KEY not set — random secret generated. All sessions invalidated on restart. "
        "Set LCCA_SECRET_KEY in .env for production.",
        RuntimeWarning,
    )

def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 260000)
    return f"pbkdf2:sha256:260000:{salt}:{dk.hex()}"

def verify_password(password: str, stored: str) -> bool:
    try:
        _, algo, iters, salt, stored_hash = stored.split(":")
        dk = hashlib.pbkdf2_hmac(algo, password.encode(), salt.encode(), int(iters))
        return hmac.compare_digest(dk.hex(), stored_hash)
    except Exception:
        return False

def get_current_user(request: Request, db: Session) -> models.User:
    uid = request.session.get("user_id")
    if not uid:
        raise HTTPException(status_code=401, detail="Not authenticated")
    user = db.query(models.User).filter(models.User.id == uid, models.User.is_active == True).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found or inactive")
    return user

def require_admin(request: Request, db: Session = Depends(get_db)) -> models.User:
    return get_current_user(request, db)

def require_role(*allowed_roles: str):
    allowed_lower = {r.lower() for r in allowed_roles} | {"administrator"}
    def _dep(request: Request, db: Session = Depends(get_db)) -> models.User:
        user = get_current_user(request, db)
        if user.role.lower() not in allowed_lower:
            raise HTTPException(status_code=403,
                detail=f"Requires one of: {', '.join(sorted(allowed_roles))}")
        return user
    return _dep
