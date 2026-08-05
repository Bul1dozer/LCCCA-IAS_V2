import os
from datetime import datetime
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session, joinedload
from .. import models, schemas, auth
from ..database import get_db
from ..audit import audit_from_request
from ..ledger import post_payment, reverse_payment_entry, validate_payment_amount, check_duplicate_reference, compute_balance, quantize
from ..pdf_generator import build_receipt_pdf

router = APIRouter(prefix="/api/payments", tags=["Payments"],
                   dependencies=[Depends(auth.require_admin)])


def _overpayment_policy():
    return os.environ.get("OVERPAYMENT_POLICY", "credit").strip().lower()


def _to_out(p: models.Payment) -> schemas.PaymentOut:
    data = schemas.PaymentOut.model_validate(p).model_dump()
    data["learner_name"] = p.learner.full_name if p.learner else None
    data["learner_code"] = p.learner.learner_code if p.learner else None
    return schemas.PaymentOut(**data)


@router.get("", response_model=list[schemas.PaymentOut])
def list_payments(learner_id: int | None = None, db: Session = Depends(get_db)):
    q = db.query(models.Payment).options(joinedload(models.Payment.learner)).filter(
        models.Payment.is_active == True
    )
    if learner_id:
        q = q.filter(models.Payment.learner_id == learner_id)
    return [_to_out(p) for p in q.order_by(models.Payment.payment_date.desc()).all()]


@router.post("", response_model=schemas.PaymentOut, status_code=201)
def create_payment(payload: schemas.PaymentCreate, request: Request, db: Session = Depends(get_db)):
    learner = db.query(models.Learner).filter(
        models.Learner.id == payload.learner_id, models.Learner.is_active == True
    ).first()
    if not learner:
        raise HTTPException(404, "Learner not found")

    try:
        amount = validate_payment_amount(payload.amount_paid)
    except ValueError as e:
        raise HTTPException(422, str(e))

    if payload.reference_number and check_duplicate_reference(db, payload.reference_number):
        raise HTTPException(409, f"Duplicate payment reference: '{payload.reference_number}'")

    if _overpayment_policy() == "reject":
        bal = compute_balance(learner.id, db)
        if amount > bal:
            raise HTTPException(422,
                f"Payment N$ {amount:,.2f} exceeds outstanding balance N$ {bal:,.2f}. "
                "OVERPAYMENT_POLICY=reject is active.")

    username = request.session.get("username", "system")
    p = models.Payment(
        learner_id=payload.learner_id, amount_paid=amount,
        payment_date=payload.date_paid, date_paid=payload.date_paid,
        payment_method=payload.payment_method,
        reference_number=payload.reference_number, notes=payload.notes,
        created_by=username,
    )
    db.add(p)
    db.flush()
    post_payment(db, learner.id, p.id, amount, payload.date_paid, username)
    audit_from_request(request, db, "CREATE", "Payment", p.id,
                       f"Payment N$ {amount:,.2f} for {learner.full_name}")
    db.commit()
    db.refresh(p)
    return _to_out(p)


@router.get("/{payment_id}", response_model=schemas.PaymentOut)
def get_payment(payment_id: int, db: Session = Depends(get_db)):
    p = db.query(models.Payment).options(joinedload(models.Payment.learner)).filter(
        models.Payment.id == payment_id
    ).first()
    if not p:
        raise HTTPException(404, "Payment not found")
    return _to_out(p)


@router.get("/{payment_id}/receipt")
def payment_receipt(payment_id: int, db: Session = Depends(get_db)):
    p = db.query(models.Payment).options(joinedload(models.Payment.learner)).filter(
        models.Payment.id == payment_id
    ).first()
    if not p:
        raise HTTPException(404, "Payment not found")
    pdf = build_receipt_pdf(p, p.learner)
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="Receipt_{payment_id:06d}.pdf"'},
    )


@router.post("/{payment_id}/reverse", response_model=schemas.PaymentOut)
def reverse_payment(payment_id: int, request: Request, db: Session = Depends(get_db),
                    reason: str = "Manual reversal"):
    p = db.query(models.Payment).options(joinedload(models.Payment.learner)).filter(
        models.Payment.id == payment_id, models.Payment.is_active == True
    ).first()
    if not p:
        raise HTTPException(404, "Payment not found or already reversed")
    username = request.session.get("username", "system")
    reverse_payment_entry(db, payment_id, reversed_by=username, reversal_reason=reason)
    p.is_active = False
    p.reversed_at = datetime.utcnow()
    p.reversed_by = username
    p.reversal_reason = reason
    audit_from_request(request, db, "REVERSE", "Payment", payment_id,
                       f"Reversed N$ {p.amount_paid} — {reason}")
    db.commit()
    db.refresh(p)
    return _to_out(p)


@router.delete("/{payment_id}", status_code=204)
def delete_payment(payment_id: int, request: Request, db: Session = Depends(get_db)):
    p = db.query(models.Payment).filter(
        models.Payment.id == payment_id, models.Payment.is_active == True
    ).first()
    if not p:
        raise HTTPException(404, "Payment not found")
    username = request.session.get("username", "system")
    reverse_payment_entry(db, payment_id, reversed_by=username)
    p.is_active = False
    p.reversed_at = datetime.utcnow()
    p.reversed_by = username
    audit_from_request(request, db, "DELETE", "Payment", payment_id, "Soft-deleted")
    db.commit()
    return None
