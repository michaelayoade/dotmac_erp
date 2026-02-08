"""
AR Schemas.

Pydantic schemas for Accounts Receivable APIs.
Field names match template forms for seamless UI integration.
"""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# =============================================================================
# Customer
# =============================================================================


class CustomerBase(BaseModel):
    """
    Base customer schema with template-friendly field names.

    Input schemas (Create/Update) use field names directly without validation_alias.
    This allows API clients to send template-friendly names (customer_name, tax_id, etc.)
    """

    customer_code: str | None = Field(default=None, max_length=30)
    customer_type: str = Field(default="COMPANY", max_length=20)
    customer_name: str = Field(
        max_length=255
    )  # Template name (service maps to legal_name)
    trading_name: str | None = Field(default=None, max_length=255)
    tax_id: str | None = Field(
        default=None, max_length=50
    )  # Template name (service maps to tax_identification_number)
    vat_category: str | None = Field(default=None, max_length=50)
    payment_terms_days: int = Field(
        default=30
    )  # Template name (service maps to credit_terms_days)
    credit_limit: Decimal | None = None
    currency_code: str = Field(default="NGN", max_length=3)
    default_revenue_account_id: UUID | None = None
    default_tax_code_id: UUID | None = None
    default_receivable_account_id: UUID | None = (
        None  # Template name (service maps to ar_control_account_id)
    )
    is_active: bool = True
    # Additional template fields
    email: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=50)
    address: str | None = Field(default=None, max_length=500)


class CustomerCreate(CustomerBase):
    """Create customer request."""

    pass


class CustomerUpdate(BaseModel):
    """Update customer request."""

    customer_name: str | None = Field(default=None, max_length=255)
    trading_name: str | None = Field(default=None, max_length=255)
    tax_id: str | None = Field(default=None, max_length=50)
    vat_category: str | None = Field(default=None, max_length=50)
    default_tax_code_id: UUID | None = None
    payment_terms_days: int | None = None
    credit_limit: Decimal | None = None
    is_active: bool | None = None
    email: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=50)
    address: str | None = Field(default=None, max_length=500)


class CustomerRead(BaseModel):
    """Customer response with template-friendly field names."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    customer_id: UUID
    organization_id: UUID
    customer_code: str
    customer_type: str
    customer_name: str = Field(validation_alias="legal_name")
    trading_name: str | None = None
    tax_id: str | None = Field(
        default=None, validation_alias="tax_identification_number"
    )
    vat_category: str | None = None
    payment_terms_days: int = Field(validation_alias="credit_terms_days")
    credit_limit: Decimal | None = None
    currency_code: str
    default_revenue_account_id: UUID | None = None
    default_receivable_account_id: UUID | None = Field(
        default=None, validation_alias="ar_control_account_id"
    )
    default_tax_code_id: UUID | None = None
    is_active: bool
    created_at: datetime
    updated_at: datetime | None = None


# =============================================================================
# AR Invoice
# =============================================================================


class ARInvoiceLineCreate(BaseModel):
    """AR invoice line for creation."""

    revenue_account_id: UUID
    item_id: UUID | None = None
    description: str = Field(max_length=500)
    quantity: Decimal = Field(default=Decimal("1"))
    unit_price: Decimal
    tax_code_id: UUID | None = None
    cost_center_id: UUID | None = None
    project_id: UUID | None = None


class ARInvoiceCreate(BaseModel):
    """Create AR invoice request."""

    customer_id: UUID
    invoice_date: date
    due_date: date
    currency_code: str = Field(max_length=3)
    description: str | None = None
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
    customer_name: str | None = None
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
    reference_number: str | None = None
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
    original_invoice_id: UUID | None = None
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
