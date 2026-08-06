import asyncio
import sys
from types import SimpleNamespace


def test_invoice_email_smtp_message_includes_pdf_attachment(monkeypatch):
    from app.email_engine import _send_via_smtp

    captured = {}

    async def fake_send(message, **kwargs):
        captured["message"] = message
        captured["kwargs"] = kwargs

    monkeypatch.setitem(sys.modules, "aiosmtplib", SimpleNamespace(send=fake_send))

    smtp_cfg = SimpleNamespace(
        from_name="LCCCA Academic Office",
        from_address="accounts@example.test",
        host="smtp.example.test",
        port=587,
        username="accounts@example.test",
        password_encrypted="not-a-real-password",
    )

    asyncio.run(_send_via_smtp(
        smtp_cfg,
        "parent@example.test",
        "LCCA Invoice INV-2026-000034",
        "Dear Parent,<br>Please find attached.",
        "Dear Parent,\nPlease find attached.",
        attachment_bytes=b"%PDF invoice bytes",
        attachment_filename="Invoice_INV-2026-000034.pdf",
    ))

    attachments = list(captured["message"].iter_attachments())
    assert len(attachments) == 1
    assert attachments[0].get_filename() == "Invoice_INV-2026-000034.pdf"
    assert attachments[0].get_content_type() == "application/pdf"
    assert attachments[0].get_payload(decode=True) == b"%PDF invoice bytes"
