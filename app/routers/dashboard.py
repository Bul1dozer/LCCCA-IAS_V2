from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func, text
from .. import models, schemas, auth
from ..database import get_db
from ..ledger import quantize, reconcile_check

router = APIRouter(prefix="/api/dashboard", tags=["Dashboard"],
                   dependencies=[Depends(auth.require_admin)])


def _utc_timestamp(value):
    if not value:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.isoformat().replace("+00:00", "Z")


@router.get("/stats", response_model=schemas.DashboardStats)
def get_stats(db: Session = Depends(get_db)):
    total = db.query(models.Learner).filter(models.Learner.is_active == True).count()
    active = db.query(models.Learner).filter(
        models.Learner.is_active == True, models.Learner.status == "Active"
    ).count()
    parents = db.query(models.Parent).filter(models.Parent.is_active == True).count()
    outstanding = db.execute(text("""
        SELECT COALESCE(SUM(CASE WHEN dc_indicator='DR' AND is_voided=0 THEN amount ELSE 0 END),0)
             - COALESCE(SUM(CASE WHEN dc_indicator='CR' AND is_voided=0 THEN amount ELSE 0 END),0)
        FROM ledger_entries
    """)).scalar() or Decimal(0)
    total_paid = db.execute(text(
        "SELECT COALESCE(SUM(amount_paid),0) FROM payments WHERE is_active=1"
    )).scalar() or Decimal(0)
    invoices_gen = db.query(models.Invoice).filter(
        models.Invoice.status.notin_(["Void", "Reversed"])
    ).count()
    invoices_sent = db.query(models.EmailLog).filter(models.EmailLog.status == "Sent").count()
    total_charged = db.execute(text("""
        SELECT COALESCE(SUM(current_charges),0) FROM invoices
        WHERE status NOT IN ('Void','Reversed')
    """)).scalar() or Decimal(0)
    coll_rate = Decimal("0.0")
    if total_charged and Decimal(str(total_charged)) > 0:
        coll_rate = quantize(
            min(Decimal(str(total_paid)) / Decimal(str(total_charged)), Decimal("1")) * 100
        )
    return schemas.DashboardStats(
        total_learners=total, total_parents=parents,
        total_outstanding_balance=quantize(outstanding),
        total_payments_received=quantize(total_paid),
        invoices_generated=invoices_gen, invoices_sent=invoices_sent, active_learners=active,
        collection_rate=coll_rate,
    )


@router.get("/activity", response_model=list[schemas.DashboardActivity])
def activity(limit: int = 12, db: Session = Depends(get_db)):
    items = []

    for audit in db.query(models.AuditLog).order_by(models.AuditLog.created_at.desc()).limit(limit).all():
        resource = audit.resource_type or "record"
        detail = f"{audit.username} {audit.action.lower()} {resource.lower()}"
        if audit.resource_id:
            detail += f" #{audit.resource_id}"
        items.append({
            "description": audit.detail or detail,
            "timestamp": _utc_timestamp(audit.created_at),
            "icon": "shield-check",
        })

    for invoice in db.query(models.Invoice).order_by(models.Invoice.created_at.desc()).limit(limit).all():
        items.append({
            "description": f"Invoice {invoice.invoice_number} generated",
            "timestamp": _utc_timestamp(invoice.created_at),
            "icon": "file-earmark-text",
        })

    for payment in db.query(models.Payment).order_by(models.Payment.created_at.desc()).limit(limit).all():
        learner = payment.learner.full_name if payment.learner else "learner"
        items.append({
            "description": f"Payment of N${quantize(payment.amount_paid)} recorded for {learner}",
            "timestamp": _utc_timestamp(payment.created_at),
            "icon": "cash-coin",
        })

    for learner in db.query(models.Learner).order_by(models.Learner.created_at.desc()).limit(limit).all():
        items.append({
            "description": f"Learner {learner.full_name} added",
            "timestamp": _utc_timestamp(learner.created_at),
            "icon": "person-plus",
        })

    items.sort(key=lambda item: item["timestamp"] or "", reverse=True)
    return items[: max(1, min(limit, 50))]


@router.get("/collections-chart", response_model=list[schemas.MonthlyCollection])
def collections_chart(months: int = 6, db: Session = Depends(get_db)):
    today = datetime.utcnow()
    labels = []
    for i in range(months - 1, -1, -1):
        yr, mo = today.year, today.month - i
        while mo <= 0:
            mo += 12; yr -= 1
        labels.append((yr, mo, datetime(yr, mo, 1).strftime("%b %Y")))
    buckets = {lbl: Decimal("0.00") for _, _, lbl in labels}
    for p in db.query(models.Payment).filter(models.Payment.is_active == True).all():
        lbl = p.payment_date.strftime("%b %Y") if p.payment_date else p.date_paid.strftime("%b %Y")
        if lbl in buckets:
            buckets[lbl] += quantize(p.amount_paid)
    return [schemas.MonthlyCollection(month=lbl, total=buckets[lbl]) for _, _, lbl in labels]


@router.get("/grade-distribution", response_model=list[schemas.GradeDistribution])
def grade_distribution(db: Session = Depends(get_db)):
    rows = db.query(models.Learner.grade, func.count(models.Learner.id)).filter(
        models.Learner.is_active == True
    ).group_by(models.Learner.grade).all()
    return [schemas.GradeDistribution(grade=g, count=c) for g, c in rows]


@router.get("/reconciliation", response_model=schemas.ReconciliationReport)
def reconciliation(db: Session = Depends(get_db)):
    r = reconcile_check(db)
    return schemas.ReconciliationReport(
        ledger_total=Decimal(r["ledger_total"]),
        cache_total=Decimal(r["cache_total"]),
        discrepancy=Decimal(r["discrepancy"]),
        reconciled=r["reconciled"],
    )
