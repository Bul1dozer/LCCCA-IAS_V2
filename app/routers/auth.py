from datetime import datetime, timedelta
import secrets as sec
import time
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from .. import models, schemas, auth
from ..database import get_db
from ..audit import audit
from ..password_reset_email import send_password_reset
from ..rate_limit import RateLimitExceeded

router = APIRouter(prefix="/api/auth", tags=["Authentication"])

LOGIN_WINDOW_SECONDS = auth.env_int("LCCA_LOGIN_WINDOW_SECONDS", 300)
LOGIN_MAX_ATTEMPTS = auth.env_int("LCCA_LOGIN_MAX_ATTEMPTS", 5)
RESET_WINDOW_SECONDS = auth.env_int("LCCA_RESET_WINDOW_SECONDS", 900)
RESET_MAX_ATTEMPTS = auth.env_int("LCCA_RESET_MAX_ATTEMPTS", 5)
DEV_RESET_TOKEN_ENABLED = auth.env_bool("LCCA_ALLOW_DEV_RESET_TOKEN", False) and not auth.IS_PRODUCTION

def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _rate_key(request: Request, username: str) -> str:
    return f"{_client_ip(request)}:{username.strip().lower()}"


def _rate_limit_hit(key: str, limit: int, window_seconds: int) -> None:
    try:
        auth.RATE_LIMITER.hit(key, limit, window_seconds)
    except RateLimitExceeded:
        raise HTTPException(status_code=429, detail="Too many attempts. Try again later.")


def _complete_login(request: Request, user: models.User) -> None:
    request.session.clear()
    request.session["user_id"] = user.id
    request.session["username"] = user.username
    request.session["role"] = user.role


@router.post("/login")
def login(payload: schemas.LoginRequest, request: Request, db: Session = Depends(get_db)):
    key = _rate_key(request, payload.username)
    user = db.query(models.User).filter(
        models.User.username == payload.username, models.User.is_active == True
    ).first()
    if not user or not auth.verify_password(payload.password, user.password_hash):
        _rate_limit_hit(key, LOGIN_MAX_ATTEMPTS, LOGIN_WINDOW_SECONDS)
        audit(db, "LOGIN", "User", None, f"Failed login: '{payload.username}'",
              request=request, username=payload.username)
        db.commit()
        raise HTTPException(401, "Invalid username or password")

    auth.RATE_LIMITER.clear(key)
    if user.totp_enabled:
        if not user.totp_secret:
            audit(db, "LOGIN", "User", user.id, f"2FA login blocked: missing secret for {user.username}",
                  request=request, user_id=user.id, username=user.username)
            db.commit()
            raise HTTPException(403, "Two-factor authentication is not configured correctly.")
        request.session.clear()
        request.session["pending_2fa_user_id"] = user.id
        request.session["pending_2fa_username"] = user.username
        request.session["pending_2fa_attempts"] = 0
        request.session["pending_2fa_started_at"] = int(time.time())
        audit(db, "LOGIN", "User", user.id, f"2FA challenge issued: {user.username}",
              request=request, user_id=user.id, username=user.username)
        db.commit()
        return {"message": "Two-factor authentication required.", "requires_2fa": True}

    user.last_login = datetime.utcnow()
    _complete_login(request, user)
    audit(db, "LOGIN", "User", user.id, f"Login: {user.username}",
          request=request, user_id=user.id, username=user.username)
    db.commit()
    return {"message": "Login successful", "user": {
        "id": user.id, "username": user.username,
        "full_name": user.full_name, "role": user.role,
    }}


@router.post("/login/2fa")
def verify_login_2fa(
    payload: schemas.TwoFactorLoginRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    import pyotp

    uid = request.session.get("pending_2fa_user_id")
    if not uid:
        raise HTTPException(401, "No two-factor login is pending.")
    attempts = int(request.session.get("pending_2fa_attempts", 0))
    if attempts >= LOGIN_MAX_ATTEMPTS:
        request.session.clear()
        raise HTTPException(429, "Too many attempts. Try again later.")

    user = db.query(models.User).filter(
        models.User.id == uid,
        models.User.is_active == True,
    ).first()
    if not user or not user.totp_enabled or not user.totp_secret:
        request.session.clear()
        raise HTTPException(401, "No two-factor login is pending.")

    secret = auth.decrypt_totp_secret(user.totp_secret.secret)
    totp = pyotp.TOTP(secret)
    if not totp.verify(str(payload.code).strip(), valid_window=1):
        request.session["pending_2fa_attempts"] = attempts + 1
        audit(db, "LOGIN", "User", user.id, f"Failed 2FA login: {user.username}",
              request=request, user_id=user.id, username=user.username)
        db.commit()
        raise HTTPException(401, "Invalid two-factor code.")

    if not auth.is_encrypted_totp_secret(user.totp_secret.secret):
        user.totp_secret.secret = auth.encrypt_totp_secret(secret)
    user.last_login = datetime.utcnow()
    _complete_login(request, user)
    audit(db, "LOGIN", "User", user.id, f"Login with 2FA: {user.username}",
          request=request, user_id=user.id, username=user.username)
    db.commit()
    return {"message": "Login successful", "user": {
        "id": user.id, "username": user.username,
        "full_name": user.full_name, "role": user.role,
    }}


@router.post("/logout")
def logout(request: Request, db: Session = Depends(get_db)):
    uid = request.session.get("user_id")
    uname = request.session.get("username", "unknown")
    if uid:
        audit(db, "LOGOUT", "User", uid, f"Logout: {uname}",
              request=request, user_id=uid, username=uname)
        db.commit()
    request.session.clear()
    return {"message": "Logged out successfully"}


@router.get("/me")
def me(request: Request, db: Session = Depends(get_db)):
    user = auth.get_current_user(request, db)
    return {"id": user.id, "username": user.username,
            "full_name": user.full_name, "role": user.role,
            "email": user.email}


@router.post("/change-password")
def change_password(
    payload: schemas.ChangePasswordRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """Authenticated password change — available in the Settings tab."""
    user = auth.get_current_user(request, db)
    if not auth.verify_password(payload.current_password, user.password_hash):
        raise HTTPException(400, "Current password is incorrect.")
    if payload.new_password != payload.confirm_password:
        raise HTTPException(400, "New passwords do not match.")
    auth.validate_password_policy(payload.new_password)
    if auth.verify_password(payload.new_password, user.password_hash):
        raise HTTPException(400, "New password must be different from the current password.")
    user.password_hash = auth.hash_password(payload.new_password)
    audit(db, "CONFIG", "User", user.id, f"Password changed: {user.username}",
          request=request, user_id=user.id, username=user.username)
    db.commit()
    _complete_login(request, user)
    return {"message": "Password changed successfully."}


@router.post("/forgot-password")
def forgot_password(
    payload: schemas.ForgotPasswordRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Initiate password recovery.
    Always returns 200 to prevent username enumeration.
    Returns the token directly when SMTP is not configured (dev/fallback mode).
    """
    key = _rate_key(request, payload.username)
    _rate_limit_hit(f"reset:{key}", RESET_MAX_ATTEMPTS, RESET_WINDOW_SECONDS)

    user = db.query(models.User).filter(
        models.User.username == payload.username,
        models.User.is_active == True,
    ).first()

    # Silently reject wrong username or email mismatch — still return 200
    if not user or (user.email and user.email.lower() != payload.email.lower()):
        return {"message": "If those details match an account, a reset link has been sent.",
                "token": None}
    if not user.email and payload.email:
        # user has no email set — can't verify, refuse quietly
        return {"message": "If those details match an account, a reset link has been sent.",
                "token": None}

    # Expire old tokens
    db.query(models.PasswordResetToken).filter(
        models.PasswordResetToken.user_id == user.id,
        models.PasswordResetToken.used == False,
    ).update({"used": True})

    token = sec.token_hex(32)
    token_hash = auth.reset_token_digest(token)
    base_url = auth.os.environ.get("LCCA_PUBLIC_BASE_URL", str(request.base_url).rstrip("/"))
    reset_link = f"{base_url}/reset-password?token={token}"
    ip = request.client.host if request.client else None
    db.add(models.PasswordResetToken(
        user_id=user.id, token=token_hash,
        expires_at=datetime.utcnow() + timedelta(hours=1),
        requested_from_ip=ip,
    ))
    audit(db, "CONFIG", "User", user.id, f"Password reset requested: {user.username}",
          request=request, username=user.username)
    delivery = send_password_reset(db, user, reset_link)
    db.commit()

    return {
        "message": "If those details match an account, a reset link has been sent.",
        "token": token if DEV_RESET_TOKEN_ENABLED else None,
        "reset_link": reset_link if DEV_RESET_TOKEN_ENABLED else None,
        "note": "Development reset-token fallback enabled." if DEV_RESET_TOKEN_ENABLED else None,
        "delivery": delivery["status"] if DEV_RESET_TOKEN_ENABLED else None,
    }


@router.post("/reset-password")
def reset_password(
    payload: schemas.ResetPasswordRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """Complete the password reset using a token from forgot-password."""
    if payload.new_password != payload.confirm_password:
        raise HTTPException(400, "Passwords do not match.")
    auth.validate_password_policy(payload.new_password)

    token_hash = auth.reset_token_digest(payload.token)
    rec = db.query(models.PasswordResetToken).filter(
        models.PasswordResetToken.token.in_([token_hash, payload.token]),
        models.PasswordResetToken.used == False,
    ).first()
    if not rec:
        raise HTTPException(400, "Invalid or already-used reset token.")
    if rec.expires_at < datetime.utcnow():
        raise HTTPException(400, "Reset token has expired. Request a new one.")

    user = db.query(models.User).filter(
        models.User.id == rec.user_id,
        models.User.is_active == True,
    ).first()
    if not user:
        raise HTTPException(400, "User not found.")
    if auth.verify_password(payload.new_password, user.password_hash):
        raise HTTPException(400, "New password must be different from the current password.")

    user.password_hash = auth.hash_password(payload.new_password)
    rec.used = True
    audit(db, "CONFIG", "User", user.id, f"Password reset completed: {user.username}",
          request=request, username=user.username)
    db.commit()
    return {"message": "Password reset successfully. You can now log in."}
