"""
Payments router — v3 Production.

Changes:
- amount_paid validated as > 0 (rejects zero/negative)
- Duplicate reference_number check
- No mutable balance mutation — ledger.post_payment() handles it
- Soft delete instead of hard delete (reversed_at/reversed_by)
- Payment reversal posts a DR to the ledger
- payment_date used for financial calculations; created_at for audit only
"""

from datetime import date, datetime
import os
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session, joinedload

from .. import models, schemas, auth, pdf_generator
from ..database import get_db
from ..audit import audit_from_request
from ..ledger import (
    post_payment,
    reverse_payment_entry,
    validate_payment_amount,
    check_duplicate_reference,
    compute_balance,
    quantize,
)

router = APIRouter(
    prefix="/api/payments",
    tags=["Payments"],
    dependencies=[Depends(auth.require_admin)],
)


def _overpayment_policy() -> str:
    """
    'credit' (default): excess payment is recorded as a credit balance,
        automatically applied against the learner's future invoices.
    'reject': payments that exceed the current outstanding balance are
        rejected outright with a 422, requiring the exact amount (or less).
    Configurable via the OVERPAYMENT_POLICY environment variable.
    """
    return os.environ.get("OVERPAYMENT_POLICY", "credit").strip().lower()


def _to_out(payment: models.Payment) -> schemas.PaymentOut:
    data = schemas.PaymentOut.model_validate(payment).model_dump()
    data["learner_name"] = payment.learner.full_name if payment.learner else None
    data["learner_code"] = payment.learner.learner_code if payment.learner else None
    return schemas.PaymentOut(**data)


@router.get("", response_model=list[schemas.PaymentOut])
def list_payments(learner_id: int | None = None, db: Session = Depends(get_db)):
    query = (
        db.query(models.Payment)
        .options(joinedload(models.Payment.learner))
        .filter(models.Payment.is_active == True)
    )
    if learner_id:
        query = query.filter(models.Payment.learner_id == learner_id)
    payments = query.order_by(models.Payment.payment_date.desc(), models.Payment.id.desc()).all()
    return [_to_out(p) for p in payments]


@router.post("", response_model=schemas.PaymentOut, status_code=201)
def create_payment(payload: schemas.PaymentCreate, request: Request, db: Session = Depends(get_db)):
    # Validate learner exists and is active
    learner = db.query(models.Learner).filter(
        models.Learner.id == payload.learner_id,
        models.Learner.is_active == True,
    ).first()
    if not learner:
        raise HTTPException(status_code=404, detail="Learner not found")

    # Validate amount > 0
    try:
        amount = validate_payment_amount(payload.amount_paid)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    # Check for duplicate reference number
    if payload.reference_number and check_duplicate_reference(db, payload.reference_number):
        raise HTTPException(
            status_code=409,
            detail=f"A payment with reference '{payload.reference_number}' already exists. "
                   "Check for duplicate posting."
        )

    # Configurable overpayment handling
    if _overpayment_policy() == "reject":
        current_balance = compute_balance(learner.id, db)
        if amount > current_balance:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Payment of N$ {amount:,.2f} exceeds the outstanding balance of "
                    f"N$ {current_balance:,.2f}. This installation is configured to reject "
                    "overpayments (OVERPAYMENT_POLICY=reject). Either reduce the payment "
                    "amount or switch the policy to 'credit' to allow prepayments."
                ),
            )

    payment_date = payload.date_paid  # use the accounting date, not created_at
    username = request.session.get("username", "system")

    payment = models.Payment(
        learner_id=payload.learner_id,
        amount_paid=amount,
        payment_date=payment_date,
        date_paid=payment_date,
        payment_method=payload.payment_method,
        reference_number=payload.reference_number,
        notes=payload.notes,
        created_by=username,
    )
    db.add(payment)
    db.flush()  # get payment.id

    # Post to ledger (also syncs balance cache)
    post_payment(
        db=db,
        learner_id=learner.id,
        payment_id=payment.id,
        amount=amount,
        payment_date=payment_date,
        created_by=username,
    )

    audit_from_request(
        request, db, "CREATE", "Payment", payment.id,
        f"Payment of N$ {amount:,.2f} for {learner.full_name} [Ref: {payload.reference_number}]"
    )
    db.commit()
    db.refresh(payment)
    return _to_out(payment)


@router.get("/{payment_id}", response_model=schemas.PaymentOut)
def get_payment(payment_id: int, db: Session = Depends(get_db)):
    payment = (
        db.query(models.Payment)
        .options(joinedload(models.Payment.learner))
        .filter(models.Payment.id == payment_id)
        .first()
    )
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    return _to_out(payment)


@router.put("/{payment_id}", response_model=schemas.PaymentOut)
def update_payment(payment_id: int, payload: schemas.PaymentUpdate, request: Request, db: Session = Depends(get_db)):
    """
    Update a payment.  Only non-financial fields (method, notes, reference)
    can be edited after posting.  To change the amount, reverse and re-post.
    """
    payment = db.query(models.Payment).filter(
        models.Payment.id == payment_id,
        models.Payment.is_active == True,
    ).first()
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")

    # Check for duplicate ref (excluding self)
    if payload.reference_number and check_duplicate_reference(db, payload.reference_number, exclude_id=payment_id):
        raise HTTPException(
            status_code=409,
            detail=f"Reference '{payload.reference_number}' is already used by another payment."
        )

    # Only allow non-amount edits via PUT; amount changes require reversal
    if quantize(payload.amount_paid) != quantize(payment.amount_paid):
        raise HTTPException(
            status_code=422,
            detail="Cannot change payment amount after posting. Use the reversal endpoint instead."
        )

    payment.date_paid = payload.date_paid
    payment.payment_date = payload.date_paid
    payment.payment_method = payload.payment_method
    payment.reference_number = payload.reference_number
    payment.notes = payload.notes

    audit_from_request(request, db, "UPDATE", "Payment", payment_id,
                       f"Updated payment {payment_id} meta-data")
    db.commit()
    db.refresh(payment)
    return _to_out(payment)


@router.post("/{payment_id}/reverse", response_model=schemas.PaymentOut)
def reverse_payment(
    payment_id: int,
    request: Request,
    db: Session = Depends(get_db),
    reason: str = "Manual reversal",
):
    """
    Reverse a payment: marks it inactive, voids its ledger entry,
    and restores the learner's balance.
    """
    payment = db.query(models.Payment).filter(
        models.Payment.id == payment_id,
        models.Payment.is_active == True,
    ).first()
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found or already reversed")

    username = request.session.get("username", "system")
    now = datetime.utcnow()

    # Void the ledger entry (re-adds the amount to what's owed)
    reverse_payment_entry(db, payment_id, reversed_by=username, reversal_reason=reason)

    # Soft-delete the payment record
    payment.is_active = False
    payment.reversed_at = now
    payment.reversed_by = username
    payment.reversal_reason = reason

    audit_from_request(
        request, db, "REVERSE", "Payment", payment_id,
        f"Reversed payment N$ {payment.amount_paid} for learner_id={payment.learner_id}. Reason: {reason}"
    )
    db.commit()
    db.refresh(payment)
    return _to_out(payment)


# Legacy DELETE endpoint → now redirects to soft reverse
@router.delete("/{payment_id}", status_code=204)
def delete_payment(payment_id: int, request: Request, db: Session = Depends(get_db)):
    """Soft-deletes (reverses) the payment. Hard delete is not permitted."""
    payment = db.query(models.Payment).filter(
        models.Payment.id == payment_id,
        models.Payment.is_active == True,
    ).first()
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")

    username = request.session.get("username", "system")
    reverse_payment_entry(db, payment_id, reversed_by=username, reversal_reason="Deleted via UI")
    payment.is_active = False
    payment.reversed_at = datetime.utcnow()
    payment.reversed_by = username

    audit_from_request(request, db, "DELETE", "Payment", payment_id,
                       f"Soft-deleted payment {payment_id}")
    db.commit()
    return None


@router.get("/{payment_id}/receipt")
def download_receipt(payment_id: int, db: Session = Depends(get_db)):
    payment = (
        db.query(models.Payment)
        .options(joinedload(models.Payment.learner))
        .filter(models.Payment.id == payment_id)
        .first()
    )
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")

    pdf_bytes = pdf_generator.build_receipt_pdf(payment, payment.learner)
    filename = f"Receipt_{payment.learner.learner_code}_{payment.id:06d}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )
