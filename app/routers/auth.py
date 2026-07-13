import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.orm import Session
from .. import models, schemas, auth
from ..database import get_db
from ..audit import audit

router = APIRouter(prefix="/api/auth", tags=["Authentication"])


@router.post("/login")
def login(payload: schemas.LoginRequest, request: Request, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(
        models.User.username == payload.username,
        models.User.is_active == True
    ).first()
    if not user or not auth.verify_password(payload.password, user.password_hash):
        audit(db, "LOGIN", "User", None, f"Failed login attempt for '{payload.username}'",
              request=request, username=payload.username)
        db.commit()
        raise HTTPException(status_code=401, detail="Invalid username or password")

    from datetime import datetime
    user.last_login = datetime.utcnow()
    request.session["user_id"] = user.id
    request.session["username"] = user.username
    request.session["role"] = user.role

    audit(db, "LOGIN", "User", user.id, f"Successful login: {user.username}",
          request=request, user_id=user.id, username=user.username)
    db.commit()
    return {"message": "Login successful", "user": {
        "id": user.id, "username": user.username,
        "full_name": user.full_name, "role": user.role,
        "totp_enabled": user.totp_enabled,
    }}


@router.post("/password-change")
def change_password(payload: schemas.PasswordChangeRequest, request: Request, current_user: models.User = Depends(auth.require_admin), db: Session = Depends(get_db)):
    if not auth.verify_password(payload.current_password, current_user.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    current_user.password_hash = auth.hash_password(payload.new_password)
    current_user.password_reset_token = None
    current_user.password_reset_expires_at = None
    audit(db, "CONFIG", "User", current_user.id, f"Password changed for {current_user.username}", request=request, user_id=current_user.id, username=current_user.username)
    db.commit()
    return {"message": "Password changed successfully"}


@router.post("/password-reset")
def request_password_reset(payload: schemas.PasswordResetRequest, request: Request, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.username == payload.username, models.User.is_active == True).first()
    if user:
        user.password_reset_token = secrets.token_urlsafe(24)
        user.password_reset_expires_at = datetime.utcnow() + timedelta(hours=1)
        db.commit()
    return {
        "message": "If the account exists, a recovery token has been issued.",
        "token": user.password_reset_token if user else None,
    }


@router.post("/password-reset/confirm")
def confirm_password_reset(payload: schemas.PasswordResetConfirmRequest, request: Request, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.password_reset_token == payload.token).first()
    if not user or not user.password_reset_expires_at or user.password_reset_expires_at < datetime.utcnow():
        raise HTTPException(status_code=400, detail="Invalid or expired password reset token")
    user.password_hash = auth.hash_password(payload.new_password)
    user.password_reset_token = None
    user.password_reset_expires_at = None
    audit(db, "CONFIG", "User", user.id, f"Password reset completed for {user.username}", request=request, user_id=user.id, username=user.username)
    db.commit()
    return {"message": "Password reset successful"}


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


@router.get("/me", response_model=schemas.CurrentUser)
def me(request: Request, db: Session = Depends(get_db)):
    user = auth.get_current_user(request, db)
    return schemas.CurrentUser(
        id=user.id, username=user.username,
        full_name=user.full_name, role=user.role
    )
