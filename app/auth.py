"""Authentication helpers for LCCA-IAS."""
import base64, hashlib, hmac, logging, os, secrets, warnings
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session
from .database import get_db
from . import models
from .rate_limit import build_rate_limiter

logger = logging.getLogger(__name__)

APP_ENV = os.environ.get("LCCA_ENV", os.environ.get("APP_ENV", "development")).strip().lower()
IS_PRODUCTION = APP_ENV in {"prod", "production"}

SECRET_KEY = os.environ.get("LCCA_SECRET_KEY")
if not SECRET_KEY and IS_PRODUCTION:
    raise RuntimeError("LCCA_SECRET_KEY must be set when LCCA_ENV=production.")
if not SECRET_KEY:
    SECRET_KEY = secrets.token_hex(32)
    warnings.warn(
        "LCCA_SECRET_KEY not set — random secret generated. All sessions invalidated on restart. "
        "Set LCCA_SECRET_KEY in .env for production.",
        RuntimeWarning,
    )

def env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}

def env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("Invalid integer for %s=%r; using %s", name, raw, default)
        return default

RATE_LIMITER = build_rate_limiter(IS_PRODUCTION)

CSRF_COOKIE_NAME = "lcca_csrf"
CSRF_HEADER_NAME = "x-csrf-token"

TOTP_ENCRYPTION_KEY = os.environ.get("LCCA_TOTP_ENCRYPTION_KEY")
if IS_PRODUCTION and not TOTP_ENCRYPTION_KEY:
    raise RuntimeError("LCCA_TOTP_ENCRYPTION_KEY must be set when LCCA_ENV=production.")


def _dev_totp_key() -> bytes:
    digest = hashlib.sha256(f"totp:{SECRET_KEY}".encode()).digest()
    return base64.urlsafe_b64encode(digest)


def _totp_fernet():
    from cryptography.fernet import Fernet

    key = TOTP_ENCRYPTION_KEY.encode() if TOTP_ENCRYPTION_KEY else _dev_totp_key()
    return Fernet(key)

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

def validate_password_policy(password: str) -> None:
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters.")

def reset_token_digest(token: str) -> str:
    return hmac.new(SECRET_KEY.encode(), token.encode(), hashlib.sha256).hexdigest()

def encrypt_totp_secret(secret: str) -> str:
    return _totp_fernet().encrypt(secret.encode()).decode()

def decrypt_totp_secret(stored: str) -> str:
    from cryptography.fernet import InvalidToken

    try:
        return _totp_fernet().decrypt(stored.encode()).decode()
    except (InvalidToken, ValueError):
        # Migration path: existing databases may contain plaintext base32 secrets.
        if stored and all(ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567=" for ch in stored.upper()):
            return stored
        raise

def is_encrypted_totp_secret(stored: str) -> bool:
    try:
        _totp_fernet().decrypt(stored.encode())
        return True
    except Exception:
        return False

def ensure_csrf_token(request: Request) -> str:
    token = request.cookies.get(CSRF_COOKIE_NAME)
    if not token:
        token = secrets.token_urlsafe(32)
    request.state.csrf_token = token
    return token

def validate_csrf_request(request: Request) -> None:
    cookie_token = request.cookies.get(CSRF_COOKIE_NAME)
    header_token = request.headers.get(CSRF_HEADER_NAME)
    if not cookie_token or not header_token or not hmac.compare_digest(cookie_token, header_token):
        raise HTTPException(status_code=403, detail="Invalid CSRF token.")

def get_current_user(request: Request, db: Session) -> models.User:
    uid = request.session.get("user_id")
    if not uid:
        logger.warning("Authorization rejected: missing authenticated session")
        raise HTTPException(status_code=401, detail="Not authenticated")
    user = db.query(models.User).filter(models.User.id == uid, models.User.is_active == True).first()
    if not user:
        logger.warning("Authorization rejected: inactive or missing user_id=%s", uid)
        raise HTTPException(status_code=401, detail="User not found or inactive")
    return user

def _normalized_role_variants(role: str) -> set[str]:
    raw = (role or "").strip().lower()
    variants = {raw}
    if raw == "opereations manager":
        variants.add("operations manager")
    return variants


def _role_grants_admin(user: models.User, db: Session) -> bool:
    role_name = (user.role or "").strip().lower()
    if role_name in {"administrator", "admin"}:
        return True
    if _normalized_role_variants(user.role) & {"principal", "operations manager"}:
        return True
    role = db.query(models.Role).filter(models.Role.name == user.role).first()
    if not role or not role.permissions:
        return False
    permissions = {p.strip().lower() for p in role.permissions.replace(";", ",").split(",")}
    return bool({"admin", "administrator", "*"} & permissions)

def require_admin(request: Request, db: Session = Depends(get_db)) -> models.User:
    user = get_current_user(request, db)
    if not _role_grants_admin(user, db):
        logger.warning(
            "Authorization rejected: non-admin user_id=%s role=%s",
            user.id,
            user.role,
        )
        raise HTTPException(status_code=403, detail="Administrator privileges required")
    return user

def require_role(*allowed_roles: str):
    allowed_lower = {r.lower() for r in allowed_roles} | {"administrator"}
    def _dep(request: Request, db: Session = Depends(get_db)) -> models.User:
        user = get_current_user(request, db)
        user_roles = _normalized_role_variants(user.role)
        if not (user_roles & allowed_lower):
            raise HTTPException(status_code=403,
                detail=f"Requires one of: {', '.join(sorted(allowed_roles))}")
        return user
    return _dep
