"""
TaxReturnService - Tax return preparation and filing.

Manages VAT/GST/Sales tax return preparation, review, and filing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.ifrs.tax.tax_return import (
    TaxReturn,
    TaxReturnStatus,
    TaxReturnType,
)
from app.models.ifrs.tax.tax_period import TaxPeriod, TaxPeriodStatus
from app.models.ifrs.tax.tax_transaction import TaxTransaction, TaxTransactionType
from app.services.common import coerce_uuid
from app.services.response import ListResponseMixin


@dataclass
class TaxReturnInput:
    """Input for creating a tax return."""

    tax_period_id: UUID
    jurisdiction_id: UUID
    return_type: TaxReturnType
    adjustments: Decimal = Decimal("0")


@dataclass
class TaxReturnBoxValue:
    """Value for a specific tax return box."""

    box_number: str
    description: str
    amount: Decimal
    transaction_count: int = 0


class TaxReturnService(ListResponseMixin):
    """
    Service for tax return preparation and filing.

    Handles VAT/GST return preparation, review workflow,
    filing, and payment tracking.
    """

    @staticmethod
    def prepare_return(
        db: Session,
        organization_id: UUID,
        input: TaxReturnInput,
        prepared_by_user_id: UUID,
    ) -> TaxReturn:
        """
        Prepare a tax return from transaction data.

        Args:
            db: Database session
            organization_id: Organization scope
            input: Return input data
            prepared_by_user_id: User preparing the return

        Returns:
            Created TaxReturn
        """
        org_id = coerce_uuid(organization_id)
        period_id = coerce_uuid(input.tax_period_id)
        jurisdiction_id = coerce_uuid(input.jurisdiction_id)
        user_id = coerce_uuid(prepared_by_user_id)

        # Validate tax period exists and is open
        period = db.query(TaxPeriod).filter(
            TaxPeriod.period_id == period_id,
            TaxPeriod.organization_id == org_id,
        ).first()

        if not period:
            raise HTTPException(status_code=404, detail="Tax period not found")

        if period.status != TaxPeriodStatus.OPEN:
            raise HTTPException(
                status_code=400,
                detail=f"Tax period is in {period.status.value} status"
            )

        # Check for existing return
        existing = db.query(TaxReturn).filter(
            TaxReturn.tax_period_id == period_id,
            TaxReturn.organization_id == org_id,
            TaxReturn.return_type == input.return_type,
            TaxReturn.is_amendment == False,
        ).first()

        if existing and existing.status != TaxReturnStatus.DRAFT:
            raise HTTPException(
                status_code=400,
                detail="A return already exists for this period"
            )

        # Calculate totals from transactions
        # Output tax
        output_result = db.query(
            func.sum(TaxTransaction.functional_tax_amount)
        ).filter(
            TaxTransaction.organization_id == org_id,
            TaxTransaction.fiscal_period_id == period.fiscal_period_id,
            TaxTransaction.transaction_type == TaxTransactionType.OUTPUT,
        ).scalar()
        total_output = output_result or Decimal("0")

        # Input tax (recoverable)
        input_result = db.query(
            func.sum(TaxTransaction.recoverable_amount)
        ).filter(
            TaxTransaction.organization_id == org_id,
            TaxTransaction.fiscal_period_id == period.fiscal_period_id,
            TaxTransaction.transaction_type == TaxTransactionType.INPUT,
        ).scalar()
        total_input = input_result or Decimal("0")

        # Net payable
        net_payable = total_output - total_input
        final_amount = net_payable + input.adjustments

        # Build box values
        box_values = TaxReturnService._calculate_box_values(
            db, org_id, period.fiscal_period_id, input.return_type
        )

        # Create or update return
        if existing:
            tax_return = existing
            tax_return.total_output_tax = total_output
            tax_return.total_input_tax = total_input
            tax_return.net_tax_payable = net_payable
            tax_return.adjustments = input.adjustments
            tax_return.final_amount = final_amount
            tax_return.box_values = box_values
            tax_return.prepared_by_user_id = user_id
            tax_return.prepared_at = datetime.now(timezone.utc)
            tax_return.status = TaxReturnStatus.PREPARED
        else:
            tax_return = TaxReturn(
                organization_id=org_id,
                tax_period_id=period_id,
                jurisdiction_id=jurisdiction_id,
                return_type=input.return_type,
                total_output_tax=total_output,
                total_input_tax=total_input,
                net_tax_payable=net_payable,
                adjustments=input.adjustments,
                final_amount=final_amount,
                box_values=box_values,
                status=TaxReturnStatus.PREPARED,
                prepared_by_user_id=user_id,
                prepared_at=datetime.now(timezone.utc),
            )
            db.add(tax_return)

        db.commit()
        db.refresh(tax_return)

        return tax_return

    @staticmethod
    def _calculate_box_values(
        db: Session,
        organization_id: UUID,
        fiscal_period_id: UUID,
        return_type: TaxReturnType,
    ) -> dict[str, Any]:
        """Calculate values for tax return boxes."""
        # Group transactions by return box
        box_data = db.query(
            TaxTransaction.tax_return_box,
            func.sum(TaxTransaction.functional_base_amount).label("base_amount"),
            func.sum(TaxTransaction.functional_tax_amount).label("tax_amount"),
            func.count(TaxTransaction.transaction_id).label("count"),
        ).filter(
            TaxTransaction.organization_id == organization_id,
            TaxTransaction.fiscal_period_id == fiscal_period_id,
            TaxTransaction.tax_return_box.isnot(None),
        ).group_by(TaxTransaction.tax_return_box).all()

        boxes = {}
        for row in box_data:
            boxes[row.tax_return_box] = {
                "base_amount": str(row.base_amount or Decimal("0")),
                "tax_amount": str(row.tax_amount or Decimal("0")),
                "transaction_count": row.count,
            }

        return boxes

    @staticmethod
    def review_return(
        db: Session,
        organization_id: UUID,
        return_id: UUID,
        reviewed_by_user_id: UUID,
    ) -> TaxReturn:
        """
        Mark a return as reviewed.

        Args:
            db: Database session
            organization_id: Organization scope
            return_id: Return to review
            reviewed_by_user_id: User reviewing

        Returns:
            Updated TaxReturn
        """
        org_id = coerce_uuid(organization_id)
        return_id = coerce_uuid(return_id)
        user_id = coerce_uuid(reviewed_by_user_id)

        tax_return = db.query(TaxReturn).filter(
            TaxReturn.return_id == return_id,
            TaxReturn.organization_id == org_id,
        ).first()

        if not tax_return:
            raise HTTPException(status_code=404, detail="Tax return not found")

        if tax_return.status != TaxReturnStatus.PREPARED:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot review return in {tax_return.status.value} status"
            )

        # SoD check
        if tax_return.prepared_by_user_id == user_id:
            raise HTTPException(
                status_code=400,
                detail="Reviewer cannot be the same as preparer (Segregation of Duties)"
            )

        tax_return.status = TaxReturnStatus.REVIEWED
        tax_return.reviewed_by_user_id = user_id
        tax_return.reviewed_at = datetime.now(timezone.utc)

        db.commit()
        db.refresh(tax_return)

        return tax_return

    @staticmethod
    def file_return(
        db: Session,
        organization_id: UUID,
        return_id: UUID,
        filed_by_user_id: UUID,
        filing_reference: Optional[str] = None,
    ) -> TaxReturn:
        """
        File a tax return.

        Args:
            db: Database session
            organization_id: Organization scope
            return_id: Return to file
            filed_by_user_id: User filing
            filing_reference: External filing reference

        Returns:
            Updated TaxReturn
        """
        org_id = coerce_uuid(organization_id)
        return_id = coerce_uuid(return_id)
        user_id = coerce_uuid(filed_by_user_id)

        tax_return = db.query(TaxReturn).filter(
            TaxReturn.return_id == return_id,
            TaxReturn.organization_id == org_id,
        ).first()

        if not tax_return:
            raise HTTPException(status_code=404, detail="Tax return not found")

        if tax_return.status not in [TaxReturnStatus.PREPARED, TaxReturnStatus.REVIEWED]:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot file return in {tax_return.status.value} status"
            )

        tax_return.status = TaxReturnStatus.FILED
        tax_return.filed_date = date.today()
        tax_return.filed_by_user_id = user_id
        tax_return.filing_reference = filing_reference

        # Update tax period status
        period = db.query(TaxPeriod).filter(
            TaxPeriod.period_id == tax_return.tax_period_id
        ).first()
        if period:
            period.status = TaxPeriodStatus.FILED

        # Mark transactions as included in return
        db.query(TaxTransaction).filter(
            TaxTransaction.organization_id == org_id,
            TaxTransaction.fiscal_period_id == period.fiscal_period_id if period else None,
            TaxTransaction.is_included_in_return == False,
        ).update({
            TaxTransaction.is_included_in_return: True,
            TaxTransaction.tax_return_period: tax_return.return_reference or str(return_id),
        })

        db.commit()
        db.refresh(tax_return)

        return tax_return

    @staticmethod
    def record_payment(
        db: Session,
        organization_id: UUID,
        return_id: UUID,
        payment_date: date,
        payment_reference: Optional[str] = None,
        journal_entry_id: Optional[UUID] = None,
    ) -> TaxReturn:
        """
        Record payment for a tax return.

        Args:
            db: Database session
            organization_id: Organization scope
            return_id: Return being paid
            payment_date: Date of payment
            payment_reference: Payment reference
            journal_entry_id: Related journal entry

        Returns:
            Updated TaxReturn
        """
        org_id = coerce_uuid(organization_id)
        return_id = coerce_uuid(return_id)

        tax_return = db.query(TaxReturn).filter(
            TaxReturn.return_id == return_id,
            TaxReturn.organization_id == org_id,
        ).first()

        if not tax_return:
            raise HTTPException(status_code=404, detail="Tax return not found")

        if tax_return.status != TaxReturnStatus.FILED:
            raise HTTPException(
                status_code=400,
                detail="Return must be filed before payment can be recorded"
            )

        tax_return.is_paid = True
        tax_return.payment_date = payment_date
        tax_return.payment_reference = payment_reference
        tax_return.payment_journal_entry_id = coerce_uuid(journal_entry_id) if journal_entry_id else None

        # Update tax period status
        period = db.query(TaxPeriod).filter(
            TaxPeriod.period_id == tax_return.tax_period_id
        ).first()
        if period:
            period.status = TaxPeriodStatus.PAID

        db.commit()
        db.refresh(tax_return)

        return tax_return

    @staticmethod
    def create_amendment(
        db: Session,
        organization_id: UUID,
        original_return_id: UUID,
        amendment_reason: str,
        adjustments: Decimal,
        prepared_by_user_id: UUID,
    ) -> TaxReturn:
        """
        Create an amended tax return.

        Args:
            db: Database session
            organization_id: Organization scope
            original_return_id: Return being amended
            amendment_reason: Reason for amendment
            adjustments: Amount adjustments
            prepared_by_user_id: User preparing amendment

        Returns:
            Created amended TaxReturn
        """
        org_id = coerce_uuid(organization_id)
        original_id = coerce_uuid(original_return_id)
        user_id = coerce_uuid(prepared_by_user_id)

        original = db.query(TaxReturn).filter(
            TaxReturn.return_id == original_id,
            TaxReturn.organization_id == org_id,
        ).first()

        if not original:
            raise HTTPException(status_code=404, detail="Original return not found")

        if original.status != TaxReturnStatus.FILED:
            raise HTTPException(
                status_code=400,
                detail="Can only amend filed returns"
            )

        # Mark original as amended
        original.status = TaxReturnStatus.AMENDED

        # Create amendment
        amendment = TaxReturn(
            organization_id=org_id,
            tax_period_id=original.tax_period_id,
            jurisdiction_id=original.jurisdiction_id,
            return_type=original.return_type,
            total_output_tax=original.total_output_tax,
            total_input_tax=original.total_input_tax,
            net_tax_payable=original.net_tax_payable,
            adjustments=adjustments,
            final_amount=original.net_tax_payable + adjustments,
            box_values=original.box_values,
            status=TaxReturnStatus.DRAFT,
            is_amendment=True,
            original_return_id=original_id,
            amendment_reason=amendment_reason,
            prepared_by_user_id=user_id,
            prepared_at=datetime.now(timezone.utc),
        )

        db.add(amendment)
        db.commit()
        db.refresh(amendment)

        return amendment

    @staticmethod
    def get_box_values(
        db: Session,
        organization_id: UUID,
        return_id: UUID,
    ) -> list[TaxReturnBoxValue]:
        """
        Get formatted box values for a return.

        Args:
            db: Database session
            organization_id: Organization scope
            return_id: Return ID

        Returns:
            List of TaxReturnBoxValue objects
        """
        org_id = coerce_uuid(organization_id)
        return_id = coerce_uuid(return_id)

        tax_return = db.query(TaxReturn).filter(
            TaxReturn.return_id == return_id,
            TaxReturn.organization_id == org_id,
        ).first()

        if not tax_return:
            raise HTTPException(status_code=404, detail="Tax return not found")

        if not tax_return.box_values:
            return []

        result = []
        for box_num, data in tax_return.box_values.items():
            result.append(TaxReturnBoxValue(
                box_number=box_num,
                description=f"Box {box_num}",
                amount=Decimal(data.get("tax_amount", "0")),
                transaction_count=data.get("transaction_count", 0),
            ))

        return sorted(result, key=lambda x: x.box_number)

    @staticmethod
    def get(db: Session, return_id: str) -> Optional[TaxReturn]:
        """Get a tax return by ID."""
        return db.query(TaxReturn).filter(
            TaxReturn.return_id == coerce_uuid(return_id)
        ).first()

    @staticmethod
    def list(
        db: Session,
        organization_id: Optional[str] = None,
        tax_period_id: Optional[str] = None,
        jurisdiction_id: Optional[str] = None,
        return_type: Optional[TaxReturnType] = None,
        status: Optional[TaxReturnStatus] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[TaxReturn]:
        """
        List tax returns with filters.

        Args:
            db: Database session
            organization_id: Filter by organization
            tax_period_id: Filter by period
            jurisdiction_id: Filter by jurisdiction
            return_type: Filter by type
            status: Filter by status
            limit: Maximum results
            offset: Pagination offset

        Returns:
            List of TaxReturn objects
        """
        query = db.query(TaxReturn)

        if organization_id:
            query = query.filter(
                TaxReturn.organization_id == coerce_uuid(organization_id)
            )

        if tax_period_id:
            query = query.filter(
                TaxReturn.tax_period_id == coerce_uuid(tax_period_id)
            )

        if jurisdiction_id:
            query = query.filter(
                TaxReturn.jurisdiction_id == coerce_uuid(jurisdiction_id)
            )

        if return_type:
            query = query.filter(TaxReturn.return_type == return_type)

        if status:
            query = query.filter(TaxReturn.status == status)

        return query.order_by(TaxReturn.created_at.desc()).offset(offset).limit(limit).all()


# Module-level instance
tax_return_service = TaxReturnService()
