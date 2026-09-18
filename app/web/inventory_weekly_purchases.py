"""Purchase Report routes; the original module mount and weekly URLs remain valid."""

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from app.services.inventory.purchase_report_web import PurchaseReportWebService
from app.web.deps import (
    WebAuthContext,
    get_db_for_org,
    require_inventory_access,
    require_web_permission,
)

# Preserve the old route-adapter symbol for callers/tests patching the weekly
# entry points. Both names resolve to the SAME all-line, AP-permission-gated adapter.
WeeklyPurchasesWebService = PurchaseReportWebService

router = APIRouter(
    prefix="/inventory/reports",
    tags=["inventory-web"],
    dependencies=[Depends(require_web_permission("inventory:stock:read"))],
)


def purchase_report_filters(
    period: str = "weekly",
    period_start: str | None = None,
    month: str | None = None,
    quarter: str | None = None,
    year: str | None = None,
    supplier: str | None = None,
    warehouse: str | None = None,
    category: str | None = None,
    search: str | None = Query(default=None, max_length=100),
) -> dict[str, str | None]:
    """Collect query input; validation and period calculations belong to the service."""
    return {
        "period": period,
        "period_start": period_start,
        "month": month,
        "quarter": quarter,
        "year": year,
        "supplier": supplier,
        "warehouse": warehouse,
        "category": category,
        "search": search,
    }


@router.get("/purchases", response_class=HTMLResponse)
def purchases_report(
    request: Request,
    filters: dict[str, str | None] = Depends(purchase_report_filters),
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_inventory_access),
    db: Session = Depends(get_db_for_org),
) -> HTMLResponse:
    return PurchaseReportWebService(db).report_response(request, auth, filters, page=page)


@router.get("/purchases/export")
def export_purchases(
    filters: dict[str, str | None] = Depends(purchase_report_filters),
    view: str = Query(default="lines", pattern="^(lines|invoices)$"),
    auth: WebAuthContext = Depends(require_inventory_access),
    db: Session = Depends(get_db_for_org),
) -> Response:
    return PurchaseReportWebService(db).export_response(auth, filters, view=view)


@router.get("/weekly-purchases", response_class=HTMLResponse)
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
    """Legacy links open the all-line Purchase Report with a weekly selection."""
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


@router.get("/weekly-purchases/export")
def export_weekly_purchases(
    week_start: str | None = None,
    supplier: str | None = None,
    warehouse: str | None = None,
    category: str | None = None,
    search: str | None = Query(default=None, max_length=100),
    auth: WebAuthContext = Depends(require_inventory_access),
    db: Session = Depends(get_db_for_org),
) -> Response:
    """Legacy weekly CSV also uses complete invoice-line coverage and the AP gate."""
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
