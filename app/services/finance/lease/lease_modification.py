"""
LeaseModificationService - IFRS 16 Lease Modification Management.

Manages lease modifications including scope changes, term changes,
and payment reassessments per IFRS 16.44-46.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.finance.lease.lease_asset import LeaseAsset
from app.models.finance.lease.lease_contract import LeaseContract, LeaseStatus
from app.models.finance.lease.lease_liability import LeaseLiability
from app.models.finance.lease.lease_modification import (
    LeaseModification,
    ModificationType,
)
from app.services.common import coerce_uuid
from app.services.finance.lease.lease_calculation import LeaseCalculationService
from app.services.response import ListResponseMixin

logger = logging.getLogger(__name__)


@dataclass
class ModificationInput:
    """Input for creating a lease modification."""

    lease_id: UUID
    fiscal_period_id: UUID
    modification_date: date
    effective_date: date
    modification_type: ModificationType
    description: str | None = None
    is_separate_lease: bool = False
    new_lease_payments: Decimal | None = None
    revised_discount_rate: Decimal | None = None
    revised_lease_term_months: int | None = None


@dataclass
class ModificationResult:
    """Result of a lease modification."""

    success: bool
    modification: LeaseModification | None = None
    liability_adjustment: Decimal = Decimal("0")
    rou_asset_adjustment: Decimal = Decimal("0")
    gain_loss: Decimal = Decimal("0")
    message: str = ""


class LeaseModificationService(ListResponseMixin):
    """
    Service for IFRS 16 lease modification management.

    Handles different types of modifications:
    - Separate lease (IFRS 16.44): increases scope at standalone price
    - Not separate lease (IFRS 16.45-46): remeasure liability and adjust ROU asset
    """

    @staticmethod
    def process_modification(
        db: Session,
        organization_id: UUID,
        input: ModificationInput,
        created_by_user_id: UUID,
    ) -> ModificationResult:
        """
        Process a lease modification per IFRS 16.

        Args:
            db: Database session
            organization_id: Organization scope
            input: Modification input
            created_by_user_id: User processing modification

        Returns:
            ModificationResult with outcome
        """
        org_id = coerce_uuid(organization_id)
        lease_id = coerce_uuid(input.lease_id)
        user_id = coerce_uuid(created_by_user_id)

        # Load lease contract
        contract = (
            db.query(LeaseContract)
            .filter(
                LeaseContract.lease_id == lease_id,
                LeaseContract.organization_id == org_id,
            )
            .first()
        )

        if not contract:
            return ModificationResult(success=False, message="Lease contract not found")

        if contract.status not in [LeaseStatus.ACTIVE]:
            return ModificationResult(
                success=False,
                message=f"Cannot modify lease in {contract.status.value} status",
            )

        # Load liability and asset
        liability = (
            db.query(LeaseLiability).filter(LeaseLiability.lease_id == lease_id).first()
        )

        asset = db.query(LeaseAsset).filter(LeaseAsset.lease_id == lease_id).first()

        if not liability or not asset:
            return ModificationResult(
                success=False, message="Lease liability and asset must exist"
            )

        # Capture before values
        liability_before = liability.current_liability_balance
        rou_asset_before = asset.carrying_amount
        remaining_term_before = LeaseModificationService._calculate_remaining_months(
            contract.commencement_date, contract.lease_term_months, input.effective_date
        )

        if input.is_separate_lease:
            # IFRS 16.44: Account as a separate lease
            return LeaseModificationService._process_separate_lease(
                db,
                org_id,
                input,
                contract,
                liability,
                asset,
                liability_before,
                rou_asset_before,
                remaining_term_before,
                user_id,
            )
        else:
            # IFRS 16.45-46: Remeasure and adjust
            return LeaseModificationService._process_non_separate_modification(
                db,
                org_id,
                input,
                contract,
                liability,
                asset,
                liability_before,
                rou_asset_before,
                remaining_term_before,
                user_id,
            )

    @staticmethod
    def _process_separate_lease(
        db: Session,
        organization_id: UUID,
        input: ModificationInput,
        contract: LeaseContract,
        liability: LeaseLiability,
        asset: LeaseAsset,
        liability_before: Decimal,
        rou_asset_before: Decimal,
        remaining_term_before: int,
        user_id: UUID,
    ) -> ModificationResult:
        """Process modification as a separate lease (IFRS 16.44)."""
        # For a separate lease, the original lease is unchanged
        # A new lease would be created for the additional ROU
        # Record the modification for audit purposes
        modification = LeaseModification(
            lease_id=input.lease_id,
            fiscal_period_id=input.fiscal_period_id,
            modification_date=input.modification_date,
            effective_date=input.effective_date,
            modification_type=input.modification_type,
            description=input.description,
            is_separate_lease=True,
            liability_before=liability_before,
            rou_asset_before=rou_asset_before,
            remaining_lease_term_before=remaining_term_before,
            discount_rate_before=liability.discount_rate,
            liability_after=liability_before,  # Unchanged
            rou_asset_after=rou_asset_before,  # Unchanged
            liability_adjustment=Decimal("0"),
            rou_asset_adjustment=Decimal("0"),
            gain_loss_on_modification=Decimal("0"),
            created_by_user_id=user_id,
        )

        db.add(modification)
        db.commit()
        db.refresh(modification)

        return ModificationResult(
            success=True,
            modification=modification,
            message="Separate lease modification recorded. Create a new lease for additional scope.",
        )

    @staticmethod
    def _process_non_separate_modification(
        db: Session,
        organization_id: UUID,
        input: ModificationInput,
        contract: LeaseContract,
        liability: LeaseLiability,
        asset: LeaseAsset,
        liability_before: Decimal,
        rou_asset_before: Decimal,
        remaining_term_before: int,
        user_id: UUID,
    ) -> ModificationResult:
        """Process modification that is not a separate lease (IFRS 16.45-46)."""
        # Determine new discount rate
        if input.revised_discount_rate:
            new_discount_rate = input.revised_discount_rate
        else:
            # Use revised rate at modification date for certain modifications
            new_discount_rate = liability.discount_rate

        # Determine new term
        if input.revised_lease_term_months:
            new_term_months = input.revised_lease_term_months
        else:
            new_term_months = remaining_term_before

        # Determine new payments
        if input.new_lease_payments:
            new_payment_amount = input.new_lease_payments
        else:
            new_payment_amount = liability.initial_liability_amount / (
                contract.lease_term_months or 1
            )

        # Calculate new liability (PV of remaining payments at new rate)
        new_liability = LeaseCalculationService.calculate_pv(
            payment=new_payment_amount,
            rate=new_discount_rate,
            periods=new_term_months,
        )

        liability_adjustment = new_liability - liability_before

        # Determine ROU asset adjustment based on modification type
        if input.modification_type in [
            ModificationType.SCOPE_DECREASE,
            ModificationType.TERM_REDUCTION,
        ]:
            # IFRS 16.46(a): Proportional reduction for partial terminations
            reduction_ratio = (
                new_liability / liability_before
                if liability_before > 0
                else Decimal("1")
            )
            new_rou_asset = rou_asset_before * reduction_ratio
            rou_asset_adjustment = new_rou_asset - rou_asset_before
            gain_loss = liability_adjustment - rou_asset_adjustment
        else:
            # IFRS 16.46(b): Adjust ROU asset by same amount as liability
            rou_asset_adjustment = liability_adjustment
            new_rou_asset = rou_asset_before + rou_asset_adjustment
            gain_loss = Decimal("0")

        # Create modification record
        modification = LeaseModification(
            lease_id=input.lease_id,
            fiscal_period_id=input.fiscal_period_id,
            modification_date=input.modification_date,
            effective_date=input.effective_date,
            modification_type=input.modification_type,
            description=input.description,
            is_separate_lease=False,
            liability_before=liability_before,
            rou_asset_before=rou_asset_before,
            remaining_lease_term_before=remaining_term_before,
            discount_rate_before=liability.discount_rate,
            new_lease_payments=input.new_lease_payments,
            revised_discount_rate=input.revised_discount_rate,
            revised_lease_term_months=input.revised_lease_term_months,
            liability_after=new_liability,
            rou_asset_after=new_rou_asset,
            liability_adjustment=liability_adjustment,
            rou_asset_adjustment=rou_asset_adjustment,
            gain_loss_on_modification=gain_loss,
            created_by_user_id=user_id,
        )

        db.add(modification)

        # Update liability and asset
        liability.current_liability_balance = new_liability
        if input.revised_discount_rate:
            liability.discount_rate = input.revised_discount_rate

        asset.carrying_amount = new_rou_asset
        asset.modification_adjustments += rou_asset_adjustment

        # Update contract if term changed
        if input.revised_lease_term_months:
            contract.lease_term_months = input.revised_lease_term_months

        db.commit()
        db.refresh(modification)

        return ModificationResult(
            success=True,
            modification=modification,
            liability_adjustment=liability_adjustment,
            rou_asset_adjustment=rou_asset_adjustment,
            gain_loss=gain_loss,
            message="Lease modification processed successfully",
        )

    @staticmethod
    def _calculate_remaining_months(
        commencement_date: date,
        total_term_months: int,
        as_of_date: date,
    ) -> int:
        """Calculate remaining lease term in months."""
        elapsed_months = (as_of_date.year - commencement_date.year) * 12 + (
            as_of_date.month - commencement_date.month
        )
        remaining = total_term_months - elapsed_months
        return max(0, remaining)

    @staticmethod
    def approve_modification(
        db: Session,
        organization_id: UUID,
        modification_id: UUID,
        approved_by_user_id: UUID,
    ) -> LeaseModification:
        """
        Approve a lease modification.

        Args:
            db: Database session
            organization_id: Organization scope
            modification_id: Modification to approve
            approved_by_user_id: User approving

        Returns:
            Updated LeaseModification
        """
        coerce_uuid(organization_id)
        mod_id = coerce_uuid(modification_id)
        user_id = coerce_uuid(approved_by_user_id)

        modification = (
            db.query(LeaseModification)
            .filter(
                LeaseModification.modification_id == mod_id,
            )
            .first()
        )

        if not modification:
            raise HTTPException(status_code=404, detail="Modification not found")

        # SoD check
        if modification.created_by_user_id == user_id:
            raise HTTPException(
                status_code=400, detail="Approver cannot be the same as creator"
            )

        modification.approved_by_user_id = user_id
        modification.approved_at = datetime.now(UTC)

        db.commit()
        db.refresh(modification)

        return modification

    @staticmethod
    def get(
        db: Session,
        modification_id: str,
        organization_id: UUID | None = None,
    ) -> LeaseModification | None:
        """Get a modification by ID."""
        modification = (
            db.query(LeaseModification)
            .filter(LeaseModification.modification_id == coerce_uuid(modification_id))
            .first()
        )
        if not modification:
            return None
        if organization_id is not None:
            lease = db.get(LeaseContract, modification.lease_id)
            if not lease or lease.organization_id != coerce_uuid(organization_id):
                return None
        return modification

    @staticmethod
    def list_by_lease(
        db: Session,
        lease_id: UUID,
    ) -> list[LeaseModification]:
        """List all modifications for a lease."""
        return (
            db.query(LeaseModification)
            .filter(LeaseModification.lease_id == coerce_uuid(lease_id))
            .order_by(LeaseModification.effective_date.desc())
            .all()
        )

    @staticmethod
    def list(
        db: Session,
        organization_id: str | None = None,
        modification_type: ModificationType | None = None,
        from_date: date | None = None,
        to_date: date | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[LeaseModification]:
        """List modifications with filters."""
        query = db.query(LeaseModification)

        if modification_type:
            query = query.filter(
                LeaseModification.modification_type == modification_type
            )

        if from_date:
            query = query.filter(LeaseModification.effective_date >= from_date)

        if to_date:
            query = query.filter(LeaseModification.effective_date <= to_date)

        return (
            query.order_by(LeaseModification.effective_date.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )


# Module-level instance
lease_modification_service = LeaseModificationService()
