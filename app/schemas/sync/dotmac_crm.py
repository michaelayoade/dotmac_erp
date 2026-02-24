"""
DotMac CRM Sync Schemas - Pydantic models for CRM sync API.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, computed_field

# ============ Inbound Sync Payloads (CRM → ERP) ============


class CRMProjectPayload(BaseModel):
    """Project data from DotMac CRM."""

    crm_id: str = Field(..., description="UUID from CRM")
    name: str = Field(..., max_length=160)
    code: str | None = Field(None, max_length=80)
    project_type: str | None = Field(None, max_length=80)
    status: str = Field("active", description="active, completed, cancelled, archived")
    region: str | None = Field(None, max_length=80)
    description: str | None = None
    start_at: datetime | None = None
    due_at: datetime | None = None
    customer_name: str | None = Field(None, max_length=200)
    customer_crm_id: str | None = Field(None, max_length=36)
    metadata: dict | None = None
    # Service team integration (optional, backward-compatible)
    service_team_name: str | None = Field(None, max_length=200)
    service_team_department_id: str | None = Field(None, max_length=36)


class CRMTicketPayload(BaseModel):
    """Support ticket data from DotMac CRM."""

    crm_id: str = Field(..., description="UUID from CRM")
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
    customer_crm_id: str | None = Field(None, max_length=36)
    metadata: dict | None = None
    # Service team integration (optional, backward-compatible)
    service_team_name: str | None = Field(None, max_length=200)
    assigned_employee_emails: list[str] = Field(default_factory=list)


class CRMTicketCommentItem(BaseModel):
    """CRM ticket comment item."""

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


class CRMTicketActivityEntry(BaseModel):
    """CRM ticket activity item (comment-style or event-style)."""

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


class CRMWorkOrderPayload(BaseModel):
    """Work order data from DotMac CRM."""

    crm_id: str = Field(..., description="UUID from CRM")
    title: str = Field(..., max_length=200)
    work_type: str | None = Field(None, max_length=80)
    status: str = Field("active", description="active, completed, cancelled")
    priority: str | None = Field(None, max_length=40)
    project_crm_id: str | None = Field(None, description="Links to CRM project")
    ticket_crm_id: str | None = Field(None, description="Links to CRM ticket")
    assigned_employee_email: str | None = Field(None, max_length=255)
    assigned_employee_emails: list[str] = Field(default_factory=list)
    scheduled_start: datetime | None = None
    scheduled_end: datetime | None = None
    metadata: dict | None = None


class CRMInventoryItemPayload(BaseModel):
    """Inventory item data from DotMac CRM."""

    crm_id: str = Field(..., description="UUID from CRM")
    item_code: str = Field(..., max_length=50)
    item_name: str = Field(..., max_length=200)
    description: str | None = None
    category_code: str | None = Field(
        None, max_length=30, description="ERP item category code"
    )
    base_uom: str = Field("EA", max_length=20)
    currency_code: str = Field("NGN", min_length=3, max_length=3)
    list_price: Decimal | None = None
    reorder_point: Decimal | None = None
    barcode: str | None = Field(None, max_length=100)
    is_active: bool = True
    metadata: dict | None = None


class BulkSyncRequest(BaseModel):
    """Bulk sync request from DotMac CRM."""

    projects: list[CRMProjectPayload] = Field(default_factory=list, max_length=500)
    tickets: list[CRMTicketPayload] = Field(default_factory=list, max_length=500)
    work_orders: list[CRMWorkOrderPayload] = Field(default_factory=list, max_length=500)


class SyncError(BaseModel):
    """Error detail for sync operation."""

    entity_type: str
    crm_id: str
    error: str


class BulkSyncResponse(BaseModel):
    """Response from bulk sync operation."""

    projects_synced: int = 0
    tickets_synced: int = 0
    work_orders_synced: int = 0
    errors: list[SyncError] = Field(default_factory=list)


class CRMInventoryItemResponse(BaseModel):
    """Response for CRM inventory item upsert."""

    item_id: UUID
    item_code: str
    status: str
    crm_id: str


# ============ Read Schemas (ERP → CRM or UI) ============


class CRMSyncMappingRead(BaseModel):
    """Read schema for CRM sync mapping."""

    model_config = ConfigDict(from_attributes=True)

    mapping_id: UUID
    crm_entity_type: str
    crm_id: str
    local_entity_type: str
    local_entity_id: UUID
    crm_status: str
    display_name: str
    display_code: str | None = None
    customer_name: str | None = None
    synced_at: datetime


class CRMProjectRead(BaseModel):
    """Project info for expense claim dropdowns."""

    model_config = ConfigDict(from_attributes=True)

    mapping_id: UUID
    crm_id: str
    local_entity_id: UUID
    name: str
    code: str | None = None
    status: str
    customer_name: str | None = None


class CRMTicketRead(BaseModel):
    """Ticket info for expense claim dropdowns."""

    model_config = ConfigDict(from_attributes=True)

    mapping_id: UUID
    crm_id: str
    local_entity_id: UUID
    subject: str
    ticket_number: str | None = None
    status: str
    customer_name: str | None = None


class CRMWorkOrderRead(BaseModel):
    """Work order info for expense claim dropdowns."""

    model_config = ConfigDict(from_attributes=True)

    mapping_id: UUID
    crm_id: str
    local_entity_id: UUID
    title: str
    status: str
    project_name: str | None = None
    ticket_subject: str | None = None


# ============ Expense Totals (ERP → CRM) ============


class ExpenseTotals(BaseModel):
    """Expense totals by status for a CRM entity."""

    draft: Decimal = Decimal("0.00")
    submitted: Decimal = Decimal("0.00")
    approved: Decimal = Decimal("0.00")
    paid: Decimal = Decimal("0.00")
    currency: str = "NGN"


class ExpenseTotalsRequest(BaseModel):
    """Request for expense totals."""

    project_crm_ids: list[str] = Field(default_factory=list, max_length=200)
    ticket_crm_ids: list[str] = Field(default_factory=list, max_length=200)
    work_order_crm_ids: list[str] = Field(default_factory=list, max_length=200)


class ExpenseTotalsResponse(BaseModel):
    """Response with expense totals keyed by CRM ID."""

    totals: dict[str, ExpenseTotals] = Field(default_factory=dict)


# ============ Inventory Sync (ERP → CRM) ============


class InventoryItemStock(BaseModel):
    """Single item with stock levels for CRM installations."""

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
    currency_code: str = "NGN"
    barcode: str | None = None
    is_below_reorder: bool = False

    # Computed aliases for CRM client backward compatibility
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
    currency_code: str = "NGN"
    barcode: str | None = None
    warehouses: list[WarehouseStock] = Field(default_factory=list)


class InventoryListResponse(BaseModel):
    """Response with inventory items and stock levels."""

    items: list[InventoryItemStock] = Field(default_factory=list)
    total_count: int = 0
    has_more: bool = False


# ============ Workforce / Department Sync (ERP → CRM) ============


class DepartmentMemberRead(BaseModel):
    """Member of a department for CRM workforce sync."""

    employee_id: UUID
    email: str | None = None
    full_name: str
    designation_name: str | None = None
    designation_id: UUID | None = None
    role: str | None = None
    is_active: bool = True


class DepartmentRead(BaseModel):
    """Department data for CRM service team mapping."""

    department_id: str
    department_name: str
    department_type: str = "operations"
    manager: DepartmentMemberRead | None = None
    members: list[DepartmentMemberRead] = Field(default_factory=list)


class DepartmentListResponse(BaseModel):
    """Response with departments and pagination."""

    departments: list[DepartmentRead] = Field(default_factory=list)
    total: int = 0
    limit: int = 100
    offset: int = 0


class WorkforceEmployeeRead(BaseModel):
    """Employee row for CRM workforce lookup."""

    employee_id: UUID
    email: str
    is_active: bool = True
    full_name: str | None = None
    department: str | None = None
    designation: str | None = None


class WorkforceEmployeeListResponse(BaseModel):
    """Paginated workforce employee response."""

    employees: list[WorkforceEmployeeRead] = Field(default_factory=list)
    total: int = 0
    limit: int = 100
    offset: int = 0
    has_more: bool = False


# ============ Contact Sync (ERP → CRM) ============


class CompanyContactRead(BaseModel):
    """Company/government customer for CRM contacts sync."""

    customer_id: UUID
    customer_code: str
    legal_name: str
    tax_id: str | None = None
    billing_address: dict | None = None
    primary_contact: dict | None = None
    crm_id: str | None = None


class CompanyListResponse(BaseModel):
    """Response with company contacts and pagination."""

    companies: list[CompanyContactRead] = Field(default_factory=list)
    total: int = 0
    limit: int = 100
    offset: int = 0
    has_more: bool = False


class PersonContactRead(BaseModel):
    """Individual customer as a person contact for CRM sync."""

    contact_id: UUID
    customer_code: str
    legal_name: str
    email: str | None = None
    phone: str | None = None
    crm_id: str | None = None


class PersonListResponse(BaseModel):
    """Response with person contacts and pagination."""

    contacts: list[PersonContactRead] = Field(default_factory=list)
    total: int = 0
    limit: int = 100
    offset: int = 0
    has_more: bool = False


# ============ Material Request Sync (CRM → ERP) ============


class CRMMaterialRequestItemPayload(BaseModel):
    """Single item in a CRM material request."""

    item_code: str = Field(..., max_length=50)
    quantity: Decimal = Field(..., gt=0)
    uom: str | None = Field(None, max_length=20)


class CRMMaterialRequestPayload(BaseModel):
    """Material request from DotMac CRM."""

    omni_id: str = Field(
        ..., max_length=36, description="CRM-side unique ID for idempotency"
    )
    request_type: str = Field(
        "ISSUE", description="PURCHASE, TRANSFER, ISSUE, MANUFACTURE"
    )
    items: list[CRMMaterialRequestItemPayload] = Field(..., min_length=1)
    project_crm_id: str | None = Field(None, max_length=36)
    ticket_crm_id: str | None = Field(None, max_length=36)
    requested_by_email: str | None = Field(None, max_length=255)
    schedule_date: str | None = Field(None, description="YYYY-MM-DD schedule date")
    remarks: str | None = None


class CRMMaterialRequestResponse(BaseModel):
    """Response after creating a material request from CRM."""

    request_id: UUID
    request_number: str
    status: str
    omni_id: str


class CRMMaterialRequestItemRead(BaseModel):
    """Item detail in a material request status response."""

    item_code: str
    item_name: str
    requested_qty: Decimal
    ordered_qty: Decimal
    uom: str | None = None


class CRMMaterialRequestStatusRead(BaseModel):
    """Full status of a material request for CRM."""

    request_id: UUID
    request_number: str
    status: str
    request_type: str
    items: list[CRMMaterialRequestItemRead] = Field(default_factory=list)
    created_at: datetime


# ============ Purchase Order Sync (CRM → ERP) ============


class CRMPurchaseOrderItemPayload(BaseModel):
    """Single line item in a CRM purchase order."""

    item_type: str = Field(..., max_length=50)
    description: str = Field(..., max_length=500)
    quantity: Decimal = Field(..., gt=0)
    unit_price: Decimal = Field(..., ge=0)
    amount: Decimal = Field(..., ge=0)
    cable_type: str | None = Field(None, max_length=100)
    fiber_count: int | None = None
    splice_count: int | None = None
    notes: str | None = None


class CRMPurchaseOrderPayload(BaseModel):
    """Purchase order from DotMac CRM (triggered on vendor quote approval)."""

    omni_work_order_id: str = Field(
        ..., max_length=36, description="CRM work order ID for idempotency"
    )
    omni_quote_id: str | None = Field(None, max_length=36)
    omni_project_id: str | None = Field(None, max_length=36)
    project_code: str | None = Field(None, max_length=80)
    project_name: str | None = Field(None, max_length=200)
    vendor_erp_id: str | None = Field(None, max_length=255)
    vendor_name: str | None = Field(None, max_length=255)
    vendor_code: str | None = Field(None, max_length=30)
    title: str = Field(..., max_length=500)
    currency: str = Field("NGN", max_length=3)
    subtotal: Decimal
    tax_total: Decimal = Decimal("0")
    total: Decimal
    approved_at: datetime | None = None
    approved_by_email: str | None = Field(None, max_length=255)
    items: list[CRMPurchaseOrderItemPayload] = Field(..., min_length=1)


class CRMPurchaseOrderResponse(BaseModel):
    """Response after creating a purchase order from CRM."""

    purchase_order_id: str
    po_id: UUID
    status: str
    omni_work_order_id: str
