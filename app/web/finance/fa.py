"""
FA (Fixed Assets) Web Routes.

HTML template routes for Assets and Depreciation.
"""

from typing import Optional

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.services.finance.fa.web import fa_web_service
from app.templates import templates
from app.web.deps import get_db, require_finance_access, WebAuthContext, base_context


router = APIRouter(prefix="/fa", tags=["fa-web"])


# =============================================================================
# Assets
# =============================================================================

@router.get("/assets", response_class=HTMLResponse)
def list_assets(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    search: Optional[str] = None,
    category: Optional[str] = None,
    status: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    db: Session = Depends(get_db),
):
    """Assets list page."""
    context = base_context(request, auth, "Fixed Assets", "fa")
    context.update(
        fa_web_service.list_assets_context(
            db,
            str(auth.organization_id),
            search=search,
            category=category,
            status=status,
            page=page,
        )
    )
    return templates.TemplateResponse(request, "finance/fa/assets.html", context)


@router.get("/assets/new", response_class=HTMLResponse)
def new_asset_form(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """New asset form page."""
    return fa_web_service.asset_new_form_response(request, auth, db)


@router.post("/assets/new")
def create_asset(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    asset_name: str = Form(...),
    category_id: str = Form(...),
    acquisition_date: str = Form(...),
    acquisition_cost: str = Form(...),
    currency_code: Optional[str] = Form(default=None),
    description: Optional[str] = Form(default=None),
    db: Session = Depends(get_db),
):
    """Create a new fixed asset."""
    return fa_web_service.create_asset_response(
        request,
        auth,
        asset_name,
        category_id,
        acquisition_date,
        acquisition_cost,
        currency_code,
        description,
        db,
    )


# =============================================================================
# Bulk Actions - Assets
# =============================================================================


@router.post("/assets/bulk-delete")
async def bulk_delete_assets(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Bulk delete assets (only DRAFT status)."""
    from app.schemas.bulk_actions import BulkActionRequest
    from app.services.finance.fa.bulk import get_asset_bulk_service

    body = await request.json()
    req = BulkActionRequest(**body)
    service = get_asset_bulk_service(db, auth.organization_id, auth.user_id)
    return await service.bulk_delete(req.ids)


@router.post("/assets/bulk-export")
async def bulk_export_assets(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Export selected assets to CSV."""
    from app.schemas.bulk_actions import BulkExportRequest
    from app.services.finance.fa.bulk import get_asset_bulk_service

    body = await request.json()
    req = BulkExportRequest(**body)
    service = get_asset_bulk_service(db, auth.organization_id, auth.user_id)
    return await service.bulk_export(req.ids, req.format)


# =============================================================================
# Asset Categories
# =============================================================================

@router.get("/categories", response_class=HTMLResponse)
def list_categories(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    is_active: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    db: Session = Depends(get_db),
):
    """Asset categories list page."""
    active_filter = None
    if is_active == "true":
        active_filter = True
    elif is_active == "false":
        active_filter = False

    return fa_web_service.list_categories_response(
        request,
        auth,
        active_filter,
        page,
        db,
    )


@router.get("/categories/new", response_class=HTMLResponse)
def new_category_form(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """New asset category form page."""
    return fa_web_service.new_category_form_response(request, auth, db)


@router.post("/categories/new", response_class=HTMLResponse)
async def create_category(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Create a new asset category."""
    return await fa_web_service.create_category_response(request, auth, db)


@router.get("/categories/{category_id}/edit", response_class=HTMLResponse)
def edit_category_form(
    request: Request,
    category_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Edit asset category form page."""
    return fa_web_service.edit_category_form_response(request, auth, category_id, db)


@router.post("/categories/{category_id}/edit", response_class=HTMLResponse)
async def update_category(
    request: Request,
    category_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Update an existing asset category."""
    return await fa_web_service.update_category_response(request, auth, category_id, db)


@router.post("/categories/{category_id}/toggle", response_class=HTMLResponse)
def toggle_category(
    category_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Toggle asset category active/inactive status."""
    return fa_web_service.toggle_category_response(auth, category_id, db)


# =============================================================================
# Depreciation
# =============================================================================

@router.get("/depreciation", response_class=HTMLResponse)
def depreciation_schedule(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    asset_id: Optional[str] = None,
    period: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Depreciation schedule page."""
    context = base_context(request, auth, "Depreciation Schedule", "fa")
    context.update(
        fa_web_service.depreciation_context(
            db,
            str(auth.organization_id),
            asset_id=asset_id,
            period=period,
        )
    )
    return templates.TemplateResponse(request, "finance/fa/depreciation.html", context)
