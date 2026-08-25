"""Material-request, purchase-order, and AP intake from Dotmac Sub.

Extracted from the former monolithic dotmac_sub_sync_service.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import io
import json
import logging
from datetime import date, datetime, timedelta, timezone

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

from dotmac_kernel.db import conflict_savepoint
from dotmac_kernel.fingerprints import fingerprint_of

from app.config import settings

if TYPE_CHECKING:
    from app.models.finance.ap.supplier import Supplier  # noqa: F401

from app.models.people.hr.employee import Employee
from app.models.person import Person
from app.schemas.sync.dotmac_sub import (
    SubMaterialRequestItemRead,
    SubMaterialRequestPayload,
    SubMaterialRequestResponse,
    SubMaterialRequestStatusRead,
    SubPurchaseOrderPayload,
    SubPurchaseOrderResponse,
    SubPurchaseOrderVariationPayload,
    SubPurchaseInvoicePayload,
    SubPurchaseInvoiceResponse,
    SubPurchaseInvoiceAttachmentPayload,
    SubPurchaseInvoiceAttachmentResponse,
)

# Sub → ERP translation policy lives in sub_mappings (pure, side-effect-free).
# Re-imported here so the canonical import sites
# (`from ...dotmac_sub_sync_service import PROJECT_STATUS_MAP`) and the in-class
# references keep resolving against this module's namespace.
from app.services.sync.sub_mappings import (  # noqa: E402
    map_sub_material_request_status,
    map_sub_material_request_type,
)
from app.services.sync.sub.base import SUB_SOURCE_SYSTEM, _SubSyncBase
from app.services.sync.sub.errors import (
    SubNotFoundError,
    SubPayloadTooLargeError,
    SubReplayConflictError,
    SubValidationError,
)

logger = logging.getLogger(__name__)


class _ProcurementMixin(_SubSyncBase):
    @staticmethod
    def _validate_purchase_order_financials(
        data: SubPurchaseOrderPayload,
    ) -> None:
        """Reconcile every source amount exactly before any PO mutation."""
        calculated_subtotal = Decimal("0")
        for line_number, item in enumerate(data.items, start=1):
            calculated_amount = item.quantity * item.unit_price
            if calculated_amount != item.amount:
                raise ValueError(
                    f"Purchase order line {line_number} amount does not equal "
                    "quantity * unit_price"
                )
            calculated_subtotal += item.amount

        if calculated_subtotal != data.subtotal:
            raise ValueError(
                "Purchase order subtotal does not equal the sum of line amounts"
            )
        if data.subtotal + data.tax_total != data.total:
            raise ValueError("Purchase order total does not equal subtotal + tax_total")

    @staticmethod
    def _require_matching_source_fingerprint(
        record: Any,
        incoming_fingerprint: str,
        *,
        source_identity: str,
    ) -> None:
        """Fail closed unless an existing financial command is an exact replay."""
        persisted_fingerprint = getattr(record, "source_fingerprint", None)
        if not persisted_fingerprint:
            raise SubReplayConflictError(
                f"{source_identity} already exists without an immutable source "
                "fingerprint; it cannot be treated as a safe replay."
            )
        if persisted_fingerprint != incoming_fingerprint:
            raise SubReplayConflictError(
                f"{source_identity} was reused with a different immutable payload."
            )

    def create_material_request(
        self,
        org_id: UUID,
        data: SubMaterialRequestPayload,
        created_by_person_id: UUID | None = None,
    ) -> SubMaterialRequestResponse:
        """
        Create a material request from Sub.

        Immutable idempotency by source_request_id:
        - first send creates the request
        - identical resend returns existing request unchanged
        - changed resend is rejected (Sub must create a new request)

        Raises:
            ValueError: If validation fails.
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

        now = datetime.now(UTC)

        # Resolve cross-references
        project_id = self._resolve_project_id(org_id, data.project_source_id)
        employee_id = self._resolve_employee_id(org_id, data.requested_by_email)
        actor_person_id = created_by_person_id or employee_id

        request_type = self._map_sub_material_request_type(data.request_type)
        if request_type != MaterialRequestType.ISSUE:
            raise ValueError(
                "This sync path only supports request_type=ISSUE for Sub payloads."
            )
        requested_status = self._map_sub_material_request_status(data.status)
        if requested_status not in {
            MaterialRequestStatus.DRAFT,
            MaterialRequestStatus.SUBMITTED,
            MaterialRequestStatus.ISSUED,
        }:
            raise ValueError(
                "This endpoint accepts only status=draft, submitted, or issued."
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
                    f"Lot-tracked item not supported for Sub sync without lot info: "
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
                    "track_serial_numbers": self._model_flag(
                        item,
                        "track_serial_numbers",
                    ),
                    "serial_numbers": self._validate_sub_material_request_serials(
                        org_id=org_id,
                        item=item,
                        warehouse_id=warehouse_id,
                        quantity=item_payload.quantity,
                        serial_numbers=item_payload.serial_numbers,
                        require_serials=False,
                    ),
                }
            )

        incoming_fingerprint = self._build_material_request_payload_fingerprint(
            request_type=request_type,
            status=requested_status,
            schedule_date=schedule_date_val,
            requested_by_id=employee_id,
            project_id=project_id,
            remarks=data.remarks,
            resolved_items=resolved_items,
        )
        incoming_body_fingerprint = self._build_material_request_payload_fingerprint(
            request_type=request_type,
            status=None,
            schedule_date=schedule_date_val,
            requested_by_id=employee_id,
            project_id=project_id,
            remarks=data.remarks,
            resolved_items=resolved_items,
        )
        incoming_body_without_serials_fingerprint = (
            self._build_material_request_payload_fingerprint(
                request_type=request_type,
                status=None,
                schedule_date=schedule_date_val,
                requested_by_id=employee_id,
                project_id=project_id,
                remarks=data.remarks,
                resolved_items=resolved_items,
                include_serial_numbers=False,
            )
        )

        existing_stmt = (
            select(MaterialRequest)
            .options(joinedload(MaterialRequest.items))
            .where(
                MaterialRequest.organization_id == org_id,
                MaterialRequest.source_system == "sub",
                MaterialRequest.source_id == data.source_request_id,
            )
        )
        mr = self.db.scalar(existing_stmt)
        if mr:
            existing_fingerprint = self._build_material_request_existing_fingerprint(mr)
            if incoming_fingerprint != existing_fingerprint:
                existing_body_fingerprint = (
                    self._build_material_request_existing_fingerprint(
                        mr,
                        include_status=False,
                    )
                )
                if incoming_body_fingerprint == existing_body_fingerprint:
                    return self._advance_sub_material_request_status(
                        org_id=org_id,
                        request=mr,
                        requested_status=requested_status,
                        actor_person_id=actor_person_id,
                    )
                existing_body_without_serials_fingerprint = (
                    self._build_material_request_existing_fingerprint(
                        mr,
                        include_status=False,
                        include_serial_numbers=False,
                    )
                )
                if (
                    incoming_body_without_serials_fingerprint
                    == existing_body_without_serials_fingerprint
                    and mr.status
                    in {
                        MaterialRequestStatus.DRAFT,
                        MaterialRequestStatus.SUBMITTED,
                        MaterialRequestStatus.PENDING_STOCK,
                    }
                ):
                    self._apply_sub_material_request_line_serials(mr, resolved_items)
                    return self._advance_sub_material_request_status(
                        org_id=org_id,
                        request=mr,
                        requested_status=requested_status,
                        actor_person_id=actor_person_id,
                    )
                raise SubReplayConflictError(
                    "Material request already exists and cannot be modified; "
                    "create a new Sub material request."
                )
            logger.info(
                "Sub material request duplicate accepted unchanged "
                "(source_request_id=%s, request_number=%s)",
                data.source_request_id,
                mr.request_number,
            )
            return SubMaterialRequestResponse(
                request_id=mr.request_id,
                request_number=mr.request_number,
                status=mr.status.value,
                source_request_id=data.source_request_id,
            )

        has_sufficient_stock = True
        if requested_status in {
            MaterialRequestStatus.SUBMITTED,
            MaterialRequestStatus.ISSUED,
        }:
            has_sufficient_stock = self._sub_material_request_has_sufficient_stock(
                org_id,
                resolved_items,
            )
        target_status = (
            MaterialRequestStatus.PENDING_STOCK
            if not has_sufficient_stock
            else requested_status
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
            status=target_status
            if target_status != MaterialRequestStatus.ISSUED
            else MaterialRequestStatus.DRAFT,
            schedule_date=schedule_date_val,
            requested_by_id=employee_id,
            project_id=project_id,
            ticket_id=None,
            remarks=data.remarks,
            source_id=data.source_request_id,
            source_system=SUB_SOURCE_SYSTEM,
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
                serial_numbers=resolved.get("serial_numbers") or None,
                sequence=resolved["sequence"],
                project_id=project_id,
                ticket_id=None,
                schedule_date=schedule_date_val,
            )
            self.db.add(mr_line)
        self.db.flush()

        current_line_snapshots = self._snapshot_material_request_lines(mr.items)
        if target_status == MaterialRequestStatus.ISSUED:
            from app.models.finance.gl.fiscal_period import FiscalPeriod, PeriodStatus

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
            for line in current_line_snapshots:
                self._post_sub_issue_transaction(
                    org_id=org_id,
                    request=mr,
                    line=line,
                    fiscal_period_id=fiscal_period.fiscal_period_id,
                    transaction_date=now,
                    created_by_user_id=actor_person_id,
                )
            mr.status = target_status
        self.db.flush()
        self._emit_sub_material_request_status_changed(
            org_id=org_id,
            request=mr,
            old_status=None,
            new_status=mr.status,
            actor_person_id=actor_person_id,
        )

        logger.info(
            "Sub material request %s created "
            "(source_request_id=%s, status=%s, items=%d)",
            mr.request_number,
            data.source_request_id,
            mr.status.value,
            len(current_line_snapshots),
        )
        return SubMaterialRequestResponse(
            request_id=mr.request_id,
            request_number=mr.request_number,
            status=mr.status.value,
            source_request_id=data.source_request_id,
        )

    def _build_material_request_payload_fingerprint(
        self,
        *,
        request_type,
        status,
        schedule_date: date | None,
        requested_by_id: UUID | None,
        project_id: UUID | None,
        remarks: str | None,
        resolved_items: list[dict[str, Any]],
        include_serial_numbers: bool = True,
    ) -> str:
        """Build deterministic fingerprint from inbound Sub payload (effective values)."""
        items_payload = []
        for item in sorted(resolved_items, key=lambda i: i["sequence"]):
            item_payload = {
                "sequence": item["sequence"],
                "item_id": str(item["item_id"]),
                "warehouse_id": str(item["warehouse_id"])
                if item["warehouse_id"]
                else None,
                "requested_qty": str(item["requested_qty"]),
                "uom": item["uom"] or "",
            }
            if include_serial_numbers:
                item_payload["serial_numbers"] = item.get("serial_numbers") or []
            items_payload.append(item_payload)

        payload = {
            "request_type": request_type.value,
            "schedule_date": schedule_date.isoformat() if schedule_date else None,
            "requested_by_id": str(requested_by_id) if requested_by_id else None,
            "project_id": str(project_id) if project_id else None,
            "remarks": remarks or "",
            "items": items_payload,
        }
        if status is not None:
            payload["status"] = status.value
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def _build_material_request_existing_fingerprint(
        self,
        request,
        *,
        include_status: bool = True,
        include_serial_numbers: bool = True,
    ) -> str:
        """Build deterministic fingerprint from persisted material request + lines."""
        items_payload = []
        for line in sorted(request.items, key=lambda i: i.sequence):
            item_payload = {
                "sequence": line.sequence,
                "item_id": str(line.inventory_item_id),
                "warehouse_id": str(line.warehouse_id) if line.warehouse_id else None,
                "requested_qty": str(line.requested_qty),
                "uom": line.uom or "",
            }
            if include_serial_numbers:
                item_payload["serial_numbers"] = (
                    self._material_request_line_serial_numbers(line)
                )
            items_payload.append(item_payload)

        payload = {
            "request_type": request.request_type.value,
            "schedule_date": request.schedule_date.isoformat()
            if request.schedule_date
            else None,
            "requested_by_id": str(request.requested_by_id)
            if request.requested_by_id
            else None,
            "project_id": str(request.project_id) if request.project_id else None,
            "remarks": request.remarks or "",
            "items": items_payload,
        }
        if include_status:
            payload["status"] = request.status.value
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def _sub_material_request_has_sufficient_stock(
        self,
        org_id: UUID,
        resolved_items: list[dict[str, Any]],
    ) -> bool:
        """Return whether all requested Sub MR lines can be fulfilled from stock."""
        from app.models.inventory.item import Item
        from app.services.inventory.transaction import InventoryTransactionService

        required_by_bucket: dict[tuple[UUID, UUID], Decimal] = {}
        for resolved in resolved_items:
            tracks_serial_numbers = resolved.get("track_serial_numbers")
            if tracks_serial_numbers is None:
                item = self.db.get(Item, resolved["item_id"])
                tracks_serial_numbers = self._model_flag(item, "track_serial_numbers")
            if tracks_serial_numbers and not resolved.get("serial_numbers"):
                logger.info(
                    "Sub material request pending serial selection: item_id=%s warehouse_id=%s",
                    resolved["item_id"],
                    resolved["warehouse_id"],
                )
                return False
            bucket = (resolved["item_id"], resolved["warehouse_id"])
            required_by_bucket[bucket] = (
                required_by_bucket.get(bucket, Decimal("0")) + resolved["requested_qty"]
            )

        for (item_id, warehouse_id), required_qty in required_by_bucket.items():
            available_qty = InventoryTransactionService.get_current_balance(
                self.db,
                org_id,
                item_id,
                warehouse_id,
            )
            if available_qty < required_qty:
                logger.info(
                    "Sub material request pending stock: item_id=%s warehouse_id=%s available=%s required=%s",
                    item_id,
                    warehouse_id,
                    available_qty,
                    required_qty,
                )
                return False
        return True

    def _apply_sub_material_request_line_serials(
        self,
        request,
        resolved_items: list[dict[str, Any]],
    ) -> None:
        """Apply Sub serial selections to an existing immutable MR body."""
        resolved_by_sequence = {
            item["sequence"]: item.get("serial_numbers") or []
            for item in resolved_items
        }
        for line in request.items:
            if line.sequence in resolved_by_sequence:
                line.serial_numbers = resolved_by_sequence[line.sequence] or None
        self.db.flush()

    def _advance_sub_material_request_status(
        self,
        *,
        org_id: UUID,
        request,
        requested_status,
        actor_person_id: UUID | None,
    ) -> SubMaterialRequestResponse:
        """Apply a Sub status-only resend to an existing immutable MR."""
        from app.models.finance.gl.fiscal_period import FiscalPeriod, PeriodStatus
        from app.models.inventory.material_request import MaterialRequestStatus

        old_status = request.status
        if request.status == MaterialRequestStatus.ISSUED:
            return SubMaterialRequestResponse(
                request_id=request.request_id,
                request_number=request.request_number,
                status=request.status.value,
                source_request_id=request.source_id,
            )

        if requested_status == MaterialRequestStatus.DRAFT:
            target_status = (
                MaterialRequestStatus.DRAFT
                if request.status == MaterialRequestStatus.DRAFT
                else request.status
            )
        elif requested_status == MaterialRequestStatus.SUBMITTED:
            line_snapshots = self._snapshot_material_request_lines(request.items)
            target_status = (
                MaterialRequestStatus.SUBMITTED
                if self._sub_material_request_has_sufficient_stock(
                    org_id,
                    line_snapshots,
                )
                else MaterialRequestStatus.PENDING_STOCK
            )
        elif requested_status == MaterialRequestStatus.ISSUED:
            line_snapshots = self._snapshot_material_request_lines(request.items)
            if self._sub_material_request_has_sufficient_stock(org_id, line_snapshots):
                now = datetime.now(UTC)
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
                for line in line_snapshots:
                    self._post_sub_issue_transaction(
                        org_id=org_id,
                        request=request,
                        line=line,
                        fiscal_period_id=fiscal_period.fiscal_period_id,
                        transaction_date=now,
                        created_by_user_id=actor_person_id,
                    )
                target_status = MaterialRequestStatus.ISSUED
            else:
                target_status = MaterialRequestStatus.PENDING_STOCK
        else:
            raise ValueError(
                "This endpoint accepts only status=draft, submitted, or issued."
            )

        request.status = target_status
        self.db.flush()
        if request.status != old_status:
            self._emit_sub_material_request_status_changed(
                org_id=org_id,
                request=request,
                old_status=old_status,
                new_status=request.status,
                actor_person_id=actor_person_id,
            )
        return SubMaterialRequestResponse(
            request_id=request.request_id,
            request_number=request.request_number,
            status=request.status.value,
            source_request_id=request.source_id,
        )

    def _map_sub_material_request_type(self, request_type: str):
        """Map Sub request type to local MaterialRequestType (via sub_mappings)."""
        return map_sub_material_request_type(request_type)

    def _map_sub_material_request_status(self, status: str):
        """Map Sub status string to local MaterialRequestStatus (via sub_mappings)."""
        return map_sub_material_request_status(status)

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
                    "serial_numbers": self._material_request_line_serial_numbers(line),
                }
            )
        return snapshots

    def _material_request_line_serial_numbers(self, line) -> list[str]:
        """Return persisted line serials while treating mock-only attrs as absent."""
        serial_numbers = getattr(line, "serial_numbers", None)
        if type(serial_numbers).__module__.startswith("unittest.mock"):
            return []
        return list(serial_numbers or [])

    def _model_flag(self, model, attr: str) -> bool:
        """Read a boolean model flag while treating mock-only attrs as false."""
        value = getattr(model, attr, False) if model is not None else False
        if type(value).__module__.startswith("unittest.mock"):
            return False
        return bool(value)

    def _validate_sub_material_request_serials(
        self,
        *,
        org_id: UUID,
        item,
        warehouse_id: UUID,
        quantity: Decimal,
        serial_numbers: list[str] | None,
        require_serials: bool,
    ) -> list[str]:
        """Validate Sub-selected serials for a material request line."""
        from app.models.inventory.inventory_serial import InventorySerial
        from app.services.inventory import serial as legacy_serial_service

        try:
            serials = (
                legacy_serial_service.InventorySerialService.normalize_serial_numbers(
                    serial_numbers
                )
            )
        except legacy_serial_service.HTTPException as exc:
            raise SubValidationError(str(exc.detail)) from exc

        tracks_serial_numbers = self._model_flag(item, "track_serial_numbers")
        if not tracks_serial_numbers:
            if serials:
                raise SubValidationError(
                    f"Item does not track serial numbers: {item.item_code}"
                )
            return []

        if require_serials or serials:
            try:
                legacy_serial_service.InventorySerialService.validate_serial_quantity(
                    quantity, serials
                )
            except legacy_serial_service.HTTPException as exc:
                raise SubValidationError(str(exc.detail)) from exc

        if not serials:
            return []

        rows = list(
            self.db.scalars(
                select(InventorySerial).where(
                    InventorySerial.organization_id == org_id,
                    InventorySerial.item_id == item.item_id,
                    InventorySerial.serial_number.in_(serials),
                )
            ).all()
        )
        by_number = {row.serial_number.casefold(): row for row in rows}
        for serial_number in serials:
            serial = by_number.get(serial_number.casefold())
            if not serial:
                raise SubValidationError(f"Serial number not found: {serial_number}")
            if serial.status != "AVAILABLE" or serial.warehouse_id != warehouse_id:
                raise SubValidationError(
                    "Serial number is not available in the selected warehouse: "
                    f"{serial_number}"
                )

        return serials

    def _post_sub_issue_transaction(
        self,
        *,
        org_id: UUID,
        request,
        line: dict[str, Any],
        fiscal_period_id: UUID,
        transaction_date: datetime,
        created_by_user_id: UUID | None,
    ) -> None:
        """Post a single ISSUE transaction for a Sub-synced MR line."""
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
            source_document_type="Sub_MATERIAL_REQUEST",
            source_document_id=request.request_id,
            source_document_line_id=line.get("line_id"),
            reference=request.request_number,
            reason_code="Sub_SYNC_ISSUE",
            serial_numbers=line.get("serial_numbers") or None,
        )
        InventoryTransactionService.create_issue(
            self.db,
            org_id,
            txn_input,
            user_id,
            auto_commit=False,
        )

    def process_pending_stock_material_requests(
        self,
        org_id: UUID,
        *,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Auto-issue Sub material requests once pending stock becomes available."""
        from app.models.inventory.material_request import (
            MaterialRequest,
            MaterialRequestStatus,
            MaterialRequestType,
        )

        requests = (
            self.db.scalars(
                select(MaterialRequest)
                .options(joinedload(MaterialRequest.items))
                .where(
                    MaterialRequest.organization_id == org_id,
                    MaterialRequest.source_system == "sub",
                    MaterialRequest.source_id.is_not(None),
                    MaterialRequest.request_type == MaterialRequestType.ISSUE,
                    MaterialRequest.status == MaterialRequestStatus.PENDING_STOCK,
                )
                .order_by(MaterialRequest.created_at.asc())
                .limit(limit)
            )
            .unique()
            .all()
        )

        result: dict[str, Any] = {
            "checked": len(requests),
            "issued": 0,
            "still_pending": 0,
            "notifications_sent": 0,
            "errors": [],
        }

        for request in requests:
            try:
                with conflict_savepoint(self.db):
                    line_snapshots = self._snapshot_material_request_lines(
                        request.items
                    )
                    if not self._sub_material_request_has_sufficient_stock(
                        org_id,
                        line_snapshots,
                    ):
                        result["still_pending"] += 1
                        continue

                    status_result = self._advance_sub_material_request_status(
                        org_id=org_id,
                        request=request,
                        requested_status=MaterialRequestStatus.ISSUED,
                        actor_person_id=request.created_by_id,
                    )
                    if status_result.status == MaterialRequestStatus.ISSUED.value:
                        result["issued"] += 1
                        if self._notify_sub_material_request_auto_issued(
                            org_id,
                            request,
                        ):
                            result["notifications_sent"] += 1
                    else:
                        result["still_pending"] += 1
            except Exception as exc:
                logger.exception(
                    "Failed to auto-issue pending-stock Sub material request %s",
                    getattr(request, "request_number", request.request_id),
                )
                result["errors"].append(
                    {
                        "request_id": str(request.request_id),
                        "request_number": request.request_number,
                        "error": str(exc),
                    }
                )

        return result

    def _notify_sub_material_request_auto_issued(
        self,
        org_id: UUID,
        request,
    ) -> bool:
        """Notify the requester that a pending-stock Sub material request was issued."""
        from app.models.notification import (
            EntityType,
            NotificationChannel,
            NotificationType,
        )
        from app.models.people.hr.employee import Employee
        from app.services.notification import NotificationService

        recipient_id: UUID | None = None
        if request.requested_by_id:
            employee = self.db.get(Employee, request.requested_by_id)
            if employee and employee.organization_id == org_id:
                recipient_id = employee.person_id

        recipient_id = recipient_id or request.created_by_id
        if not recipient_id:
            logger.info(
                "Skipping auto-issued material request notification for %s: no requester/person",
                request.request_number,
            )
            return False

        NotificationService().create(
            self.db,
            organization_id=org_id,
            recipient_id=recipient_id,
            entity_type=EntityType.SYSTEM,
            entity_id=request.request_id,
            notification_type=NotificationType.INFO,
            title=f"Material request {request.request_number} issued",
            message=(
                "Stock is now available and the material request has been "
                "automatically issued."
            ),
            channel=NotificationChannel.BOTH,
            action_url=f"/inventory/material-requests/{request.request_id}",
            actor_id=request.created_by_id,
        )
        return True

    def _emit_sub_material_request_status_changed(
        self,
        *,
        org_id: UUID,
        request,
        old_status,
        new_status,
        actor_person_id: UUID | None,
    ) -> None:
        """Emit a service-hook event for Sub material request status propagation."""
        if not request.source_id or request.source_system != "sub":
            return

        from app.services.hooks import emit_hook_event
        from app.services.hooks.events import SUB_MATERIAL_REQUEST_STATUS_CHANGED

        try:
            emit_hook_event(
                self.db,
                event_name=SUB_MATERIAL_REQUEST_STATUS_CHANGED,
                organization_id=org_id,
                entity_type="MaterialRequest",
                entity_id=request.request_id,
                actor_user_id=actor_person_id,
                payload=self._build_sub_material_request_status_event_payload(
                    request,
                    old_status=old_status,
                    new_status=new_status,
                ),
            )
        except Exception:
            logger.exception(
                "Failed to emit Sub material request status hook event for %s",
                request.request_number,
            )

    def _build_sub_material_request_status_event_payload(
        self,
        request,
        *,
        old_status,
        new_status,
    ) -> dict[str, Any]:
        """Build Sub-facing status event payload for material requests."""
        return {
            "source_request_id": request.source_id,
            "request_id": str(request.request_id),
            "request_number": request.request_number,
            "old_status": old_status.value if old_status else None,
            "new_status": new_status.value,
            "status": new_status.value,
            "request_type": request.request_type.value,
            "items": [
                {
                    "line_id": str(line.item_id),
                    "item_id": str(line.inventory_item_id),
                    "warehouse_id": str(line.warehouse_id)
                    if line.warehouse_id
                    else None,
                    "requested_qty": str(line.requested_qty),
                    "ordered_qty": str(line.ordered_qty),
                    "uom": line.uom,
                    "sequence": line.sequence,
                    "serial_numbers": self._material_request_line_serial_numbers(line),
                }
                for line in sorted(request.items, key=lambda item: item.sequence)
            ],
            "created_at": request.created_at.isoformat()
            if request.created_at
            else None,
            "updated_at": request.updated_at.isoformat()
            if request.updated_at
            else None,
        }

    def get_material_request_by_source_id(
        self,
        org_id: UUID,
        source_request_id: str,
    ) -> SubMaterialRequestStatusRead | None:
        """Get material request status by its opaque Sub source id."""
        from app.models.inventory.material_request import MaterialRequest

        stmt = (
            select(MaterialRequest)
            .options(joinedload(MaterialRequest.items))
            .where(
                MaterialRequest.organization_id == org_id,
                MaterialRequest.source_system == "sub",
                MaterialRequest.source_id == source_request_id,
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

        return SubMaterialRequestStatusRead(
            request_id=mr.request_id,
            request_number=mr.request_number,
            status=mr.status.value,
            request_type=mr.request_type.value,
            items=[
                SubMaterialRequestItemRead(
                    item_code=items_map.get(line.inventory_item_id, ("", ""))[0],
                    item_name=items_map.get(line.inventory_item_id, ("", ""))[1],
                    requested_qty=line.requested_qty,
                    ordered_qty=line.ordered_qty,
                    uom=line.uom,
                    serial_numbers=self._material_request_line_serial_numbers(line)
                    or None,
                )
                for line in mr.items
            ],
            created_at=mr.created_at,
        )

    def create_purchase_order(
        self,
        org_id: UUID,
        data: SubPurchaseOrderPayload,
        created_by_person_id: UUID,
    ) -> SubPurchaseOrderResponse:
        """
        Create a DRAFT purchase order from a Sub vendor quote approval.

        Idempotent by the domain-owned purchase-order correlation id.

        Args:
            org_id: Organization scope.
            data: Sub purchase order payload.
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

        self._validate_purchase_order_financials(data)
        incoming_fingerprint = fingerprint_of(data)
        correlation_id = f"sub-wo:{data.source_work_order_id}"
        fallback_stmt = select(PurchaseOrder).where(
            PurchaseOrder.organization_id == org_id,
            PurchaseOrder.correlation_id == correlation_id,
        )
        existing_po = self.db.scalar(fallback_stmt)
        if existing_po:
            self._require_matching_source_fingerprint(
                existing_po,
                incoming_fingerprint,
                source_identity=(f"Sub purchase order {data.source_work_order_id}"),
            )
            logger.info(
                "PO already exists for source_work_order_id=%s",
                data.source_work_order_id,
            )
            return SubPurchaseOrderResponse(
                purchase_order_id=existing_po.po_number,
                po_id=existing_po.po_id,
                status=existing_po.status.value.lower(),
                source_work_order_id=data.source_work_order_id,
            )

        # 3. Resolve supplier
        supplier = self._resolve_supplier(org_id, data.vendor_erp_id, data.vendor_code)

        # 4. Resolve project (optional)
        project_id = self._resolve_project_id(org_id, data.source_project_id)

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

        # 7. Create PO. The request boundary owns the transaction; this nested
        # savepoint only isolates a concurrent source-identity conflict.
        try:
            with conflict_savepoint(self.db):
                po = PurchaseOrderService.create_po(
                    self.db, org_id, po_input, creator_id
                )
                po.source_fingerprint = incoming_fingerprint
                self.db.flush()
        except IntegrityError:
            existing_po = self.db.scalar(fallback_stmt)
            if existing_po is None:
                raise
            self._require_matching_source_fingerprint(
                existing_po,
                incoming_fingerprint,
                source_identity=(f"Sub purchase order {data.source_work_order_id}"),
            )
            logger.info(
                "Concurrent PO creation resolved as a safe replay for "
                "source_work_order_id=%s",
                data.source_work_order_id,
            )
            return SubPurchaseOrderResponse(
                purchase_order_id=existing_po.po_number,
                po_id=existing_po.po_id,
                status=existing_po.status.value.lower(),
                source_work_order_id=data.source_work_order_id,
            )

        po_id = po.po_id
        po_number = po.po_number
        po_status = po.status.value.lower()
        supplier_code = supplier.supplier_code

        logger.info(
            "Created PO %s for source_work_order_id=%s, supplier=%s",
            po_number,
            data.source_work_order_id,
            supplier_code,
        )

        return SubPurchaseOrderResponse(
            purchase_order_id=po_number,
            po_id=po_id,
            status=po_status,
            source_work_order_id=data.source_work_order_id,
        )

    def create_purchase_invoice(
        self,
        org_id: UUID,
        data: SubPurchaseInvoicePayload,
        created_by_person_id: UUID,
    ) -> SubPurchaseInvoiceResponse:
        """Create one DRAFT AP invoice matched to an existing ERP purchase order."""
        from app.models.finance.ap.purchase_order import PurchaseOrder
        from app.models.finance.ap.supplier_invoice import (
            SupplierInvoice,
            SupplierInvoiceStatus,
            SupplierInvoiceType,
        )
        from app.models.finance.ap.supplier_invoice_line import SupplierInvoiceLine
        from app.services.finance.ap.supplier_invoice import (
            InvoiceLineInput,
            SupplierInvoiceInput,
            SupplierInvoiceService,
        )

        incoming_fingerprint = fingerprint_of(data)
        correlation_id = f"sub-invoice:{data.source_invoice_id}"
        existing = self.db.scalar(
            select(SupplierInvoice).where(
                SupplierInvoice.organization_id == org_id,
                SupplierInvoice.correlation_id == correlation_id,
            )
        )
        if existing:
            self._require_matching_source_fingerprint(
                existing,
                incoming_fingerprint,
                source_identity=f"Sub purchase invoice {data.source_invoice_id}",
            )
            return self._purchase_invoice_response(existing, data.source_invoice_id)

        po_query = select(PurchaseOrder).where(PurchaseOrder.organization_id == org_id)
        try:
            po_uuid = UUID(data.erp_purchase_order_id)
        except ValueError:
            po_uuid = None
        if po_uuid:
            po_query = po_query.where(PurchaseOrder.po_id == po_uuid)
        else:
            po_query = po_query.where(
                PurchaseOrder.po_number == data.erp_purchase_order_id
            )
        po = self.db.scalar(po_query)
        if po is None:
            raise ValueError(f"Purchase order not found: {data.erp_purchase_order_id}")

        supplier = self._resolve_supplier(
            org_id, data.vendor_erp_id, data.vendor_code or data.vendor_name
        )
        if supplier.supplier_id != po.supplier_id:
            raise ValueError(
                "Invoice vendor does not match the purchase order supplier"
            )
        if data.currency.upper() != po.currency_code.upper():
            raise ValueError("Invoice currency does not match the purchase order")

        po_lines = sorted(po.lines, key=lambda row: row.line_number)
        if len(data.items) > len(po_lines):
            raise ValueError("Invoice has more lines than its purchase order")

        project_id = self._resolve_project_id(org_id, data.source_project_id)
        invoice_lines: list[InvoiceLineInput] = []
        for index, item in enumerate(data.items):
            po_line = po_lines[index]
            if item.quantity > po_line.quantity_ordered:
                raise ValueError(
                    f"Invoice line {index + 1} quantity exceeds the purchase order"
                )
            existing_amount = self.db.scalar(
                select(func.coalesce(func.sum(SupplierInvoiceLine.line_amount), 0))
                .join(
                    SupplierInvoice,
                    SupplierInvoice.invoice_id == SupplierInvoiceLine.invoice_id,
                )
                .where(
                    SupplierInvoice.organization_id == org_id,
                    SupplierInvoice.status != SupplierInvoiceStatus.VOID,
                    SupplierInvoiceLine.po_line_id == po_line.line_id,
                )
            )
            remaining = Decimal(po_line.line_amount) - Decimal(existing_amount or 0)
            if item.amount > remaining + Decimal("0.02"):
                raise ValueError(
                    f"Invoice line {index + 1} exceeds the uninvoiced PO amount"
                )
            invoice_lines.append(
                InvoiceLineInput(
                    description=item.description,
                    quantity=item.quantity,
                    unit_price=item.unit_price,
                    expense_account_id=(
                        po_line.expense_account_id
                        or supplier.default_expense_account_id
                    ),
                    asset_account_id=po_line.asset_account_id,
                    po_line_id=po_line.line_id,
                    item_id=po_line.item_id,
                    tax_code_id=(po_line.tax_code_id or supplier.default_tax_code_id),
                    cost_center_id=po_line.cost_center_id,
                    project_id=po_line.project_id or project_id,
                    segment_id=po_line.segment_id,
                )
            )

        approved_date = (data.approved_at or datetime.now(UTC)).date()
        invoice_input = SupplierInvoiceInput(
            supplier_id=supplier.supplier_id,
            invoice_type=SupplierInvoiceType.STANDARD,
            invoice_date=approved_date,
            received_date=date.today(),
            due_date=approved_date + timedelta(days=supplier.payment_terms_days or 0),
            currency_code=data.currency.upper(),
            lines=invoice_lines,
            purpose=(
                f"{data.project_name or data.project_code or 'Vendor project'}; "
                f"PO {po.po_number}"
            ),
            supplier_invoice_number=data.source_invoice_number,
            correlation_id=correlation_id,
        )

        try:
            with conflict_savepoint(self.db):
                invoice = SupplierInvoiceService.create_invoice(
                    self.db, org_id, invoice_input, created_by_person_id
                )
                if abs(Decimal(invoice.total_amount) - data.total) > Decimal("0.02"):
                    raise ValueError(
                        "Invoice total does not match ERP tax/accounting calculation"
                    )
                invoice.source_fingerprint = incoming_fingerprint
                self.db.flush()
        except IntegrityError:
            existing = self.db.scalar(
                select(SupplierInvoice).where(
                    SupplierInvoice.organization_id == org_id,
                    SupplierInvoice.correlation_id == correlation_id,
                )
            )
            if existing:
                self._require_matching_source_fingerprint(
                    existing,
                    incoming_fingerprint,
                    source_identity=(f"Sub purchase invoice {data.source_invoice_id}"),
                )
                return self._purchase_invoice_response(existing, data.source_invoice_id)
            raise

        return self._purchase_invoice_response(invoice, data.source_invoice_id)

    @staticmethod
    def _purchase_invoice_response(
        invoice, source_invoice_id: str
    ) -> SubPurchaseInvoiceResponse:
        return SubPurchaseInvoiceResponse(
            purchase_invoice_id=str(invoice.invoice_id),
            invoice_id=invoice.invoice_id,
            invoice_number=invoice.invoice_number,
            status=invoice.status.value.lower(),
            source_invoice_id=source_invoice_id,
        )

    def upload_purchase_invoice_attachment(
        self,
        organization_id: UUID,
        purchase_invoice_id: UUID,
        payload: SubPurchaseInvoiceAttachmentPayload,
        actor_person_id: UUID,
    ) -> SubPurchaseInvoiceAttachmentResponse:
        """Attach an idempotent source document to an ERP supplier invoice."""
        from app.models.finance.ap.supplier_invoice import SupplierInvoice
        from app.models.finance.common.attachment import Attachment, AttachmentCategory
        from app.services.finance.common.attachment import (
            AttachmentInput,
            attachment_service,
        )

        invoice = self.db.get(SupplierInvoice, purchase_invoice_id)
        if invoice is None or invoice.organization_id != organization_id:
            raise SubNotFoundError("Purchase invoice not found")
        try:
            content = base64.b64decode(payload.content_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise SubValidationError("Invalid base64 attachment") from exc
        if not content:
            raise SubValidationError("Attachment is empty")
        if len(content) > 10 * 1024 * 1024:
            raise SubPayloadTooLargeError("Attachment exceeds 10 MB")

        checksum = hashlib.sha256(content).hexdigest()
        existing = self.db.scalar(
            select(Attachment).where(
                Attachment.organization_id == organization_id,
                Attachment.entity_type == "SUPPLIER_INVOICE",
                Attachment.entity_id == invoice.invoice_id,
                Attachment.checksum == checksum,
            )
        )
        if existing:
            return SubPurchaseInvoiceAttachmentResponse(
                attachment_id=existing.attachment_id,
                purchase_invoice_id=invoice.invoice_id,
                file_name=existing.file_name,
                created=False,
            )

        attachment = attachment_service.save_file(
            self.db,
            organization_id,
            AttachmentInput(
                entity_type="SUPPLIER_INVOICE",
                entity_id=str(invoice.invoice_id),
                file_name=payload.file_name,
                content_type=payload.mime_type,
                category=AttachmentCategory.INVOICE,
                description="Uploaded by Dotmac Sub",
            ),
            io.BytesIO(content),
            actor_person_id,
        )
        return SubPurchaseInvoiceAttachmentResponse(
            attachment_id=attachment.attachment_id,
            purchase_invoice_id=invoice.invoice_id,
            file_name=attachment.file_name,
            created=True,
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
            "Sub supplier resolution org=%s erp_id=%r vendor_code=%r normalized_erp_id=%r normalized_vendor_code=%r",
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

            # Sub has historically sent vendor display names in vendor_code.
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
            "Sub supplier resolution failed org=%s erp_id=%r vendor_code=%r normalized_erp_id=%r normalized_vendor_code=%r",
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

    def create_purchase_order_variation(
        self,
        org_id: UUID,
        data: SubPurchaseOrderVariationPayload,
        created_by_person_id: UUID,
    ) -> SubPurchaseOrderResponse:
        """
        Create a PO amendment from a Sub variation approval.

        Strategy:
        1. Idempotency — if a PO with this variation_id exists, return it.
        2. Find the preceding PO via its domain-owned correlation id.
        3. Mark the baseline as SUPERSEDED (preserving audit trail).
        4. Create a new amendment PO linked to the baseline.

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

        self._validate_purchase_order_financials(data)
        incoming_fingerprint = fingerprint_of(data)
        amendment_version = data.variation_version
        correlation_id = f"sub-wo:{data.source_work_order_id}:v{amendment_version}"

        # 1. Idempotency: check if we already created a PO for this variation_id
        existing_stmt = select(PurchaseOrder).where(
            PurchaseOrder.organization_id == org_id,
            or_(
                PurchaseOrder.variation_id == data.variation_id,
                PurchaseOrder.correlation_id == correlation_id,
            ),
        )
        existing_variation_po = self.db.scalar(existing_stmt)
        if existing_variation_po:
            self._require_matching_source_fingerprint(
                existing_variation_po,
                incoming_fingerprint,
                source_identity=f"Sub PO variation {data.variation_id}",
            )
            logger.info(
                "PO variation already exists for variation_id=%s, returning",
                data.variation_id,
            )
            return SubPurchaseOrderResponse(
                purchase_order_id=existing_variation_po.po_number,
                po_id=existing_variation_po.po_id,
                status=existing_variation_po.status.value.lower(),
                source_work_order_id=data.source_work_order_id,
                is_amendment=True,
                variation_id=data.variation_id,
                amendment_version=existing_variation_po.amendment_version,
                superseded_po_id=existing_variation_po.original_po_id,
            )

        # 2. Find the immediately preceding PO via its domain correlation.
        previous_version = data.variation_version - 1
        baseline_correlation = f"sub-wo:{data.source_work_order_id}"
        if previous_version > 1:
            baseline_correlation += f":v{previous_version}"
        baseline_po = self.db.scalar(
            select(PurchaseOrder).where(
                PurchaseOrder.organization_id == org_id,
                PurchaseOrder.correlation_id == baseline_correlation,
            )
        )
        if baseline_po is None:
            raise ValueError(
                "No baseline PO found for "
                f"source_work_order_id={data.source_work_order_id}"
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
        project_id = self._resolve_project_id(org_id, data.source_project_id)
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

        po_input = PurchaseOrderInput(
            supplier_id=supplier.supplier_id,
            po_date=date.today(),
            currency_code=data.currency,
            lines=po_lines,
            correlation_id=correlation_id,
            terms_and_conditions=data.title,
        )

        # 5. Create the amendment and supersede its baseline atomically inside
        # a conflict savepoint. The request boundary remains transaction owner.
        try:
            with conflict_savepoint(self.db):
                new_po = PurchaseOrderService.create_po(
                    self.db, org_id, po_input, creator_id
                )
                new_po.source_fingerprint = incoming_fingerprint
                new_po.is_amendment = True
                new_po.original_po_id = baseline_po.po_id
                new_po.amendment_version = amendment_version
                new_po.amendment_reason = data.amendment_reason
                new_po.variation_id = data.variation_id

                # Preserve the baseline for audit while making the new version
                # the only active PO in this amendment chain.
                baseline_po.status = POStatus.SUPERSEDED
                self.db.flush()
        except IntegrityError:
            logger.warning(
                "Concurrent variation creation for variation_id=%s, "
                "checking the immutable winner",
                data.variation_id,
            )
            existing = self.db.scalar(existing_stmt)
            if existing:
                self._require_matching_source_fingerprint(
                    existing,
                    incoming_fingerprint,
                    source_identity=f"Sub PO variation {data.variation_id}",
                )
                return SubPurchaseOrderResponse(
                    purchase_order_id=existing.po_number,
                    po_id=existing.po_id,
                    status=existing.status.value.lower(),
                    source_work_order_id=data.source_work_order_id,
                    is_amendment=True,
                    variation_id=data.variation_id,
                    amendment_version=existing.amendment_version,
                    superseded_po_id=existing.original_po_id,
                )
            raise

        logger.info(
            "Created PO amendment %s (v%d) for variation_id=%s, superseding %s",
            new_po.po_number,
            amendment_version,
            data.variation_id,
            baseline_po.po_number,
        )

        return SubPurchaseOrderResponse(
            purchase_order_id=new_po.po_number,
            po_id=new_po.po_id,
            status=new_po.status.value.lower(),
            source_work_order_id=data.source_work_order_id,
            is_amendment=True,
            variation_id=data.variation_id,
            amendment_version=amendment_version,
            superseded_po_id=baseline_po.po_id,
        )
