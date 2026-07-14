from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from .. import models, schemas, auth
from ..database import get_db
from ..audit import audit

router = APIRouter(prefix="/api/auth", tags=["Authentication"])


@router.post("/login")
def login(payload: schemas.LoginRequest, request: Request, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(
        models.User.username == payload.username, models.User.is_active == True
    ).first()
    if not user or not auth.verify_password(payload.password, user.password_hash):
        audit(db, "LOGIN", "User", None, f"Failed login: '{payload.username}'",
              request=request, username=payload.username)
        db.commit()
        raise HTTPException(401, "Invalid username or password")
    user.last_login = datetime.utcnow()
    request.session["user_id"] = user.id
    request.session["username"] = user.username
    request.session["role"] = user.role
    audit(db, "LOGIN", "User", user.id, f"Login: {user.username}",
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
    if len(payload.new_password) < 8:
        raise HTTPException(400, "New password must be at least 8 characters.")
    user.password_hash = auth.hash_password(payload.new_password)
    audit(db, "CONFIG", "User", user.id, f"Password changed: {user.username}",
          request=request, user_id=user.id, username=user.username)
    db.commit()
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
    import secrets as sec
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
    ip = request.client.host if request.client else None
    db.add(models.PasswordResetToken(
        user_id=user.id, token=token,
        expires_at=datetime.utcnow() + timedelta(hours=1),
        requested_from_ip=ip,
    ))
    audit(db, "CONFIG", "User", user.id, f"Password reset requested: {user.username}",
          request=request, username=user.username)
    db.commit()

    smtp_sent = False
    reset_link = f"/reset-password?token={token}"

    return {
        "message": "If those details match an account, a reset link has been sent.",
        "token": token if not smtp_sent else None,
        "reset_link": reset_link if not smtp_sent else None,
        "note": "SMTP not configured — use this token directly to reset your password." if not smtp_sent else "Email sent.",
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
    if len(payload.new_password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters.")

    rec = db.query(models.PasswordResetToken).filter(
        models.PasswordResetToken.token == payload.token,
        models.PasswordResetToken.used == False,
    ).first()
    if not rec:
        raise HTTPException(400, "Invalid or already-used reset token.")
    if rec.expires_at < datetime.utcnow():
        raise HTTPException(400, "Reset token has expired. Request a new one.")

    user = db.query(models.User).filter(models.User.id == rec.user_id).first()
    if not user:
        raise HTTPException(400, "User not found.")

    user.password_hash = auth.hash_password(payload.new_password)
    rec.used = True
    audit(db, "CONFIG", "User", user.id, f"Password reset completed: {user.username}",
          request=request, username=user.username)
    db.commit()
    return {"message": "Password reset successfully. You can now log in."}
