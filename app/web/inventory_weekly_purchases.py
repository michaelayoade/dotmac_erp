"""Read-only weekly purchase routes, mounted with the inventory module."""

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from app.services.inventory.weekly_purchases_web import WeeklyPurchasesWebService
from app.web.deps import (
    WebAuthContext,
    get_db_for_org,
    require_inventory_access,
    require_web_permission,
)

router = APIRouter(
    prefix="/inventory/reports/weekly-purchases",
    tags=["inventory-web"],
    dependencies=[Depends(require_web_permission("inventory:stock:read"))],
)


@router.get("", response_class=HTMLResponse)
def weekly_purchases_report(
    request: Request,
    week_start: str | None = None,
    supplier: str | None = None,
    warehouse: str | None = None,
    category: str | None = None,
    search: str | None = Query(default=None, max_length=100),
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_inventory_access),
    db: Session = Depends(get_db_for_org),
) -> HTMLResponse:
    """Show posted stock-item purchases for one Monday-Sunday week."""
    return WeeklyPurchasesWebService(db).report_response(
        request,
        auth,
        filters={
            "week_start": week_start,
            "supplier": supplier,
            "warehouse": warehouse,
            "category": category,
            "search": search,
        },
        page=page,
    )


@router.get("/export")
def export_weekly_purchases(
    week_start: str | None = None,
    supplier: str | None = None,
    warehouse: str | None = None,
    category: str | None = None,
    search: str | None = Query(default=None, max_length=100),
    auth: WebAuthContext = Depends(require_inventory_access),
    db: Session = Depends(get_db_for_org),
) -> Response:
    """Export all matching lines, not just the visible page."""
    return WeeklyPurchasesWebService(db).export_response(
        auth,
        filters={
            "week_start": week_start,
            "supplier": supplier,
            "warehouse": warehouse,
            "category": category,
            "search": search,
        },
    )
