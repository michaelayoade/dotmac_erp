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

from app.models.finance.ipsas.commitment import Commitment
from app.models.finance.ipsas.enums import CommitmentStatus, CommitmentType
from app.services.common import NotFoundError, ValidationError

logger = logging.getLogger(__name__)


class CommitmentService:
    """Service for managing IPSAS commitments (encumbrances)."""

    def __init__(self, db: Session):
        self.db = db

    def _commit_and_refresh(self, commitment: Commitment) -> None:
        self.db.commit()
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
        self._commit_and_refresh(commitment)
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
        self._commit_and_refresh(commitment)
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
        self._commit_and_refresh(commitment)
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

        remaining = commitment.committed_amount - commitment.obligated_amount
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
        self._commit_and_refresh(commitment)
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
        self._commit_and_refresh(commitment)
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
        self._commit_and_refresh(commitment)
        return commitment

    def count_for_org(self, organization_id: UUID) -> int:
        """Count commitments for an organization."""
        stmt = select(func.count(Commitment.commitment_id)).where(
            Commitment.organization_id == organization_id
        )
        return self.db.scalar(stmt) or 0
