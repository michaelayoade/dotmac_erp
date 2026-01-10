"""
AP Schemas.

Pydantic schemas for Accounts Payable APIs.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# =============================================================================
# Supplier
# =============================================================================

class SupplierBase(BaseModel):
    """Base supplier schema."""

    supplier_code: str = Field(max_length=30)
    supplier_name: str = Field(max_length=200)
    tax_id: Optional[str] = Field(default=None, max_length=50)
    payment_terms_days: int = 30
    currency_code: str = Field(max_length=3)
    default_expense_account_id: Optional[UUID] = None
    default_payable_account_id: Optional[UUID] = None
    is_active: bool = True


class SupplierCreate(SupplierBase):
    """Create supplier request."""

    pass


class SupplierUpdate(BaseModel):
    """Update supplier request."""

    supplier_name: Optional[str] = Field(default=None, max_length=200)
    payment_terms_days: Optional[int] = None
    is_active: Optional[bool] = None


class SupplierRead(SupplierBase):
    """Supplier response."""

    model_config = ConfigDict(from_attributes=True)

    supplier_id: UUID
    organization_id: UUID
    created_at: datetime
    updated_at: Optional[datetime] = None


# =============================================================================
# AP Invoice
# =============================================================================

class APInvoiceLineCreate(BaseModel):
    """AP invoice line for creation."""

    expense_account_id: UUID
    description: str = Field(max_length=500)
    quantity: Decimal = Field(default=Decimal("1"))
    unit_price: Decimal
    tax_code_id: Optional[UUID] = None
    cost_center_id: Optional[UUID] = None
    project_id: Optional[UUID] = None


class APInvoiceCreate(BaseModel):
    """Create AP invoice request."""

    supplier_id: UUID
    invoice_number: str = Field(max_length=50)
    invoice_date: date
    due_date: date
    currency_code: str = Field(max_length=3)
    description: Optional[str] = None
    lines: list[APInvoiceLineCreate]


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
    supplier_name: Optional[str] = None
    invoice_number: str
    invoice_date: date
    due_date: date
    currency_code: str
    subtotal: Decimal
    tax_total: Decimal
    total_amount: Decimal
    amount_paid: Decimal
    amount_due: Decimal
    status: str
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
    reference_number: Optional[str] = None
    allocations: list[PaymentAllocationCreate]


class APPaymentRead(BaseModel):
    """AP payment response."""

    model_config = ConfigDict(from_attributes=True)

    payment_id: UUID
    organization_id: UUID
    supplier_id: UUID
    payment_number: str
    payment_date: date
    payment_method: str
    total_amount: Decimal
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
]
