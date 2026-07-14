"""Fee engine — billing/invoice generation with Decimal arithmetic."""
from datetime import date
from decimal import Decimal
from typing import List, Optional, Tuple
from sqlalchemy.orm import Session
from . import models
from .ledger import quantize, next_invoice_number, post_invoice, compute_balance


def get_active_fee_items_for_learner(db: Session, learner: models.Learner):
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

def effective_amount(asgn: models.LearnerFeeItem) -> Decimal:
    if asgn.custom_amount is not None:
        return quantize(asgn.custom_amount)
    return quantize(asgn.fee_item.amount)

def get_billing_lines(db, learner, frequency_filter=None, as_of_date=None):
    today = as_of_date or date.today()
    assignments = get_active_fee_items_for_learner(db, learner)
    lines = []
    for a in assignments:
        fi = a.fee_item
        if frequency_filter and fi.frequency != frequency_filter:
            continue
        if fi.effective_from and fi.effective_from > today:
            continue
        if fi.effective_to and fi.effective_to < today:
            continue
        lines.append((fi.name, effective_amount(a), fi.id))
    return lines

def auto_assign_mandatory_fees(db: Session, learner: models.Learner, assigned_by="system") -> int:
    mandatory = db.query(models.FeeItem).filter(
        models.FeeItem.is_mandatory == True, models.FeeItem.is_active == True,
    ).all()
    existing_ids = {a.fee_item_id for a in
                    db.query(models.LearnerFeeItem)
                    .filter(models.LearnerFeeItem.learner_id == learner.id,
                            models.LearnerFeeItem.is_active == True).all()}
    assigned = 0
    for fi in mandatory:
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
        db.add(models.LearnerFeeItem(
            learner_id=learner.id, fee_item_id=fi.id,
            is_active=True, assigned_by=assigned_by, notes="Auto-assigned",
        ))
        assigned += 1
    return assigned

def generate_invoice_for_learner(db, learner, due_date, billing_period,
                                  frequency="monthly", triggered_by="manual",
                                  month_end_run_id=None):
    lines = get_billing_lines(db, learner, frequency_filter=frequency)
    if not lines:
        return None
    current_charges = sum((amt for _, amt, _ in lines), Decimal("0.00"))
    current_charges = quantize(current_charges)
    previous_balance = compute_balance(learner.id, db)

    last_invoice = (
        db.query(models.Invoice)
        .filter(models.Invoice.learner_id == learner.id,
                models.Invoice.status.notin_(["Void", "Reversed"]))
        .order_by(models.Invoice.created_at.desc()).first()
    )
    if last_invoice:
        pmts_q = db.query(models.LedgerEntry).filter(
            models.LedgerEntry.learner_id == learner.id,
            models.LedgerEntry.dc_indicator == "CR",
            models.LedgerEntry.transaction_type == "payment",
            models.LedgerEntry.is_voided == False,
            models.LedgerEntry.transaction_date > last_invoice.issue_date,
        )
    else:
        pmts_q = db.query(models.LedgerEntry).filter(
            models.LedgerEntry.learner_id == learner.id,
            models.LedgerEntry.dc_indicator == "CR",
            models.LedgerEntry.transaction_type == "payment",
            models.LedgerEntry.is_voided == False,
        )
    payments_made = sum((quantize(e.amount) for e in pmts_q.all()), Decimal("0.00"))
    outstanding = quantize(previous_balance + current_charges - payments_made)

    inv_number = next_invoice_number(db)
    invoice = models.Invoice(
        invoice_number=inv_number, learner_id=learner.id,
        issue_date=date.today(), due_date=due_date,
        previous_balance=previous_balance, current_charges=current_charges,
        payments_made=payments_made, outstanding_balance=outstanding,
        status="Generated", billing_period=billing_period,
        month_end_run_id=month_end_run_id, created_by=triggered_by,
    )
    db.add(invoice)
    db.flush()
    for desc, amount, fee_item_id in lines:
        db.add(models.InvoiceItem(
            invoice_id=invoice.id, learner_id=learner.id,
            description=desc, amount=amount, fee_item_id=fee_item_id,
        ))
    post_invoice(db, learner.id, invoice.id, current_charges,
                 invoice.issue_date, triggered_by,
                 f"Invoice {inv_number} — {billing_period}")
    return invoice
