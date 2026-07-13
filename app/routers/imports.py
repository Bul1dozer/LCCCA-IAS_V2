"""
Bulk import router — CSV/Excel for learners, parents, payments.
Validates before committing, returns row-level error reports.
"""

import csv, io, json
from datetime import date, datetime
from fastapi import APIRouter, Depends, File, UploadFile, HTTPException, Request
from sqlalchemy.orm import Session
from .. import models, auth
from ..database import get_db
from ..audit import audit_from_request
from ..fee_engine import auto_assign_mandatory_fees

router = APIRouter(prefix="/api/imports", tags=["Imports"], dependencies=[Depends(auth.require_admin)])


def _read_file(file: UploadFile):
    """Return list of row dicts from CSV or XLSX."""
    content = file.file.read()
    name = (file.filename or "").lower()
    if name.endswith(".xlsx") or name.endswith(".xls"):
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            return []
        headers = [str(h).strip() if h else f"col_{i}" for i, h in enumerate(rows[0])]
        return [dict(zip(headers, row)) for row in rows[1:]]
    else:
        text = content.decode("utf-8-sig", errors="replace")
        reader = csv.DictReader(io.StringIO(text))
        return [row for row in reader]


@router.post("/learners")
async def import_learners(file: UploadFile = File(...), request: Request = None, db: Session = Depends(get_db)):
    rows = _read_file(file)
    success, errors = [], []
    counter = db.query(models.Learner).count()

    for i, row in enumerate(rows, start=2):
        try:
            name = str(row.get("full_name") or row.get("name") or "").strip()
            grade = str(row.get("grade") or "").strip()
            class_name = str(row.get("class_name") or row.get("class") or "").strip()
            admission_raw = str(row.get("date_of_admission") or row.get("admission_date") or "").strip()
            status = str(row.get("status") or "Active").strip()

            if not name:
                errors.append({"row": i, "error": "full_name is required"})
                continue
            if not grade:
                errors.append({"row": i, "error": "grade is required"})
                continue

            try:
                admission = date.fromisoformat(admission_raw) if admission_raw else date.today()
            except ValueError:
                errors.append({"row": i, "error": f"Invalid date_of_admission: {admission_raw}"})
                continue

            counter += 1
            from datetime import datetime as dt
            code = f"LCCA-{dt.now().year}-{counter:04d}"

            learner = models.Learner(
                learner_code=code, full_name=name, grade=grade,
                class_name=class_name or grade, date_of_admission=admission,
                status=status, balance=0.0,
            )
            db.add(learner)
            db.flush()
            auto_assign_mandatory_fees(db, learner, assigned_by="import")
            success.append({"row": i, "learner_code": code, "full_name": name})
        except Exception as e:
            errors.append({"row": i, "error": str(e)})

    db.commit()

    job = models.ImportJob(
        import_type="learners", filename=file.filename,
        total_rows=len(rows), success_rows=len(success), error_rows=len(errors),
        status="completed", error_report=json.dumps(errors),
        uploaded_by=request.session.get("username", "admin") if request else "admin",
        completed_at=datetime.utcnow(),
    )
    db.add(job)
    if request:
        audit_from_request(request, db, "IMPORT", "Learner", None,
                           f"Imported {len(success)} learners, {len(errors)} errors")
    db.commit()

    return {
        "total_rows": len(rows), "success": len(success), "errors": len(errors),
        "error_report": errors[:50],
        "template_hint": "Columns: full_name, grade, class_name, date_of_admission (YYYY-MM-DD), status",
    }


@router.post("/parents")
async def import_parents(file: UploadFile = File(...), request: Request = None, db: Session = Depends(get_db)):
    rows = _read_file(file)
    success, errors = [], []

    for i, row in enumerate(rows, start=2):
        try:
            name = str(row.get("full_name") or row.get("name") or "").strip()
            if not name:
                errors.append({"row": i, "error": "full_name is required"})
                continue
            parent = models.Parent(
                full_name=name,
                email=str(row.get("email") or "").strip() or None,
                phone=str(row.get("phone") or "").strip() or None,
                address=str(row.get("address") or "").strip() or None,
            )
            db.add(parent)
            success.append({"row": i, "full_name": name})
        except Exception as e:
            errors.append({"row": i, "error": str(e)})

    db.commit()
    job = models.ImportJob(
        import_type="parents", filename=file.filename,
        total_rows=len(rows), success_rows=len(success), error_rows=len(errors),
        status="completed", error_report=json.dumps(errors),
        uploaded_by=request.session.get("username", "admin") if request else "admin",
        completed_at=datetime.utcnow(),
    )
    db.add(job)
    db.commit()
    return {"total_rows": len(rows), "success": len(success), "errors": len(errors), "error_report": errors[:50]}


@router.post("/payments")
async def import_payments(file: UploadFile = File(...), request: Request = None, db: Session = Depends(get_db)):
    rows = _read_file(file)
    success, errors = [], []

    for i, row in enumerate(rows, start=2):
        try:
            learner_code = str(row.get("learner_code") or "").strip()
            amount_raw = str(row.get("amount_paid") or row.get("amount") or "").strip()
            date_raw = str(row.get("date_paid") or row.get("date") or "").strip()
            method = str(row.get("payment_method") or "Bank Transfer").strip()
            ref = str(row.get("reference_number") or row.get("reference") or "").strip()

            if not learner_code:
                errors.append({"row": i, "error": "learner_code required"})
                continue
            try:
                amount = float(amount_raw)
            except ValueError:
                errors.append({"row": i, "error": f"Invalid amount: {amount_raw}"})
                continue
            try:
                pay_date = date.fromisoformat(date_raw) if date_raw else date.today()
            except ValueError:
                errors.append({"row": i, "error": f"Invalid date_paid: {date_raw}"})
                continue

            learner = db.query(models.Learner).filter(models.Learner.learner_code == learner_code).first()
            if not learner:
                errors.append({"row": i, "error": f"Learner not found: {learner_code}"})
                continue

            payment = models.Payment(
                learner_id=learner.id, amount_paid=amount, date_paid=pay_date,
                payment_method=method, reference_number=ref or None,
            )
            db.add(payment)
            learner.balance = round(learner.balance - amount, 2)
            success.append({"row": i, "learner_code": learner_code, "amount": amount})
        except Exception as e:
            errors.append({"row": i, "error": str(e)})

    db.commit()
    job = models.ImportJob(
        import_type="payments", filename=file.filename,
        total_rows=len(rows), success_rows=len(success), error_rows=len(errors),
        status="completed", error_report=json.dumps(errors),
        uploaded_by=request.session.get("username", "admin") if request else "admin",
        completed_at=datetime.utcnow(),
    )
    db.add(job)
    db.commit()
    return {"total_rows": len(rows), "success": len(success), "errors": len(errors), "error_report": errors[:50]}


@router.get("/jobs")
def list_import_jobs(db: Session = Depends(get_db)):
    jobs = db.query(models.ImportJob).order_by(models.ImportJob.started_at.desc()).limit(50).all()
    return [
        {"id": j.id, "import_type": j.import_type, "filename": j.filename,
         "total_rows": j.total_rows, "success_rows": j.success_rows, "error_rows": j.error_rows,
         "status": j.status, "uploaded_by": j.uploaded_by,
         "started_at": j.started_at.isoformat() if j.started_at else None}
        for j in jobs
    ]


@router.get("/template/{import_type}")
def download_template(import_type: str):
    """Return CSV column headers as a template."""
    templates = {
        "learners": "full_name,grade,class_name,date_of_admission,status\nTendai Mukasa,Grade 3,3A,2024-01-15,Active",
        "parents": "full_name,email,phone,address\nMrs. Smith,smith@example.com,+264811234567,12 Main St",
        "payments": "learner_code,amount_paid,date_paid,payment_method,reference_number\nLCCA-2026-0001,2500,2026-02-01,Cash,RCPT-001",
    }
    if import_type not in templates:
        raise HTTPException(404, "Template not found")
    from fastapi.responses import Response
    return Response(content=templates[import_type], media_type="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={import_type}_template.csv"})
