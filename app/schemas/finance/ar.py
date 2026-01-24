"""
AR Schemas.

Pydantic schemas for Accounts Receivable APIs.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# =============================================================================
# Customer
# =============================================================================

class CustomerBase(BaseModel):
    """Base customer schema."""

    customer_code: str = Field(max_length=30)
    customer_type: str = Field(default="corporate", max_length=20)
    legal_name: str = Field(max_length=255)
    trading_name: Optional[str] = Field(default=None, max_length=255)
    tax_identification_number: Optional[str] = Field(default=None, max_length=50)
    credit_terms_days: int = 30
    credit_limit: Optional[Decimal] = None
    currency_code: str = Field(default="NGN", max_length=3)
    default_revenue_account_id: Optional[UUID] = None
    ar_control_account_id: Optional[UUID] = None
    is_active: bool = True


class CustomerCreate(CustomerBase):
    """Create customer request."""

    pass


class CustomerUpdate(BaseModel):
    """Update customer request."""

    legal_name: Optional[str] = Field(default=None, max_length=255)
    trading_name: Optional[str] = Field(default=None, max_length=255)
    credit_terms_days: Optional[int] = None
    credit_limit: Optional[Decimal] = None
    is_active: Optional[bool] = None


class CustomerRead(CustomerBase):
    """Customer response."""

    model_config = ConfigDict(from_attributes=True)

    customer_id: UUID
    organization_id: UUID
    created_at: datetime
    updated_at: Optional[datetime] = None


# =============================================================================
# AR Invoice
# =============================================================================

class ARInvoiceLineCreate(BaseModel):
    """AR invoice line for creation."""

    revenue_account_id: UUID
    description: str = Field(max_length=500)
    quantity: Decimal = Field(default=Decimal("1"))
    unit_price: Decimal
    tax_code_id: Optional[UUID] = None
    cost_center_id: Optional[UUID] = None
    project_id: Optional[UUID] = None


class ARInvoiceCreate(BaseModel):
    """Create AR invoice request."""

    customer_id: UUID
    invoice_date: date
    due_date: date
    currency_code: str = Field(max_length=3)
    description: Optional[str] = None
    lines: list[ARInvoiceLineCreate]


class ARInvoiceLineRead(BaseModel):
    """AR invoice line response."""

    model_config = ConfigDict(from_attributes=True)

    line_id: UUID
    line_number: int
    revenue_account_id: UUID
    description: str
    quantity: Decimal
    unit_price: Decimal
    line_amount: Decimal
    tax_amount: Decimal
    total_amount: Decimal


class ARInvoiceRead(BaseModel):
    """AR invoice response."""

    model_config = ConfigDict(from_attributes=True)

    invoice_id: UUID
    organization_id: UUID
    customer_id: UUID
    customer_name: Optional[str] = None
    invoice_number: str
    invoice_date: date
    due_date: date
    currency_code: str
    subtotal: Decimal
    tax_amount: Decimal
    total_amount: Decimal
    amount_paid: Decimal
    status: str
    posting_status: str
    created_at: datetime


# =============================================================================
# AR Receipt
# =============================================================================

class ReceiptAllocationCreate(BaseModel):
    """Receipt allocation to invoice."""

    invoice_id: UUID
    amount: Decimal


class ARReceiptCreate(BaseModel):
    """Create AR receipt request."""

    customer_id: UUID
    receipt_date: date
    payment_method: str = Field(max_length=30)
    bank_account_id: UUID
    currency_code: str = Field(max_length=3)
    reference_number: Optional[str] = None
    allocations: list[ReceiptAllocationCreate]


class ARReceiptRead(BaseModel):
    """AR receipt response."""

    model_config = ConfigDict(from_attributes=True)

    payment_id: UUID
    organization_id: UUID
    customer_id: UUID
    payment_number: str
    payment_date: date
    payment_method: str
    gross_amount: Decimal
    amount: Decimal
    currency_code: str
    status: str
    created_at: datetime


# =============================================================================
# AR Aging
# =============================================================================

class ARAgingBucketRead(BaseModel):
    """AR aging bucket."""

    customer_id: UUID
    customer_code: str
    customer_name: str
    current: Decimal
    days_1_30: Decimal
    days_31_60: Decimal
    days_61_90: Decimal
    over_90: Decimal
    total: Decimal


class ARAgingReportRead(BaseModel):
    """AR aging report."""

    as_of_date: date
    currency_code: str
    buckets: list[ARAgingBucketRead]
    totals: ARAgingBucketRead


# =============================================================================
# Credit Note
# =============================================================================

class CreditNoteCreate(BaseModel):
    """Create credit note request."""

    customer_id: UUID
    original_invoice_id: Optional[UUID] = None
    credit_date: date
    reason: str = Field(max_length=500)
    lines: list[ARInvoiceLineCreate]


class CreditNoteRead(BaseModel):
    """Credit note response."""

    model_config = ConfigDict(from_attributes=True)

    credit_note_id: UUID
    organization_id: UUID
    customer_id: UUID
    credit_note_number: str
    credit_date: date
    total_amount: Decimal
    status: str
    created_at: datetime


__all__ = [
    "CustomerCreate",
    "CustomerUpdate",
    "CustomerRead",
    "ARInvoiceLineCreate",
    "ARInvoiceCreate",
    "ARInvoiceLineRead",
    "ARInvoiceRead",
    "ReceiptAllocationCreate",
    "ARReceiptCreate",
    "ARReceiptRead",
    "ARAgingBucketRead",
    "ARAgingReportRead",
    "CreditNoteCreate",
    "CreditNoteRead",
]
