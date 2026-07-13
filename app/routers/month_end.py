"""Month-end processing router — manual trigger + run history."""
import asyncio
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Request
from sqlalchemy.orm import Session
from .. import models, auth
from ..database import get_db
from ..audit import audit_from_request

router = APIRouter(prefix="/api/month-end", tags=["Month-End"], dependencies=[Depends(auth.require_admin)])


@router.get("/runs")
def list_runs(limit: int = 20, db: Session = Depends(get_db)):
    runs = db.query(models.MonthEndRun).order_by(models.MonthEndRun.started_at.desc()).limit(limit).all()
    return [_run_out(r) for r in runs]


@router.get("/runs/{run_id}")
def get_run(run_id: int, db: Session = Depends(get_db)):
    run = db.query(models.MonthEndRun).filter(models.MonthEndRun.id == run_id).first()
    if not run:
        raise HTTPException(404, "Run not found")
    return _run_out(run, include_log=True)


@router.post("/trigger", status_code=202)
async def trigger_month_end(
    payload: dict,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """
    Manually trigger a month-end run.
    payload: {"billing_period": "2026-01"}  — defaults to current month if omitted.
    """
    from ..scheduler import run_month_end
    billing_period = payload.get("billing_period")
    username = request.session.get("username", "admin")

    audit_from_request(request, db, "MONTHEND", "MonthEndRun", None,
                       f"Manual month-end triggered for period {billing_period or 'current'}")
    db.commit()

    background_tasks.add_task(run_month_end, billing_period=billing_period, triggered_by=username)
    return {"message": "Month-end processing started in background", "billing_period": billing_period or "current"}


def _run_out(r: models.MonthEndRun, include_log: bool = False) -> dict:
    d = {
        "id": r.id, "billing_period": r.billing_period,
        "triggered_by": r.triggered_by, "trigger_type": r.trigger_type,
        "status": r.status, "started_at": r.started_at.isoformat() if r.started_at else None,
        "completed_at": r.completed_at.isoformat() if r.completed_at else None,
        "invoices_generated": r.invoices_generated, "notifications_sent": r.notifications_sent,
        "errors": r.errors,
    }
    if include_log:
        d["log"] = r.log
    return d
