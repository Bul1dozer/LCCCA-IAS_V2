from datetime import date, datetime
from decimal import Decimal
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload
from .. import models, schemas, auth
from ..database import get_db
from ..audit import audit_from_request
from ..ledger import next_invoice_number, post_invoice, void_invoice_entries, compute_balance, quantize
from ..fee_engine import generate_invoice_for_learner, get_active_fee_items_for_learner
from ..pdf_generator import build_invoice_pdf
from ..email_engine import send_invoice_email

router = APIRouter(prefix="/api/invoices", tags=["Invoices"],
                   dependencies=[Depends(auth.require_admin)])


def _enrich_item(item: models.InvoiceItem, invoice: models.Invoice) -> schemas.InvoiceItemOut:
    learner_name = None
    if item.learner_id:
        if invoice.parent and invoice.parent.relationships_:
            for rel in invoice.parent.relationships_:
                if rel.learner and rel.learner.id == item.learner_id:
                    learner_name = rel.learner.full_name
                    break
        if not learner_name and invoice.learner and invoice.learner.id == item.learner_id:
            learner_name = invoice.learner.full_name
    elif invoice.learner:
        learner_name = invoice.learner.full_name
    return schemas.InvoiceItemOut(
        id=item.id, description=item.description,
        amount=item.amount, learner_id=item.learner_id,
        learner_name=learner_name,
    )


def _primary_parent_for_legacy_invoice(invoice: models.Invoice) -> models.Parent | None:
    if invoice.parent:
        return invoice.parent
    if not invoice.learner:
        return None
    relationships = [rel for rel in invoice.learner.relationships_ if rel.parent]
    primary = next((rel for rel in relationships if rel.is_primary), None)
    return (primary or relationships[0]).parent if relationships else None


def _linked_learner_names(invoice: models.Invoice) -> list[str]:
    names_by_id = {}
    if invoice.parent and invoice.parent.relationships_:
        for rel in invoice.parent.relationships_:
            if rel.learner:
                names_by_id[rel.learner.id] = rel.learner.full_name
    if invoice.learner:
        names_by_id[invoice.learner.id] = invoice.learner.full_name

    linked = []
    seen = set()
    for item in invoice.items:
        if item.learner_id and item.learner_id not in seen:
            linked.append(names_by_id.get(item.learner_id, f"Learner #{item.learner_id}"))
            seen.add(item.learner_id)

    if not linked and invoice.learner:
        linked.append(invoice.learner.full_name)
    return linked


def _parent_contacts_for_invoice(invoice: models.Invoice) -> list[models.Parent]:
    if invoice.parent:
        return [invoice.parent]
    if not invoice.learner:
        return []
    return [rel.parent for rel in invoice.learner.relationships_ if rel.parent]


def _to_out(invoice: models.Invoice) -> schemas.InvoiceOut:
    data = schemas.InvoiceOut.model_validate(invoice).model_dump()
    data["learner_name"] = invoice.learner.full_name if invoice.learner else None
    data["learner_code"] = invoice.learner.learner_code if invoice.learner else None
    parent = _primary_parent_for_legacy_invoice(invoice)
    data["parent_name"] = parent.full_name if parent else None
    data["linked_learner_names"] = _linked_learner_names(invoice)
    data["items"] = [_enrich_item(i, invoice) for i in invoice.items]
    return schemas.InvoiceOut(**data)


def _load_invoice(invoice_id: int, db: Session):
    return db.query(models.Invoice).options(
        joinedload(models.Invoice.learner),
        joinedload(models.Invoice.learner).joinedload(models.Learner.relationships_)
        .joinedload(models.LearnerParentRelationship.parent),
        joinedload(models.Invoice.parent).joinedload(models.Parent.relationships_)
        .joinedload(models.LearnerParentRelationship.learner),
        joinedload(models.Invoice.items),
    ).filter(models.Invoice.id == invoice_id).first()


@router.get("", response_model=list[schemas.InvoiceOut])
def list_invoices(
    learner_id: int | None = None,
    parent_id: int | None = None,
    search: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    q = db.query(models.Invoice).options(
        joinedload(models.Invoice.learner),
        joinedload(models.Invoice.learner).joinedload(models.Learner.relationships_)
        .joinedload(models.LearnerParentRelationship.parent),
        joinedload(models.Invoice.parent),
        joinedload(models.Invoice.parent).joinedload(models.Parent.relationships_)
        .joinedload(models.LearnerParentRelationship.learner),
        joinedload(models.Invoice.items),
    )
    if learner_id:
        q = q.filter(models.Invoice.learner_id == learner_id)
    if parent_id:
        q = q.filter(models.Invoice.parent_id == parent_id)
    if search:
        like = f"%{search}%"
        q = (
            q.outerjoin(models.Invoice.parent)
            .outerjoin(models.Invoice.learner)
            .filter(or_(
                models.Invoice.invoice_number.ilike(like),
                models.Parent.full_name.ilike(like),
                models.Learner.full_name.ilike(like),
            ))
        )
    return [_to_out(i) for i in q.order_by(models.Invoice.created_at.desc()).all()]


@router.post("/generate", response_model=schemas.InvoiceOut, status_code=201)
def generate_invoice(payload: schemas.GenerateInvoiceRequest,
                     request: Request, db: Session = Depends(get_db)):
    """Generate a single-learner invoice (classic flow)."""
    learner = db.query(models.Learner).filter(
        models.Learner.id == payload.learner_id, models.Learner.is_active == True
    ).first()
    if not learner:
        raise HTTPException(404, "Learner not found")

    username = request.session.get("username", "system")
    has_profile = bool(get_active_fee_items_for_learner(db, learner))
    billing_period = payload.billing_period or payload.due_date.strftime("%Y-%m")

    if has_profile:
        invoice = generate_invoice_for_learner(
            db=db, learner=learner, due_date=payload.due_date,
            billing_period=billing_period, triggered_by=username,
        )
        if not invoice:
            raise HTTPException(400, "No active monthly fee items for this learner.")
    else:
        if not payload.fee_structure_id:
            raise HTTPException(400, "No fee profile assigned. Provide fee_structure_id or assign fee items.")
        fs = db.query(models.FeeStructure).filter(models.FeeStructure.id == payload.fee_structure_id).first()
        if not fs:
            raise HTTPException(404, "Fee structure not found")
        prev_bal = compute_balance(learner.id, db)
        charges = quantize(fs.total)
        inv_num = next_invoice_number(db)
        invoice = models.Invoice(
            invoice_number=inv_num, learner_id=learner.id,
            fee_structure_id=fs.id, issue_date=date.today(),
            due_date=payload.due_date, previous_balance=prev_bal,
            current_charges=charges, payments_made=Decimal("0.00"),
            outstanding_balance=quantize(prev_bal + charges),
            status="Generated", billing_period=billing_period, created_by=username,
        )
        db.add(invoice)
        db.flush()
        for desc, amt in [("Tuition Fee", fs.tuition_fee), ("Development Fee", fs.development_fee),
                          ("Transport Fee", fs.transport_fee), ("Miscellaneous", fs.misc_charges)]:
            a = quantize(amt) if amt else Decimal("0.00")
            if a > 0:
                db.add(models.InvoiceItem(
                    invoice_id=invoice.id, learner_id=learner.id,
                    description=f"{desc} ({fs.name})", amount=a,
                ))
        post_invoice(db, learner.id, invoice.id, charges, invoice.issue_date, username)

    audit_from_request(request, db, "GENERATE", "Invoice", invoice.id,
                       f"Generated {invoice.invoice_number} for {learner.full_name}")
    db.commit()
    inv = _load_invoice(invoice.id, db)
    return _to_out(inv)


@router.get("/{invoice_id}", response_model=schemas.InvoiceOut)
def get_invoice(invoice_id: int, db: Session = Depends(get_db)):
    invoice = _load_invoice(invoice_id, db)
    if not invoice:
        raise HTTPException(404, "Invoice not found")
    return _to_out(invoice)


def _invoice_pdf_response(invoice: models.Invoice, disposition: str) -> Response:
    learner = invoice.learner if invoice.learner_id else None
    pdf = build_invoice_pdf(
        invoice,
        learner,
        _parent_contacts_for_invoice(invoice),
        invoice.items,
    )
    filename = f"Invoice_{invoice.invoice_number}.pdf"
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'{disposition}; filename="{filename}"'},
    )


@router.get("/{invoice_id}/preview")
def preview_invoice(invoice_id: int, db: Session = Depends(get_db)):
    invoice = _load_invoice(invoice_id, db)
    if not invoice:
        raise HTTPException(404, "Invoice not found")
    return _invoice_pdf_response(invoice, "inline")


@router.get("/{invoice_id}/pdf")
def download_invoice_pdf(invoice_id: int, db: Session = Depends(get_db)):
    invoice = _load_invoice(invoice_id, db)
    if not invoice:
        raise HTTPException(404, "Invoice not found")
    return _invoice_pdf_response(invoice, "attachment")


@router.post("/{invoice_id}/send")
async def send_invoice(
    invoice_id: int,
    payload: schemas.SendEmailRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    invoice = _load_invoice(invoice_id, db)
    if not invoice:
        raise HTTPException(404, "Invoice not found")

    learner = invoice.learner if invoice.learner_id else None
    parents = _parent_contacts_for_invoice(invoice)
    pdf = build_invoice_pdf(invoice, learner, parents, invoice.items)
    result = await send_invoice_email(
        db,
        invoice,
        learner,
        parents,
        pdf,
        recipient_override=payload.recipient_email,
    )
    audit_from_request(
        request,
        db,
        "SEND",
        "Invoice",
        invoice.id,
        f"Emailed {invoice.invoice_number} to {result['recipient']}",
    )
    db.commit()
    return {
        "invoice_id": invoice.id,
        "invoice_number": invoice.invoice_number,
        "recipient_email": result["recipient"],
        "status": result["status"],
        "real_send": result["real_send"],
        "error": result["error"],
    }


@router.post("/{invoice_id}/void", response_model=schemas.InvoiceOut)
def void_invoice(invoice_id: int, request: Request, db: Session = Depends(get_db),
                 reason: str = "Voided by administrator"):
    invoice = _load_invoice(invoice_id, db)
    if not invoice:
        raise HTTPException(404, "Invoice not found")
    if invoice.status in ("Void", "Reversed"):
        raise HTTPException(409, f"Invoice is already {invoice.status}")

    username = request.session.get("username", "system")

    # Void ledger entries for each learner covered by this invoice
    learner_ids = set()
    if invoice.learner_id:
        learner_ids.add(invoice.learner_id)
    for item in invoice.items:
        if item.learner_id:
            learner_ids.add(item.learner_id)

    for lid in learner_ids:
        # Find and void ledger entries for this invoice+learner
        entries = db.query(models.LedgerEntry).filter(
            models.LedgerEntry.reference_type == "Invoice",
            models.LedgerEntry.reference_id == invoice_id,
            models.LedgerEntry.learner_id == lid,
            models.LedgerEntry.is_voided == False,
        ).all()
        for e in entries:
            e.is_voided = True
            e.voided_at = datetime.utcnow()
            e.voided_by = username
        if entries:
            db.flush()
            from ..ledger import sync_balance_cache
            sync_balance_cache(lid, db)

    invoice.status = "Void"
    invoice.voided_at = datetime.utcnow()
    invoice.voided_by = username
    invoice.void_reason = reason

    audit_from_request(request, db, "VOID", "Invoice", invoice_id,
                       f"Voided {invoice.invoice_number}. Reason: {reason}")
    db.commit()
    inv = _load_invoice(invoice_id, db)
    return _to_out(inv)


@router.delete("/{invoice_id}", status_code=204)
def delete_invoice(invoice_id: int, request: Request, db: Session = Depends(get_db)):
    """Preserved for backward compat — soft voids instead of hard deleting."""
    invoice = db.query(models.Invoice).filter(models.Invoice.id == invoice_id).first()
    if not invoice:
        raise HTTPException(404, "Invoice not found")
    if invoice.status in ("Void", "Reversed"):
        return None
    username = request.session.get("username", "system")
    void_invoice_entries(db, invoice_id, voided_by=username)
    invoice.status = "Void"
    invoice.voided_at = datetime.utcnow()
    invoice.voided_by = username
    audit_from_request(request, db, "DELETE", "Invoice", invoice_id,
                       f"Soft-deleted (voided) {invoice.invoice_number}")
    db.commit()
    return None
