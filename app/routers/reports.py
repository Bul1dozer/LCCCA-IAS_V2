"""
Reports router — v3: all balances derived from ledger, not learner.balance cache.
"""
from datetime import date
import calendar
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import text

from .. import models, schemas, auth, pdf_generator
from ..database import get_db
from ..ledger import compute_balance, quantize

router = APIRouter(prefix="/api/reports", tags=["Reports"], dependencies=[Depends(auth.require_admin)])


def _log_report(db: Session, report_type: str, parameters: str = ""):
    db.add(models.ReportLog(report_type=report_type, parameters=parameters))
    db.commit()


@router.get("/outstanding-balances")
def outstanding_balances_report(db: Session = Depends(get_db)):
    """Outstanding balances derived directly from ledger — authoritative."""
    learners = db.query(models.Learner).filter(
        models.Learner.is_active == True,
        models.Learner.status == "Active",
    ).all()

    rows = []
    ledger_total = Decimal("0.00")

    for l in learners:
        balance = compute_balance(l.id, db)
        if balance > 0:
            rows.append({
                "learner_code": l.learner_code,
                "full_name": l.full_name,
                "grade": l.grade,
                "class_name": l.class_name,
                "balance": balance,
            })
            ledger_total += balance

    # Sort by balance descending
    rows.sort(key=lambda r: r["balance"], reverse=True)

    _log_report(db, "Outstanding Balances Report")
    pdf_bytes = pdf_generator.build_outstanding_report_pdf(rows, float(ledger_total))
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": 'inline; filename="Outstanding_Balances_Report.pdf"'},
    )


@router.get("/monthly-collections")
def monthly_collections_report(
    year: int = Query(default_factory=lambda: date.today().year),
    month: int = Query(default_factory=lambda: date.today().month),
    db: Session = Depends(get_db),
):
    if month < 1 or month > 12:
        raise HTTPException(status_code=400, detail="Month must be between 1 and 12")

    start = date(year, month, 1)
    end_day = calendar.monthrange(year, month)[1]
    end = date(year, month, end_day)

    # Use payment_date (accounting date) not created_at
    payments = db.query(models.Payment).options(joinedload(models.Payment.learner)).filter(
        models.Payment.payment_date >= start,
        models.Payment.payment_date <= end,
        models.Payment.is_active == True,
    ).order_by(models.Payment.payment_date).all()

    rows = [
        {
            "date_paid": p.payment_date,
            "learner_name": p.learner.full_name if p.learner else "Unknown",
            "learner_code": p.learner.learner_code if p.learner else "-",
            "payment_method": p.payment_method,
            "reference_number": p.reference_number,
            "amount_paid": float(p.amount_paid),
        }
        for p in payments
    ]
    total = sum(r["amount_paid"] for r in rows)
    period_label = start.strftime("%B %Y")

    _log_report(db, "Monthly Collections Report", f"{year}-{month:02d}")
    pdf_bytes = pdf_generator.build_collections_report_pdf(rows, total, period_label)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="Monthly_Collections_{year}_{month:02d}.pdf"'},
    )


@router.get("/learner-statement/{learner_id}")
def learner_statement_report(learner_id: int, db: Session = Depends(get_db)):
    learner = db.query(models.Learner).filter(
        models.Learner.id == learner_id,
        models.Learner.is_active == True,
    ).first()
    if not learner:
        raise HTTPException(status_code=404, detail="Learner not found")

    invoices = db.query(models.Invoice).filter(
        models.Invoice.learner_id == learner_id,
        models.Invoice.status.notin_(["Void", "Reversed"]),
    ).order_by(models.Invoice.issue_date).all()

    payments = db.query(models.Payment).filter(
        models.Payment.learner_id == learner_id,
        models.Payment.is_active == True,
    ).order_by(models.Payment.payment_date).all()

    _log_report(db, "Learner Account Statement", f"learner_id={learner_id}")
    pdf_bytes = pdf_generator.build_statement_pdf(learner, invoices, payments)
    filename = f"Statement_{learner.learner_code}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


@router.get("/payment-history")
def payment_history_report(db: Session = Depends(get_db)):
    payments = db.query(models.Payment).options(joinedload(models.Payment.learner)).filter(
        models.Payment.is_active == True
    ).order_by(models.Payment.payment_date.desc()).all()

    rows = [
        {
            "date_paid": p.payment_date,
            "learner_name": p.learner.full_name if p.learner else "Unknown",
            "learner_code": p.learner.learner_code if p.learner else "-",
            "payment_method": p.payment_method,
            "reference_number": p.reference_number,
            "amount_paid": float(p.amount_paid),
        }
        for p in payments
    ]
    total = sum(r["amount_paid"] for r in rows)

    _log_report(db, "Payment History Report")
    pdf_bytes = pdf_generator.build_payment_history_pdf(rows, total)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": 'inline; filename="Payment_History_Report.pdf"'},
    )


@router.get("/log")
def report_log(db: Session = Depends(get_db)):
    logs = db.query(models.ReportLog).order_by(models.ReportLog.generated_at.desc()).limit(50).all()
    return [
        {
            "id": l.id,
            "report_type": l.report_type,
            "parameters": l.parameters,
            "generated_by": l.generated_by,
            "generated_at": l.generated_at,
        }
        for l in logs
    ]
