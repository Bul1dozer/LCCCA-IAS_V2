"""
Invoices router — v3 Production.

Changes:
- Hard delete REMOVED. Replaced with Void and Reverse.
- Invoice numbers generated atomically via ledger.next_invoice_number()
- All amounts use Decimal
- Ledger entry posted on invoice creation
- Void voids ledger entry and marks invoice Void
- No balance mutation directly on learner.balance
"""

from datetime import date, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session, joinedload

from .. import models, schemas, auth, pdf_generator
from ..database import get_db
from ..audit import audit_from_request
from ..ledger import (
    next_invoice_number,
    post_invoice,
    void_invoice_entries,
    compute_balance,
    quantize,
)
from ..fee_engine import (
    generate_invoice_for_learner,
    get_active_fee_items_for_learner,
    get_billing_lines,
)

router = APIRouter(
    prefix="/api/invoices",
    tags=["Invoices"],
    dependencies=[Depends(auth.require_admin)],
)


def _to_out(invoice: models.Invoice) -> schemas.InvoiceOut:
    data = schemas.InvoiceOut.model_validate(invoice).model_dump()
    data["learner_name"] = invoice.learner.full_name if invoice.learner else None
    data["learner_code"] = invoice.learner.learner_code if invoice.learner else None
    data["parent_name"] = invoice.parent.full_name if invoice.parent else None
    data["items"] = [schemas.InvoiceItemOut.model_validate(i) for i in invoice.items]
    return schemas.InvoiceOut(**data)


@router.get("", response_model=list[schemas.InvoiceOut])
def list_invoices(learner_id: int | None = None, db: Session = Depends(get_db)):
    q = db.query(models.Invoice).options(
        joinedload(models.Invoice.learner), joinedload(models.Invoice.items)
    )
    if learner_id:
        q = q.filter(models.Invoice.learner_id == learner_id)
    return [_to_out(i) for i in q.order_by(models.Invoice.created_at.desc()).all()]


@router.post("/generate", response_model=schemas.InvoiceOut, status_code=201)
def generate_invoice(payload: schemas.GenerateInvoiceRequest, request: Request, db: Session = Depends(get_db)):
    """Generate either a learner invoice or a parent invoice aggregating linked learners."""
    username = request.session.get("username", "system")

    if payload.parent_id:
        parent = db.query(models.Parent).filter(models.Parent.id == payload.parent_id, models.Parent.is_active == True).first()
        if not parent:
            raise HTTPException(404, "Parent not found")
        learners = [rel.learner for rel in parent.relationships_ if rel.learner and rel.learner.is_active]
        if not learners:
            raise HTTPException(400, "This parent has no linked learners.")
        lines = []
        for learner in learners:
            learner_lines = get_billing_lines(db, learner, frequency_filter="monthly")
            for desc, amount, fee_item_id in learner_lines:
                lines.append((f"{learner.full_name} — {desc}", amount, fee_item_id))
        if not lines:
            raise HTTPException(400, "No active billable fee items found for the linked learners.")

        current_charges = quantize(sum((amount for _, amount, _ in lines), Decimal("0.00")))
        inv_number = next_invoice_number(db)
        invoice = models.Invoice(
            invoice_number=inv_number,
            parent_id=parent.id,
            learner_id=None,
            issue_date=date.today(),
            due_date=payload.due_date,
            previous_balance=Decimal("0.00"),
            current_charges=current_charges,
            payments_made=Decimal("0.00"),
            outstanding_balance=current_charges,
            status="Generated",
            created_by=username,
        )
        db.add(invoice)
        db.flush()
        for desc, amount, _ in lines:
            db.add(models.InvoiceItem(invoice_id=invoice.id, description=desc, amount=amount))
        for learner in learners:
            post_invoice(db=db, learner_id=learner.id, invoice_id=invoice.id, amount=current_charges/len(learners), transaction_date=invoice.issue_date, created_by=username, notes=f"Parent invoice {inv_number}")
        audit_from_request(request, db, "GENERATE", "Invoice", invoice.id,
                           f"Generated parent invoice {invoice.invoice_number} for {parent.full_name}")
        db.commit()
        db.refresh(invoice)
        return _to_out(invoice)

    learner = db.query(models.Learner).filter(
        models.Learner.id == payload.learner_id,
        models.Learner.is_active == True,
    ).first()
    if not learner:
        raise HTTPException(404, "Learner not found")

    username = request.session.get("username", "system")
    has_profile = bool(get_active_fee_items_for_learner(db, learner))

    if has_profile:
        billing_period = payload.due_date.strftime("%Y-%m")
        invoice = generate_invoice_for_learner(
            db=db, learner=learner, due_date=payload.due_date,
            billing_period=billing_period, frequency="monthly",
            triggered_by=username,
        )
        if not invoice:
            raise HTTPException(400, "No active billable fee items found for this learner's profile.")
    else:
        # Fallback to v1 fee_structure
        if not payload.fee_structure_id:
            raise HTTPException(
                400,
                "This learner has no fee profile assigned. Provide a fee_structure_id or assign fee items first."
            )
        fee_structure = db.query(models.FeeStructure).filter(
            models.FeeStructure.id == payload.fee_structure_id
        ).first()
        if not fee_structure:
            raise HTTPException(404, "Fee structure not found")

        previous_balance = compute_balance(learner.id, db)
        current_charges = quantize(fee_structure.total)
        payments_made = Decimal("0.00")  # v1 path: simplified
        outstanding_balance = quantize(previous_balance + current_charges)

        inv_number = next_invoice_number(db)
        invoice = models.Invoice(
            invoice_number=inv_number,
            learner_id=learner.id,
            fee_structure_id=fee_structure.id,
            issue_date=date.today(),
            due_date=payload.due_date,
            previous_balance=previous_balance,
            current_charges=current_charges,
            payments_made=payments_made,
            outstanding_balance=outstanding_balance,
            status="Generated",
            created_by=username,
        )
        db.add(invoice)
        db.flush()

        for desc, amount in [
            ("Tuition Fee", fee_structure.tuition_fee),
            ("Development Fee", fee_structure.development_fee),
            ("Hostel Fee", fee_structure.hostel_fee),
            ("Transport Fee", fee_structure.transport_fee),
            ("Miscellaneous Charges", fee_structure.misc_charges),
        ]:
            a = quantize(amount) if amount else Decimal("0.00")
            if a > 0:
                db.add(models.InvoiceItem(
                    invoice_id=invoice.id,
                    description=f"{desc} ({fee_structure.name})",
                    amount=a,
                ))

        # Post to ledger
        post_invoice(
            db=db,
            learner_id=learner.id,
            invoice_id=invoice.id,
            amount=current_charges,
            transaction_date=invoice.issue_date,
            created_by=username,
        )

    audit_from_request(request, db, "GENERATE", "Invoice", invoice.id,
                       f"Generated invoice {invoice.invoice_number} for {learner.full_name if learner else parent.full_name}")
    db.commit()
    db.refresh(invoice)
    return _to_out(invoice)


@router.get("/{invoice_id}", response_model=schemas.InvoiceOut)
def get_invoice(invoice_id: int, db: Session = Depends(get_db)):
    invoice = db.query(models.Invoice).options(
        joinedload(models.Invoice.learner), joinedload(models.Invoice.items)
    ).filter(models.Invoice.id == invoice_id).first()
    if not invoice:
        raise HTTPException(404, "Invoice not found")
    return _to_out(invoice)


@router.post("/{invoice_id}/void", response_model=schemas.InvoiceOut)
def void_invoice(
    invoice_id: int,
    request: Request,
    db: Session = Depends(get_db),
    reason: str = "Voided by administrator",
):
    """
    Void an invoice: marks it Void, voids its ledger DR entry,
    and restores the learner's balance.  No hard delete.
    """
    invoice = db.query(models.Invoice).options(
        joinedload(models.Invoice.learner), joinedload(models.Invoice.items)
    ).filter(models.Invoice.id == invoice_id).first()
    if not invoice:
        raise HTTPException(404, "Invoice not found")
    if invoice.status in ("Void", "Reversed"):
        raise HTTPException(409, f"Invoice is already {invoice.status}")

    username = request.session.get("username", "system")
    now = datetime.utcnow()

    # Void ledger entry (re-credits the DR, restoring balance)
    void_invoice_entries(db, invoice_id, voided_by=username, void_reason=reason)

    invoice.status = "Void"
    invoice.voided_at = now
    invoice.voided_by = username
    invoice.void_reason = reason

    audit_from_request(request, db, "VOID", "Invoice", invoice_id,
                       f"Voided invoice {invoice.invoice_number}. Reason: {reason}")
    db.commit()
    db.refresh(invoice)
    return _to_out(invoice)


# Legacy DELETE endpoint — now voids instead of hard-deleting
@router.delete("/{invoice_id}", status_code=204)
def delete_invoice(invoice_id: int, request: Request, db: Session = Depends(get_db)):
    """Preserved for backward compatibility. Now voids the invoice."""
    invoice = db.query(models.Invoice).filter(models.Invoice.id == invoice_id).first()
    if not invoice:
        raise HTTPException(404, "Invoice not found")
    if invoice.status in ("Void", "Reversed"):
        return None  # idempotent

    username = request.session.get("username", "system")
    void_invoice_entries(db, invoice_id, voided_by=username, void_reason="Deleted via UI")
    invoice.status = "Void"
    invoice.voided_at = datetime.utcnow()
    invoice.voided_by = username

    audit_from_request(request, db, "DELETE", "Invoice", invoice_id,
                       f"Soft-deleted (voided) invoice {invoice.invoice_number}")
    db.commit()
    return None


def _build_pdf(invoice: models.Invoice, db: Session) -> bytes:
    learner = invoice.learner
    parents = [rel.parent for rel in learner.relationships_]
    return pdf_generator.build_invoice_pdf(invoice, learner, parents, invoice.items)


@router.get("/{invoice_id}/pdf")
def download_invoice_pdf(invoice_id: int, request: Request, db: Session = Depends(get_db)):
    invoice = db.query(models.Invoice).options(
        joinedload(models.Invoice.learner).joinedload(models.Learner.relationships_),
        joinedload(models.Invoice.items),
    ).filter(models.Invoice.id == invoice_id).first()
    if not invoice:
        raise HTTPException(404, "Invoice not found")
    audit_from_request(request, db, "EXPORT", "Invoice", invoice_id,
                       f"Downloaded PDF: {invoice.invoice_number}")
    db.commit()
    pdf_bytes = _build_pdf(invoice, db)
    return Response(content=pdf_bytes, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{invoice.invoice_number}.pdf"'})


@router.get("/{invoice_id}/preview")
def preview_invoice_pdf(invoice_id: int, db: Session = Depends(get_db)):
    invoice = db.query(models.Invoice).options(
        joinedload(models.Invoice.learner).joinedload(models.Learner.relationships_),
        joinedload(models.Invoice.items),
    ).filter(models.Invoice.id == invoice_id).first()
    if not invoice:
        raise HTTPException(404, "Invoice not found")
    pdf_bytes = _build_pdf(invoice, db)
    return Response(content=pdf_bytes, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{invoice.invoice_number}.pdf"'})


@router.post("/{invoice_id}/send", response_model=schemas.EmailLogOut, status_code=201)
async def send_invoice(invoice_id: int, payload: schemas.SendEmailRequest,
                       request: Request, db: Session = Depends(get_db)):
    from ..email_engine import send_invoice_email
    invoice = db.query(models.Invoice).options(
        joinedload(models.Invoice.learner).joinedload(models.Learner.relationships_),
        joinedload(models.Invoice.items),
    ).filter(models.Invoice.id == invoice_id).first()
    if not invoice:
        raise HTTPException(404, "Invoice not found")
    learner = invoice.learner
    parents = [rel.parent for rel in learner.relationships_]
    pdf_bytes = _build_pdf(invoice, db)
    result = await send_invoice_email(db, invoice, learner, parents, pdf_bytes,
                                      recipient_override=payload.recipient_email)
    audit_from_request(request, db, "SEND", "Invoice", invoice_id,
                       f"Invoice {invoice.invoice_number} sent [{result['status']}] to {result['recipient']}")
    db.commit()
    log = db.query(models.EmailLog).filter(
        models.EmailLog.invoice_id == invoice_id
    ).order_by(models.EmailLog.sent_at.desc()).first()
    data = schemas.EmailLogOut.model_validate(log).model_dump()
    data["invoice_number"] = invoice.invoice_number
    data["learner_name"] = learner.full_name
    return schemas.EmailLogOut(**data)
