"""
DeferredTaxService - IAS 12 deferred tax calculations.

Manages deferred tax basis tracking, temporary differences, and movements.
"""

from __future__ import annotations

import builtins
import logging
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.finance.tax.deferred_tax_basis import DeferredTaxBasis, DifferenceType
from app.models.finance.tax.deferred_tax_movement import DeferredTaxMovement
from app.services.common import coerce_uuid
from app.services.response import ListResponseMixin

logger = logging.getLogger(__name__)


@dataclass
class DeferredTaxBasisInput:
    """Input for creating a deferred tax basis."""

    basis_code: str
    basis_name: str
    jurisdiction_id: UUID
    difference_type: DifferenceType
    source_type: str
    applicable_tax_rate: Decimal
    description: str | None = None
    source_id: UUID | None = None
    gl_account_id: UUID | None = None
    accounting_base: Decimal = Decimal("0")
    tax_base: Decimal = Decimal("0")
    is_recognized: bool = True
    recognition_probability: Decimal | None = None
    expected_reversal_year: int | None = None
    is_current_year_reversal: bool = False


@dataclass
class DeferredTaxCalculationResult:
    """Result of deferred tax calculation."""

    temporary_difference: Decimal
    deferred_tax_amount: Decimal
    is_asset: bool
    recognized_amount: Decimal
    unrecognized_amount: Decimal


@dataclass
class DeferredTaxMovementResult:
    """Result of deferred tax movement calculation."""

    movement_id: UUID
    deferred_tax_movement_pl: Decimal
    deferred_tax_movement_oci: Decimal
    deferred_tax_movement_equity: Decimal
    tax_rate_change_impact: Decimal
    deferred_tax_closing: Decimal


@dataclass
class DeferredTaxSummary:
    """Summary of deferred tax position."""

    total_dta: Decimal  # Deferred tax asset
    total_dtl: Decimal  # Deferred tax liability
    net_position: Decimal
    unrecognized_dta: Decimal
    items_count: int


class DeferredTaxService(ListResponseMixin):
    """
    Service for IAS 12 deferred tax calculations.

    Handles:
    - Deferred tax basis tracking
    - Temporary difference calculations
    - Tax rate change impacts
    - Recognition assessments
    """

    @staticmethod
    def calculate_temporary_difference(
        accounting_base: Decimal,
        tax_base: Decimal,
    ) -> Decimal:
        """
        Calculate temporary difference.

        Positive = taxable temporary difference (DTL)
        Negative = deductible temporary difference (DTA)

        Args:
            accounting_base: Book value
            tax_base: Tax value

        Returns:
            Temporary difference amount
        """
        return accounting_base - tax_base

    @staticmethod
    def calculate_deferred_tax(
        temporary_difference: Decimal,
        tax_rate: Decimal,
    ) -> tuple[Decimal, bool]:
        """
        Calculate deferred tax amount.

        Args:
            temporary_difference: Temporary difference amount
            tax_rate: Applicable tax rate

        Returns:
            Tuple of (deferred_tax_amount, is_asset)
        """
        deferred_tax = (abs(temporary_difference) * tax_rate).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

        # Deductible difference (negative) = DTA
        # Taxable difference (positive) = DTL
        is_asset = temporary_difference < 0

        return (deferred_tax, is_asset)

    @staticmethod
    def create_basis(
        db: Session,
        organization_id: UUID,
        input: DeferredTaxBasisInput,
    ) -> DeferredTaxBasis:
        """
        Create a new deferred tax basis.

        Args:
            db: Database session
            organization_id: Organization scope
            input: Basis input data

        Returns:
            Created DeferredTaxBasis
        """
        org_id = coerce_uuid(organization_id)

        # Check for duplicate
        stmt = select(DeferredTaxBasis).where(
            DeferredTaxBasis.organization_id == org_id,
            DeferredTaxBasis.basis_code == input.basis_code,
        )
        existing = db.scalars(stmt).first()
        if existing:
            raise HTTPException(
                status_code=400,
                detail=f"Basis code '{input.basis_code}' already exists",
            )

        # Calculate temporary difference and deferred tax
        temp_diff = DeferredTaxService.calculate_temporary_difference(
            input.accounting_base, input.tax_base
        )
        deferred_tax, is_asset = DeferredTaxService.calculate_deferred_tax(
            temp_diff, input.applicable_tax_rate
        )

        # Apply recognition
        if input.is_recognized:
            recognized = deferred_tax
            unrecognized = Decimal("0")
        else:
            recognized = Decimal("0")
            unrecognized = deferred_tax

        # Partial recognition based on probability
        if input.recognition_probability and input.recognition_probability < Decimal(
            "1"
        ):
            recognized = (deferred_tax * input.recognition_probability).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            unrecognized = deferred_tax - recognized

        basis = DeferredTaxBasis(
            organization_id=org_id,
            jurisdiction_id=input.jurisdiction_id,
            basis_code=input.basis_code,
            basis_name=input.basis_name,
            description=input.description,
            difference_type=input.difference_type,
            source_type=input.source_type,
            source_id=input.source_id,
            gl_account_id=input.gl_account_id,
            accounting_base=input.accounting_base,
            tax_base=input.tax_base,
            temporary_difference=temp_diff,
            applicable_tax_rate=input.applicable_tax_rate,
            deferred_tax_amount=recognized,
            is_asset=is_asset,
            is_recognized=input.is_recognized,
            recognition_probability=input.recognition_probability,
            unrecognized_amount=unrecognized,
            expected_reversal_year=input.expected_reversal_year,
            is_current_year_reversal=input.is_current_year_reversal,
            is_active=True,
        )

        db.add(basis)
        db.commit()
        db.refresh(basis)

        return basis

    @staticmethod
    def update_basis(
        db: Session,
        organization_id: UUID,
        basis_id: UUID,
        accounting_base: Decimal,
        tax_base: Decimal,
        tax_rate: Decimal | None = None,
    ) -> DeferredTaxCalculationResult:
        """
        Update a deferred tax basis with new values.

        Args:
            db: Database session
            organization_id: Organization scope
            basis_id: Basis to update
            accounting_base: New accounting base
            tax_base: New tax base
            tax_rate: New tax rate (optional)

        Returns:
            DeferredTaxCalculationResult with new values
        """
        org_id = coerce_uuid(organization_id)
        b_id = coerce_uuid(basis_id)

        basis = db.get(DeferredTaxBasis, b_id)
        if not basis or basis.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Deferred tax basis not found")

        # Update rate if provided
        if tax_rate is not None:
            basis.applicable_tax_rate = tax_rate

        # Calculate new values
        temp_diff = DeferredTaxService.calculate_temporary_difference(
            accounting_base, tax_base
        )
        deferred_tax, is_asset = DeferredTaxService.calculate_deferred_tax(
            temp_diff, basis.applicable_tax_rate
        )

        # Apply recognition
        if basis.is_recognized:
            if (
                basis.recognition_probability
                and basis.recognition_probability < Decimal("1")
            ):
                recognized = (deferred_tax * basis.recognition_probability).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )
                unrecognized = deferred_tax - recognized
            else:
                recognized = deferred_tax
                unrecognized = Decimal("0")
        else:
            recognized = Decimal("0")
            unrecognized = deferred_tax

        # Update basis
        basis.accounting_base = accounting_base
        basis.tax_base = tax_base
        basis.temporary_difference = temp_diff
        basis.deferred_tax_amount = recognized
        basis.is_asset = is_asset
        basis.unrecognized_amount = unrecognized

        db.commit()
        db.refresh(basis)

        return DeferredTaxCalculationResult(
            temporary_difference=temp_diff,
            deferred_tax_amount=deferred_tax,
            is_asset=is_asset,
            recognized_amount=recognized,
            unrecognized_amount=unrecognized,
        )

    @staticmethod
    def create_movement(
        db: Session,
        organization_id: UUID,
        basis_id: UUID,
        fiscal_period_id: UUID,
        accounting_base_closing: Decimal,
        tax_base_closing: Decimal,
        tax_rate_closing: Decimal,
        movement_category: str,
        movement_description: str | None = None,
        deferred_tax_movement_oci: Decimal = Decimal("0"),
        deferred_tax_movement_equity: Decimal = Decimal("0"),
    ) -> DeferredTaxMovementResult:
        """
        Create a deferred tax movement for a period.

        Args:
            db: Database session
            organization_id: Organization scope
            basis_id: Deferred tax basis
            fiscal_period_id: Fiscal period
            accounting_base_closing: Closing accounting base
            tax_base_closing: Closing tax base
            tax_rate_closing: Closing tax rate
            movement_category: Category of movement
            movement_description: Description
            deferred_tax_movement_oci: Movement to OCI
            deferred_tax_movement_equity: Movement directly to equity

        Returns:
            DeferredTaxMovementResult with movement details
        """
        org_id = coerce_uuid(organization_id)
        b_id = coerce_uuid(basis_id)
        period_id = coerce_uuid(fiscal_period_id)

        # Load basis
        basis = db.get(DeferredTaxBasis, b_id)
        if not basis or basis.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Deferred tax basis not found")

        # Opening values from current basis
        accounting_base_opening = basis.accounting_base
        tax_base_opening = basis.tax_base
        temp_diff_opening = basis.temporary_difference
        deferred_tax_opening = basis.deferred_tax_amount
        tax_rate_opening = basis.applicable_tax_rate

        # Calculate closing temporary difference
        temp_diff_closing = DeferredTaxService.calculate_temporary_difference(
            accounting_base_closing, tax_base_closing
        )

        # Calculate closing deferred tax at closing rate
        deferred_tax_closing_gross, is_asset = (
            DeferredTaxService.calculate_deferred_tax(
                temp_diff_closing, tax_rate_closing
            )
        )

        # Apply recognition
        if basis.is_recognized:
            if (
                basis.recognition_probability
                and basis.recognition_probability < Decimal("1")
            ):
                deferred_tax_closing = (
                    deferred_tax_closing_gross * basis.recognition_probability
                ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                unrecognized = deferred_tax_closing_gross - deferred_tax_closing
            else:
                deferred_tax_closing = deferred_tax_closing_gross
                unrecognized = Decimal("0")
        else:
            deferred_tax_closing = Decimal("0")
            unrecognized = deferred_tax_closing_gross

        # Calculate movements
        accounting_base_movement = accounting_base_closing - accounting_base_opening
        tax_base_movement = tax_base_closing - tax_base_opening
        temp_diff_movement = temp_diff_closing - temp_diff_opening

        # Tax rate change impact (at opening temp diff)
        if tax_rate_closing != tax_rate_opening:
            tax_rate_change_impact = (
                abs(temp_diff_opening) * (tax_rate_closing - tax_rate_opening)
            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if temp_diff_opening < 0:  # DTA
                tax_rate_change_impact = -tax_rate_change_impact
        else:
            tax_rate_change_impact = Decimal("0")

        # Total deferred tax movement
        total_dt_movement = deferred_tax_closing - deferred_tax_opening

        # P&L movement = total - OCI - equity
        dt_movement_pl = (
            total_dt_movement - deferred_tax_movement_oci - deferred_tax_movement_equity
        )

        # Recognition change
        recognition_change = (
            deferred_tax_closing_gross - deferred_tax_closing
        ) - basis.unrecognized_amount

        # Create movement record
        movement = DeferredTaxMovement(
            basis_id=b_id,
            fiscal_period_id=period_id,
            accounting_base_opening=accounting_base_opening,
            tax_base_opening=tax_base_opening,
            temporary_difference_opening=temp_diff_opening,
            deferred_tax_opening=deferred_tax_opening,
            accounting_base_movement=accounting_base_movement,
            tax_base_movement=tax_base_movement,
            temporary_difference_movement=temp_diff_movement,
            tax_rate_opening=tax_rate_opening,
            tax_rate_closing=tax_rate_closing,
            tax_rate_change_impact=tax_rate_change_impact,
            deferred_tax_movement_pl=dt_movement_pl,
            deferred_tax_movement_oci=deferred_tax_movement_oci,
            deferred_tax_movement_equity=deferred_tax_movement_equity,
            accounting_base_closing=accounting_base_closing,
            tax_base_closing=tax_base_closing,
            temporary_difference_closing=temp_diff_closing,
            deferred_tax_closing=deferred_tax_closing,
            recognition_change=recognition_change,
            unrecognized_closing=unrecognized,
            movement_description=movement_description,
            movement_category=movement_category,
        )

        db.add(movement)

        # Update basis with closing values
        basis.accounting_base = accounting_base_closing
        basis.tax_base = tax_base_closing
        basis.temporary_difference = temp_diff_closing
        basis.applicable_tax_rate = tax_rate_closing
        basis.deferred_tax_amount = deferred_tax_closing
        basis.is_asset = is_asset
        basis.unrecognized_amount = unrecognized

        db.commit()
        db.refresh(movement)

        return DeferredTaxMovementResult(
            movement_id=movement.movement_id,
            deferred_tax_movement_pl=dt_movement_pl,
            deferred_tax_movement_oci=deferred_tax_movement_oci,
            deferred_tax_movement_equity=deferred_tax_movement_equity,
            tax_rate_change_impact=tax_rate_change_impact,
            deferred_tax_closing=deferred_tax_closing,
        )

    @staticmethod
    def get_summary(
        db: Session,
        organization_id: UUID,
        jurisdiction_id: UUID | None = None,
    ) -> DeferredTaxSummary:
        """
        Get deferred tax summary.

        Args:
            db: Database session
            organization_id: Organization scope
            jurisdiction_id: Filter by jurisdiction (optional)

        Returns:
            DeferredTaxSummary with aggregated position
        """
        org_id = coerce_uuid(organization_id)

        stmt = select(DeferredTaxBasis).where(
            DeferredTaxBasis.organization_id == org_id,
            DeferredTaxBasis.is_active == True,
        )

        if jurisdiction_id:
            stmt = stmt.where(
                DeferredTaxBasis.jurisdiction_id == coerce_uuid(jurisdiction_id)
            )

        bases = list(db.scalars(stmt).all())

        total_dta = Decimal("0")
        total_dtl = Decimal("0")
        total_unrecognized = Decimal("0")

        for basis in bases:
            if basis.is_asset:
                total_dta += basis.deferred_tax_amount
            else:
                total_dtl += basis.deferred_tax_amount
            total_unrecognized += basis.unrecognized_amount

        return DeferredTaxSummary(
            total_dta=total_dta,
            total_dtl=total_dtl,
            net_position=total_dta - total_dtl,
            unrecognized_dta=total_unrecognized,
            items_count=len(bases),
        )

    @staticmethod
    def get_movement_summary(
        db: Session,
        organization_id: UUID,
        fiscal_period_id: UUID,
    ) -> dict:
        """
        Get deferred tax movement summary for a period.

        Args:
            db: Database session
            organization_id: Organization scope
            fiscal_period_id: Fiscal period

        Returns:
            Dict with aggregated movements
        """
        org_id = coerce_uuid(organization_id)
        period_id = coerce_uuid(fiscal_period_id)

        stmt = (
            select(
                func.sum(DeferredTaxMovement.deferred_tax_movement_pl).label(
                    "pl_total"
                ),
                func.sum(DeferredTaxMovement.deferred_tax_movement_oci).label(
                    "oci_total"
                ),
                func.sum(DeferredTaxMovement.deferred_tax_movement_equity).label(
                    "equity_total"
                ),
                func.sum(DeferredTaxMovement.tax_rate_change_impact).label(
                    "rate_change"
                ),
                func.sum(DeferredTaxMovement.recognition_change).label("recognition"),
            )
            .join(DeferredTaxBasis)
            .where(
                DeferredTaxBasis.organization_id == org_id,
                DeferredTaxMovement.fiscal_period_id == period_id,
            )
        )
        result = db.execute(stmt).first()

        if result is None:
            return {
                "deferred_tax_expense_pl": Decimal("0"),
                "deferred_tax_oci": Decimal("0"),
                "deferred_tax_equity": Decimal("0"),
                "tax_rate_change_impact": Decimal("0"),
                "recognition_change": Decimal("0"),
            }

        return {
            "deferred_tax_expense_pl": result.pl_total or Decimal("0"),
            "deferred_tax_oci": result.oci_total or Decimal("0"),
            "deferred_tax_equity": result.equity_total or Decimal("0"),
            "tax_rate_change_impact": result.rate_change or Decimal("0"),
            "recognition_change": result.recognition or Decimal("0"),
        }

    @staticmethod
    def get(
        db: Session,
        basis_id: str,
        organization_id: UUID | None = None,
    ) -> DeferredTaxBasis:
        """Get a deferred tax basis by ID."""
        basis = db.get(DeferredTaxBasis, coerce_uuid(basis_id))
        if not basis:
            raise HTTPException(status_code=404, detail="Deferred tax basis not found")
        if organization_id is not None and basis.organization_id != coerce_uuid(
            organization_id
        ):
            raise HTTPException(status_code=404, detail="Deferred tax basis not found")
        return basis

    @staticmethod
    def list(
        db: Session,
        organization_id: str | None = None,
        jurisdiction_id: str | None = None,
        difference_type: DifferenceType | None = None,
        is_asset: bool | None = None,
        asset_liability_type: str | None = None,
        is_active: bool | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> builtins.list[DeferredTaxBasis]:
        """List deferred tax bases with optional filters."""
        stmt = select(DeferredTaxBasis)

        if organization_id:
            stmt = stmt.where(
                DeferredTaxBasis.organization_id == coerce_uuid(organization_id)
            )

        if jurisdiction_id:
            stmt = stmt.where(
                DeferredTaxBasis.jurisdiction_id == coerce_uuid(jurisdiction_id)
            )

        if difference_type:
            stmt = stmt.where(DeferredTaxBasis.difference_type == difference_type)

        if asset_liability_type and is_asset is None:
            normalized = asset_liability_type.strip().upper()
            if normalized == "ASSET":
                is_asset = True
            elif normalized == "LIABILITY":
                is_asset = False

        if is_asset is not None:
            stmt = stmt.where(DeferredTaxBasis.is_asset == is_asset)

        if is_active is not None:
            stmt = stmt.where(DeferredTaxBasis.is_active == is_active)

        return list(
            db.scalars(
                stmt.order_by(DeferredTaxBasis.basis_code)
                .limit(limit)
                .offset(offset)
            ).all()
        )

    @staticmethod
    def list_movements(
        db: Session,
        basis_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> builtins.list[DeferredTaxMovement]:
        """List movements for a deferred tax basis."""
        stmt = (
            select(DeferredTaxMovement)
            .where(DeferredTaxMovement.basis_id == coerce_uuid(basis_id))
            .order_by(DeferredTaxMovement.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(db.scalars(stmt).all())


# Module-level singleton instance
deferred_tax_service = DeferredTaxService()
