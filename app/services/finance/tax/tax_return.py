"""
TaxReturnService - Tax return preparation and filing.

Manages VAT/GST/Sales tax return preparation, review, and filing.
"""

from __future__ import annotations

import builtins
import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.models.finance.tax.tax_period import TaxPeriod, TaxPeriodStatus
from app.models.finance.tax.tax_return import (
    TaxReturn,
    TaxReturnStatus,
    TaxReturnType,
)
from app.models.finance.tax.tax_transaction import TaxTransaction, TaxTransactionType
from app.services.common import coerce_uuid
from app.services.response import ListResponseMixin

logger = logging.getLogger(__name__)


@dataclass
class TaxReturnInput:
    """Input for creating a tax return."""

    tax_period_id: UUID
    jurisdiction_id: UUID
    return_type: TaxReturnType
    adjustments: Decimal = Decimal("0")


@dataclass
class TaxReturnUpdateInput:
    """Input for updating an existing tax return."""

    return_type: TaxReturnType | None = None
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
        period = db.scalar(
            select(TaxPeriod).where(
                TaxPeriod.period_id == period_id,
                TaxPeriod.organization_id == org_id,
            )
        )

        if not period:
            raise HTTPException(status_code=404, detail="Tax period not found")

        if period.status != TaxPeriodStatus.OPEN:
            raise HTTPException(
                status_code=400, detail=f"Tax period is in {period.status.value} status"
            )

        # Check for existing return
        existing = db.scalar(
            select(TaxReturn).where(
                TaxReturn.tax_period_id == period_id,
                TaxReturn.organization_id == org_id,
                TaxReturn.return_type == input.return_type,
                TaxReturn.is_amendment.is_(False),
            )
        )

        if existing and existing.status != TaxReturnStatus.DRAFT:
            raise HTTPException(
                status_code=400, detail="A return already exists for this period"
            )

        # Calculate totals from transactions
        # Output tax
        output_result = db.scalar(
            select(func.sum(TaxTransaction.functional_tax_amount)).where(
                TaxTransaction.organization_id == org_id,
                TaxTransaction.fiscal_period_id == period.fiscal_period_id,
                TaxTransaction.transaction_type == TaxTransactionType.OUTPUT,
            )
        )
        total_output = output_result or Decimal("0")

        # Input tax (recoverable)
        input_result = db.scalar(
            select(func.sum(TaxTransaction.recoverable_amount)).where(
                TaxTransaction.organization_id == org_id,
                TaxTransaction.fiscal_period_id == period.fiscal_period_id,
                TaxTransaction.transaction_type == TaxTransactionType.INPUT,
            )
        )
        total_input = input_result or Decimal("0")

        # Net payable
        net_payable = total_output - total_input
        final_amount = net_payable + input.adjustments

        if period.fiscal_period_id is None:
            raise ValueError("Tax period is missing a fiscal period reference")
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
            tax_return.prepared_at = datetime.now(UTC)
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
                prepared_at=datetime.now(UTC),
            )
            db.add(tax_return)

        db.commit()
        db.refresh(tax_return)

        return tax_return

    @staticmethod
    def update_return(
        db: Session,
        organization_id: UUID,
        return_id: UUID,
        input: TaxReturnUpdateInput,
        updated_by_user_id: UUID,
    ) -> TaxReturn:
        """Update a draft/prepared tax return and recalculate totals."""
        org_id = coerce_uuid(organization_id)
        ret_id = coerce_uuid(return_id)
        user_id = coerce_uuid(updated_by_user_id)

        tax_return = db.scalar(
            select(TaxReturn).where(
                TaxReturn.return_id == ret_id,
                TaxReturn.organization_id == org_id,
            )
        )
        if not tax_return:
            raise HTTPException(status_code=404, detail="Tax return not found")

        if tax_return.status not in {TaxReturnStatus.DRAFT, TaxReturnStatus.PREPARED}:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot edit return in {tax_return.status.value} status",
            )

        period = db.scalar(
            select(TaxPeriod).where(
                TaxPeriod.period_id == tax_return.tax_period_id,
                TaxPeriod.organization_id == org_id,
            )
        )
        if not period:
            raise HTTPException(status_code=404, detail="Tax period not found")
        if period.status != TaxPeriodStatus.OPEN:
            raise HTTPException(
                status_code=400, detail=f"Tax period is in {period.status.value} status"
            )

        return_type = input.return_type or tax_return.return_type

        existing = db.scalar(
            select(TaxReturn).where(
                TaxReturn.tax_period_id == tax_return.tax_period_id,
                TaxReturn.organization_id == org_id,
                TaxReturn.return_type == return_type,
                TaxReturn.is_amendment.is_(False),
                TaxReturn.return_id != ret_id,
            )
        )
        if existing and existing.status != TaxReturnStatus.DRAFT:
            raise HTTPException(
                status_code=400, detail="A return already exists for this period"
            )

        output_result = db.scalar(
            select(func.sum(TaxTransaction.functional_tax_amount)).where(
                TaxTransaction.organization_id == org_id,
                TaxTransaction.fiscal_period_id == period.fiscal_period_id,
                TaxTransaction.transaction_type == TaxTransactionType.OUTPUT,
            )
        )
        total_output = output_result or Decimal("0")

        input_result = db.scalar(
            select(func.sum(TaxTransaction.recoverable_amount)).where(
                TaxTransaction.organization_id == org_id,
                TaxTransaction.fiscal_period_id == period.fiscal_period_id,
                TaxTransaction.transaction_type == TaxTransactionType.INPUT,
            )
        )
        total_input = input_result or Decimal("0")

        net_payable = total_output - total_input
        final_amount = net_payable + input.adjustments

        if period.fiscal_period_id is None:
            raise ValueError("Tax period is missing a fiscal period reference")

        box_values = TaxReturnService._calculate_box_values(
            db, org_id, period.fiscal_period_id, return_type
        )

        tax_return.return_type = return_type
        tax_return.total_output_tax = total_output
        tax_return.total_input_tax = total_input
        tax_return.net_tax_payable = net_payable
        tax_return.adjustments = input.adjustments
        tax_return.final_amount = final_amount
        tax_return.box_values = box_values
        tax_return.prepared_by_user_id = user_id
        tax_return.prepared_at = datetime.now(UTC)
        tax_return.status = TaxReturnStatus.PREPARED

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
        box_data = db.execute(
            select(
                TaxTransaction.tax_return_box,
                func.sum(TaxTransaction.functional_base_amount).label("base_amount"),
                func.sum(TaxTransaction.functional_tax_amount).label("tax_amount"),
                func.count(TaxTransaction.transaction_id).label("count"),
            )
            .where(
                TaxTransaction.organization_id == organization_id,
                TaxTransaction.fiscal_period_id == fiscal_period_id,
                TaxTransaction.tax_return_box.isnot(None),
            )
            .group_by(TaxTransaction.tax_return_box)
        ).all()

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

        tax_return = db.scalar(
            select(TaxReturn).where(
                TaxReturn.return_id == return_id,
                TaxReturn.organization_id == org_id,
            )
        )

        if not tax_return:
            raise HTTPException(status_code=404, detail="Tax return not found")

        if tax_return.status != TaxReturnStatus.PREPARED:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot review return in {tax_return.status.value} status",
            )

        # SoD check
        if tax_return.prepared_by_user_id == user_id:
            raise HTTPException(
                status_code=400,
                detail="Reviewer cannot be the same as preparer (Segregation of Duties)",
            )

        tax_return.status = TaxReturnStatus.REVIEWED
        tax_return.reviewed_by_user_id = user_id
        tax_return.reviewed_at = datetime.now(UTC)

        db.commit()
        db.refresh(tax_return)

        return tax_return

    @staticmethod
    def file_return(
        db: Session,
        organization_id: UUID,
        return_id: UUID,
        filed_by_user_id: UUID,
        filing_reference: str | None = None,
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

        tax_return = db.scalar(
            select(TaxReturn).where(
                TaxReturn.return_id == return_id,
                TaxReturn.organization_id == org_id,
            )
        )

        if not tax_return:
            raise HTTPException(status_code=404, detail="Tax return not found")

        if tax_return.status not in [
            TaxReturnStatus.PREPARED,
            TaxReturnStatus.REVIEWED,
        ]:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot file return in {tax_return.status.value} status",
            )

        tax_return.status = TaxReturnStatus.FILED
        tax_return.filed_date = date.today()
        tax_return.filed_by_user_id = user_id
        tax_return.filing_reference = filing_reference

        # Update tax period status
        period = db.scalar(
            select(TaxPeriod).where(TaxPeriod.period_id == tax_return.tax_period_id)
        )
        if period:
            period.status = TaxPeriodStatus.FILED

        # Mark transactions as included in return
        if period and period.fiscal_period_id is not None:
            db.execute(
                update(TaxTransaction)
                .where(
                    TaxTransaction.organization_id == org_id,
                    TaxTransaction.fiscal_period_id == period.fiscal_period_id,
                    TaxTransaction.is_included_in_return.is_(False),
                )
                .values(
                    is_included_in_return=True,
                    tax_return_period=tax_return.return_reference or str(return_id),
                )
            )

        db.commit()
        db.refresh(tax_return)

        return tax_return

    @staticmethod
    def record_payment(
        db: Session,
        organization_id: UUID,
        return_id: UUID,
        payment_date: date,
        payment_reference: str | None = None,
        journal_entry_id: UUID | None = None,
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

        tax_return = db.scalar(
            select(TaxReturn).where(
                TaxReturn.return_id == return_id,
                TaxReturn.organization_id == org_id,
            )
        )

        if not tax_return:
            raise HTTPException(status_code=404, detail="Tax return not found")

        if tax_return.status != TaxReturnStatus.FILED:
            raise HTTPException(
                status_code=400,
                detail="Return must be filed before payment can be recorded",
            )

        tax_return.is_paid = True
        tax_return.payment_date = payment_date
        tax_return.payment_reference = payment_reference
        tax_return.payment_journal_entry_id = (
            coerce_uuid(journal_entry_id) if journal_entry_id else None
        )

        # Update tax period status
        period = db.scalar(
            select(TaxPeriod).where(TaxPeriod.period_id == tax_return.tax_period_id)
        )
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

        original = db.scalar(
            select(TaxReturn).where(
                TaxReturn.return_id == original_id,
                TaxReturn.organization_id == org_id,
            )
        )

        if not original:
            raise HTTPException(status_code=404, detail="Original return not found")

        if original.status != TaxReturnStatus.FILED:
            raise HTTPException(status_code=400, detail="Can only amend filed returns")

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
            prepared_at=datetime.now(UTC),
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

        tax_return = db.scalar(
            select(TaxReturn).where(
                TaxReturn.return_id == return_id,
                TaxReturn.organization_id == org_id,
            )
        )

        if not tax_return:
            raise HTTPException(status_code=404, detail="Tax return not found")

        if not tax_return.box_values:
            return []

        result = []
        for box_num, data in tax_return.box_values.items():
            result.append(
                TaxReturnBoxValue(
                    box_number=box_num,
                    description=f"Box {box_num}",
                    amount=Decimal(data.get("tax_amount", "0")),
                    transaction_count=data.get("transaction_count", 0),
                )
            )

        return sorted(result, key=lambda x: x.box_number)

    @staticmethod
    def recalculate(
        db: Session,
        organization_id: UUID,
        return_id: UUID,
    ) -> TaxReturn:
        """
        Recalculate a draft return from current transaction data.

        Args:
            db: Database session
            organization_id: Organization scope
            return_id: Return to recalculate

        Returns:
            Updated TaxReturn
        """
        org_id = coerce_uuid(organization_id)
        ret_id = coerce_uuid(return_id)

        tax_return = db.scalar(
            select(TaxReturn).where(
                TaxReturn.return_id == ret_id,
                TaxReturn.organization_id == org_id,
            )
        )

        if not tax_return:
            raise HTTPException(status_code=404, detail="Tax return not found")

        if tax_return.status not in [TaxReturnStatus.DRAFT, TaxReturnStatus.PREPARED]:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot recalculate return in {tax_return.status.value} status",
            )

        # Get fiscal period for this tax period
        period = db.scalar(
            select(TaxPeriod).where(TaxPeriod.period_id == tax_return.tax_period_id)
        )

        if not period:
            raise HTTPException(status_code=404, detail="Tax period not found")

        # Recalculate output tax
        output_result = db.scalar(
            select(func.sum(TaxTransaction.functional_tax_amount)).where(
                TaxTransaction.organization_id == org_id,
                TaxTransaction.fiscal_period_id == period.fiscal_period_id,
                TaxTransaction.transaction_type == TaxTransactionType.OUTPUT,
            )
        )
        total_output = output_result or Decimal("0")

        # Recalculate input tax (recoverable)
        input_result = db.scalar(
            select(func.sum(TaxTransaction.recoverable_amount)).where(
                TaxTransaction.organization_id == org_id,
                TaxTransaction.fiscal_period_id == period.fiscal_period_id,
                TaxTransaction.transaction_type == TaxTransactionType.INPUT,
            )
        )
        total_input = input_result or Decimal("0")

        # Update return
        tax_return.total_output_tax = total_output
        tax_return.total_input_tax = total_input
        tax_return.net_tax_payable = total_output - total_input
        tax_return.final_amount = tax_return.net_tax_payable + tax_return.adjustments

        # Recalculate box values
        if period.fiscal_period_id is None:
            raise ValueError("Tax period is missing a fiscal period reference")
        tax_return.box_values = TaxReturnService._calculate_box_values(
            db, org_id, period.fiscal_period_id, tax_return.return_type
        )

        db.commit()
        db.refresh(tax_return)

        return tax_return

    @staticmethod
    def get_return_transactions(
        db: Session,
        organization_id: UUID,
        return_id: UUID,
        page: int = 1,
        limit: int = 50,
    ) -> tuple[list[TaxTransaction], int]:
        """
        Get transactions that are/would be included in a return.

        Args:
            db: Database session
            organization_id: Organization scope
            return_id: Return ID
            page: Page number (1-based)
            limit: Results per page

        Returns:
            Tuple of (transactions list, total count)
        """
        org_id = coerce_uuid(organization_id)
        ret_id = coerce_uuid(return_id)

        tax_return = db.scalar(
            select(TaxReturn).where(
                TaxReturn.return_id == ret_id,
                TaxReturn.organization_id == org_id,
            )
        )

        if not tax_return:
            raise HTTPException(status_code=404, detail="Tax return not found")

        # Get fiscal period
        period = db.scalar(
            select(TaxPeriod).where(TaxPeriod.period_id == tax_return.tax_period_id)
        )

        if not period:
            return [], 0

        # Get total count
        total_count = (
            db.scalar(
                select(func.count(TaxTransaction.transaction_id)).where(
                    TaxTransaction.organization_id == org_id,
                    TaxTransaction.fiscal_period_id == period.fiscal_period_id,
                )
            )
            or 0
        )

        # Get paginated results
        offset = (page - 1) * limit
        transactions = list(
            db.scalars(
                select(TaxTransaction)
                .where(
                    TaxTransaction.organization_id == org_id,
                    TaxTransaction.fiscal_period_id == period.fiscal_period_id,
                )
                .order_by(TaxTransaction.transaction_date.desc())
                .offset(offset)
                .limit(limit)
            ).all()
        )

        return transactions, total_count

    @staticmethod
    def get_unreported_transaction_count(
        db: Session,
        organization_id: UUID,
        tax_period_id: UUID,
    ) -> dict:
        """
        Get count and totals of unreported transactions for a period.

        Args:
            db: Database session
            organization_id: Organization scope
            tax_period_id: Tax period ID

        Returns:
            Dict with transaction_count, output_total, input_total
        """
        org_id = coerce_uuid(organization_id)
        period_id = coerce_uuid(tax_period_id)

        # Get fiscal period
        period = db.scalar(
            select(TaxPeriod).where(
                TaxPeriod.period_id == period_id,
                TaxPeriod.organization_id == org_id,
            )
        )

        if not period:
            return {
                "transaction_count": 0,
                "output_total": Decimal("0"),
                "input_total": Decimal("0"),
            }

        # Get unreported transactions count
        count = (
            db.scalar(
                select(func.count(TaxTransaction.transaction_id)).where(
                    TaxTransaction.organization_id == org_id,
                    TaxTransaction.fiscal_period_id == period.fiscal_period_id,
                    TaxTransaction.is_included_in_return.is_(False),
                )
            )
            or 0
        )

        # Output tax total
        output_result = db.scalar(
            select(func.sum(TaxTransaction.functional_tax_amount)).where(
                TaxTransaction.organization_id == org_id,
                TaxTransaction.fiscal_period_id == period.fiscal_period_id,
                TaxTransaction.transaction_type == TaxTransactionType.OUTPUT,
                TaxTransaction.is_included_in_return.is_(False),
            )
        )

        # Input tax total (recoverable)
        input_result = db.scalar(
            select(func.sum(TaxTransaction.recoverable_amount)).where(
                TaxTransaction.organization_id == org_id,
                TaxTransaction.fiscal_period_id == period.fiscal_period_id,
                TaxTransaction.transaction_type == TaxTransactionType.INPUT,
                TaxTransaction.is_included_in_return.is_(False),
            )
        )

        return {
            "transaction_count": count,
            "output_total": output_result or Decimal("0"),
            "input_total": input_result or Decimal("0"),
        }

    @staticmethod
    def get(
        db: Session,
        return_id: str,
        organization_id: UUID | None = None,
    ) -> TaxReturn | None:
        """Get a tax return by ID."""
        tax_return = db.scalar(
            select(TaxReturn).where(TaxReturn.return_id == coerce_uuid(return_id))
        )
        if not tax_return:
            return None
        if organization_id is not None and tax_return.organization_id != coerce_uuid(
            organization_id
        ):
            return None
        return tax_return

    @staticmethod
    def list(
        db: Session,
        organization_id: str | None = None,
        tax_period_id: str | None = None,
        jurisdiction_id: str | None = None,
        return_type: TaxReturnType | None = None,
        status: TaxReturnStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> builtins.list[TaxReturn]:
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
        stmt = select(TaxReturn)

        if organization_id:
            stmt = stmt.where(TaxReturn.organization_id == coerce_uuid(organization_id))

        if tax_period_id:
            stmt = stmt.where(TaxReturn.tax_period_id == coerce_uuid(tax_period_id))

        if jurisdiction_id:
            stmt = stmt.where(TaxReturn.jurisdiction_id == coerce_uuid(jurisdiction_id))

        if return_type:
            stmt = stmt.where(TaxReturn.return_type == return_type)

        if status:
            stmt = stmt.where(TaxReturn.status == status)

        return list(
            db.scalars(
                stmt.order_by(TaxReturn.created_at.desc()).offset(offset).limit(limit)
            ).all()
        )


# Module-level instance
tax_return_service = TaxReturnService()
