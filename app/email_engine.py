"""
Email Engine v2 — real SMTP delivery with template support, retry logic,
and delivery tracking. Falls back to simulated mode when no SMTP config exists.

The SMTP send runs with asyncio.wait_for so a slow/blocked connection
cannot stall the event loop indefinitely.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def _get_active_smtp(db: "Session"):
    from . import models
    return db.query(models.SmtpConfig).filter(models.SmtpConfig.is_active == True).first()


def _parent_salutation(parent) -> str:
    if not parent:
        return "Parent/Guardian"

    relationship_types = [
        str(getattr(rel, "relationship_type", "") or "").strip().lower()
        for rel in getattr(parent, "relationships_", []) or []
    ]
    title = None
    if any(rel_type in {"father", "dad"} for rel_type in relationship_types):
        title = "Mr"
    elif any(rel_type in {"mother", "mom", "mum"} for rel_type in relationship_types):
        title = "Mrs"

    full_name = str(getattr(parent, "full_name", "") or "").strip()
    if not title:
        return full_name or "Parent/Guardian"

    if full_name.lower().startswith(("mr ", "mrs ", "ms ", "miss ", "dr ", "prof ")):
        return full_name
    return f"{title} {full_name}" if full_name else title


def _email_recipient_parent(parents: list, recipient_email: Optional[str] = None):
    if not parents:
        return None
    if recipient_email:
        lowered = recipient_email.strip().lower()
        matched = next(
            (p for p in parents if str(getattr(p, "email", "") or "").strip().lower() == lowered),
            None,
        )
        if matched:
            return matched
    return next((p for p in parents if getattr(p, "email", None)), None) or parents[0]


async def _send_via_smtp(
    smtp_cfg,
    to_addr: str,
    subject: str,
    body_html: str,
    body_text: str = "",
    attachment_bytes: Optional[bytes] = None,
    attachment_filename: Optional[str] = None,
) -> str:
    """Send one email via aiosmtplib. Returns message-id string."""
    import aiosmtplib
    from email.message import EmailMessage
    import uuid

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"{smtp_cfg.from_name} <{smtp_cfg.from_address}>"
    msg["To"] = to_addr
    msg_id = f"<{uuid.uuid4()}@lcca-ias>"
    msg["Message-ID"] = msg_id
    msg.set_content(body_text or body_html.replace("<br>", "\n"))
    msg.add_alternative(body_html, subtype="html")

    if attachment_bytes:
        msg.add_attachment(
            attachment_bytes,
            maintype="application",
            subtype="pdf",
            filename=attachment_filename or "Invoice.pdf",
        )

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
    recipient_parent = _email_recipient_parent(parents, recipient_email)
    recipient_name = _parent_salutation(recipient_parent) if recipient_parent else None
    if not recipient_email and recipient_parent and getattr(recipient_parent, "email", None):
        recipient_email = recipient_parent.email
    if not recipient_email:
        recipient_email = "no-email-on-file@lcca.edu.na"

    if learner:
        billing_label = learner.full_name
        learner_sentence = f"{learner.full_name} ({learner.learner_code})"
        notification_learner_id = learner.id
    else:
        primary_parent = parents[0] if parents else None
        billing_label = primary_parent.full_name if primary_parent else "Parent/Guardian"
        linked_ids = [item.learner_id for item in getattr(invoice, "items", []) if item.learner_id]
        notification_learner_id = linked_ids[0] if linked_ids else None
        learner_sentence = "the linked learners on your family account"

    subject = f"LCCA Invoice {invoice.invoice_number} - {billing_label}"
    body_text = (
        f"Dear {recipient_name or 'Parent/Guardian'},\n\n"
        f"Please find attached Invoice {invoice.invoice_number} for "
        f"{learner_sentence}. "
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
                _send_via_smtp(
                    smtp_cfg,
                    recipient_email,
                    subject,
                    body_html,
                    body_text,
                    attachment_bytes=pdf_bytes,
                    attachment_filename=f"Invoice_{invoice.invoice_number}.pdf",
                ),
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
        learner_id=notification_learner_id,
        recipient=recipient_email,
        subject=subject,
        body_preview=body_text[:500],
        status=status.lower(),
        error_message=error_msg,
        sent_at=datetime.utcnow() if status in ("Sent", "Simulated") else None,
    ))

    if status in ("Sent", "Simulated"):
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
        error_message=error_msg,
        sent_at=datetime.utcnow() if status in ("sent", "simulated") else None,
    ))

    return {"status": status, "provider_id": provider_id, "error": error_msg}
