"""
LCCA-IAS v4 ORM Models — Production + V2 Features.

V2 additions:
  - Learner.physical_address
  - Learner.learner_code: 10-digit YYYYMMDDNN format
  - Parent employer fields (employer_name, employer_address, employer_phone, occupation)
  - Invoice.parent_id for parent-centric multi-learner invoices (learner_id now nullable)
  - InvoiceItem.learner_id to tag which child each line item belongs to
  - PasswordResetToken for forgot-password recovery flow
  - Grade 10 added to VALID_GRADES
"""
from datetime import datetime
from decimal import Decimal
from sqlalchemy import (
    Column, Integer, String, Numeric, Date, DateTime, ForeignKey,
    Text, Boolean, Index, UniqueConstraint, CheckConstraint
)
from sqlalchemy.orm import relationship
import enum
from .database import Base

MONETARY = lambda: Numeric(12, 2)

VALID_GRADES = [
    "Baby Class", "Toddler Class", "Grade 0",
    "Grade 1", "Grade 2", "Grade 3", "Grade 4", "Grade 5",
    "Grade 6", "Grade 7", "Grade 8", "Grade 9", "Grade 10",
]

# ---------------------------------------------------------------------------
# Invoice Counter
# ---------------------------------------------------------------------------
class InvoiceCounter(Base):
    __tablename__ = "invoice_counter"
    id = Column(Integer, primary_key=True)
    year = Column(Integer, nullable=False)
    last_sequence = Column(Integer, nullable=False, default=0)

# ---------------------------------------------------------------------------
# Ledger
# ---------------------------------------------------------------------------
class LedgerEntry(Base):
    __tablename__ = "ledger_entries"
    id = Column(Integer, primary_key=True, index=True)
    learner_id = Column(Integer, ForeignKey("learners.id"), nullable=False, index=True)
    transaction_type = Column(String(20), nullable=False)
    reference_type = Column(String(30))
    reference_id = Column(Integer)
    amount = Column(MONETARY(), nullable=False)
    dc_indicator = Column(String(2), nullable=False)
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
    totp_secret = relationship("TotpSecret", back_populates="user", uselist=False, cascade="all, delete-orphan")
    reset_tokens = relationship("PasswordResetToken", back_populates="user", cascade="all, delete-orphan")

# ---------------------------------------------------------------------------
# Learners — V2: adds physical_address; learner_code is now 10-digit YYYYMMDDNN
# ---------------------------------------------------------------------------
class Learner(Base):
    __tablename__ = "learners"
    id = Column(Integer, primary_key=True, index=True)
    learner_code = Column(String(20), unique=True, nullable=False, index=True)
    full_name = Column(String(150), nullable=False)
    grade = Column(String(50), nullable=False)
    class_name = Column(String(50), nullable=False)
    date_of_admission = Column(Date, nullable=False)
    status = Column(String(20), nullable=False, default="Active")
    balance = Column(MONETARY(), nullable=False, default=0)
    id_number = Column(String(30))
    date_of_birth = Column(Date)
    physical_address = Column(Text)                          # V2 NEW
    transport_route_id = Column(Integer, ForeignKey("transport_routes.id"), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    is_active = Column(Boolean, nullable=False, default=True)
    deleted_at = Column(DateTime)
    deleted_by = Column(String(100))

    relationships_ = relationship("LearnerParentRelationship", back_populates="learner", cascade="all, delete-orphan")
    payments = relationship("Payment", back_populates="learner")
    invoices = relationship("Invoice", back_populates="learner", foreign_keys="Invoice.learner_id")
    fee_assignments = relationship("LearnerFeeItem", back_populates="learner", cascade="all, delete-orphan")
    transport_route = relationship("TransportRoute", back_populates="learners")
    ledger_entries = relationship("LedgerEntry", back_populates="learner", cascade="all, delete-orphan")

# ---------------------------------------------------------------------------
# Parents — V2: adds employer fields
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
    preferred_channel = Column(String(20), nullable=False, default="email")
    # Employer details — V2 NEW
    employer_name = Column(String(150))
    employer_address = Column(Text)
    employer_phone = Column(String(30))
    occupation = Column(String(100))
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    is_active = Column(Boolean, nullable=False, default=True)
    deleted_at = Column(DateTime)
    deleted_by = Column(String(100))

    relationships_ = relationship("LearnerParentRelationship", back_populates="parent", cascade="all, delete-orphan")
    invoices = relationship("Invoice", back_populates="parent", foreign_keys="Invoice.parent_id")  # V2 NEW

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
# Fee Structures (v1 legacy)
# ---------------------------------------------------------------------------
class FeeStructure(Base):
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
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

# ---------------------------------------------------------------------------
# Payments
# ---------------------------------------------------------------------------
class Payment(Base):
    __tablename__ = "payments"
    id = Column(Integer, primary_key=True, index=True)
    learner_id = Column(Integer, ForeignKey("learners.id"), nullable=False)
    amount_paid = Column(MONETARY(), nullable=False)
    payment_date = Column(Date, nullable=False)
    date_paid = Column(Date, nullable=False)
    payment_method = Column(String(50), nullable=False)
    reference_number = Column(String(100))
    notes = Column(Text)
    bank_statement_line_id = Column(Integer, ForeignKey("bank_statement_lines.id"), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    created_by = Column(String(100), default="system")
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
# Invoices — V2: parent_id added; learner_id now nullable for multi-learner
# ---------------------------------------------------------------------------
class Invoice(Base):
    __tablename__ = "invoices"
    id = Column(Integer, primary_key=True, index=True)
    invoice_number = Column(String(50), unique=True, nullable=False, index=True)
    # V2: parent_id is the primary billing party for parent-centric invoices
    parent_id = Column(Integer, ForeignKey("parents.id"), nullable=True, index=True)
    learner_id = Column(Integer, ForeignKey("learners.id"), nullable=True)
    fee_structure_id = Column(Integer, ForeignKey("fee_structures.id"), nullable=True)
    month_end_run_id = Column(Integer, ForeignKey("month_end_runs.id"), nullable=True)
    issue_date = Column(Date, nullable=False)
    due_date = Column(Date, nullable=False)
    previous_balance = Column(MONETARY(), nullable=False, default=0)
    current_charges = Column(MONETARY(), nullable=False, default=0)
    payments_made = Column(MONETARY(), nullable=False, default=0)
    outstanding_balance = Column(MONETARY(), nullable=False, default=0)
    status = Column(String(20), nullable=False, default="Generated")
    billing_period = Column(String(20))
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    created_by = Column(String(100), default="system")
    voided_at = Column(DateTime)
    voided_by = Column(String(100))
    void_reason = Column(Text)
    reversed_by_invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=True)

    parent = relationship("Parent", back_populates="invoices", foreign_keys=[parent_id])
    learner = relationship("Learner", back_populates="invoices", foreign_keys=[learner_id])
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
    learner_id = Column(Integer, ForeignKey("learners.id"), nullable=True)  # V2: which child
    description = Column(String(200), nullable=False)
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
# Fee Items & Assignments
# ---------------------------------------------------------------------------
class FeeItem(Base):
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

class TotpSecret(Base):
    __tablename__ = "totp_secrets"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False)
    secret = Column(String(64), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    user = relationship("User", back_populates="totp_secret")

# ---------------------------------------------------------------------------
# Password Reset Tokens — V2 NEW
# ---------------------------------------------------------------------------
class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    token = Column(String(64), unique=True, nullable=False, index=True)
    expires_at = Column(DateTime, nullable=False)
    used = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    requested_from_ip = Column(String(45))
    user = relationship("User", back_populates="reset_tokens")

# ---------------------------------------------------------------------------
# Audit Log
# ---------------------------------------------------------------------------
class AuditLog(Base):
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

# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------
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
    error_message = Column(Text)
    retry_count = Column(Integer, nullable=False, default=0)
    sent_at = Column(DateTime)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    invoice = relationship("Invoice", back_populates="notification_logs")

# ---------------------------------------------------------------------------
# Month-End
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
