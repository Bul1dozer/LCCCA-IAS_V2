"""
LCCA-IAS Fee Engine — v3 (Production).

All monetary values use Decimal throughout.
Invoice numbers generated via ledger.next_invoice_number() (atomic).
Balance computation delegates to ledger.compute_balance().
"""

from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional, Tuple
from sqlalchemy.orm import Session

from . import models
from .ledger import (
    quantize,
    next_invoice_number,
    post_invoice,
    compute_balance,
)


GRADE_ORDER = [
    "Baby Class", "Toddler Class", "Grade 0",
    "Grade 1", "Grade 2", "Grade 3", "Grade 4",
    "Grade 5", "Grade 6", "Grade 7", "Grade 8", "Grade 9",
]


def get_active_fee_items_for_learner(db: Session, learner: models.Learner) -> List[models.LearnerFeeItem]:
    """Return all active LearnerFeeItem assignments for a learner."""
    return (
        db.query(models.LearnerFeeItem)
        .join(models.FeeItem)
        .filter(
            models.LearnerFeeItem.learner_id == learner.id,
            models.LearnerFeeItem.is_active == True,
            models.FeeItem.is_active == True,
        )
        .order_by(models.FeeItem.sort_order, models.FeeItem.name)
        .all()
    )


def effective_amount(assignment: models.LearnerFeeItem) -> Decimal:
    """Return the billing amount as Decimal: custom override if set, else catalogue price."""
    if assignment.custom_amount is not None:
        return quantize(assignment.custom_amount)
    return quantize(assignment.fee_item.amount)


def get_billing_lines(
    db: Session,
    learner: models.Learner,
    frequency_filter: Optional[str] = None,
    as_of_date: Optional[date] = None,
) -> List[Tuple[str, Decimal, int]]:
    """
    Returns [(description, amount: Decimal, fee_item_id), ...] for the learner's profile.
    All amounts are Decimal — no floats.
    """
    today = as_of_date or date.today()
    assignments = get_active_fee_items_for_learner(db, learner)
    lines = []
    for asgn in assignments:
        fi = asgn.fee_item
        if frequency_filter and fi.frequency != frequency_filter:
            continue
        if fi.effective_from and fi.effective_from > today:
            continue
        if fi.effective_to and fi.effective_to < today:
            continue
        lines.append((fi.name, effective_amount(asgn), fi.id))
    return lines


def auto_assign_mandatory_fees(db: Session, learner: models.Learner, assigned_by: str = "system") -> int:
    """
    Auto-assign all active mandatory FeeItems that match the learner's grade/class.
    Does not duplicate existing active assignments.
    Returns count of new assignments made.
    """
    mandatory_items = (
        db.query(models.FeeItem)
        .filter(models.FeeItem.is_mandatory == True, models.FeeItem.is_active == True)
        .all()
    )

    existing_ids = {
        a.fee_item_id for a in
        db.query(models.LearnerFeeItem)
        .filter(models.LearnerFeeItem.learner_id == learner.id, models.LearnerFeeItem.is_active == True)
        .all()
    }

    assigned = 0
    for fi in mandatory_items:
        if fi.id in existing_ids:
            continue
        if fi.applicable_grades:
            grades = [g.strip() for g in fi.applicable_grades.split(",")]
            if learner.grade not in grades:
                continue
        if fi.applicable_classes:
            classes = [c.strip() for c in fi.applicable_classes.split(",")]
            if learner.class_name not in classes:
                continue
        asgn = models.LearnerFeeItem(
            learner_id=learner.id,
            fee_item_id=fi.id,
            is_active=True,
            assigned_by=assigned_by,
            notes="Auto-assigned (mandatory rule)",
        )
        db.add(asgn)
        assigned += 1

    return assigned


def generate_invoice_for_learner(
    db: Session,
    learner: models.Learner,
    due_date: date,
    billing_period: str,
    frequency: str = "monthly",
    triggered_by: str = "manual",
    month_end_run_id: Optional[int] = None,
) -> Optional[models.Invoice]:
    """
    Core invoice generation from a learner's fee profile.

    Changes from v2:
    - All amounts are Decimal
    - previous_balance derived from ledger (not learner.balance)
    - payments_made derived from ledger (not a manual sum)
    - Invoice number generated atomically via next_invoice_number()
    - Posts a DR ledger entry after invoice creation
    - No mutation of learner.balance directly (ledger handles it)

    Returns the created Invoice or None if no billable lines exist.
    """
    lines = get_billing_lines(db, learner, frequency_filter=frequency)
    if not lines:
        return None

    # Current charges: sum of all line items as Decimal
    current_charges = sum((amt for _, amt, _ in lines), Decimal("0.00"))
    current_charges = quantize(current_charges)

    # Previous balance: derived from ledger (authoritative)
    previous_balance = compute_balance(learner.id, db)

    # Payments since last invoice: ledger CR entries since last invoice date
    last_invoice = (
        db.query(models.Invoice)
        .filter(
            models.Invoice.learner_id == learner.id,
            models.Invoice.status.notin_(["Void", "Reversed"]),
        )
        .order_by(models.Invoice.created_at.desc())
        .first()
    )

    # payments_made = CR ledger entries since last invoice
    if last_invoice:
        payments_q = (
            db.query(models.LedgerEntry)
            .filter(
                models.LedgerEntry.learner_id == learner.id,
                models.LedgerEntry.dc_indicator == "CR",
                models.LedgerEntry.transaction_type == "payment",
                models.LedgerEntry.is_voided == False,
                models.LedgerEntry.transaction_date > last_invoice.issue_date,
            )
        )
    else:
        payments_q = (
            db.query(models.LedgerEntry)
            .filter(
                models.LedgerEntry.learner_id == learner.id,
                models.LedgerEntry.dc_indicator == "CR",
                models.LedgerEntry.transaction_type == "payment",
                models.LedgerEntry.is_voided == False,
            )
        )

    payments_made = sum(
        (quantize(e.amount) for e in payments_q.all()),
        Decimal("0.00")
    )

    # Outstanding = previous + current - payments (at time of invoice)
    outstanding_balance = quantize(previous_balance + current_charges - payments_made)

    # Generate invoice number atomically
    inv_number = next_invoice_number(db)

    invoice = models.Invoice(
        invoice_number=inv_number,
        learner_id=learner.id,
        issue_date=date.today(),
        due_date=due_date,
        previous_balance=previous_balance,
        current_charges=current_charges,
        payments_made=payments_made,
        outstanding_balance=outstanding_balance,
        status="Generated",
        billing_period=billing_period,
        month_end_run_id=month_end_run_id,
        created_by=triggered_by,
    )
    db.add(invoice)
    db.flush()  # Get invoice.id before posting to ledger

    # Add invoice line items
    for desc, amount, fee_item_id in lines:
        db.add(models.InvoiceItem(
            invoice_id=invoice.id,
            description=desc,
            amount=amount,
            fee_item_id=fee_item_id,
        ))

    # Post DR to ledger (this also syncs learner.balance cache)
    post_invoice(
        db=db,
        learner_id=learner.id,
        invoice_id=invoice.id,
        amount=current_charges,
        transaction_date=invoice.issue_date,
        created_by=triggered_by,
        notes=f"Invoice {inv_number} — {billing_period}",
    )

    return invoice
