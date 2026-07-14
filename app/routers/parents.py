"""
Parents router — V2 features:
  - Employer details (employer_name, employer_address, employer_phone, occupation)
  - POST /{parent_id}/generate-invoice: single invoice covering ALL linked learners
  - GET  /{parent_id}/invoices: all invoices billed to this parent
"""
from datetime import datetime, date
from decimal import Decimal
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session
from sqlalchemy import or_

from .. import models, schemas, auth
from ..database import get_db
from ..audit import audit_from_request

router = APIRouter(prefix="/api/parents", tags=["Parents"],
                   dependencies=[Depends(auth.require_admin)])


def _to_out(parent: models.Parent) -> schemas.ParentOut:
    return schemas.ParentOut.model_validate(parent)


def _to_detail(parent: models.Parent) -> schemas.ParentDetail:
    learners = [
        schemas.LearnerMini(
            id=rel.learner.id, learner_code=rel.learner.learner_code,
            full_name=rel.learner.full_name, grade=rel.learner.grade,
            class_name=rel.learner.class_name,
            relationship_type=rel.relationship_type, relationship_id=rel.id,
        )
        for rel in parent.relationships_
    ]
    data = schemas.ParentOut.model_validate(parent).model_dump()
    data["learners"] = learners
    return schemas.ParentDetail(**data)


@router.get("", response_model=list[schemas.ParentOut])
def list_parents(
    search: Optional[str] = Query(None),
    include_inactive: bool = Query(False),
    db: Session = Depends(get_db),
):
    q = db.query(models.Parent)
    if not include_inactive:
        q = q.filter(models.Parent.is_active == True)
    if search:
        like = f"%{search}%"
        q = q.filter(or_(
            models.Parent.full_name.ilike(like),
            models.Parent.email.ilike(like),
            models.Parent.phone.ilike(like),
        ))
    return q.order_by(models.Parent.full_name).all()


@router.post("", response_model=schemas.ParentOut, status_code=201)
def create_parent(payload: schemas.ParentCreate, request: Request, db: Session = Depends(get_db)):
    parent = models.Parent(**payload.model_dump())
    db.add(parent)
    db.flush()
    audit_from_request(request, db, "CREATE", "Parent", parent.id, f"Created: {parent.full_name}")
    db.commit()
    db.refresh(parent)
    return _to_out(parent)


@router.get("/{parent_id}", response_model=schemas.ParentDetail)
def get_parent(parent_id: int, db: Session = Depends(get_db)):
    parent = db.query(models.Parent).filter(models.Parent.id == parent_id).first()
    if not parent:
        raise HTTPException(404, "Parent not found")
    return _to_detail(parent)


@router.put("/{parent_id}", response_model=schemas.ParentOut)
def update_parent(parent_id: int, payload: schemas.ParentUpdate,
                  request: Request, db: Session = Depends(get_db)):
    parent = db.query(models.Parent).filter(
        models.Parent.id == parent_id, models.Parent.is_active == True
    ).first()
    if not parent:
        raise HTTPException(404, "Parent not found")
    for field, value in payload.model_dump(exclude_unset=False).items():
        if hasattr(parent, field):
            setattr(parent, field, value)
    audit_from_request(request, db, "UPDATE", "Parent", parent_id, f"Updated: {parent.full_name}")
    db.commit()
    db.refresh(parent)
    return _to_out(parent)


@router.delete("/{parent_id}", status_code=204)
def delete_parent(parent_id: int, request: Request, db: Session = Depends(get_db)):
    parent = db.query(models.Parent).filter(
        models.Parent.id == parent_id, models.Parent.is_active == True
    ).first()
    if not parent:
        raise HTTPException(404, "Parent not found")
    username = request.session.get("username", "system")
    parent.is_active = False
    parent.deleted_at = datetime.utcnow()
    parent.deleted_by = username
    audit_from_request(request, db, "DELETE", "Parent", parent_id, f"Soft-deleted: {parent.full_name}")
    db.commit()
    return None


@router.post("/{parent_id}/link-learner", response_model=schemas.ParentDetail, status_code=201)
def link_learner(parent_id: int, payload: schemas.LinkLearnerRequest,
                 request: Request, db: Session = Depends(get_db)):
    parent = db.query(models.Parent).filter(
        models.Parent.id == parent_id, models.Parent.is_active == True
    ).first()
    if not parent:
        raise HTTPException(404, "Parent not found")
    learner = db.query(models.Learner).filter(
        models.Learner.id == payload.learner_id, models.Learner.is_active == True
    ).first()
    if not learner:
        raise HTTPException(404, "Learner not found")
    existing = db.query(models.LearnerParentRelationship).filter(
        models.LearnerParentRelationship.parent_id == parent_id,
        models.LearnerParentRelationship.learner_id == payload.learner_id,
    ).first()
    if existing:
        raise HTTPException(400, "Learner already linked to this parent")
    db.add(models.LearnerParentRelationship(
        parent_id=parent_id, learner_id=payload.learner_id,
        relationship_type=payload.relationship_type,
    ))
    audit_from_request(request, db, "CREATE", "LearnerParentRelationship", None,
                       f"Linked {learner.learner_code} to parent {parent.full_name}")
    db.commit()
    db.refresh(parent)
    return _to_detail(parent)


@router.delete("/relationships/{relationship_id}", status_code=204)
def unlink_learner(relationship_id: int, request: Request, db: Session = Depends(get_db)):
    rel = db.query(models.LearnerParentRelationship).filter(
        models.LearnerParentRelationship.id == relationship_id
    ).first()
    if not rel:
        raise HTTPException(404, "Relationship not found")
    audit_from_request(request, db, "DELETE", "LearnerParentRelationship", relationship_id,
                       f"Unlinked relationship {relationship_id}")
    db.delete(rel)
    db.commit()
    return None


# ---------------------------------------------------------------------------
# Parent-centric multi-learner invoice generation — V2 KEY FEATURE
# ---------------------------------------------------------------------------

@router.post("/{parent_id}/generate-invoice", status_code=201)
def generate_parent_invoice(
    parent_id: int,
    payload: schemas.GenerateParentInvoiceRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Generate ONE invoice for a parent covering ALL their active linked learners.

    How it works:
    1. Fetch all active learners linked to this parent.
    2. For each learner, get their active monthly fee lines.
    3. Build one Invoice (parent_id set, learner_id=NULL).
    4. Each InvoiceItem has a learner_id so the PDF/UI shows which child it's for.
    5. Post individual ledger DR entries per learner (so per-learner balances stay correct).
    6. Invoice total = sum of all children's fees combined.

    If a learner has already been invoiced for this billing_period (non-voided),
    their fees are skipped (idempotency at the learner level).
    """
    from ..ledger import next_invoice_number, post_invoice, compute_balance, quantize
    from ..fee_engine import get_billing_lines

    parent = db.query(models.Parent).filter(
        models.Parent.id == parent_id, models.Parent.is_active == True
    ).first()
    if not parent:
        raise HTTPException(404, "Parent not found")

    active_rels = [
        rel for rel in parent.relationships_
        if rel.learner and rel.learner.is_active and rel.learner.status == "Active"
    ]
    if not active_rels:
        raise HTTPException(400, "No active learners linked to this parent.")

    billing_period = payload.billing_period or payload.due_date.strftime("%Y-%m")
    username = request.session.get("username", "system")

    # Collect billable items across all learners
    all_items = []       # (learner, description, amount, fee_item_id)
    learner_totals = {}  # learner_id -> (learner, Decimal total)
    total_prev_balance = Decimal("0.00")

    for rel in active_rels:
        learner = rel.learner

        # Idempotency: skip this learner if already invoiced for this period
        already = db.query(models.Invoice).filter(
            models.Invoice.learner_id == learner.id,
            models.Invoice.billing_period == billing_period,
            models.Invoice.status.notin_(["Void", "Reversed"]),
        ).first()
        # Also check parent invoices that cover this learner
        already_parent = db.query(models.InvoiceItem).join(models.Invoice).filter(
            models.InvoiceItem.learner_id == learner.id,
            models.Invoice.billing_period == billing_period,
            models.Invoice.status.notin_(["Void", "Reversed"]),
        ).first()
        if already or already_parent:
            continue

        lines = get_billing_lines(db, learner, frequency_filter="monthly")
        if not lines:
            continue

        prev_bal = compute_balance(learner.id, db)
        total_prev_balance += prev_bal

        for desc, amount, fee_item_id in lines:
            label = f"[{learner.full_name} — {learner.grade}] {desc}"
            all_items.append((learner, label, amount, fee_item_id))
            if learner.id not in learner_totals:
                learner_totals[learner.id] = (learner, Decimal("0.00"))
            lid, (lobj, ltot) = learner.id, learner_totals[learner.id]
            learner_totals[lid] = (lobj, ltot + amount)

    if not all_items:
        raise HTTPException(400,
            f"No billable monthly fees found for any of this parent's learners for "
            f"period {billing_period}. Either all learners have already been invoiced "
            "for this period or none have active fee items assigned.")

    total_charges = quantize(sum(amt for _, _, amt, _ in all_items))
    total_prev_balance = quantize(total_prev_balance)
    outstanding = quantize(total_prev_balance + total_charges)

    inv_number = next_invoice_number(db)
    invoice = models.Invoice(
        invoice_number=inv_number,
        parent_id=parent_id,
        learner_id=None,  # multi-learner invoice: no single learner
        issue_date=date.today(),
        due_date=payload.due_date,
        previous_balance=total_prev_balance,
        current_charges=total_charges,
        payments_made=Decimal("0.00"),
        outstanding_balance=outstanding,
        status="Generated",
        billing_period=billing_period,
        created_by=username,
    )
    db.add(invoice)
    db.flush()

    # Add line items, each tagged with which learner it belongs to
    for learner, desc, amount, fee_item_id in all_items:
        db.add(models.InvoiceItem(
            invoice_id=invoice.id,
            learner_id=learner.id,
            description=desc,
            amount=amount,
            fee_item_id=fee_item_id,
        ))

    # Post individual DR ledger entries per learner
    for lid, (lobj, lamt) in learner_totals.items():
        post_invoice(
            db=db, learner_id=lid, invoice_id=invoice.id,
            amount=quantize(lamt), transaction_date=invoice.issue_date,
            created_by=username,
            notes=f"Parent invoice {inv_number} — {lobj.full_name}",
        )

    audit_from_request(request, db, "GENERATE", "Invoice", invoice.id,
        f"Parent invoice {inv_number} for {parent.full_name}: "
        f"{len(learner_totals)} learner(s), N$ {total_charges:,.2f}")
    db.commit()
    db.refresh(invoice)

    return {
        "id": invoice.id,
        "invoice_number": invoice.invoice_number,
        "parent_id": parent_id,
        "parent_name": parent.full_name,
        "issue_date": str(invoice.issue_date),
        "due_date": str(invoice.due_date),
        "billing_period": invoice.billing_period,
        "previous_balance": str(invoice.previous_balance),
        "current_charges": str(invoice.current_charges),
        "outstanding_balance": str(invoice.outstanding_balance),
        "status": invoice.status,
        "learner_count": len(learner_totals),
        "items": [
            {"description": item.description, "amount": str(item.amount),
             "learner_id": item.learner_id}
            for item in invoice.items
        ],
    }


@router.get("/{parent_id}/invoices")
def get_parent_invoices(parent_id: int, db: Session = Depends(get_db)):
    """All invoices billed to this parent (parent-centric invoices only)."""
    parent = db.query(models.Parent).filter(models.Parent.id == parent_id).first()
    if not parent:
        raise HTTPException(404, "Parent not found")
    invoices = db.query(models.Invoice).filter(
        models.Invoice.parent_id == parent_id,
    ).order_by(models.Invoice.created_at.desc()).all()
    return [
        {
            "id": inv.id,
            "invoice_number": inv.invoice_number,
            "issue_date": str(inv.issue_date),
            "due_date": str(inv.due_date),
            "current_charges": str(inv.current_charges),
            "outstanding_balance": str(inv.outstanding_balance),
            "status": inv.status,
            "billing_period": inv.billing_period,
            "learner_count": len(set(item.learner_id for item in inv.items if item.learner_id)),
        }
        for inv in invoices
    ]
