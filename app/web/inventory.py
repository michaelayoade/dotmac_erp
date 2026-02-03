"""
INV (Inventory) Web Routes for Operations Module.

HTML template routes for Items and Inventory Transactions.
"""

from typing import Optional

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.services.inventory.web import inv_web_service
from app.services.operations.inv_web import operations_inv_web_service
from app.web.deps import get_db, require_operations_access, WebAuthContext


router = APIRouter(prefix="/inventory", tags=["inventory-web"])


@router.get("/items", response_class=HTMLResponse)
def list_items(
    request: Request,
    auth: WebAuthContext = Depends(require_operations_access),
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
    auth: WebAuthContext = Depends(require_operations_access),
    db: Session = Depends(get_db),
):
    """New inventory item form page."""
    return inv_web_service.item_new_form_response(request, auth, db)


@router.post("/items/new")
def create_item(
    request: Request,
    auth: WebAuthContext = Depends(require_operations_access),
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
    auth: WebAuthContext = Depends(require_operations_access),
    db: Session = Depends(get_db),
):
    """Item detail page."""
    return inv_web_service.item_detail_response(request, auth, db, item_id)


@router.get("/items/{item_id}/edit", response_class=HTMLResponse)
def edit_item_form(
    request: Request,
    item_id: str,
    auth: WebAuthContext = Depends(require_operations_access),
    db: Session = Depends(get_db),
):
    """Edit inventory item form page."""
    return inv_web_service.item_edit_form_response(request, auth, db, item_id)


@router.post("/items/{item_id}/edit")
async def update_item(
    request: Request,
    item_id: str,
    auth: WebAuthContext = Depends(require_operations_access),
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
    auth: WebAuthContext = Depends(require_operations_access),
    db: Session = Depends(get_db),
):
    """Bulk delete items (if no transactions)."""
    from app.schemas.bulk_actions import BulkActionRequest
    from app.services.inventory.bulk import get_item_bulk_service

    body = await request.json()
    req = BulkActionRequest(**body)
    service = get_item_bulk_service(db, auth.organization_id, auth.user_id)
    return await service.bulk_delete(req.ids)


@router.post("/items/bulk-export")
async def bulk_export_items(
    request: Request,
    auth: WebAuthContext = Depends(require_operations_access),
    db: Session = Depends(get_db),
):
    """Export selected items to CSV."""
    from app.schemas.bulk_actions import BulkExportRequest
    from app.services.inventory.bulk import get_item_bulk_service

    body = await request.json()
    req = BulkExportRequest(**body)
    service = get_item_bulk_service(db, auth.organization_id, auth.user_id)
    return await service.bulk_export(req.ids, req.format)


@router.post("/items/bulk-activate")
async def bulk_activate_items(
    request: Request,
    auth: WebAuthContext = Depends(require_operations_access),
    db: Session = Depends(get_db),
):
    """Bulk activate items."""
    from app.schemas.bulk_actions import BulkActionRequest
    from app.services.inventory.bulk import get_item_bulk_service

    body = await request.json()
    req = BulkActionRequest(**body)
    service = get_item_bulk_service(db, auth.organization_id, auth.user_id)
    return await service.bulk_activate(req.ids)


@router.post("/items/bulk-deactivate")
async def bulk_deactivate_items(
    request: Request,
    auth: WebAuthContext = Depends(require_operations_access),
    db: Session = Depends(get_db),
):
    """Bulk deactivate items."""
    from app.schemas.bulk_actions import BulkActionRequest
    from app.services.inventory.bulk import get_item_bulk_service

    body = await request.json()
    req = BulkActionRequest(**body)
    service = get_item_bulk_service(db, auth.organization_id, auth.user_id)
    return await service.bulk_deactivate(req.ids)


@router.get("/transactions", response_class=HTMLResponse)
def list_transactions(
    request: Request,
    auth: WebAuthContext = Depends(require_operations_access),
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
    auth: WebAuthContext = Depends(require_operations_access),
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
    auth: WebAuthContext = Depends(require_operations_access),
    db: Session = Depends(get_db),
):
    """New item category form page."""
    return inv_web_service.category_form_response(request, auth, db)


@router.post("/categories/new")
def create_category(
    request: Request,
    auth: WebAuthContext = Depends(require_operations_access),
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
    auth: WebAuthContext = Depends(require_operations_access),
    db: Session = Depends(get_db),
):
    """Edit item category form page."""
    return inv_web_service.category_edit_form_response(request, auth, db, category_id)


@router.post("/categories/{category_id}/edit")
def update_category(
    request: Request,
    category_id: str,
    auth: WebAuthContext = Depends(require_operations_access),
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
    auth: WebAuthContext = Depends(require_operations_access),
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
    auth: WebAuthContext = Depends(require_operations_access),
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
    auth: WebAuthContext = Depends(require_operations_access),
    db: Session = Depends(get_db),
):
    """New warehouse form page."""
    return inv_web_service.warehouse_form_response(request, auth, db)


@router.post("/warehouses/new")
def create_warehouse(
    request: Request,
    auth: WebAuthContext = Depends(require_operations_access),
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
    auth: WebAuthContext = Depends(require_operations_access),
    db: Session = Depends(get_db),
):
    """Warehouse detail page."""
    return inv_web_service.warehouse_detail_response(request, auth, db, warehouse_id)


@router.get("/warehouses/{warehouse_id}/edit", response_class=HTMLResponse)
def edit_warehouse_form(
    request: Request,
    warehouse_id: str,
    auth: WebAuthContext = Depends(require_operations_access),
    db: Session = Depends(get_db),
):
    """Edit warehouse form page."""
    return inv_web_service.warehouse_edit_form_response(request, auth, db, warehouse_id)


@router.post("/warehouses/{warehouse_id}/edit")
def update_warehouse(
    request: Request,
    warehouse_id: str,
    auth: WebAuthContext = Depends(require_operations_access),
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
    auth: WebAuthContext = Depends(require_operations_access),
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
    auth: WebAuthContext = Depends(require_operations_access),
    db: Session = Depends(get_db),
):
    """New inventory receipt form page."""
    return inv_web_service.transaction_form_response(request, auth, "RECEIPT", db)


@router.post("/transactions/receipt/new")
def create_receipt_transaction(
    request: Request,
    auth: WebAuthContext = Depends(require_operations_access),
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
    auth: WebAuthContext = Depends(require_operations_access),
    db: Session = Depends(get_db),
):
    """New inventory issue form page."""
    return inv_web_service.transaction_form_response(request, auth, "ISSUE", db)


@router.post("/transactions/issue/new")
def create_issue_transaction(
    request: Request,
    auth: WebAuthContext = Depends(require_operations_access),
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
    auth: WebAuthContext = Depends(require_operations_access),
    db: Session = Depends(get_db),
):
    """New inventory transfer form page."""
    return inv_web_service.transaction_form_response(request, auth, "TRANSFER", db)


@router.post("/transactions/transfer/new")
def create_transfer_transaction(
    request: Request,
    auth: WebAuthContext = Depends(require_operations_access),
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
    auth: WebAuthContext = Depends(require_operations_access),
    db: Session = Depends(get_db),
):
    """New inventory adjustment form page."""
    return inv_web_service.transaction_form_response(request, auth, "ADJUSTMENT", db)


@router.post("/transactions/adjustment/new")
def create_adjustment_transaction(
    request: Request,
    auth: WebAuthContext = Depends(require_operations_access),
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


# ============================================================================
# Material Requests
# ============================================================================


@router.get("/material-requests", response_class=HTMLResponse)
def material_request_list(
    request: Request,
    status: Optional[str] = None,
    request_type: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    project_id: Optional[str] = None,
    auth: WebAuthContext = Depends(require_operations_access),
    db: Session = Depends(get_db),
):
    """Material request list page."""
    return operations_inv_web_service.material_request_list_response(
        request=request,
        auth=auth,
        db=db,
        status=status,
        request_type=request_type,
        start_date=start_date,
        end_date=end_date,
        project_id=project_id,
    )


@router.get("/material-requests/new", response_class=HTMLResponse)
def new_material_request_form(
    request: Request,
    auth: WebAuthContext = Depends(require_operations_access),
    db: Session = Depends(get_db),
):
    """New material request form."""
    return operations_inv_web_service.new_material_request_form_response(
        request=request,
        auth=auth,
        db=db,
    )


@router.post("/material-requests/new", response_class=HTMLResponse)
async def create_material_request(
    request: Request,
    auth: WebAuthContext = Depends(require_operations_access),
    db: Session = Depends(get_db),
):
    """Create new material request."""
    return await operations_inv_web_service.create_material_request_response(
        request=request,
        auth=auth,
        db=db,
    )


@router.get("/material-requests/reports/summary", response_class=HTMLResponse)
def material_request_report(
    request: Request,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    group_by: str = Query(default="status", pattern="^(status|type)$"),
    auth: WebAuthContext = Depends(require_operations_access),
    db: Session = Depends(get_db),
):
    """Material request summary report page."""
    return operations_inv_web_service.material_request_report_response(
        request=request,
        auth=auth,
        db=db,
        start_date=start_date,
        end_date=end_date,
        group_by=group_by,
    )


@router.get("/material-requests/{request_id}", response_class=HTMLResponse)
def material_request_detail(
    request: Request,
    request_id: str,
    auth: WebAuthContext = Depends(require_operations_access),
    db: Session = Depends(get_db),
):
    """Material request detail page."""
    return operations_inv_web_service.material_request_detail_response(
        request=request,
        request_id=request_id,
        auth=auth,
        db=db,
    )


@router.get("/material-requests/{request_id}/edit", response_class=HTMLResponse)
def edit_material_request_form(
    request: Request,
    request_id: str,
    auth: WebAuthContext = Depends(require_operations_access),
    db: Session = Depends(get_db),
):
    """Edit material request form."""
    return operations_inv_web_service.edit_material_request_form_response(
        request=request,
        request_id=request_id,
        auth=auth,
        db=db,
    )


@router.post("/material-requests/{request_id}/edit", response_class=HTMLResponse)
async def update_material_request(
    request: Request,
    request_id: str,
    auth: WebAuthContext = Depends(require_operations_access),
    db: Session = Depends(get_db),
):
    """Update material request."""
    return await operations_inv_web_service.update_material_request_response(
        request=request,
        request_id=request_id,
        auth=auth,
        db=db,
    )


@router.post("/material-requests/{request_id}/submit", response_class=HTMLResponse)
def submit_material_request(
    request: Request,
    request_id: str,
    auth: WebAuthContext = Depends(require_operations_access),
    db: Session = Depends(get_db),
):
    """Submit material request."""
    return operations_inv_web_service.submit_material_request_response(
        request_id=request_id,
        auth=auth,
        db=db,
    )


@router.post("/material-requests/{request_id}/cancel", response_class=HTMLResponse)
def cancel_material_request(
    request: Request,
    request_id: str,
    auth: WebAuthContext = Depends(require_operations_access),
    db: Session = Depends(get_db),
):
    """Cancel material request."""
    return operations_inv_web_service.cancel_material_request_response(
        request_id=request_id,
        auth=auth,
        db=db,
    )


# ============================================================================
# Transaction Detail & Reverse
# ============================================================================


@router.get("/transactions/{transaction_id}", response_class=HTMLResponse)
def transaction_detail(
    request: Request,
    transaction_id: str,
    auth: WebAuthContext = Depends(require_operations_access),
    db: Session = Depends(get_db),
):
    """Inventory transaction detail page."""
    return operations_inv_web_service.transaction_detail_response(
        request=request,
        transaction_id=transaction_id,
        auth=auth,
        db=db,
    )


@router.post("/transactions/{transaction_id}/reverse", response_class=HTMLResponse)
def reverse_transaction(
    request: Request,
    transaction_id: str,
    auth: WebAuthContext = Depends(require_operations_access),
    db: Session = Depends(get_db),
):
    """Reverse an inventory transaction."""
    return operations_inv_web_service.reverse_transaction_response(
        transaction_id=transaction_id,
        auth=auth,
        db=db,
    )


# ============================================================================
# Inventory Counts
# ============================================================================


@router.get("/counts", response_class=HTMLResponse)
def list_counts(
    request: Request,
    auth: WebAuthContext = Depends(require_operations_access),
    status: Optional[str] = None,
    search: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    db: Session = Depends(get_db),
):
    """Inventory counts list page."""
    return operations_inv_web_service.list_counts_response(
        request=request,
        auth=auth,
        db=db,
        status=status,
        search=search,
        page=page,
    )


@router.get("/counts/new", response_class=HTMLResponse)
def new_count_form(
    request: Request,
    auth: WebAuthContext = Depends(require_operations_access),
    db: Session = Depends(get_db),
):
    """New inventory count form."""
    return operations_inv_web_service.new_count_form_response(
        request=request,
        auth=auth,
        db=db,
    )


@router.post("/counts/new", response_class=HTMLResponse)
async def create_count(
    request: Request,
    auth: WebAuthContext = Depends(require_operations_access),
    db: Session = Depends(get_db),
):
    """Create new inventory count."""
    return await operations_inv_web_service.create_count_response(
        request=request,
        auth=auth,
        db=db,
    )


@router.get("/counts/{count_id}", response_class=HTMLResponse)
def count_detail(
    request: Request,
    count_id: str,
    auth: WebAuthContext = Depends(require_operations_access),
    db: Session = Depends(get_db),
):
    """Inventory count detail page."""
    return operations_inv_web_service.count_detail_response(
        request=request,
        count_id=count_id,
        auth=auth,
        db=db,
    )


@router.post("/counts/{count_id}/start", response_class=HTMLResponse)
def start_count(
    request: Request,
    count_id: str,
    auth: WebAuthContext = Depends(require_operations_access),
    db: Session = Depends(get_db),
):
    """Start an inventory count."""
    return operations_inv_web_service.start_count_response(
        count_id=count_id,
        auth=auth,
        db=db,
    )


@router.post("/counts/{count_id}/complete", response_class=HTMLResponse)
def complete_count(
    request: Request,
    count_id: str,
    auth: WebAuthContext = Depends(require_operations_access),
    db: Session = Depends(get_db),
):
    """Complete an inventory count."""
    return operations_inv_web_service.complete_count_response(
        count_id=count_id,
        auth=auth,
        db=db,
    )


@router.post("/counts/{count_id}/post", response_class=HTMLResponse)
def post_count(
    request: Request,
    count_id: str,
    auth: WebAuthContext = Depends(require_operations_access),
    db: Session = Depends(get_db),
):
    """Post inventory count adjustments."""
    return operations_inv_web_service.post_count_response(
        count_id=count_id,
        auth=auth,
        db=db,
    )


# ============================================================================
# Bill of Materials
# ============================================================================


@router.get("/boms", response_class=HTMLResponse)
def list_boms(
    request: Request,
    auth: WebAuthContext = Depends(require_operations_access),
    search: Optional[str] = None,
    status: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    db: Session = Depends(get_db),
):
    """Bill of Materials list page."""
    return operations_inv_web_service.list_boms_response(
        request=request,
        auth=auth,
        db=db,
        search=search,
        status=status,
        page=page,
    )


@router.get("/boms/new", response_class=HTMLResponse)
def new_bom_form(
    request: Request,
    auth: WebAuthContext = Depends(require_operations_access),
    db: Session = Depends(get_db),
):
    """New BOM form."""
    return operations_inv_web_service.new_bom_form_response(
        request=request,
        auth=auth,
        db=db,
    )


@router.post("/boms/new", response_class=HTMLResponse)
async def create_bom(
    request: Request,
    auth: WebAuthContext = Depends(require_operations_access),
    db: Session = Depends(get_db),
):
    """Create new BOM."""
    return await operations_inv_web_service.create_bom_response(
        request=request,
        auth=auth,
        db=db,
    )


@router.get("/boms/{bom_id}", response_class=HTMLResponse)
def bom_detail(
    request: Request,
    bom_id: str,
    auth: WebAuthContext = Depends(require_operations_access),
    db: Session = Depends(get_db),
):
    """BOM detail page."""
    return operations_inv_web_service.bom_detail_response(
        request=request,
        bom_id=bom_id,
        auth=auth,
        db=db,
    )


# ============================================================================
# Price Lists
# ============================================================================


@router.get("/price-lists", response_class=HTMLResponse)
def list_price_lists(
    request: Request,
    auth: WebAuthContext = Depends(require_operations_access),
    search: Optional[str] = None,
    list_type: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    db: Session = Depends(get_db),
):
    """Price lists page."""
    return operations_inv_web_service.list_price_lists_response(
        request=request,
        auth=auth,
        db=db,
        search=search,
        list_type=list_type,
        page=page,
    )


@router.get("/price-lists/new", response_class=HTMLResponse)
def new_price_list_form(
    request: Request,
    auth: WebAuthContext = Depends(require_operations_access),
    db: Session = Depends(get_db),
):
    """New price list form."""
    return operations_inv_web_service.new_price_list_form_response(
        request=request,
        auth=auth,
        db=db,
    )


@router.post("/price-lists/new", response_class=HTMLResponse)
async def create_price_list(
    request: Request,
    auth: WebAuthContext = Depends(require_operations_access),
    db: Session = Depends(get_db),
):
    """Create new price list."""
    return await operations_inv_web_service.create_price_list_response(
        request=request,
        auth=auth,
        db=db,
    )


# ============================================================================
# Lots & Serial Numbers
# ============================================================================


@router.get("/lots", response_class=HTMLResponse)
def list_lots(
    request: Request,
    auth: WebAuthContext = Depends(require_operations_access),
    search: Optional[str] = None,
    status: Optional[str] = None,
    warehouse: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    db: Session = Depends(get_db),
):
    """Lots and serial numbers list page."""
    return operations_inv_web_service.list_lots_response(
        request=request,
        auth=auth,
        db=db,
        search=search,
        status=status,
        warehouse=warehouse,
        page=page,
    )


@router.get("/lots/{lot_id}", response_class=HTMLResponse)
def lot_detail(
    request: Request,
    lot_id: str,
    auth: WebAuthContext = Depends(require_operations_access),
    db: Session = Depends(get_db),
):
    """Lot detail page."""
    return operations_inv_web_service.lot_detail_response(
        request=request,
        lot_id=lot_id,
        auth=auth,
        db=db,
    )


@router.post("/lots/{lot_id}/toggle-quarantine", response_class=HTMLResponse)
def toggle_lot_quarantine(
    request: Request,
    lot_id: str,
    auth: WebAuthContext = Depends(require_operations_access),
    db: Session = Depends(get_db),
):
    """Toggle lot quarantine status."""
    return operations_inv_web_service.toggle_lot_quarantine_response(
        lot_id=lot_id,
        auth=auth,
        db=db,
    )


# ============================================================================
# Inventory Reports
# ============================================================================


@router.get("/reports", response_class=HTMLResponse)
def inventory_reports_hub(
    request: Request,
    auth: WebAuthContext = Depends(require_operations_access),
    db: Session = Depends(get_db),
):
    """Inventory reports hub page."""
    return operations_inv_web_service.inventory_reports_hub_response(
        request=request,
        auth=auth,
    )


@router.get("/reports/stock-on-hand", response_class=HTMLResponse)
def stock_on_hand_report(
    request: Request,
    auth: WebAuthContext = Depends(require_operations_access),
    warehouse: Optional[str] = None,
    category: Optional[str] = None,
    show_zero: Optional[str] = None,
    format: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    db: Session = Depends(get_db),
):
    """Stock on hand report."""
    return operations_inv_web_service.stock_on_hand_report_response(
        request=request,
        auth=auth,
        db=db,
        warehouse=warehouse,
        category=category,
        show_zero=show_zero,
        format=format,
        page=page,
    )
