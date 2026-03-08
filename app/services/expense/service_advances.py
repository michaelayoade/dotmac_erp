"""Cash advance operations."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select

from app.models.expense import CashAdvance, CashAdvanceStatus
from app.services.common import PaginatedResult, PaginationParams
from app.services.expense.service_common import CashAdvanceNotFoundError, ExpenseServiceError


class ExpenseAdvanceMixin:
    def list_advances(
        self,
        org_id: UUID,
        *,
        employee_id: UUID | None = None,
        status: CashAdvanceStatus | None = None,
        from_date: date | None = None,
        to_date: date | None = None,
        pagination: PaginationParams | None = None,
    ) -> PaginatedResult[CashAdvance]:
        query = select(CashAdvance).where(CashAdvance.organization_id == org_id)
        if employee_id:
            query = query.where(CashAdvance.employee_id == employee_id)
        if status:
            query = query.where(CashAdvance.status == status)
        if from_date:
            query = query.where(CashAdvance.request_date >= from_date)
        if to_date:
            query = query.where(CashAdvance.request_date <= to_date)
        query = query.order_by(CashAdvance.request_date.desc())
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

    def get_advance(self, org_id: UUID, advance_id: UUID) -> CashAdvance:
        advance = self.db.scalar(
            select(CashAdvance).where(
                CashAdvance.advance_id == advance_id,
                CashAdvance.organization_id == org_id,
            )
        )
        if not advance:
            raise CashAdvanceNotFoundError(advance_id)
        return advance

    def create_advance(
        self,
        org_id: UUID,
        *,
        employee_id: UUID | None = None,
        request_date: date,
        purpose: str,
        requested_amount: Decimal,
        currency_code: str = "NGN",
        expected_settlement_date: date | None = None,
        cost_center_id: UUID | None = None,
        advance_account_id: UUID | None = None,
        notes: str | None = None,
    ) -> CashAdvance:
        count = (
            self.db.scalar(
                select(func.count(CashAdvance.advance_id)).where(
                    CashAdvance.organization_id == org_id
                )
            )
            or 0
        )
        advance = CashAdvance(
            organization_id=org_id,
            employee_id=employee_id,
            advance_number=f"ADV-{date.today().year}-{count + 1:05d}",
            request_date=request_date,
            purpose=purpose,
            requested_amount=requested_amount,
            currency_code=currency_code,
            expected_settlement_date=expected_settlement_date,
            cost_center_id=cost_center_id,
            advance_account_id=advance_account_id,
            notes=notes,
            status=CashAdvanceStatus.DRAFT,
            amount_settled=Decimal("0"),
            amount_refunded=Decimal("0"),
        )
        self.db.add(advance)
        self.db.flush()
        return advance

    def update_advance(self, org_id: UUID, advance_id: UUID, **kwargs) -> CashAdvance:
        advance = self.get_advance(org_id, advance_id)
        if advance.status != CashAdvanceStatus.DRAFT:
            raise ExpenseServiceError(
                f"Cannot update advance in {advance.status.value} status"
            )
        for key, value in kwargs.items():
            if value is not None and hasattr(advance, key):
                setattr(advance, key, value)
        self.db.flush()
        return advance

    def delete_advance(self, org_id: UUID, advance_id: UUID) -> None:
        advance = self.get_advance(org_id, advance_id)
        if advance.status != CashAdvanceStatus.DRAFT:
            raise ExpenseServiceError(
                f"Cannot delete advance in {advance.status.value} status"
            )
        self.db.delete(advance)
        self.db.flush()

    def submit_advance(self, org_id: UUID, advance_id: UUID) -> CashAdvance:
        advance = self.get_advance(org_id, advance_id)
        if advance.status != CashAdvanceStatus.DRAFT:
            raise ExpenseServiceError(
                f"Cannot submit advance in {advance.status.value} status"
            )
        advance.status = CashAdvanceStatus.SUBMITTED
        self.db.flush()
        return advance

    def approve_advance(
        self,
        org_id: UUID,
        advance_id: UUID,
        *,
        approver_id: UUID,
        approved_amount: Decimal | None = None,
    ) -> CashAdvance:
        advance = self.get_advance(org_id, advance_id)
        if advance.status != CashAdvanceStatus.SUBMITTED:
            raise ExpenseServiceError(
                f"Cannot approve advance in {advance.status.value} status"
            )
        advance.status = CashAdvanceStatus.APPROVED
        advance.approver_id = approver_id
        advance.approved_on = date.today()
        advance.approved_amount = approved_amount or advance.requested_amount
        self.db.flush()
        return advance

    def reject_advance(
        self,
        org_id: UUID,
        advance_id: UUID,
        *,
        approver_id: UUID | None = None,
        reason: str,
    ) -> CashAdvance:
        advance = self.get_advance(org_id, advance_id)
        if advance.status != CashAdvanceStatus.SUBMITTED:
            raise ExpenseServiceError(
                f"Cannot reject advance in {advance.status.value} status"
            )
        advance.status = CashAdvanceStatus.REJECTED
        advance.approver_id = approver_id
        advance.approved_on = date.today()
        advance.rejection_reason = reason
        self.db.flush()
        return advance

    def disburse_advance(
        self,
        org_id: UUID,
        advance_id: UUID,
        *,
        disbursed_amount: Decimal | None = None,
        disbursement_date: date | None = None,
        payment_reference: str | None = None,
    ) -> CashAdvance:
        advance = self.get_advance(org_id, advance_id)
        if advance.status != CashAdvanceStatus.APPROVED:
            raise ExpenseServiceError(
                f"Cannot disburse advance in {advance.status.value} status"
            )
        advance.status = CashAdvanceStatus.DISBURSED
        advance.disbursed_on = disbursement_date or date.today()
        advance.payment_reference = payment_reference
        self.db.flush()
        return advance

    def record_refund(
        self,
        org_id: UUID,
        advance_id: UUID,
        *,
        refund_amount: Decimal,
        payment_reference: str | None = None,
    ) -> CashAdvance:
        advance = self.get_advance(org_id, advance_id)
        if advance.status != CashAdvanceStatus.DISBURSED:
            raise ExpenseServiceError(
                f"Cannot record refund for advance in {advance.status.value} status"
            )
        advance.amount_refunded += refund_amount
        total_accounted = advance.amount_settled + advance.amount_refunded
        if total_accounted >= (advance.approved_amount or advance.requested_amount):
            advance.status = CashAdvanceStatus.FULLY_SETTLED
            advance.settled_on = date.today()
        self.db.flush()
        return advance

    def settle_advance(
        self,
        org_id: UUID,
        advance_id: UUID,
        *,
        settled_amount: Decimal,
        settlement_date: date | None = None,
        notes: str | None = None,
    ) -> CashAdvance:
        advance = self.get_advance(org_id, advance_id)
        if advance.status != CashAdvanceStatus.DISBURSED:
            raise ExpenseServiceError(
                f"Cannot settle advance in {advance.status.value} status"
            )
        advance.amount_settled = settled_amount
        if notes:
            advance.notes = notes
        total_accounted = advance.amount_settled + advance.amount_refunded
        if total_accounted >= (advance.approved_amount or advance.requested_amount):
            advance.status = CashAdvanceStatus.FULLY_SETTLED
            advance.settled_on = settlement_date or date.today()
        self.db.flush()
        return advance
