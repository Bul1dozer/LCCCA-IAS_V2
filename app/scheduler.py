"""
Month-End Scheduler — v3 Production: APScheduler-based automated billing engine.

Idempotency hardening:
  - A unique (billing_period, status='running') guard prevents two
    concurrent runs for the same period via a DB-level lock attempt.
  - Per-learner invoice existence check still guards at the individual
    invoice level (belt and braces).
  - If a run crashes mid-way, re-running it picks up exactly where it
    left off because already-invoiced learners are skipped.
"""

import logging
from datetime import date, datetime, timedelta
from calendar import monthrange
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from .database import SessionLocal, get_session
from . import models
from .fee_engine import generate_invoice_for_learner
from .email_engine import send_invoice_email
from .pdf_generator import build_invoice_pdf
from .audit import audit

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler = None


def get_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler(timezone="Africa/Windhoek")
    return _scheduler


async def run_month_end(billing_period: str = None, triggered_by: str = "scheduler") -> dict:
    """
    Execute a full month-end billing run.

    Idempotency guarantee: running this twice for the same billing_period
    will NEVER create duplicate invoices. A DB-level unique constraint on
    (billing_period, status='running') blocks concurrent runs for the same
    period; per-learner existence checks make re-runs after partial
    failure safe (already-billed learners are skipped).

    billing_period: "YYYY-MM" string. Defaults to current month.
    Returns summary dict.
    """
    if not billing_period:
        now = datetime.now()
        billing_period = now.strftime("%Y-%m")

    db: Session = SessionLocal()

    # --- Idempotency guard: prevent two concurrent "running" runs for the same period ---
    existing_running = db.query(models.MonthEndRun).filter(
        models.MonthEndRun.billing_period == billing_period,
        models.MonthEndRun.status == "running",
    ).first()
    if existing_running:
        db.close()
        logger.warning("Month-end run for %s already in progress (run_id=%s). Skipping.",
                       billing_period, existing_running.id)
        return {
            "run_id": existing_running.id,
            "billing_period": billing_period,
            "status": "skipped_already_running",
            "invoices_generated": 0,
            "notifications_sent": 0,
            "errors": ["A run for this billing period is already in progress."],
        }

    run = models.MonthEndRun(
        billing_period=billing_period,
        triggered_by=triggered_by,
        trigger_type="scheduled" if triggered_by == "scheduler" else "manual",
        status="running",
        started_at=datetime.utcnow(),
    )
    db.add(run)
    try:
        db.commit()
    except IntegrityError:
        # Another process won the race to create the "running" row first
        db.rollback()
        db.close()
        logger.warning("Race condition detected creating run for %s — another process is handling it.",
                       billing_period)
        return {
            "run_id": None,
            "billing_period": billing_period,
            "status": "skipped_race_condition",
            "invoices_generated": 0,
            "notifications_sent": 0,
            "errors": ["Concurrent month-end run detected and prevented duplicate processing."],
        }
    db.refresh(run)

    log_lines = []
    invoices_generated = 0
    notifications_sent = 0
    errors = []

    try:
        year, month = map(int, billing_period.split("-"))
        last_day = monthrange(year, month)[1]
        due_date = date(year, month, last_day)

        learners = db.query(models.Learner).filter(
            models.Learner.status == "Active",
            models.Learner.is_active == True,
        ).all()
        learner_ids = [l.id for l in learners]
        log_lines.append(f"Processing {len(learner_ids)} active learners for period {billing_period}")

        db.close()

        for lid in learner_ids:
            db2 = SessionLocal()
            try:
                learner = db2.query(models.Learner).filter(
                    models.Learner.id == lid,
                    models.Learner.is_active == True,
                ).first()
                if not learner:
                    continue

                # Idempotency: skip if invoice already exists for this period (non-voided)
                existing = db2.query(models.Invoice).filter(
                    models.Invoice.learner_id == lid,
                    models.Invoice.billing_period == billing_period,
                    models.Invoice.status.notin_(["Void", "Reversed"]),
                ).first()
                if existing:
                    log_lines.append(f"  SKIP {learner.learner_code}: already invoiced ({existing.invoice_number})")
                    continue

                invoice = generate_invoice_for_learner(
                    db=db2, learner=learner, due_date=due_date,
                    billing_period=billing_period, frequency="monthly",
                    triggered_by=triggered_by, month_end_run_id=run.id,
                )
                if invoice:
                    db2.flush()
                    invoices_generated += 1
                    parents = [rel.parent for rel in learner.relationships_]
                    pdf_bytes = build_invoice_pdf(invoice, learner, parents, invoice.items)
                    db2.commit()
                    log_lines.append(f"  INVOICE {invoice.invoice_number} generated for {learner.full_name}")

                    db3 = SessionLocal()
                    try:
                        inv_obj = db3.query(models.Invoice).filter(models.Invoice.id == invoice.id).first()
                        l_obj = db3.query(models.Learner).filter(models.Learner.id == lid).first()
                        p_objs = [rel.parent for rel in l_obj.relationships_]
                        result = await send_invoice_email(db3, inv_obj, l_obj, p_objs, pdf_bytes)
                        db3.commit()
                        if result["status"] in ("Sent", "Simulated"):
                            notifications_sent += 1
                            log_lines.append(f"    EMAIL -> {result['recipient']} [{result['status']}]")
                        else:
                            log_lines.append(f"    EMAIL FAILED -> {result.get('error')}")
                    except Exception as e:
                        db3.rollback()
                        log_lines.append(f"    EMAIL ERROR: {e}")
                    finally:
                        db3.close()
                else:
                    log_lines.append(f"  SKIP {learner.learner_code}: no billable monthly fees")

            except Exception as e:
                db2.rollback()
                err = f"ERROR {lid}: {e}"
                errors.append(err)
                log_lines.append(err)
                logger.exception("Month-end error for learner id %s", lid)
            finally:
                db2.close()

        db = SessionLocal()
        run_obj = db.query(models.MonthEndRun).filter(models.MonthEndRun.id == run.id).first()
        run_obj.status = "completed"
        run_obj.completed_at = datetime.utcnow()
        run_obj.invoices_generated = invoices_generated
        run_obj.notifications_sent = notifications_sent
        run_obj.log = "\n".join(log_lines)
        run_obj.errors = "\n".join(errors) if errors else None
        audit(db, action="MONTHEND", resource_type="MonthEndRun", resource_id=run.id,
              detail=f"Period {billing_period}: {invoices_generated} invoices, {notifications_sent} notifications",
              username=triggered_by)
        db.commit()
        logger.info("Month-end complete: %d invoices, %d notifications", invoices_generated, notifications_sent)

    except Exception as e:
        try:
            db_err = SessionLocal()
            run_obj = db_err.query(models.MonthEndRun).filter(models.MonthEndRun.id == run.id).first()
            if run_obj:
                run_obj.status = "failed"
                run_obj.errors = str(e)
                run_obj.completed_at = datetime.utcnow()
                db_err.commit()
            db_err.close()
        except Exception:
            pass
        logger.exception("Month-end run failed — safe to re-run; already-billed learners will be skipped")
        raise
    finally:
        try:
            db.close()
        except Exception:
            pass

    return {
        "run_id": run.id,
        "billing_period": billing_period,
        "status": run.status,
        "invoices_generated": invoices_generated,
        "notifications_sent": notifications_sent,
        "errors": errors,
    }


def start_scheduler():
    sched = get_scheduler()
    if not sched.running:
        sched.add_job(
            run_month_end,
            CronTrigger(day=1, hour=0, minute=5),
            id="monthly_billing",
            replace_existing=True,
            name="Monthly Billing Run",
        )
        sched.start()
        logger.info("APScheduler started — monthly billing job registered")


def stop_scheduler():
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
