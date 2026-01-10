"""
INV (Inventory) Web Routes.

HTML template routes for Items and Inventory Transactions.
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.web.deps import get_db, require_web_auth, WebAuthContext, base_context
from app.services.ifrs.inv.web import inv_web_service

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
