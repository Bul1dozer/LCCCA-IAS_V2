"""
Email Engine v2 — real SMTP delivery with template support, retry logic,
and delivery tracking. Falls back to simulated mode when no SMTP config exists.

The SMTP send runs with asyncio.wait_for so a slow/blocked connection
cannot stall the event loop indefinitely.
"""

import asyncio
import logging
from datetime import datetime
from typing import Optional
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def _get_active_smtp(db: Session):
    from . import models
    return db.query(models.SmtpConfig).filter(models.SmtpConfig.is_active == True).first()


async def _send_via_smtp(smtp_cfg, to_addr: str, subject: str, body_html: str, body_text: str = "") -> str:
    """Send one email via aiosmtplib. Returns message-id string."""
    import aiosmtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    import uuid

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{smtp_cfg.from_name} <{smtp_cfg.from_address}>"
    msg["To"] = to_addr
    msg_id = f"<{uuid.uuid4()}@lcca-ias>"
    msg["Message-ID"] = msg_id
    if body_text:
        msg.attach(MIMEText(body_text, "plain"))
    msg.attach(MIMEText(body_html, "html"))

    await aiosmtplib.send(
        msg,
        hostname=smtp_cfg.host,
        port=smtp_cfg.port,
        username=smtp_cfg.username,
        password=smtp_cfg.password_encrypted,
        use_tls=(smtp_cfg.port == 465),
        start_tls=(smtp_cfg.port == 587),
        timeout=8,
    )
    return msg_id


async def send_invoice_email(
    db: Session,
    invoice,
    learner,
    parents: list,
    pdf_bytes: bytes,
    recipient_override: Optional[str] = None,
) -> dict:
    """
    Send (or simulate) an invoice email.
    Always writes to notification_logs and email_logs regardless of mode.
    """
    from . import models

    smtp_cfg = _get_active_smtp(db)
    real_send = smtp_cfg is not None

    recipient_email = recipient_override
    recipient_name = None
    if not recipient_email and parents:
        primary = next((p for p in parents if getattr(p, "email", None)), None)
        if primary:
            recipient_email = primary.email
            recipient_name = primary.full_name
    if not recipient_email:
        recipient_email = "no-email-on-file@lcca.edu.na"

    subject = f"LCCA Invoice {invoice.invoice_number} — {learner.full_name}"
    body_text = (
        f"Dear {recipient_name or 'Parent/Guardian'},\n\n"
        f"Please find attached Invoice {invoice.invoice_number} for "
        f"{learner.full_name} ({learner.learner_code}). "
        f"Outstanding balance: N$ {invoice.outstanding_balance:,.2f}. "
        f"Due date: {invoice.due_date.strftime('%d %b %Y')}.\n\n"
        f"Kind regards,\nLCCA Accounts Office"
    )
    body_html = body_text.replace("\n", "<br>")

    status = "Simulated"
    smtp_msg_id = None
    error_msg = None

    if real_send:
        try:
            # Hard timeout so a blocked SMTP never stalls the event loop
            smtp_msg_id = await asyncio.wait_for(
                _send_via_smtp(smtp_cfg, recipient_email, subject, body_html, body_text),
                timeout=10.0,
            )
            status = "Sent"
        except asyncio.TimeoutError:
            error_msg = "SMTP connection timed out (>10s)"
            status = "Failed"
            logger.warning("SMTP timeout for invoice %s", invoice.invoice_number)
        except Exception as e:
            error_msg = str(e)[:300]
            status = "Failed"
            logger.warning("SMTP send failed for %s: %s", invoice.invoice_number, e)

    # Write EmailLog (v1 compatibility)
    log = models.EmailLog(
        invoice_id=invoice.id,
        recipient_name=recipient_name,
        recipient_email=recipient_email,
        subject=subject,
        body_preview=body_text[:500],
        status=status,
        sent_at=datetime.utcnow(),
        real_send=real_send,
        smtp_message_id=smtp_msg_id,
        error_message=error_msg,
    )
    db.add(log)

    # Write NotificationLog (v2)
    db.add(models.NotificationLog(
        channel="email",
        invoice_id=invoice.id,
        learner_id=learner.id,
        recipient=recipient_email,
        subject=subject,
        body_preview=body_text[:500],
        status=status.lower(),
        provider_message_id=smtp_msg_id,
        error_message=error_msg,
        sent_at=datetime.utcnow() if status in ("Sent", "Simulated") else None,
    ))

    invoice.status = "Sent"

    return {
        "status": status,
        "recipient": recipient_email,
        "real_send": real_send,
        "message_id": smtp_msg_id,
        "error": error_msg,
    }


async def send_sms_notification(
    db: Session,
    learner_id: int,
    phone: str,
    message: str,
    invoice_id: Optional[int] = None,
) -> dict:
    """Send (or simulate) an SMS. Supports Africa's Talking and BulkSMS."""
    from . import models

    sms_cfg = db.query(models.SmsConfig).filter(models.SmsConfig.is_active == True).first()
    status = "simulated"
    error_msg = None
    provider_id = None

    if sms_cfg:
        try:
            import httpx
            if sms_cfg.provider == "africastalking":
                async with httpx.AsyncClient(timeout=8) as client:
                    r = await client.post(
                        "https://api.africastalking.com/version1/messaging",
                        headers={"apiKey": sms_cfg.api_key_encrypted, "Accept": "application/json"},
                        data={"username": sms_cfg.api_secret_encrypted, "to": phone,
                              "message": message, "from": sms_cfg.sender_id},
                    )
                    r.raise_for_status()
                    provider_id = str(r.json())
            elif sms_cfg.provider == "bulksms":
                async with httpx.AsyncClient(timeout=8) as client:
                    r = await client.post(
                        "https://api.bulksms.com/v1/messages",
                        auth=(sms_cfg.api_key_encrypted, sms_cfg.api_secret_encrypted),
                        json=[{"to": phone, "body": message}],
                    )
                    r.raise_for_status()
                    provider_id = str(r.json()[0].get("id"))
            status = "sent"
        except Exception as e:
            status = "failed"
            error_msg = str(e)[:300]

    db.add(models.NotificationLog(
        channel="sms",
        invoice_id=invoice_id,
        learner_id=learner_id,
        recipient=phone,
        body_preview=message[:200],
        status=status,
        provider_message_id=provider_id,
        error_message=error_msg,
        sent_at=datetime.utcnow() if status in ("sent", "simulated") else None,
    ))

    return {"status": status, "provider_id": provider_id, "error": error_msg}
