"""
DotMac CRM Sync Service - Business logic for CRM entity synchronization.

Handles:
- Syncing projects, tickets, and work orders from DotMac CRM
- Mapping CRM entities to local ERP entities
- Providing expense totals for CRM entities
"""
import hashlib
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.expense.expense_claim import ExpenseClaim, ExpenseClaimStatus
from app.models.finance.core_org.project import Project, ProjectStatus, ProjectType
from app.models.people.hr.employee import Employee
from app.models.person import Person
from app.models.pm.task import Task, TaskStatus, TaskPriority
from app.models.support.ticket import Ticket, TicketStatus, TicketPriority
from app.models.sync.dotmac_crm_sync import (
    CRMEntityType,
    CRMSyncMapping,
    CRMSyncStatus,
)
from app.schemas.sync.dotmac_crm import (
    CRMProjectPayload,
    CRMProjectRead,
    CRMTicketPayload,
    CRMTicketRead,
    CRMWorkOrderPayload,
    CRMWorkOrderRead,
    ExpenseTotals,
    InventoryItemDetail,
    InventoryItemStock,
    InventoryListResponse,
    WarehouseStock,
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
            self._update_mapping(mapping, data.name, data.code, data.customer_name, data.status, data.metadata)
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
                crm_status=CRM_SYNC_STATUS_MAP.get(data.status.lower(), CRMSyncStatus.ACTIVE),
                display_name=data.name,
                display_code=data.code,
                customer_name=data.customer_name,
                crm_data=data.metadata,
            )
            self.db.add(mapping)

        logger.info("Synced CRM project %s -> %s", data.crm_id, mapping.local_entity_id)
        return mapping

    def sync_ticket(
        self,
        org_id: UUID,
        data: CRMTicketPayload,
    ) -> CRMSyncMapping:
        """
        Sync a ticket from CRM to ERP.

        Creates or updates both the local Ticket and the CRMSyncMapping.
        """
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
            self._update_mapping(mapping, data.subject, data.ticket_number, data.customer_name, data.status, data.metadata)
        else:
            ticket = self._create_ticket(org_id, data)
            self.db.flush()

            mapping = CRMSyncMapping(
                organization_id=org_id,
                crm_entity_type=CRMEntityType.TICKET,
                crm_id=data.crm_id,
                local_entity_type="ticket",
                local_entity_id=ticket.ticket_id,
                crm_status=CRM_SYNC_STATUS_MAP.get(data.status.lower(), CRMSyncStatus.ACTIVE),
                display_name=data.subject,
                display_code=data.ticket_number,
                customer_name=data.customer_name,
                crm_data=data.metadata,
            )
            self.db.add(mapping)

        logger.info("Synced CRM ticket %s -> %s", data.crm_id, mapping.local_entity_id)
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
                task = self._create_task(org_id, data, project_id, ticket_id, employee_id)
                self.db.flush()
                mapping.local_entity_id = task.task_id
                mapping.local_entity_type = "task"
            self._update_mapping(mapping, data.title, None, None, data.status, data.metadata)
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
                crm_status=CRM_SYNC_STATUS_MAP.get(data.status.lower(), CRMSyncStatus.ACTIVE),
                display_name=data.title,
                crm_data=data.metadata,
            )
            self.db.add(mapping)

        logger.info("Synced CRM work order %s -> %s", data.crm_id, mapping.local_entity_id)
        return mapping

    # ============ List Operations (for UI dropdowns) ============

    def list_projects(
        self,
        org_id: UUID,
        search: Optional[str] = None,
        status: Optional[str] = None,
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
            stmt = stmt.where(CRMSyncMapping.crm_status == CRM_SYNC_STATUS_MAP.get(status.lower(), CRMSyncStatus.ACTIVE))

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
        search: Optional[str] = None,
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
        search: Optional[str] = None,
        employee_id: Optional[UUID] = None,
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

    def list_inventory_items(
        self,
        org_id: UUID,
        search: Optional[str] = None,
        category_code: Optional[str] = None,
        warehouse_id: Optional[UUID] = None,
        include_zero_stock: bool = False,
        only_below_reorder: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> InventoryListResponse:
        """
        List inventory items with current stock levels for CRM.

        Returns items that CRM can use for installation assignments.

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
        # Import here to avoid circular imports
        from app.models.finance.inv.item import Item
        from app.models.finance.inv.item_category import ItemCategory
        from app.services.finance.inv.balance import InventoryBalanceService

        # Build base query for active inventory items
        stmt = (
            select(Item, ItemCategory)
            .outerjoin(ItemCategory, Item.category_id == ItemCategory.category_id)
            .where(
                Item.organization_id == org_id,
                Item.is_active.is_(True),
                Item.track_inventory.is_(True),
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

        items: list[InventoryItemStock] = []
        has_more = False

        # Fast path when we don't filter by availability or reorder point
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

            for item, category in results:
                if warehouse_id:
                    balance = InventoryBalanceService.get_item_balance(
                        self.db, org_id, item.item_id, warehouse_id
                    )
                    on_hand = balance.quantity_on_hand if balance else Decimal("0")
                    reserved = balance.quantity_reserved if balance else Decimal("0")
                    available = balance.quantity_available if balance else Decimal("0")
                else:
                    on_hand = InventoryBalanceService.get_on_hand(
                        self.db, org_id, item.item_id
                    )
                    reserved = InventoryBalanceService.get_reserved(
                        self.db, org_id, item.item_id
                    )
                    available = on_hand - reserved

                reorder_point = item.reorder_point or Decimal("0")
                is_below_reorder = available <= reorder_point if reorder_point else False

                items.append(InventoryItemStock(
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
                    is_below_reorder=is_below_reorder,
                ))
        else:
            # Filtered pagination: compute total_count and has_more against filtered items
            total_count = 0
            seen_filtered = 0
            batch_size = max(limit, 200)
            current_offset = 0

            while True:
                page_stmt = stmt.order_by(Item.item_code).offset(current_offset).limit(batch_size)
                results = self.db.execute(page_stmt).all()
                if not results:
                    break

                for item, category in results:
                    if warehouse_id:
                        balance = InventoryBalanceService.get_item_balance(
                            self.db, org_id, item.item_id, warehouse_id
                        )
                        on_hand = balance.quantity_on_hand if balance else Decimal("0")
                        reserved = balance.quantity_reserved if balance else Decimal("0")
                        available = balance.quantity_available if balance else Decimal("0")
                    else:
                        on_hand = InventoryBalanceService.get_on_hand(
                            self.db, org_id, item.item_id
                        )
                        reserved = InventoryBalanceService.get_reserved(
                            self.db, org_id, item.item_id
                        )
                        available = on_hand - reserved

                    if not include_zero_stock and available <= 0:
                        continue

                    reorder_point = item.reorder_point or Decimal("0")
                    is_below_reorder = available <= reorder_point if reorder_point else False
                    if only_below_reorder and not is_below_reorder:
                        continue

                    total_count += 1
                    if seen_filtered < offset:
                        seen_filtered += 1
                        continue

                    if len(items) < limit:
                        items.append(InventoryItemStock(
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
                            is_below_reorder=is_below_reorder,
                        ))
                    else:
                        has_more = True

                current_offset += batch_size
                if len(results) < batch_size:
                    break

        return InventoryListResponse(
            items=items,
            total_count=total_count,
            has_more=has_more,
        )

    def get_inventory_item_detail(
        self,
        org_id: UUID,
        item_id: UUID,
    ) -> Optional[InventoryItemDetail]:
        """
        Get detailed inventory item info with warehouse breakdown.

        Args:
            org_id: Organization ID
            item_id: Item ID to retrieve

        Returns:
            InventoryItemDetail with warehouse-level stock, or None if not found
        """
        from app.models.finance.inv.item import Item
        from app.models.finance.inv.item_category import ItemCategory
        from app.services.finance.inv.balance import InventoryBalanceService

        # Get item
        item = self.db.get(Item, item_id)
        if not item or item.organization_id != org_id:
            return None

        # Get category
        category = self.db.get(ItemCategory, item.category_id) if item.category_id else None

        # Get stock summary with warehouse breakdown
        summary = InventoryBalanceService.get_item_stock_summary(
            self.db, org_id, item_id
        )

        warehouse_stocks: list[WarehouseStock] = []
        if summary:
            for wh_balance in summary.warehouses:
                if wh_balance.warehouse_id:
                    warehouse_stocks.append(WarehouseStock(
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
                    ))

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
        from app.models.finance.inv.item_category import ItemCategory

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
        from app.models.finance.inv.warehouse import Warehouse

        stmt = (
            select(Warehouse.warehouse_id, Warehouse.warehouse_code, Warehouse.warehouse_name)
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
    ) -> Optional[ExpenseTotals]:
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
    ) -> Optional[ExpenseTotals]:
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
    ) -> Optional[ExpenseTotals]:
        """Get expense totals for a CRM work order."""
        mapping = self._get_mapping(org_id, CRMEntityType.WORK_ORDER, crm_id)
        if not mapping:
            return None

        return self._calculate_expense_totals(
            org_id,
            task_id=mapping.local_entity_id,
        )

    # ============ Lookup Helpers ============

    def get_local_project_id(self, org_id: UUID, crm_id: str) -> Optional[UUID]:
        """Get local project ID for a CRM project."""
        mapping = self._get_mapping(org_id, CRMEntityType.PROJECT, crm_id)
        return mapping.local_entity_id if mapping else None

    def get_local_ticket_id(self, org_id: UUID, crm_id: str) -> Optional[UUID]:
        """Get local ticket ID for a CRM ticket."""
        mapping = self._get_mapping(org_id, CRMEntityType.TICKET, crm_id)
        return mapping.local_entity_id if mapping else None

    def get_local_task_id(self, org_id: UUID, crm_id: str) -> Optional[UUID]:
        """Get local task ID for a CRM work order."""
        mapping = self._get_mapping(org_id, CRMEntityType.WORK_ORDER, crm_id)
        return mapping.local_entity_id if mapping else None

    # ============ Private Helpers ============

    def _get_mapping(
        self,
        org_id: UUID,
        entity_type: CRMEntityType,
        crm_id: str,
    ) -> Optional[CRMSyncMapping]:
        """Get CRM sync mapping by org, type, and CRM ID."""
        stmt = select(CRMSyncMapping).where(
            CRMSyncMapping.organization_id == org_id,
            CRMSyncMapping.crm_entity_type == entity_type,
            CRMSyncMapping.crm_id == crm_id,
        )
        return self.db.scalar(stmt)

    def _update_mapping(
        self,
        mapping: CRMSyncMapping,
        display_name: str,
        display_code: Optional[str],
        customer_name: Optional[str],
        status: str,
        crm_data: Optional[dict] = None,
    ) -> None:
        """Update mapping fields on sync."""
        mapping.display_name = display_name
        mapping.display_code = display_code
        mapping.customer_name = customer_name
        mapping.crm_status = CRM_SYNC_STATUS_MAP.get(status.lower(), CRMSyncStatus.ACTIVE)
        mapping.synced_at = datetime.now(timezone.utc)
        if crm_data is not None:
            mapping.crm_data = crm_data

    def _generate_unique_code(self, prefix: str, crm_id: str, max_len: int = 20) -> str:
        """Generate a unique code from CRM ID using hash to avoid collisions."""
        # Use hash of full CRM ID for uniqueness, take enough chars to fit max_len
        hash_suffix = hashlib.sha256(crm_id.encode()).hexdigest()[:max_len - len(prefix) - 1]
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
        project.status = PROJECT_STATUS_MAP.get(data.status.lower(), ProjectStatus.ACTIVE)
        project.start_date = data.start_at.date() if data.start_at else project.start_date
        project.end_date = data.due_at.date() if data.due_at else project.end_date

    def _create_ticket(self, org_id: UUID, data: CRMTicketPayload) -> Ticket:
        """Create a local Ticket from CRM data."""
        ticket_number = data.ticket_number or self._generate_unique_code("CRM", data.crm_id, max_len=50)

        ticket = Ticket(
            organization_id=org_id,
            ticket_number=ticket_number,
            subject=data.subject,
            status=TICKET_STATUS_MAP.get(data.status.lower(), TicketStatus.OPEN),
            priority=self._map_ticket_priority(data.priority),
        )
        self.db.add(ticket)
        return ticket

    def _update_ticket(self, ticket: Ticket, data: CRMTicketPayload) -> None:
        """Update existing ticket from CRM data."""
        ticket.subject = data.subject
        ticket.status = TICKET_STATUS_MAP.get(data.status.lower(), TicketStatus.OPEN)
        ticket.priority = self._map_ticket_priority(data.priority)

    def _create_task(
        self,
        org_id: UUID,
        data: CRMWorkOrderPayload,
        project_id: UUID,
        ticket_id: Optional[UUID],
        employee_id: Optional[UUID],
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
        project_id: Optional[UUID],
        ticket_id: Optional[UUID],
        employee_id: Optional[UUID],
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

    def _resolve_project_id(self, org_id: UUID, crm_id: Optional[str]) -> Optional[UUID]:
        """Resolve CRM project ID to local project ID."""
        if not crm_id:
            return None
        mapping = self._get_mapping(org_id, CRMEntityType.PROJECT, crm_id)
        return mapping.local_entity_id if mapping else None

    def _resolve_ticket_id(self, org_id: UUID, crm_id: Optional[str]) -> Optional[UUID]:
        """Resolve CRM ticket ID to local ticket ID."""
        if not crm_id:
            return None
        mapping = self._get_mapping(org_id, CRMEntityType.TICKET, crm_id)
        return mapping.local_entity_id if mapping else None

    def _resolve_employee_id(self, org_id: UUID, email: Optional[str]) -> Optional[UUID]:
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
        """
        from sqlalchemy.exc import IntegrityError

        stmt = select(Project).where(
            Project.organization_id == org_id,
            Project.project_code == "CRM-DEFAULT",
        )
        project = self.db.scalar(stmt)

        if project:
            return project.project_id

        # Try to create - may fail if concurrent request created it
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
            return project.project_id
        except IntegrityError:
            # Race condition - another request created it, rollback and fetch
            self.db.rollback()
            project = self.db.scalar(stmt)
            if project:
                return project.project_id
            raise  # Re-raise if still not found (unexpected)

    def _map_project_type(self, type_str: Optional[str]) -> ProjectType:
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

    def _map_ticket_priority(self, priority_str: Optional[str]) -> TicketPriority:
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

    def _map_task_priority(self, priority_str: Optional[str]) -> TaskPriority:
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
        project_id: Optional[UUID] = None,
        ticket_id: Optional[UUID] = None,
        task_id: Optional[UUID] = None,
    ) -> ExpenseTotals:
        """Calculate expense totals grouped by status."""
        # Build base query
        stmt = select(
            ExpenseClaim.status,
            func.coalesce(func.sum(ExpenseClaim.total_claimed_amount), 0).label("total"),
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
            elif status in (ExpenseClaimStatus.APPROVED, ExpenseClaimStatus.PENDING_APPROVAL):
                totals.approved += amount
            elif status == ExpenseClaimStatus.PAID:
                totals.paid = amount

        return totals
