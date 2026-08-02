"""People assets maintenance workflow service."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.fixed_assets.asset import Asset
from app.models.fixed_assets.maintenance_request import (
    MaintenancePriority,
    MaintenanceRequest,
    MaintenanceRequestStatus,
    MaintenanceStatusLog,
)
from app.models.fixed_assets.maintenance_work_order import (
    MaintenanceWorkOrder,
    MaintenanceWorkOrderPart,
    MaintenanceWorkOrderPartStatus,
    MaintenanceWorkOrderStatus,
)
from app.models.inventory.inventory_transaction import TransactionType
from app.models.inventory.item import Item
from app.models.procurement.enums import UrgencyLevel
from app.schemas.procurement.requisition import RequisitionCreate, RequisitionLineCreate
from app.services.common import (
    NotFoundError,
    PaginatedResult,
    PaginationParams,
    ValidationError,
)
from app.services.inventory.balance import InventoryBalanceService
from app.services.inventory.transaction import (
    InventoryTransactionService,
    TransactionInput,
)
from app.services.procurement.requisition import RequisitionService
from app.services.people.assets.lifecycle_event_service import (
    record_asset_lifecycle_event,
)

__all__ = ["AssetMaintenanceService"]

try:
    from datetime import UTC  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover
    UTC = timezone.utc


class AssetMaintenanceService:
    """Manage maintenance requests, work orders, parts, and completion."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def _get_asset(self, org_id: UUID, asset_id: UUID) -> Asset:
        asset = self.db.get(Asset, asset_id)
        if not asset or asset.organization_id != org_id:
            raise NotFoundError("Asset not found")
        return asset

    def _get_request(
        self, org_id: UUID, maintenance_request_id: UUID
    ) -> MaintenanceRequest:
        request = self.db.get(MaintenanceRequest, maintenance_request_id)
        if not request or request.organization_id != org_id:
            raise NotFoundError("Maintenance request not found")
        return request

    def _get_work_order(
        self, org_id: UUID, work_order_id: UUID
    ) -> MaintenanceWorkOrder:
        work_order = self.db.get(MaintenanceWorkOrder, work_order_id)
        if not work_order or work_order.organization_id != org_id:
            raise NotFoundError("Maintenance work order not found")
        return work_order

    def _log_status(
        self,
        *,
        org_id: UUID,
        asset_id: UUID,
        entity_type: str,
        entity_id: UUID,
        previous_status: str,
        new_status: str,
        changed_by_user_id: UUID | None,
        reason: str | None = None,
    ) -> None:
        self.db.add(
            MaintenanceStatusLog(
                organization_id=org_id,
                entity_type=entity_type,
                entity_id=entity_id,
                previous_status=previous_status,
                new_status=new_status,
                changed_by_user_id=changed_by_user_id,
                reason=reason,
            )
        )
        record_asset_lifecycle_event(
            self.db,
            org_id=org_id,
            asset_id=asset_id,
            event_category="MAINTENANCE",
            event_type=f"{entity_type}_{previous_status}_TO_{new_status}",
            source_type="maintenance",
            source_record_id=entity_id,
            actor_user_id=changed_by_user_id,
            previous_status=previous_status,
            new_status=new_status,
            notes=reason or "Maintenance status updated",
        )

    def _set_request_status(
        self,
        request: MaintenanceRequest,
        new_status: MaintenanceRequestStatus,
        changed_by_user_id: UUID | None,
        reason: str | None = None,
    ) -> None:
        prev = request.status
        if prev == new_status:
            return
        request.status = new_status
        request.status_changed_at = datetime.now(UTC)
        request.status_changed_by_id = changed_by_user_id
        self._log_status(
            org_id=request.organization_id,
            asset_id=request.asset_id,
            entity_type="MAINTENANCE_REQUEST",
            entity_id=request.maintenance_request_id,
            previous_status=prev.value,
            new_status=new_status.value,
            changed_by_user_id=changed_by_user_id,
            reason=reason,
        )

    def _set_work_order_status(
        self,
        work_order: MaintenanceWorkOrder,
        new_status: MaintenanceWorkOrderStatus,
        changed_by_user_id: UUID | None,
        reason: str | None = None,
    ) -> None:
        prev = work_order.status
        if prev == new_status:
            return
        work_order.status = new_status
        work_order.status_changed_at = datetime.now(UTC)
        work_order.status_changed_by_id = changed_by_user_id
        self._log_status(
            org_id=work_order.organization_id,
            asset_id=work_order.asset_id,
            entity_type="MAINTENANCE_WORK_ORDER",
            entity_id=work_order.work_order_id,
            previous_status=prev.value,
            new_status=new_status.value,
            changed_by_user_id=changed_by_user_id,
            reason=reason,
        )

    def _next_request_number(self, org_id: UUID) -> str:
        total = (
            self.db.scalar(
                select(func.count(MaintenanceRequest.maintenance_request_id)).where(
                    MaintenanceRequest.organization_id == org_id
                )
            )
            or 0
        )
        return f"FAMR-{int(total) + 1:06d}"

    def _next_work_order_number(self, org_id: UUID) -> str:
        total = (
            self.db.scalar(
                select(func.count(MaintenanceWorkOrder.work_order_id)).where(
                    MaintenanceWorkOrder.organization_id == org_id
                )
            )
            or 0
        )
        return f"FAMWO-{int(total) + 1:06d}"

    def _next_procurement_requisition_number(self) -> str:
        stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
        return f"PRQ-{stamp}-{str(uuid4())[:4].upper()}"

    def list_requests(
        self,
        org_id: UUID,
        *,
        asset_id: UUID | None = None,
        status: MaintenanceRequestStatus | None = None,
        pagination: PaginationParams | None = None,
    ) -> PaginatedResult[MaintenanceRequest]:
        query = select(MaintenanceRequest).where(
            MaintenanceRequest.organization_id == org_id
        )
        if asset_id:
            query = query.where(MaintenanceRequest.asset_id == asset_id)
        if status:
            query = query.where(MaintenanceRequest.status == status)
        query = query.order_by(MaintenanceRequest.created_at.desc())
        total = self.db.scalar(select(func.count()).select_from(query.subquery())) or 0
        if pagination:
            query = query.offset(pagination.offset).limit(pagination.limit)
        items = list(self.db.scalars(query).all())
        return PaginatedResult(
            items=items,
            total=total,
            offset=pagination.offset if pagination else 0,
            limit=pagination.limit if pagination else len(items),
        )

    def create_request(
        self,
        org_id: UUID,
        *,
        asset_id: UUID,
        title: str,
        description: str | None,
        priority: MaintenancePriority,
        due_date: date | None,
        requested_by_user_id: UUID | None,
        created_by_user_id: UUID | None,
    ) -> MaintenanceRequest:
        self._get_asset(org_id, asset_id)
        request = MaintenanceRequest(
            organization_id=org_id,
            asset_id=asset_id,
            request_number=self._next_request_number(org_id),
            title=title,
            description=description,
            priority=priority,
            status=MaintenanceRequestStatus.OPEN,
            due_date=due_date,
            requested_by_user_id=requested_by_user_id,
            created_by_user_id=created_by_user_id,
        )
        self.db.add(request)
        self.db.flush()
        return request

    def list_work_orders(
        self,
        org_id: UUID,
        *,
        maintenance_request_id: UUID | None = None,
        status: MaintenanceWorkOrderStatus | None = None,
        pagination: PaginationParams | None = None,
    ) -> PaginatedResult[MaintenanceWorkOrder]:
        query = select(MaintenanceWorkOrder).where(
            MaintenanceWorkOrder.organization_id == org_id
        )
        if maintenance_request_id:
            query = query.where(
                MaintenanceWorkOrder.maintenance_request_id == maintenance_request_id
            )
        if status:
            query = query.where(MaintenanceWorkOrder.status == status)
        query = query.order_by(MaintenanceWorkOrder.created_at.desc())
        total = self.db.scalar(select(func.count()).select_from(query.subquery())) or 0
        if pagination:
            query = query.offset(pagination.offset).limit(pagination.limit)
        items = list(self.db.scalars(query).all())
        return PaginatedResult(
            items=items,
            total=total,
            offset=pagination.offset if pagination else 0,
            limit=pagination.limit if pagination else len(items),
        )

    def create_work_order(
        self,
        org_id: UUID,
        *,
        maintenance_request_id: UUID,
        assigned_to_user_id: UUID | None,
        planned_start_date: datetime | None,
        estimated_cost: Decimal | None,
        created_by_user_id: UUID | None,
    ) -> MaintenanceWorkOrder:
        request = self._get_request(org_id, maintenance_request_id)
        if request.status in {
            MaintenanceRequestStatus.COMPLETED,
            MaintenanceRequestStatus.CANCELLED,
        }:
            raise ValidationError("Cannot create work order for closed request")
        work_order = MaintenanceWorkOrder(
            organization_id=org_id,
            maintenance_request_id=maintenance_request_id,
            asset_id=request.asset_id,
            work_order_number=self._next_work_order_number(org_id),
            title=request.title,
            description=request.description,
            status=MaintenanceWorkOrderStatus.ASSIGNED,
            assigned_to_user_id=assigned_to_user_id,
            planned_start_date=planned_start_date,
            estimated_cost=estimated_cost or Decimal("0"),
            created_by_user_id=created_by_user_id,
        )
        self.db.add(work_order)
        self._set_request_status(
            request,
            MaintenanceRequestStatus.ASSIGNED,
            changed_by_user_id=created_by_user_id,
            reason=f"Work order {work_order.work_order_number} created",
        )
        self.db.flush()
        return work_order

    def start_work_order(
        self,
        org_id: UUID,
        work_order_id: UUID,
        *,
        started_by_user_id: UUID | None,
    ) -> MaintenanceWorkOrder:
        work_order = self._get_work_order(org_id, work_order_id)
        if work_order.status not in {
            MaintenanceWorkOrderStatus.ASSIGNED,
            MaintenanceWorkOrderStatus.DRAFT,
            MaintenanceWorkOrderStatus.WAITING_FOR_PARTS,
        }:
            raise ValidationError(
                f"Cannot start work order in {work_order.status.value} status"
            )
        work_order.started_at = datetime.now(UTC)
        self._set_work_order_status(
            work_order,
            MaintenanceWorkOrderStatus.IN_PROGRESS,
            started_by_user_id,
        )
        request = self._get_request(org_id, work_order.maintenance_request_id)
        self._set_request_status(
            request,
            MaintenanceRequestStatus.IN_PROGRESS,
            started_by_user_id,
        )
        self.db.flush()
        return work_order

    def use_parts(
        self,
        org_id: UUID,
        work_order_id: UUID,
        *,
        fiscal_period_id: UUID,
        warehouse_id: UUID,
        parts: list[dict],
        used_by_user_id: UUID,
        trigger_procurement: bool = True,
        procurement_requester_id: UUID | None = None,
        procurement_department_id: UUID | None = None,
    ) -> dict:
        work_order = self._get_work_order(org_id, work_order_id)
        request = self._get_request(org_id, work_order.maintenance_request_id)

        if work_order.status in {
            MaintenanceWorkOrderStatus.COMPLETED,
            MaintenanceWorkOrderStatus.CANCELLED,
        }:
            raise ValidationError("Cannot use parts on closed work order")

        shortages: list[dict] = []
        used_part_rows: list[MaintenanceWorkOrderPart] = []
        pending_part_rows: list[MaintenanceWorkOrderPart] = []
        total_issued_cost = Decimal("0")

        for part in parts:
            item_id = part["item_id"]
            requested_qty = Decimal(str(part["quantity"]))
            if requested_qty <= 0:
                raise ValidationError("Part quantity must be greater than zero")
            item = self.db.get(Item, item_id)
            if not item or item.organization_id != org_id:
                raise NotFoundError(f"Inventory item {item_id} not found")

            on_hand = InventoryBalanceService.get_on_hand(
                self.db,
                org_id,
                item_id,
                warehouse_id=warehouse_id,
            )

            issue_qty = min(on_hand, requested_qty)
            missing_qty = requested_qty - issue_qty

            if issue_qty > 0:
                txn = InventoryTransactionService.create_issue(
                    db=self.db,
                    organization_id=org_id,
                    input=TransactionInput(
                        transaction_type=TransactionType.ISSUE,
                        transaction_date=datetime.now(UTC),
                        fiscal_period_id=fiscal_period_id,
                        item_id=item_id,
                        warehouse_id=warehouse_id,
                        quantity=issue_qty,
                        unit_cost=Decimal("0"),
                        uom=item.base_uom,
                        currency_code=item.currency_code,
                        source_document_type="FA_MAINTENANCE_WORK_ORDER",
                        source_document_id=work_order.work_order_id,
                        reference=work_order.work_order_number,
                        reason_code="MAINTENANCE_PARTS",
                    ),
                    created_by_user_id=used_by_user_id,
                    auto_commit=False,
                )
                total_issued_cost += txn.total_cost or Decimal("0")
                row = MaintenanceWorkOrderPart(
                    organization_id=org_id,
                    work_order_id=work_order.work_order_id,
                    item_id=item_id,
                    warehouse_id=warehouse_id,
                    requested_quantity=requested_qty,
                    issued_quantity=issue_qty,
                    uom=item.base_uom,
                    status=MaintenanceWorkOrderPartStatus.USED,
                    issue_transaction_id=txn.transaction_id,
                    created_by_user_id=used_by_user_id,
                    notes=part.get("notes"),
                )
                self.db.add(row)
                used_part_rows.append(row)

            if missing_qty > 0:
                shortages.append(
                    {
                        "item": item,
                        "missing_qty": missing_qty,
                        "notes": part.get("notes"),
                    }
                )
                row = MaintenanceWorkOrderPart(
                    organization_id=org_id,
                    work_order_id=work_order.work_order_id,
                    item_id=item_id,
                    warehouse_id=warehouse_id,
                    requested_quantity=requested_qty,
                    issued_quantity=issue_qty,
                    uom=item.base_uom,
                    status=MaintenanceWorkOrderPartStatus.PENDING_PROCUREMENT,
                    created_by_user_id=used_by_user_id,
                    notes=part.get("notes"),
                )
                self.db.add(row)
                pending_part_rows.append(row)

        # E4 float elimination: the column is exact Decimal money — never float.
        current_actual_cost = Decimal(work_order.actual_cost or 0)
        updated_actual_cost = current_actual_cost + total_issued_cost
        work_order.actual_cost = updated_actual_cost

        requisition_id: UUID | None = None
        if shortages:
            self._set_work_order_status(
                work_order,
                MaintenanceWorkOrderStatus.WAITING_FOR_PARTS,
                used_by_user_id,
                reason="Inventory shortage detected for one or more spare parts",
            )
            self._set_request_status(
                request,
                MaintenanceRequestStatus.WAITING_FOR_PARTS,
                used_by_user_id,
                reason="Waiting for spare parts",
            )

            if trigger_procurement:
                requester_id = procurement_requester_id
                if requester_id is None:
                    raise ValidationError(
                        "procurement_requester_id is required when stock is insufficient"
                    )

                urgency = (
                    UrgencyLevel.EMERGENCY
                    if request.priority == MaintenancePriority.CRITICAL
                    else UrgencyLevel.URGENT
                    if request.priority == MaintenancePriority.HIGH
                    else UrgencyLevel.NORMAL
                )
                lines: list[RequisitionLineCreate] = []
                for idx, shortage in enumerate(shortages, start=1):
                    item = shortage["item"]
                    missing_qty = shortage["missing_qty"]
                    price = (
                        item.last_purchase_cost
                        or item.average_cost
                        or item.standard_cost
                        or Decimal("0")
                    )
                    lines.append(
                        RequisitionLineCreate(
                            line_number=idx,
                            item_id=item.item_id,
                            description=f"Spare part for {work_order.work_order_number}: {item.item_name}",
                            quantity=missing_qty,
                            uom=item.purchase_uom or item.base_uom,
                            estimated_unit_price=price,
                            estimated_amount=missing_qty * price,
                        )
                    )

                requisition = RequisitionService(self.db).create(
                    organization_id=org_id,
                    data=RequisitionCreate(
                        requisition_number=self._next_procurement_requisition_number(),
                        requisition_date=date.today(),
                        requester_id=requester_id,
                        department_id=procurement_department_id,
                        urgency=urgency,
                        justification=f"Auto-created from maintenance work order {work_order.work_order_number}",
                        lines=lines,
                    ),
                    created_by_user_id=used_by_user_id,
                )
                requisition_id = requisition.requisition_id
                for row in pending_part_rows:
                    row.procurement_requisition_id = requisition_id
        else:
            if work_order.status in {
                MaintenanceWorkOrderStatus.ASSIGNED,
                MaintenanceWorkOrderStatus.DRAFT,
                MaintenanceWorkOrderStatus.WAITING_FOR_PARTS,
            }:
                self._set_work_order_status(
                    work_order,
                    MaintenanceWorkOrderStatus.IN_PROGRESS,
                    used_by_user_id,
                )
            if request.status in {
                MaintenanceRequestStatus.OPEN,
                MaintenanceRequestStatus.ASSIGNED,
                MaintenanceRequestStatus.WAITING_FOR_PARTS,
            }:
                self._set_request_status(
                    request,
                    MaintenanceRequestStatus.IN_PROGRESS,
                    used_by_user_id,
                )

        self.db.flush()
        return {
            "work_order": work_order,
            "request": request,
            "used_parts": used_part_rows,
            "pending_parts": pending_part_rows,
            "procurement_requisition_id": requisition_id,
        }

    def complete_work_order(
        self,
        org_id: UUID,
        work_order_id: UUID,
        *,
        completed_by_user_id: UUID | None,
        completion_notes: str | None = None,
        labor_hours: Decimal | None = None,
        additional_cost: Decimal | None = None,
    ) -> MaintenanceWorkOrder:
        work_order = self._get_work_order(org_id, work_order_id)
        request = self._get_request(org_id, work_order.maintenance_request_id)

        if work_order.status == MaintenanceWorkOrderStatus.COMPLETED:
            raise ValidationError("Work order is already completed")
        if work_order.status == MaintenanceWorkOrderStatus.CANCELLED:
            raise ValidationError("Cannot complete cancelled work order")

        work_order.completed_at = datetime.now(UTC)
        work_order.completion_notes = completion_notes
        if labor_hours is not None:
            work_order.labor_hours = float(labor_hours)
        if additional_cost:
            # E4 float elimination: exact Decimal money end-to-end.
            current_actual_cost = Decimal(work_order.actual_cost or 0)
            updated_actual_cost = current_actual_cost + additional_cost
            work_order.actual_cost = updated_actual_cost
        self._set_work_order_status(
            work_order,
            MaintenanceWorkOrderStatus.COMPLETED,
            completed_by_user_id,
        )

        request.completed_at = datetime.now(UTC)
        self._set_request_status(
            request,
            MaintenanceRequestStatus.COMPLETED,
            completed_by_user_id,
        )
        self.db.flush()
        return work_order
