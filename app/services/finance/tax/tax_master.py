"""
Tax Master Services - Tax code and jurisdiction management.

Manages tax codes, rates, and jurisdiction configuration.
"""

from __future__ import annotations

import builtins
import logging
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.finance.tax.tax_code import TaxCode, TaxType
from app.models.finance.tax.tax_jurisdiction import TaxJurisdiction
from app.services.common import coerce_uuid
from app.services.response import ListResponseMixin

logger = logging.getLogger(__name__)


@dataclass
class TaxCodeInput:
    """Input for creating a tax code."""

    tax_code: str
    tax_name: str
    tax_type: TaxType
    jurisdiction_id: UUID
    tax_rate: Decimal
    effective_from: date
    description: str | None = None
    effective_to: date | None = None
    is_compound: bool = False
    is_inclusive: bool = False
    is_recoverable: bool = True
    recovery_rate: Decimal = Decimal("1.0")
    applies_to_purchases: bool = True
    applies_to_sales: bool = True
    tax_return_box: str | None = None
    reporting_code: str | None = None
    tax_collected_account_id: UUID | None = None
    tax_paid_account_id: UUID | None = None
    tax_expense_account_id: UUID | None = None


@dataclass
class TaxJurisdictionInput:
    """Input for creating a tax jurisdiction."""

    jurisdiction_code: str
    jurisdiction_name: str
    country_code: str
    jurisdiction_level: str
    current_tax_rate: Decimal
    tax_rate_effective_from: date
    currency_code: str
    current_tax_payable_account_id: UUID
    current_tax_expense_account_id: UUID
    deferred_tax_asset_account_id: UUID
    deferred_tax_liability_account_id: UUID
    deferred_tax_expense_account_id: UUID
    description: str | None = None
    state_province: str | None = None
    future_tax_rate: Decimal | None = None
    future_rate_effective_from: date | None = None
    has_reduced_rate: bool = False
    reduced_rate: Decimal | None = None
    reduced_rate_threshold: Decimal | None = None
    fiscal_year_end_month: int = 12
    filing_due_months: int = 6
    extension_months: int | None = None
    tax_authority_name: str | None = None
    tax_id_number: str | None = None


@dataclass
class TaxCalculationResult:
    """Result of tax calculation."""

    base_amount: Decimal
    tax_rate: Decimal
    tax_amount: Decimal
    recoverable_amount: Decimal
    non_recoverable_amount: Decimal
    total_amount: Decimal


class TaxCodeService(ListResponseMixin):
    """
    Service for tax code management.

    Handles tax code CRUD and tax calculations.
    """

    @staticmethod
    def create_tax_code(
        db: Session,
        organization_id: UUID,
        input: TaxCodeInput,
    ) -> TaxCode:
        """
        Create a new tax code.

        Args:
            db: Database session
            organization_id: Organization scope
            input: Tax code input data

        Returns:
            Created TaxCode
        """
        org_id = coerce_uuid(organization_id)

        # Check for duplicate
        existing = (
            db.query(TaxCode)
            .filter(
                TaxCode.organization_id == org_id,
                TaxCode.tax_code == input.tax_code,
            )
            .first()
        )
        if existing:
            raise HTTPException(
                status_code=400,
                detail=f"Tax code '{input.tax_code}' already exists",
            )

        tax_code = TaxCode(
            organization_id=org_id,
            tax_code=input.tax_code,
            tax_name=input.tax_name,
            description=input.description,
            tax_type=input.tax_type,
            jurisdiction_id=input.jurisdiction_id,
            tax_rate=input.tax_rate,
            effective_from=input.effective_from,
            effective_to=input.effective_to,
            is_compound=input.is_compound,
            is_inclusive=input.is_inclusive,
            is_recoverable=input.is_recoverable,
            recovery_rate=input.recovery_rate,
            applies_to_purchases=input.applies_to_purchases,
            applies_to_sales=input.applies_to_sales,
            tax_return_box=input.tax_return_box,
            reporting_code=input.reporting_code,
            tax_collected_account_id=input.tax_collected_account_id,
            tax_paid_account_id=input.tax_paid_account_id,
            tax_expense_account_id=input.tax_expense_account_id,
            is_active=True,
        )

        db.add(tax_code)
        db.commit()
        db.refresh(tax_code)

        return tax_code

    @staticmethod
    def calculate_tax(
        db: Session,
        organization_id: UUID,
        tax_code_id: UUID,
        base_amount: Decimal,
        transaction_date: date,
    ) -> TaxCalculationResult:
        """
        Calculate tax for a given base amount.

        Args:
            db: Database session
            organization_id: Organization scope
            tax_code_id: Tax code to use
            base_amount: Amount to calculate tax on
            transaction_date: Date for rate lookup

        Returns:
            TaxCalculationResult with calculated amounts
        """
        org_id = coerce_uuid(organization_id)
        tc_id = coerce_uuid(tax_code_id)

        tax_code = db.get(TaxCode, tc_id)
        if not tax_code or tax_code.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Tax code not found")

        if not tax_code.is_active:
            raise HTTPException(status_code=400, detail="Tax code is not active")

        # Check effective dates
        if transaction_date < tax_code.effective_from:
            raise HTTPException(
                status_code=400,
                detail="Transaction date is before tax code effective date",
            )
        if tax_code.effective_to and transaction_date > tax_code.effective_to:
            raise HTTPException(
                status_code=400,
                detail="Transaction date is after tax code expiry date",
            )

        # Calculate tax
        if tax_code.is_inclusive:
            # Tax is included in base amount
            tax_amount = (
                base_amount * tax_code.tax_rate / (Decimal("1") + tax_code.tax_rate)
            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            net_base = base_amount - tax_amount
        else:
            # Tax is additional
            net_base = base_amount
            tax_amount = (base_amount * tax_code.tax_rate).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )

        # Calculate recoverable portion
        if tax_code.is_recoverable:
            recoverable = (tax_amount * tax_code.recovery_rate).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
        else:
            recoverable = Decimal("0")

        non_recoverable = tax_amount - recoverable

        return TaxCalculationResult(
            base_amount=net_base,
            tax_rate=tax_code.tax_rate,
            tax_amount=tax_amount,
            recoverable_amount=recoverable,
            non_recoverable_amount=non_recoverable,
            total_amount=net_base + tax_amount,
        )

    @staticmethod
    def update_tax_rate(
        db: Session,
        organization_id: UUID,
        tax_code_id: UUID,
        new_rate: Decimal,
        effective_from: date,
    ) -> TaxCode:
        """
        Update tax rate with new effective date.

        Args:
            db: Database session
            organization_id: Organization scope
            tax_code_id: Tax code to update
            new_rate: New tax rate
            effective_from: Effective date of new rate

        Returns:
            Updated TaxCode
        """
        org_id = coerce_uuid(organization_id)
        tc_id = coerce_uuid(tax_code_id)

        tax_code = db.get(TaxCode, tc_id)
        if not tax_code or tax_code.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Tax code not found")

        # End current rate
        if effective_from > tax_code.effective_from:
            tax_code.effective_to = effective_from

        # Create new rate record (or update if same effective date)
        tax_code.tax_rate = new_rate
        tax_code.effective_from = effective_from
        tax_code.effective_to = None

        db.commit()
        db.refresh(tax_code)

        return tax_code

    @staticmethod
    def deactivate_tax_code(
        db: Session,
        organization_id: UUID,
        tax_code_id: UUID,
    ) -> TaxCode:
        """Deactivate a tax code."""
        org_id = coerce_uuid(organization_id)
        tc_id = coerce_uuid(tax_code_id)

        tax_code = db.get(TaxCode, tc_id)
        if not tax_code or tax_code.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Tax code not found")

        tax_code.is_active = False

        db.commit()
        db.refresh(tax_code)

        return tax_code

    @staticmethod
    def get(
        db: Session,
        tax_code_id: str,
        organization_id: UUID | None = None,
    ) -> TaxCode:
        """Get a tax code by ID."""
        tax_code = db.get(TaxCode, coerce_uuid(tax_code_id))
        if not tax_code:
            raise HTTPException(status_code=404, detail="Tax code not found")
        if organization_id is not None and tax_code.organization_id != coerce_uuid(
            organization_id
        ):
            raise HTTPException(status_code=404, detail="Tax code not found")
        return tax_code

    @staticmethod
    def get_by_code(
        db: Session,
        organization_id: str,
        code: str,
    ) -> TaxCode | None:
        """Get a tax code by code string."""
        return (
            db.query(TaxCode)
            .filter(
                TaxCode.organization_id == coerce_uuid(organization_id),
                TaxCode.tax_code == code,
            )
            .first()
        )

    @staticmethod
    def list(
        db: Session,
        organization_id: str | None = None,
        tax_type: TaxType | None = None,
        jurisdiction_id: str | None = None,
        is_active: bool | None = None,
        applies_to_purchases: bool | None = None,
        applies_to_sales: bool | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> builtins.list[TaxCode]:
        """List tax codes with optional filters."""
        query = db.query(TaxCode)

        if organization_id:
            query = query.filter(
                TaxCode.organization_id == coerce_uuid(organization_id)
            )

        if tax_type:
            query = query.filter(TaxCode.tax_type == tax_type)

        if jurisdiction_id:
            query = query.filter(
                TaxCode.jurisdiction_id == coerce_uuid(jurisdiction_id)
            )

        if is_active is not None:
            query = query.filter(TaxCode.is_active == is_active)

        if applies_to_purchases is not None:
            query = query.filter(TaxCode.applies_to_purchases == applies_to_purchases)

        if applies_to_sales is not None:
            query = query.filter(TaxCode.applies_to_sales == applies_to_sales)

        query = query.order_by(TaxCode.tax_code)
        return query.limit(limit).offset(offset).all()

    @staticmethod
    def get_effective_codes(
        db: Session,
        organization_id: str,
        as_of_date: date,
    ) -> builtins.list[TaxCode]:
        """Get all tax codes effective on a given date."""
        org_id = coerce_uuid(organization_id)

        return (
            db.query(TaxCode)
            .filter(
                TaxCode.organization_id == org_id,
                TaxCode.is_active == True,
                TaxCode.effective_from <= as_of_date,
                (TaxCode.effective_to.is_(None)) | (TaxCode.effective_to >= as_of_date),
            )
            .order_by(TaxCode.tax_code)
            .all()
        )


class TaxJurisdictionService(ListResponseMixin):
    """
    Service for tax jurisdiction management.

    Handles jurisdiction CRUD and tax rate lookups.
    """

    @staticmethod
    def create_jurisdiction(
        db: Session,
        organization_id: UUID,
        input: TaxJurisdictionInput,
    ) -> TaxJurisdiction:
        """
        Create a new tax jurisdiction.

        Args:
            db: Database session
            organization_id: Organization scope
            input: Jurisdiction input data

        Returns:
            Created TaxJurisdiction
        """
        org_id = coerce_uuid(organization_id)

        # Check for duplicate
        existing = (
            db.query(TaxJurisdiction)
            .filter(
                TaxJurisdiction.organization_id == org_id,
                TaxJurisdiction.jurisdiction_code == input.jurisdiction_code,
            )
            .first()
        )
        if existing:
            raise HTTPException(
                status_code=400,
                detail=f"Jurisdiction code '{input.jurisdiction_code}' already exists",
            )

        jurisdiction = TaxJurisdiction(
            organization_id=org_id,
            jurisdiction_code=input.jurisdiction_code,
            jurisdiction_name=input.jurisdiction_name,
            description=input.description,
            country_code=input.country_code,
            state_province=input.state_province,
            jurisdiction_level=input.jurisdiction_level,
            current_tax_rate=input.current_tax_rate,
            tax_rate_effective_from=input.tax_rate_effective_from,
            future_tax_rate=input.future_tax_rate,
            future_rate_effective_from=input.future_rate_effective_from,
            has_reduced_rate=input.has_reduced_rate,
            reduced_rate=input.reduced_rate,
            reduced_rate_threshold=input.reduced_rate_threshold,
            fiscal_year_end_month=input.fiscal_year_end_month,
            filing_due_months=input.filing_due_months,
            extension_months=input.extension_months,
            currency_code=input.currency_code,
            tax_authority_name=input.tax_authority_name,
            tax_id_number=input.tax_id_number,
            current_tax_payable_account_id=input.current_tax_payable_account_id,
            current_tax_expense_account_id=input.current_tax_expense_account_id,
            deferred_tax_asset_account_id=input.deferred_tax_asset_account_id,
            deferred_tax_liability_account_id=input.deferred_tax_liability_account_id,
            deferred_tax_expense_account_id=input.deferred_tax_expense_account_id,
            is_active=True,
        )

        db.add(jurisdiction)
        db.commit()
        db.refresh(jurisdiction)

        return jurisdiction

    @staticmethod
    def get_applicable_rate(
        db: Session,
        organization_id: UUID,
        jurisdiction_id: UUID,
        as_of_date: date,
        taxable_income: Decimal | None = None,
    ) -> Decimal:
        """
        Get applicable tax rate for a jurisdiction.

        Considers future rates and reduced rates for small business.

        Args:
            db: Database session
            organization_id: Organization scope
            jurisdiction_id: Jurisdiction to get rate for
            as_of_date: Date for rate lookup
            taxable_income: Income for reduced rate check

        Returns:
            Applicable tax rate
        """
        org_id = coerce_uuid(organization_id)
        jur_id = coerce_uuid(jurisdiction_id)

        jurisdiction = db.get(TaxJurisdiction, jur_id)
        if not jurisdiction or jurisdiction.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Jurisdiction not found")

        # Check if future rate applies
        if (
            jurisdiction.future_tax_rate
            and jurisdiction.future_rate_effective_from
            and as_of_date >= jurisdiction.future_rate_effective_from
        ):
            base_rate = jurisdiction.future_tax_rate
        else:
            base_rate = jurisdiction.current_tax_rate

        # Check for reduced rate
        if (
            jurisdiction.has_reduced_rate
            and jurisdiction.reduced_rate
            and jurisdiction.reduced_rate_threshold
            and taxable_income
            and taxable_income <= jurisdiction.reduced_rate_threshold
        ):
            return jurisdiction.reduced_rate

        return base_rate

    @staticmethod
    def update_future_rate(
        db: Session,
        organization_id: UUID,
        jurisdiction_id: UUID,
        future_rate: Decimal,
        effective_from: date,
    ) -> TaxJurisdiction:
        """
        Set a future tax rate for a jurisdiction.

        Args:
            db: Database session
            organization_id: Organization scope
            jurisdiction_id: Jurisdiction to update
            future_rate: New future tax rate
            effective_from: Effective date of future rate

        Returns:
            Updated TaxJurisdiction
        """
        org_id = coerce_uuid(organization_id)
        jur_id = coerce_uuid(jurisdiction_id)

        jurisdiction = db.get(TaxJurisdiction, jur_id)
        if not jurisdiction or jurisdiction.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Jurisdiction not found")

        jurisdiction.future_tax_rate = future_rate
        jurisdiction.future_rate_effective_from = effective_from

        db.commit()
        db.refresh(jurisdiction)

        return jurisdiction

    @staticmethod
    def apply_future_rate(
        db: Session,
        organization_id: UUID,
        jurisdiction_id: UUID,
    ) -> TaxJurisdiction:
        """
        Apply the future rate as the current rate.

        Args:
            db: Database session
            organization_id: Organization scope
            jurisdiction_id: Jurisdiction to update

        Returns:
            Updated TaxJurisdiction
        """
        org_id = coerce_uuid(organization_id)
        jur_id = coerce_uuid(jurisdiction_id)

        jurisdiction = db.get(TaxJurisdiction, jur_id)
        if not jurisdiction or jurisdiction.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Jurisdiction not found")

        if not jurisdiction.future_tax_rate:
            raise HTTPException(status_code=400, detail="No future rate defined")

        jurisdiction.current_tax_rate = jurisdiction.future_tax_rate
        jurisdiction.tax_rate_effective_from = (
            jurisdiction.future_rate_effective_from or date.today()
        )
        jurisdiction.future_tax_rate = None
        jurisdiction.future_rate_effective_from = None

        db.commit()
        db.refresh(jurisdiction)

        return jurisdiction

    @staticmethod
    def get(
        db: Session,
        jurisdiction_id: str,
        organization_id: UUID | None = None,
    ) -> TaxJurisdiction:
        """Get a jurisdiction by ID."""
        jurisdiction = db.get(TaxJurisdiction, coerce_uuid(jurisdiction_id))
        if not jurisdiction:
            raise HTTPException(status_code=404, detail="Jurisdiction not found")
        if organization_id is not None and jurisdiction.organization_id != coerce_uuid(
            organization_id
        ):
            raise HTTPException(status_code=404, detail="Jurisdiction not found")
        return jurisdiction

    @staticmethod
    def get_by_code(
        db: Session,
        organization_id: str,
        code: str,
    ) -> TaxJurisdiction | None:
        """Get a jurisdiction by code string."""
        return (
            db.query(TaxJurisdiction)
            .filter(
                TaxJurisdiction.organization_id == coerce_uuid(organization_id),
                TaxJurisdiction.jurisdiction_code == code,
            )
            .first()
        )

    @staticmethod
    def list(
        db: Session,
        organization_id: str | None = None,
        country_code: str | None = None,
        jurisdiction_level: str | None = None,
        is_active: bool | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> builtins.list[TaxJurisdiction]:
        """List jurisdictions with optional filters."""
        query = db.query(TaxJurisdiction)

        if organization_id:
            query = query.filter(
                TaxJurisdiction.organization_id == coerce_uuid(organization_id)
            )

        if country_code:
            query = query.filter(TaxJurisdiction.country_code == country_code)

        if jurisdiction_level:
            query = query.filter(
                TaxJurisdiction.jurisdiction_level == jurisdiction_level
            )

        if is_active is not None:
            query = query.filter(TaxJurisdiction.is_active == is_active)

        query = query.order_by(TaxJurisdiction.jurisdiction_code)
        return query.limit(limit).offset(offset).all()

    @staticmethod
    def deactivate_jurisdiction(
        db: Session,
        organization_id: UUID,
        jurisdiction_id: UUID,
    ) -> TaxJurisdiction:
        """
        Deactivate a tax jurisdiction (soft delete).

        The jurisdiction cannot be deactivated if it has active tax codes.

        Args:
            db: Database session
            organization_id: Organization scope
            jurisdiction_id: Jurisdiction to deactivate

        Returns:
            Updated TaxJurisdiction

        Raises:
            HTTPException: If jurisdiction not found or has active tax codes
        """
        org_id = coerce_uuid(organization_id)
        jur_id = coerce_uuid(jurisdiction_id)

        jurisdiction = db.get(TaxJurisdiction, jur_id)
        if not jurisdiction or jurisdiction.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Jurisdiction not found")

        if not jurisdiction.is_active:
            raise HTTPException(
                status_code=400, detail="Jurisdiction is already inactive"
            )

        # Check for active tax codes in this jurisdiction
        active_codes = (
            db.query(TaxCode)
            .filter(
                TaxCode.jurisdiction_id == jur_id,
                TaxCode.is_active == True,
            )
            .count()
        )

        if active_codes > 0:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot deactivate jurisdiction with {active_codes} active tax codes",
            )

        jurisdiction.is_active = False
        db.commit()
        db.refresh(jurisdiction)

        return jurisdiction


# Module-level singleton instances
tax_code_service = TaxCodeService()
tax_jurisdiction_service = TaxJurisdictionService()
