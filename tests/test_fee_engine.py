"""
Unit tests: fee_engine.py — billing/invoice generation logic.
"""
from decimal import Decimal
from datetime import date
import pytest


@pytest.fixture()
def fee_item(db_session):
    from app import models
    fi = models.FeeItem(
        name="Test Tuition", category="tuition", frequency="monthly",
        amount=Decimal("1500.00"), is_mandatory=True, is_active=True,
    )
    db_session.add(fi)
    db_session.commit()
    db_session.refresh(fi)
    return fi


def test_effective_amount_uses_custom_override(db_session, sample_learner, fee_item):
    from app import models
    from app.fee_engine import effective_amount
    asgn = models.LearnerFeeItem(
        learner_id=sample_learner.id, fee_item_id=fee_item.id,
        custom_amount=Decimal("999.99"), is_active=True,
    )
    db_session.add(asgn)
    db_session.commit()
    asgn.fee_item = fee_item
    assert effective_amount(asgn) == Decimal("999.99")


def test_effective_amount_falls_back_to_catalogue_price(db_session, sample_learner, fee_item):
    from app import models
    from app.fee_engine import effective_amount
    asgn = models.LearnerFeeItem(
        learner_id=sample_learner.id, fee_item_id=fee_item.id, is_active=True,
    )
    db_session.add(asgn)
    db_session.commit()
    asgn.fee_item = fee_item
    assert effective_amount(asgn) == Decimal("1500.00")


def test_generate_invoice_no_billable_lines_returns_none(db_session, sample_learner):
    from app.fee_engine import generate_invoice_for_learner
    invoice = generate_invoice_for_learner(
        db=db_session, learner=sample_learner, due_date=date(2026, 2, 28),
        billing_period="2026-02", triggered_by="pytest",
    )
    assert invoice is None


def test_generate_invoice_creates_ledger_entry(db_session, sample_learner, fee_item):
    from app import models
    from app.fee_engine import generate_invoice_for_learner
    from app.ledger import compute_balance

    db_session.add(models.LearnerFeeItem(
        learner_id=sample_learner.id, fee_item_id=fee_item.id, is_active=True,
    ))
    db_session.commit()

    invoice = generate_invoice_for_learner(
        db=db_session, learner=sample_learner, due_date=date(2026, 2, 28),
        billing_period="2026-02", triggered_by="pytest",
    )
    db_session.commit()

    assert invoice is not None
    assert invoice.current_charges == Decimal("1500.00")
    assert invoice.invoice_number.startswith("INV-")
    assert compute_balance(sample_learner.id, db_session) == Decimal("1500.00")


def test_generate_invoice_carries_forward_previous_balance(db_session, sample_learner, fee_item):
    from app import models
    from app.fee_engine import generate_invoice_for_learner

    db_session.add(models.LearnerFeeItem(
        learner_id=sample_learner.id, fee_item_id=fee_item.id, is_active=True,
    ))
    db_session.commit()

    inv1 = generate_invoice_for_learner(
        db=db_session, learner=sample_learner, due_date=date(2026, 2, 28),
        billing_period="2026-02", triggered_by="pytest",
    )
    db_session.commit()
    assert inv1.previous_balance == Decimal("0.00")
    assert inv1.outstanding_balance == Decimal("1500.00")

    inv2 = generate_invoice_for_learner(
        db=db_session, learner=sample_learner, due_date=date(2026, 3, 31),
        billing_period="2026-03", triggered_by="pytest",
    )
    db_session.commit()
    assert inv2.previous_balance == Decimal("1500.00")
    assert inv2.outstanding_balance == Decimal("3000.00")


def test_auto_assign_mandatory_fees(db_session, sample_learner, fee_item):
    from app import models
    from app.fee_engine import auto_assign_mandatory_fees

    # The number of mandatory, active fee items currently in the catalogue
    # (other tests in this session may have created additional ones, since
    # the test DB is shared across the session — so we measure against the
    # actual catalogue state rather than assuming a fixed count).
    expected = db_session.query(models.FeeItem).filter(
        models.FeeItem.is_mandatory == True, models.FeeItem.is_active == True,
    ).count()

    count = auto_assign_mandatory_fees(db_session, sample_learner, assigned_by="pytest")
    db_session.commit()
    assert count == expected
    assert count >= 1  # at minimum, the fee_item fixture's own item was assigned

    # Running again should not duplicate any assignment
    count2 = auto_assign_mandatory_fees(db_session, sample_learner, assigned_by="pytest")
    db_session.commit()
    assert count2 == 0


def test_auto_assign_skips_non_matching_grade(db_session, sample_learner):
    """A mandatory fee item restricted to a different grade should not be assigned."""
    from app import models
    from app.fee_engine import auto_assign_mandatory_fees

    fi = models.FeeItem(
        name="Grade 10 Only Fee", category="other", frequency="monthly",
        amount=Decimal("75.00"), is_mandatory=True, is_active=True,
        applicable_grades="Grade 10",  # sample_learner is Grade 5
    )
    db_session.add(fi)
    db_session.commit()

    assignments_before = db_session.query(models.LearnerFeeItem).filter(
        models.LearnerFeeItem.learner_id == sample_learner.id,
        models.LearnerFeeItem.fee_item_id == fi.id,
    ).count()
    auto_assign_mandatory_fees(db_session, sample_learner, assigned_by="pytest")
    db_session.commit()
    assignments_after = db_session.query(models.LearnerFeeItem).filter(
        models.LearnerFeeItem.learner_id == sample_learner.id,
        models.LearnerFeeItem.fee_item_id == fi.id,
    ).count()
    assert assignments_before == 0
    assert assignments_after == 0


def test_auto_assign_skips_non_matching_class(db_session, sample_learner):
    """A mandatory fee item restricted to a different class should not be assigned."""
    from app import models
    from app.fee_engine import auto_assign_mandatory_fees

    fi = models.FeeItem(
        name="Class 9B Only Fee", category="other", frequency="monthly",
        amount=Decimal("60.00"), is_mandatory=True, is_active=True,
        applicable_classes="9B",  # sample_learner is class 5A
    )
    db_session.add(fi)
    db_session.commit()

    auto_assign_mandatory_fees(db_session, sample_learner, assigned_by="pytest")
    db_session.commit()
    linked = db_session.query(models.LearnerFeeItem).filter(
        models.LearnerFeeItem.learner_id == sample_learner.id,
        models.LearnerFeeItem.fee_item_id == fi.id,
    ).count()
    assert linked == 0


def test_get_billing_lines_respects_effective_date_window(db_session, sample_learner):
    """Fee items outside their effective_from/effective_to window are excluded."""
    from app import models
    from app.fee_engine import get_billing_lines

    future_fee = models.FeeItem(
        name="Future Fee", category="other", frequency="monthly",
        amount=Decimal("40.00"), is_active=True,
        effective_from=date(2099, 1, 1),  # not yet effective
    )
    expired_fee = models.FeeItem(
        name="Expired Fee", category="other", frequency="monthly",
        amount=Decimal("40.00"), is_active=True,
        effective_to=date(2000, 1, 1),  # long expired
    )
    db_session.add_all([future_fee, expired_fee])
    db_session.commit()

    db_session.add(models.LearnerFeeItem(learner_id=sample_learner.id, fee_item_id=future_fee.id, is_active=True))
    db_session.add(models.LearnerFeeItem(learner_id=sample_learner.id, fee_item_id=expired_fee.id, is_active=True))
    db_session.commit()

    lines = get_billing_lines(db_session, sample_learner, frequency_filter="monthly", as_of_date=date(2026, 6, 1))
    names = [name for name, _, _ in lines]
    assert "Future Fee" not in names
    assert "Expired Fee" not in names
