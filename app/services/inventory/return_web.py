"""
Inventory Return web/service helpers.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload

from app.config import settings
from app.models.finance.common.attachment import AttachmentCategory
from app.models.finance.gl.fiscal_period import FiscalPeriod
from app.models.inventory import (
    InventoryReturn,
    InventoryReturnMode,
    Item,
    MaterialRequest,
    MaterialRequestStatus,
    Warehouse,
)
from app.models.inventory.inventory_lot import InventoryLot
from app.models.inventory.inventory_lot_balance import InventoryLotBalance
from app.models.inventory.inventory_transaction import (
    InventoryTransaction,
    TransactionType,
)
from app.models.people.hr import Employee
from app.models.person import Person
from app.services.common import (
    PaginationParams,
    coerce_uuid,
    paginate,
    pagination_context,
)
from app.services.finance.common import format_file_size
from app.services.finance.common.attachment import attachment_service
from app.services.inventory.transaction import (
    InventoryTransactionService,
    TransactionInput,
)


class InventoryReturnWebService:
    """Return-to-store workflow helpers."""

    @staticmethod
    def _generate_return_number() -> str:
        stamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
        suffix = datetime.utcnow().strftime("%f")[-4:]
        return f"RET-{stamp}-{suffix}"

    @staticmethod
    def form_context(db: Session, organization_id: str) -> dict[str, Any]:
        org_id = coerce_uuid(organization_id)
        warehouses = list(
            db.scalars(
                select(Warehouse)
                .where(
                    Warehouse.organization_id == org_id,
                    Warehouse.is_active.is_(True),
                )
                .order_by(Warehouse.warehouse_name.asc())
            ).all()
        )
        return {
            "today": datetime.utcnow().date().isoformat(),
            "warehouses": [
                {
                    "warehouse_id": str(warehouse.warehouse_id),
                    "warehouse_code": warehouse.warehouse_code,
                    "warehouse_name": warehouse.warehouse_name,
                }
                for warehouse in warehouses
            ],
        }

    @staticmethod
    def list_context(
        db: Session,
        organization_id: str,
        *,
        page: int = 1,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Build context for the inventory returns list."""
        org_id = coerce_uuid(organization_id)

        stmt = (
            select(InventoryReturn)
            .options(
                joinedload(InventoryReturn.item),
                joinedload(InventoryReturn.source_warehouse),
                joinedload(InventoryReturn.destination_warehouse),
                joinedload(InventoryReturn.material_request),
            )
            .where(InventoryReturn.organization_id == org_id)
            .order_by(
                InventoryReturn.return_date.desc(),
                InventoryReturn.created_at.desc(),
            )
        )
        result = paginate(db, stmt, PaginationParams.from_page(page, limit))
        return {
            "returns": result.items,
            **pagination_context(result),
        }

    @staticmethod
    def material_request_typeahead(
        db: Session,
        organization_id: str,
        query: str,
        limit: int = 8,
    ) -> dict[str, Any]:
        org_id = coerce_uuid(organization_id)
        term = query.strip()
        if not term:
            return {"items": []}
        search_term = f"%{term}%"
        stmt = (
            select(MaterialRequest)
            .where(
                MaterialRequest.organization_id == org_id,
                MaterialRequest.status.in_(
                    [
                        MaterialRequestStatus.ISSUED,
                        MaterialRequestStatus.TRANSFERRED,
                    ]
                ),
            )
            .where(
                or_(
                    MaterialRequest.request_number.ilike(search_term),
                    MaterialRequest.remarks.ilike(search_term),
                )
            )
            .order_by(MaterialRequest.created_at.desc())
            .limit(limit)
        )
        requests = list(db.scalars(stmt).all())
        items: list[dict[str, Any]] = []
        for material_request in requests:
            requester = None
            if material_request.requested_by_id:
                employee = db.scalars(
                    select(Employee)
                    .join(Person, Person.id == Employee.person_id)
                    .where(Employee.employee_id == material_request.requested_by_id)
                ).first()
                if employee and employee.person:
                    requester = employee.person.name
            label = material_request.request_number
            if requester:
                label = f"{label} - {requester}"
            items.append(
                {
                    "ref": str(material_request.request_id),
                    "label": label,
                    "request_number": material_request.request_number,
                    "request_type": material_request.request_type.value,
                    "status": material_request.status.value,
                    "requested_by_name": requester or "",
                }
            )
        return {"items": items}

    @staticmethod
    def item_typeahead(
        db: Session,
        organization_id: str,
        query: str,
        limit: int = 8,
    ) -> dict[str, Any]:
        org_id = coerce_uuid(organization_id)
        term = query.strip()
        if not term:
            return {"items": []}
        search_term = f"%{term}%"
        stmt = (
            select(Item)
            .where(
                Item.organization_id == org_id,
                Item.is_active.is_(True),
                Item.track_inventory.is_(True),
            )
            .where(
                or_(
                    Item.item_code.ilike(search_term),
                    Item.item_name.ilike(search_term),
                    Item.barcode.ilike(search_term),
                )
            )
            .order_by(Item.item_code.asc())
            .limit(limit)
        )
        inventory_items = list(db.scalars(stmt).all())
        return {
            "items": [
                {
                    "ref": str(item.item_id),
                    "label": f"{item.item_code} - {item.item_name}",
                    "item_code": item.item_code,
                    "item_name": item.item_name,
                    "base_uom": item.base_uom,
                    "track_lots": bool(item.track_lots),
                    "track_serial_numbers": bool(item.track_serial_numbers),
                }
                for item in inventory_items
            ]
        }

    @staticmethod
    def create_from_form(
        db: Session,
        organization_id: UUID,
        user_id: UUID,
        *,
        material_request_id: str | None,
        item_id: str,
        source_warehouse_id: str,
        destination_warehouse_id: str,
        quantity: str,
        return_date: str,
        reason: str,
        reference: str | None = None,
        remarks: str | None = None,
        lot_number: str | None = None,
        serial_numbers_text: str | None = None,
    ) -> InventoryReturn:
        org_id = coerce_uuid(organization_id)
        item_uuid = coerce_uuid(item_id)
        source_wh_id = coerce_uuid(source_warehouse_id)
        destination_wh_id = coerce_uuid(destination_warehouse_id)
        qty = Decimal(quantity)
        if qty <= 0:
            raise ValueError("Quantity must be greater than zero")
        if source_wh_id == destination_wh_id:
            raise ValueError("Source and destination warehouses must be different")

        parsed_reason = (reason or "").strip()
        if not parsed_reason:
            raise ValueError("Return reason is required")

        item = db.get(Item, item_uuid)
        if not item or item.organization_id != org_id:
            raise ValueError("Inventory item not found")

        source_warehouse = db.get(Warehouse, source_wh_id)
        if not source_warehouse or source_warehouse.organization_id != org_id:
            raise ValueError("Source warehouse not found")

        destination_warehouse = db.get(Warehouse, destination_wh_id)
        if not destination_warehouse or destination_warehouse.organization_id != org_id:
            raise ValueError("Destination warehouse not found")
        if not destination_warehouse.is_receiving:
            raise ValueError("Destination warehouse is not configured for receiving")

        txn_date = datetime.strptime(return_date, "%Y-%m-%d")
        fiscal_period = db.scalars(
            select(FiscalPeriod).where(
                FiscalPeriod.organization_id == org_id,
                FiscalPeriod.start_date <= txn_date.date(),
                FiscalPeriod.end_date >= txn_date.date(),
            )
        ).first()
        if not fiscal_period:
            raise ValueError(
                "No fiscal period found for the return date. Please open the fiscal period first."
            )

        mode = InventoryReturnMode.MANUAL
        material_request = None
        material_request_item = None
        if material_request_id:
            material_request = (
                db.scalars(
                    select(MaterialRequest)
                    .options(joinedload(MaterialRequest.items))
                    .where(
                        MaterialRequest.request_id == coerce_uuid(material_request_id),
                        MaterialRequest.organization_id == org_id,
                    )
                )
                .unique()
                .first()
            )
            if not material_request:
                raise ValueError("Material request not found")
            mode = InventoryReturnMode.MATERIAL_REQUEST
            matching_lines = [
                line
                for line in material_request.items
                if line.inventory_item_id == item_uuid
                and (line.warehouse_id or material_request.default_warehouse_id)
                == source_wh_id
            ]
            if not matching_lines:
                raise ValueError(
                    "Selected item and source warehouse do not match the material request"
                )
            if len(matching_lines) > 1:
                raise ValueError(
                    "Multiple material request lines match this item and source warehouse"
                )
            material_request_item = matching_lines[0]
            returned_qty = db.scalar(
                select(func.sum(InventoryReturn.quantity)).where(
                    InventoryReturn.organization_id == org_id,
                    InventoryReturn.material_request_item_id
                    == material_request_item.item_id,
                )
            ) or Decimal("0")
            allowed_qty = (
                material_request_item.requested_qty or Decimal("0")
            ) - returned_qty
            if qty > allowed_qty:
                raise ValueError(
                    f"Return quantity exceeds remaining issued quantity ({allowed_qty})"
                )

        parsed_serial_numbers = [
            serial.strip()
            for serial in (serial_numbers_text or "").splitlines()
            if serial.strip()
        ]
        if item.track_lots and not (lot_number or "").strip():
            raise ValueError("Lot number is required for lot-tracked items")
        if item.track_serial_numbers and not parsed_serial_numbers:
            raise ValueError(
                "At least one serial number is required for serial-tracked items"
            )

        existing_lot = None
        normalized_lot_number = (lot_number or "").strip() or None
        if normalized_lot_number:
            existing_lot = db.scalars(
                select(InventoryLot)
                .join(
                    InventoryLotBalance,
                    InventoryLotBalance.lot_id == InventoryLot.lot_id,
                )
                .where(
                    InventoryLot.organization_id == org_id,
                    InventoryLot.item_id == item_uuid,
                    InventoryLot.lot_number == normalized_lot_number,
                    InventoryLotBalance.warehouse_id == destination_wh_id,
                )
            ).first()

        source_transaction = None
        if material_request_item is not None:
            if material_request is None:
                raise ValueError(
                    "Material request context is required when a request item is provided."
                )
            source_transaction = db.scalars(
                select(InventoryTransaction)
                .where(
                    InventoryTransaction.organization_id == org_id,
                    InventoryTransaction.source_document_type == "MATERIAL_REQUEST",
                    InventoryTransaction.source_document_id
                    == material_request.request_id,
                    InventoryTransaction.source_document_line_id
                    == material_request_item.item_id,
                    InventoryTransaction.transaction_type.in_(
                        [TransactionType.ISSUE, TransactionType.TRANSFER]
                    ),
                )
                .order_by(InventoryTransaction.transaction_date.desc())
            ).first()

        inventory_return = InventoryReturn(
            organization_id=org_id,
            return_number=InventoryReturnWebService._generate_return_number(),
            return_mode=mode,
            material_request_id=material_request.request_id
            if material_request
            else None,
            material_request_item_id=material_request_item.item_id
            if material_request_item
            else None,
            item_id=item_uuid,
            source_warehouse_id=source_wh_id,
            destination_warehouse_id=destination_wh_id,
            return_date=txn_date.date(),
            quantity=qty,
            uom=item.base_uom,
            reason=parsed_reason,
            reference=(reference or "").strip() or None,
            remarks=(remarks or "").strip() or None,
            lot_id=existing_lot.lot_id if existing_lot else None,
            lot_number=normalized_lot_number,
            serial_numbers=parsed_serial_numbers or None,
            source_transaction_id=source_transaction.transaction_id
            if source_transaction
            else None,
            created_by_id=user_id,
        )
        db.add(inventory_return)
        db.flush()

        txn_input = TransactionInput(
            transaction_type=TransactionType.RETURN,
            transaction_date=txn_date,
            fiscal_period_id=fiscal_period.fiscal_period_id,
            item_id=item_uuid,
            warehouse_id=destination_wh_id,
            quantity=qty,
            unit_cost=item.average_cost or Decimal("0"),
            uom=item.base_uom,
            currency_code=item.currency_code
            or settings.default_presentation_currency_code,
            lot_id=existing_lot.lot_id if existing_lot else None,
            lot_number=normalized_lot_number,
            serial_numbers=parsed_serial_numbers or None,
            source_document_type="INVENTORY_RETURN",
            source_document_id=inventory_return.return_id,
            reference=inventory_return.return_number,
            reason_code="RETURN_TO_STORE",
        )
        posted_transaction = InventoryTransactionService.create_transaction(
            db=db,
            organization_id=org_id,
            input=txn_input,
            created_by_user_id=user_id,
        )
        inventory_return.posted_transaction_id = posted_transaction.transaction_id
        db.add(inventory_return)
        db.flush()
        db.refresh(inventory_return)
        return inventory_return

    @staticmethod
    def detail_context(
        db: Session, organization_id: str, return_id: str
    ) -> dict[str, Any]:
        def _resolve_person_name(person_id: UUID | None) -> str | None:
            if not person_id:
                return None
            return db.scalar(
                select(Person.name_expr()).where(
                    Person.id == person_id,
                    Person.organization_id == org_id,
                )
            )

        org_id = coerce_uuid(organization_id)
        inventory_return = db.scalars(
            select(InventoryReturn)
            .options(
                joinedload(InventoryReturn.item),
                joinedload(InventoryReturn.source_warehouse),
                joinedload(InventoryReturn.destination_warehouse),
                joinedload(InventoryReturn.material_request),
                joinedload(InventoryReturn.posted_transaction),
                joinedload(InventoryReturn.lot),
            )
            .where(
                InventoryReturn.return_id == coerce_uuid(return_id),
                InventoryReturn.organization_id == org_id,
            )
        ).first()
        if not inventory_return:
            return {"inventory_return": None}
        created_by_name = _resolve_person_name(inventory_return.created_by_id)
        updated_by_name = _resolve_person_name(inventory_return.updated_by_id)
        attachments = attachment_service.list_for_entity(
            db,
            organization_id=org_id,
            entity_type="INVENTORY_RETURN",
            entity_id=inventory_return.return_id,
        )
        return {
            "inventory_return": inventory_return,
            "created_by_name": created_by_name,
            "updated_by_name": updated_by_name,
            "attachments": [
                {
                    "attachment_id": str(attachment.attachment_id),
                    "file_name": attachment.file_name,
                    "content_type": attachment.content_type,
                    "description": attachment.description,
                    "category": (
                        attachment.category.value
                        if isinstance(attachment.category, AttachmentCategory)
                        else str(attachment.category)
                    ),
                    "file_size_display": format_file_size(attachment.file_size),
                    "download_url": (
                        f"/inventory/attachments/{attachment.attachment_id}/download"
                    ),
                    "is_image": attachment.content_type.lower().startswith("image/"),
                    "uploaded_at": attachment.uploaded_at,
                }
                for attachment in attachments
            ],
        }
