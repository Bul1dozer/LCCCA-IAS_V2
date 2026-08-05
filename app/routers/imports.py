"""
Bulk import router — CSV/Excel for learners, parents, payments.
Validates before committing, returns row-level error reports.
"""

import csv, io, json
from datetime import date, datetime
from decimal import Decimal
from fastapi import APIRouter, Depends, File, UploadFile, HTTPException, Request
from sqlalchemy.orm import Session
from .. import models, auth
from ..database import get_db
from ..audit import audit_from_request
from ..fee_engine import auto_assign_mandatory_fees
from ..ledger import post_payment, validate_payment_amount, check_duplicate_reference

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


def _parse_date(value: str | None):
    raw = str(value or "").strip()
    if not raw:
        return None
    return date.fromisoformat(raw)


def _norm(value) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _learner_fingerprint(name, grade, class_name, admission, birth_date) -> tuple:
    return (
        _norm(name),
        _norm(grade),
        _norm(class_name),
        admission.isoformat() if admission else "",
        birth_date.isoformat() if birth_date else "",
    )


@router.post("/learners")
async def import_learners(file: UploadFile = File(...), request: Request = None, db: Session = Depends(get_db)):
    rows = _read_file(file)
    success, errors = [], []
    existing_learners = db.query(models.Learner).all()
    existing_codes = {l.learner_code for l in existing_learners if l.learner_code}
    existing_id_numbers = {_norm(l.id_number) for l in existing_learners if l.id_number}
    existing_fingerprints = {
        _learner_fingerprint(l.full_name, l.grade, l.class_name, l.date_of_admission, l.date_of_birth)
        for l in existing_learners
    }
    seen_codes = set()
    seen_id_numbers = set()
    seen_fingerprints = set()

    for i, row in enumerate(rows, start=2):
        try:
            name = str(row.get("full_name") or row.get("name") or "").strip()
            learner_code = str(row.get("learner_code") or "").strip()
            id_number = str(row.get("id_number") or "").strip() or None
            grade = str(row.get("grade") or "").strip()
            class_name = str(row.get("class_name") or row.get("class") or "").strip()
            admission_raw = str(row.get("date_of_admission") or row.get("admission_date") or "").strip()
            birth_raw = str(row.get("date_of_birth") or "").strip()
            status = str(row.get("status") or "Active").strip()
            physical_address = str(row.get("physical_address") or row.get("address") or "").strip() or None

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
            try:
                birth_date = _parse_date(birth_raw)
            except ValueError:
                errors.append({"row": i, "error": f"Invalid date_of_birth: {birth_raw}"})
                continue

            normalized_id = _norm(id_number)
            fingerprint = _learner_fingerprint(name, grade, class_name or grade, admission, birth_date)
            if learner_code and (learner_code in existing_codes or learner_code in seen_codes):
                errors.append({"row": i, "error": f"Duplicate learner_code: {learner_code}"})
                continue
            if normalized_id and (normalized_id in existing_id_numbers or normalized_id in seen_id_numbers):
                errors.append({"row": i, "error": f"Duplicate id_number: {id_number}"})
                continue
            if fingerprint in existing_fingerprints or fingerprint in seen_fingerprints:
                errors.append({
                    "row": i,
                    "error": "Duplicate learner row: same name, grade, class, admission date, and birth date",
                })
                continue

            with db.begin_nested():
                learner = models.Learner(
                    learner_code=learner_code or "import",
                    full_name=name,
                    grade=grade,
                    class_name=class_name or grade,
                    date_of_admission=admission,
                    status=status,
                    balance=Decimal("0.00"),
                    id_number=id_number,
                    date_of_birth=birth_date,
                    physical_address=physical_address,
                )
                db.add(learner)
                db.flush()
                auto_assign_mandatory_fees(db, learner, assigned_by="import")
            existing_codes.add(learner.learner_code)
            seen_codes.add(learner.learner_code)
            if learner_code:
                seen_codes.add(learner_code)
            if normalized_id:
                seen_id_numbers.add(normalized_id)
                existing_id_numbers.add(normalized_id)
            seen_fingerprints.add(fingerprint)
            existing_fingerprints.add(fingerprint)
            success.append({"row": i, "learner_code": learner.learner_code, "full_name": name})
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
        "template_hint": "Columns: learner_code, full_name, grade, class_name, date_of_admission, date_of_birth, id_number, physical_address, status",
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
                amount = validate_payment_amount(Decimal(amount_raw))
            except Exception:
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
            if ref and check_duplicate_reference(db, ref):
                errors.append({"row": i, "error": f"Duplicate payment reference: {ref}"})
                continue

            with db.begin_nested():
                payment = models.Payment(
                    learner_id=learner.id,
                    amount_paid=amount,
                    payment_date=pay_date,
                    date_paid=pay_date,
                    payment_method=method,
                    reference_number=ref or None,
                    created_by=request.session.get("username", "import") if request else "import",
                )
                db.add(payment)
                db.flush()
                post_payment(db, learner.id, payment.id, amount, pay_date, payment.created_by)
            success.append({"row": i, "learner_code": learner_code, "amount": str(amount)})
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
        "learners": "learner_code,full_name,grade,class_name,date_of_admission,date_of_birth,id_number,physical_address,status\n,Tendai Mukasa,Grade 3,3A,2024-01-15,2015-04-02,,12 Main St,Active",
        "parents": "full_name,email,phone,address\nMrs. Smith,smith@example.com,+264811234567,12 Main St",
        "payments": "learner_code,amount_paid,date_paid,payment_method,reference_number\nLCCA-2026-0001,2500,2026-02-01,Cash,RCPT-001",
    }
    if import_type not in templates:
        raise HTTPException(404, "Template not found")
    from fastapi.responses import Response
    return Response(content=templates[import_type], media_type="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={import_type}_template.csv"})
