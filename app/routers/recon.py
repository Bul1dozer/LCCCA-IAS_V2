"""Bank Reconciliation router."""
import csv, io, json
from datetime import date, datetime
from fastapi import APIRouter, Depends, File, UploadFile, HTTPException, Request
from sqlalchemy.orm import Session
from .. import models, auth
from ..database import get_db
from ..audit import audit_from_request

router = APIRouter(prefix="/api/recon", tags=["Reconciliation"], dependencies=[Depends(auth.require_admin)])


@router.post("/import-statement")
async def import_bank_statement(file: UploadFile = File(...), request: Request = None, db: Session = Depends(get_db)):
    """Import a CSV bank statement. Columns: date, description, amount, reference."""
    content = file.file.read()
    text = content.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    rows = list(reader)

    import_ref = f"STMT-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    imported, errors = [], []

    for i, row in enumerate(rows, start=2):
        try:
            raw_date = str(row.get("date") or row.get("transaction_date") or "").strip()
            description = str(row.get("description") or row.get("narrative") or "").strip()
            amount_raw = str(row.get("amount") or row.get("credit") or "0").replace(",", "").strip()
            reference = str(row.get("reference") or row.get("ref") or "").strip()

            try:
                txn_date = date.fromisoformat(raw_date)
            except ValueError:
                errors.append({"row": i, "error": f"Invalid date: {raw_date}"})
                continue
            try:
                amount = float(amount_raw)
            except ValueError:
                errors.append({"row": i, "error": f"Invalid amount: {amount_raw}"})
                continue
            if amount <= 0:
                continue  # skip debits / fees

            line = models.BankStatementLine(
                import_ref=import_ref, transaction_date=txn_date,
                description=description, amount=amount, reference=reference,
                recon_status="unmatched",
            )
            db.add(line)
            db.flush()
            imported.append(line.id)

            # Attempt auto-match against payments by reference number
            if reference:
                payment = db.query(models.Payment).filter(
                    models.Payment.reference_number == reference,
                    models.Payment.bank_statement_line_id == None,
                ).first()
                if payment and abs(payment.amount_paid - amount) < 0.01:
                    payment.bank_statement_line_id = line.id
                    line.recon_status = "matched"
                    line.matched_payment_id = payment.id

        except Exception as e:
            errors.append({"row": i, "error": str(e)})

    db.commit()
    if request:
        audit_from_request(request, db, "RECON", "BankStatementLine", None,
                           f"Imported {len(imported)} statement lines, {len(errors)} errors")
        db.commit()

    return {
        "import_ref": import_ref, "lines_imported": len(imported),
        "auto_matched": sum(1 for lid in imported
                            if db.query(models.BankStatementLine).filter(models.BankStatementLine.id == lid,
                                        models.BankStatementLine.recon_status == "matched").count()),
        "errors": errors[:20],
    }


@router.get("/lines")
def list_statement_lines(status: str = None, limit: int = 200, db: Session = Depends(get_db)):
    q = db.query(models.BankStatementLine)
    if status:
        q = q.filter(models.BankStatementLine.recon_status == status)
    lines = q.order_by(models.BankStatementLine.transaction_date.desc()).limit(limit).all()
    return [_line_out(l) for l in lines]


@router.post("/match/{line_id}")
def manual_match(line_id: int, payload: dict, request: Request, db: Session = Depends(get_db)):
    line = db.query(models.BankStatementLine).filter(models.BankStatementLine.id == line_id).first()
    if not line:
        raise HTTPException(404, "Statement line not found")
    payment = db.query(models.Payment).filter(models.Payment.id == payload["payment_id"]).first()
    if not payment:
        raise HTTPException(404, "Payment not found")

    line.recon_status = "manually_matched"
    line.matched_payment_id = payment.id
    payment.bank_statement_line_id = line.id
    audit_from_request(request, db, "RECON", "BankStatementLine", line_id,
                       f"Manually matched line {line_id} to payment {payment.id}")
    db.commit()
    return _line_out(line)


@router.post("/exclude/{line_id}")
def exclude_line(line_id: int, request: Request, db: Session = Depends(get_db)):
    line = db.query(models.BankStatementLine).filter(models.BankStatementLine.id == line_id).first()
    if not line:
        raise HTTPException(404, "Statement line not found")
    line.recon_status = "excluded"
    audit_from_request(request, db, "RECON", "BankStatementLine", line_id, f"Excluded line {line_id}")
    db.commit()
    return _line_out(line)


@router.get("/summary")
def recon_summary(db: Session = Depends(get_db)):
    from sqlalchemy import func
    rows = db.query(models.BankStatementLine.recon_status, func.count(), func.sum(models.BankStatementLine.amount)) \
        .group_by(models.BankStatementLine.recon_status).all()
    return [{"status": r[0], "count": r[1], "total": round(r[2] or 0, 2)} for r in rows]


def _line_out(l: models.BankStatementLine) -> dict:
    return {
        "id": l.id, "import_ref": l.import_ref,
        "transaction_date": str(l.transaction_date), "description": l.description,
        "amount": l.amount, "reference": l.reference, "recon_status": l.recon_status,
        "matched_payment_id": l.matched_payment_id,
    }
