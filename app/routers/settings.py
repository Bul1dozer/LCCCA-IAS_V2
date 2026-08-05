"""
System settings router: SMTP config, SMS config, email templates, TOTP 2FA.
"""
import base64, io
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from .. import models, auth
from ..database import get_db
from ..audit import audit_from_request

router = APIRouter(prefix="/api/settings", tags=["Settings"], dependencies=[Depends(auth.require_admin)])


# ---- SMTP ----

@router.get("/smtp")
def get_smtp(db: Session = Depends(get_db)):
    cfg = db.query(models.SmtpConfig).filter(models.SmtpConfig.is_active == True).first()
    if not cfg:
        return {"configured": False}
    return {
        "configured": True, "id": cfg.id, "name": cfg.name, "host": cfg.host,
        "port": cfg.port, "username": cfg.username, "use_tls": cfg.use_tls,
        "from_address": cfg.from_address, "from_name": cfg.from_name,
    }


@router.post("/smtp")
def save_smtp(payload: dict, request: Request, db: Session = Depends(get_db)):
    cfg = db.query(models.SmtpConfig).first()
    if cfg:
        cfg.name = payload.get("name", "Default SMTP")
        cfg.host = payload["host"]
        cfg.port = int(payload.get("port", 587))
        cfg.username = payload.get("username")
        cfg.use_tls = bool(payload.get("use_tls", True))
        cfg.from_address = payload.get("from_address")
        cfg.from_name = payload.get("from_name", "LCCA Accounts")
        cfg.is_active = True
        if payload.get("password"):
            cfg.password_encrypted = payload["password"]
    else:
        cfg = models.SmtpConfig(
            name=payload.get("name", "Default SMTP"),
            host=payload["host"], port=int(payload.get("port", 587)),
            username=payload.get("username"),
            password_encrypted=payload.get("password"),
            use_tls=bool(payload.get("use_tls", True)),
            from_address=payload.get("from_address"),
            from_name=payload.get("from_name", "LCCA Accounts"),
            is_active=True,
        )
        db.add(cfg)
    audit_from_request(request, db, "CONFIG", "SmtpConfig", None, "SMTP configuration updated")
    db.commit()
    return {"message": "SMTP configuration saved."}


@router.post("/smtp/test")
async def test_smtp(request: Request, db: Session = Depends(get_db)):
    from ..email_engine import _get_active_smtp, _send_real
    cfg = _get_active_smtp(db)
    if not cfg:
        raise HTTPException(400, "No SMTP configuration found. Save settings first.")
    uid = request.session.get("user_id")
    user = db.query(models.User).filter(models.User.id == uid).first()
    to_addr = (user.email if user and user.email else None) or cfg.from_address
    if not to_addr:
        raise HTTPException(400, "No recipient address available for test email.")
    try:
        msg_id = await _send_real(cfg, to_addr, "LCCA-IAS SMTP Test",
                                  "<p>SMTP is configured correctly for LCCA-IAS.</p>",
                                  "SMTP is configured correctly for LCCA-IAS.")
        return {"success": True, "message_id": msg_id, "sent_to": to_addr}
    except Exception as e:
        raise HTTPException(500, f"SMTP test failed: {e}")


# ---- SMS ----

@router.get("/sms")
def get_sms(db: Session = Depends(get_db)):
    cfg = db.query(models.SmsConfig).filter(models.SmsConfig.is_active == True).first()
    if not cfg:
        return {"configured": False}
    return {"configured": True, "id": cfg.id, "provider": cfg.provider,
            "sender_id": cfg.sender_id}


@router.post("/sms")
def save_sms(payload: dict, request: Request, db: Session = Depends(get_db)):
    cfg = db.query(models.SmsConfig).first()
    if cfg:
        cfg.provider = payload["provider"]
        cfg.sender_id = payload.get("sender_id")
        cfg.is_active = True
        if payload.get("api_key"):
            cfg.api_key_encrypted = payload["api_key"]
        if payload.get("api_secret"):
            cfg.api_secret_encrypted = payload["api_secret"]
    else:
        cfg = models.SmsConfig(
            provider=payload["provider"], sender_id=payload.get("sender_id"),
            api_key_encrypted=payload.get("api_key"),
            api_secret_encrypted=payload.get("api_secret"), is_active=True,
        )
        db.add(cfg)
    audit_from_request(request, db, "CONFIG", "SmsConfig", None, "SMS configuration updated")
    db.commit()
    return {"message": "SMS configuration saved."}


# ---- 2FA / TOTP ----

@router.get("/2fa/status")
def status_2fa(request: Request, db: Session = Depends(get_db)):
    uid = request.session.get("user_id")
    user = db.query(models.User).filter(models.User.id == uid).first()
    if not user:
        raise HTTPException(401)
    return {"enabled": bool(user.totp_enabled)}


@router.post("/2fa/setup")
def setup_2fa(request: Request, db: Session = Depends(get_db)):
    import pyotp, qrcode
    uid = request.session.get("user_id")
    user = db.query(models.User).filter(models.User.id == uid).first()
    if not user:
        raise HTTPException(401)
    if user.totp_enabled:
        raise HTTPException(400, "2FA is already enabled.")

    secret = pyotp.random_base32()
    encrypted_secret = auth.encrypt_totp_secret(secret)
    totp_rec = db.query(models.TotpSecret).filter(models.TotpSecret.user_id == uid).first()
    if totp_rec:
        totp_rec.secret = encrypted_secret
    else:
        db.add(models.TotpSecret(user_id=uid, secret=encrypted_secret))
    db.commit()

    otp_uri = pyotp.totp.TOTP(secret).provisioning_uri(
        name=user.username, issuer_name="LCCA-IAS"
    )
    img = qrcode.make(otp_uri)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    qr_b64 = base64.b64encode(buf.getvalue()).decode()

    return {"secret": secret, "qr_code": f"data:image/png;base64,{qr_b64}", "otp_uri": otp_uri}


@router.post("/2fa/verify")
def verify_2fa(payload: dict, request: Request, db: Session = Depends(get_db)):
    import pyotp
    uid = request.session.get("user_id")
    totp_rec = db.query(models.TotpSecret).filter(models.TotpSecret.user_id == uid).first()
    if not totp_rec:
        raise HTTPException(400, "2FA not set up. Run /setup first.")
    secret = auth.decrypt_totp_secret(totp_rec.secret)
    totp = pyotp.TOTP(secret)
    if not totp.verify(str(payload.get("code", ""))):
        raise HTTPException(400, "Invalid code.")
    user = db.query(models.User).filter(models.User.id == uid).first()
    if not auth.is_encrypted_totp_secret(totp_rec.secret):
        totp_rec.secret = auth.encrypt_totp_secret(secret)
    user.totp_enabled = True
    audit_from_request(request, db, "CONFIG", "User", uid, "2FA enabled")
    db.commit()
    return {"message": "2FA enabled successfully."}


@router.post("/2fa/disable")
def disable_2fa(payload: dict, request: Request, db: Session = Depends(get_db)):
    import pyotp
    uid = request.session.get("user_id")
    user = db.query(models.User).filter(models.User.id == uid).first()
    if not user:
        raise HTTPException(401)
    if not auth.verify_password(str(payload.get("current_password", "")), user.password_hash):
        raise HTTPException(400, "Current password is incorrect.")
    totp_rec = db.query(models.TotpSecret).filter(models.TotpSecret.user_id == uid).first()
    if user.totp_enabled and totp_rec:
        secret = auth.decrypt_totp_secret(totp_rec.secret)
        totp = pyotp.TOTP(secret)
        if not totp.verify(str(payload.get("code", "")).strip(), valid_window=1):
            raise HTTPException(400, "Invalid code.")
    user.totp_enabled = False
    db.query(models.TotpSecret).filter(models.TotpSecret.user_id == uid).delete()
    audit_from_request(request, db, "CONFIG", "User", uid, "2FA disabled")
    db.commit()
    return {"message": "2FA disabled."}


# ---- Notification Logs ----

@router.get("/notification-logs")
def notification_logs(channel: str = None, status: str = None, limit: int = 100,
                       db: Session = Depends(get_db)):
    q = db.query(models.NotificationLog)
    if channel:
        q = q.filter(models.NotificationLog.channel == channel)
    if status:
        q = q.filter(models.NotificationLog.status == status)
    logs = q.order_by(models.NotificationLog.created_at.desc()).limit(limit).all()
    return [
        {"id": l.id, "channel": l.channel, "recipient": l.recipient, "subject": l.subject,
         "status": l.status, "error_message": l.error_message, "retry_count": l.retry_count,
         "sent_at": l.sent_at.isoformat() if l.sent_at else None,
         "created_at": l.created_at.isoformat() if l.created_at else None}
        for l in logs
    ]
