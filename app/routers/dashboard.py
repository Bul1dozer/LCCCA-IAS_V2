"""
Dashboard router — v3: uses ledger-derived totals, not learner.balance cache.
"""
from datetime import datetime, timedelta
from collections import defaultdict
from decimal import Decimal

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func, text

from .. import models, schemas, auth
from ..database import get_db
from ..ledger import quantize

router = APIRouter(prefix="/api/dashboard", tags=["Dashboard"], dependencies=[Depends(auth.require_admin)])


@router.get("/stats", response_model=schemas.DashboardStats)
def get_stats(db: Session = Depends(get_db)):
    total_learners = db.query(models.Learner).filter(models.Learner.is_active == True).count()
    active_learners = db.query(models.Learner).filter(
        models.Learner.status == "Active",
        models.Learner.is_active == True,
    ).count()
    total_parents = db.query(models.Parent).filter(models.Parent.is_active == True).count()

    # Authoritative outstanding balance from ledger
    ledger_outstanding = db.execute(
        text("""
            SELECT
              COALESCE(SUM(CASE WHEN dc_indicator='DR' AND is_voided=0 THEN amount ELSE 0 END), 0)
              - COALESCE(SUM(CASE WHEN dc_indicator='CR' AND is_voided=0 THEN amount ELSE 0 END), 0)
            FROM ledger_entries
        """)
    ).scalar() or Decimal(0)

    # Total payments received (active only)
    total_payments = db.execute(
        text("SELECT COALESCE(SUM(amount_paid), 0) FROM payments WHERE is_active=1")
    ).scalar() or Decimal(0)

    invoices_generated = db.query(models.Invoice).filter(
        models.Invoice.status.notin_(["Void", "Reversed"])
    ).count()
    invoices_sent = db.query(models.Invoice).filter(models.Invoice.status == "Sent").count()

    # Total charged from non-voided invoices
    total_charged = db.execute(
        text("""
            SELECT COALESCE(SUM(current_charges), 0)
            FROM invoices
            WHERE status NOT IN ('Void', 'Reversed')
        """)
    ).scalar() or Decimal(0)

    collection_rate = Decimal("0.0")
    if total_charged and total_charged > 0:
        collection_rate = quantize(
            min(Decimal(str(total_payments)) / Decimal(str(total_charged)), Decimal("1.0")) * 100
        )

    return schemas.DashboardStats(
        total_learners=total_learners,
        total_parents=total_parents,
        total_outstanding_balance=quantize(ledger_outstanding),
        total_payments_received=quantize(total_payments),
        invoices_generated=invoices_generated,
        invoices_sent=invoices_sent,
        active_learners=active_learners,
        collection_rate=collection_rate,
    )


@router.get("/activity", response_model=list[schemas.ActivityItem])
def recent_activity(limit: int = 10, db: Session = Depends(get_db)):
    activities = []

    for p in db.query(models.Payment).filter(
        models.Payment.is_active == True
    ).order_by(models.Payment.created_at.desc()).limit(limit).all():
        learner = db.query(models.Learner).filter(models.Learner.id == p.learner_id).first()
        activities.append(schemas.ActivityItem(
            type="payment",
            description=f"Payment of N$ {float(p.amount_paid):,.2f} received from {learner.full_name if learner else 'Unknown learner'}",
            timestamp=p.created_at,
            icon="cash-coin",
        ))

    for inv in db.query(models.Invoice).filter(
        models.Invoice.status.notin_(["Void", "Reversed"])
    ).order_by(models.Invoice.created_at.desc()).limit(limit).all():
        learner = db.query(models.Learner).filter(models.Learner.id == inv.learner_id).first()
        action = "sent to" if inv.status == "Sent" else "generated for"
        activities.append(schemas.ActivityItem(
            type="invoice",
            description=f"Invoice {inv.invoice_number} {action} {learner.full_name if learner else 'Unknown learner'}",
            timestamp=inv.created_at,
            icon="file-earmark-text",
        ))

    for l in db.query(models.Learner).filter(
        models.Learner.is_active == True
    ).order_by(models.Learner.created_at.desc()).limit(limit).all():
        activities.append(schemas.ActivityItem(
            type="learner",
            description=f"New learner enrolled: {l.full_name} ({l.grade})",
            timestamp=l.created_at,
            icon="person-plus",
        ))

    activities.sort(key=lambda a: a.timestamp, reverse=True)
    return activities[:limit]


@router.get("/collections-chart", response_model=list[schemas.MonthlyCollection])
def collections_chart(months: int = 6, db: Session = Depends(get_db)):
    """Aggregate payments by month (using payment_date, not created_at)."""
    today = datetime.utcnow()
    labels = []
    for i in range(months - 1, -1, -1):
        year = today.year
        month = today.month - i
        while month <= 0:
            month += 12
            year -= 1
        label = datetime(year, month, 1).strftime("%b %Y")
        labels.append((year, month, label))

    buckets = defaultdict(Decimal)
    for _, _, label in labels:
        buckets[label] = Decimal("0.00")

    payments = db.query(models.Payment).filter(models.Payment.is_active == True).all()
    for p in payments:
        label = p.payment_date.strftime("%b %Y") if p.payment_date else p.date_paid.strftime("%b %Y")
        if label in buckets:
            buckets[label] += quantize(p.amount_paid)

    return [schemas.MonthlyCollection(month=label, total=quantize(buckets[label])) for _, _, label in labels]


@router.get("/grade-distribution", response_model=list[schemas.GradeDistribution])
def grade_distribution(db: Session = Depends(get_db)):
    rows = db.query(models.Learner.grade, func.count(models.Learner.id)).filter(
        models.Learner.is_active == True
    ).group_by(models.Learner.grade).all()
    return [schemas.GradeDistribution(grade=g, count=c) for g, c in rows]


@router.get("/reconciliation", response_model=schemas.ReconciliationReport)
def reconciliation_check(db: Session = Depends(get_db)):
    """
    Compare ledger-derived totals vs balance cache.
    Any discrepancy indicates a data integrity issue.
    """
    from ..ledger import reconcile_check
    result = reconcile_check(db)
    return schemas.ReconciliationReport(
        ledger_total=Decimal(result["ledger_total"]),
        cache_total=Decimal(result["cache_total"]),
        discrepancy=Decimal(result["discrepancy"]),
        reconciled=result["reconciled"],
    )
