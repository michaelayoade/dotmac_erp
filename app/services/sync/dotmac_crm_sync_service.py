"""
DotMac CRM Sync Service - Business logic for CRM entity synchronization.

Handles:
- Syncing projects, tickets, and work orders from DotMac CRM
- Mapping CRM entities to local ERP entities
- Providing expense totals for CRM entities
- Workforce/department, company/person contacts for CRM
- Material request creation from CRM
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload, selectinload

if TYPE_CHECKING:
    from app.models.finance.ap.supplier import Supplier  # noqa: F401

from app.models.expense.expense_claim import ExpenseClaim, ExpenseClaimStatus
from app.models.finance.core_org.project import Project, ProjectStatus, ProjectType
from app.models.people.hr.employee import Employee
from app.models.person import Person
from app.models.pm.task import Task, TaskPriority, TaskStatus
from app.models.support.comment import CommentType, TicketComment
from app.models.support.ticket import Ticket, TicketPriority, TicketStatus
from app.models.sync.dotmac_crm_sync import (
    CRMEntityType,
    CRMSyncMapping,
    CRMSyncStatus,
)
from app.models.sync.sync_entity import SyncEntity, SyncStatus
from app.schemas.sync.dotmac_crm import (
    CompanyContactRead,
    CompanyListResponse,
    CRMInventoryItemPayload,
    CRMInventoryItemResponse,
    CRMMaterialRequestItemRead,
    CRMMaterialRequestPayload,
    CRMMaterialRequestResponse,
    CRMMaterialRequestStatusRead,
    CRMProjectPayload,
    CRMProjectRead,
    CRMPurchaseOrderPayload,
    CRMPurchaseOrderResponse,
    CRMTicketActivityEntry,
    CRMTicketCommentItem,
    CRMTicketPayload,
    CRMTicketRead,
    CRMWorkOrderPayload,
    CRMWorkOrderRead,
    DepartmentListResponse,
    DepartmentMemberRead,
    DepartmentRead,
    ExpenseTotals,
    InventoryItemDetail,
    InventoryItemStock,
    InventoryListResponse,
    PersonContactRead,
    PersonListResponse,
    WarehouseStock,
    WorkforceEmployeeListResponse,
    WorkforceEmployeeRead,
)

logger = logging.getLogger(__name__)


# Status mapping from CRM status strings to ERP enums
PROJECT_STATUS_MAP = {
    "planned": ProjectStatus.PLANNING,
    "active": ProjectStatus.ACTIVE,
    "on_hold": ProjectStatus.ON_HOLD,
    "completed": ProjectStatus.COMPLETED,
    "cancelled": ProjectStatus.CANCELLED,
    "canceled": ProjectStatus.CANCELLED,
}

TICKET_STATUS_MAP = {
    "open": TicketStatus.OPEN,
    "active": TicketStatus.OPEN,
    "in_progress": TicketStatus.REPLIED,
    "resolved": TicketStatus.RESOLVED,
    "closed": TicketStatus.CLOSED,
    "completed": TicketStatus.CLOSED,
    "cancelled": TicketStatus.CLOSED,
    "canceled": TicketStatus.CLOSED,
}

TASK_STATUS_MAP = {
    "draft": TaskStatus.OPEN,
    "scheduled": TaskStatus.OPEN,
    "active": TaskStatus.IN_PROGRESS,
    "in_progress": TaskStatus.IN_PROGRESS,
    "completed": TaskStatus.COMPLETED,
    "cancelled": TaskStatus.CANCELLED,
    "canceled": TaskStatus.CANCELLED,
}

CRM_SYNC_STATUS_MAP = {
    "active": CRMSyncStatus.ACTIVE,
    "planned": CRMSyncStatus.ACTIVE,
    "in_progress": CRMSyncStatus.ACTIVE,
    "open": CRMSyncStatus.ACTIVE,
    "completed": CRMSyncStatus.COMPLETED,
    "resolved": CRMSyncStatus.COMPLETED,
    "closed": CRMSyncStatus.COMPLETED,
    "cancelled": CRMSyncStatus.CANCELLED,
    "canceled": CRMSyncStatus.CANCELLED,
    "archived": CRMSyncStatus.ARCHIVED,
}


# Valid local_entity_type values for CRMSyncMapping
VALID_LOCAL_ENTITY_TYPES = frozenset({"project", "ticket", "task", "purchase_order"})


class DotMacCRMSyncService:
    """Service for syncing entities from DotMac CRM."""

    def __init__(self, db: Session):
        self.db = db

    # ============ Sync Operations ============

    def sync_project(
        self,
        org_id: UUID,
        data: CRMProjectPayload,
    ) -> CRMSyncMapping:
        """
        Sync a project from CRM to ERP.

        Creates or updates both the local Project and the CRMSyncMapping.
        """
        # Check if mapping exists
        mapping = self._get_mapping(org_id, CRMEntityType.PROJECT, data.crm_id)

        if mapping:
            # Update existing project
            project = self.db.get(Project, mapping.local_entity_id)
            if project:
                self._update_project(project, data)
            else:
                project = self._create_project(org_id, data)
                self.db.flush()  # Get project_id
                mapping.local_entity_id = project.project_id
                mapping.local_entity_type = "project"
            self._update_mapping(
                mapping,
                data.name,
                data.code,
                data.customer_name,
                data.status,
                data.metadata,
            )
        else:
            # Create new project
            project = self._create_project(org_id, data)
            self.db.flush()  # Get project_id

            mapping = CRMSyncMapping(
                organization_id=org_id,
                crm_entity_type=CRMEntityType.PROJECT,
                crm_id=data.crm_id,
                local_entity_type="project",
                local_entity_id=project.project_id,
                crm_status=CRM_SYNC_STATUS_MAP.get(
                    data.status.lower(), CRMSyncStatus.ACTIVE
                ),
                display_name=data.name,
                display_code=data.code,
                customer_name=data.customer_name,
                crm_data=data.metadata,
                synced_at=datetime.now(UTC),
            )
            self.db.add(mapping)

        logger.info("Synced CRM project %s -> %s", data.crm_id, mapping.local_entity_id)
        return mapping

    def sync_ticket(
        self,
        org_id: UUID,
        data: CRMTicketPayload,
        item_errors: list[str] | None = None,
    ) -> CRMSyncMapping:
        """
        Sync a ticket from CRM to ERP.

        Creates or updates both the local Ticket and the CRMSyncMapping.
        """
        ticket_crm_data = self._build_ticket_crm_data(data)
        mapping = self._get_mapping(org_id, CRMEntityType.TICKET, data.crm_id)

        if mapping:
            ticket = self.db.get(Ticket, mapping.local_entity_id)
            if ticket:
                self._update_ticket(ticket, data)
            else:
                ticket = self._create_ticket(org_id, data)
                self.db.flush()
                mapping.local_entity_id = ticket.ticket_id
                mapping.local_entity_type = "ticket"
            self._update_mapping(
                mapping,
                data.subject,
                data.ticket_number,
                data.customer_name,
                data.status,
                ticket_crm_data,
            )
        else:
            ticket = self._create_ticket(org_id, data)
            self.db.flush()

            mapping = CRMSyncMapping(
                organization_id=org_id,
                crm_entity_type=CRMEntityType.TICKET,
                crm_id=data.crm_id,
                local_entity_type="ticket",
                local_entity_id=ticket.ticket_id,
                crm_status=CRM_SYNC_STATUS_MAP.get(
                    data.status.lower(), CRMSyncStatus.ACTIVE
                ),
                display_name=data.subject,
                display_code=data.ticket_number,
                customer_name=data.customer_name,
                crm_data=ticket_crm_data,
                synced_at=datetime.now(UTC),
            )
            self.db.add(mapping)

        comments_processed, comment_dedupe_hits, comment_errors = (
            self._sync_ticket_comments(org_id, ticket, data.comments)
        )
        activity_processed, activity_dedupe_hits, activity_errors = (
            self._sync_ticket_activity(org_id, ticket, data.activity_log, data.comments)
        )

        if item_errors is not None:
            item_errors.extend(comment_errors)
            item_errors.extend(activity_errors)

        logger.info(
            "Synced CRM ticket %s -> %s comments_processed=%d activity_processed=%d "
            "dedupe_hits=%d",
            data.crm_id,
            mapping.local_entity_id,
            comments_processed,
            activity_processed,
            comment_dedupe_hits + activity_dedupe_hits,
        )
        return mapping

    def sync_work_order(
        self,
        org_id: UUID,
        data: CRMWorkOrderPayload,
    ) -> CRMSyncMapping:
        """
        Sync a work order from CRM to ERP as a Task.

        Creates or updates both the local Task and the CRMSyncMapping.
        """
        mapping = self._get_mapping(org_id, CRMEntityType.WORK_ORDER, data.crm_id)

        # Resolve project reference if provided
        project_id = self._resolve_project_id(org_id, data.project_crm_id)

        # Resolve ticket reference if provided
        ticket_id = self._resolve_ticket_id(org_id, data.ticket_crm_id)

        # Resolve employee by email
        employee_id = self._resolve_employee_id(org_id, data.assigned_employee_email)

        if mapping:
            task = self.db.get(Task, mapping.local_entity_id)
            if task:
                self._update_task(task, data, project_id, ticket_id, employee_id)
            else:
                if not project_id:
                    project_id = self._get_or_create_default_project(org_id)
                task = self._create_task(
                    org_id, data, project_id, ticket_id, employee_id
                )
                self.db.flush()
                mapping.local_entity_id = task.task_id
                mapping.local_entity_type = "task"
            self._update_mapping(
                mapping, data.title, None, None, data.status, data.metadata
            )
        else:
            # Work orders require a project - create a default one if needed
            if not project_id:
                project_id = self._get_or_create_default_project(org_id)

            task = self._create_task(org_id, data, project_id, ticket_id, employee_id)
            self.db.flush()

            mapping = CRMSyncMapping(
                organization_id=org_id,
                crm_entity_type=CRMEntityType.WORK_ORDER,
                crm_id=data.crm_id,
                local_entity_type="task",
                local_entity_id=task.task_id,
                crm_status=CRM_SYNC_STATUS_MAP.get(
                    data.status.lower(), CRMSyncStatus.ACTIVE
                ),
                display_name=data.title,
                crm_data=data.metadata,
                synced_at=datetime.now(UTC),
            )
            self.db.add(mapping)

        logger.info(
            "Synced CRM work order %s -> %s", data.crm_id, mapping.local_entity_id
        )
        return mapping

    # ============ List Operations (for UI dropdowns) ============

    def list_projects(
        self,
        org_id: UUID,
        search: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[CRMProjectRead]:
        """List CRM projects for expense claim dropdown."""
        stmt = (
            select(CRMSyncMapping)
            .where(CRMSyncMapping.organization_id == org_id)
            .where(CRMSyncMapping.crm_entity_type == CRMEntityType.PROJECT)
        )

        if search:
            search_filter = f"%{search}%"
            stmt = stmt.where(
                (CRMSyncMapping.display_name.ilike(search_filter))
                | (CRMSyncMapping.display_code.ilike(search_filter))
            )

        if status:
            stmt = stmt.where(
                CRMSyncMapping.crm_status
                == CRM_SYNC_STATUS_MAP.get(status.lower(), CRMSyncStatus.ACTIVE)
            )

        stmt = stmt.order_by(CRMSyncMapping.display_name).limit(limit)
        mappings = list(self.db.scalars(stmt).all())

        return [
            CRMProjectRead(
                mapping_id=m.mapping_id,
                crm_id=m.crm_id,
                local_entity_id=m.local_entity_id,
                name=m.display_name,
                code=m.display_code,
                status=m.crm_status.value,
                customer_name=m.customer_name,
            )
            for m in mappings
        ]

    def list_tickets(
        self,
        org_id: UUID,
        search: str | None = None,
        limit: int = 50,
    ) -> list[CRMTicketRead]:
        """List CRM tickets for expense claim dropdown."""
        stmt = (
            select(CRMSyncMapping)
            .where(CRMSyncMapping.organization_id == org_id)
            .where(CRMSyncMapping.crm_entity_type == CRMEntityType.TICKET)
        )

        if search:
            search_filter = f"%{search}%"
            stmt = stmt.where(
                (CRMSyncMapping.display_name.ilike(search_filter))
                | (CRMSyncMapping.display_code.ilike(search_filter))
            )

        stmt = stmt.order_by(CRMSyncMapping.created_at.desc()).limit(limit)
        mappings = list(self.db.scalars(stmt).all())

        return [
            CRMTicketRead(
                mapping_id=m.mapping_id,
                crm_id=m.crm_id,
                local_entity_id=m.local_entity_id,
                subject=m.display_name,
                ticket_number=m.display_code,
                status=m.crm_status.value,
                customer_name=m.customer_name,
            )
            for m in mappings
        ]

    def list_work_orders(
        self,
        org_id: UUID,
        search: str | None = None,
        employee_id: UUID | None = None,
        limit: int = 50,
    ) -> list[CRMWorkOrderRead]:
        """List CRM work orders for expense claim dropdown."""
        stmt = (
            select(CRMSyncMapping)
            .where(CRMSyncMapping.organization_id == org_id)
            .where(CRMSyncMapping.crm_entity_type == CRMEntityType.WORK_ORDER)
        )

        if search:
            stmt = stmt.where(CRMSyncMapping.display_name.ilike(f"%{search}%"))

        # If employee_id filter, join to Task and filter by assigned_to
        if employee_id:
            stmt = stmt.join(
                Task,
                (CRMSyncMapping.local_entity_id == Task.task_id)
                & (CRMSyncMapping.local_entity_type == "task"),
            ).where(Task.assigned_to_id == employee_id)

        stmt = stmt.order_by(CRMSyncMapping.created_at.desc()).limit(limit)
        mappings = list(self.db.scalars(stmt).all())

        return [
            CRMWorkOrderRead(
                mapping_id=m.mapping_id,
                crm_id=m.crm_id,
                local_entity_id=m.local_entity_id,
                title=m.display_name,
                status=m.crm_status.value,
                project_name=None,  # Could be enriched if needed
                ticket_subject=None,
            )
            for m in mappings
        ]

    # ============ Inventory Sync (ERP → CRM) ============

    def upsert_inventory_item(
        self,
        org_id: UUID,
        data: CRMInventoryItemPayload,
    ) -> CRMInventoryItemResponse:
        """Create or update an ERP inventory item from CRM payload."""
        from app.models.inventory.item import CostingMethod, Item, ItemType
        from app.models.inventory.item_category import ItemCategory

        item_code = (data.item_code or "").strip()
        item_name = (data.item_name or "").strip()
        if not item_code:
            raise ValueError("item_code is required")
        if not item_name:
            raise ValueError("item_name is required")

        category: ItemCategory | None
        if data.category_code:
            category = self.db.scalar(
                select(ItemCategory).where(
                    ItemCategory.organization_id == org_id,
                    ItemCategory.category_code == data.category_code,
                    ItemCategory.is_active.is_(True),
                )
            )
            if not category:
                raise ValueError(f"Item category not found: {data.category_code}")
        else:
            category = self.db.scalar(
                select(ItemCategory)
                .where(
                    ItemCategory.organization_id == org_id,
                    ItemCategory.is_active.is_(True),
                )
                .order_by(ItemCategory.category_code.asc())
            )
            if not category:
                raise ValueError(
                    "No active item category available in ERP for this organization"
                )
        if category is None:
            raise ValueError("No item category available for CRM item sync")

        item = self.db.scalar(
            select(Item).where(
                Item.organization_id == org_id,
                Item.item_code == item_code,
            )
        )

        if item:
            item.item_name = item_name
            item.description = data.description
            if data.category_code:
                item.category_id = category.category_id
            item.base_uom = (data.base_uom or "EA").strip().upper()
            item.purchase_uom = item.base_uom
            item.sales_uom = item.base_uom
            item.currency_code = (data.currency_code or "NGN").strip().upper()
            item.list_price = data.list_price
            item.reorder_point = data.reorder_point
            item.barcode = data.barcode
            item.is_active = data.is_active
            # Reuse sync-tracking fields for CRM source correlation.
            item.erpnext_id = data.crm_id
            item.last_synced_at = datetime.now(UTC)
            status = "updated"
        else:
            item = Item(
                organization_id=org_id,
                item_code=item_code,
                item_name=item_name,
                description=data.description,
                item_type=ItemType.INVENTORY,
                category_id=category.category_id,
                base_uom=(data.base_uom or "EA").strip().upper(),
                purchase_uom=(data.base_uom or "EA").strip().upper(),
                sales_uom=(data.base_uom or "EA").strip().upper(),
                costing_method=CostingMethod.WEIGHTED_AVERAGE,
                currency_code=(data.currency_code or "NGN").strip().upper(),
                list_price=data.list_price,
                reorder_point=data.reorder_point,
                barcode=data.barcode,
                track_inventory=True,
                is_active=data.is_active,
                is_purchaseable=True,
                is_saleable=True,
                erpnext_id=data.crm_id,
                last_synced_at=datetime.now(UTC),
            )
            self.db.add(item)
            status = "created"

        self.db.flush()
        logger.info(
            "Upserted CRM inventory item crm_id=%s item_code=%s status=%s",
            data.crm_id,
            item.item_code,
            status,
        )
        return CRMInventoryItemResponse(
            item_id=item.item_id,
            item_code=item.item_code,
            status=status,
            crm_id=data.crm_id,
        )

    def list_inventory_items(
        self,
        org_id: UUID,
        search: str | None = None,
        category_code: str | None = None,
        warehouse_id: UUID | None = None,
        include_zero_stock: bool = False,
        only_below_reorder: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> InventoryListResponse:
        """
        List inventory items with current stock levels for CRM.

        Uses batch stock loading (2 queries) instead of per-item queries.

        Args:
            org_id: Organization ID
            search: Search term for item code/name
            category_code: Filter by category code
            warehouse_id: Filter by specific warehouse
            include_zero_stock: Include items with zero stock (default: False)
            only_below_reorder: Only show items below reorder point
            limit: Max items to return
            offset: Pagination offset

        Returns:
            InventoryListResponse with items and pagination info
        """
        from app.models.inventory.item import Item
        from app.models.inventory.item_category import ItemCategory
        from app.services.inventory.balance import InventoryBalanceService

        # Build base query for active inventory items
        stmt = (
            select(Item, ItemCategory)
            .outerjoin(ItemCategory, Item.category_id == ItemCategory.category_id)
            .where(
                Item.organization_id == org_id,
                Item.is_active.is_(True),
            )
        )

        if search:
            search_filter = f"%{search}%"
            stmt = stmt.where(
                (Item.item_code.ilike(search_filter))
                | (Item.item_name.ilike(search_filter))
                | (Item.barcode.ilike(search_filter))
            )

        if category_code:
            stmt = stmt.where(ItemCategory.category_code == category_code)

        # Fast path: no stock-level filtering needed
        if include_zero_stock and not only_below_reorder:
            count_stmt = select(func.count()).select_from(
                stmt.with_only_columns(Item.item_id).subquery()
            )
            total_count = self.db.scalar(count_stmt) or 0

            stmt = stmt.order_by(Item.item_code).offset(offset).limit(limit + 1)
            results = self.db.execute(stmt).all()

            has_more = len(results) > limit
            if has_more:
                results = results[:limit]

            # Batch-load stock levels (2 queries instead of 2*N)
            item_ids = [item.item_id for item, _cat in results]
            stock_map = InventoryBalanceService.get_batch_stock_levels(
                self.db, org_id, item_ids, warehouse_id
            )

            items = self._build_stock_items(list(results), stock_map)
            return InventoryListResponse(
                items=items, total_count=total_count, has_more=has_more
            )

        # Filtered path: need stock levels to filter, process in batches
        all_qualified: list[InventoryItemStock] = []
        batch_size = 500
        current_offset = 0

        while True:
            page_stmt = (
                stmt.order_by(Item.item_code).offset(current_offset).limit(batch_size)
            )
            results = self.db.execute(page_stmt).all()
            if not results:
                break

            # Batch-load stock for this page (2 queries per batch)
            item_ids = [item.item_id for item, _cat in results]
            stock_map = InventoryBalanceService.get_batch_stock_levels(
                self.db, org_id, item_ids, warehouse_id
            )

            for item, category in results:
                on_hand, reserved = stock_map.get(
                    item.item_id, (Decimal("0"), Decimal("0"))
                )
                available = on_hand - reserved

                if not include_zero_stock and available <= 0:
                    continue

                reorder_point = item.reorder_point or Decimal("0")
                is_below = available <= reorder_point if reorder_point else False
                if only_below_reorder and not is_below:
                    continue

                all_qualified.append(
                    InventoryItemStock(
                        item_id=item.item_id,
                        item_code=item.item_code,
                        item_name=item.item_name,
                        description=item.description,
                        category_code=category.category_code if category else None,
                        category_name=category.category_name if category else None,
                        base_uom=item.base_uom,
                        quantity_on_hand=on_hand,
                        quantity_reserved=reserved,
                        quantity_available=available,
                        reorder_point=item.reorder_point,
                        list_price=item.list_price,
                        currency_code=item.currency_code,
                        barcode=item.barcode,
                        is_below_reorder=is_below,
                    )
                )

            current_offset += batch_size

        total_count = len(all_qualified)
        page_items = all_qualified[offset : offset + limit]
        has_more = (offset + limit) < total_count

        return InventoryListResponse(
            items=page_items, total_count=total_count, has_more=has_more
        )

    def _build_stock_items(
        self,
        results: list,
        stock_map: dict[UUID, tuple[Decimal, Decimal]],
    ) -> list[InventoryItemStock]:
        """Build InventoryItemStock list from query results and batch stock data."""
        items: list[InventoryItemStock] = []
        for item, category in results:
            on_hand, reserved = stock_map.get(
                item.item_id, (Decimal("0"), Decimal("0"))
            )
            available = on_hand - reserved
            reorder_point = item.reorder_point or Decimal("0")

            items.append(
                InventoryItemStock(
                    item_id=item.item_id,
                    item_code=item.item_code,
                    item_name=item.item_name,
                    description=item.description,
                    category_code=category.category_code if category else None,
                    category_name=category.category_name if category else None,
                    base_uom=item.base_uom,
                    quantity_on_hand=on_hand,
                    quantity_reserved=reserved,
                    quantity_available=available,
                    reorder_point=item.reorder_point,
                    list_price=item.list_price,
                    currency_code=item.currency_code,
                    barcode=item.barcode,
                    is_below_reorder=(
                        available <= reorder_point if reorder_point else False
                    ),
                )
            )
        return items

    def get_inventory_item_detail(
        self,
        org_id: UUID,
        item_id: UUID,
    ) -> InventoryItemDetail | None:
        """
        Get detailed inventory item info with warehouse breakdown.

        Args:
            org_id: Organization ID
            item_id: Item ID to retrieve

        Returns:
            InventoryItemDetail with warehouse-level stock, or None if not found
        """
        from app.models.inventory.item import Item
        from app.models.inventory.item_category import ItemCategory
        from app.services.inventory.balance import InventoryBalanceService

        # Get item
        item = self.db.get(Item, item_id)
        if not item or item.organization_id != org_id:
            return None

        # Get category
        category = (
            self.db.get(ItemCategory, item.category_id) if item.category_id else None
        )

        # Get stock summary with warehouse breakdown
        summary = InventoryBalanceService.get_item_stock_summary(
            self.db, org_id, item_id
        )

        warehouse_stocks: list[WarehouseStock] = []
        if summary:
            for wh_balance in summary.warehouses:
                if wh_balance.warehouse_id:
                    warehouse_stocks.append(
                        WarehouseStock(
                            warehouse_id=wh_balance.warehouse_id,
                            warehouse_code=wh_balance.warehouse_code or "",
                            warehouse_name=(
                                getattr(wh_balance, "warehouse_name", None)
                                or wh_balance.warehouse_code
                                or ""
                            ),
                            quantity_on_hand=wh_balance.quantity_on_hand,
                            quantity_reserved=wh_balance.quantity_reserved,
                            quantity_available=wh_balance.quantity_available,
                        )
                    )

        total_on_hand = summary.total_on_hand if summary else Decimal("0")
        total_reserved = summary.total_reserved if summary else Decimal("0")
        total_available = summary.total_available if summary else Decimal("0")

        return InventoryItemDetail(
            item_id=item.item_id,
            item_code=item.item_code,
            item_name=item.item_name,
            description=item.description,
            category_code=category.category_code if category else None,
            category_name=category.category_name if category else None,
            base_uom=item.base_uom,
            total_on_hand=total_on_hand,
            total_reserved=total_reserved,
            total_available=total_available,
            reorder_point=item.reorder_point,
            list_price=item.list_price,
            currency_code=item.currency_code,
            barcode=item.barcode,
            warehouses=warehouse_stocks,
        )

    def get_categories(self, org_id: UUID) -> list[dict]:
        """
        Get list of item categories for filtering.

        Returns:
            List of {code, name} dicts
        """
        from app.models.inventory.item_category import ItemCategory

        stmt = (
            select(ItemCategory.category_code, ItemCategory.category_name)
            .where(
                ItemCategory.organization_id == org_id,
                ItemCategory.is_active.is_(True),
            )
            .order_by(ItemCategory.category_name)
        )
        results = self.db.execute(stmt).all()
        return [{"code": code, "name": name} for code, name in results]

    def get_warehouses(self, org_id: UUID) -> list[dict]:
        """
        Get list of warehouses for filtering.

        Returns:
            List of {warehouse_id, code, name} dicts
        """
        from app.models.inventory.warehouse import Warehouse

        stmt = (
            select(
                Warehouse.warehouse_id,
                Warehouse.warehouse_code,
                Warehouse.warehouse_name,
            )
            .where(
                Warehouse.organization_id == org_id,
                Warehouse.is_active.is_(True),
            )
            .order_by(Warehouse.warehouse_name)
        )
        results = self.db.execute(stmt).all()
        return [
            {"warehouse_id": str(wh_id), "code": code, "name": name}
            for wh_id, code, name in results
        ]

    # ============ Expense Totals ============

    def get_expense_totals_for_project(
        self,
        org_id: UUID,
        crm_id: str,
    ) -> ExpenseTotals | None:
        """Get expense totals for a CRM project."""
        mapping = self._get_mapping(org_id, CRMEntityType.PROJECT, crm_id)
        if not mapping:
            return None

        return self._calculate_expense_totals(
            org_id,
            project_id=mapping.local_entity_id,
        )

    def get_expense_totals_for_ticket(
        self,
        org_id: UUID,
        crm_id: str,
    ) -> ExpenseTotals | None:
        """Get expense totals for a CRM ticket."""
        mapping = self._get_mapping(org_id, CRMEntityType.TICKET, crm_id)
        if not mapping:
            return None

        return self._calculate_expense_totals(
            org_id,
            ticket_id=mapping.local_entity_id,
        )

    def get_expense_totals_for_work_order(
        self,
        org_id: UUID,
        crm_id: str,
    ) -> ExpenseTotals | None:
        """Get expense totals for a CRM work order."""
        mapping = self._get_mapping(org_id, CRMEntityType.WORK_ORDER, crm_id)
        if not mapping:
            return None

        return self._calculate_expense_totals(
            org_id,
            task_id=mapping.local_entity_id,
        )

    def get_batch_expense_totals(
        self,
        org_id: UUID,
        project_crm_ids: list[str],
        ticket_crm_ids: list[str],
        work_order_crm_ids: list[str],
    ) -> dict[str, ExpenseTotals]:
        """
        Get expense totals for multiple CRM entities in batched queries.

        Instead of 2 queries per CRM ID (mapping lookup + aggregation),
        resolves all mappings in up to 3 queries then aggregates in up to 3.
        """
        result: dict[str, ExpenseTotals] = {}

        # Batch-resolve mappings (up to 3 queries)
        project_map = self._batch_get_mappings(
            org_id, CRMEntityType.PROJECT, project_crm_ids
        )
        ticket_map = self._batch_get_mappings(
            org_id, CRMEntityType.TICKET, ticket_crm_ids
        )
        wo_map = self._batch_get_mappings(
            org_id, CRMEntityType.WORK_ORDER, work_order_crm_ids
        )

        # Batch-aggregate expenses (up to 3 queries)
        for crm_to_local, fk_col in [
            (project_map, ExpenseClaim.project_id),
            (ticket_map, ExpenseClaim.ticket_id),
            (wo_map, ExpenseClaim.task_id),
        ]:
            if not crm_to_local:
                continue
            local_to_crm = {v: k for k, v in crm_to_local.items()}
            local_ids = list(crm_to_local.values())

            stmt = (
                select(
                    fk_col,
                    ExpenseClaim.status,
                    func.coalesce(func.sum(ExpenseClaim.total_claimed_amount), 0).label(
                        "total"
                    ),
                )
                .where(
                    ExpenseClaim.organization_id == org_id,
                    fk_col.in_(local_ids),
                )
                .group_by(fk_col, ExpenseClaim.status)
            )
            rows = self.db.execute(stmt).all()

            # Group by local_id
            grouped: dict[UUID, ExpenseTotals] = {}
            for local_id, status, total in rows:
                if local_id not in grouped:
                    grouped[local_id] = ExpenseTotals()
                amount = Decimal(str(total)) if total else Decimal("0.00")
                totals = grouped[local_id]
                if status == ExpenseClaimStatus.DRAFT:
                    totals.draft = amount
                elif status == ExpenseClaimStatus.SUBMITTED:
                    totals.submitted = amount
                elif status in (
                    ExpenseClaimStatus.APPROVED,
                    ExpenseClaimStatus.PENDING_APPROVAL,
                ):
                    totals.approved += amount
                elif status == ExpenseClaimStatus.PAID:
                    totals.paid = amount

            for local_id, totals in grouped.items():
                crm_id = local_to_crm.get(local_id)
                if crm_id:
                    result[crm_id] = totals

        return result

    # ============ Workforce / Department Endpoints ============

    def list_workforce_employees(
        self,
        org_id: UUID,
        *,
        include_inactive: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> WorkforceEmployeeListResponse:
        """List employees for CRM author/staff lookup."""
        from app.models.people.hr.employee import Employee, EmployeeStatus

        stmt = (
            select(Employee)
            .options(
                joinedload(Employee.person),
                joinedload(Employee.department),
                joinedload(Employee.designation),
            )
            .where(Employee.organization_id == org_id)
        )
        if not include_inactive:
            stmt = stmt.where(Employee.status == EmployeeStatus.ACTIVE)

        # Exclude rows without a person/email because CRM needs email key for mapping.
        employees_all = list(
            self.db.scalars(stmt.order_by(Employee.employee_code)).all()
        )
        employees_filtered = [
            emp
            for emp in employees_all
            if emp.person and (emp.person.email or "").strip()
        ]

        total = len(employees_filtered)
        page = employees_filtered[offset : offset + limit]
        has_more = offset + limit < total

        rows = [
            WorkforceEmployeeRead(
                employee_id=emp.employee_id,
                email=emp.person.email.strip().lower()
                if emp.person and emp.person.email
                else "",
                is_active=emp.status == EmployeeStatus.ACTIVE,
                full_name=(
                    f"{emp.person.first_name or ''} {emp.person.last_name or ''}".strip()
                    if emp.person
                    else None
                ),
                department=emp.department.department_name if emp.department else None,
                designation=emp.designation.designation_name
                if emp.designation
                else None,
            )
            for emp in page
        ]

        return WorkforceEmployeeListResponse(
            employees=rows,
            total=total,
            limit=limit,
            offset=offset,
            has_more=has_more,
        )

    def list_departments(
        self,
        org_id: UUID,
        *,
        include_inactive: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> DepartmentListResponse:
        """List departments with members for CRM service-team mapping."""
        from app.models.people.hr.department import Department
        from app.models.people.hr.employee import Employee, EmployeeStatus

        stmt = select(Department).where(Department.organization_id == org_id)
        if not include_inactive:
            stmt = stmt.where(Department.is_active.is_(True))

        # Count total before pagination
        count_stmt = select(func.count()).select_from(
            stmt.with_only_columns(Department.department_id).subquery()
        )
        total = self.db.scalar(count_stmt) or 0

        stmt = (
            stmt.options(
                selectinload(Department.head).joinedload(Employee.person),
                selectinload(Department.head).joinedload(Employee.designation),
                selectinload(Department.employees).joinedload(Employee.person),
                selectinload(Department.employees).joinedload(Employee.designation),
            )
            .order_by(Department.department_name)
            .offset(offset)
            .limit(limit)
        )
        departments = list(self.db.scalars(stmt).unique().all())

        result: list[DepartmentRead] = []
        for dept in departments:
            # Build manager from head relationship
            manager = None
            if dept.head and dept.head.person:
                p = dept.head.person
                head_designation = dept.head.designation
                manager = DepartmentMemberRead(
                    employee_id=dept.head.employee_id,
                    email=p.email,
                    full_name=f"{p.first_name} {p.last_name}".strip(),
                    designation_name=head_designation.designation_name
                    if head_designation
                    else None,
                    designation_id=head_designation.designation_id
                    if head_designation
                    else None,
                    role="manager",
                    is_active=dept.head.status == EmployeeStatus.ACTIVE,
                )

            # Build members from employees relationship
            members: list[DepartmentMemberRead] = []
            for emp in dept.employees:
                if not include_inactive and emp.status != EmployeeStatus.ACTIVE:
                    continue
                if emp.person:
                    ep = emp.person
                    emp_designation = emp.designation
                    members.append(
                        DepartmentMemberRead(
                            employee_id=emp.employee_id,
                            email=ep.email,
                            full_name=f"{ep.first_name} {ep.last_name}".strip(),
                            designation_name=emp_designation.designation_name
                            if emp_designation
                            else None,
                            designation_id=emp_designation.designation_id
                            if emp_designation
                            else None,
                            role=None,
                            is_active=emp.status == EmployeeStatus.ACTIVE,
                        )
                    )

            result.append(
                DepartmentRead(
                    department_id=dept.department_code,
                    department_name=dept.department_name,
                    department_type="operations",
                    manager=manager,
                    members=members,
                )
            )

        return DepartmentListResponse(
            departments=result,
            total=total,
            limit=limit,
            offset=offset,
        )

    # ============ Contact Sync (ERP → CRM) ============

    def list_companies(
        self,
        org_id: UUID,
        *,
        updated_since: datetime | None = None,
        include_inactive: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> CompanyListResponse:
        """List company/government customers for CRM contacts sync."""
        from app.models.finance.ar.customer import Customer, CustomerType

        stmt = select(Customer).where(
            Customer.organization_id == org_id,
            Customer.customer_type.in_([CustomerType.COMPANY, CustomerType.GOVERNMENT]),
        )
        if not include_inactive:
            stmt = stmt.where(Customer.is_active.is_(True))
        if updated_since:
            stmt = stmt.where(
                func.coalesce(Customer.updated_at, Customer.created_at) >= updated_since
            )

        count_stmt = select(func.count()).select_from(
            stmt.with_only_columns(Customer.customer_id).subquery()
        )
        total = self.db.scalar(count_stmt) or 0

        stmt = stmt.order_by(Customer.legal_name).offset(offset).limit(limit + 1)
        customers = list(self.db.scalars(stmt).all())
        has_more = len(customers) > limit
        if has_more:
            customers = customers[:limit]

        companies = [
            CompanyContactRead(
                customer_id=c.customer_id,
                customer_code=c.customer_code,
                legal_name=c.legal_name,
                tax_id=c.tax_identification_number,
                billing_address=c.billing_address,
                primary_contact=c.primary_contact,
                crm_id=c.crm_id,
            )
            for c in customers
        ]

        return CompanyListResponse(
            companies=companies,
            total=total,
            limit=limit,
            offset=offset,
            has_more=has_more,
        )

    def list_people_contacts(
        self,
        org_id: UUID,
        *,
        updated_since: datetime | None = None,
        include_inactive: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> PersonListResponse:
        """List individual customers as person contacts for CRM sync."""
        from app.models.finance.ar.customer import Customer, CustomerType

        stmt = select(Customer).where(
            Customer.organization_id == org_id,
            Customer.customer_type == CustomerType.INDIVIDUAL,
        )
        if not include_inactive:
            stmt = stmt.where(Customer.is_active.is_(True))
        if updated_since:
            stmt = stmt.where(
                func.coalesce(Customer.updated_at, Customer.created_at) >= updated_since
            )

        count_stmt = select(func.count()).select_from(
            stmt.with_only_columns(Customer.customer_id).subquery()
        )
        total = self.db.scalar(count_stmt) or 0

        stmt = stmt.order_by(Customer.legal_name).offset(offset).limit(limit + 1)
        customers = list(self.db.scalars(stmt).all())
        has_more = len(customers) > limit
        if has_more:
            customers = customers[:limit]

        contacts: list[PersonContactRead] = []
        for c in customers:
            # Extract email/phone from primary_contact JSONB
            email = None
            phone = None
            if c.primary_contact and isinstance(c.primary_contact, dict):
                email = c.primary_contact.get("email")
                phone = c.primary_contact.get("phone")

            contacts.append(
                PersonContactRead(
                    contact_id=c.customer_id,
                    customer_code=c.customer_code,
                    legal_name=c.legal_name,
                    email=email,
                    phone=phone,
                    crm_id=c.crm_id,
                )
            )

        return PersonListResponse(
            contacts=contacts,
            total=total,
            limit=limit,
            offset=offset,
            has_more=has_more,
        )

    # ============ Material Request (CRM → ERP) ============

    def create_material_request(
        self,
        org_id: UUID,
        data: CRMMaterialRequestPayload,
    ) -> CRMMaterialRequestResponse:
        """
        Create a material request from CRM.

        Idempotent: if a request with the same omni_id already exists, return it.

        Raises:
            ValueError: If an item_code is not found or request_type is invalid.
        """
        from app.models.finance.core_config.numbering_sequence import SequenceType
        from app.models.inventory.item import Item
        from app.models.inventory.material_request import (
            MaterialRequest,
            MaterialRequestItem,
            MaterialRequestStatus,
            MaterialRequestType,
        )
        from app.services.finance.common.numbering import SyncNumberingService

        # Idempotency check — return existing if already created
        existing_stmt = select(MaterialRequest).where(
            MaterialRequest.organization_id == org_id,
            MaterialRequest.crm_id == data.omni_id,
        )
        existing = self.db.scalar(existing_stmt)
        if existing:
            logger.info(
                "Material request already exists for omni_id=%s, returning existing",
                data.omni_id,
            )
            return CRMMaterialRequestResponse(
                request_id=existing.request_id,
                request_number=existing.request_number,
                status=existing.status.value,
                omni_id=data.omni_id,
            )

        # Resolve cross-references
        project_id = self._resolve_project_id(org_id, data.project_crm_id)
        ticket_id = self._resolve_ticket_id(org_id, data.ticket_crm_id)
        employee_id = self._resolve_employee_id(org_id, data.requested_by_email)

        # Map request type
        request_type_map = {
            "PURCHASE": MaterialRequestType.PURCHASE,
            "TRANSFER": MaterialRequestType.TRANSFER,
            "ISSUE": MaterialRequestType.ISSUE,
            "MANUFACTURE": MaterialRequestType.MANUFACTURE,
        }
        request_type = request_type_map.get(data.request_type.upper())
        if not request_type:
            raise ValueError(
                f"Invalid request_type: {data.request_type}. "
                f"Must be one of: {', '.join(request_type_map)}"
            )

        # Parse schedule date
        schedule_date_val: date | None = None
        if data.schedule_date:
            try:
                schedule_date_val = date.fromisoformat(data.schedule_date)
            except ValueError as exc:
                raise ValueError(
                    f"Invalid schedule_date format: {data.schedule_date}. Use YYYY-MM-DD."
                ) from exc

        # Resolve items and validate
        resolved_items: list[tuple] = []
        for item_payload in data.items:
            item_stmt = select(Item).where(
                Item.organization_id == org_id,
                Item.item_code == item_payload.item_code,
            )
            item = self.db.scalar(item_stmt)
            if not item:
                raise ValueError(f"Item not found: {item_payload.item_code}")
            resolved_items.append((item.item_id, item_payload))

        # Generate request number
        numbering = SyncNumberingService(self.db)
        request_number = numbering.generate_next_number(
            org_id, SequenceType.MATERIAL_REQUEST
        )

        # Create header
        mr = MaterialRequest(
            organization_id=org_id,
            request_number=request_number,
            request_type=request_type,
            status=MaterialRequestStatus.SUBMITTED,
            schedule_date=schedule_date_val,
            requested_by_id=employee_id,
            project_id=project_id,
            ticket_id=ticket_id,
            remarks=data.remarks,
            crm_id=data.omni_id,
        )
        self.db.add(mr)
        self.db.flush()  # get request_id

        # Create line items
        for seq, (inv_item_id, item_payload) in enumerate(resolved_items, start=1):
            line = MaterialRequestItem(
                organization_id=org_id,
                request_id=mr.request_id,
                inventory_item_id=inv_item_id,
                requested_qty=item_payload.quantity,
                uom=item_payload.uom,
                sequence=seq,
                project_id=project_id,
                ticket_id=ticket_id,
            )
            self.db.add(line)

        self.db.flush()

        logger.info(
            "Created material request %s (crm_id=%s) with %d items",
            request_number,
            data.omni_id,
            len(resolved_items),
        )

        return CRMMaterialRequestResponse(
            request_id=mr.request_id,
            request_number=mr.request_number,
            status=mr.status.value,
            omni_id=data.omni_id,
        )

    def get_material_request_by_crm_id(
        self,
        org_id: UUID,
        omni_id: str,
    ) -> CRMMaterialRequestStatusRead | None:
        """Get material request status by CRM omni_id."""
        from app.models.inventory.material_request import MaterialRequest

        stmt = (
            select(MaterialRequest)
            .options(joinedload(MaterialRequest.items))
            .where(
                MaterialRequest.organization_id == org_id,
                MaterialRequest.crm_id == omni_id,
            )
        )
        mr = self.db.scalar(stmt)
        if not mr:
            return None

        # Resolve item names for the response
        from app.models.inventory.item import Item

        item_ids = [line.inventory_item_id for line in mr.items]
        items_map: dict[UUID, str] = {}
        if item_ids:
            items_stmt = select(Item.item_id, Item.item_name).where(
                Item.item_id.in_(item_ids)
            )
            items_map = {row[0]: row[1] for row in self.db.execute(items_stmt).all()}

        return CRMMaterialRequestStatusRead(
            request_id=mr.request_id,
            request_number=mr.request_number,
            status=mr.status.value,
            request_type=mr.request_type.value,
            items=[
                CRMMaterialRequestItemRead(
                    item_code=items_map.get(line.inventory_item_id, ""),
                    item_name=items_map.get(line.inventory_item_id, ""),
                    requested_qty=line.requested_qty,
                    ordered_qty=line.ordered_qty,
                    uom=line.uom,
                )
                for line in mr.items
            ],
            created_at=mr.created_at,
        )

    # ============ Purchase Order (CRM → ERP) ============

    def create_purchase_order(
        self,
        org_id: UUID,
        data: CRMPurchaseOrderPayload,
        created_by_person_id: UUID,
    ) -> CRMPurchaseOrderResponse:
        """
        Create a DRAFT purchase order from a CRM vendor quote approval.

        Idempotent: if a PO for the same omni_work_order_id already exists, return it.

        Args:
            org_id: Organization scope.
            data: CRM purchase order payload.
            created_by_person_id: Person ID of the API key user (fallback creator).

        Raises:
            ValueError: If supplier not found or other validation fails.
        """
        from app.models.finance.ap.purchase_order import PurchaseOrder
        from app.services.finance.ap.purchase_order import (
            POLineInput,
            PurchaseOrderInput,
            PurchaseOrderService,
        )

        correlation_id = f"crm-wo:{data.omni_work_order_id}"

        # 1. Idempotency: check CRMSyncMapping first
        existing_mapping = self._get_mapping(
            org_id, CRMEntityType.PURCHASE_ORDER, data.omni_work_order_id
        )
        if existing_mapping:
            po = self.db.get(PurchaseOrder, existing_mapping.local_entity_id)
            if po:
                logger.info(
                    "PO already exists for omni_work_order_id=%s (mapping), returning existing",
                    data.omni_work_order_id,
                )
                return CRMPurchaseOrderResponse(
                    purchase_order_id=po.po_number,
                    po_id=po.po_id,
                    status=po.status.value.lower(),
                    omni_work_order_id=data.omni_work_order_id,
                )

        # 2. Fallback idempotency: check by correlation_id (PO committed but mapping failed)
        fallback_stmt = select(PurchaseOrder).where(
            PurchaseOrder.organization_id == org_id,
            PurchaseOrder.correlation_id == correlation_id,
        )
        existing_po = self.db.scalar(fallback_stmt)
        if existing_po:
            logger.info(
                "PO already exists for omni_work_order_id=%s (correlation_id), "
                "re-creating mapping",
                data.omni_work_order_id,
            )
            # Re-create the missing mapping and persist it
            self._create_po_sync_mapping(
                org_id, data, existing_po.po_id, existing_po.po_number
            )
            self.db.commit()
            return CRMPurchaseOrderResponse(
                purchase_order_id=existing_po.po_number,
                po_id=existing_po.po_id,
                status=existing_po.status.value.lower(),
                omni_work_order_id=data.omni_work_order_id,
            )

        # 3. Resolve supplier
        supplier = self._resolve_supplier(org_id, data.vendor_erp_id, data.vendor_code)

        # 4. Resolve project (optional)
        project_id = self._resolve_project_id(org_id, data.omni_project_id)

        # 5. Resolve approver → person_id for created_by
        approver_person_id = self._resolve_person_id_by_email(
            org_id, data.approved_by_email
        )
        creator_id = approver_person_id or created_by_person_id

        # 6. Build PO input — distribute tax_total proportionally across lines
        po_lines: list[POLineInput] = []
        line_subtotal = sum(i.amount for i in data.items) or Decimal("1")
        tax_distributed = Decimal("0")
        for idx, item in enumerate(data.items):
            if idx == len(data.items) - 1:
                # Last line gets remainder to avoid rounding drift
                line_tax = data.tax_total - tax_distributed
            else:
                line_tax = (data.tax_total * item.amount / line_subtotal).quantize(
                    Decimal("0.01")
                )
                tax_distributed += line_tax
            po_lines.append(
                POLineInput(
                    description=item.description,
                    quantity_ordered=item.quantity,
                    unit_price=item.unit_price,
                    tax_amount=line_tax,
                    project_id=project_id,
                )
            )

        po_input = PurchaseOrderInput(
            supplier_id=supplier.supplier_id,
            po_date=date.today(),
            currency_code=data.currency,
            lines=po_lines,
            correlation_id=correlation_id,
            terms_and_conditions=data.title,
        )

        # 7. Create PO (commits internally)
        po = PurchaseOrderService.create_po(self.db, org_id, po_input, creator_id)

        # 8. Create CRMSyncMapping (tracks the PO for idempotency)
        self._create_po_sync_mapping(org_id, data, po.po_id, po.po_number)
        self.db.commit()

        logger.info(
            "Created PO %s for omni_work_order_id=%s, supplier=%s",
            po.po_number,
            data.omni_work_order_id,
            supplier.supplier_code,
        )

        return CRMPurchaseOrderResponse(
            purchase_order_id=po.po_number,
            po_id=po.po_id,
            status=po.status.value.lower(),
            omni_work_order_id=data.omni_work_order_id,
        )

    def _resolve_supplier(
        self,
        org_id: UUID,
        vendor_erp_id: str | None,
        vendor_code: str | None,
    ) -> Supplier:
        """Resolve a supplier by erpnext_id or supplier_code.

        Raises:
            ValueError: If supplier not found.
        """
        from app.models.finance.ap.supplier import Supplier

        if vendor_erp_id:
            stmt = select(Supplier).where(
                Supplier.organization_id == org_id,
                Supplier.erpnext_id == vendor_erp_id,
                Supplier.is_active.is_(True),
            )
            supplier = self.db.scalar(stmt)
            if supplier:
                return supplier

        if vendor_code:
            stmt = select(Supplier).where(
                Supplier.organization_id == org_id,
                Supplier.supplier_code == vendor_code,
                Supplier.is_active.is_(True),
            )
            supplier = self.db.scalar(stmt)
            if supplier:
                return supplier

        raise ValueError(
            f"Supplier not found: erp_id={vendor_erp_id}, code={vendor_code}"
        )

    def _resolve_person_id_by_email(
        self, org_id: UUID, email: str | None
    ) -> UUID | None:
        """Resolve an email to a Person ID (via Employee → Person)."""
        if not email:
            return None

        email_lower = email.lower()
        stmt = (
            select(Person.id)
            .join(Employee, Employee.person_id == Person.id)
            .where(
                Employee.organization_id == org_id,
                func.lower(Person.email) == email_lower,
            )
        )
        result = self.db.scalar(stmt)
        if result:
            return result

        # Fallback to personal_email on Employee
        stmt = select(Employee.person_id).where(
            Employee.organization_id == org_id,
            func.lower(Employee.personal_email) == email_lower,
        )
        return self.db.scalar(stmt)

    def _create_po_sync_mapping(
        self,
        org_id: UUID,
        data: CRMPurchaseOrderPayload,
        po_id: UUID,
        po_number: str,
    ) -> None:
        """Create a CRMSyncMapping for a purchase order."""
        mapping = CRMSyncMapping(
            organization_id=org_id,
            crm_entity_type=CRMEntityType.PURCHASE_ORDER,
            crm_id=data.omni_work_order_id,
            local_entity_type="purchase_order",
            local_entity_id=po_id,
            crm_status=CRMSyncStatus.ACTIVE,
            display_name=data.title[:255],
            display_code=po_number,
            customer_name=data.vendor_name,
            crm_data={
                "omni_work_order_id": data.omni_work_order_id,
                "omni_quote_id": data.omni_quote_id,
                "omni_project_id": data.omni_project_id,
                "project_code": data.project_code,
                "vendor_erp_id": data.vendor_erp_id,
                "vendor_code": data.vendor_code,
                "total": str(data.total),
            },
        )
        self.db.add(mapping)
        self.db.flush()

    # ============ Lookup Helpers ============

    def get_local_project_id(self, org_id: UUID, crm_id: str) -> UUID | None:
        """Get local project ID for a CRM project."""
        mapping = self._get_mapping(org_id, CRMEntityType.PROJECT, crm_id)
        return mapping.local_entity_id if mapping else None

    def get_local_ticket_id(self, org_id: UUID, crm_id: str) -> UUID | None:
        """Get local ticket ID for a CRM ticket."""
        mapping = self._get_mapping(org_id, CRMEntityType.TICKET, crm_id)
        return mapping.local_entity_id if mapping else None

    def get_local_task_id(self, org_id: UUID, crm_id: str) -> UUID | None:
        """Get local task ID for a CRM work order."""
        mapping = self._get_mapping(org_id, CRMEntityType.WORK_ORDER, crm_id)
        return mapping.local_entity_id if mapping else None

    # ============ Private Helpers ============

    def _get_mapping(
        self,
        org_id: UUID,
        entity_type: CRMEntityType,
        crm_id: str,
    ) -> CRMSyncMapping | None:
        """Get CRM sync mapping by org, type, and CRM ID."""
        stmt = select(CRMSyncMapping).where(
            CRMSyncMapping.organization_id == org_id,
            CRMSyncMapping.crm_entity_type == entity_type,
            CRMSyncMapping.crm_id == crm_id,
        )
        return self.db.scalar(stmt)

    def _batch_get_mappings(
        self,
        org_id: UUID,
        entity_type: CRMEntityType,
        crm_ids: list[str],
    ) -> dict[str, UUID]:
        """Get multiple CRM sync mappings in a single query.

        Returns:
            Dict mapping crm_id -> local_entity_id
        """
        if not crm_ids:
            return {}
        stmt = select(CRMSyncMapping.crm_id, CRMSyncMapping.local_entity_id).where(
            CRMSyncMapping.organization_id == org_id,
            CRMSyncMapping.crm_entity_type == entity_type,
            CRMSyncMapping.crm_id.in_(crm_ids),
        )
        rows = self.db.execute(stmt).all()
        return {crm_id: local_id for crm_id, local_id in rows}

    def _update_mapping(
        self,
        mapping: CRMSyncMapping,
        display_name: str,
        display_code: str | None,
        customer_name: str | None,
        status: str,
        crm_data: dict | None = None,
    ) -> None:
        """Update mapping fields on sync."""
        mapping.display_name = display_name
        mapping.display_code = display_code
        mapping.customer_name = customer_name
        mapping.crm_status = CRM_SYNC_STATUS_MAP.get(
            status.lower(), CRMSyncStatus.ACTIVE
        )
        mapping.synced_at = datetime.now(UTC)
        if crm_data is not None:
            mapping.crm_data = crm_data

    def _generate_unique_code(self, prefix: str, crm_id: str, max_len: int = 20) -> str:
        """Generate a unique code from CRM ID using hash to avoid collisions."""
        # Use hash of full CRM ID for uniqueness, take enough chars to fit max_len
        hash_suffix = hashlib.sha256(crm_id.encode()).hexdigest()[
            : max_len - len(prefix) - 1
        ]
        return f"{prefix}-{hash_suffix.upper()}"

    def _create_project(self, org_id: UUID, data: CRMProjectPayload) -> Project:
        """Create a local Project from CRM data."""
        # Generate unique project code from CRM ID hash
        project_code = self._generate_unique_code("CRM", data.crm_id, max_len=20)

        project = Project(
            organization_id=org_id,
            project_code=project_code,
            project_name=data.name,
            description=data.description,
            status=PROJECT_STATUS_MAP.get(data.status.lower(), ProjectStatus.ACTIVE),
            project_type=self._map_project_type(data.project_type),
            start_date=data.start_at.date() if data.start_at else None,
            end_date=data.due_at.date() if data.due_at else None,
        )
        self.db.add(project)
        return project

    def _update_project(self, project: Project, data: CRMProjectPayload) -> None:
        """Update existing project from CRM data."""
        project.project_name = data.name
        project.description = data.description
        project.status = PROJECT_STATUS_MAP.get(
            data.status.lower(), ProjectStatus.ACTIVE
        )
        project.start_date = (
            data.start_at.date() if data.start_at else project.start_date
        )
        project.end_date = data.due_at.date() if data.due_at else project.end_date

    def _create_ticket(self, org_id: UUID, data: CRMTicketPayload) -> Ticket:
        """Create a local Ticket from CRM data."""
        ticket_number = data.ticket_number or self._generate_unique_code(
            "CRM", data.crm_id, max_len=50
        )

        ticket = Ticket(
            organization_id=org_id,
            ticket_number=ticket_number,
            subject=data.subject,
            description=data.description,
            status=TICKET_STATUS_MAP.get(data.status.lower(), TicketStatus.OPEN),
            priority=self._map_ticket_priority(data.priority),
        )
        self.db.add(ticket)
        return ticket

    def _update_ticket(self, ticket: Ticket, data: CRMTicketPayload) -> None:
        """Update existing ticket from CRM data."""
        ticket.subject = data.subject
        if data.description is not None:
            ticket.description = data.description
        ticket.status = TICKET_STATUS_MAP.get(data.status.lower(), TicketStatus.OPEN)
        ticket.priority = self._map_ticket_priority(data.priority)

    def _build_ticket_crm_data(self, data: CRMTicketPayload) -> dict | None:
        """Build mapping crm_data for ticket payload, preserving backward compatibility."""
        crm_data: dict = dict(data.metadata or {})
        if data.description is not None:
            crm_data["description"] = data.description
        if data.comments:
            crm_data["comments"] = data.comments
        if data.activity_log:
            crm_data["activity_log"] = data.activity_log
        return crm_data or None

    def _sync_ticket_comments(
        self,
        org_id: UUID,
        ticket: Ticket,
        raw_comments: list[dict[str, Any]],
    ) -> tuple[int, int, list[str]]:
        """Sync CRM comment items to support.ticket_comment with idempotent dedupe."""
        processed = 0
        dedupe_hits = 0
        errors: list[str] = []

        for idx, raw in enumerate(raw_comments or []):
            try:
                item = CRMTicketCommentItem.model_validate(raw)
            except ValidationError as exc:
                errors.append(
                    f"comments[{idx}] validation failed: {exc.errors()[0].get('msg', 'invalid')}"
                )
                continue

            _, dedupe = self._upsert_crm_comment_item(org_id, ticket, item)
            processed += 1
            dedupe_hits += dedupe

        return processed, dedupe_hits, errors

    def _sync_ticket_activity(
        self,
        org_id: UUID,
        ticket: Ticket,
        raw_activity: list[dict[str, Any]],
        raw_comments: list[dict[str, Any]],
    ) -> tuple[int, int, list[str]]:
        """Sync CRM activity_log entries to support.ticket_comment (activity timeline)."""
        processed = 0
        dedupe_hits = 0
        errors: list[str] = []

        comment_ids = {
            str(item.get("id"))
            for item in (raw_comments or [])
            if isinstance(item, dict) and item.get("id")
        }

        for idx, raw in enumerate(raw_activity or []):
            try:
                entry = CRMTicketActivityEntry.model_validate(raw)
            except ValidationError as exc:
                errors.append(
                    f"activity_log[{idx}] validation failed: {exc.errors()[0].get('msg', 'invalid')}"
                )
                continue

            # Avoid double insert when comment appears in both comments[] and activity_log[].
            if entry.kind == "comment" and entry.id in comment_ids:
                dedupe_hits += 1
                logger.debug(
                    "CRM ticket activity dedupe hit: kind=comment id=%s ticket=%s",
                    entry.id,
                    ticket.ticket_number,
                )
                continue

            _, dedupe = self._upsert_crm_activity_item(org_id, ticket, entry)
            processed += 1
            dedupe_hits += dedupe

        return processed, dedupe_hits, errors

    def _upsert_crm_comment_item(
        self,
        org_id: UUID,
        ticket: Ticket,
        item: CRMTicketCommentItem,
    ) -> tuple[TicketComment, int]:
        """Upsert a CRM comment item by SyncEntity(source='crm', doctype, id)."""
        sync = self.db.scalar(
            select(SyncEntity).where(
                SyncEntity.organization_id == org_id,
                SyncEntity.source_system == "crm",
                SyncEntity.source_doctype == "ticket_comment",
                SyncEntity.source_name == item.id,
            )
        )

        author_id = self._resolve_crm_person_id(org_id, item.author_person_id)
        comment_type = (
            CommentType.INTERNAL_NOTE if item.is_internal else CommentType.COMMENT
        )

        if sync and sync.target_id:
            existing = self.db.get(TicketComment, sync.target_id)
            if existing:
                existing.ticket_id = ticket.ticket_id
                existing.comment_type = comment_type
                existing.is_internal = item.is_internal
                existing.author_id = author_id
                existing.content = item.body or ""
                if item.timestamp:
                    existing.created_at = item.timestamp
                sync.mark_synced(existing.comment_id)
                logger.debug(
                    "CRM ticket comment dedupe hit: id=%s ticket=%s",
                    item.id,
                    ticket.ticket_number,
                )
                return existing, 1

        comment = TicketComment(
            ticket_id=ticket.ticket_id,
            comment_type=comment_type,
            content=item.body or "",
            author_id=author_id,
            is_internal=item.is_internal,
        )
        if item.timestamp:
            comment.created_at = item.timestamp
        self.db.add(comment)
        self.db.flush()

        if sync:
            sync.target_table = "support.ticket_comment"
            sync.target_id = comment.comment_id
            sync.mark_synced(comment.comment_id)
        else:
            self.db.add(
                SyncEntity(
                    organization_id=org_id,
                    source_system="crm",
                    source_doctype="ticket_comment",
                    source_name=item.id,
                    target_table="support.ticket_comment",
                    target_id=comment.comment_id,
                    sync_status=SyncStatus.SYNCED,
                )
            )
        return comment, 0

    def _upsert_crm_activity_item(
        self,
        org_id: UUID,
        ticket: Ticket,
        entry: CRMTicketActivityEntry,
    ) -> tuple[TicketComment, int]:
        """Upsert a CRM activity item by SyncEntity(source='crm', kind, id)."""
        doctype = f"ticket_activity_{entry.kind}"
        sync = self.db.scalar(
            select(SyncEntity).where(
                SyncEntity.organization_id == org_id,
                SyncEntity.source_system == "crm",
                SyncEntity.source_doctype == doctype,
                SyncEntity.source_name == entry.id,
            )
        )
        author_id = self._resolve_crm_person_id(org_id, entry.author_person_id)

        if entry.kind == "comment":
            comment_type = (
                CommentType.INTERNAL_NOTE if entry.is_internal else CommentType.COMMENT
            )
            content = entry.body or ""
            action = None
            old_value = None
            new_value = None
            is_internal = entry.is_internal
        else:
            comment_type = CommentType.SYSTEM
            details_str = (
                json.dumps(entry.details, sort_keys=True) if entry.details else ""
            )
            content = (
                entry.body or f"{entry.event_type or 'event'} {details_str}".strip()
            )
            action = entry.event_type or "crm_event"
            old_value = None
            new_value = entry.status
            is_internal = True

        if sync and sync.target_id:
            existing = self.db.get(TicketComment, sync.target_id)
            if existing:
                existing.ticket_id = ticket.ticket_id
                existing.comment_type = comment_type
                existing.content = content
                existing.author_id = author_id
                existing.is_internal = is_internal
                existing.action = action
                existing.old_value = old_value
                existing.new_value = new_value
                if entry.timestamp:
                    existing.created_at = entry.timestamp
                sync.mark_synced(existing.comment_id)
                logger.debug(
                    "CRM ticket activity dedupe hit: kind=%s id=%s ticket=%s",
                    entry.kind,
                    entry.id,
                    ticket.ticket_number,
                )
                return existing, 1

        comment = TicketComment(
            ticket_id=ticket.ticket_id,
            comment_type=comment_type,
            content=content,
            author_id=author_id,
            is_internal=is_internal,
            action=action,
            old_value=old_value,
            new_value=new_value,
        )
        if entry.timestamp:
            comment.created_at = entry.timestamp
        self.db.add(comment)
        self.db.flush()

        if sync:
            sync.target_table = "support.ticket_comment"
            sync.target_id = comment.comment_id
            sync.mark_synced(comment.comment_id)
        else:
            self.db.add(
                SyncEntity(
                    organization_id=org_id,
                    source_system="crm",
                    source_doctype=doctype,
                    source_name=entry.id,
                    target_table="support.ticket_comment",
                    target_id=comment.comment_id,
                    sync_status=SyncStatus.SYNCED,
                )
            )
        return comment, 0

    def _resolve_crm_person_id(
        self, org_id: UUID, author_person_id: str | None
    ) -> UUID | None:
        """Resolve CRM author to local Person ID.

        Accepts either:
        - Person UUID (people.id)
        - Employee UUID (hr.employee.employee_id), then maps to person_id.
        """
        if not author_person_id:
            return None
        try:
            external_id = UUID(author_person_id)
        except (TypeError, ValueError):
            return None

        # 1) Direct Person UUID
        person = self.db.get(Person, external_id)
        if not person or person.organization_id != org_id:
            # 2) Employee UUID -> Person UUID fallback
            employee = self.db.get(Employee, external_id)
            if (
                employee
                and employee.organization_id == org_id
                and employee.person_id is not None
            ):
                return employee.person_id
            return None
        return external_id

    def _create_task(
        self,
        org_id: UUID,
        data: CRMWorkOrderPayload,
        project_id: UUID,
        ticket_id: UUID | None,
        employee_id: UUID | None,
    ) -> Task:
        """Create a local Task from CRM work order data."""
        task_code = self._generate_unique_code("WO", data.crm_id, max_len=30)

        task = Task(
            organization_id=org_id,
            project_id=project_id,
            task_code=task_code,
            task_name=data.title,
            status=TASK_STATUS_MAP.get(data.status.lower(), TaskStatus.OPEN),
            priority=self._map_task_priority(data.priority),
            assigned_to_id=employee_id,
            ticket_id=ticket_id,
            start_date=data.scheduled_start.date() if data.scheduled_start else None,
            due_date=data.scheduled_end.date() if data.scheduled_end else None,
        )
        self.db.add(task)
        return task

    def _update_task(
        self,
        task: Task,
        data: CRMWorkOrderPayload,
        project_id: UUID | None,
        ticket_id: UUID | None,
        employee_id: UUID | None,
    ) -> None:
        """Update existing task from CRM work order data."""
        task.task_name = data.title
        task.status = TASK_STATUS_MAP.get(data.status.lower(), TaskStatus.OPEN)
        task.priority = self._map_task_priority(data.priority)
        if project_id:
            task.project_id = project_id
        if ticket_id:
            task.ticket_id = ticket_id
        if employee_id:
            task.assigned_to_id = employee_id
        if data.scheduled_start:
            task.start_date = data.scheduled_start.date()
        if data.scheduled_end:
            task.due_date = data.scheduled_end.date()

    def _resolve_project_id(self, org_id: UUID, crm_id: str | None) -> UUID | None:
        """Resolve CRM project ID to local project ID."""
        if not crm_id:
            return None
        mapping = self._get_mapping(org_id, CRMEntityType.PROJECT, crm_id)
        return mapping.local_entity_id if mapping else None

    def _resolve_ticket_id(self, org_id: UUID, crm_id: str | None) -> UUID | None:
        """Resolve CRM ticket ID to local ticket ID."""
        if not crm_id:
            return None
        mapping = self._get_mapping(org_id, CRMEntityType.TICKET, crm_id)
        return mapping.local_entity_id if mapping else None

    def _resolve_employee_id(self, org_id: UUID, email: str | None) -> UUID | None:
        """Resolve employee email to employee ID.

        Looks up by person.email (work email) or employee.personal_email.
        """
        if not email:
            return None
        email_lower = email.lower()

        # First try via Person.email (work email)
        stmt = (
            select(Employee.employee_id)
            .join(Person, Employee.person_id == Person.id)
            .where(
                Employee.organization_id == org_id,
                func.lower(Person.email) == email_lower,
            )
        )
        result = self.db.scalar(stmt)
        if result:
            return result

        # Fallback to personal_email
        stmt = select(Employee.employee_id).where(
            Employee.organization_id == org_id,
            func.lower(Employee.personal_email) == email_lower,
        )
        return self.db.scalar(stmt)

    def _get_or_create_default_project(self, org_id: UUID) -> UUID:
        """Get or create a default project for orphan work orders.

        Handles race condition by catching IntegrityError on duplicate insert.
        Uses a savepoint to avoid rolling back the entire transaction.
        """
        from sqlalchemy.exc import IntegrityError

        stmt = select(Project).where(
            Project.organization_id == org_id,
            Project.project_code == "CRM-DEFAULT",
        )
        project = self.db.scalar(stmt)

        if project:
            return project.project_id

        # Try to create inside savepoint so failure doesn't roll back the
        # outer transaction (e.g. an in-progress bulk sync batch).
        savepoint = self.db.begin_nested()
        try:
            project = Project(
                organization_id=org_id,
                project_code="CRM-DEFAULT",
                project_name="CRM Work Orders (Unassigned)",
                description="Default project for CRM work orders without a project assignment",
                status=ProjectStatus.ACTIVE,
                project_type=ProjectType.INTERNAL,
            )
            self.db.add(project)
            self.db.flush()
            savepoint.commit()
            return project.project_id
        except IntegrityError:
            # Race condition - another request created it
            savepoint.rollback()
            project = self.db.scalar(stmt)
            if project:
                return project.project_id
            raise  # Re-raise if still not found (unexpected)

    def _map_project_type(self, type_str: str | None) -> ProjectType:
        """Map CRM project type to local enum."""
        if not type_str:
            return ProjectType.CLIENT
        type_map = {
            "internal": ProjectType.INTERNAL,
            "client": ProjectType.CLIENT,
            "fiber": ProjectType.FIBER_OPTICS_INSTALLATION,
            "airfiber": ProjectType.AIR_FIBER_INSTALLATION,
        }
        return type_map.get(type_str.lower(), ProjectType.CLIENT)

    def _map_ticket_priority(self, priority_str: str | None) -> TicketPriority:
        """Map CRM priority to local enum."""
        if not priority_str:
            return TicketPriority.MEDIUM
        priority_map = {
            "low": TicketPriority.LOW,
            "medium": TicketPriority.MEDIUM,
            "high": TicketPriority.HIGH,
            "urgent": TicketPriority.URGENT,
            "critical": TicketPriority.URGENT,
        }
        return priority_map.get(priority_str.lower(), TicketPriority.MEDIUM)

    def _map_task_priority(self, priority_str: str | None) -> TaskPriority:
        """Map CRM priority to local enum."""
        if not priority_str:
            return TaskPriority.MEDIUM
        priority_map = {
            "low": TaskPriority.LOW,
            "medium": TaskPriority.MEDIUM,
            "high": TaskPriority.HIGH,
            "urgent": TaskPriority.URGENT,
            "critical": TaskPriority.URGENT,
        }
        return priority_map.get(priority_str.lower(), TaskPriority.MEDIUM)

    def _calculate_expense_totals(
        self,
        org_id: UUID,
        project_id: UUID | None = None,
        ticket_id: UUID | None = None,
        task_id: UUID | None = None,
    ) -> ExpenseTotals:
        """Calculate expense totals grouped by status."""
        # Build base query
        stmt = select(
            ExpenseClaim.status,
            func.coalesce(func.sum(ExpenseClaim.total_claimed_amount), 0).label(
                "total"
            ),
        ).where(ExpenseClaim.organization_id == org_id)

        if project_id:
            stmt = stmt.where(ExpenseClaim.project_id == project_id)
        if ticket_id:
            stmt = stmt.where(ExpenseClaim.ticket_id == ticket_id)
        if task_id:
            stmt = stmt.where(ExpenseClaim.task_id == task_id)

        stmt = stmt.group_by(ExpenseClaim.status)
        results = self.db.execute(stmt).all()

        totals = ExpenseTotals()
        for status, total in results:
            amount = Decimal(str(total)) if total else Decimal("0.00")
            if status == ExpenseClaimStatus.DRAFT:
                totals.draft = amount
            elif status == ExpenseClaimStatus.SUBMITTED:
                totals.submitted = amount
            elif status in (
                ExpenseClaimStatus.APPROVED,
                ExpenseClaimStatus.PENDING_APPROVAL,
            ):
                totals.approved += amount
            elif status == ExpenseClaimStatus.PAID:
                totals.paid = amount

        return totals
