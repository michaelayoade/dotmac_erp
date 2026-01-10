"""
HedgeAccountingService - IFRS 9 hedge accounting.

Manages hedge relationships, effectiveness testing, and hedge accounting entries.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.ifrs.fin_inst.financial_instrument import FinancialInstrument
from app.models.ifrs.fin_inst.hedge_relationship import (
    HedgeRelationship,
    HedgeType,
    HedgeStatus,
)
from app.models.ifrs.fin_inst.hedge_effectiveness import HedgeEffectiveness
from app.services.common import coerce_uuid
from app.services.response import ListResponseMixin


@dataclass
class HedgeDesignationInput:
    """Input for designating a hedge relationship."""

    hedge_code: str
    hedge_name: str
    hedge_type: HedgeType
    hedging_instrument_id: UUID
    hedged_item_type: str
    hedged_item_description: str
    hedged_risk: str
    designation_date: date
    effective_date: date
    prospective_test_method: str
    retrospective_test_method: str
    description: Optional[str] = None
    hedged_item_id: Optional[UUID] = None
    hedging_instrument_proportion: Decimal = Decimal("1.0")
    hedge_ratio: Decimal = Decimal("1.0")
    documentation_reference: Optional[str] = None


@dataclass
class EffectivenessTestInput:
    """Input for effectiveness testing."""

    test_date: date
    hedging_instrument_fv_change: Decimal
    hedged_item_fv_change: Decimal
    prospective_test_passed: bool = True
    prospective_test_result: Optional[Decimal] = None
    prospective_test_notes: Optional[str] = None


@dataclass
class EffectivenessTestResult:
    """Result of effectiveness test."""

    effectiveness_id: UUID
    hedge_effectiveness_ratio: Decimal
    is_highly_effective: bool
    retrospective_test_passed: bool
    hedge_ineffectiveness: Decimal
    effective_portion: Decimal
    ineffectiveness_recognized_pl: Decimal
    effective_portion_oci: Decimal


class HedgeAccountingService(ListResponseMixin):
    """
    Service for IFRS 9 hedge accounting.

    Handles:
    - Hedge relationship designation
    - Prospective and retrospective effectiveness testing
    - Ineffectiveness calculation
    - Cash flow hedge reserve management
    """

    @staticmethod
    def calculate_effectiveness_ratio(
        hedging_instrument_change: Decimal,
        hedged_item_change: Decimal,
    ) -> Decimal:
        """
        Calculate hedge effectiveness ratio.

        Ratio = Hedging instrument FV change / Hedged item FV change

        Args:
            hedging_instrument_change: FV change of hedging instrument
            hedged_item_change: FV change of hedged item

        Returns:
            Effectiveness ratio (should be between 0.8 and 1.25 for highly effective)
        """
        if hedged_item_change == 0:
            return Decimal("0")

        # Take absolute value for ratio calculation
        ratio = abs(hedging_instrument_change / hedged_item_change)
        return ratio.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)

    @staticmethod
    def is_highly_effective(ratio: Decimal) -> bool:
        """
        Determine if hedge is highly effective.

        Under IFRS 9, quantitative thresholds are not mandatory but
        80%-125% is commonly used as guidance.

        Args:
            ratio: Effectiveness ratio

        Returns:
            True if highly effective
        """
        return Decimal("0.80") <= ratio <= Decimal("1.25")

    @staticmethod
    def calculate_ineffectiveness(
        hedge_type: HedgeType,
        hedging_instrument_change: Decimal,
        hedged_item_change: Decimal,
    ) -> tuple[Decimal, Decimal]:
        """
        Calculate hedge ineffectiveness.

        For fair value hedges: Both changes go to P&L
        For cash flow hedges: Effective portion to OCI, ineffective to P&L

        Args:
            hedge_type: Type of hedge
            hedging_instrument_change: FV change of hedging instrument
            hedged_item_change: FV change of hedged item

        Returns:
            Tuple of (ineffectiveness, effective_portion)
        """
        # For simplicity, ineffectiveness = abs(sum of changes)
        # More complex would consider the lower of changes method

        total_change = hedging_instrument_change + hedged_item_change

        if hedge_type == HedgeType.FAIR_VALUE:
            # Fair value hedge: ineffectiveness is the net
            ineffectiveness = total_change
            effective_portion = -hedged_item_change  # The offset amount
        elif hedge_type == HedgeType.CASH_FLOW:
            # Cash flow hedge: effective = lower of abs values
            abs_instrument = abs(hedging_instrument_change)
            abs_item = abs(hedged_item_change)
            effective_portion = min(abs_instrument, abs_item)
            if hedging_instrument_change < 0:
                effective_portion = -effective_portion
            ineffectiveness = hedging_instrument_change - effective_portion
        else:
            # Net investment hedge: similar to cash flow
            abs_instrument = abs(hedging_instrument_change)
            abs_item = abs(hedged_item_change)
            effective_portion = min(abs_instrument, abs_item)
            if hedging_instrument_change < 0:
                effective_portion = -effective_portion
            ineffectiveness = hedging_instrument_change - effective_portion

        return (ineffectiveness, effective_portion)

    @staticmethod
    def designate_hedge(
        db: Session,
        organization_id: UUID,
        input: HedgeDesignationInput,
        created_by_user_id: UUID,
    ) -> HedgeRelationship:
        """
        Designate a new hedge relationship.

        Args:
            db: Database session
            organization_id: Organization scope
            input: Hedge designation input
            created_by_user_id: User creating the hedge

        Returns:
            Created HedgeRelationship
        """
        org_id = coerce_uuid(organization_id)
        user_id = coerce_uuid(created_by_user_id)
        instrument_id = coerce_uuid(input.hedging_instrument_id)

        # Verify hedging instrument exists
        hedging_instrument = db.get(FinancialInstrument, instrument_id)
        if not hedging_instrument or hedging_instrument.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Hedging instrument not found")

        # Check for duplicate hedge code
        existing = (
            db.query(HedgeRelationship)
            .filter(
                HedgeRelationship.organization_id == org_id,
                HedgeRelationship.hedge_code == input.hedge_code,
            )
            .first()
        )
        if existing:
            raise HTTPException(
                status_code=400,
                detail=f"Hedge code '{input.hedge_code}' already exists",
            )

        hedge = HedgeRelationship(
            organization_id=org_id,
            hedge_code=input.hedge_code,
            hedge_name=input.hedge_name,
            description=input.description,
            hedge_type=input.hedge_type,
            hedging_instrument_id=instrument_id,
            hedging_instrument_proportion=input.hedging_instrument_proportion,
            hedged_item_type=input.hedged_item_type,
            hedged_item_id=input.hedged_item_id,
            hedged_item_description=input.hedged_item_description,
            hedged_risk=input.hedged_risk,
            hedge_ratio=input.hedge_ratio,
            designation_date=input.designation_date,
            effective_date=input.effective_date,
            status=HedgeStatus.DESIGNATED,
            prospective_test_method=input.prospective_test_method,
            prospective_test_passed=True,  # Assumed passed at designation
            retrospective_test_method=input.retrospective_test_method,
            documentation_reference=input.documentation_reference,
            created_by_user_id=user_id,
        )

        db.add(hedge)
        db.commit()
        db.refresh(hedge)

        return hedge

    @staticmethod
    def approve_hedge(
        db: Session,
        organization_id: UUID,
        hedge_id: UUID,
        approved_by_user_id: UUID,
    ) -> HedgeRelationship:
        """
        Approve a hedge designation and activate it.

        Args:
            db: Database session
            organization_id: Organization scope
            hedge_id: Hedge to approve
            approved_by_user_id: User approving

        Returns:
            Updated HedgeRelationship
        """
        org_id = coerce_uuid(organization_id)
        h_id = coerce_uuid(hedge_id)
        user_id = coerce_uuid(approved_by_user_id)

        hedge = db.get(HedgeRelationship, h_id)
        if not hedge or hedge.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Hedge relationship not found")

        if hedge.status != HedgeStatus.DESIGNATED:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot approve hedge with status '{hedge.status.value}'",
            )

        # SoD check
        if hedge.created_by_user_id == user_id:
            raise HTTPException(
                status_code=400,
                detail="Segregation of duties violation: creator cannot approve",
            )

        hedge.status = HedgeStatus.ACTIVE
        hedge.approved_by_user_id = user_id
        hedge.approved_at = datetime.now(timezone.utc)

        db.commit()
        db.refresh(hedge)

        return hedge

    @staticmethod
    def perform_effectiveness_test(
        db: Session,
        organization_id: UUID,
        hedge_id: UUID,
        fiscal_period_id: UUID,
        input: EffectivenessTestInput,
        created_by_user_id: UUID,
    ) -> EffectivenessTestResult:
        """
        Perform effectiveness test for a hedge relationship.

        Args:
            db: Database session
            organization_id: Organization scope
            hedge_id: Hedge to test
            fiscal_period_id: Fiscal period
            input: Test input data
            created_by_user_id: User performing test

        Returns:
            EffectivenessTestResult with test outcome
        """
        org_id = coerce_uuid(organization_id)
        h_id = coerce_uuid(hedge_id)
        period_id = coerce_uuid(fiscal_period_id)
        user_id = coerce_uuid(created_by_user_id)

        # Load hedge
        hedge = db.get(HedgeRelationship, h_id)
        if not hedge or hedge.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Hedge relationship not found")

        if hedge.status not in [HedgeStatus.ACTIVE, HedgeStatus.DESIGNATED]:
            raise HTTPException(
                status_code=400,
                detail="Hedge must be active or designated for testing",
            )

        # Calculate effectiveness ratio
        ratio = HedgeAccountingService.calculate_effectiveness_ratio(
            input.hedging_instrument_fv_change,
            input.hedged_item_fv_change,
        )

        # Determine if highly effective
        is_effective = HedgeAccountingService.is_highly_effective(ratio)
        retrospective_passed = is_effective and input.prospective_test_passed

        # Calculate ineffectiveness
        ineffectiveness, effective_portion = HedgeAccountingService.calculate_ineffectiveness(
            hedge.hedge_type,
            input.hedging_instrument_fv_change,
            input.hedged_item_fv_change,
        )

        # Determine P&L and OCI impacts
        ineffectiveness_pl = Decimal("0")
        effective_oci = Decimal("0")

        if hedge.hedge_type == HedgeType.FAIR_VALUE:
            # Fair value hedge: both changes to P&L
            ineffectiveness_pl = ineffectiveness
        elif hedge.hedge_type == HedgeType.CASH_FLOW:
            # Cash flow hedge: effective to OCI, ineffective to P&L
            ineffectiveness_pl = ineffectiveness
            effective_oci = effective_portion
        elif hedge.hedge_type == HedgeType.NET_INVESTMENT:
            # Net investment: effective to OCI
            ineffectiveness_pl = ineffectiveness
            effective_oci = effective_portion

        # Create effectiveness record
        effectiveness = HedgeEffectiveness(
            hedge_id=h_id,
            fiscal_period_id=period_id,
            test_date=input.test_date,
            prospective_test_passed=input.prospective_test_passed,
            prospective_test_result=input.prospective_test_result,
            prospective_test_notes=input.prospective_test_notes,
            hedging_instrument_fv_change=input.hedging_instrument_fv_change,
            hedged_item_fv_change=input.hedged_item_fv_change,
            hedge_effectiveness_ratio=ratio,
            retrospective_test_passed=retrospective_passed,
            hedge_ineffectiveness=abs(ineffectiveness),
            ineffectiveness_recognized_pl=ineffectiveness_pl,
            effective_portion=effective_portion,
            effective_portion_oci=effective_oci,
            is_highly_effective=is_effective,
            created_by_user_id=user_id,
        )

        db.add(effectiveness)

        # Update hedge reserves if cash flow hedge
        if hedge.hedge_type == HedgeType.CASH_FLOW:
            hedge.cash_flow_hedge_reserve += effective_oci

        # Update prospective test status on hedge
        hedge.prospective_test_passed = input.prospective_test_passed

        db.commit()
        db.refresh(effectiveness)

        return EffectivenessTestResult(
            effectiveness_id=effectiveness.effectiveness_id,
            hedge_effectiveness_ratio=ratio,
            is_highly_effective=is_effective,
            retrospective_test_passed=retrospective_passed,
            hedge_ineffectiveness=abs(ineffectiveness),
            effective_portion=effective_portion,
            ineffectiveness_recognized_pl=ineffectiveness_pl,
            effective_portion_oci=effective_oci,
        )

    @staticmethod
    def discontinue_hedge(
        db: Session,
        organization_id: UUID,
        hedge_id: UUID,
        discontinuation_date: date,
        reason: str = "VOLUNTARY",
    ) -> HedgeRelationship:
        """
        Discontinue a hedge relationship.

        Args:
            db: Database session
            organization_id: Organization scope
            hedge_id: Hedge to discontinue
            discontinuation_date: Date of discontinuation
            reason: Reason for discontinuation

        Returns:
            Updated HedgeRelationship
        """
        org_id = coerce_uuid(organization_id)
        h_id = coerce_uuid(hedge_id)

        hedge = db.get(HedgeRelationship, h_id)
        if not hedge or hedge.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Hedge relationship not found")

        if hedge.status not in [HedgeStatus.ACTIVE, HedgeStatus.DESIGNATED]:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot discontinue hedge with status '{hedge.status.value}'",
            )

        hedge.status = HedgeStatus.DISCONTINUED
        hedge.termination_date = discontinuation_date

        db.commit()
        db.refresh(hedge)

        return hedge

    @staticmethod
    def reclassify_to_pl(
        db: Session,
        organization_id: UUID,
        hedge_id: UUID,
        fiscal_period_id: UUID,
        reclassification_amount: Decimal,
        created_by_user_id: UUID,
    ) -> HedgeEffectiveness:
        """
        Reclassify amounts from OCI to P&L for cash flow hedges.

        Args:
            db: Database session
            organization_id: Organization scope
            hedge_id: Hedge relationship
            fiscal_period_id: Fiscal period
            reclassification_amount: Amount to reclassify
            created_by_user_id: User performing reclassification

        Returns:
            HedgeEffectiveness record with reclassification
        """
        org_id = coerce_uuid(organization_id)
        h_id = coerce_uuid(hedge_id)
        period_id = coerce_uuid(fiscal_period_id)
        user_id = coerce_uuid(created_by_user_id)

        hedge = db.get(HedgeRelationship, h_id)
        if not hedge or hedge.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Hedge relationship not found")

        if hedge.hedge_type != HedgeType.CASH_FLOW:
            raise HTTPException(
                status_code=400,
                detail="Reclassification only applies to cash flow hedges",
            )

        # Get or create effectiveness record for this period
        effectiveness = (
            db.query(HedgeEffectiveness)
            .filter(
                HedgeEffectiveness.hedge_id == h_id,
                HedgeEffectiveness.fiscal_period_id == period_id,
            )
            .first()
        )

        if effectiveness:
            effectiveness.reclassification_to_pl += reclassification_amount
        else:
            # Create minimal record for reclassification
            from datetime import date as date_type

            effectiveness = HedgeEffectiveness(
                hedge_id=h_id,
                fiscal_period_id=period_id,
                test_date=date_type.today(),
                prospective_test_passed=True,
                hedging_instrument_fv_change=Decimal("0"),
                hedged_item_fv_change=Decimal("0"),
                hedge_effectiveness_ratio=Decimal("1.0"),
                retrospective_test_passed=True,
                hedge_ineffectiveness=Decimal("0"),
                ineffectiveness_recognized_pl=Decimal("0"),
                effective_portion=Decimal("0"),
                effective_portion_oci=Decimal("0"),
                reclassification_to_pl=reclassification_amount,
                is_highly_effective=True,
                created_by_user_id=user_id,
            )
            db.add(effectiveness)

        # Update hedge reserve
        hedge.cash_flow_hedge_reserve -= reclassification_amount

        db.commit()
        db.refresh(effectiveness)

        return effectiveness

    @staticmethod
    def get(
        db: Session,
        hedge_id: str,
    ) -> HedgeRelationship:
        """Get a hedge relationship by ID."""
        hedge = db.get(HedgeRelationship, coerce_uuid(hedge_id))
        if not hedge:
            raise HTTPException(status_code=404, detail="Hedge relationship not found")
        return hedge

    @staticmethod
    def list(
        db: Session,
        organization_id: Optional[str] = None,
        hedge_type: Optional[HedgeType] = None,
        status: Optional[HedgeStatus] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[HedgeRelationship]:
        """List hedge relationships with optional filters."""
        query = db.query(HedgeRelationship)

        if organization_id:
            query = query.filter(
                HedgeRelationship.organization_id == coerce_uuid(organization_id)
            )

        if hedge_type:
            query = query.filter(HedgeRelationship.hedge_type == hedge_type)

        if status:
            query = query.filter(HedgeRelationship.status == status)

        query = query.order_by(HedgeRelationship.designation_date.desc())
        return query.limit(limit).offset(offset).all()

    @staticmethod
    def list_effectiveness_tests(
        db: Session,
        hedge_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> list[HedgeEffectiveness]:
        """List effectiveness tests for a hedge."""
        return (
            db.query(HedgeEffectiveness)
            .filter(HedgeEffectiveness.hedge_id == coerce_uuid(hedge_id))
            .order_by(HedgeEffectiveness.test_date.desc())
            .limit(limit)
            .offset(offset)
            .all()
        )

    @staticmethod
    def get_cash_flow_hedge_reserve_summary(
        db: Session,
        organization_id: str,
    ) -> dict:
        """
        Get summary of cash flow hedge reserves.

        Args:
            db: Database session
            organization_id: Organization scope

        Returns:
            Dict with reserve summary
        """
        from sqlalchemy import func

        org_id = coerce_uuid(organization_id)

        result = (
            db.query(
                func.sum(HedgeRelationship.cash_flow_hedge_reserve).label("total_reserve"),
                func.count(HedgeRelationship.hedge_id).label("active_hedges"),
            )
            .filter(
                HedgeRelationship.organization_id == org_id,
                HedgeRelationship.status == HedgeStatus.ACTIVE,
                HedgeRelationship.hedge_type == HedgeType.CASH_FLOW,
            )
            .first()
        )

        return {
            "total_cash_flow_hedge_reserve": result.total_reserve or Decimal("0"),
            "active_cash_flow_hedges": result.active_hedges or 0,
        }


# Module-level singleton instance
hedge_accounting_service = HedgeAccountingService()
