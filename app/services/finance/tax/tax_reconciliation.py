"""
TaxReconciliationService - IAS 12 tax rate reconciliation.

Prepares tax rate reconciliation for financial statement disclosures.
"""

from __future__ import annotations

import builtins
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.finance.tax.tax_jurisdiction import TaxJurisdiction
from app.models.finance.tax.tax_reconciliation import TaxReconciliation
from app.services.common import coerce_uuid
from app.services.response import ListResponseMixin

logger = logging.getLogger(__name__)


@dataclass
class TaxReconciliationInput:
    """Input for creating a tax reconciliation."""

    fiscal_period_id: UUID
    jurisdiction_id: UUID
    profit_before_tax: Decimal
    current_tax_expense: Decimal
    deferred_tax_expense: Decimal
    permanent_differences: Decimal = Decimal("0")
    non_deductible_expenses: Decimal = Decimal("0")
    non_taxable_income: Decimal = Decimal("0")
    rate_differential_on_foreign_income: Decimal = Decimal("0")
    tax_credits_utilized: Decimal = Decimal("0")
    change_in_unrecognized_dta: Decimal = Decimal("0")
    effect_of_tax_rate_change: Decimal = Decimal("0")
    prior_year_adjustments: Decimal = Decimal("0")
    other_reconciling_items: Decimal = Decimal("0")
    other_items_description: str | None = None
    notes: str | None = None


@dataclass
class ReconciliationLine:
    """Single line in tax reconciliation."""

    description: str
    amount: Decimal
    rate_effect: Decimal


class TaxReconciliationService(ListResponseMixin):
    """
    Service for IAS 12 tax rate reconciliation.

    Handles:
    - Tax expense reconciliation preparation
    - Effective tax rate calculation
    - Disclosure-ready output
    """

    @staticmethod
    def calculate_effective_tax_rate(
        total_tax_expense: Decimal,
        profit_before_tax: Decimal,
    ) -> Decimal:
        """
        Calculate effective tax rate.

        Args:
            total_tax_expense: Total income tax expense
            profit_before_tax: Profit before tax

        Returns:
            Effective tax rate as decimal
        """
        if profit_before_tax == 0:
            return Decimal("0")

        return (total_tax_expense / profit_before_tax).quantize(
            Decimal("0.000001"), rounding=ROUND_HALF_UP
        )

    @staticmethod
    def create_reconciliation(
        db: Session,
        organization_id: UUID,
        input: TaxReconciliationInput,
        prepared_by_user_id: UUID,
    ) -> TaxReconciliation:
        """
        Create a tax reconciliation.

        Args:
            db: Database session
            organization_id: Organization scope
            input: Reconciliation input data
            prepared_by_user_id: User preparing the reconciliation

        Returns:
            Created TaxReconciliation
        """
        org_id = coerce_uuid(organization_id)
        user_id = coerce_uuid(prepared_by_user_id)

        # Get jurisdiction for statutory rate
        jurisdiction = db.get(TaxJurisdiction, input.jurisdiction_id)
        if not jurisdiction or jurisdiction.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Jurisdiction not found")

        # Check for existing reconciliation
        existing = (
            db.query(TaxReconciliation)
            .filter(
                TaxReconciliation.organization_id == org_id,
                TaxReconciliation.fiscal_period_id == input.fiscal_period_id,
                TaxReconciliation.jurisdiction_id == input.jurisdiction_id,
            )
            .first()
        )
        if existing:
            raise HTTPException(
                status_code=400,
                detail="Reconciliation already exists for this period and jurisdiction",
            )

        statutory_rate = jurisdiction.current_tax_rate
        tax_at_statutory_rate = (input.profit_before_tax * statutory_rate).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

        total_tax_expense = input.current_tax_expense + input.deferred_tax_expense
        effective_rate = TaxReconciliationService.calculate_effective_tax_rate(
            total_tax_expense, input.profit_before_tax
        )
        rate_variance = effective_rate - statutory_rate

        reconciliation = TaxReconciliation(
            organization_id=org_id,
            fiscal_period_id=input.fiscal_period_id,
            jurisdiction_id=input.jurisdiction_id,
            profit_before_tax=input.profit_before_tax,
            statutory_tax_rate=statutory_rate,
            tax_at_statutory_rate=tax_at_statutory_rate,
            permanent_differences=input.permanent_differences,
            non_deductible_expenses=input.non_deductible_expenses,
            non_taxable_income=input.non_taxable_income,
            rate_differential_on_foreign_income=input.rate_differential_on_foreign_income,
            tax_credits_utilized=input.tax_credits_utilized,
            change_in_unrecognized_dta=input.change_in_unrecognized_dta,
            effect_of_tax_rate_change=input.effect_of_tax_rate_change,
            prior_year_adjustments=input.prior_year_adjustments,
            other_reconciling_items=input.other_reconciling_items,
            other_items_description=input.other_items_description,
            total_tax_expense=total_tax_expense,
            current_tax_expense=input.current_tax_expense,
            deferred_tax_expense=input.deferred_tax_expense,
            effective_tax_rate=effective_rate,
            rate_variance=rate_variance,
            notes=input.notes,
            prepared_by_user_id=user_id,
        )

        db.add(reconciliation)
        db.commit()
        db.refresh(reconciliation)

        return reconciliation

    @staticmethod
    def review_reconciliation(
        db: Session,
        organization_id: UUID,
        reconciliation_id: UUID,
        reviewed_by_user_id: UUID,
    ) -> TaxReconciliation:
        """
        Mark a reconciliation as reviewed.

        Args:
            db: Database session
            organization_id: Organization scope
            reconciliation_id: Reconciliation to review
            reviewed_by_user_id: User reviewing

        Returns:
            Updated TaxReconciliation
        """
        org_id = coerce_uuid(organization_id)
        rec_id = coerce_uuid(reconciliation_id)
        user_id = coerce_uuid(reviewed_by_user_id)

        reconciliation = db.get(TaxReconciliation, rec_id)
        if not reconciliation or reconciliation.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Reconciliation not found")

        # SoD check
        if reconciliation.prepared_by_user_id == user_id:
            raise HTTPException(
                status_code=400,
                detail="Segregation of duties violation: preparer cannot review",
            )

        reconciliation.reviewed_by_user_id = user_id
        reconciliation.reviewed_at = datetime.now(UTC)

        db.commit()
        db.refresh(reconciliation)

        return reconciliation

    @staticmethod
    def get_reconciliation_lines(
        reconciliation: TaxReconciliation,
    ) -> list[ReconciliationLine]:
        """
        Get reconciliation as a list of lines for disclosure.

        Args:
            reconciliation: TaxReconciliation record

        Returns:
            List of ReconciliationLine for display
        """
        lines = []

        # Starting point
        lines.append(
            ReconciliationLine(
                description="Tax at statutory rate",
                amount=reconciliation.tax_at_statutory_rate,
                rate_effect=reconciliation.statutory_tax_rate,
            )
        )

        # Reconciling items
        if reconciliation.permanent_differences != 0:
            rate_effect = (
                reconciliation.permanent_differences / reconciliation.profit_before_tax
                if reconciliation.profit_before_tax != 0
                else Decimal("0")
            )
            lines.append(
                ReconciliationLine(
                    description="Permanent differences",
                    amount=reconciliation.permanent_differences,
                    rate_effect=rate_effect,
                )
            )

        if reconciliation.non_deductible_expenses != 0:
            rate_effect = (
                reconciliation.non_deductible_expenses
                / reconciliation.profit_before_tax
                if reconciliation.profit_before_tax != 0
                else Decimal("0")
            )
            lines.append(
                ReconciliationLine(
                    description="Non-deductible expenses",
                    amount=reconciliation.non_deductible_expenses,
                    rate_effect=rate_effect,
                )
            )

        if reconciliation.non_taxable_income != 0:
            rate_effect = (
                -reconciliation.non_taxable_income / reconciliation.profit_before_tax
                if reconciliation.profit_before_tax != 0
                else Decimal("0")
            )
            lines.append(
                ReconciliationLine(
                    description="Non-taxable income",
                    amount=-reconciliation.non_taxable_income,
                    rate_effect=rate_effect,
                )
            )

        if reconciliation.rate_differential_on_foreign_income != 0:
            rate_effect = (
                reconciliation.rate_differential_on_foreign_income
                / reconciliation.profit_before_tax
                if reconciliation.profit_before_tax != 0
                else Decimal("0")
            )
            lines.append(
                ReconciliationLine(
                    description="Rate differential on foreign income",
                    amount=reconciliation.rate_differential_on_foreign_income,
                    rate_effect=rate_effect,
                )
            )

        if reconciliation.tax_credits_utilized != 0:
            rate_effect = (
                -reconciliation.tax_credits_utilized / reconciliation.profit_before_tax
                if reconciliation.profit_before_tax != 0
                else Decimal("0")
            )
            lines.append(
                ReconciliationLine(
                    description="Tax credits utilized",
                    amount=-reconciliation.tax_credits_utilized,
                    rate_effect=rate_effect,
                )
            )

        if reconciliation.change_in_unrecognized_dta != 0:
            rate_effect = (
                reconciliation.change_in_unrecognized_dta
                / reconciliation.profit_before_tax
                if reconciliation.profit_before_tax != 0
                else Decimal("0")
            )
            lines.append(
                ReconciliationLine(
                    description="Change in unrecognized deferred tax assets",
                    amount=reconciliation.change_in_unrecognized_dta,
                    rate_effect=rate_effect,
                )
            )

        if reconciliation.effect_of_tax_rate_change != 0:
            rate_effect = (
                reconciliation.effect_of_tax_rate_change
                / reconciliation.profit_before_tax
                if reconciliation.profit_before_tax != 0
                else Decimal("0")
            )
            lines.append(
                ReconciliationLine(
                    description="Effect of tax rate changes",
                    amount=reconciliation.effect_of_tax_rate_change,
                    rate_effect=rate_effect,
                )
            )

        if reconciliation.prior_year_adjustments != 0:
            rate_effect = (
                reconciliation.prior_year_adjustments / reconciliation.profit_before_tax
                if reconciliation.profit_before_tax != 0
                else Decimal("0")
            )
            lines.append(
                ReconciliationLine(
                    description="Prior year adjustments",
                    amount=reconciliation.prior_year_adjustments,
                    rate_effect=rate_effect,
                )
            )

        if reconciliation.other_reconciling_items != 0:
            rate_effect = (
                reconciliation.other_reconciling_items
                / reconciliation.profit_before_tax
                if reconciliation.profit_before_tax != 0
                else Decimal("0")
            )
            lines.append(
                ReconciliationLine(
                    description=reconciliation.other_items_description or "Other items",
                    amount=reconciliation.other_reconciling_items,
                    rate_effect=rate_effect,
                )
            )

        # Total
        lines.append(
            ReconciliationLine(
                description="Total tax expense",
                amount=reconciliation.total_tax_expense,
                rate_effect=reconciliation.effective_tax_rate,
            )
        )

        return lines

    @staticmethod
    def validate_reconciliation(
        reconciliation: TaxReconciliation,
    ) -> tuple[bool, str | None]:
        """
        Validate that reconciliation balances.

        Args:
            reconciliation: TaxReconciliation to validate

        Returns:
            Tuple of (is_valid, error_message)
        """
        calculated_total = (
            reconciliation.tax_at_statutory_rate
            + reconciliation.permanent_differences
            + reconciliation.non_deductible_expenses
            - reconciliation.non_taxable_income
            + reconciliation.rate_differential_on_foreign_income
            - reconciliation.tax_credits_utilized
            + reconciliation.change_in_unrecognized_dta
            + reconciliation.effect_of_tax_rate_change
            + reconciliation.prior_year_adjustments
            + reconciliation.other_reconciling_items
        )

        tolerance = Decimal("0.01")
        difference = abs(calculated_total - reconciliation.total_tax_expense)

        if difference > tolerance:
            return (
                False,
                f"Reconciliation does not balance. Calculated: {calculated_total}, Actual: {reconciliation.total_tax_expense}, Difference: {difference}",
            )

        return (True, None)

    @staticmethod
    def get(
        db: Session,
        reconciliation_id: str,
        organization_id: UUID | None = None,
    ) -> TaxReconciliation:
        """Get a reconciliation by ID."""
        reconciliation = db.get(TaxReconciliation, coerce_uuid(reconciliation_id))
        if not reconciliation:
            raise HTTPException(status_code=404, detail="Reconciliation not found")
        if (
            organization_id is not None
            and reconciliation.organization_id != coerce_uuid(organization_id)
        ):
            raise HTTPException(status_code=404, detail="Reconciliation not found")
        return reconciliation

    @staticmethod
    def get_by_period_jurisdiction(
        db: Session,
        organization_id: str,
        fiscal_period_id: str,
        jurisdiction_id: str,
    ) -> TaxReconciliation | None:
        """Get reconciliation by period and jurisdiction."""
        return (
            db.query(TaxReconciliation)
            .filter(
                TaxReconciliation.organization_id == coerce_uuid(organization_id),
                TaxReconciliation.fiscal_period_id == coerce_uuid(fiscal_period_id),
                TaxReconciliation.jurisdiction_id == coerce_uuid(jurisdiction_id),
            )
            .first()
        )

    @staticmethod
    def list(
        db: Session,
        organization_id: str | None = None,
        fiscal_period_id: str | None = None,
        jurisdiction_id: str | None = None,
        is_reviewed: bool | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> builtins.list[TaxReconciliation]:
        """List reconciliations with optional filters."""
        query = db.query(TaxReconciliation)

        if organization_id:
            query = query.filter(
                TaxReconciliation.organization_id == coerce_uuid(organization_id)
            )

        if fiscal_period_id:
            query = query.filter(
                TaxReconciliation.fiscal_period_id == coerce_uuid(fiscal_period_id)
            )

        if jurisdiction_id:
            query = query.filter(
                TaxReconciliation.jurisdiction_id == coerce_uuid(jurisdiction_id)
            )

        if is_reviewed is not None:
            if is_reviewed:
                query = query.filter(TaxReconciliation.reviewed_by_user_id.isnot(None))
            else:
                query = query.filter(TaxReconciliation.reviewed_by_user_id.is_(None))

        query = query.order_by(TaxReconciliation.created_at.desc())
        return query.limit(limit).offset(offset).all()


# Module-level singleton instance
tax_reconciliation_service = TaxReconciliationService()
