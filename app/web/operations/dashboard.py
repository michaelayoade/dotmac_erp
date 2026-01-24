"""
Operations Dashboard Web Routes.

Dashboard page for the Operations module.
"""

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.services.finance.inv.web import inv_web_service
from app.services.finance.inv.material_request_web import material_request_web_service
from app.services.support.web import support_web_service
from app.templates import templates
from app.web.deps import base_context, get_db, require_operations_access, WebAuthContext

router = APIRouter(tags=["operations-dashboard-web"])


@router.get("/", response_class=HTMLResponse)
@router.get("/dashboard", response_class=HTMLResponse)
def operations_dashboard(
    request: Request,
    auth: WebAuthContext = Depends(require_operations_access),
    db: Session = Depends(get_db),
):
    """Operations module dashboard page."""
    context = base_context(request, auth, "Operations Dashboard", "dashboard", db=db)

    # Get low stock alerts for dashboard
    low_stock_context = inv_web_service.low_stock_dashboard_context(
        db, str(auth.organization_id), include_below_minimum=True
    )
    context.update(low_stock_context)

    # Get recent transactions summary
    transactions_context = inv_web_service.list_transactions_context(
        db,
        str(auth.organization_id),
        search=None,
        transaction_type=None,
        page=1,
        limit=10,
    )
    context["recent_transactions"] = transactions_context.get("transactions", [])

    # Get material request stats for dashboard widget
    mr_context = material_request_web_service.dashboard_context(db, str(auth.organization_id))
    context.update(mr_context)

    # Get support ticket stats for dashboard widget
    support_context = support_web_service.dashboard_context(db, str(auth.organization_id))
    context.update(support_context)

    # Get stock count stats for dashboard widget
    try:
        from app.models.finance.inv.inventory_count import InventoryCount, CountStatus

        count_stats = {
            "draft": db.query(InventoryCount).filter(
                InventoryCount.organization_id == auth.organization_id,
                InventoryCount.status == CountStatus.DRAFT
            ).count(),
            "in_progress": db.query(InventoryCount).filter(
                InventoryCount.organization_id == auth.organization_id,
                InventoryCount.status == CountStatus.IN_PROGRESS
            ).count(),
            "completed": db.query(InventoryCount).filter(
                InventoryCount.organization_id == auth.organization_id,
                InventoryCount.status == CountStatus.COMPLETED
            ).count(),
        }
        context["count_stats"] = count_stats
    except Exception:
        context["count_stats"] = None

    # Get expiring lots for dashboard widget
    try:
        from app.models.finance.inv.inventory_lot import InventoryLot
        from app.models.finance.inv.item import Item

        now = datetime.now().date()
        expiring_soon = now + timedelta(days=30)

        expiring_lots = db.query(InventoryLot).filter(
            InventoryLot.organization_id == auth.organization_id,
            InventoryLot.expiry_date != None,
            InventoryLot.expiry_date > now,
            InventoryLot.expiry_date <= expiring_soon,
            InventoryLot.quantity_available > 0
        ).order_by(InventoryLot.expiry_date).limit(10).all()

        # Load item info for each lot
        for lot in expiring_lots:
            lot.item = db.get(Item, lot.item_id)

        context["expiring_lots"] = expiring_lots
        context["expiring_lots_count"] = len(expiring_lots)
    except Exception:
        context["expiring_lots"] = []
        context["expiring_lots_count"] = 0

    return templates.TemplateResponse(request, "operations/dashboard.html", context)
