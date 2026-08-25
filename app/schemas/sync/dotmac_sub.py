"""Canonical contracts for operational data exchanged with Dotmac Sub."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    field_validator,
    model_validator,
)

from app.config import settings
from app.services.finance.money_boundary import (
    MoneyBoundaryError,
    boundary_currency,
    check_canonical_money_lexeme,
    to_boundary_money,
)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _require_wire_money_string(value: object) -> object:
    """Reject JSON number tokens before Pydantic can coerce them to Decimal."""
    if isinstance(value, bool | int | float):
        raise ValueError(
            f"refusing {type(value).__name__} {value!r} as money at ingress; "
            'send the amount as a string (e.g. "48375.00")'
        )
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError(f"refusing non-finite value {value!r} as money at ingress")
        return value
    if isinstance(value, str):
        try:
            check_canonical_money_lexeme(value, field="money at ingress")
        except MoneyBoundaryError as exc:
            raise ValueError(str(exc)) from exc
        return value
    raise ValueError(
        f"refusing {type(value).__name__} as money at ingress; send the "
        'amount as a string (e.g. "48375.00")'
    )


# Inbound operational projections


class SubProjectPayload(_StrictModel):
    source_id: str = Field(..., min_length=1, max_length=120)
    name: str = Field(..., max_length=160)
    code: str | None = Field(None, max_length=80)
    project_type: str | None = Field(None, max_length=80)
    status: str = Field("active", max_length=40)
    region: str | None = Field(None, max_length=80)
    description: str | None = None
    start_at: datetime | None = None
    due_at: datetime | None = None
    customer_name: str | None = Field(None, max_length=200)
    customer_source_id: str | None = Field(None, max_length=120)
    metadata: dict[str, Any] = Field(default_factory=dict)
    service_team_name: str | None = Field(None, max_length=200)
    service_team_department_id: str | None = Field(None, max_length=120)


class SubProjectTaskPayload(_StrictModel):
    source_id: str = Field(..., min_length=1, max_length=120)
    project_source_id: str = Field(..., min_length=1, max_length=120)
    parent_task_source_id: str | None = Field(None, max_length=120)
    title: str = Field(..., min_length=1, max_length=200)
    number: str | None = Field(None, max_length=40)
    description: str | None = None
    status: str = Field("todo", max_length=40)
    priority: str | None = Field(None, max_length=40)
    start_at: datetime | None = None
    due_at: datetime | None = None
    completed_at: datetime | None = None
    effort_hours: Decimal | None = Field(None, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SubWorkOrderPayload(_StrictModel):
    source_id: str = Field(..., min_length=1, max_length=120)
    title: str = Field(..., max_length=200)
    work_type: str | None = Field(None, max_length=80)
    status: str = Field("active", max_length=40)
    priority: str | None = Field(None, max_length=40)
    project_source_id: str | None = Field(None, max_length=120)
    assigned_employee_email: str | None = Field(None, max_length=255)
    assigned_employee_emails: list[str] = Field(default_factory=list)
    scheduled_start: datetime | None = None
    scheduled_end: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class BulkSyncRequest(_StrictModel):
    projects: list[SubProjectPayload] = Field(default_factory=list, max_length=500)
    project_tasks: list[SubProjectTaskPayload] = Field(
        default_factory=list, max_length=500
    )
    work_orders: list[SubWorkOrderPayload] = Field(default_factory=list, max_length=500)


class SyncError(_StrictModel):
    entity_type: str
    source_id: str
    error: str


class BulkSyncResponse(_StrictModel):
    contract_version: Literal[2] = 2
    projects_synced: int = 0
    project_tasks_synced: int = 0
    work_orders_synced: int = 0
    errors: list[SyncError] = Field(default_factory=list)


# ERP-owned inventory reads


class InventoryItemStock(_StrictModel):
    item_id: UUID
    item_code: str
    item_name: str
    description: str | None = None
    category_code: str | None = None
    category_name: str | None = None
    base_uom: str
    quantity_on_hand: Decimal
    quantity_reserved: Decimal
    quantity_available: Decimal
    reorder_point: Decimal | None = None
    list_price: Decimal | None = None
    currency_code: str = settings.default_functional_currency_code
    barcode: str | None = None
    is_below_reorder: bool = False

    @computed_field  # type: ignore[prop-decorator]
    @property
    def stock_uom(self) -> str:
        return self.base_uom

    @computed_field  # type: ignore[prop-decorator]
    @property
    def on_hand(self) -> Decimal:
        return self.quantity_on_hand

    @computed_field  # type: ignore[prop-decorator]
    @property
    def reserved(self) -> Decimal:
        return self.quantity_reserved


class WarehouseStock(_StrictModel):
    warehouse_id: UUID
    warehouse_code: str
    warehouse_name: str
    quantity_on_hand: Decimal
    quantity_reserved: Decimal
    quantity_available: Decimal
    serial_numbers: list[str] = Field(default_factory=list)


class InventoryItemDetail(_StrictModel):
    item_id: UUID
    item_code: str
    item_name: str
    description: str | None = None
    category_code: str | None = None
    category_name: str | None = None
    base_uom: str
    total_on_hand: Decimal
    total_reserved: Decimal
    total_available: Decimal
    reorder_point: Decimal | None = None
    list_price: Decimal | None = None
    currency_code: str = settings.default_functional_currency_code
    barcode: str | None = None
    track_serial_numbers: bool = False
    warehouses: list[WarehouseStock] = Field(default_factory=list)


class SubAvailableSerialRead(_StrictModel):
    serial_id: UUID
    serial_number: str
    status: str
    item_code: str
    warehouse_code: str


class SubAvailableSerialListResponse(_StrictModel):
    item_code: str
    item_name: str
    warehouse_code: str
    warehouse_name: str
    track_serial_numbers: bool
    serials: list[SubAvailableSerialRead] = Field(default_factory=list)
    total_count: int = 0
    has_more: bool = False


class InventoryListResponse(_StrictModel):
    items: list[InventoryItemStock] = Field(default_factory=list)
    total_count: int = 0
    has_more: bool = False


# Material support


class SubMaterialRequestItemPayload(_StrictModel):
    item_code: str = Field(..., max_length=50)
    quantity: Decimal = Field(..., gt=0)
    uom: str | None = Field(None, max_length=20)
    from_warehouse_code: str = Field(..., max_length=100)
    serial_numbers: list[str] | None = None


class SubMaterialRequestPayload(_StrictModel):
    source_request_id: str = Field(..., min_length=1, max_length=120)
    request_type: str = Field("ISSUE", max_length=30)
    status: str = Field(..., max_length=40)
    items: list[SubMaterialRequestItemPayload] = Field(..., min_length=1)
    project_source_id: str | None = Field(None, max_length=120)
    requested_by_email: str | None = Field(None, max_length=255)
    schedule_date: str | None = Field(None, max_length=10)
    remarks: str | None = None


class SubMaterialRequestResponse(_StrictModel):
    request_id: UUID
    request_number: str
    status: str
    source_request_id: str


class SubMaterialRequestItemRead(_StrictModel):
    item_code: str
    item_name: str
    requested_qty: Decimal
    ordered_qty: Decimal
    uom: str | None = None
    serial_numbers: list[str] | None = None


class SubMaterialRequestStatusRead(_StrictModel):
    request_id: UUID
    request_number: str
    status: str
    request_type: str
    items: list[SubMaterialRequestItemRead] = Field(default_factory=list)
    created_at: datetime


# Expense requests


class SubExpenseClaimItemPayload(_StrictModel):
    category_code: str = Field(..., min_length=1, max_length=30)
    description: str = Field(..., min_length=1, max_length=500)
    claimed_amount: Decimal = Field(..., gt=0)
    expense_date: str | None = Field(None, max_length=10)
    vendor_name: str | None = Field(None, max_length=200)
    receipt_url: str | None = Field(None, max_length=500)
    notes: str | None = None


class SubExpenseClaimPayload(_StrictModel):
    source_request_id: str = Field(..., min_length=1, max_length=120)
    purpose: str = Field(..., min_length=1, max_length=500)
    claim_date: str = Field(..., max_length=10)
    requested_by_email: str = Field(..., min_length=3, max_length=255)
    external_work_reference: str | None = Field(None, max_length=255)
    project_source_id: str | None = Field(None, max_length=120)
    currency_code: str | None = Field(None, min_length=3, max_length=3)
    remarks: str | None = None
    reference_number: str | None = Field(None, max_length=50)
    items: list[SubExpenseClaimItemPayload] = Field(..., min_length=1)


class SubExpenseClaimResponse(_StrictModel):
    claim_id: UUID
    claim_number: str
    status: str
    source_request_id: str


class SubExpenseClaimStatusResponse(_StrictModel):
    claim_id: UUID
    claim_number: str
    status: str
    rejection_reason: str | None = None
    paid_on: date | None = None
    total_claimed_amount: Decimal
    total_approved_amount: Decimal | None = None
    source_request_id: str


class SubExpenseCategoryItem(_StrictModel):
    category_code: str
    category_name: str
    requires_receipt: bool = True
    max_amount_per_claim: Decimal | None = None


class SubExpenseCategoriesResponse(_StrictModel):
    items: list[SubExpenseCategoryItem] = Field(default_factory=list)


# Procurement and accounts payable commands


class SubPurchaseOrderItemPayload(_StrictModel):
    item_type: str = Field(..., max_length=50)
    description: str = Field(..., max_length=500)
    quantity: Decimal = Field(..., gt=0)
    unit_price: Decimal = Field(..., ge=0)
    amount: Decimal = Field(..., ge=0)
    cable_type: str | None = Field(None, max_length=100)
    fiber_count: int | None = None
    splice_count: int | None = None
    notes: str | None = None

    _amount_ingress = field_validator("amount", mode="before")(
        _require_wire_money_string
    )


class SubPurchaseOrderPayload(_StrictModel):
    source_work_order_id: str = Field(..., min_length=1, max_length=120)
    source_quote_id: str | None = Field(None, max_length=120)
    source_project_id: str | None = Field(None, max_length=120)
    project_code: str | None = Field(None, max_length=80)
    project_name: str | None = Field(None, max_length=200)
    vendor_erp_id: str | None = Field(None, max_length=255)
    vendor_name: str | None = Field(None, max_length=255)
    vendor_code: str | None = Field(None, max_length=30)
    title: str = Field(..., max_length=500)
    currency: str = Field(settings.default_functional_currency_code, max_length=3)
    subtotal: Decimal
    tax_total: Decimal = Decimal("0.00")
    total: Decimal
    approved_at: datetime | None = None
    approved_by_email: str | None = Field(None, max_length=255)
    items: list[SubPurchaseOrderItemPayload] = Field(..., min_length=1)

    _money_ingress = field_validator("subtotal", "tax_total", "total", mode="before")(
        _require_wire_money_string
    )

    @model_validator(mode="after")
    def _validate_boundary_money(self) -> SubPurchaseOrderPayload:
        label = f"purchase order {self.source_work_order_id}"
        minor_units = boundary_currency(
            self.currency, field=f"{label} currency"
        ).minor_units
        money_fields: list[tuple[str, Decimal]] = [
            (f"{label} subtotal", self.subtotal),
            (f"{label} tax_total", self.tax_total),
            (f"{label} total", self.total),
        ]
        money_fields.extend(
            (f"{label} line {index} amount", item.amount)
            for index, item in enumerate(self.items, 1)
        )
        for field_name, value in money_fields:
            to_boundary_money(value, self.currency, field=field_name)
            exponent = value.as_tuple().exponent
            if not isinstance(exponent, int) or -exponent != minor_units:
                raise MoneyBoundaryError(
                    f"{field_name}: expected exactly {minor_units} fractional "
                    f'digits (the canonical fixed-minor-unit form, e.g. "48375.00"); '
                    f"got {value}"
                )
        return self


class SubPurchaseOrderVariationPayload(SubPurchaseOrderPayload):
    variation_id: str = Field(..., min_length=1, max_length=120)
    variation_version: int = Field(..., ge=2, le=2_147_483_647)
    amendment_reason: str = Field(..., max_length=500)


class SubPurchaseOrderResponse(_StrictModel):
    purchase_order_id: str
    po_id: UUID
    status: str
    source_work_order_id: str
    is_amendment: bool = False
    variation_id: str | None = None
    amendment_version: int = 1
    superseded_po_id: UUID | None = None


class SubPurchaseInvoiceItemPayload(_StrictModel):
    item_type: str | None = Field(None, max_length=80)
    description: str = Field(..., min_length=1, max_length=2000)
    quantity: Decimal = Field(..., gt=0)
    unit_price: Decimal = Field(..., ge=0)
    amount: Decimal = Field(..., ge=0)
    notes: str | None = Field(None, max_length=2000)

    _amount_ingress = field_validator("amount", mode="before")(
        _require_wire_money_string
    )


class SubPurchaseInvoicePayload(_StrictModel):
    source_invoice_id: str = Field(..., min_length=1, max_length=120)
    source_invoice_number: str = Field(..., min_length=1, max_length=100)
    source_project_id: str = Field(..., min_length=1, max_length=120)
    installation_project_id: str = Field(..., min_length=1, max_length=120)
    source_quote_id: str | None = Field(None, max_length=120)
    erp_purchase_order_id: str = Field(..., min_length=1, max_length=100)
    vendor_erp_id: str | None = Field(None, max_length=255)
    vendor_name: str = Field(..., min_length=1, max_length=255)
    vendor_code: str | None = Field(None, max_length=160)
    project_code: str | None = Field(None, max_length=80)
    project_name: str | None = Field(None, max_length=200)
    currency: str = Field(..., min_length=3, max_length=3)
    tax_rate_percent: Decimal = Field(Decimal("0"), ge=0, le=100)
    subtotal: Decimal = Field(..., ge=0)
    tax_total: Decimal = Field(Decimal("0.00"), ge=0)
    total: Decimal = Field(..., gt=0)
    approved_at: datetime | None = None
    approved_by_email: str | None = Field(None, max_length=255)
    items: list[SubPurchaseInvoiceItemPayload] = Field(..., min_length=1)

    _money_ingress = field_validator("subtotal", "tax_total", "total", mode="before")(
        _require_wire_money_string
    )

    @model_validator(mode="after")
    def _validate_boundary_money(self) -> SubPurchaseInvoicePayload:
        label = f"purchase invoice {self.source_invoice_id}"
        minor_units = boundary_currency(
            self.currency, field=f"{label} currency"
        ).minor_units
        money_fields: list[tuple[str, Decimal]] = [
            (f"{label} subtotal", self.subtotal),
            (f"{label} tax_total", self.tax_total),
            (f"{label} total", self.total),
        ]
        money_fields.extend(
            (f"{label} line {index} amount", item.amount)
            for index, item in enumerate(self.items, 1)
        )
        for field_name, value in money_fields:
            to_boundary_money(value, self.currency, field=field_name)
            exponent = value.as_tuple().exponent
            if not isinstance(exponent, int) or -exponent != minor_units:
                raise MoneyBoundaryError(
                    f"{field_name}: expected exactly {minor_units} fractional "
                    f'digits (the canonical fixed-minor-unit form, e.g. "48375.00"); '
                    f"got {value}"
                )
        return self


class SubPurchaseInvoiceResponse(_StrictModel):
    purchase_invoice_id: str
    invoice_id: UUID
    invoice_number: str
    status: str
    source_invoice_id: str


class SubPurchaseInvoiceAttachmentPayload(_StrictModel):
    file_name: str = Field(..., min_length=1, max_length=255)
    mime_type: str = Field(..., min_length=1, max_length=100)
    content_base64: str = Field(..., min_length=1)


class SubPurchaseInvoiceAttachmentResponse(_StrictModel):
    attachment_id: UUID
    purchase_invoice_id: UUID
    file_name: str
    created: bool


class SubPurchaseInvoiceStatusResponse(_StrictModel):
    source_invoice_id: str
    purchase_invoice_id: UUID
    invoice_number: str
    status: str
    currency: str
    total_amount: Decimal
    amount_paid: Decimal
    balance_due: Decimal
    source_updated_at: datetime | None
