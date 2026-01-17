"""
INV (Inventory) Web Routes.

HTML template routes for Items and Inventory Transactions.
"""

from typing import Optional

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.services.ifrs.inv.web import inv_web_service
from app.web.deps import get_db, require_web_auth, WebAuthContext


router = APIRouter(prefix="/inv", tags=["inv-web"])


@router.get("/items", response_class=HTMLResponse)
def list_items(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    search: Optional[str] = None,
    category: Optional[str] = None,
    status: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=10, le=500),
    db: Session = Depends(get_db),
):
    """Items list page."""
    return inv_web_service.list_items_response(request, auth, search, category, status, page, limit, db)


@router.get("/items/new", response_class=HTMLResponse)
def new_item_form(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """New inventory item form page."""
    return inv_web_service.item_new_form_response(request, auth, db)


@router.post("/items/new")
def create_item(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    item_code: str = Form(...),
    item_name: str = Form(...),
    category_id: str = Form(...),
    item_type: str = Form(default="INVENTORY"),
    base_uom: str = Form(default="EACH"),
    purchase_uom: Optional[str] = Form(default=None),
    sales_uom: Optional[str] = Form(default=None),
    costing_method: str = Form(default="WEIGHTED_AVERAGE"),
    currency_code: Optional[str] = Form(default=None),
    standard_cost: Optional[str] = Form(default=None),
    list_price: Optional[str] = Form(default=None),
    reorder_point: Optional[str] = Form(default=None),
    reorder_quantity: Optional[str] = Form(default=None),
    minimum_stock: Optional[str] = Form(default=None),
    maximum_stock: Optional[str] = Form(default=None),
    lead_time_days: Optional[str] = Form(default=None),
    description: Optional[str] = Form(default=None),
    track_inventory: Optional[str] = Form(default=None),
    track_lots: Optional[str] = Form(default=None),
    track_serial_numbers: Optional[str] = Form(default=None),
    is_purchaseable: Optional[str] = Form(default=None),
    is_saleable: Optional[str] = Form(default=None),
    db: Session = Depends(get_db),
):
    """Create a new inventory item."""
    # HTML checkboxes send nothing when unchecked, so we check for presence
    return inv_web_service.create_item_response(
        request,
        auth,
        item_code,
        item_name,
        category_id,
        item_type,
        base_uom,
        purchase_uom,
        sales_uom,
        costing_method,
        currency_code,
        standard_cost,
        list_price,
        reorder_point,
        reorder_quantity,
        minimum_stock,
        maximum_stock,
        lead_time_days,
        description,
        track_inventory is not None,
        track_lots is not None,
        track_serial_numbers is not None,
        is_purchaseable is not None,
        is_saleable is not None,
        db,
    )


@router.get("/items/{item_id}", response_class=HTMLResponse)
def view_item(
    request: Request,
    item_id: str,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Item detail page."""
    return inv_web_service.item_detail_response(request, auth, db, item_id)


@router.get("/items/{item_id}/edit", response_class=HTMLResponse)
def edit_item_form(
    request: Request,
    item_id: str,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Edit inventory item form page."""
    return inv_web_service.item_edit_form_response(request, auth, db, item_id)


@router.post("/items/{item_id}/edit")
async def update_item(
    request: Request,
    item_id: str,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Update an inventory item."""
    form = await request.form()
    item_code = form.get("item_code")
    item_name = form.get("item_name")
    category_id = form.get("category_id")
    item_type = form.get("item_type") or "INVENTORY"
    base_uom = form.get("base_uom") or "EACH"
    purchase_uom = form.get("purchase_uom")
    sales_uom = form.get("sales_uom")
    costing_method = form.get("costing_method") or "WEIGHTED_AVERAGE"
    currency_code = form.get("currency_code")
    standard_cost = form.get("standard_cost")
    list_price = form.get("list_price")
    reorder_point = form.get("reorder_point")
    reorder_quantity = form.get("reorder_quantity")
    minimum_stock = form.get("minimum_stock")
    maximum_stock = form.get("maximum_stock")
    lead_time_days = form.get("lead_time_days")
    description = form.get("description")

    # HTML checkboxes send nothing when unchecked, so we check for presence.
    track_inventory = form.get("track_inventory") is not None
    track_lots = form.get("track_lots") is not None
    track_serial_numbers = form.get("track_serial_numbers") is not None
    is_purchaseable = form.get("is_purchaseable") is not None
    is_saleable = form.get("is_saleable") is not None

    return inv_web_service.update_item_response(
        request,
        auth,
        item_id,
        item_code,
        item_name,
        category_id,
        item_type,
        base_uom,
        purchase_uom,
        sales_uom,
        costing_method,
        currency_code,
        standard_cost,
        list_price,
        reorder_point,
        reorder_quantity,
        minimum_stock,
        maximum_stock,
        lead_time_days,
        description,
        track_inventory,
        track_lots,
        track_serial_numbers,
        is_purchaseable,
        is_saleable,
        db,
    )


# ═══════════════════════════════════════════════════════════════════
# Bulk Actions - Items
# ═══════════════════════════════════════════════════════════════════


@router.post("/items/bulk-delete")
async def bulk_delete_items(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Bulk delete items (if no transactions)."""
    from app.schemas.bulk_actions import BulkActionRequest
    from app.services.ifrs.inv.bulk import get_item_bulk_service

    body = await request.json()
    req = BulkActionRequest(**body)
    service = get_item_bulk_service(db, auth.organization_id, auth.user_id)
    return await service.bulk_delete(req.ids)


@router.post("/items/bulk-export")
async def bulk_export_items(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Export selected items to CSV."""
    from app.schemas.bulk_actions import BulkExportRequest
    from app.services.ifrs.inv.bulk import get_item_bulk_service

    body = await request.json()
    req = BulkExportRequest(**body)
    service = get_item_bulk_service(db, auth.organization_id, auth.user_id)
    return await service.bulk_export(req.ids, req.format)


@router.post("/items/bulk-activate")
async def bulk_activate_items(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Bulk activate items."""
    from app.schemas.bulk_actions import BulkActionRequest
    from app.services.ifrs.inv.bulk import get_item_bulk_service

    body = await request.json()
    req = BulkActionRequest(**body)
    service = get_item_bulk_service(db, auth.organization_id, auth.user_id)
    return await service.bulk_activate(req.ids)


@router.post("/items/bulk-deactivate")
async def bulk_deactivate_items(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Bulk deactivate items."""
    from app.schemas.bulk_actions import BulkActionRequest
    from app.services.ifrs.inv.bulk import get_item_bulk_service

    body = await request.json()
    req = BulkActionRequest(**body)
    service = get_item_bulk_service(db, auth.organization_id, auth.user_id)
    return await service.bulk_deactivate(req.ids)


@router.get("/transactions", response_class=HTMLResponse)
def list_transactions(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    search: Optional[str] = None,
    transaction_type: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    db: Session = Depends(get_db),
):
    """Inventory transactions list page."""
    return inv_web_service.list_transactions_response(
        request,
        auth,
        search,
        transaction_type,
        page,
        db,
    )


# ============================================================================
# Item Categories
# ============================================================================


@router.get("/categories", response_class=HTMLResponse)
def list_categories(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    search: Optional[str] = None,
    status: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=10, le=500),
    db: Session = Depends(get_db),
):
    """Item categories list page."""
    return inv_web_service.list_categories_response(request, auth, search, status, page, limit, db)


@router.get("/categories/new", response_class=HTMLResponse)
def new_category_form(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """New item category form page."""
    return inv_web_service.category_form_response(request, auth, db)


@router.post("/categories/new")
def create_category(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    category_code: str = Form(...),
    category_name: str = Form(...),
    inventory_account_id: str = Form(...),
    cogs_account_id: str = Form(...),
    revenue_account_id: str = Form(...),
    inventory_adjustment_account_id: str = Form(...),
    description: Optional[str] = Form(default=None),
    parent_category_id: Optional[str] = Form(default=None),
    purchase_variance_account_id: Optional[str] = Form(default=None),
    db: Session = Depends(get_db),
):
    """Create a new item category."""
    return inv_web_service.create_category_response(
        request,
        auth,
        category_code,
        category_name,
        inventory_account_id,
        cogs_account_id,
        revenue_account_id,
        inventory_adjustment_account_id,
        description,
        parent_category_id,
        purchase_variance_account_id,
        db,
    )


@router.get("/categories/{category_id}/edit", response_class=HTMLResponse)
def edit_category_form(
    request: Request,
    category_id: str,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Edit item category form page."""
    return inv_web_service.category_edit_form_response(request, auth, db, category_id)


@router.post("/categories/{category_id}/edit")
def update_category(
    request: Request,
    category_id: str,
    auth: WebAuthContext = Depends(require_web_auth),
    category_code: str = Form(...),
    category_name: str = Form(...),
    inventory_account_id: str = Form(...),
    cogs_account_id: str = Form(...),
    revenue_account_id: str = Form(...),
    inventory_adjustment_account_id: str = Form(...),
    description: Optional[str] = Form(default=None),
    parent_category_id: Optional[str] = Form(default=None),
    purchase_variance_account_id: Optional[str] = Form(default=None),
    db: Session = Depends(get_db),
):
    """Update an item category."""
    return inv_web_service.update_category_response(
        request,
        auth,
        category_id,
        category_code,
        category_name,
        inventory_account_id,
        cogs_account_id,
        revenue_account_id,
        inventory_adjustment_account_id,
        description,
        parent_category_id,
        purchase_variance_account_id,
        db,
    )


@router.post("/categories/{category_id}/toggle")
def toggle_category_status(
    request: Request,
    category_id: str,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Toggle category active/inactive status."""
    return inv_web_service.toggle_category_status_response(request, auth, category_id, db)


# ============================================================================
# Warehouses
# ============================================================================


@router.get("/warehouses", response_class=HTMLResponse)
def list_warehouses(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    search: Optional[str] = None,
    status: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=10, le=500),
    db: Session = Depends(get_db),
):
    """Warehouses list page."""
    return inv_web_service.list_warehouses_response(request, auth, search, status, page, limit, db)


@router.get("/warehouses/new", response_class=HTMLResponse)
def new_warehouse_form(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """New warehouse form page."""
    return inv_web_service.warehouse_form_response(request, auth, db)


@router.post("/warehouses/new")
def create_warehouse(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    warehouse_code: str = Form(...),
    warehouse_name: str = Form(...),
    description: Optional[str] = Form(default=None),
    contact_name: Optional[str] = Form(default=None),
    contact_phone: Optional[str] = Form(default=None),
    contact_email: Optional[str] = Form(default=None),
    address_line1: Optional[str] = Form(default=None),
    address_line2: Optional[str] = Form(default=None),
    address_city: Optional[str] = Form(default=None),
    address_state: Optional[str] = Form(default=None),
    address_postal_code: Optional[str] = Form(default=None),
    address_country: Optional[str] = Form(default=None),
    is_receiving: Optional[str] = Form(default=None),
    is_shipping: Optional[str] = Form(default=None),
    is_consignment: Optional[str] = Form(default=None),
    is_transit: Optional[str] = Form(default=None),
    db: Session = Depends(get_db),
):
    """Create a new warehouse."""
    return inv_web_service.create_warehouse_response(
        request,
        auth,
        warehouse_code,
        warehouse_name,
        description,
        contact_name,
        contact_phone,
        contact_email,
        address_line1,
        address_line2,
        address_city,
        address_state,
        address_postal_code,
        address_country,
        is_receiving is not None,
        is_shipping is not None,
        is_consignment is not None,
        is_transit is not None,
        db,
    )


@router.get("/warehouses/{warehouse_id}", response_class=HTMLResponse)
def view_warehouse(
    request: Request,
    warehouse_id: str,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Warehouse detail page."""
    return inv_web_service.warehouse_detail_response(request, auth, db, warehouse_id)


@router.get("/warehouses/{warehouse_id}/edit", response_class=HTMLResponse)
def edit_warehouse_form(
    request: Request,
    warehouse_id: str,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Edit warehouse form page."""
    return inv_web_service.warehouse_edit_form_response(request, auth, db, warehouse_id)


@router.post("/warehouses/{warehouse_id}/edit")
def update_warehouse(
    request: Request,
    warehouse_id: str,
    auth: WebAuthContext = Depends(require_web_auth),
    warehouse_code: str = Form(...),
    warehouse_name: str = Form(...),
    description: Optional[str] = Form(default=None),
    contact_name: Optional[str] = Form(default=None),
    contact_phone: Optional[str] = Form(default=None),
    contact_email: Optional[str] = Form(default=None),
    address_line1: Optional[str] = Form(default=None),
    address_line2: Optional[str] = Form(default=None),
    address_city: Optional[str] = Form(default=None),
    address_state: Optional[str] = Form(default=None),
    address_postal_code: Optional[str] = Form(default=None),
    address_country: Optional[str] = Form(default=None),
    is_receiving: Optional[str] = Form(default=None),
    is_shipping: Optional[str] = Form(default=None),
    is_consignment: Optional[str] = Form(default=None),
    is_transit: Optional[str] = Form(default=None),
    db: Session = Depends(get_db),
):
    """Update a warehouse."""
    return inv_web_service.update_warehouse_response(
        request,
        auth,
        warehouse_id,
        warehouse_code,
        warehouse_name,
        description,
        contact_name,
        contact_phone,
        contact_email,
        address_line1,
        address_line2,
        address_city,
        address_state,
        address_postal_code,
        address_country,
        is_receiving is not None,
        is_shipping is not None,
        is_consignment is not None,
        is_transit is not None,
        db,
    )


@router.post("/warehouses/{warehouse_id}/toggle")
def toggle_warehouse_status(
    request: Request,
    warehouse_id: str,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Toggle warehouse active/inactive status."""
    return inv_web_service.toggle_warehouse_status_response(request, auth, warehouse_id, db)


# ============================================================================
# Inventory Transactions (Manual Entry)
# ============================================================================


@router.get("/transactions/receipt/new", response_class=HTMLResponse)
def new_receipt_form(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """New inventory receipt form page."""
    return inv_web_service.transaction_form_response(request, auth, "RECEIPT", db)


@router.post("/transactions/receipt/new")
def create_receipt_transaction(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    item_id: str = Form(...),
    warehouse_id: str = Form(...),
    quantity: str = Form(...),
    unit_cost: str = Form(...),
    transaction_date: str = Form(...),
    reference: Optional[str] = Form(default=None),
    notes: Optional[str] = Form(default=None),
    lot_number: Optional[str] = Form(default=None),
    db: Session = Depends(get_db),
):
    """Create a manual inventory receipt."""
    return inv_web_service.create_transaction_response(
        request, auth, "RECEIPT", item_id, warehouse_id, quantity,
        unit_cost, transaction_date, reference, notes, lot_number, db
    )


@router.get("/transactions/issue/new", response_class=HTMLResponse)
def new_issue_form(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """New inventory issue form page."""
    return inv_web_service.transaction_form_response(request, auth, "ISSUE", db)


@router.post("/transactions/issue/new")
def create_issue_transaction(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    item_id: str = Form(...),
    warehouse_id: str = Form(...),
    quantity: str = Form(...),
    unit_cost: str = Form(...),
    transaction_date: str = Form(...),
    reference: Optional[str] = Form(default=None),
    notes: Optional[str] = Form(default=None),
    lot_number: Optional[str] = Form(default=None),
    db: Session = Depends(get_db),
):
    """Create a manual inventory issue."""
    return inv_web_service.create_transaction_response(
        request, auth, "ISSUE", item_id, warehouse_id, quantity,
        unit_cost, transaction_date, reference, notes, lot_number, db
    )


@router.get("/transactions/transfer/new", response_class=HTMLResponse)
def new_transfer_form(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """New inventory transfer form page."""
    return inv_web_service.transaction_form_response(request, auth, "TRANSFER", db)


@router.post("/transactions/transfer/new")
def create_transfer_transaction(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    item_id: str = Form(...),
    from_warehouse_id: str = Form(...),
    to_warehouse_id: str = Form(...),
    quantity: str = Form(...),
    transaction_date: str = Form(...),
    reference: Optional[str] = Form(default=None),
    notes: Optional[str] = Form(default=None),
    lot_number: Optional[str] = Form(default=None),
    db: Session = Depends(get_db),
):
    """Create an inventory transfer."""
    return inv_web_service.create_transfer_response(
        request, auth, item_id, from_warehouse_id, to_warehouse_id,
        quantity, transaction_date, reference, notes, lot_number, db
    )


@router.get("/transactions/adjustment/new", response_class=HTMLResponse)
def new_adjustment_form(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """New inventory adjustment form page."""
    return inv_web_service.transaction_form_response(request, auth, "ADJUSTMENT", db)


@router.post("/transactions/adjustment/new")
def create_adjustment_transaction(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    item_id: str = Form(...),
    warehouse_id: str = Form(...),
    quantity: str = Form(...),
    unit_cost: str = Form(...),
    transaction_date: str = Form(...),
    adjustment_type: str = Form(...),
    reason: str = Form(...),
    reference: Optional[str] = Form(default=None),
    db: Session = Depends(get_db),
):
    """Create an inventory adjustment."""
    return inv_web_service.create_adjustment_response(
        request, auth, item_id, warehouse_id, quantity, unit_cost,
        transaction_date, adjustment_type, reason, reference, db
    )
