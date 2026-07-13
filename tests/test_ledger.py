"""
Unit tests: ledger.py — the financial core of LCCA-IAS.
"""
from decimal import Decimal
from datetime import date
import pytest


def test_quantize_handles_float_precision():
    from app.ledger import quantize
    # Classic floating point trap: 0.1 + 0.2 != 0.3 in raw float arithmetic
    result = quantize(0.1) + quantize(0.2)
    assert result == Decimal("0.30")
    assert str(result) == "0.30"


def test_quantize_rounds_half_up():
    from app.ledger import quantize
    assert quantize("10.005") == Decimal("10.01")
    assert quantize("10.004") == Decimal("10.00")


def test_compute_balance_empty_learner_is_zero(db_session, sample_learner):
    from app.ledger import compute_balance
    balance = compute_balance(sample_learner.id, db_session)
    assert balance == Decimal("0.00")


def test_post_invoice_increases_balance(db_session, sample_learner):
    from app.ledger import post_invoice, compute_balance
    post_invoice(
        db=db_session, learner_id=sample_learner.id, invoice_id=1,
        amount=Decimal("500.00"), transaction_date=date(2026, 1, 1),
        created_by="pytest",
    )
    db_session.commit()
    assert compute_balance(sample_learner.id, db_session) == Decimal("500.00")


def test_post_payment_decreases_balance(db_session, sample_learner):
    from app.ledger import post_invoice, post_payment, compute_balance
    post_invoice(db_session, sample_learner.id, 2, Decimal("500.00"), date(2026, 1, 1), "pytest")
    post_payment(db_session, sample_learner.id, 1, Decimal("200.00"), date(2026, 1, 5), "pytest")
    db_session.commit()
    assert compute_balance(sample_learner.id, db_session) == Decimal("300.00")


def test_post_invoice_rejects_zero_or_negative(db_session, sample_learner):
    from app.ledger import post_invoice
    with pytest.raises(ValueError):
        post_invoice(db_session, sample_learner.id, 3, Decimal("0.00"), date(2026, 1, 1), "pytest")
    with pytest.raises(ValueError):
        post_invoice(db_session, sample_learner.id, 4, Decimal("-50.00"), date(2026, 1, 1), "pytest")


def test_post_payment_rejects_zero_or_negative(db_session, sample_learner):
    from app.ledger import post_payment
    with pytest.raises(ValueError):
        post_payment(db_session, sample_learner.id, 2, Decimal("0.00"), date(2026, 1, 1), "pytest")
    with pytest.raises(ValueError):
        post_payment(db_session, sample_learner.id, 3, Decimal("-10.00"), date(2026, 1, 1), "pytest")


def test_validate_payment_amount(db_session):
    from app.ledger import validate_payment_amount
    assert validate_payment_amount(100) == Decimal("100.00")
    with pytest.raises(ValueError):
        validate_payment_amount(0)
    with pytest.raises(ValueError):
        validate_payment_amount(-1)


def test_void_invoice_restores_balance(db_session, sample_learner):
    from app.ledger import post_invoice, void_invoice_entries, compute_balance
    post_invoice(db_session, sample_learner.id, 5, Decimal("1000.00"), date(2026, 1, 1), "pytest")
    db_session.commit()
    assert compute_balance(sample_learner.id, db_session) == Decimal("1000.00")

    void_invoice_entries(db_session, 5, voided_by="pytest", void_reason="test void")
    db_session.commit()
    assert compute_balance(sample_learner.id, db_session) == Decimal("0.00")


def test_reverse_payment_restores_balance(db_session, sample_learner):
    from app.ledger import post_invoice, post_payment, reverse_payment_entry, compute_balance
    post_invoice(db_session, sample_learner.id, 6, Decimal("800.00"), date(2026, 1, 1), "pytest")
    post_payment(db_session, sample_learner.id, 4, Decimal("300.00"), date(2026, 1, 5), "pytest")
    db_session.commit()
    assert compute_balance(sample_learner.id, db_session) == Decimal("500.00")

    reverse_payment_entry(db_session, payment_id=4, reversed_by="pytest")
    db_session.commit()
    assert compute_balance(sample_learner.id, db_session) == Decimal("800.00")


def test_next_invoice_number_format(db_session):
    from app.ledger import next_invoice_number
    import re
    num = next_invoice_number(db_session)
    db_session.commit()
    assert re.match(r"^INV-\d{4}-\d{6}$", num), f"unexpected format: {num}"


def test_next_invoice_number_sequential_no_duplicates(db_session):
    from app.ledger import next_invoice_number
    numbers = []
    for _ in range(20):
        numbers.append(next_invoice_number(db_session))
        db_session.commit()
    assert len(numbers) == len(set(numbers)), "duplicate invoice numbers generated sequentially"


def test_check_duplicate_reference(db_session, sample_learner):
    from app import models
    from app.ledger import check_duplicate_reference
    p = models.Payment(
        learner_id=sample_learner.id, amount_paid=Decimal("50.00"),
        payment_date=date(2026, 1, 1), date_paid=date(2026, 1, 1),
        payment_method="Cash", reference_number="DUP-CHECK-1", is_active=True,
    )
    db_session.add(p)
    db_session.commit()
    assert check_duplicate_reference(db_session, "DUP-CHECK-1") is True
    assert check_duplicate_reference(db_session, "NONEXISTENT-REF") is False
    # excluding self should return False
    assert check_duplicate_reference(db_session, "DUP-CHECK-1", exclude_id=p.id) is False


def test_reconcile_check_balances(db_session, sample_learner):
    from app.ledger import post_invoice, reconcile_check
    post_invoice(db_session, sample_learner.id, 7, Decimal("1234.56"), date(2026, 1, 1), "pytest")
    db_session.commit()
    report = reconcile_check(db_session)
    assert report["reconciled"] is True
    assert Decimal(report["discrepancy"]) == Decimal("0.00")


def test_post_adjustment_dr_and_cr(db_session, sample_learner):
    from app.ledger import post_adjustment, compute_balance
    post_adjustment(db_session, sample_learner.id, Decimal("100.00"), "DR",
                    date(2026, 1, 1), "pytest", "manual fee adjustment")
    db_session.commit()
    assert compute_balance(sample_learner.id, db_session) == Decimal("100.00")

    post_adjustment(db_session, sample_learner.id, Decimal("40.00"), "CR",
                    date(2026, 1, 2), "pytest", "goodwill credit")
    db_session.commit()
    assert compute_balance(sample_learner.id, db_session) == Decimal("60.00")


def test_post_adjustment_invalid_dc_indicator(db_session, sample_learner):
    from app.ledger import post_adjustment
    with pytest.raises(ValueError):
        post_adjustment(db_session, sample_learner.id, Decimal("10.00"), "XX",
                        date(2026, 1, 1), "pytest", "bad indicator")
