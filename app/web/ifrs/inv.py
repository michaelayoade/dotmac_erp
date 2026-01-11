"""
INV (Inventory) Web Routes.

HTML template routes for Items and Inventory Transactions.
"""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.web.deps import get_db, require_web_auth, WebAuthContext, base_context
from app.services.ifrs.inv.web import inv_web_service
from app.services.ifrs.inv.item import item_service, ItemInput
from app.models.ifrs.inv.item import ItemType, CostingMethod

templates = Jinja2Templates(directory="templates")

router = APIRouter(prefix="/inv", tags=["inv-web"])


# =============================================================================
# Items
# =============================================================================

@router.get("/items", response_class=HTMLResponse)
def list_items(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    search: Optional[str] = None,
    category: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    db: Session = Depends(get_db),
):
    """Items list page."""
    context = base_context(request, auth, "Inventory Items", "inv")
    context.update(
        inv_web_service.list_items_context(
            db,
            str(auth.organization_id),
            search=search,
            category=category,
            page=page,
        )
    )
    return templates.TemplateResponse(request, "ifrs/inv/items.html", context)


@router.get("/items/new", response_class=HTMLResponse)
def new_item_form(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """New inventory item form page."""
    context = base_context(request, auth, "New Item", "inv")
    context.update(inv_web_service.item_form_context(db, str(auth.organization_id)))
    return templates.TemplateResponse(request, "ifrs/inv/item_form.html", context)


@router.post("/items/new")
def create_item(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    item_code: str = Form(...),
    item_name: str = Form(...),
    category_id: str = Form(...),
    item_type: str = Form(default="INVENTORY"),
    base_uom: str = Form(default="EACH"),
    costing_method: str = Form(default="WEIGHTED_AVERAGE"),
    currency_code: str = Form(default="USD"),
    standard_cost: Optional[str] = Form(default=None),
    list_price: Optional[str] = Form(default=None),
    reorder_point: Optional[str] = Form(default=None),
    reorder_quantity: Optional[str] = Form(default=None),
    description: Optional[str] = Form(default=None),
    track_inventory: bool = Form(default=True),
    track_lots: bool = Form(default=False),
    track_serial_numbers: bool = Form(default=False),
    is_purchaseable: bool = Form(default=True),
    is_saleable: bool = Form(default=True),
    db: Session = Depends(get_db),
):
    """Create a new inventory item."""
    from decimal import Decimal

    try:
        input_data = ItemInput(
            item_code=item_code,
            item_name=item_name,
            category_id=UUID(category_id),
            item_type=ItemType(item_type),
            base_uom=base_uom,
            costing_method=CostingMethod(costing_method),
            currency_code=currency_code,
            standard_cost=Decimal(standard_cost) if standard_cost else None,
            list_price=Decimal(list_price) if list_price else None,
            reorder_point=Decimal(reorder_point) if reorder_point else None,
            reorder_quantity=Decimal(reorder_quantity) if reorder_quantity else None,
            description=description,
            track_inventory=track_inventory,
            track_lots=track_lots,
            track_serial_numbers=track_serial_numbers,
            is_purchaseable=is_purchaseable,
            is_saleable=is_saleable,
        )

        item_service.create_item(db, auth.organization_id, input_data)
        return RedirectResponse(url="/inv/items", status_code=303)

    except Exception as e:
        context = base_context(request, auth, "New Item", "inv")
        context.update(inv_web_service.item_form_context(db, str(auth.organization_id)))
        context["error"] = str(e)
        return templates.TemplateResponse(request, "ifrs/inv/item_form.html", context)


@router.get("/items/{item_id}", response_class=HTMLResponse)
def view_item(
    request: Request,
    item_id: str,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Item detail page."""
    context = base_context(request, auth, "Item Details", "inv")
    context.update(
        inv_web_service.item_detail_context(db, str(auth.organization_id), item_id)
    )
    return templates.TemplateResponse(request, "ifrs/inv/item_detail.html", context)


@router.get("/items/{item_id}/edit", response_class=HTMLResponse)
def edit_item_form(
    request: Request,
    item_id: str,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Edit inventory item form page."""
    context = base_context(request, auth, "Edit Item", "inv")
    context.update(inv_web_service.item_form_context(db, str(auth.organization_id), item_id))
    if not context.get("item"):
        return RedirectResponse(url="/inv/items", status_code=303)
    return templates.TemplateResponse(request, "ifrs/inv/item_form.html", context)


@router.post("/items/{item_id}/edit")
def update_item(
    request: Request,
    item_id: str,
    auth: WebAuthContext = Depends(require_web_auth),
    item_code: str = Form(...),
    item_name: str = Form(...),
    category_id: str = Form(...),
    item_type: str = Form(default="INVENTORY"),
    base_uom: str = Form(default="EACH"),
    costing_method: str = Form(default="WEIGHTED_AVERAGE"),
    currency_code: str = Form(default="USD"),
    standard_cost: Optional[str] = Form(default=None),
    list_price: Optional[str] = Form(default=None),
    reorder_point: Optional[str] = Form(default=None),
    reorder_quantity: Optional[str] = Form(default=None),
    description: Optional[str] = Form(default=None),
    track_inventory: bool = Form(default=False),
    track_lots: bool = Form(default=False),
    track_serial_numbers: bool = Form(default=False),
    is_purchaseable: bool = Form(default=False),
    is_saleable: bool = Form(default=False),
    db: Session = Depends(get_db),
):
    """Update an inventory item."""
    from decimal import Decimal

    try:
        updates = {
            "item_code": item_code,
            "item_name": item_name,
            "category_id": UUID(category_id),
            "item_type": ItemType(item_type),
            "base_uom": base_uom,
            "costing_method": CostingMethod(costing_method),
            "currency_code": currency_code,
            "standard_cost": Decimal(standard_cost) if standard_cost else None,
            "list_price": Decimal(list_price) if list_price else None,
            "reorder_point": Decimal(reorder_point) if reorder_point else None,
            "reorder_quantity": Decimal(reorder_quantity) if reorder_quantity else None,
            "description": description,
            "track_inventory": track_inventory,
            "track_lots": track_lots,
            "track_serial_numbers": track_serial_numbers,
            "is_purchaseable": is_purchaseable,
            "is_saleable": is_saleable,
        }

        item_service.update_item(db, auth.organization_id, UUID(item_id), updates)
        return RedirectResponse(url=f"/inv/items/{item_id}", status_code=303)

    except Exception as e:
        context = base_context(request, auth, "Edit Item", "inv")
        context.update(inv_web_service.item_form_context(db, str(auth.organization_id), item_id))
        context["error"] = str(e)
        return templates.TemplateResponse(request, "ifrs/inv/item_form.html", context)


# =============================================================================
# Transactions
# =============================================================================

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
    context = base_context(request, auth, "Inventory Transactions", "inv")
    context.update(
        inv_web_service.list_transactions_context(
            db,
            str(auth.organization_id),
            search=search,
            transaction_type=transaction_type,
            page=page,
        )
    )
    return templates.TemplateResponse(request, "ifrs/inv/transactions.html", context)
