from collections import defaultdict
from decimal import Decimal
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func, text
from .. import models, schemas, auth
from ..database import get_db
from ..ledger import quantize, reconcile_check

router = APIRouter(prefix="/api/dashboard", tags=["Dashboard"],
                   dependencies=[Depends(auth.require_admin)])


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
        invoices_generated=invoices_gen, active_learners=active,
        collection_rate=coll_rate,
    )


@router.get("/collections-chart", response_model=list[schemas.MonthlyCollection])
def collections_chart(months: int = 6, db: Session = Depends(get_db)):
    from datetime import datetime
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
