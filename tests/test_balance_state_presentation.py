from datetime import date
from decimal import Decimal
from types import SimpleNamespace


def test_invoice_pdf_balance_state_for_payment_due():
    from app.pdf_generator import _invoice_balance_presentation

    invoice = SimpleNamespace(
        outstanding_balance=Decimal("924.00"),
        due_date=date(2026, 8, 31),
    )

    state = _invoice_balance_presentation(invoice)

    assert state["summary_label"] == "Outstanding Balance Due"
    assert state["summary_amount"] == "N$ 924.00"
    assert state["notice"] == "PAYMENT DUE BY: 31/08/2026"


def test_invoice_pdf_balance_state_for_credit_uses_absolute_amount():
    from app.pdf_generator import _invoice_balance_presentation

    invoice = SimpleNamespace(
        outstanding_balance=Decimal("-924.00"),
        due_date=date(2026, 8, 31),
    )

    state = _invoice_balance_presentation(invoice)

    assert state["summary_label"] == "Credit Balance Applied"
    assert state["summary_amount"] == "N$ 924.00"
    assert state["notice"] == "DO NOT PAY - CREDIT APPLIED"
    assert "carried forward" in state["message"]


def test_invoice_pdf_balance_state_for_settled_account():
    from app.pdf_generator import _invoice_balance_presentation

    invoice = SimpleNamespace(
        outstanding_balance=Decimal("0.00"),
        due_date=date(2026, 8, 31),
    )

    state = _invoice_balance_presentation(invoice)

    assert state["summary_label"] == "Account Settled"
    assert state["summary_amount"] == "N$ 0.00"
    assert state["notice"] == "NO PAYMENT REQUIRED"
