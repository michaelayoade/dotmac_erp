"""
AP Schemas.

Pydantic schemas for Accounts Payable APIs.
Field names match template forms for seamless UI integration.
"""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# =============================================================================
# Supplier
# =============================================================================


class SupplierBase(BaseModel):
    """
    Base supplier schema with template-friendly field names.

    Input schemas (Create/Update) use field names directly without validation_alias.
    This allows API clients to send template-friendly names (supplier_name, tax_id, etc.)
    """

    supplier_code: str = Field(max_length=30)
    supplier_type: str = Field(default="VENDOR", max_length=30)
    supplier_name: str = Field(
        max_length=255
    )  # Template name (service maps to legal_name)
    trading_name: str | None = Field(default=None, max_length=255)
    tax_id: str | None = Field(
        default=None, max_length=50
    )  # Template name (service maps to tax_identification_number)
    payment_terms_days: int = 30
    currency_code: str = Field(default="NGN", max_length=3)
    default_expense_account_id: UUID | None = None
    default_payable_account_id: UUID | None = (
        None  # Template name (service maps to ap_control_account_id)
    )
    is_active: bool = True
    # Additional template fields
    email: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=50)
    address: str | None = Field(default=None, max_length=500)
    payment_method: str | None = Field(
        default=None, max_length=30
    )  # BANK_TRANSFER, CHECK, WIRE, CASH


class SupplierCreate(SupplierBase):
    """Create supplier request."""

    pass


class SupplierUpdate(BaseModel):
    """Update supplier request."""

    supplier_name: str | None = Field(default=None, max_length=255)
    trading_name: str | None = Field(default=None, max_length=255)
    tax_id: str | None = Field(default=None, max_length=50)
    payment_terms_days: int | None = None
    is_active: bool | None = None
    email: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=50)
    address: str | None = Field(default=None, max_length=500)
    payment_method: str | None = Field(default=None, max_length=30)


class SupplierRead(BaseModel):
    """Supplier response with template-friendly field names."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    supplier_id: UUID
    organization_id: UUID
    supplier_code: str
    supplier_type: str
    supplier_name: str = Field(validation_alias="legal_name")
    trading_name: str | None = None
    tax_id: str | None = Field(
        default=None, validation_alias="tax_identification_number"
    )
    payment_terms_days: int
    currency_code: str
    default_expense_account_id: UUID | None = None
    default_payable_account_id: UUID | None = Field(
        default=None, validation_alias="ap_control_account_id"
    )
    is_active: bool
    created_at: datetime
    updated_at: datetime | None = None


# =============================================================================
# AP Invoice
# =============================================================================


class APInvoiceLineCreate(BaseModel):
    """AP invoice line for creation."""

    expense_account_id: UUID
    description: str = Field(max_length=500)
    quantity: Decimal = Field(default=Decimal("1"))
    unit_price: Decimal
    tax_code_id: UUID | None = None
    cost_center_id: UUID | None = None
    project_id: UUID | None = None


class APInvoiceCreate(BaseModel):
    """Create AP invoice request."""

    supplier_id: UUID
    invoice_type: str = Field(default="standard", max_length=20)
    invoice_number: str = Field(max_length=50)
    invoice_date: date
    received_date: date | None = None
    due_date: date
    currency_code: str = Field(max_length=3)
    description: str | None = None
    lines: list[APInvoiceLineCreate] = Field(min_length=1)


class APInvoiceLineRead(BaseModel):
    """AP invoice line response."""

    model_config = ConfigDict(from_attributes=True)

    line_id: UUID
    line_number: int
    expense_account_id: UUID
    description: str
    quantity: Decimal
    unit_price: Decimal
    line_amount: Decimal
    tax_amount: Decimal
    total_amount: Decimal


class APInvoiceRead(BaseModel):
    """AP invoice response."""

    model_config = ConfigDict(from_attributes=True)

    invoice_id: UUID
    organization_id: UUID
    supplier_id: UUID
    invoice_number: str
    supplier_invoice_number: str | None = None
    invoice_type: str
    invoice_date: date
    received_date: date
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
# AP Payment
# =============================================================================


class PaymentAllocationCreate(BaseModel):
    """Payment allocation to invoice."""

    invoice_id: UUID
    amount: Decimal


class APPaymentCreate(BaseModel):
    """Create AP payment request."""

    supplier_id: UUID
    payment_date: date
    payment_method: str = Field(max_length=30)
    bank_account_id: UUID
    currency_code: str = Field(max_length=3)
    reference_number: str | None = None
    allocations: list[PaymentAllocationCreate] = Field(min_length=1)


class APPaymentRead(BaseModel):
    """AP payment response."""

    model_config = ConfigDict(from_attributes=True)

    payment_id: UUID
    organization_id: UUID
    supplier_id: UUID
    payment_number: str
    payment_date: date
    payment_method: str
    amount: Decimal
    currency_code: str
    status: str
    created_at: datetime


# =============================================================================
# AP Aging
# =============================================================================


class APAgingBucketRead(BaseModel):
    """AP aging bucket."""

    supplier_id: UUID
    supplier_code: str
    supplier_name: str
    current: Decimal
    days_1_30: Decimal
    days_31_60: Decimal
    days_61_90: Decimal
    over_90: Decimal
    total: Decimal


class APAgingReportRead(BaseModel):
    """AP aging report."""

    as_of_date: date
    currency_code: str
    buckets: list[APAgingBucketRead]
    totals: APAgingBucketRead


# =============================================================================
# Purchase Order
# =============================================================================


class POLineCreate(BaseModel):
    """PO line input."""

    item_id: UUID | None = None
    description: str
    quantity: Decimal
    unit_price: Decimal
    expense_account_id: UUID | None = None


class POCreate(BaseModel):
    """Create PO request."""

    supplier_id: UUID
    po_date: date
    expected_delivery_date: date | None = None
    currency_code: str = Field(max_length=3)
    description: str | None = None
    lines: list[POLineCreate] = Field(min_length=1)


class PORead(BaseModel):
    """PO response."""

    model_config = ConfigDict(from_attributes=True)
    po_id: UUID
    organization_id: UUID
    supplier_id: UUID
    po_number: str
    po_date: date
    status: str
    currency_code: str
    total_amount: Decimal


# =============================================================================
# Goods Receipt
# =============================================================================


class GRLineCreate(BaseModel):
    """Goods receipt line input."""

    po_line_id: UUID | None = None
    item_id: UUID
    quantity_received: Decimal
    unit_cost: Decimal
    warehouse_id: UUID | None = None


class GRCreate(BaseModel):
    """Create goods receipt request."""

    po_id: UUID
    receipt_date: date
    notes: str | None = None
    lines: list[GRLineCreate] = Field(min_length=1)


class GRRead(BaseModel):
    """Goods receipt response."""

    model_config = ConfigDict(from_attributes=True)
    receipt_id: UUID
    organization_id: UUID
    po_id: UUID
    receipt_number: str
    receipt_date: date
    status: str
    total_amount: Decimal


# =============================================================================
# Payment Batch
# =============================================================================


class PaymentBatchCreate(BaseModel):
    """Create payment batch request."""

    batch_name: str = Field(max_length=100)
    payment_date: date
    bank_account_id: UUID
    payment_method: str = "EFT"
    description: str | None = None


class PaymentBatchRead(BaseModel):
    """Payment batch response."""

    model_config = ConfigDict(from_attributes=True)
    batch_id: UUID
    organization_id: UUID
    batch_number: str
    batch_name: str
    payment_date: date
    status: str
    total_amount: Decimal
    payment_count: int


class BankFileResultRead(BaseModel):
    """Bank file generation result."""

    success: bool
    file_format: str
    file_content: str | None = None
    payment_count: int
    total_amount: str


__all__ = [
    "SupplierCreate",
    "SupplierUpdate",
    "SupplierRead",
    "APInvoiceLineCreate",
    "APInvoiceCreate",
    "APInvoiceLineRead",
    "APInvoiceRead",
    "PaymentAllocationCreate",
    "APPaymentCreate",
    "APPaymentRead",
    "APAgingBucketRead",
    "APAgingReportRead",
    "POLineCreate",
    "POCreate",
    "PORead",
    "GRLineCreate",
    "GRCreate",
    "GRRead",
    "PaymentBatchCreate",
    "PaymentBatchRead",
    "BankFileResultRead",
]
