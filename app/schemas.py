"""
Pydantic schemas (request / response models) for LCCA-IAS v3.
All monetary fields use Decimal instead of float.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Optional, List
from pydantic import BaseModel, ConfigDict, field_validator


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
class LoginRequest(BaseModel):
    username: str
    password: str


class CurrentUser(BaseModel):
    id: int
    username: str
    full_name: str
    role: str


class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str


class PasswordResetRequest(BaseModel):
    username: str


class PasswordResetConfirmRequest(BaseModel):
    token: str
    new_password: str


# ---------------------------------------------------------------------------
# Learners
# ---------------------------------------------------------------------------
class LearnerBase(BaseModel):
    full_name: str
    grade: str
    class_name: str
    date_of_admission: date
    status: str = "Active"
    physical_address: Optional[str] = None


class LearnerCreate(LearnerBase):
    pass


class LearnerUpdate(LearnerBase):
    pass


class LearnerOut(LearnerBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    learner_code: str
    learner_id: Optional[str] = None
    balance: Decimal
    created_at: datetime


class ParentMini(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    full_name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    relationship_type: Optional[str] = None
    relationship_id: Optional[int] = None


class LearnerDetail(LearnerOut):
    parents: List[ParentMini] = []


# ---------------------------------------------------------------------------
# Parents
# ---------------------------------------------------------------------------
class ParentBase(BaseModel):
    full_name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    employer_name: Optional[str] = None
    employer_address: Optional[str] = None
    employer_phone: Optional[str] = None
    position: Optional[str] = None


class ParentCreate(ParentBase):
    pass


class ParentUpdate(ParentBase):
    pass


class ParentOut(ParentBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: datetime


class LearnerMini(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    learner_code: str
    full_name: str
    grade: str
    class_name: str
    relationship_type: Optional[str] = None
    relationship_id: Optional[int] = None


class ParentDetail(ParentOut):
    learners: List[LearnerMini] = []


class LinkLearnerRequest(BaseModel):
    learner_id: int
    relationship_type: str = "Parent"


# ---------------------------------------------------------------------------
# Fee Structures (v1 compat)
# ---------------------------------------------------------------------------
class FeeStructureBase(BaseModel):
    name: str
    grade: str
    term: str
    tuition_fee: Decimal = Decimal("0.00")
    development_fee: Decimal = Decimal("0.00")
    hostel_fee: Decimal = Decimal("0.00")
    transport_fee: Decimal = Decimal("0.00")
    misc_charges: Decimal = Decimal("0.00")


class FeeStructureCreate(FeeStructureBase):
    pass


class FeeStructureUpdate(FeeStructureBase):
    pass


class FeeStructureOut(FeeStructureBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    total: Decimal
    created_at: datetime


# ---------------------------------------------------------------------------
# Payments
# ---------------------------------------------------------------------------
class PaymentBase(BaseModel):
    learner_id: int
    amount_paid: Decimal
    date_paid: date
    payment_method: str
    reference_number: Optional[str] = None
    notes: Optional[str] = None

    @field_validator("amount_paid")
    @classmethod
    def amount_must_be_positive(cls, v):
        if v <= 0:
            raise ValueError("Payment amount must be greater than zero")
        return v


class PaymentCreate(PaymentBase):
    pass


class PaymentUpdate(BaseModel):
    amount_paid: Decimal
    date_paid: date
    payment_method: str
    reference_number: Optional[str] = None
    notes: Optional[str] = None

    @field_validator("amount_paid")
    @classmethod
    def amount_must_be_positive(cls, v):
        if v <= 0:
            raise ValueError("Payment amount must be greater than zero")
        return v


class PaymentOut(PaymentBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: datetime
    learner_name: Optional[str] = None
    learner_code: Optional[str] = None
    is_active: bool = True


# ---------------------------------------------------------------------------
# Invoices
# ---------------------------------------------------------------------------
class InvoiceItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    description: str
    amount: Decimal


class GenerateInvoiceRequest(BaseModel):
    learner_id: Optional[int] = None
    parent_id: Optional[int] = None
    fee_structure_id: Optional[int] = None
    due_date: date


class VoidInvoiceRequest(BaseModel):
    reason: str = "Voided by administrator"


class InvoiceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    invoice_number: str
    learner_id: Optional[int] = None
    parent_id: Optional[int] = None
    fee_structure_id: Optional[int] = None
    issue_date: date
    due_date: date
    previous_balance: Decimal
    current_charges: Decimal
    payments_made: Decimal
    outstanding_balance: Decimal
    status: str
    created_at: datetime
    learner_name: Optional[str] = None
    learner_code: Optional[str] = None
    parent_name: Optional[str] = None
    items: List[InvoiceItemOut] = []
    voided_at: Optional[datetime] = None
    void_reason: Optional[str] = None


# ---------------------------------------------------------------------------
# Email Logs
# ---------------------------------------------------------------------------
class SendEmailRequest(BaseModel):
    recipient_email: Optional[str] = None


class EmailLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    invoice_id: Optional[int] = None
    recipient_name: Optional[str] = None
    recipient_email: str
    subject: str
    body_preview: Optional[str] = None
    status: str
    sent_at: datetime
    invoice_number: Optional[str] = None
    learner_name: Optional[str] = None


# ---------------------------------------------------------------------------
# Ledger
# ---------------------------------------------------------------------------
class LedgerEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    transaction_type: str
    reference_type: Optional[str] = None
    reference_id: Optional[int] = None
    amount: Decimal
    dc_indicator: str
    transaction_date: date
    created_at: datetime
    created_by: str
    notes: Optional[str] = None
    is_voided: bool


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
class DashboardStats(BaseModel):
    total_learners: int
    total_parents: int
    total_outstanding_balance: Decimal
    total_payments_received: Decimal
    invoices_generated: int
    invoices_sent: int
    active_learners: int
    collection_rate: Decimal


class ActivityItem(BaseModel):
    type: str
    description: str
    timestamp: datetime
    icon: str


class MonthlyCollection(BaseModel):
    month: str
    total: Decimal


class GradeDistribution(BaseModel):
    grade: str
    count: int


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------
class ReconciliationReport(BaseModel):
    ledger_total: Decimal
    cache_total: Decimal
    discrepancy: Decimal
    reconciled: bool
