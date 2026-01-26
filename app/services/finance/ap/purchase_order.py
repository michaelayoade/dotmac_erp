"""
PurchaseOrderService - Purchase order lifecycle management.

Manages PO creation, approval, and status tracking.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, List, Optional
from uuid import UUID
import uuid as uuid_lib

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.finance.ap.purchase_order import PurchaseOrder, POStatus
from app.models.finance.ap.purchase_order_line import PurchaseOrderLine
from app.models.finance.ap.supplier import Supplier
from app.models.finance.core_config.numbering_sequence import SequenceType
from app.services.common import coerce_uuid
from app.services.finance.platform.sequence import SequenceService
from app.services.response import ListResponseMixin


@dataclass
class POLineInput:
    """Input for a purchase order line."""

    description: str
    quantity_ordered: Decimal
    unit_price: Decimal
    item_id: Optional[UUID] = None
    tax_code_id: Optional[UUID] = None
    tax_amount: Decimal = Decimal("0")
    expense_account_id: Optional[UUID] = None
    asset_account_id: Optional[UUID] = None
    cost_center_id: Optional[UUID] = None
    project_id: Optional[UUID] = None
    segment_id: Optional[UUID] = None
    delivery_date: Optional[date] = None


@dataclass
class PurchaseOrderInput:
    """Input for creating/updating a purchase order."""

    supplier_id: UUID
    po_date: date
    currency_code: str
    lines: list[POLineInput] = field(default_factory=list)
    expected_delivery_date: Optional[date] = None
    exchange_rate: Optional[Decimal] = None
    shipping_address: Optional[dict[str, Any]] = None
    terms_and_conditions: Optional[str] = None
    budget_id: Optional[UUID] = None
    correlation_id: Optional[str] = None


class PurchaseOrderService(ListResponseMixin):
    """
    Service for purchase order lifecycle management.
    """

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
        supplier = db.query(Supplier).filter(
            Supplier.supplier_id == supplier_id,
            Supplier.organization_id == org_id,
        ).first()
        if not supplier:
            raise HTTPException(status_code=404, detail="Supplier not found")

        # Validate lines
        if not input.lines:
            raise HTTPException(status_code=400, detail="Purchase order must have at least one line")

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

        po = db.query(PurchaseOrder).filter(
            PurchaseOrder.po_id == po_id,
            PurchaseOrder.organization_id == org_id,
        ).first()

        if not po:
            raise HTTPException(status_code=404, detail="Purchase order not found")

        if po.status != POStatus.DRAFT:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot submit PO in {po.status.value} status"
            )

        po.status = POStatus.PENDING_APPROVAL
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

        po = db.query(PurchaseOrder).filter(
            PurchaseOrder.po_id == po_id,
            PurchaseOrder.organization_id == org_id,
        ).first()

        if not po:
            raise HTTPException(status_code=404, detail="Purchase order not found")

        if po.status != POStatus.PENDING_APPROVAL:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot approve PO in {po.status.value} status"
            )

        # SoD check
        if po.created_by_user_id == approved_by_user_id:
            raise HTTPException(
                status_code=400,
                detail="Approver cannot be the same as creator (Segregation of Duties)"
            )

        po.status = POStatus.APPROVED
        po.approved_by_user_id = approved_by_user_id
        po.approved_at = datetime.now(timezone.utc)
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

        po = db.query(PurchaseOrder).filter(
            PurchaseOrder.po_id == po_id,
            PurchaseOrder.organization_id == org_id,
        ).first()

        if not po:
            raise HTTPException(status_code=404, detail="Purchase order not found")

        if po.status in [POStatus.RECEIVED, POStatus.CLOSED]:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot cancel PO in {po.status.value} status"
            )

        if po.amount_received > 0:
            raise HTTPException(
                status_code=400,
                detail="Cannot cancel PO with received goods"
            )

        po.status = POStatus.CANCELLED
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

        po = db.query(PurchaseOrder).filter(
            PurchaseOrder.po_id == po_id,
            PurchaseOrder.organization_id == org_id,
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
    ) -> PurchaseOrder:
        """
        Update the received amount on a PO (called by GoodsReceiptService).

        Args:
            db: Database session
            po_id: Purchase order ID
            amount_received: Amount received to add

        Returns:
            Updated PurchaseOrder
        """
        po_id = coerce_uuid(po_id)

        po = db.query(PurchaseOrder).filter(
            PurchaseOrder.po_id == po_id,
        ).first()

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
    def get(db: Session, po_id: str) -> Optional[PurchaseOrder]:
        """Get a purchase order by ID."""
        return db.query(PurchaseOrder).filter(
            PurchaseOrder.po_id == coerce_uuid(po_id)
        ).first()

    @staticmethod
    def get_by_number(
        db: Session,
        organization_id: UUID,
        po_number: str,
    ) -> Optional[PurchaseOrder]:
        """Get a purchase order by number."""
        return db.query(PurchaseOrder).filter(
            PurchaseOrder.organization_id == coerce_uuid(organization_id),
            PurchaseOrder.po_number == po_number,
        ).first()

    @staticmethod
    def get_po_lines(db: Session, po_id: str) -> List[PurchaseOrderLine]:
        """Get all lines for a purchase order."""
        return (
            db.query(PurchaseOrderLine)
            .filter(PurchaseOrderLine.po_id == coerce_uuid(po_id))
            .order_by(PurchaseOrderLine.line_number)
            .all()
        )

    @staticmethod
    def list(
        db: Session,
        organization_id: Optional[str] = None,
        supplier_id: Optional[str] = None,
        status: Optional[POStatus] = None,
        from_date: Optional[date] = None,
        to_date: Optional[date] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[PurchaseOrder]:
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
        query = db.query(PurchaseOrder)

        if organization_id:
            query = query.filter(
                PurchaseOrder.organization_id == coerce_uuid(organization_id)
            )

        if supplier_id:
            query = query.filter(
                PurchaseOrder.supplier_id == coerce_uuid(supplier_id)
            )

        if status:
            query = query.filter(PurchaseOrder.status == status)

        if from_date:
            query = query.filter(PurchaseOrder.po_date >= from_date)

        if to_date:
            query = query.filter(PurchaseOrder.po_date <= to_date)

        return query.order_by(PurchaseOrder.po_date.desc()).offset(offset).limit(limit).all()


# Module-level instance
purchase_order_service = PurchaseOrderService()
