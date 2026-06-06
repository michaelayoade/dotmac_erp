"""Material-request and purchase-order creation from CRM.

Extracted from the former monolithic dotmac_crm_sync_service.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import date, datetime, timezone

try:
    from datetime import UTC  # type: ignore
except ImportError:  # pragma: no cover
    UTC = timezone.utc

from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

from app.config import settings

if TYPE_CHECKING:
    from app.models.finance.ap.supplier import Supplier  # noqa: F401

from app.models.people.hr.employee import Employee
from app.models.person import Person
from app.models.sync.dotmac_crm_sync import (
    CRMEntityType,
    CRMSyncMapping,
    CRMSyncStatus,
)
from app.schemas.sync.dotmac_crm import (
    CRMMaterialRequestItemRead,
    CRMMaterialRequestPayload,
    CRMMaterialRequestResponse,
    CRMMaterialRequestStatusRead,
    CRMPurchaseOrderPayload,
    CRMPurchaseOrderResponse,
    CRMPurchaseOrderVariationPayload,
)

# CRM → ERP translation policy lives in crm_mappings (pure, side-effect-free).
# Re-imported here so the canonical import sites
# (`from ...dotmac_crm_sync_service import PROJECT_STATUS_MAP`) and the in-class
# references keep resolving against this module's namespace.
from app.services.sync.crm_mappings import (  # noqa: E402
    map_crm_material_request_status,
    map_crm_material_request_type,
)
from app.services.sync.crm_mappings import (  # noqa: E402
    is_variation_id_conflict as _is_variation_id_conflict,
)

from app.services.sync.crm.base import _CRMSyncBase

logger = logging.getLogger(__name__)


class _ProcurementMixin(_CRMSyncBase):
    def create_material_request(
        self,
        org_id: UUID,
        data: CRMMaterialRequestPayload,
        created_by_person_id: UUID | None = None,
    ) -> CRMMaterialRequestResponse:
        """
        Create a material request from CRM.

        Immutable idempotency by omni_id:
        - first send creates the request
        - identical resend returns existing request unchanged
        - changed resend is rejected (CRM must create a new omni_id)

        Raises:
            ValueError: If validation fails.
        """
        from app.models.finance.core_config.numbering_sequence import SequenceType
        from app.models.finance.gl.fiscal_period import FiscalPeriod, PeriodStatus
        from app.models.inventory.item import Item
        from app.models.inventory.material_request import (
            MaterialRequest,
            MaterialRequestItem,
            MaterialRequestStatus,
            MaterialRequestType,
        )
        from app.services.inventory.transaction import InventoryTransactionService
        from app.services.finance.common.numbering import SyncNumberingService

        now = datetime.now(UTC)

        # Resolve cross-references
        project_id = self._resolve_project_id(org_id, data.project_crm_id)
        ticket_id = self._resolve_ticket_id(org_id, data.ticket_crm_id)
        employee_id = self._resolve_employee_id(org_id, data.requested_by_email)
        actor_person_id = created_by_person_id or employee_id

        request_type = self._map_crm_material_request_type(data.request_type)
        if request_type != MaterialRequestType.ISSUE:
            raise ValueError(
                "This sync path only supports request_type=ISSUE for CRM payloads."
            )
        target_status = self._map_crm_material_request_status(data.status)
        if target_status != MaterialRequestStatus.ISSUED:
            raise ValueError("This endpoint accepts only status=issued.")

        # Parse schedule date
        schedule_date_val: date | None = None
        if data.schedule_date:
            try:
                schedule_date_val = date.fromisoformat(data.schedule_date)
            except ValueError as exc:
                raise ValueError(
                    f"Invalid schedule_date format: {data.schedule_date}. Use YYYY-MM-DD."
                ) from exc

        # Resolve items + warehouses
        resolved_items: list[dict[str, Any]] = []
        for seq, item_payload in enumerate(data.items, start=1):
            item_stmt = select(Item).where(
                Item.organization_id == org_id,
                Item.item_code == item_payload.item_code,
            )
            item = self.db.scalar(item_stmt)
            if not item:
                raise ValueError(f"Item not found: {item_payload.item_code}")
            if item.track_lots:
                raise ValueError(
                    f"Lot-tracked item not supported for CRM sync without lot info: "
                    f"{item_payload.item_code}"
                )
            warehouse_id = self._resolve_warehouse_id(
                org_id,
                item_payload.from_warehouse_code,
            )
            if not warehouse_id:
                raise ValueError(
                    f"Warehouse not found for from_warehouse_code: "
                    f"{item_payload.from_warehouse_code}"
                )
            resolved_items.append(
                {
                    "sequence": seq,
                    "item_id": item.item_id,
                    "requested_qty": item_payload.quantity,
                    "uom": item_payload.uom or item.base_uom,
                    "warehouse_id": warehouse_id,
                }
            )

        incoming_fingerprint = self._build_material_request_payload_fingerprint(
            request_type=request_type,
            status=target_status,
            schedule_date=schedule_date_val,
            requested_by_id=employee_id,
            project_id=project_id,
            ticket_id=ticket_id,
            remarks=data.remarks,
            resolved_items=resolved_items,
        )

        existing_stmt = (
            select(MaterialRequest)
            .options(joinedload(MaterialRequest.items))
            .where(
                MaterialRequest.organization_id == org_id,
                MaterialRequest.crm_id == data.omni_id,
            )
        )
        mr = self.db.scalar(existing_stmt)
        if mr:
            existing_fingerprint = self._build_material_request_existing_fingerprint(mr)
            if incoming_fingerprint != existing_fingerprint:
                from fastapi import HTTPException

                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Material request already exists and cannot be modified; "
                        "create a new CRM material request."
                    ),
                )
            logger.info(
                "CRM material request duplicate accepted unchanged (omni_id=%s, request_number=%s)",
                data.omni_id,
                mr.request_number,
            )
            return CRMMaterialRequestResponse(
                request_id=mr.request_id,
                request_number=mr.request_number,
                status=mr.status.value,
                omni_id=data.omni_id,
            )

        fiscal_period = self.db.scalar(
            select(FiscalPeriod).where(
                FiscalPeriod.organization_id == org_id,
                FiscalPeriod.start_date <= now.date(),
                FiscalPeriod.end_date >= now.date(),
                FiscalPeriod.status.in_(PeriodStatus.accepts_postings()),
            )
        )
        if not fiscal_period:
            raise ValueError(
                "No open fiscal period found for today. "
                "Please ensure an OPEN/REOPENED fiscal period exists."
            )

        required_by_bucket: dict[tuple[UUID, UUID], Decimal] = {}
        for resolved in resolved_items:
            bucket = (resolved["item_id"], resolved["warehouse_id"])
            required_by_bucket[bucket] = (
                required_by_bucket.get(
                    bucket,
                    Decimal("0"),
                )
                + resolved["requested_qty"]
            )

        for (item_id, warehouse_id), required_qty in required_by_bucket.items():
            available_qty = InventoryTransactionService.get_current_balance(
                self.db,
                org_id,
                item_id,
                warehouse_id,
            )
            if available_qty < required_qty:
                raise ValueError(
                    "Insufficient inventory for request: "
                    f"item_id={item_id}, warehouse_id={warehouse_id}, "
                    f"available={available_qty}, required={required_qty}"
                )

        numbering = SyncNumberingService(self.db)
        request_number = numbering.generate_next_number(
            org_id,
            SequenceType.MATERIAL_REQUEST,
        )
        mr = MaterialRequest(
            organization_id=org_id,
            request_number=request_number,
            request_type=request_type,
            status=MaterialRequestStatus.DRAFT,
            schedule_date=schedule_date_val,
            requested_by_id=employee_id,
            project_id=project_id,
            ticket_id=ticket_id,
            remarks=data.remarks,
            crm_id=data.omni_id,
            created_by_id=actor_person_id,
        )
        self.db.add(mr)
        self.db.flush()

        for resolved in resolved_items:
            mr_line = MaterialRequestItem(
                organization_id=org_id,
                request_id=mr.request_id,
                inventory_item_id=resolved["item_id"],
                warehouse_id=resolved["warehouse_id"],
                requested_qty=resolved["requested_qty"],
                uom=resolved["uom"],
                sequence=resolved["sequence"],
                project_id=project_id,
                ticket_id=ticket_id,
                schedule_date=schedule_date_val,
            )
            self.db.add(mr_line)
        self.db.flush()

        current_line_snapshots = self._snapshot_material_request_lines(mr.items)
        for line in current_line_snapshots:
            self._post_crm_issue_transaction(
                org_id=org_id,
                request=mr,
                line=line,
                fiscal_period_id=fiscal_period.fiscal_period_id,
                transaction_date=now,
                created_by_user_id=actor_person_id,
            )

        mr.status = target_status
        self.db.flush()

        logger.info(
            "CRM material request %s created (omni_id=%s, status=%s, items=%d)",
            mr.request_number,
            data.omni_id,
            mr.status.value,
            len(current_line_snapshots),
        )
        return CRMMaterialRequestResponse(
            request_id=mr.request_id,
            request_number=mr.request_number,
            status=mr.status.value,
            omni_id=data.omni_id,
        )

    def _build_material_request_payload_fingerprint(
        self,
        *,
        request_type,
        status,
        schedule_date: date | None,
        requested_by_id: UUID | None,
        project_id: UUID | None,
        ticket_id: UUID | None,
        remarks: str | None,
        resolved_items: list[dict[str, Any]],
    ) -> str:
        """Build deterministic fingerprint from inbound CRM payload (effective values)."""
        payload = {
            "request_type": request_type.value,
            "status": status.value,
            "schedule_date": schedule_date.isoformat() if schedule_date else None,
            "requested_by_id": str(requested_by_id) if requested_by_id else None,
            "project_id": str(project_id) if project_id else None,
            "ticket_id": str(ticket_id) if ticket_id else None,
            "remarks": remarks or "",
            "items": [
                {
                    "sequence": item["sequence"],
                    "item_id": str(item["item_id"]),
                    "warehouse_id": str(item["warehouse_id"])
                    if item["warehouse_id"]
                    else None,
                    "requested_qty": str(item["requested_qty"]),
                    "uom": item["uom"] or "",
                }
                for item in sorted(resolved_items, key=lambda i: i["sequence"])
            ],
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def _build_material_request_existing_fingerprint(self, request) -> str:
        """Build deterministic fingerprint from persisted material request + lines."""
        payload = {
            "request_type": request.request_type.value,
            "status": request.status.value,
            "schedule_date": request.schedule_date.isoformat()
            if request.schedule_date
            else None,
            "requested_by_id": str(request.requested_by_id)
            if request.requested_by_id
            else None,
            "project_id": str(request.project_id) if request.project_id else None,
            "ticket_id": str(request.ticket_id) if request.ticket_id else None,
            "remarks": request.remarks or "",
            "items": [
                {
                    "sequence": line.sequence,
                    "item_id": str(line.inventory_item_id),
                    "warehouse_id": str(line.warehouse_id)
                    if line.warehouse_id
                    else None,
                    "requested_qty": str(line.requested_qty),
                    "uom": line.uom or "",
                }
                for line in sorted(request.items, key=lambda i: i.sequence)
            ],
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def _map_crm_material_request_type(self, request_type: str):
        """Map CRM request type to local MaterialRequestType (via crm_mappings)."""
        return map_crm_material_request_type(request_type)

    def _map_crm_material_request_status(self, status: str):
        """Map CRM status string to local MaterialRequestStatus (via crm_mappings)."""
        return map_crm_material_request_status(status)

    def _resolve_warehouse_id(
        self,
        org_id: UUID,
        warehouse_code: str | None,
    ) -> UUID | None:
        """Resolve warehouse UUID from external warehouse code."""
        from app.models.inventory.warehouse import Warehouse

        code = (warehouse_code or "").strip()
        if not code:
            return None
        stmt = select(Warehouse.warehouse_id).where(
            Warehouse.organization_id == org_id,
            func.lower(Warehouse.warehouse_code) == code.lower(),
        )
        return self.db.scalar(stmt)

    def _snapshot_material_request_lines(
        self,
        lines: list,
    ) -> list[dict[str, Any]]:
        """Capture MR line values for issue posting."""
        snapshots: list[dict[str, Any]] = []
        for line in sorted(lines, key=lambda l: l.sequence):
            snapshots.append(
                {
                    "line_id": line.item_id,
                    "sequence": line.sequence,
                    "item_id": line.inventory_item_id,
                    "warehouse_id": line.warehouse_id,
                    "requested_qty": line.requested_qty,
                    "uom": line.uom,
                }
            )
        return snapshots

    def _post_crm_issue_transaction(
        self,
        *,
        org_id: UUID,
        request,
        line: dict[str, Any],
        fiscal_period_id: UUID,
        transaction_date: datetime,
        created_by_user_id: UUID | None,
    ) -> None:
        """Post a single ISSUE transaction for a CRM-synced MR line."""
        from app.models.inventory.item import Item
        from app.models.inventory.inventory_transaction import TransactionType
        from app.services.inventory.transaction import (
            InventoryTransactionService,
            TransactionInput,
        )

        warehouse_id = line.get("warehouse_id")
        if not warehouse_id:
            raise ValueError(
                f"Missing warehouse for material request line sequence={line.get('sequence')}"
            )
        item = self.db.get(Item, line["item_id"])
        if not item:
            raise ValueError(f"Inventory item not found for line: {line['item_id']}")
        user_id = created_by_user_id or request.created_by_id
        if not user_id:
            raise ValueError(
                "Cannot post inventory issue without a responsible user/person id."
            )
        txn_input = TransactionInput(
            transaction_type=TransactionType.ISSUE,
            transaction_date=transaction_date,
            fiscal_period_id=fiscal_period_id,
            item_id=line["item_id"],
            warehouse_id=warehouse_id,
            quantity=line["requested_qty"],
            unit_cost=item.average_cost or Decimal("0"),
            uom=line.get("uom") or item.base_uom or "",
            currency_code=item.currency_code
            or settings.default_presentation_currency_code,
            source_document_type="CRM_MATERIAL_REQUEST",
            source_document_id=request.request_id,
            source_document_line_id=line.get("line_id"),
            reference=request.request_number,
            reason_code="CRM_SYNC_ISSUE",
        )
        InventoryTransactionService.create_issue(
            self.db,
            org_id,
            txn_input,
            user_id,
            auto_commit=False,
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
        items_map: dict[UUID, tuple[str, str]] = {}
        if item_ids:
            items_stmt = select(Item.item_id, Item.item_code, Item.item_name).where(
                Item.item_id.in_(item_ids)
            )
            items_map = {
                row[0]: (row[1], row[2]) for row in self.db.execute(items_stmt).all()
            }

        return CRMMaterialRequestStatusRead(
            request_id=mr.request_id,
            request_number=mr.request_number,
            status=mr.status.value,
            request_type=mr.request_type.value,
            items=[
                CRMMaterialRequestItemRead(
                    item_code=items_map.get(line.inventory_item_id, ("", ""))[0],
                    item_name=items_map.get(line.inventory_item_id, ("", ""))[1],
                    requested_qty=line.requested_qty,
                    ordered_qty=line.ordered_qty,
                    uom=line.uom,
                )
                for line in mr.items
            ],
            created_at=mr.created_at,
        )

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
        """Resolve a supplier by erpnext_id, supplier_code, or supplier name.

        Raises:
            ValueError: If supplier not found.
        """
        from app.models.finance.ap.supplier import Supplier

        normalized_erp_id = (vendor_erp_id or "").strip() or None
        normalized_vendor_code = (vendor_code or "").strip() or None

        logger.info(
            "CRM supplier resolution org=%s erp_id=%r vendor_code=%r normalized_erp_id=%r normalized_vendor_code=%r",
            org_id,
            vendor_erp_id,
            vendor_code,
            normalized_erp_id,
            normalized_vendor_code,
        )

        if normalized_erp_id:
            stmt = select(Supplier).where(
                Supplier.organization_id == org_id,
                Supplier.erpnext_id == normalized_erp_id,
                Supplier.is_active.is_(True),
            )
            supplier = self.db.scalar(stmt)
            if supplier:
                return supplier

        if normalized_vendor_code:
            stmt = select(Supplier).where(
                Supplier.organization_id == org_id,
                Supplier.supplier_code == normalized_vendor_code,
                Supplier.is_active.is_(True),
            )
            supplier = self.db.scalar(stmt)
            if supplier:
                return supplier

            # CRM has historically sent vendor display names in vendor_code.
            stmt = select(Supplier).where(
                Supplier.organization_id == org_id,
                Supplier.is_active.is_(True),
                or_(
                    func.lower(func.trim(Supplier.legal_name))
                    == normalized_vendor_code.lower(),
                    func.lower(func.trim(Supplier.trading_name))
                    == normalized_vendor_code.lower(),
                ),
            )
            supplier = self.db.scalar(stmt)
            if supplier:
                return supplier

        logger.warning(
            "CRM supplier resolution failed org=%s erp_id=%r vendor_code=%r normalized_erp_id=%r normalized_vendor_code=%r",
            org_id,
            vendor_erp_id,
            vendor_code,
            normalized_erp_id,
            normalized_vendor_code,
        )
        raise ValueError(
            f"Supplier not found: erp_id={normalized_erp_id}, code={normalized_vendor_code}"
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

    def create_purchase_order_variation(
        self,
        org_id: UUID,
        data: CRMPurchaseOrderVariationPayload,
        created_by_person_id: UUID,
    ) -> CRMPurchaseOrderResponse:
        """
        Create a PO amendment from a CRM variation approval.

        Strategy:
        1. Idempotency — if a PO with this variation_id exists, return it.
        2. Find the baseline PO via the original work-order sync mapping.
        3. Mark the baseline as SUPERSEDED (preserving audit trail).
        4. Create a new amendment PO linked to the baseline.
        5. Update the sync mapping to point to the new PO.

        SoD is maintained: the amendment PO starts in DRAFT and must go
        through the same approval workflow as the original.

        Raises:
            ValueError: If baseline PO not found, supplier resolution fails,
                        or the baseline is already CANCELLED/CLOSED.
        """
        from app.models.finance.ap.purchase_order import POStatus, PurchaseOrder
        from app.services.finance.ap.purchase_order import (
            POLineInput,
            PurchaseOrderInput,
            PurchaseOrderService,
        )

        # 1. Idempotency: check if we already created a PO for this variation_id
        existing_stmt = select(PurchaseOrder).where(
            PurchaseOrder.organization_id == org_id,
            PurchaseOrder.variation_id == data.variation_id,
        )
        existing_variation_po = self.db.scalar(existing_stmt)
        if existing_variation_po:
            logger.info(
                "PO variation already exists for variation_id=%s, returning",
                data.variation_id,
            )
            return CRMPurchaseOrderResponse(
                purchase_order_id=existing_variation_po.po_number,
                po_id=existing_variation_po.po_id,
                status=existing_variation_po.status.value.lower(),
                omni_work_order_id=data.omni_work_order_id,
                is_amendment=True,
                variation_id=data.variation_id,
                amendment_version=existing_variation_po.amendment_version,
                superseded_po_id=existing_variation_po.original_po_id,
            )

        # 2. Find the baseline PO via sync mapping
        baseline_mapping = self._get_mapping(
            org_id, CRMEntityType.PURCHASE_ORDER, data.omni_work_order_id
        )
        if not baseline_mapping:
            raise ValueError(
                f"No baseline PO found for omni_work_order_id={data.omni_work_order_id}"
            )

        baseline_po = self.db.get(PurchaseOrder, baseline_mapping.local_entity_id)
        if not baseline_po:
            raise ValueError(
                f"Baseline PO entity missing for mapping={baseline_mapping.mapping_id}"
            )

        # Prevent amendments on terminal or already-superseded states
        blocked = {POStatus.CANCELLED, POStatus.CLOSED, POStatus.SUPERSEDED}
        if baseline_po.status in blocked:
            raise ValueError(
                f"Cannot amend PO {baseline_po.po_number} in "
                f"{baseline_po.status.value} status"
            )

        # Accounting safety: block supersede when PO has received or
        # invoiced quantities — orphaning those financial records is unsafe.
        # A future residual-safe change-order flow can relax this guard.
        #
        # Belt-and-suspenders: check both header-level summary amounts AND
        # individual line quantities.  The header fields are updated by
        # update_received_amount(), but a race or bug could leave them stale.
        from app.models.finance.ap.purchase_order_line import PurchaseOrderLine

        has_header_activity = (
            baseline_po.amount_received > 0 or baseline_po.amount_invoiced > 0
        )
        has_line_activity = self.db.scalar(
            select(PurchaseOrderLine.line_id)
            .where(
                PurchaseOrderLine.po_id == baseline_po.po_id,
                or_(
                    PurchaseOrderLine.quantity_received > 0,
                    PurchaseOrderLine.quantity_invoiced > 0,
                ),
            )
            .limit(1)
        )
        if has_header_activity or has_line_activity:
            raise ValueError(
                f"Cannot supersede PO {baseline_po.po_number}: "
                f"amount_received={baseline_po.amount_received}, "
                f"amount_invoiced={baseline_po.amount_invoiced}. "
                f"POs with received or invoiced quantities cannot be amended "
                f"until residual-safe change-order logic is implemented."
            )

        # 3. Resolve dependencies (same as create flow)
        supplier = self._resolve_supplier(org_id, data.vendor_erp_id, data.vendor_code)
        project_id = self._resolve_project_id(org_id, data.omni_project_id)
        approver_person_id = self._resolve_person_id_by_email(
            org_id, data.approved_by_email
        )
        creator_id = approver_person_id or created_by_person_id

        # 4. Build PO input with amendment metadata
        po_lines: list[POLineInput] = []
        line_subtotal = sum(i.amount for i in data.items) or Decimal("1")
        tax_distributed = Decimal("0")
        for idx, item in enumerate(data.items):
            if idx == len(data.items) - 1:
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

        # Determine version: baseline version + 1, or explicit from payload
        amendment_version = data.variation_version

        correlation_id = f"crm-wo:{data.omni_work_order_id}:v{amendment_version}"

        po_input = PurchaseOrderInput(
            supplier_id=supplier.supplier_id,
            po_date=date.today(),
            currency_code=data.currency,
            lines=po_lines,
            correlation_id=correlation_id,
            terms_and_conditions=data.title,
        )

        # 5. Create the amendment PO
        new_po = PurchaseOrderService.create_po(self.db, org_id, po_input, creator_id)

        # Set amendment-specific fields
        new_po.is_amendment = True
        new_po.original_po_id = baseline_po.po_id
        new_po.amendment_version = amendment_version
        new_po.amendment_reason = data.amendment_reason
        new_po.variation_id = data.variation_id

        # 6. Supersede the baseline PO (preserving its data for audit)
        baseline_po.status = POStatus.SUPERSEDED
        self.db.flush()

        # 7. Update sync mapping to point to the new PO
        baseline_mapping.local_entity_id = new_po.po_id
        baseline_mapping.display_code = new_po.po_number
        baseline_mapping.display_name = data.title[:255]
        baseline_mapping.crm_data = {
            **(baseline_mapping.crm_data or {}),
            "variation_id": data.variation_id,
            "variation_version": amendment_version,
            "amendment_reason": data.amendment_reason,
            "superseded_po_id": str(baseline_po.po_id),
            "total": str(data.total),
        }
        baseline_mapping.synced_at = datetime.now(UTC)

        try:
            self.db.commit()
        except IntegrityError as exc:
            if not _is_variation_id_conflict(exc):
                self.db.rollback()
                raise

            # Race condition: another request already created this variation.
            # Roll back and return the existing PO (idempotent).
            self.db.rollback()
            logger.warning(
                "Concurrent variation creation for variation_id=%s, "
                "returning existing PO",
                data.variation_id,
            )
            existing = self.db.scalar(
                select(PurchaseOrder).where(
                    PurchaseOrder.organization_id == org_id,
                    PurchaseOrder.variation_id == data.variation_id,
                )
            )
            if existing:
                return CRMPurchaseOrderResponse(
                    purchase_order_id=existing.po_number,
                    po_id=existing.po_id,
                    status=existing.status.value.lower(),
                    omni_work_order_id=data.omni_work_order_id,
                    is_amendment=True,
                    variation_id=data.variation_id,
                    amendment_version=existing.amendment_version,
                    superseded_po_id=existing.original_po_id,
                )
            raise  # Re-raise if existing not found (unexpected)

        logger.info(
            "Created PO amendment %s (v%d) for variation_id=%s, superseding %s",
            new_po.po_number,
            amendment_version,
            data.variation_id,
            baseline_po.po_number,
        )

        return CRMPurchaseOrderResponse(
            purchase_order_id=new_po.po_number,
            po_id=new_po.po_id,
            status=new_po.status.value.lower(),
            omni_work_order_id=data.omni_work_order_id,
            is_amendment=True,
            variation_id=data.variation_id,
            amendment_version=amendment_version,
            superseded_po_id=baseline_po.po_id,
        )
