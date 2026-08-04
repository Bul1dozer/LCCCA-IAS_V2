"""Password reset email delivery adapters."""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from . import auth, models
from .email_engine import _get_active_smtp, _send_via_smtp


def _mail_sink_path() -> Path:
    return Path(os.environ.get("LCCA_MAIL_SINK_PATH", ".pytest_tmp/mail_sink.jsonl"))


def _render(user: models.User, reset_link: str) -> tuple[str, str, str]:
    subject = "LCCA-IAS password reset"
    text = (
        f"Hello {user.full_name or user.username},\n\n"
        "A password reset was requested for your LCCA-IAS account. "
        "Open the link below to set a new password. This link expires in one hour "
        "and can only be used once.\n\n"
        f"{reset_link}\n\n"
        "If you did not request this change, ignore this email."
    )
    html = text.replace("\n", "<br>")
    return subject, text, html


def _write_notification(db: Session, user: models.User, status: str, subject: str, body: str, error: str | None = None) -> None:
    db.add(models.NotificationLog(
        channel="email",
        recipient=user.email or "",
        subject=subject,
        body_preview=body[:500],
        status=status,
        error_message=error,
        sent_at=datetime.utcnow() if status in {"sent", "simulated"} else None,
    ))


def send_password_reset(db: Session, user: models.User, reset_link: str) -> dict:
    subject, body_text, body_html = _render(user, reset_link)
    smtp_cfg = _get_active_smtp(db)
    provider = os.environ.get("LCCA_MAIL_PROVIDER", "").strip().lower()

    if smtp_cfg:
        try:
            message_id = asyncio.run(asyncio.wait_for(
                _send_via_smtp(smtp_cfg, user.email, subject, body_html, body_text),
                timeout=10.0,
            ))
            _write_notification(db, user, "sent", subject, body_text)
            return {"status": "sent", "provider": "smtp", "message_id": message_id}
        except Exception as exc:
            error = str(exc)[:300]
            _write_notification(db, user, "failed", subject, body_text, error)
            return {"status": "failed", "provider": "smtp", "error": error}

    if provider == "sink" or (not auth.IS_PRODUCTION and provider in {"", "memory", "file"}):
        path = _mail_sink_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "created_at": datetime.utcnow().isoformat(),
            "to": user.email,
            "subject": subject,
            "body_text": body_text,
            "reset_link": reset_link,
        }
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, sort_keys=True) + "\n")
        _write_notification(db, user, "simulated", subject, body_text)
        return {"status": "simulated", "provider": "sink", "path": str(path)}

    _write_notification(db, user, "failed", subject, body_text, "No password reset mail provider configured.")
    return {"status": "failed", "provider": None, "error": "No password reset mail provider configured."}
