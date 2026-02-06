"""
Operations dashboard web view service.

Provides view-focused data for the Operations dashboard.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.services.inventory.material_request_web import material_request_web_service
from app.services.inventory.web import inv_web_service
from app.services.support.web import support_web_service

logger = logging.getLogger(__name__)


class OperationsDashboardWebService:
    """View service for the operations dashboard web route."""

    @staticmethod
    def dashboard_context(db: Session, organization_id) -> dict:
        context: dict = {}

        try:
            low_stock_context = inv_web_service.low_stock_dashboard_context(
                db, str(organization_id), include_below_minimum=True
            )
            context.update(low_stock_context)
        except Exception:
            logger.exception("Failed to load low stock dashboard context")
            context.update(
                {
                    "items": [],
                    "total_items": 0,
                    "critical_count": 0,
                    "low_count": 0,
                    "warning_count": 0,
                    "total_suggested_value": "-",
                    "total_suggested_value_raw": 0,
                    "include_below_minimum": True,
                }
            )

        try:
            transactions_context = inv_web_service.list_transactions_context(
                db,
                str(organization_id),
                search=None,
                transaction_type=None,
                page=1,
                limit=10,
            )
            context["recent_transactions"] = transactions_context.get(
                "transactions", []
            )
        except Exception:
            logger.exception(
                "Failed to load recent transactions for operations dashboard"
            )
            context["recent_transactions"] = []

        try:
            mr_context = material_request_web_service.dashboard_context(
                db, str(organization_id)
            )
            context.update(mr_context)
        except Exception:
            logger.exception("Failed to load material request dashboard context")
            context["material_request_stats"] = None
            context["recent_pending_requests"] = []

        try:
            support_context = support_web_service.dashboard_context(
                db, str(organization_id)
            )
            context.update(support_context)
        except Exception:
            logger.exception("Failed to load support dashboard context")
            context["support_stats"] = None
            context["recent_open_tickets"] = []

        try:
            from app.models.inventory.inventory_count import CountStatus, InventoryCount

            count_stats = {
                "draft": db.query(InventoryCount)
                .filter(
                    InventoryCount.organization_id == organization_id,
                    InventoryCount.status == CountStatus.DRAFT,
                )
                .count(),
                "in_progress": db.query(InventoryCount)
                .filter(
                    InventoryCount.organization_id == organization_id,
                    InventoryCount.status == CountStatus.IN_PROGRESS,
                )
                .count(),
                "completed": db.query(InventoryCount)
                .filter(
                    InventoryCount.organization_id == organization_id,
                    InventoryCount.status == CountStatus.COMPLETED,
                )
                .count(),
            }
            context["count_stats"] = count_stats
        except Exception:
            context["count_stats"] = None

        try:
            from app.models.inventory.inventory_lot import InventoryLot
            from app.models.inventory.item import Item

            now = datetime.now().date()
            expiring_soon = now + timedelta(days=30)

            expiring_lots = (
                db.query(InventoryLot)
                .filter(
                    InventoryLot.organization_id == organization_id,
                    InventoryLot.expiry_date != None,
                    InventoryLot.expiry_date > now,
                    InventoryLot.expiry_date <= expiring_soon,
                    InventoryLot.quantity_available > 0,
                )
                .order_by(InventoryLot.expiry_date)
                .limit(10)
                .all()
            )

            for lot in expiring_lots:
                setattr(lot, "item", db.get(Item, lot.item_id))

            context["expiring_lots"] = expiring_lots
            context["expiring_lots_count"] = len(expiring_lots)
        except Exception:
            context["expiring_lots"] = []
            context["expiring_lots_count"] = 0

        return context


operations_dashboard_web_service = OperationsDashboardWebService()
