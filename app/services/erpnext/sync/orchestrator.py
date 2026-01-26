"""
ERPNext Sync Orchestrator.

Coordinates the full migration process with proper dependency ordering.
"""
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional, Sequence, cast

from sqlalchemy.orm import Session

from app.models.sync import SyncHistory, SyncJobStatus, SyncType
from app.services.erpnext.client import ERPNextClient, ERPNextConfig

from .base import SyncResult

# Core sync services
from .accounts import AccountSyncService
from .items import ItemCategorySyncService, ItemSyncService
from .assets import AssetCategorySyncService, AssetSyncService
from .contacts import CustomerSyncService, SupplierSyncService
from .warehouses import WarehouseSyncService

# HR sync services
from .hr import (
    DepartmentSyncService,
    DesignationSyncService,
    EmploymentTypeSyncService,
    EmployeeGradeSyncService,
    EmployeeSyncService,
)

# Leave & Attendance sync services
from .leave import (
    LeaveTypeSyncService,
    LeaveAllocationSyncService,
    LeaveApplicationSyncService,
)
from .attendance import ShiftTypeSyncService, AttendanceSyncService

# Expense sync services
from .expense import ExpenseCategorySyncService, ExpenseClaimSyncService

# Project & Support sync services
from .projects import ProjectSyncService
from .support import TicketSyncService
from .tasks import TaskSyncService
from .timesheets import TimesheetSyncService
from .material_request import MaterialRequestSyncService

logger = logging.getLogger(__name__)


@dataclass
class MigrationConfig:
    """Configuration for ERPNext migration."""

    # ERPNext connection
    erpnext_url: str
    erpnext_api_key: str
    erpnext_api_secret: str
    erpnext_company: Optional[str] = None

    # Sync options
    sync_type: SyncType = SyncType.FULL
    batch_size: int = 100

    # Entity types to sync (empty = all)
    entity_types: Optional[list[str]] = None

    # DotMac ERP configuration
    ar_control_account_id: Optional[uuid.UUID] = None
    ap_control_account_id: Optional[uuid.UUID] = None
    default_inventory_account_id: Optional[uuid.UUID] = None
    default_asset_account_id: Optional[uuid.UUID] = None
    default_depreciation_account_id: Optional[uuid.UUID] = None


# Sync phases with dependencies
SYNC_PHASES = [
    {
        "name": "Phase 1: Foundation",
        "entities": ["accounts", "item_categories", "asset_categories", "warehouses"],
    },
    {
        "name": "Phase 2: Master Data",
        "entities": ["customers", "suppliers", "items", "assets"],
    },
    {
        "name": "Phase 3: HR Foundation",
        "entities": [
            "departments",
            "designations",
            "employment_types",
            "employee_grades",
        ],
    },
    {
        "name": "Phase 4: HR Master Data",
        "entities": ["employees"],
    },
    {
        "name": "Phase 5: Projects & Support",
        "entities": ["projects", "tickets"],
    },
    {
        "name": "Phase 5.5: Project Details",
        "entities": ["tasks", "timesheets"],
    },
    {
        "name": "Phase 5.7: Inventory Requests",
        "entities": ["material_requests"],
    },
    {
        "name": "Phase 6: Leave & Attendance",
        "entities": [
            "leave_types",
            "shift_types",
            "leave_allocations",
            "leave_applications",
            "attendance",
        ],
    },
    {
        "name": "Phase 7: Expenses",
        "entities": ["expense_categories", "expense_claims"],
    },
]

# All supported entity types
SUPPORTED_ENTITIES = {
    # Core Finance
    "accounts": "Chart of Accounts",
    "item_categories": "Item Categories",
    "asset_categories": "Asset Categories",
    "warehouses": "Warehouses",
    "customers": "Customers",
    "suppliers": "Suppliers",
    "items": "Items",
    "assets": "Assets",
    # HR Foundation
    "departments": "Departments",
    "designations": "Designations",
    "employment_types": "Employment Types",
    "employee_grades": "Employee Grades",
    # HR Master Data
    "employees": "Employees",
    # Projects & Support
    "projects": "Projects",
    "tickets": "Tickets/Issues",
    "tasks": "Project Tasks",
    "timesheets": "Timesheets",
    # Inventory Requests
    "material_requests": "Material Requests",
    # Leave & Attendance
    "leave_types": "Leave Types",
    "shift_types": "Shift Types",
    "leave_allocations": "Leave Allocations",
    "leave_applications": "Leave Applications",
    "attendance": "Attendance Records",
    # Expenses
    "expense_categories": "Expense Categories",
    "expense_claims": "Expense Claims",
}


class ERPNextSyncOrchestrator:
    """
    Orchestrates ERPNext to DotMac ERP migration.

    Handles:
    - Dependency ordering
    - Phase execution
    - Progress tracking
    - Error aggregation
    """

    def __init__(
        self,
        db: Session,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        config: MigrationConfig,
    ):
        self.db = db
        self.organization_id = organization_id
        self.user_id = user_id
        self.config = config
        self._client: Optional[ERPNextClient] = None
        self._history: Optional[SyncHistory] = None

    @property
    def client(self) -> ERPNextClient:
        """Lazy-initialize ERPNext client."""
        if self._client is None:
            self._client = ERPNextClient(
                ERPNextConfig(
                    url=self.config.erpnext_url,
                    api_key=self.config.erpnext_api_key,
                    api_secret=self.config.erpnext_api_secret,
                    company=self.config.erpnext_company,
                )
            )
        return self._client

    def _get_service(self, entity_type: str) -> Any:
        """Get sync service for entity type."""
        services = {
            # Core Finance
            "accounts": AccountSyncService,
            "item_categories": ItemCategorySyncService,
            "asset_categories": AssetCategorySyncService,
            "warehouses": WarehouseSyncService,
            "customers": CustomerSyncService,
            "suppliers": SupplierSyncService,
            "items": ItemSyncService,
            "assets": AssetSyncService,
            # HR Foundation
            "departments": DepartmentSyncService,
            "designations": DesignationSyncService,
            "employment_types": EmploymentTypeSyncService,
            "employee_grades": EmployeeGradeSyncService,
            # HR Master Data
            "employees": EmployeeSyncService,
            # Projects & Support
            "projects": ProjectSyncService,
            "tickets": TicketSyncService,
            "tasks": TaskSyncService,
            "timesheets": TimesheetSyncService,
            # Inventory Requests
            "material_requests": MaterialRequestSyncService,
            # Leave & Attendance
            "leave_types": LeaveTypeSyncService,
            "shift_types": ShiftTypeSyncService,
            "leave_allocations": LeaveAllocationSyncService,
            "leave_applications": LeaveApplicationSyncService,
            "attendance": AttendanceSyncService,
            # Expenses
            "expense_categories": ExpenseCategorySyncService,
            "expense_claims": ExpenseClaimSyncService,
        }

        service_class = services.get(entity_type)
        if not service_class:
            raise ValueError(f"Unknown entity type: {entity_type}")

        # Create service with config
        service = service_class(
            db=self.db,
            organization_id=self.organization_id,
            user_id=self.user_id,
        )

        # Inject account IDs where needed
        if hasattr(service, "ar_control_account_id"):
            service.ar_control_account_id = self.config.ar_control_account_id
        if hasattr(service, "ap_control_account_id"):
            service.ap_control_account_id = self.config.ap_control_account_id
        if hasattr(service, "inventory_account_id"):
            service.inventory_account_id = self.config.default_inventory_account_id
        if hasattr(service, "asset_account_id"):
            service.asset_account_id = self.config.default_asset_account_id
        if hasattr(service, "depreciation_account_id"):
            service.depreciation_account_id = self.config.default_depreciation_account_id

        return service

    def _create_history(self, entity_types: list[str]) -> SyncHistory:
        """Create sync history record."""
        history = SyncHistory(
            organization_id=self.organization_id,
            source_system="erpnext",
            sync_type=self.config.sync_type,
            entity_types=entity_types,
            created_by_user_id=self.user_id,
        )
        self.db.add(history)
        self.db.flush()
        return history

    def _filter_entities(self, entities: Sequence[str]) -> list[str]:
        """Filter entities based on config."""
        if not self.config.entity_types:
            return list(entities)
        return [e for e in entities if e in self.config.entity_types]

    def test_connection(self) -> dict[str, Any]:
        """Test ERPNext connection."""
        try:
            result = self.client.test_connection()
            return {"success": True, "user": result.get("user")}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def preview(self) -> dict[str, Any]:
        """
        Preview what will be synced.

        Returns counts for each entity type.
        """
        entity_types: dict[str, dict[str, Any]] = {}
        preview: dict[str, Any] = {
            "entity_types": entity_types,
            "total_records": 0,
        }

        # Determine entities to sync
        all_entities: list[str] = []
        for phase in SYNC_PHASES:
            all_entities.extend(phase["entities"])

        entities = self._filter_entities(all_entities)

        for entity_type in entities:
            try:
                count = self._get_count(entity_type)
                preview["entity_types"][entity_type] = {
                    "name": SUPPORTED_ENTITIES.get(entity_type, entity_type),
                    "count": count,
                }
                preview["total_records"] += count
            except Exception as e:
                logger.warning("Failed to get count for %s: %s", entity_type, e)
                preview["entity_types"][entity_type] = {
                    "name": SUPPORTED_ENTITIES.get(entity_type, entity_type),
                    "count": 0,
                    "error": str(e),
                }

        return preview

    def _get_count(self, entity_type: str) -> int:
        """Get count of records for entity type."""
        doctype_map = {
            # Core Finance
            "accounts": "Account",
            "item_categories": "Item Group",
            "asset_categories": "Asset Category",
            "warehouses": "Warehouse",
            "customers": "Customer",
            "suppliers": "Supplier",
            "items": "Item",
            "assets": "Asset",
            # HR Foundation
            "departments": "Department",
            "designations": "Designation",
            "employment_types": "Employment Type",
            "employee_grades": "Employee Grade",
            # HR Master Data
            "employees": "Employee",
            # Projects & Support
            "projects": "Project",
            "tickets": "Issue",  # May be "HD Ticket" for v14+
            "tasks": "Task",
            "timesheets": "Timesheet",
            # Inventory Requests
            "material_requests": "Material Request",
            # Leave & Attendance
            "leave_types": "Leave Type",
            "shift_types": "Shift Type",
            "leave_allocations": "Leave Allocation",
            "leave_applications": "Leave Application",
            "attendance": "Attendance",
            # Expenses
            "expense_categories": "Expense Claim Type",
            "expense_claims": "Expense Claim",
        }

        doctype = doctype_map.get(entity_type)
        if not doctype:
            return 0

        filters: dict[str, Any] = {}
        # Company filters for company-scoped DocTypes
        if self.config.erpnext_company and entity_type in [
            "accounts", "assets", "warehouses", "departments",
            "employees", "projects", "expense_claims", "timesheets",
            "material_requests",
        ]:
            filters["company"] = self.config.erpnext_company

        # Active record filters
        if entity_type in ["customers", "suppliers", "items"]:
            filters["disabled"] = 0
        if entity_type == "employees":
            filters["status"] = "Active"

        return self.client.get_count(doctype, filters)

    def run(self) -> SyncHistory:
        """
        Execute the full migration.

        Returns:
            SyncHistory with results
        """
        # Determine entities to sync
        all_entities: list[str] = []
        for phase in SYNC_PHASES:
            all_entities.extend(phase["entities"])

        entities = self._filter_entities(all_entities)

        # Create history record
        self._history = self._create_history(entities)
        self._history.start()

        try:
            # Execute phases in order
            for phase in SYNC_PHASES:
                phase_entities = self._filter_entities(phase["entities"])
                if not phase_entities:
                    continue

                logger.info("Starting %s", phase["name"])

                for entity_type in phase_entities:
                    result = self._sync_entity_type(entity_type)
                    self._update_history(result)

                # Commit after each phase
                self.db.commit()

            self._history.complete()

        except Exception as e:
            logger.exception("Migration failed: %s", e)
            self._history.fail(str(e))
            self.db.rollback()

        finally:
            if self._client:
                self._client.close()

        self.db.commit()
        return self._history

    def _sync_entity_type(self, entity_type: str) -> SyncResult:
        """Sync a single entity type."""
        logger.info("Syncing %s", entity_type)

        try:
            service = self._get_service(entity_type)
            incremental = self.config.sync_type == SyncType.INCREMENTAL
            result = cast(
                SyncResult,
                service.sync(
                client=self.client,
                incremental=incremental,
                batch_size=self.config.batch_size,
                ),
            )
            return result

        except Exception as e:
            logger.exception("Failed to sync %s: %s", entity_type, e)
            result = SyncResult(entity_type=entity_type)
            result.add_error("system", str(e))
            return result

    def _update_history(self, result: SyncResult) -> None:
        """Update history record with result."""
        if not self._history:
            return

        self._history.total_records += result.total_records
        self._history.synced_count += result.synced_count
        self._history.skipped_count += result.skipped_count
        self._history.error_count += result.error_count

        # Add errors (capped)
        for error in result.errors:
            self._history.add_error(
                doctype=result.entity_type,
                name=error.get("name", ""),
                error=error.get("error", ""),
            )

    def run_single(self, entity_type: str) -> SyncResult:
        """
        Sync a single entity type only.

        Useful for retrying or selective sync.
        """
        if entity_type not in SUPPORTED_ENTITIES:
            raise ValueError(f"Unknown entity type: {entity_type}")

        try:
            result = self._sync_entity_type(entity_type)
            self.db.commit()
            return result
        finally:
            if self._client:
                self._client.close()
