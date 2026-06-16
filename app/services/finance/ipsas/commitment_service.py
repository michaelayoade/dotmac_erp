"""
Commitment Service - IPSAS encumbrance/commitment lifecycle.

Manages commitment (encumbrance) tracking: PENDING -> COMMITTED ->
OBLIGATED -> PARTIALLY_PAID -> EXPENDED.
"""

import logging
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.finance.ipsas.appropriation import Appropriation
from app.models.finance.ipsas.commitment import Commitment
from app.models.finance.ipsas.enums import (
    AppropriationStatus,
    CommitmentStatus,
    CommitmentType,
    FundStatus,
)
from app.models.finance.ipsas.fund import Fund
from app.services.common import NotFoundError, ValidationError
from app.services.finance.ipsas.available_balance_service import (
    AvailableBalanceService,
)

logger = logging.getLogger(__name__)


class CommitmentService:
    """Service for managing IPSAS commitments (encumbrances)."""

    def __init__(self, db: Session):
        self.db = db

    def _flush_and_refresh(self, commitment: Commitment) -> None:
        # Flush (not commit): the request dependency owns the transaction;
        # committing here would also drop the SET LOCAL RLS GUC mid-request.
        self.db.flush()
        self.db.refresh(commitment)

    def list_for_org(
        self,
        organization_id: UUID,
        *,
        fund_id: UUID | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Commitment]:
        """List commitments with optional filters."""
        stmt = select(Commitment).where(Commitment.organization_id == organization_id)

        if fund_id:
            stmt = stmt.where(Commitment.fund_id == fund_id)
        if status:
            stmt = stmt.where(Commitment.status == CommitmentStatus(status))

        stmt = (
            stmt.order_by(Commitment.commitment_date.desc()).offset(offset).limit(limit)
        )
        return list(self.db.scalars(stmt).all())

    def get_or_404(
        self, commitment_id: UUID, organization_id: UUID | None = None
    ) -> Commitment:
        """Get a commitment by ID or raise NotFoundError.

        If organization_id is provided, also verifies tenant ownership.
        """
        commitment = self.db.get(Commitment, commitment_id)
        if not commitment:
            raise NotFoundError(f"Commitment {commitment_id} not found")
        if organization_id and commitment.organization_id != organization_id:
            raise NotFoundError(f"Commitment {commitment_id} not found")
        return commitment

    def _validate_new_commitment(
        self,
        *,
        organization_id: UUID,
        fund_id: UUID,
        appropriation_id: UUID | None,
        amount: Decimal,
    ) -> None:
        """Guard a new commitment: positive amount, active fund, and (when an
        appropriation is linked) an approved appropriation with enough available
        budget. IPSAS encumbrance control is a hard ceiling — a commitment may
        not exceed the appropriation's available balance.
        """
        if amount is None or amount <= 0:
            raise ValidationError("Committed amount must be greater than zero.")

        fund = self.db.get(Fund, fund_id)
        if not fund or fund.organization_id != organization_id:
            raise NotFoundError(f"Fund {fund_id} not found")
        if fund.status != FundStatus.ACTIVE:
            raise ValidationError(f"Cannot commit against a {fund.status.value} fund.")

        if appropriation_id is not None:
            approp = self.db.get(Appropriation, appropriation_id)
            if not approp or approp.organization_id != organization_id:
                raise NotFoundError(f"Appropriation {appropriation_id} not found")
            if approp.status not in (
                AppropriationStatus.APPROVED,
                AppropriationStatus.ACTIVE,
            ):
                raise ValidationError(
                    f"Cannot commit against a {approp.status.value} appropriation."
                )
            balance = AvailableBalanceService(self.db).calculate(
                organization_id, appropriation_id=appropriation_id
            )
            if amount > balance.available_balance:
                raise ValidationError(
                    f"Commitment {amount} exceeds the appropriation's available "
                    f"balance {balance.available_balance}."
                )

    def create(
        self,
        *,
        organization_id: UUID,
        commitment_number: str,
        commitment_type: str,
        fund_id: UUID,
        account_id: UUID,
        fiscal_year_id: UUID,
        fiscal_period_id: UUID,
        committed_amount: Decimal,
        currency_code: str,
        created_by_user_id: UUID,
        appropriation_id: UUID | None = None,
    ) -> Commitment:
        """Create a generic commitment (not tied to a specific source document)."""
        self._validate_new_commitment(
            organization_id=organization_id,
            fund_id=fund_id,
            appropriation_id=appropriation_id,
            amount=committed_amount,
        )
        commitment = Commitment(
            organization_id=organization_id,
            commitment_number=commitment_number,
            commitment_type=CommitmentType(commitment_type),
            status=CommitmentStatus.COMMITTED,
            fund_id=fund_id,
            appropriation_id=appropriation_id,
            source_type="manual",
            source_id=organization_id,  # Use org_id as placeholder source
            account_id=account_id,
            fiscal_year_id=fiscal_year_id,
            fiscal_period_id=fiscal_period_id,
            currency_code=currency_code,
            committed_amount=committed_amount,
            commitment_date=date.today(),
            created_by_user_id=created_by_user_id,
        )
        self.db.add(commitment)
        self.db.flush()

        logger.info(
            "Created commitment %s: %s %s",
            commitment_number,
            currency_code,
            committed_amount,
        )
        self._flush_and_refresh(commitment)
        return commitment

    def create_commitment_from_po(
        self,
        *,
        organization_id: UUID,
        po_id: UUID,
        fund_id: UUID,
        account_id: UUID,
        fiscal_year_id: UUID,
        fiscal_period_id: UUID,
        amount: Decimal,
        currency_code: str,
        created_by_user_id: UUID,
        commitment_number: str,
        appropriation_id: UUID | None = None,
    ) -> Commitment:
        """Create a commitment from a purchase order."""
        self._validate_new_commitment(
            organization_id=organization_id,
            fund_id=fund_id,
            appropriation_id=appropriation_id,
            amount=amount,
        )
        commitment = Commitment(
            organization_id=organization_id,
            commitment_number=commitment_number,
            commitment_type=CommitmentType.PURCHASE_ORDER,
            status=CommitmentStatus.COMMITTED,
            fund_id=fund_id,
            appropriation_id=appropriation_id,
            source_type="purchase_order",
            source_id=po_id,
            account_id=account_id,
            fiscal_year_id=fiscal_year_id,
            fiscal_period_id=fiscal_period_id,
            currency_code=currency_code,
            committed_amount=amount,
            commitment_date=date.today(),
            created_by_user_id=created_by_user_id,
        )
        self.db.add(commitment)
        self.db.flush()

        logger.info(
            "Created commitment %s from PO %s: %s %s",
            commitment_number,
            po_id,
            currency_code,
            amount,
        )
        self._flush_and_refresh(commitment)
        return commitment

    def create_commitment_from_contract(
        self,
        *,
        organization_id: UUID,
        contract_id: UUID,
        fund_id: UUID,
        account_id: UUID,
        fiscal_year_id: UUID,
        fiscal_period_id: UUID,
        amount: Decimal,
        currency_code: str,
        created_by_user_id: UUID,
        commitment_number: str,
        appropriation_id: UUID | None = None,
    ) -> Commitment:
        """Create a commitment from a procurement contract."""
        self._validate_new_commitment(
            organization_id=organization_id,
            fund_id=fund_id,
            appropriation_id=appropriation_id,
            amount=amount,
        )
        commitment = Commitment(
            organization_id=organization_id,
            commitment_number=commitment_number,
            commitment_type=CommitmentType.CONTRACT,
            status=CommitmentStatus.COMMITTED,
            fund_id=fund_id,
            appropriation_id=appropriation_id,
            source_type="contract",
            source_id=contract_id,
            account_id=account_id,
            fiscal_year_id=fiscal_year_id,
            fiscal_period_id=fiscal_period_id,
            currency_code=currency_code,
            committed_amount=amount,
            commitment_date=date.today(),
            created_by_user_id=created_by_user_id,
        )
        self.db.add(commitment)
        self.db.flush()

        logger.info(
            "Created commitment %s from contract %s: %s %s",
            commitment_number,
            contract_id,
            currency_code,
            amount,
        )
        self._flush_and_refresh(commitment)
        return commitment

    def record_obligation(
        self,
        commitment_id: UUID,
        amount: Decimal,
    ) -> Commitment:
        """Record obligation (invoice received) against a commitment."""
        commitment = self.get_or_404(commitment_id)

        if commitment.status not in (
            CommitmentStatus.COMMITTED,
            CommitmentStatus.OBLIGATED,
        ):
            raise ValidationError(
                f"Cannot obligate commitment in {commitment.status.value} status"
            )

        remaining = (
            commitment.committed_amount
            - commitment.obligated_amount
            - commitment.cancelled_amount
        )
        if amount > remaining:
            raise ValidationError(
                f"Obligation amount {amount} exceeds remaining "
                f"committed balance {remaining}"
            )

        commitment.obligated_amount += amount
        commitment.obligation_date = date.today()
        commitment.status = CommitmentStatus.OBLIGATED
        self.db.flush()

        logger.info("Recorded obligation %s on commitment %s", amount, commitment_id)
        self._flush_and_refresh(commitment)
        return commitment

    def record_expenditure(
        self,
        commitment_id: UUID,
        amount: Decimal,
    ) -> Commitment:
        """Record expenditure (payment made) against a commitment."""
        commitment = self.get_or_404(commitment_id)

        if commitment.status not in (
            CommitmentStatus.OBLIGATED,
            CommitmentStatus.PARTIALLY_PAID,
        ):
            raise ValidationError(
                f"Cannot expend commitment in {commitment.status.value} status"
            )

        remaining = commitment.obligated_amount - commitment.expended_amount
        if amount > remaining:
            raise ValidationError(
                f"Expenditure amount {amount} exceeds remaining "
                f"obligated balance {remaining}"
            )

        commitment.expended_amount += amount
        commitment.expenditure_date = date.today()

        if commitment.expended_amount >= commitment.obligated_amount:
            commitment.status = CommitmentStatus.EXPENDED
        else:
            commitment.status = CommitmentStatus.PARTIALLY_PAID

        self.db.flush()
        logger.info("Recorded expenditure %s on commitment %s", amount, commitment_id)
        self._flush_and_refresh(commitment)
        return commitment

    def cancel_commitment(
        self,
        commitment_id: UUID,
        amount: Decimal | None = None,
    ) -> Commitment:
        """Cancel a commitment (full or partial)."""
        commitment = self.get_or_404(commitment_id)

        if commitment.status in (
            CommitmentStatus.EXPENDED,
            CommitmentStatus.CANCELLED,
            CommitmentStatus.LAPSED,
        ):
            raise ValidationError(
                f"Cannot cancel commitment in {commitment.status.value} status"
            )

        if amount is None:
            # Full cancellation
            cancel_amount = (
                commitment.committed_amount
                - commitment.obligated_amount
                - commitment.expended_amount
            )
            commitment.cancelled_amount = cancel_amount
            commitment.status = CommitmentStatus.CANCELLED
        else:
            # Partial cancellation
            available = (
                commitment.committed_amount
                - commitment.obligated_amount
                - commitment.expended_amount
                - commitment.cancelled_amount
            )
            if amount > available:
                raise ValidationError(
                    f"Cancel amount {amount} exceeds available balance {available}"
                )
            commitment.cancelled_amount += amount

        self.db.flush()
        logger.info("Cancelled commitment %s (amount: %s)", commitment_id, amount)
        self._flush_and_refresh(commitment)
        return commitment

    def count_for_org(self, organization_id: UUID) -> int:
        """Count commitments for an organization."""
        stmt = select(func.count(Commitment.commitment_id)).where(
            Commitment.organization_id == organization_id
        )
        return self.db.scalar(stmt) or 0
