"""
Schema / model-level integrity tests: NUMERIC types, CHECK constraints,
soft delete fields, uniqueness.
"""
from decimal import Decimal
from datetime import date
import pytest
from sqlalchemy.exc import IntegrityError


def test_learner_balance_is_decimal_not_float(db_session, sample_learner):
    assert isinstance(sample_learner.balance, Decimal)


def test_ledger_entry_rejects_negative_amount(db_session, sample_learner):
    from app import models
    entry = models.LedgerEntry(
        learner_id=sample_learner.id, transaction_type="invoice",
        reference_type="Invoice", reference_id=1,
        amount=Decimal("-10.00"), dc_indicator="DR",
        transaction_date=date(2026, 1, 1), created_by="pytest",
    )
    db_session.add(entry)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_ledger_entry_rejects_invalid_dc_indicator(db_session, sample_learner):
    from app import models
    entry = models.LedgerEntry(
        learner_id=sample_learner.id, transaction_type="invoice",
        reference_type="Invoice", reference_id=1,
        amount=Decimal("100.00"), dc_indicator="XX",
        transaction_date=date(2026, 1, 1), created_by="pytest",
    )
    db_session.add(entry)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_payment_rejects_zero_amount_at_db_level(db_session, sample_learner):
    from app import models
    p = models.Payment(
        learner_id=sample_learner.id, amount_paid=Decimal("0.00"),
        payment_date=date(2026, 1, 1), date_paid=date(2026, 1, 1),
        payment_method="Cash",
    )
    db_session.add(p)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_invoice_number_uniqueness_enforced(db_session, sample_learner):
    from app import models
    inv1 = models.Invoice(
        invoice_number="INV-DUPTEST-000001", learner_id=sample_learner.id,
        issue_date=date(2026, 1, 1), due_date=date(2026, 1, 31),
        current_charges=Decimal("100.00"),
    )
    db_session.add(inv1)
    db_session.commit()

    inv2 = models.Invoice(
        invoice_number="INV-DUPTEST-000001", learner_id=sample_learner.id,
        issue_date=date(2026, 1, 1), due_date=date(2026, 1, 31),
        current_charges=Decimal("200.00"),
    )
    db_session.add(inv2)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_learner_soft_delete_fields_default_active(db_session, sample_learner):
    assert sample_learner.is_active is True
    assert sample_learner.deleted_at is None


def test_learner_fee_item_uniqueness(db_session, sample_learner):
    from app import models
    fi = models.FeeItem(name="Dup Fee", category="other", frequency="monthly",
                        amount=Decimal("50.00"), is_active=True)
    db_session.add(fi)
    db_session.commit()

    a1 = models.LearnerFeeItem(learner_id=sample_learner.id, fee_item_id=fi.id)
    db_session.add(a1)
    db_session.commit()

    a2 = models.LearnerFeeItem(learner_id=sample_learner.id, fee_item_id=fi.id)
    db_session.add(a2)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_fee_item_amount_nonnegative_constraint(db_session):
    from app import models
    fi = models.FeeItem(
        name="Bad Fee", category="other", frequency="monthly",
        amount=Decimal("-5.00"), is_active=True,
    )
    db_session.add(fi)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_learner_id_is_generated_from_admission_date_and_count(db_session):
    from app import models
    from datetime import date

    learner = models.Learner(
        learner_code="TEST-LEARNER-1",
        full_name="Test Learner",
        grade="Grade 1",
        class_name="1A",
        date_of_admission=date(2026, 3, 4),
        status="Active",
        balance=Decimal("0.00"),
    )
    db_session.add(learner)
    db_session.commit()
    db_session.refresh(learner)

    assert learner.learner_id is not None
    assert len(learner.learner_id) == 10
    assert learner.learner_id.startswith("260304")


def test_parent_can_store_employer_details(db_session):
    from app import models

    parent = models.Parent(
        full_name="Parent Example",
        email="parent@example.com",
        phone="0812345678",
        address="123 Main Street",
        employer_name="ABC Company",
        employer_address="456 Work Road",
        employer_phone="0887654321",
        position="Accountant",
    )
    db_session.add(parent)
    db_session.commit()
    db_session.refresh(parent)

    assert parent.employer_name == "ABC Company"
    assert parent.position == "Accountant"
