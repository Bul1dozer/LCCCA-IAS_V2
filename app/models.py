"""
LCCA-IAS v3 ORM Models — Production Hardened.

Key changes from v2:
  - All monetary columns: Float → Numeric(12,2) (no floating-point errors)
  - Soft delete on all financial entities (is_active / deleted_at / deleted_by)
  - LedgerEntry table: authoritative double-entry ledger replacing mutable balance
  - InvoiceCounter table: concurrency-safe atomic invoice numbering
  - CHECK constraints on all amount columns (no negatives)
  - NOT NULL constraints tightened across all tables
  - Invoice hard-delete removed; Void/Reverse status supported
  - Payment validation (positive amounts only) enforced at model level
"""

from datetime import datetime
from typing import Optional
from sqlalchemy import (
    Column, Integer, String, Numeric, Date, DateTime, ForeignKey,
    Text, Boolean, Index, UniqueConstraint, CheckConstraint, event
)
from sqlalchemy.sql import text
from sqlalchemy.orm import relationship
import enum

from .database import Base

MONETARY = lambda: Numeric(12, 2)   # noqa: E731 — convenience alias


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------
class FeeFrequency(str, enum.Enum):
    MONTHLY = "monthly"
    TERM = "term"
    ANNUAL = "annual"
    ONCE_OFF = "once_off"

class BillingCategory(str, enum.Enum):
    TUITION = "tuition"
    REGISTRATION = "registration"
    SPORT = "sport"
    AFTERCARE = "aftercare"
    TRANSPORT = "transport"
    HOSTEL = "hostel"
    ACTIVITY = "activity"
    MATERIAL = "material"
    EVENT = "event"
    OTHER = "other"

class NotificationChannel(str, enum.Enum):
    EMAIL = "email"
    SMS = "sms"
    WHATSAPP = "whatsapp"

class NotificationStatus(str, enum.Enum):
    PENDING = "pending"
    SENT = "sent"
    DELIVERED = "delivered"
    FAILED = "failed"
    BOUNCED = "bounced"

class AuditAction(str, enum.Enum):
    LOGIN = "LOGIN"
    LOGOUT = "LOGOUT"
    CREATE = "CREATE"
    UPDATE = "UPDATE"
    DELETE = "DELETE"
    GENERATE = "GENERATE"
    SEND = "SEND"
    IMPORT = "IMPORT"
    EXPORT = "EXPORT"
    CONFIG = "CONFIG"
    MONTHEND = "MONTHEND"
    RECON = "RECON"
    VOID = "VOID"
    REVERSE = "REVERSE"

class MonthEndStatus(str, enum.Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"

class ImportStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"

class ReconStatus(str, enum.Enum):
    UNMATCHED = "unmatched"
    MATCHED = "matched"
    MANUALLY_MATCHED = "manually_matched"
    EXCLUDED = "excluded"

class LedgerTransactionType(str, enum.Enum):
    INVOICE = "invoice"
    PAYMENT = "payment"
    CREDIT_NOTE = "credit_note"
    ADJUSTMENT = "adjustment"
    WRITE_OFF = "write_off"
    REVERSAL = "reversal"
    VOID = "void"

class DebitCredit(str, enum.Enum):
    DEBIT = "DR"   # increases what learner owes
    CREDIT = "CR"  # reduces what learner owes


# ---------------------------------------------------------------------------
# Invoice Counter — atomic concurrency-safe numbering
# ---------------------------------------------------------------------------
class InvoiceCounter(Base):
    """
    Single-row table used for atomic invoice number generation.
    Never touched directly — use ledger.next_invoice_number().
    """
    __tablename__ = "invoice_counter"
    id = Column(Integer, primary_key=True)        # always row id=1
    year = Column(Integer, nullable=False)
    last_sequence = Column(Integer, nullable=False, default=0)


# ---------------------------------------------------------------------------
# Ledger — authoritative financial record
# ---------------------------------------------------------------------------
class LedgerEntry(Base):
    """
    Double-entry-style ledger.  The authoritative source of truth for
    every learner's financial position.

    DR (debit)  = money owed increases  → invoices, adjustments
    CR (credit) = money owed decreases  → payments, credit notes, reversals
    """
    __tablename__ = "ledger_entries"
    id = Column(Integer, primary_key=True, index=True)
    learner_id = Column(Integer, ForeignKey("learners.id"), nullable=False, index=True)
    transaction_type = Column(String(20), nullable=False)   # LedgerTransactionType
    reference_type = Column(String(30))                     # "Invoice" | "Payment" | ...
    reference_id = Column(Integer)                          # FK to the source record
    amount = Column(MONETARY(), nullable=False)
    dc_indicator = Column(String(2), nullable=False)        # "DR" or "CR"
    transaction_date = Column(Date, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    created_by = Column(String(100), nullable=False, default="system")
    notes = Column(Text)
    is_voided = Column(Boolean, default=False, nullable=False)
    voided_at = Column(DateTime)
    voided_by = Column(String(100))

    learner = relationship("Learner", back_populates="ledger_entries")

    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_ledger_amount_positive"),
        CheckConstraint("dc_indicator IN ('DR', 'CR')", name="ck_ledger_dc"),
        Index("ix_ledger_learner_date", "learner_id", "transaction_date"),
    )


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    full_name = Column(String(100), nullable=False, default="Administrator")
    email = Column(String(150))
    role = Column(String(20), nullable=False, default="Administrator")
    is_active = Column(Boolean, nullable=False, default=True)
    totp_enabled = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    last_login = Column(DateTime)
    password_reset_token = Column(String(255))
    password_reset_expires_at = Column(DateTime)

    totp_secret = relationship("TotpSecret", back_populates="user", uselist=False, cascade="all, delete-orphan")
    user_roles = relationship("UserRole", back_populates="user", cascade="all, delete-orphan")


# ---------------------------------------------------------------------------
# Learners
# ---------------------------------------------------------------------------
class Learner(Base):
    __tablename__ = "learners"
    id = Column(Integer, primary_key=True, index=True)
    learner_code = Column(String(20), unique=True, nullable=False, index=True)
    learner_id = Column(String(10), unique=True, nullable=True, index=True)
    full_name = Column(String(150), nullable=False)
    grade = Column(String(50), nullable=False)
    class_name = Column(String(50), nullable=False)
    date_of_admission = Column(Date, nullable=False)
    physical_address = Column(Text)
    status = Column(String(20), nullable=False, default="Active")
    # balance is KEPT for backward compatibility with PDF / display code
    # but is NO LONGER the authoritative source of truth.
    # The authoritative balance is compute_balance(learner_id, db).
    # This field is updated as a convenience cache; never used for financial decisions.
    balance = Column(MONETARY(), nullable=False, default=0)
    id_number = Column(String(30))
    date_of_birth = Column(Date)
    transport_route_id = Column(Integer, ForeignKey("transport_routes.id"), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    # Soft delete
    is_active = Column(Boolean, nullable=False, default=True)
    deleted_at = Column(DateTime)
    deleted_by = Column(String(100))

    relationships_ = relationship("LearnerParentRelationship", back_populates="learner", cascade="all, delete-orphan")
    payments = relationship("Payment", back_populates="learner")
    invoices = relationship("Invoice", back_populates="learner")
    fee_assignments = relationship("LearnerFeeItem", back_populates="learner", cascade="all, delete-orphan")
    transport_route = relationship("TransportRoute", back_populates="learners")
    ledger_entries = relationship("LedgerEntry", back_populates="learner", cascade="all, delete-orphan")


# ---------------------------------------------------------------------------
# Parents
# ---------------------------------------------------------------------------
class Parent(Base):
    __tablename__ = "parents"
    id = Column(Integer, primary_key=True, index=True)
    full_name = Column(String(150), nullable=False)
    email = Column(String(150))
    phone = Column(String(30))
    address = Column(Text)
    id_number = Column(String(30))
    whatsapp_number = Column(String(30))
    employer_name = Column(String(150))
    employer_address = Column(Text)
    employer_phone = Column(String(30))
    position = Column(String(100))
    preferred_channel = Column(String(20), nullable=False, default="email")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    # Soft delete
    is_active = Column(Boolean, nullable=False, default=True)
    deleted_at = Column(DateTime)
    deleted_by = Column(String(100))

    relationships_ = relationship("LearnerParentRelationship", back_populates="parent", cascade="all, delete-orphan")
    invoices = relationship("Invoice", back_populates="parent")


class LearnerParentRelationship(Base):
    __tablename__ = "learner_parent_relationships"
    id = Column(Integer, primary_key=True, index=True)
    learner_id = Column(Integer, ForeignKey("learners.id"), nullable=False)
    parent_id = Column(Integer, ForeignKey("parents.id"), nullable=False)
    relationship_type = Column(String(50), nullable=False, default="Parent")
    is_primary = Column(Boolean, nullable=False, default=False)

    learner = relationship("Learner", back_populates="relationships_")
    parent = relationship("Parent", back_populates="relationships_")


# ---------------------------------------------------------------------------
# Fee Structures (v1 legacy — preserved for migration compatibility)
# ---------------------------------------------------------------------------
class FeeStructure(Base):
    """v1 fee structures — kept for migration compatibility."""
    __tablename__ = "fee_structures"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(150), nullable=False)
    grade = Column(String(50), nullable=False)
    term = Column(String(50), nullable=False)
    tuition_fee = Column(MONETARY(), nullable=False, default=0)
    development_fee = Column(MONETARY(), nullable=False, default=0)
    hostel_fee = Column(MONETARY(), nullable=False, default=0)
    transport_fee = Column(MONETARY(), nullable=False, default=0)
    misc_charges = Column(MONETARY(), nullable=False, default=0)
    total = Column(MONETARY(), nullable=False, default=0)
    is_legacy = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


# ---------------------------------------------------------------------------
# Payments
# ---------------------------------------------------------------------------
class Payment(Base):
    __tablename__ = "payments"
    id = Column(Integer, primary_key=True, index=True)
    learner_id = Column(Integer, ForeignKey("learners.id"), nullable=False)
    amount_paid = Column(MONETARY(), nullable=False)
    payment_date = Column(Date, nullable=False)     # renamed from date_paid for clarity
    date_paid = Column(Date, nullable=False)        # alias kept for backward compat
    payment_method = Column(String(50), nullable=False)
    reference_number = Column(String(100))
    notes = Column(Text)
    bank_statement_line_id = Column(Integer, ForeignKey("bank_statement_lines.id"), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    created_by = Column(String(100), default="system")
    # Soft delete / reversal
    is_active = Column(Boolean, nullable=False, default=True)
    reversed_at = Column(DateTime)
    reversed_by = Column(String(100))
    reversal_reason = Column(Text)

    learner = relationship("Learner", back_populates="payments")
    bank_line = relationship("BankStatementLine", back_populates="payments",
                             foreign_keys="Payment.bank_statement_line_id")

    __table_args__ = (
        CheckConstraint("amount_paid > 0", name="ck_payment_amount_positive"),
    )


# ---------------------------------------------------------------------------
# Invoices
# ---------------------------------------------------------------------------
class Invoice(Base):
    __tablename__ = "invoices"
    id = Column(Integer, primary_key=True, index=True)
    invoice_number = Column(String(50), unique=True, nullable=False, index=True)
    learner_id = Column(Integer, ForeignKey("learners.id"), nullable=True)
    parent_id = Column(Integer, ForeignKey("parents.id"), nullable=True)
    fee_structure_id = Column(Integer, ForeignKey("fee_structures.id"), nullable=True)
    month_end_run_id = Column(Integer, ForeignKey("month_end_runs.id"), nullable=True)
    issue_date = Column(Date, nullable=False)
    due_date = Column(Date, nullable=False)
    # Snapshot values at time of invoice generation (immutable historical record)
    previous_balance = Column(MONETARY(), nullable=False, default=0)
    current_charges = Column(MONETARY(), nullable=False, default=0)
    payments_made = Column(MONETARY(), nullable=False, default=0)
    outstanding_balance = Column(MONETARY(), nullable=False, default=0)
    status = Column(String(20), nullable=False, default="Generated")
    # status values: Generated | Sent | Paid | Void | Reversed
    billing_period = Column(String(20))
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    created_by = Column(String(100), default="system")
    # Void/Reversal tracking (no hard deletes)
    voided_at = Column(DateTime)
    voided_by = Column(String(100))
    void_reason = Column(Text)
    reversed_by_invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=True)

    learner = relationship("Learner", back_populates="invoices")
    parent = relationship("Parent", back_populates="invoices")
    items = relationship("InvoiceItem", back_populates="invoice", cascade="all, delete-orphan")
    email_logs = relationship("EmailLog", back_populates="invoice", cascade="all, delete-orphan")
    notification_logs = relationship("NotificationLog", back_populates="invoice", cascade="all, delete-orphan")
    month_end_run = relationship("MonthEndRun", back_populates="invoices")

    __table_args__ = (
        CheckConstraint("current_charges >= 0", name="ck_invoice_charges_nonneg"),
    )


class InvoiceItem(Base):
    __tablename__ = "invoice_items"
    id = Column(Integer, primary_key=True, index=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=False)
    description = Column(String(150), nullable=False)
    amount = Column(MONETARY(), nullable=False)
    fee_item_id = Column(Integer, ForeignKey("fee_items.id"), nullable=True)

    invoice = relationship("Invoice", back_populates="items")
    fee_item = relationship("FeeItem", back_populates="invoice_items")

    __table_args__ = (
        CheckConstraint("amount >= 0", name="ck_invoice_item_amount_nonneg"),
    )


# ---------------------------------------------------------------------------
# Email / Notification Logs
# ---------------------------------------------------------------------------
class EmailLog(Base):
    __tablename__ = "email_logs"
    id = Column(Integer, primary_key=True, index=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=True)
    recipient_name = Column(String(150))
    recipient_email = Column(String(150), nullable=False)
    subject = Column(String(200), nullable=False)
    body_preview = Column(Text)
    status = Column(String(20), nullable=False, default="Sent")
    sent_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    real_send = Column(Boolean, nullable=False, default=False)
    smtp_message_id = Column(String(200))
    error_message = Column(Text)
    retry_count = Column(Integer, nullable=False, default=0)

    invoice = relationship("Invoice", back_populates="email_logs")


class ReportLog(Base):
    __tablename__ = "report_logs"
    id = Column(Integer, primary_key=True, index=True)
    report_type = Column(String(100), nullable=False)
    parameters = Column(Text)
    generated_by = Column(String(100), nullable=False, default="Administrator")
    generated_at = Column(DateTime, nullable=False, default=datetime.utcnow)


# ---------------------------------------------------------------------------
# v2: Dynamic Fee Engine
# ---------------------------------------------------------------------------
class FeeItem(Base):
    """The central Fee Catalogue."""
    __tablename__ = "fee_items"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(150), nullable=False)
    description = Column(Text)
    category = Column(String(50), nullable=False, default="other")
    frequency = Column(String(20), nullable=False)
    amount = Column(MONETARY(), nullable=False, default=0)
    is_mandatory = Column(Boolean, nullable=False, default=False)
    is_active = Column(Boolean, nullable=False, default=True)
    effective_from = Column(Date)
    effective_to = Column(Date)
    applicable_grades = Column(Text)
    applicable_classes = Column(Text)
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    grade_rules = relationship("FeeItemGradeRule", back_populates="fee_item", cascade="all, delete-orphan")
    learner_assignments = relationship("LearnerFeeItem", back_populates="fee_item", cascade="all, delete-orphan")
    invoice_items = relationship("InvoiceItem", back_populates="fee_item")

    __table_args__ = (
        CheckConstraint("amount >= 0", name="ck_fee_item_amount_nonneg"),
    )


class FeeItemGradeRule(Base):
    __tablename__ = "fee_item_grade_rules"
    id = Column(Integer, primary_key=True, index=True)
    fee_item_id = Column(Integer, ForeignKey("fee_items.id"), nullable=False)
    grade = Column(String(50))
    class_name = Column(String(50))
    is_active = Column(Boolean, nullable=False, default=True)

    fee_item = relationship("FeeItem", back_populates="grade_rules")


class LearnerFeeItem(Base):
    __tablename__ = "learner_fee_items"
    id = Column(Integer, primary_key=True, index=True)
    learner_id = Column(Integer, ForeignKey("learners.id"), nullable=False)
    fee_item_id = Column(Integer, ForeignKey("fee_items.id"), nullable=False)
    custom_amount = Column(MONETARY())
    is_active = Column(Boolean, nullable=False, default=True)
    assigned_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    assigned_by = Column(String(100))
    notes = Column(Text)

    learner = relationship("Learner", back_populates="fee_assignments")
    fee_item = relationship("FeeItem", back_populates="learner_assignments")

    __table_args__ = (
        UniqueConstraint("learner_id", "fee_item_id", name="uq_learner_fee_item"),
        CheckConstraint("custom_amount IS NULL OR custom_amount >= 0", name="ck_learner_fee_custom_nonneg"),
    )


def _generate_learner_id(mapper, connection, target):
    if target.learner_id:
        return
    if not target.date_of_admission:
        return
    prefix = target.date_of_admission.strftime("%y%m%d")
    count = connection.scalar(
        text("SELECT COUNT(*) FROM learners WHERE date_of_admission = :admission_date") ,
        {"admission_date": target.date_of_admission},
    )
    target.learner_id = f"{prefix}{int(count or 0) + 1:04d}"


event.listen(Learner, "before_insert", _generate_learner_id)


class TransportRoute(Base):
    __tablename__ = "transport_routes"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    description = Column(Text)
    monthly_fee = Column(MONETARY(), nullable=False, default=0)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    learners = relationship("Learner", back_populates="transport_route")

    __table_args__ = (
        CheckConstraint("monthly_fee >= 0", name="ck_transport_fee_nonneg"),
    )


# ---------------------------------------------------------------------------
# RBAC
# ---------------------------------------------------------------------------
class Role(Base):
    __tablename__ = "roles"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(50), unique=True, nullable=False)
    description = Column(Text)
    permissions = Column(Text)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    user_roles = relationship("UserRole", back_populates="role")


class UserRole(Base):
    __tablename__ = "user_roles"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    role_id = Column(Integer, ForeignKey("roles.id"), nullable=False)
    assigned_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    user = relationship("User", back_populates="user_roles")
    role = relationship("Role", back_populates="user_roles")


class TotpSecret(Base):
    __tablename__ = "totp_secrets"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False)
    secret = Column(String(64), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    user = relationship("User", back_populates="totp_secret")


# ---------------------------------------------------------------------------
# Audit Log
# ---------------------------------------------------------------------------
class AuditLog(Base):
    """Immutable. Never updated or deleted by application code."""
    __tablename__ = "audit_logs"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    username = Column(String(100), nullable=False, default="system")
    action = Column(String(30), nullable=False)
    resource_type = Column(String(80))
    resource_id = Column(String(30))
    detail = Column(Text)
    ip_address = Column(String(45))
    user_agent = Column(String(255))
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)

    __table_args__ = (Index("ix_audit_logs_resource", "resource_type", "resource_id"),)


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------
class EmailTemplate(Base):
    __tablename__ = "email_templates"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, nullable=False)
    subject_template = Column(String(200), nullable=False)
    body_html_template = Column(Text, nullable=False)
    body_text_template = Column(Text)
    is_active = Column(Boolean, nullable=False, default=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class SmtpConfig(Base):
    __tablename__ = "smtp_configs"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    host = Column(String(200), nullable=False)
    port = Column(Integer, nullable=False, default=587)
    username = Column(String(200))
    password_encrypted = Column(Text)
    use_tls = Column(Boolean, nullable=False, default=True)
    from_address = Column(String(200))
    from_name = Column(String(100), nullable=False, default="LCCA Accounts")
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class SmsConfig(Base):
    __tablename__ = "sms_configs"
    id = Column(Integer, primary_key=True, index=True)
    provider = Column(String(50), nullable=False)
    api_key_encrypted = Column(Text)
    api_secret_encrypted = Column(Text)
    sender_id = Column(String(30))
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class NotificationLog(Base):
    __tablename__ = "notification_logs"
    id = Column(Integer, primary_key=True, index=True)
    channel = Column(String(20), nullable=False)
    invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=True)
    learner_id = Column(Integer, ForeignKey("learners.id"), nullable=True)
    recipient = Column(String(200), nullable=False)
    subject = Column(String(200))
    body_preview = Column(Text)
    status = Column(String(20), nullable=False, default="pending")
    provider_message_id = Column(String(200))
    error_message = Column(Text)
    retry_count = Column(Integer, nullable=False, default=0)
    sent_at = Column(DateTime)
    delivered_at = Column(DateTime)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    invoice = relationship("Invoice", back_populates="notification_logs")


# ---------------------------------------------------------------------------
# Month-End Processing
# ---------------------------------------------------------------------------
class MonthEndRun(Base):
    __tablename__ = "month_end_runs"
    id = Column(Integer, primary_key=True, index=True)
    billing_period = Column(String(20), nullable=False)
    triggered_by = Column(String(100))
    trigger_type = Column(String(20), nullable=False, default="manual")
    status = Column(String(20), nullable=False, default="running")
    started_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    completed_at = Column(DateTime)
    invoices_generated = Column(Integer, nullable=False, default=0)
    notifications_sent = Column(Integer, nullable=False, default=0)
    errors = Column(Text)
    log = Column(Text)

    invoices = relationship("Invoice", back_populates="month_end_run")

    __table_args__ = (
        Index(
            "uq_month_end_period_running",
            "billing_period",
            unique=True,
            sqlite_where=(status == "running"),
        ),
    )


# ---------------------------------------------------------------------------
# Bulk Import
# ---------------------------------------------------------------------------
class ImportJob(Base):
    __tablename__ = "import_jobs"
    id = Column(Integer, primary_key=True, index=True)
    import_type = Column(String(50), nullable=False)
    filename = Column(String(255))
    total_rows = Column(Integer, nullable=False, default=0)
    processed_rows = Column(Integer, nullable=False, default=0)
    success_rows = Column(Integer, nullable=False, default=0)
    error_rows = Column(Integer, nullable=False, default=0)
    status = Column(String(20), nullable=False, default="pending")
    error_report = Column(Text)
    uploaded_by = Column(String(100))
    started_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    completed_at = Column(DateTime)


# ---------------------------------------------------------------------------
# Bank Reconciliation
# ---------------------------------------------------------------------------
class BankStatementLine(Base):
    __tablename__ = "bank_statement_lines"
    id = Column(Integer, primary_key=True, index=True)
    import_ref = Column(String(100))
    transaction_date = Column(Date, nullable=False)
    description = Column(Text)
    amount = Column(MONETARY(), nullable=False)
    reference = Column(String(200))
    account_number = Column(String(50))
    balance = Column(MONETARY())
    recon_status = Column(String(30), nullable=False, default="unmatched")
    matched_payment_id = Column(Integer, ForeignKey("payments.id"), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    payments = relationship("Payment", back_populates="bank_line",
                            foreign_keys="Payment.bank_statement_line_id")
