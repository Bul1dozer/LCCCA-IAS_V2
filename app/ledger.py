"""LCCA-IAS Ledger Engine — authoritative financial core."""
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from sqlalchemy.orm import Session
from sqlalchemy import text
from . import models

TWO = Decimal("0.01")

def quantize(v) -> Decimal:
    return Decimal(str(v)).quantize(TWO, rounding=ROUND_HALF_UP)

def compute_balance(learner_id: int, db: Session) -> Decimal:
    db.flush()
    r = db.execute(text("""
        SELECT COALESCE(SUM(CASE WHEN dc_indicator='DR' AND is_voided=0 THEN amount ELSE 0 END),0)
             - COALESCE(SUM(CASE WHEN dc_indicator='CR' AND is_voided=0 THEN amount ELSE 0 END),0)
        FROM ledger_entries WHERE learner_id=:lid
    """), {"lid": learner_id}).scalar()
    return quantize(r or 0)

def sync_balance_cache(learner_id: int, db: Session):
    b = compute_balance(learner_id, db)
    l = db.query(models.Learner).filter(models.Learner.id == learner_id).first()
    if l:
        l.balance = b

def next_invoice_number(db: Session) -> str:
    year = datetime.now().year
    exists = db.execute(text("SELECT 1 FROM invoice_counter WHERE id=1")).first()
    if not exists:
        db.execute(text("INSERT INTO invoice_counter(id,year,last_sequence) VALUES(1,:y,0)"), {"y": year})
    db.execute(text("UPDATE invoice_counter SET year=:y,last_sequence=0 WHERE id=1 AND year!=:y"), {"y": year})
    db.execute(text("UPDATE invoice_counter SET last_sequence=last_sequence+1 WHERE id=1"))
    seq = db.execute(text("SELECT last_sequence FROM invoice_counter WHERE id=1")).scalar()
    db.flush()
    return f"INV-{year}-{seq:06d}"

def post_invoice(db, learner_id, invoice_id, amount, transaction_date, created_by="system", notes=None):
    amount = quantize(amount)
    if amount <= 0:
        raise ValueError(f"Invoice amount must be positive, got {amount}")
    entry = models.LedgerEntry(
        learner_id=learner_id, transaction_type="invoice",
        reference_type="Invoice", reference_id=invoice_id,
        amount=amount, dc_indicator="DR", transaction_date=transaction_date,
        created_by=created_by, notes=notes or f"Invoice #{invoice_id}",
    )
    db.add(entry)
    db.flush()
    sync_balance_cache(learner_id, db)
    return entry

def post_payment(db, learner_id, payment_id, amount, payment_date, created_by="system", notes=None):
    amount = quantize(amount)
    if amount <= 0:
        raise ValueError(f"Payment amount must be positive, got {amount}")
    entry = models.LedgerEntry(
        learner_id=learner_id, transaction_type="payment",
        reference_type="Payment", reference_id=payment_id,
        amount=amount, dc_indicator="CR", transaction_date=payment_date,
        created_by=created_by, notes=notes or f"Payment #{payment_id}",
    )
    db.add(entry)
    db.flush()
    sync_balance_cache(learner_id, db)
    return entry

def void_invoice_entries(db, invoice_id, voided_by, void_reason=None):
    now = datetime.utcnow()
    entries = db.query(models.LedgerEntry).filter(
        models.LedgerEntry.reference_type == "Invoice",
        models.LedgerEntry.reference_id == invoice_id,
        models.LedgerEntry.is_voided == False,
    ).all()
    for e in entries:
        e.is_voided = True
        e.voided_at = now
        e.voided_by = voided_by
    if entries:
        db.flush()
        sync_balance_cache(entries[0].learner_id, db)

def reverse_payment_entry(db, payment_id, reversed_by, reversal_reason=None):
    now = datetime.utcnow()
    entry = db.query(models.LedgerEntry).filter(
        models.LedgerEntry.reference_type == "Payment",
        models.LedgerEntry.reference_id == payment_id,
        models.LedgerEntry.is_voided == False,
    ).first()
    if entry:
        entry.is_voided = True
        entry.voided_at = now
        entry.voided_by = reversed_by
        db.flush()
        sync_balance_cache(entry.learner_id, db)

def validate_payment_amount(amount) -> Decimal:
    d = quantize(amount)
    if d <= 0:
        raise ValueError(f"Payment amount must be > 0, got {d}")
    return d

def check_duplicate_reference(db, reference_number, exclude_id=None):
    if not reference_number:
        return False
    q = db.query(models.Payment).filter(
        models.Payment.reference_number == reference_number,
        models.Payment.is_active == True,
    )
    if exclude_id:
        q = q.filter(models.Payment.id != exclude_id)
    return q.first() is not None

def reconcile_check(db):
    ledger_total = db.execute(text("""
        SELECT COALESCE(SUM(CASE WHEN dc_indicator='DR' AND is_voided=0 THEN amount ELSE 0 END),0)
             - COALESCE(SUM(CASE WHEN dc_indicator='CR' AND is_voided=0 THEN amount ELSE 0 END),0)
        FROM ledger_entries
    """)).scalar() or Decimal(0)
    cache_total = db.execute(text(
        "SELECT COALESCE(SUM(balance),0) FROM learners WHERE is_active=1"
    )).scalar() or Decimal(0)
    lt = quantize(ledger_total)
    ct = quantize(cache_total)
    return {"ledger_total": str(lt), "cache_total": str(ct),
            "discrepancy": str(quantize(lt - ct)), "reconciled": lt == ct}
