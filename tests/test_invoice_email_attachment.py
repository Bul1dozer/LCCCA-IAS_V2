import asyncio
import sys
from datetime import date
from decimal import Decimal
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


def test_invoice_email_failure_does_not_mark_invoice_sent(monkeypatch):
    import app.email_engine as email_engine

    class FakeDb:
        def __init__(self):
            self.added = []

        def add(self, item):
            self.added.append(item)

    async def fake_send(*args, **kwargs):
        raise TimeoutError("SMTP timeout")

    smtp_cfg = SimpleNamespace(
        from_name="LCCCA Academic Office",
        from_address="accounts@example.test",
        host="smtp.example.test",
        port=587,
        username="accounts@example.test",
        password_encrypted="not-a-real-password",
    )
    invoice = SimpleNamespace(
        id=34,
        invoice_number="INV-2026-000034",
        outstanding_balance=Decimal("100.00"),
        due_date=date(2026, 8, 31),
        status="Generated",
    )
    learner = SimpleNamespace(id=12, full_name="Test Learner", learner_code="LCCA-2026-0012")
    parent = SimpleNamespace(full_name="Test Parent", email="parent@example.test")

    monkeypatch.setattr(email_engine, "_get_active_smtp", lambda db: smtp_cfg)
    monkeypatch.setattr(email_engine, "_send_via_smtp", fake_send)

    result = asyncio.run(email_engine.send_invoice_email(
        FakeDb(),
        invoice,
        learner,
        [parent],
        b"%PDF invoice bytes",
    ))

    assert result["status"] == "Failed"
    assert invoice.status == "Generated"


def test_parent_salutation_uses_title_from_relationship_type():
    from app.email_engine import _parent_salutation

    father = SimpleNamespace(
        full_name="Samuel Nambinga",
        relationships_=[SimpleNamespace(relationship_type="Father")],
    )
    mother = SimpleNamespace(
        full_name="Maria Mocke",
        relationships_=[SimpleNamespace(relationship_type="Mother")],
    )
    already_titled = SimpleNamespace(
        full_name="Mrs Helena Shivute",
        relationships_=[SimpleNamespace(relationship_type="Mother")],
    )
    guardian = SimpleNamespace(
        full_name="Alex Guardian",
        relationships_=[SimpleNamespace(relationship_type="Guardian")],
    )

    assert _parent_salutation(father) == "Mr Samuel Nambinga"
    assert _parent_salutation(mother) == "Mrs Maria Mocke"
    assert _parent_salutation(already_titled) == "Mrs Helena Shivute"
    assert _parent_salutation(guardian) == "Alex Guardian"


def test_invoice_email_body_addresses_parent_with_relationship_title(monkeypatch):
    import app.email_engine as email_engine

    class FakeDb:
        def __init__(self):
            self.added = []

        def add(self, item):
            self.added.append(item)

    invoice = SimpleNamespace(
        id=35,
        invoice_number="INV-2026-000035",
        outstanding_balance=Decimal("250.00"),
        due_date=date(2026, 8, 31),
        status="Generated",
    )
    learner = SimpleNamespace(id=12, full_name="Test Learner", learner_code="LCCA-2026-0012")
    parent = SimpleNamespace(
        full_name="Maria Mocke",
        email="parent@example.test",
        relationships_=[SimpleNamespace(relationship_type="Mother")],
    )
    fake_db = FakeDb()

    monkeypatch.setattr(email_engine, "_get_active_smtp", lambda db: None)

    asyncio.run(email_engine.send_invoice_email(
        fake_db,
        invoice,
        learner,
        [parent],
        b"%PDF invoice bytes",
    ))

    body_previews = [
        getattr(item, "body_preview", "")
        for item in fake_db.added
        if getattr(item, "body_preview", "")
    ]
    assert any(body.startswith("Dear Mrs Maria Mocke,") for body in body_previews)
