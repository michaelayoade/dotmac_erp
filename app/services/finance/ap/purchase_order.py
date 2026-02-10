"""
PurchaseOrderService - Purchase order lifecycle management.

Manages PO creation, approval, and status tracking.
"""

from __future__ import annotations

import builtins
import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance.ap.purchase_order import POStatus, PurchaseOrder
from app.models.finance.ap.purchase_order_line import PurchaseOrderLine
from app.models.finance.ap.supplier import Supplier
from app.models.finance.core_config.numbering_sequence import SequenceType
from app.services.common import coerce_uuid
from app.services.finance.ap.input_utils import (
    parse_date_str,
    parse_decimal,
    parse_json_list,
    require_uuid,
    resolve_currency_code,
)
from app.services.finance.platform.sequence import SequenceService
from app.services.response import ListResponseMixin

logger = logging.getLogger(__name__)


@dataclass
class POLineInput:
    """Input for a purchase order line."""

    description: str
    quantity_ordered: Decimal
    unit_price: Decimal
    item_id: UUID | None = None
    tax_code_id: UUID | None = None
    tax_amount: Decimal = Decimal("0")
    expense_account_id: UUID | None = None
    asset_account_id: UUID | None = None
    cost_center_id: UUID | None = None
    project_id: UUID | None = None
    segment_id: UUID | None = None
    delivery_date: date | None = None


@dataclass
class PurchaseOrderInput:
    """Input for creating/updating a purchase order."""

    supplier_id: UUID
    po_date: date
    currency_code: str
    lines: list[POLineInput] = field(default_factory=list)
    expected_delivery_date: date | None = None
    exchange_rate: Decimal | None = None
    shipping_address: dict[str, Any] | None = None
    terms_and_conditions: str | None = None
    budget_id: UUID | None = None
    correlation_id: str | None = None


class PurchaseOrderService(ListResponseMixin):
    """
    Service for purchase order lifecycle management.
    """

    @staticmethod
    def build_input_from_payload(
        db: Session,
        organization_id: UUID,
        payload: dict,
    ) -> PurchaseOrderInput:
        """Build PurchaseOrderInput from raw payload (strings or JSON)."""
        org_id = coerce_uuid(organization_id)

        supplier_id = require_uuid(payload.get("supplier_id"), "Supplier")
        po_date = parse_date_str(payload.get("po_date"), "PO date", True)
        if po_date is None:
            raise ValueError("PO date is required")
        currency_code = resolve_currency_code(db, org_id, payload.get("currency_code"))

        lines_data = parse_json_list(payload.get("lines"), "Lines")
        lines: list[POLineInput] = []
        for line in lines_data:
            if not line.get("description"):
                raise ValueError("Line description is required")
            quantity = parse_decimal(
                line.get("quantity_ordered", line.get("quantity", 0)),
                "Quantity ordered",
            )
            unit_price = parse_decimal(line.get("unit_price", 0), "Unit price")
            lines.append(
                POLineInput(
                    description=line.get("description", ""),
                    quantity_ordered=quantity,
                    unit_price=unit_price,
                    item_id=coerce_uuid(line.get("item_id"))
                    if line.get("item_id")
                    else None,
                    tax_code_id=coerce_uuid(line.get("tax_code_id"))
                    if line.get("tax_code_id")
                    else None,
                    tax_amount=parse_decimal(line.get("tax_amount", 0), "Tax amount"),
                    expense_account_id=coerce_uuid(line.get("expense_account_id"))
                    if line.get("expense_account_id")
                    else None,
                    asset_account_id=coerce_uuid(line.get("asset_account_id"))
                    if line.get("asset_account_id")
                    else None,
                    cost_center_id=coerce_uuid(line.get("cost_center_id"))
                    if line.get("cost_center_id")
                    else None,
                    project_id=coerce_uuid(line.get("project_id"))
                    if line.get("project_id")
                    else None,
                    segment_id=coerce_uuid(line.get("segment_id"))
                    if line.get("segment_id")
                    else None,
                    delivery_date=parse_date_str(
                        line.get("delivery_date"), "Delivery date"
                    ),
                )
            )

        exchange_rate: Decimal | None = None
        if payload.get("exchange_rate") not in (None, ""):
            exchange_rate = parse_decimal(payload.get("exchange_rate"), "Exchange rate")

        return PurchaseOrderInput(
            supplier_id=supplier_id,
            po_date=po_date,
            currency_code=currency_code,
            lines=lines,
            expected_delivery_date=parse_date_str(
                payload.get("expected_delivery_date"), "Expected delivery date"
            ),
            exchange_rate=exchange_rate,
            shipping_address=payload.get("shipping_address"),
            terms_and_conditions=payload.get("terms_and_conditions"),
            budget_id=coerce_uuid(payload.get("budget_id"))
            if payload.get("budget_id")
            else None,
            correlation_id=payload.get("correlation_id"),
        )

    @staticmethod
    def create_po(
        db: Session,
        organization_id: UUID,
        input: PurchaseOrderInput,
        created_by_user_id: UUID,
    ) -> PurchaseOrder:
        """
        Create a new purchase order in DRAFT status.

        Args:
            db: Database session
            organization_id: Organization scope
            input: PO input data
            created_by_user_id: User creating the PO

        Returns:
            Created PurchaseOrder

        Raises:
            HTTPException(400): If validation fails
            HTTPException(404): If supplier not found
        """
        org_id = coerce_uuid(organization_id)
        supplier_id = coerce_uuid(input.supplier_id)

        # Validate supplier exists
        supplier = db.scalars(
            select(Supplier).where(
                Supplier.supplier_id == supplier_id,
                Supplier.organization_id == org_id,
            )
        ).first()
        if not supplier:
            raise HTTPException(status_code=404, detail="Supplier not found")

        # Validate lines
        if not input.lines:
            raise HTTPException(
                status_code=400, detail="Purchase order must have at least one line"
            )

        # Generate PO number
        po_number = SequenceService.get_next_number(
            db, org_id, SequenceType.PURCHASE_ORDER
        )

        # Calculate totals
        subtotal = Decimal("0")
        tax_total = Decimal("0")

        for line_input in input.lines:
            line_amount = line_input.quantity_ordered * line_input.unit_price
            subtotal += line_amount
            tax_total += line_input.tax_amount

        total_amount = subtotal + tax_total

        # Create PO
        po = PurchaseOrder(
            organization_id=org_id,
            supplier_id=supplier_id,
            po_number=po_number,
            po_date=input.po_date,
            expected_delivery_date=input.expected_delivery_date,
            currency_code=input.currency_code,
            exchange_rate=input.exchange_rate,
            subtotal=subtotal,
            tax_amount=tax_total,
            total_amount=total_amount,
            status=POStatus.DRAFT,
            shipping_address=input.shipping_address,
            terms_and_conditions=input.terms_and_conditions,
            budget_id=input.budget_id,
            created_by_user_id=created_by_user_id,
            correlation_id=input.correlation_id,
        )
        db.add(po)
        db.flush()

        # Create lines
        for idx, line_input in enumerate(input.lines, start=1):
            line_amount = line_input.quantity_ordered * line_input.unit_price
            line = PurchaseOrderLine(
                po_id=po.po_id,
                line_number=idx,
                item_id=line_input.item_id,
                description=line_input.description,
                quantity_ordered=line_input.quantity_ordered,
                unit_price=line_input.unit_price,
                line_amount=line_amount,
                tax_code_id=line_input.tax_code_id,
                tax_amount=line_input.tax_amount,
                expense_account_id=line_input.expense_account_id,
                asset_account_id=line_input.asset_account_id,
                cost_center_id=line_input.cost_center_id,
                project_id=line_input.project_id,
                segment_id=line_input.segment_id,
                delivery_date=line_input.delivery_date,
            )
            db.add(line)

        db.commit()
        db.refresh(po)

        return po

    @staticmethod
    def submit_for_approval(
        db: Session,
        organization_id: UUID,
        po_id: UUID,
        submitted_by_user_id: UUID,
    ) -> PurchaseOrder:
        """
        Submit PO for approval.

        Args:
            db: Database session
            organization_id: Organization scope
            po_id: Purchase order ID
            submitted_by_user_id: User submitting

        Returns:
            Updated PurchaseOrder

        Raises:
            HTTPException(400): If PO not in DRAFT status
            HTTPException(404): If PO not found
        """
        org_id = coerce_uuid(organization_id)
        po_id = coerce_uuid(po_id)

        po = db.scalars(
            select(PurchaseOrder).where(
                PurchaseOrder.po_id == po_id,
                PurchaseOrder.organization_id == org_id,
            )
        ).first()

        if not po:
            raise HTTPException(status_code=404, detail="Purchase order not found")

        if po.status != POStatus.DRAFT:
            raise HTTPException(
                status_code=400, detail=f"Cannot submit PO in {po.status.value} status"
            )

        po.status = POStatus.PENDING_APPROVAL

        try:
            from app.services.finance.automation.event_dispatcher import (
                fire_workflow_event,
            )

            fire_workflow_event(
                db=db,
                organization_id=org_id,
                entity_type="PURCHASE_ORDER",
                entity_id=po_id,
                event="ON_STATUS_CHANGE",
                old_values={"status": "DRAFT"},
                new_values={"status": "PENDING_APPROVAL"},
                user_id=coerce_uuid(submitted_by_user_id),
            )
        except Exception as e:
            logger.exception("Workflow event failed for PO %s submit: %s", po_id, e)

        db.commit()
        db.refresh(po)

        return po

    @staticmethod
    def approve_po(
        db: Session,
        organization_id: UUID,
        po_id: UUID,
        approved_by_user_id: UUID,
    ) -> PurchaseOrder:
        """
        Approve a purchase order.

        Args:
            db: Database session
            organization_id: Organization scope
            po_id: Purchase order ID
            approved_by_user_id: User approving

        Returns:
            Updated PurchaseOrder

        Raises:
            HTTPException(400): If PO not pending approval
            HTTPException(404): If PO not found
        """
        org_id = coerce_uuid(organization_id)
        po_id = coerce_uuid(po_id)

        po = db.scalars(
            select(PurchaseOrder).where(
                PurchaseOrder.po_id == po_id,
                PurchaseOrder.organization_id == org_id,
            )
        ).first()

        if not po:
            raise HTTPException(status_code=404, detail="Purchase order not found")

        if po.status != POStatus.PENDING_APPROVAL:
            raise HTTPException(
                status_code=400, detail=f"Cannot approve PO in {po.status.value} status"
            )

        # SoD check
        if po.created_by_user_id == approved_by_user_id:
            raise HTTPException(
                status_code=400,
                detail="Approver cannot be the same as creator (Segregation of Duties)",
            )

        po.status = POStatus.APPROVED
        po.approved_by_user_id = approved_by_user_id
        po.approved_at = datetime.now(UTC)

        try:
            from app.services.finance.automation.event_dispatcher import (
                fire_workflow_event,
            )

            fire_workflow_event(
                db=db,
                organization_id=org_id,
                entity_type="PURCHASE_ORDER",
                entity_id=po_id,
                event="ON_APPROVAL",
                old_values={"status": "PENDING_APPROVAL"},
                new_values={"status": "APPROVED"},
                user_id=coerce_uuid(approved_by_user_id),
            )
        except Exception as e:
            logger.exception("Workflow event failed for PO %s approval: %s", po_id, e)

        db.commit()
        db.refresh(po)

        return po

    @staticmethod
    def cancel_po(
        db: Session,
        organization_id: UUID,
        po_id: UUID,
    ) -> PurchaseOrder:
        """
        Cancel a purchase order.

        Args:
            db: Database session
            organization_id: Organization scope
            po_id: Purchase order ID

        Returns:
            Updated PurchaseOrder

        Raises:
            HTTPException(400): If PO cannot be cancelled
            HTTPException(404): If PO not found
        """
        org_id = coerce_uuid(organization_id)
        po_id = coerce_uuid(po_id)

        po = db.scalars(
            select(PurchaseOrder).where(
                PurchaseOrder.po_id == po_id,
                PurchaseOrder.organization_id == org_id,
            )
        ).first()

        if not po:
            raise HTTPException(status_code=404, detail="Purchase order not found")

        if po.status in [POStatus.RECEIVED, POStatus.CLOSED]:
            raise HTTPException(
                status_code=400, detail=f"Cannot cancel PO in {po.status.value} status"
            )

        if po.amount_received > 0:
            raise HTTPException(
                status_code=400, detail="Cannot cancel PO with received goods"
            )

        po.status = POStatus.CANCELLED

        try:
            from app.services.finance.automation.event_dispatcher import (
                fire_workflow_event,
            )

            fire_workflow_event(
                db=db,
                organization_id=org_id,
                entity_type="PURCHASE_ORDER",
                entity_id=po_id,
                event="ON_STATUS_CHANGE",
                old_values={},
                new_values={"status": "CANCELLED"},
            )
        except Exception as e:
            logger.exception("Workflow event failed for PO %s cancel: %s", po_id, e)

        db.commit()
        db.refresh(po)

        return po

    @staticmethod
    def close_po(
        db: Session,
        organization_id: UUID,
        po_id: UUID,
    ) -> PurchaseOrder:
        """
        Close a purchase order (fully received or manually closed).

        Args:
            db: Database session
            organization_id: Organization scope
            po_id: Purchase order ID

        Returns:
            Updated PurchaseOrder
        """
        org_id = coerce_uuid(organization_id)
        po_id = coerce_uuid(po_id)

        po = db.scalars(
            select(PurchaseOrder).where(
                PurchaseOrder.po_id == po_id,
                PurchaseOrder.organization_id == org_id,
            )
        ).first()

        if not po:
            raise HTTPException(status_code=404, detail="Purchase order not found")

        if po.status == POStatus.CANCELLED:
            raise HTTPException(status_code=400, detail="Cannot close cancelled PO")

        po.status = POStatus.CLOSED
        db.commit()
        db.refresh(po)

        return po

    @staticmethod
    def update_received_amount(
        db: Session,
        po_id: UUID,
        amount_received: Decimal,
        organization_id: UUID | None = None,
    ) -> PurchaseOrder:
        """
        Update the received amount on a PO (called by GoodsReceiptService).

        Args:
            db: Database session
            po_id: Purchase order ID
            amount_received: Amount received to add
            organization_id: Organization scope for multi-tenancy check

        Returns:
            Updated PurchaseOrder
        """
        po_id = coerce_uuid(po_id)

        stmt = select(PurchaseOrder).where(PurchaseOrder.po_id == po_id)
        if organization_id:
            stmt = stmt.where(
                PurchaseOrder.organization_id == coerce_uuid(organization_id),
            )
        po = db.scalars(stmt).first()

        if not po:
            raise HTTPException(status_code=404, detail="Purchase order not found")

        po.amount_received += amount_received

        # Update status based on received amount
        if po.amount_received >= po.total_amount:
            po.status = POStatus.RECEIVED
        elif po.amount_received > 0:
            po.status = POStatus.PARTIALLY_RECEIVED

        db.commit()
        db.refresh(po)

        return po

    @staticmethod
    def get(
        db: Session,
        po_id: str,
        organization_id: UUID | None = None,
    ) -> PurchaseOrder | None:
        """Get a purchase order by ID with optional org_id isolation."""
        po = db.get(PurchaseOrder, coerce_uuid(po_id))
        if po is None:
            return None
        if organization_id is not None and po.organization_id != organization_id:
            return None
        return po

    @staticmethod
    def get_by_number(
        db: Session,
        organization_id: UUID,
        po_number: str,
    ) -> PurchaseOrder | None:
        """Get a purchase order by number."""
        return db.scalars(
            select(PurchaseOrder).where(
                PurchaseOrder.organization_id == coerce_uuid(organization_id),
                PurchaseOrder.po_number == po_number,
            )
        ).first()

    @staticmethod
    def get_po_lines(db: Session, po_id: str) -> builtins.list[PurchaseOrderLine]:
        """Get all lines for a purchase order."""
        return list(
            db.scalars(
                select(PurchaseOrderLine)
                .where(PurchaseOrderLine.po_id == coerce_uuid(po_id))
                .order_by(PurchaseOrderLine.line_number)
            ).all()
        )

    @staticmethod
    def list(
        db: Session,
        organization_id: str | None = None,
        supplier_id: str | None = None,
        status: POStatus | None = None,
        from_date: date | None = None,
        to_date: date | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> builtins.list[PurchaseOrder]:
        """
        List purchase orders with filters.

        Args:
            db: Database session
            organization_id: Filter by organization
            supplier_id: Filter by supplier
            status: Filter by status
            from_date: Filter by start date
            to_date: Filter by end date
            limit: Maximum results
            offset: Pagination offset

        Returns:
            List of PurchaseOrder objects
        """
        stmt = select(PurchaseOrder)

        if organization_id:
            stmt = stmt.where(
                PurchaseOrder.organization_id == coerce_uuid(organization_id)
            )

        if supplier_id:
            stmt = stmt.where(PurchaseOrder.supplier_id == coerce_uuid(supplier_id))

        if status:
            stmt = stmt.where(PurchaseOrder.status == status)

        if from_date:
            stmt = stmt.where(PurchaseOrder.po_date >= from_date)

        if to_date:
            stmt = stmt.where(PurchaseOrder.po_date <= to_date)

        return list(
            db.scalars(
                stmt.order_by(PurchaseOrder.po_date.desc()).offset(offset).limit(limit)
            ).all()
        )


# Module-level instance
purchase_order_service = PurchaseOrderService()
