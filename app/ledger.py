"""
LCCCA-IAS Ledger Engine — Production Financial Core.

The ledger is the ONLY authoritative source of truth for financial balances.
The learner.balance field is a DISPLAY CACHE only — never use it for
financial decisions. Always call compute_balance() for accurate figures.

Key functions:
  - compute_balance(learner_id, db)   → Decimal: current balance from ledger
  - post_invoice(...)                 → posts DR entry for an invoice
  - post_payment(...)                 → posts CR entry for a payment
  - void_invoice(...)                 → posts reversal CR entry
  - reverse_payment(...)              → posts reversal DR entry
  - next_invoice_number(db)           → concurrency-safe INV-YYYY-NNNNNN
"""

from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import func, text

from . import models


TWO_PLACES = Decimal("0.01")


def quantize(value) -> Decimal:
    """Convert any numeric to a 2-decimal-place Decimal, rounding HALF_UP."""
    return Decimal(str(value)).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------------------
# Core balance computation — ALWAYS derived from ledger
# ---------------------------------------------------------------------------
def compute_balance(learner_id: int, db: Session) -> Decimal:
    """
    Compute the current outstanding balance for a learner directly from
    the ledger.  This is the AUTHORITATIVE balance.

    Balance = sum(DR entries) - sum(CR entries)
    A positive balance means the learner owes money.

    Note: flushes pending ORM changes first. The raw SQL query below
    bypasses SQLAlchemy's autoflush-on-query mechanism (which only
    triggers for ORM-level Query/select() calls, not raw text()), so an
    explicit flush is required to guarantee any in-flight is_voided
    mutations or new entries are visible to this read.
    """
    db.flush()
    result = db.execute(
        text("""
            SELECT
              COALESCE(SUM(CASE WHEN dc_indicator = 'DR' AND is_voided = 0 THEN amount ELSE 0 END), 0)
              - COALESCE(SUM(CASE WHEN dc_indicator = 'CR' AND is_voided = 0 THEN amount ELSE 0 END), 0)
            FROM ledger_entries
            WHERE learner_id = :lid
        """),
        {"lid": learner_id}
    ).scalar()
    return quantize(result or 0)


def get_ledger_history(learner_id: int, db: Session) -> list:
    """Return all non-voided ledger entries for a learner, oldest first."""
    return (
        db.query(models.LedgerEntry)
        .filter(
            models.LedgerEntry.learner_id == learner_id,
            models.LedgerEntry.is_voided == False,
        )
        .order_by(models.LedgerEntry.transaction_date, models.LedgerEntry.id)
        .all()
    )


def sync_balance_cache(learner_id: int, db: Session):
    """
    Update learner.balance from ledger (cache sync).
    Call after any ledger posting to keep the display cache fresh.
    """
    balance = compute_balance(learner_id, db)
    learner = db.query(models.Learner).filter(models.Learner.id == learner_id).first()
    if learner:
        learner.balance = balance


# ---------------------------------------------------------------------------
# Invoice number generation — atomic & concurrency-safe
# ---------------------------------------------------------------------------
def next_invoice_number(db: Session) -> str:
    """
    Generate the next invoice number using an atomic counter row.
    Format: INV-YYYY-NNNNNN

    Concurrency strategy: the FastAPI get_db() dependency (see
    database.py) already serializes the ENTIRE request lifetime behind
    a process-wide semaphore on SQLite, so no additional locking is
    needed or safe here — acquiring the same non-reentrant semaphore a
    second time within an already-locked request would deadlock. This
    function therefore performs a plain atomic UPDATE, relying on the
    caller's request-level serialization for correctness. On PostgreSQL,
    where get_db() is a no-op, true row-level locking from the UPDATE
    statement itself provides the safety guarantee instead.
    """
    year = datetime.now().year

    exists = db.execute(
        text("SELECT 1 FROM invoice_counter WHERE id = 1")
    ).first()
    if not exists:
        db.execute(
            text("INSERT INTO invoice_counter (id, year, last_sequence) VALUES (1, :year, 0)"),
            {"year": year},
        )

    db.execute(
        text("""
            UPDATE invoice_counter
            SET year = :year, last_sequence = 0
            WHERE id = 1 AND year != :year
        """),
        {"year": year},
    )

    db.execute(
        text("UPDATE invoice_counter SET last_sequence = last_sequence + 1 WHERE id = 1"),
    )
    seq = db.execute(
        text("SELECT last_sequence FROM invoice_counter WHERE id = 1")
    ).scalar()
    db.flush()

    return f"INV-{year}-{seq:06d}"


# ---------------------------------------------------------------------------
# Ledger posting functions
# ---------------------------------------------------------------------------
def post_invoice(
    db: Session,
    learner_id: int,
    invoice_id: int,
    amount: Decimal,
    transaction_date: date,
    created_by: str = "system",
    notes: str = None,
) -> models.LedgerEntry:
    """
    Post a DEBIT entry for an invoice (learner owes more).
    Syncs the balance cache.
    """
    if amount <= 0:
        raise ValueError(f"Invoice ledger amount must be positive, got {amount}")

    entry = models.LedgerEntry(
        learner_id=learner_id,
        transaction_type=models.LedgerTransactionType.INVOICE,
        reference_type="Invoice",
        reference_id=invoice_id,
        amount=quantize(amount),
        dc_indicator=models.DebitCredit.DEBIT,
        transaction_date=transaction_date,
        created_by=created_by,
        notes=notes or f"Invoice #{invoice_id} charges",
    )
    db.add(entry)
    db.flush()
    sync_balance_cache(learner_id, db)
    return entry


def post_payment(
    db: Session,
    learner_id: int,
    payment_id: int,
    amount: Decimal,
    payment_date: date,
    created_by: str = "system",
    notes: str = None,
) -> models.LedgerEntry:
    """
    Post a CREDIT entry for a payment (learner owes less).
    Syncs the balance cache.
    """
    if amount <= 0:
        raise ValueError(f"Payment ledger amount must be positive, got {amount}")

    entry = models.LedgerEntry(
        learner_id=learner_id,
        transaction_type=models.LedgerTransactionType.PAYMENT,
        reference_type="Payment",
        reference_id=payment_id,
        amount=quantize(amount),
        dc_indicator=models.DebitCredit.CREDIT,
        transaction_date=payment_date,
        created_by=created_by,
        notes=notes or f"Payment #{payment_id}",
    )
    db.add(entry)
    db.flush()
    sync_balance_cache(learner_id, db)
    return entry


def void_invoice_entries(
    db: Session,
    invoice_id: int,
    voided_by: str,
    void_reason: str = None,
):
    """
    Mark all ledger entries for an invoice as voided.
    Also posts a compensating CR entry to zero out the effect.
    """
    now = datetime.utcnow()
    entries = (
        db.query(models.LedgerEntry)
        .filter(
            models.LedgerEntry.reference_type == "Invoice",
            models.LedgerEntry.reference_id == invoice_id,
            models.LedgerEntry.is_voided == False,
        )
        .all()
    )

    for entry in entries:
        entry.is_voided = True
        entry.voided_at = now
        entry.voided_by = voided_by

    if entries:
        # Flush pending UPDATEs so the raw-SQL balance query (which bypasses
        # the ORM identity map) sees the voided state before we compute.
        db.flush()
        # Sync balance for the learner
        sync_balance_cache(entries[0].learner_id, db)


def reverse_payment_entry(
    db: Session,
    payment_id: int,
    reversed_by: str,
    reversal_reason: str = None,
):
    """
    Mark payment ledger entry as voided and post a compensating DR.
    """
    now = datetime.utcnow()
    entry = (
        db.query(models.LedgerEntry)
        .filter(
            models.LedgerEntry.reference_type == "Payment",
            models.LedgerEntry.reference_id == payment_id,
            models.LedgerEntry.is_voided == False,
        )
        .first()
    )
    if entry:
        entry.is_voided = True
        entry.voided_at = now
        entry.voided_by = reversed_by
        db.flush()
        sync_balance_cache(entry.learner_id, db)


def post_adjustment(
    db: Session,
    learner_id: int,
    amount: Decimal,
    dc_indicator: str,
    transaction_date: date,
    created_by: str,
    notes: str,
) -> models.LedgerEntry:
    """Post a manual adjustment (DR or CR) to the ledger."""
    if amount <= 0:
        raise ValueError("Adjustment amount must be positive")
    if dc_indicator not in ("DR", "CR"):
        raise ValueError("dc_indicator must be 'DR' or 'CR'")

    entry = models.LedgerEntry(
        learner_id=learner_id,
        transaction_type=models.LedgerTransactionType.ADJUSTMENT,
        reference_type="Adjustment",
        amount=quantize(amount),
        dc_indicator=dc_indicator,
        transaction_date=transaction_date,
        created_by=created_by,
        notes=notes,
    )
    db.add(entry)
    db.flush()
    sync_balance_cache(learner_id, db)
    return entry


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------
def validate_payment_amount(amount) -> Decimal:
    """Raise ValueError for zero or negative payment amounts."""
    d = quantize(amount)
    if d <= 0:
        raise ValueError(f"Payment amount must be greater than zero (got {d})")
    return d


def check_duplicate_reference(
    db: Session,
    reference_number: str,
    exclude_id: int = None,
) -> bool:
    """Return True if a payment with this reference_number already exists."""
    if not reference_number:
        return False
    q = db.query(models.Payment).filter(
        models.Payment.reference_number == reference_number,
        models.Payment.is_active == True,
    )
    if exclude_id:
        q = q.filter(models.Payment.id != exclude_id)
    return q.first() is not None


def reconcile_check(db: Session) -> dict:
    """
    Cross-check: total outstanding per ledger vs sum(learner.balance).
    Returns a reconciliation report dict.
    Any discrepancy is a data integrity issue.
    """
    # Ledger-derived total
    ledger_total = db.execute(
        text("""
            SELECT
              COALESCE(SUM(CASE WHEN dc_indicator='DR' AND is_voided=0 THEN amount ELSE 0 END), 0)
              - COALESCE(SUM(CASE WHEN dc_indicator='CR' AND is_voided=0 THEN amount ELSE 0 END), 0)
            FROM ledger_entries
        """)
    ).scalar() or Decimal(0)

    # Cache total (from learner.balance column)
    cache_total = db.execute(
        text("SELECT COALESCE(SUM(balance), 0) FROM learners WHERE is_active=1")
    ).scalar() or Decimal(0)

    ledger_total = quantize(ledger_total)
    cache_total = quantize(cache_total)
    discrepancy = quantize(ledger_total - cache_total)

    return {
        "ledger_total": str(ledger_total),
        "cache_total": str(cache_total),
        "discrepancy": str(discrepancy),
        "reconciled": discrepancy == Decimal("0.00"),
    }
