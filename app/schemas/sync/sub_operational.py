"""
Dotmac Sub Sync Schemas - Pydantic models for Sub sync API.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import (
    AliasChoices,
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


def _require_wire_money_string(value: object) -> object:
    """Pre-coercion (``mode="before"``) guard for monetary ingress.

    Pydantic v2 coerces numbers → ``Decimal`` BEFORE ``mode="after"``
    validators run, so the E4 boundary would otherwise never see the raw
    wire type it promises to reject. This runs on the RAW ingress value.

    Wire policy (matches the connector's outbound E4 shape
    ``{"amount": "48375.00"}``): external money is a canonical decimal
    STRING only, in the lexical grammar ``serialize_amount`` emits. EVERY
    JSON number token — int and float alike (pydantic materializes them as
    Python ``int``/``float`` for before-validators) — plus booleans,
    non-finite values (NaN/Infinity) and every non-canonical spelling
    (``"1e3"``, ``" 100.00 "``, ``"+100.00"``, ``"01.00"``, ``".50"``,
    ``"100."``) is refused before any coercion can launder it. The
    currency-AWARE half of the grammar (exact minor-unit digit count) runs
    in the model-level validator, where the sibling ``currency`` field is
    available. Internal Python callers may pass ``Decimal`` (finite only).
    """
    if isinstance(value, (bool, int, float)):
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


# ============ Inbound Sync Payloads (Sub → ERP) ============


class SubProjectPayload(BaseModel):
    """Project data from Dotmac Sub."""

    source_reference: str = Field(
        ...,
        description="Opaque source UUID",
        validation_alias=AliasChoices("source_id", "source_reference"),
    )
    name: str = Field(..., max_length=160)
    code: str | None = Field(None, max_length=80)
    project_type: str | None = Field(None, max_length=80)
    status: str = Field("active", description="active, completed, cancelled, archived")
    region: str | None = Field(None, max_length=80)
    description: str | None = None
    start_at: datetime | None = None
    due_at: datetime | None = None
    customer_name: str | None = Field(None, max_length=200)
    customer_source_reference: str | None = Field(
        None,
        max_length=36,
    )
    metadata: dict | None = None
    # Service team integration (optional, backward-compatible)
    service_team_name: str | None = Field(None, max_length=200)
    service_team_department_id: str | None = Field(None, max_length=36)


class SubTicketPayload(BaseModel):
    """Support ticket data from Dotmac Sub."""

    source_reference: str = Field(
        ...,
        description="Opaque source UUID",
        validation_alias=AliasChoices("source_id", "source_reference"),
    )
    subject: str = Field(..., max_length=255)
    ticket_number: str | None = Field(None, max_length=40)
    ticket_type: str | None = Field(None, max_length=80)
    status: str = Field("active", description="active, completed, cancelled")
    priority: str | None = Field(None, max_length=40)
    description: str | None = Field(
        default=None,
        validation_alias=AliasChoices("description", "body", "ticket_description"),
    )
    comments: list[dict[str, Any]] = Field(
        default_factory=list,
        validation_alias=AliasChoices("comments", "ticket_comments"),
    )
    activity_log: list[dict[str, Any]] = Field(
        default_factory=list,
        validation_alias=AliasChoices("activity_log", "activityLog", "activities"),
    )
    customer_name: str | None = Field(None, max_length=200)
    customer_source_reference: str | None = Field(
        None,
        max_length=36,
    )
    metadata: dict | None = None
    # Service team integration (optional, backward-compatible)
    service_team_name: str | None = Field(None, max_length=200)
    assigned_employee_emails: list[str] = Field(default_factory=list)


class SubTicketCommentItem(BaseModel):
    """Sub ticket comment item."""

    id: str = Field(..., min_length=1, max_length=255)
    timestamp: datetime | None = Field(
        default=None,
        validation_alias=AliasChoices("timestamp", "created_at", "createdAt"),
    )
    author_person_id: str | None = Field(
        None,
        max_length=255,
        validation_alias=AliasChoices("author_person_id", "authorPersonId"),
    )
    is_internal: bool = Field(
        False,
        validation_alias=AliasChoices("is_internal", "isInternal"),
    )
    body: str | None = None
    attachments_count: int = Field(
        0,
        ge=0,
        validation_alias=AliasChoices("attachments_count", "attachmentsCount"),
    )


class SubTicketActivityEntry(BaseModel):
    """Sub ticket activity item (comment-style or event-style)."""

    kind: Literal["comment", "event"]
    id: str = Field(..., min_length=1, max_length=255)
    timestamp: datetime | None = Field(
        default=None,
        validation_alias=AliasChoices("timestamp", "created_at", "createdAt"),
    )
    author_person_id: str | None = Field(
        None,
        max_length=255,
        validation_alias=AliasChoices("author_person_id", "authorPersonId"),
    )
    is_internal: bool = Field(
        False,
        validation_alias=AliasChoices("is_internal", "isInternal"),
    )
    body: str | None = None
    attachments_count: int = Field(
        0,
        ge=0,
        validation_alias=AliasChoices("attachments_count", "attachmentsCount"),
    )
    event_type: str | None = Field(
        None,
        max_length=100,
        validation_alias=AliasChoices("event_type", "eventType"),
    )
    status: str | None = Field(None, max_length=80)
    details: dict[str, Any] | None = None


class SubWorkOrderPayload(BaseModel):
    """Work order data from Dotmac Sub."""

    source_reference: str = Field(
        ...,
        description="Opaque source UUID",
        validation_alias=AliasChoices("source_id", "source_reference"),
    )
    title: str = Field(..., max_length=200)
    work_type: str | None = Field(None, max_length=80)
    status: str = Field("active", description="active, completed, cancelled")
    priority: str | None = Field(None, max_length=40)
    project_source_reference: str | None = Field(
        None,
        description="Links to Sub project",
    )
    ticket_source_reference: str | None = Field(
        None,
        description="Links to Sub ticket",
    )
    assigned_employee_email: str | None = Field(None, max_length=255)
    assigned_employee_emails: list[str] = Field(default_factory=list)
    scheduled_start: datetime | None = None
    scheduled_end: datetime | None = None
    metadata: dict | None = None


class SubProjectTaskPayload(BaseModel):
    """Project task projected from Sub into ERP project management."""

    source_id: str = Field(..., min_length=1, max_length=120)
    project_source_id: str = Field(..., min_length=1, max_length=120)
    parent_task_source_id: str | None = Field(None, max_length=120)
    ticket_source_id: str | None = Field(None, max_length=120)
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


class BulkSyncRequest(BaseModel):
    """Bulk sync request from Dotmac Sub."""

    projects: list[SubProjectPayload] = Field(default_factory=list, max_length=500)
    project_tasks: list[SubProjectTaskPayload] = Field(
        default_factory=list, max_length=500
    )
    tickets: list[SubTicketPayload] = Field(default_factory=list, max_length=500)
    work_orders: list[SubWorkOrderPayload] = Field(default_factory=list, max_length=500)


class SyncError(BaseModel):
    """Error detail for sync operation."""

    entity_type: str
    source_reference: str
    error: str


class BulkSyncResponse(BaseModel):
    """Response from bulk sync operation."""

    contract_version: Literal[2] = 2
    projects_synced: int = 0
    project_tasks_synced: int = 0
    tickets_synced: int = 0
    work_orders_synced: int = 0
    errors: list[SyncError] = Field(default_factory=list)


class SubNccFinancialsResponse(BaseModel):
    """NCC Section F financial statement projection consumed by Sub."""

    period: dict
    summary: dict
    detail: dict
    note: str


class SubNccStaffHeadcountResponse(BaseModel):
    """NCC Section G matrix: category -> nationality -> gender -> count."""

    total_active: int
    by_category: dict[str, dict[str, dict[str, int]]]


# ============ Inventory Sync (ERP → Sub) ============


class InventoryItemStock(BaseModel):
    """Single item with stock levels for Sub installations."""

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

    # Computed aliases for Sub client backward compatibility
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


class WarehouseStock(BaseModel):
    """Stock levels at a specific warehouse."""

    warehouse_id: UUID
    warehouse_code: str
    warehouse_name: str
    quantity_on_hand: Decimal
    quantity_reserved: Decimal
    quantity_available: Decimal
    serial_numbers: list[str] = Field(default_factory=list)


class InventoryItemDetail(BaseModel):
    """Detailed item info with warehouse breakdown."""

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


class SubAvailableSerialRead(BaseModel):
    """Available serial unit for Sub material request selection."""

    serial_id: UUID
    serial_number: str
    status: str
    item_code: str
    warehouse_code: str


class SubAvailableSerialListResponse(BaseModel):
    """Available serials for one item and warehouse."""

    item_code: str
    item_name: str
    warehouse_code: str
    warehouse_name: str
    track_serial_numbers: bool
    serials: list[SubAvailableSerialRead] = Field(default_factory=list)
    total_count: int = 0
    has_more: bool = False


class InventoryListResponse(BaseModel):
    """Response with inventory items and stock levels."""

    items: list[InventoryItemStock] = Field(default_factory=list)
    total_count: int = 0
    has_more: bool = False


# ============ Material Request Sync (Sub → ERP) ============


class SubMaterialRequestItemPayload(BaseModel):
    """Single item in a Sub material request."""

    item_code: str = Field(..., max_length=50)
    quantity: Decimal = Field(..., gt=0)
    uom: str | None = Field(None, max_length=20)
    from_warehouse_code: str = Field(..., max_length=100)
    serial_numbers: list[str] | None = Field(
        default=None,
        description=(
            "Selected serial numbers for serial-tracked ISSUE lines. "
            "Required when the item tracks serial numbers and the request is submitted/issued."
        ),
    )


class SubMaterialRequestPayload(BaseModel):
    """Material request from Dotmac Sub."""

    source_request_id: str = Field(
        ..., max_length=36, description="Sub-side unique ID for idempotency"
    )
    request_type: str = Field(
        "ISSUE", description="PURCHASE, TRANSFER, ISSUE, MANUFACTURE"
    )
    status: str = Field(
        ...,
        description=(
            "Sub request status mapped to local MaterialRequest status "
            "(e.g. submitted, issued)"
        ),
    )
    items: list[SubMaterialRequestItemPayload] = Field(..., min_length=1)
    project_source_reference: str | None = Field(
        None,
        max_length=36,
    )
    ticket_source_reference: str | None = Field(
        None,
        max_length=36,
    )
    requested_by_email: str | None = Field(None, max_length=255)
    schedule_date: str | None = Field(None, description="YYYY-MM-DD schedule date")
    remarks: str | None = None


class SubMaterialRequestResponse(BaseModel):
    """Response after creating a material request from Sub."""

    request_id: UUID
    request_number: str
    status: str
    source_request_id: str


class SubMaterialRequestItemRead(BaseModel):
    """Item detail in a material request status response."""

    item_code: str
    item_name: str
    requested_qty: Decimal
    ordered_qty: Decimal
    uom: str | None = None
    serial_numbers: list[str] | None = None


class SubMaterialRequestStatusRead(BaseModel):
    """Full status of a material request for Sub."""

    request_id: UUID
    request_number: str
    status: str
    request_type: str
    items: list[SubMaterialRequestItemRead] = Field(default_factory=list)
    created_at: datetime


# ============ Expense Claim Sync (Sub → ERP) ============


class SubExpenseClaimItemPayload(BaseModel):
    """Single expense line in a Sub field-technician expense request."""

    category_code: str = Field(..., min_length=1, max_length=30)
    description: str = Field(..., min_length=1, max_length=500)
    claimed_amount: Decimal = Field(..., gt=0)
    expense_date: str | None = Field(
        None, description="YYYY-MM-DD expense date; defaults to the claim_date"
    )
    vendor_name: str | None = Field(None, max_length=200)
    receipt_url: str | None = Field(None, max_length=500)
    notes: str | None = None


class SubExpenseClaimPayload(BaseModel):
    """Expense claim from Dotmac Sub (field-technician expense request)."""

    source_claim_id: str = Field(
        ..., max_length=36, description="Sub expense request UUID for idempotency"
    )
    purpose: str = Field(..., min_length=1, max_length=500)
    claim_date: str = Field(..., description="YYYY-MM-DD claim date")
    requested_by_email: str = Field(..., min_length=3, max_length=255)
    ticket_source_reference: str | None = Field(
        None,
        max_length=36,
    )
    project_source_reference: str | None = Field(
        None,
        max_length=36,
    )
    currency_code: str | None = Field(None, min_length=3, max_length=3)
    remarks: str | None = None
    reference_number: str | None = Field(
        None, max_length=50, description="Sub expense request number"
    )
    items: list[SubExpenseClaimItemPayload] = Field(..., min_length=1)


class SubExpenseClaimResponse(BaseModel):
    """Response after creating an expense claim from Sub."""

    claim_id: UUID
    claim_number: str
    status: str
    source_claim_id: str


class SubExpenseClaimStatusResponse(BaseModel):
    """Expense claim status for Sub polling."""

    claim_id: UUID
    claim_number: str
    status: str
    rejection_reason: str | None = None
    paid_on: date | None = None
    total_claimed_amount: Decimal
    total_approved_amount: Decimal | None = None
    source_claim_id: str


class SubExpenseCategoryItem(BaseModel):
    """Active expense category exposed to the Sub expense-request form."""

    category_code: str
    category_name: str
    requires_receipt: bool = True
    max_amount_per_claim: Decimal | None = None


class SubExpenseCategoriesResponse(BaseModel):
    """Response with active expense categories for Sub."""

    items: list[SubExpenseCategoryItem] = Field(default_factory=list)


# ============ Purchase Order Sync (Sub → ERP) ============


class SubPurchaseOrderItemPayload(BaseModel):
    """Single line item in a Sub purchase order."""

    item_type: str = Field(..., max_length=50)
    description: str = Field(..., max_length=500)
    quantity: Decimal = Field(..., gt=0)
    unit_price: Decimal = Field(..., ge=0)
    amount: Decimal = Field(..., ge=0)
    cable_type: str | None = Field(None, max_length=100)
    fiber_count: int | None = None
    splice_count: int | None = None
    notes: str | None = None


class SubPurchaseOrderPayload(BaseModel):
    """Purchase order from Dotmac Sub (triggered on vendor quote approval)."""

    source_work_order_id: str = Field(
        ..., max_length=36, description="Sub work order ID for idempotency"
    )
    source_quote_id: str | None = Field(None, max_length=36)
    source_project_id: str | None = Field(None, max_length=36)
    project_code: str | None = Field(None, max_length=80)
    project_name: str | None = Field(None, max_length=200)
    vendor_erp_id: str | None = Field(None, max_length=255)
    vendor_name: str | None = Field(None, max_length=255)
    vendor_code: str | None = Field(None, max_length=30)
    title: str = Field(..., max_length=500)
    currency: str = Field(settings.default_functional_currency_code, max_length=3)
    subtotal: Decimal
    tax_total: Decimal = Decimal("0")
    total: Decimal
    approved_at: datetime | None = None
    approved_by_email: str | None = Field(None, max_length=255)
    items: list[SubPurchaseOrderItemPayload] = Field(..., min_length=1)


class SubPurchaseOrderResponse(BaseModel):
    """Response after creating a purchase order from Sub."""

    purchase_order_id: str
    po_id: UUID
    status: str
    source_work_order_id: str
    is_amendment: bool = False
    variation_id: str | None = None
    amendment_version: int = 1
    superseded_po_id: UUID | None = None


class SubPurchaseInvoiceItemPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_type: str | None = Field(None, max_length=80)
    description: str = Field(..., min_length=1, max_length=2000)
    quantity: Decimal = Field(..., gt=0)
    unit_price: Decimal = Field(..., ge=0)
    amount: Decimal = Field(..., ge=0)
    notes: str | None = Field(None, max_length=2000)

    # ``amount`` is boundary money; ``quantity``/``unit_price`` are
    # rates/quantities and deliberately keep pydantic's default coercion.
    _amount_ingress = field_validator("amount", mode="before")(
        _require_wire_money_string
    )


class SubPurchaseInvoicePayload(BaseModel):
    """Approved vendor invoice originated by Sub/Sub and matched to an ERP PO."""

    model_config = ConfigDict(extra="forbid")

    source_invoice_id: str = Field(..., min_length=1, max_length=36)
    source_invoice_number: str = Field(..., min_length=1, max_length=100)
    source_project_id: str = Field(..., min_length=1, max_length=36)
    installation_project_id: str = Field(..., min_length=1, max_length=36)
    source_quote_id: str | None = Field(None, max_length=36)
    erp_purchase_order_id: str = Field(..., min_length=1, max_length=100)
    vendor_erp_id: str | None = Field(None, max_length=255)
    vendor_name: str = Field(..., min_length=1, max_length=255)
    vendor_code: str | None = Field(None, max_length=160)
    project_code: str | None = Field(None, max_length=80)
    project_name: str | None = Field(None, max_length=200)
    currency: str = Field(..., min_length=3, max_length=3)
    tax_rate_percent: Decimal = Field(Decimal("0"), ge=0, le=100)
    subtotal: Decimal = Field(..., ge=0)
    # Default carries the canonical 2-fractional-digit scale (all provisioned
    # currencies are 2-minor-unit today; the model validator enforces the
    # exact scale for whatever currency the document declares).
    tax_total: Decimal = Field(Decimal("0.00"), ge=0)
    total: Decimal = Field(..., gt=0)
    approved_at: datetime | None = None
    approved_by_email: str | None = Field(None, max_length=255)
    items: list[SubPurchaseInvoiceItemPayload] = Field(..., min_length=1)

    # Pre-coercion strings-only enforcement for the header money facts (see
    # ``_require_wire_money_string`` for the wire policy).
    _money_ingress = field_validator("subtotal", "tax_total", "total", mode="before")(
        _require_wire_money_string
    )

    @model_validator(mode="after")
    def _validate_boundary_money(self) -> SubPurchaseInvoicePayload:
        """E4 fail-closed money boundary for the Sub/Sub payables command.

        Header totals and line amounts must be exact in the document
        currency's minor units (typed kernel Money at the boundary; no
        missing currency, no excess precision). The strings-only wire policy
        (every JSON number token rejected; currency-independent lexical
        grammar) is enforced in the ``mode="before"`` ingress validators
        above — by the time this runs, pydantic has already coerced to
        ``Decimal``, so the raw-type check must precede coercion. HERE, with
        the sibling ``currency`` available, the currency-AWARE half of the
        canonical grammar applies: every money value must carry EXACTLY the
        currency's minor-unit digit count (the fixed-minor-unit form
        ``serialize_amount`` emits — pydantic's str→Decimal coercion
        preserves the wire string's scale, so the check holds for wire and
        internal callers alike). Line ``quantity`` and ``unit_price`` are
        rates/quantities and deliberately stay plain decimals. ERP-internal
        AP posting/tax precision is unchanged.
        """
        label = f"purchase invoice {self.source_invoice_id}"
        minor_units = boundary_currency(
            self.currency, field=f"{label} currency"
        ).minor_units
        money_fields: list[tuple[str, Decimal]] = [
            (f"{label} subtotal", self.subtotal),
            (f"{label} tax_total", self.tax_total),
            (f"{label} total", self.total),
        ]
        for index, item in enumerate(self.items, 1):
            money_fields.append((f"{label} line {index} amount", item.amount))
        for field_name, value in money_fields:
            to_boundary_money(value, self.currency, field=field_name)
            # A non-finite Decimal reports a str exponent ("n"/"N"/"F"). Those
            # are already refused by the ingress validators and by
            # to_boundary_money above; narrowing explicitly (rather than
            # casting) keeps this fail-closed if that ever regresses.
            exponent = value.as_tuple().exponent
            if not isinstance(exponent, int) or -exponent != minor_units:
                raise MoneyBoundaryError(
                    f"{field_name}: expected exactly {minor_units} fractional "
                    f"digits (the canonical fixed-minor-unit form, e.g. "
                    f'"48375.00"); got {value}'
                )
        return self


class SubPurchaseInvoiceResponse(BaseModel):
    purchase_invoice_id: str
    invoice_id: UUID
    invoice_number: str
    status: str
    source_invoice_id: str


class SubPurchaseInvoiceAttachmentPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file_name: str = Field(..., min_length=1, max_length=255)
    mime_type: str = Field(..., min_length=1, max_length=100)
    content_base64: str = Field(..., min_length=1)


class SubPurchaseInvoiceAttachmentResponse(BaseModel):
    attachment_id: UUID
    purchase_invoice_id: UUID
    file_name: str
    created: bool
