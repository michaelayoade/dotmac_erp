"""
DotMac CRM Sync Schemas - Pydantic models for CRM sync API.
"""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, computed_field

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


class CRMTicketPayload(BaseModel):
    """Support ticket data from DotMac CRM."""

    crm_id: str = Field(..., description="UUID from CRM")
    subject: str = Field(..., max_length=255)
    ticket_number: str | None = Field(None, max_length=40)
    ticket_type: str | None = Field(None, max_length=80)
    status: str = Field("active", description="active, completed, cancelled")
    priority: str | None = Field(None, max_length=40)
    customer_name: str | None = Field(None, max_length=200)
    customer_crm_id: str | None = Field(None, max_length=36)
    metadata: dict | None = None


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
    scheduled_start: datetime | None = None
    scheduled_end: datetime | None = None
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
