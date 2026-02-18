"""
PAYE Calculator Service - Nigeria Tax Act 2025 Implementation.

This module implements the Nigerian PAYE (Pay As You Earn) tax calculation
based on the Nigeria Tax Act 2025 (NTA), effective January 2026.

Key Features:
- Progressive tax bands (0%, 15%, 18%, 21%, 23%, 25%)
- Rent relief (20% of annual rent, max ₦500,000)
- Statutory deductions (Pension 8%, NHF 2.5%, NHIS variable)
- Annualized calculation with monthly proration

Example Calculation:
    Gross Monthly: ₦500,000 | Basic Monthly: ₦300,000 | Annual Rent: ₦1,200,000

    Annual Gross:     ₦6,000,000
    Pension (8%):    -₦288,000   (of ₦3.6M basic)
    NHF (2.5%):      -₦90,000
    Rent Relief:     -₦240,000   (20% of ₦1.2M)
    ───────────────────────────────────────
    Taxable Income:   ₦5,382,000

    Tax Calculation:
    Band 1: ₦0-800K @ 0%        = ₦0
    Band 2: ₦800K-3M @ 15%      = ₦330,000
    Band 3: ₦3M-5.382M @ 18%    = ₦428,760
    ───────────────────────────────────────
    Annual Tax:       ₦758,760
    Monthly PAYE:     ₦63,230
    Effective Rate:   12.6%
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.people.payroll.employee_tax_profile import EmployeeTaxProfile
from app.models.people.payroll.tax_band import TaxBand
from app.services.common import coerce_uuid

logger = logging.getLogger(__name__)


@dataclass
class TaxBandBreakdown:
    """Tax calculation breakdown for a single band."""

    band_name: str
    min_amount: Decimal
    max_amount: Decimal | None
    rate: Decimal
    taxable_in_band: Decimal
    tax_amount: Decimal

    @property
    def rate_percent(self) -> Decimal:
        return self.rate * 100

    @property
    def range_display(self) -> str:
        min_fmt = f"₦{self.min_amount:,.0f}"
        if self.max_amount is None:
            return f"{min_fmt}+"
        max_fmt = f"₦{self.max_amount:,.0f}"
        return f"{min_fmt} - {max_fmt}"


@dataclass
class PAYEBreakdown:
    """
    Complete PAYE calculation breakdown.

    Provides all details of the tax calculation for transparency
    and audit purposes.
    """

    # Input amounts (annual)
    annual_gross: Decimal
    annual_basic: Decimal
    annual_rent: Decimal

    # Statutory deductions (annual)
    pension_amount: Decimal
    pension_rate: Decimal
    employer_pension_amount: Decimal
    employer_pension_rate: Decimal
    nhf_amount: Decimal
    nhf_rate: Decimal
    nhis_amount: Decimal
    nhis_rate: Decimal
    total_statutory: Decimal

    # Rent relief
    rent_relief: Decimal

    # Taxable income
    taxable_income: Decimal
    rent_relief_rate: Decimal = Decimal("0.20")

    # Tax calculation
    band_breakdowns: list[TaxBandBreakdown] = field(default_factory=list)
    annual_tax: Decimal = Decimal("0")

    # Monthly amounts
    monthly_tax: Decimal = Decimal("0")
    monthly_pension: Decimal = Decimal("0")
    monthly_employer_pension: Decimal = Decimal("0")
    monthly_nhf: Decimal = Decimal("0")
    monthly_nhis: Decimal = Decimal("0")

    # Effective rate
    effective_rate: Decimal = Decimal("0")

    # Employee profile info
    employee_id: UUID | None = None
    tin: str | None = None
    tax_state: str | None = None
    is_tax_exempt: bool = False

    @property
    def effective_rate_percent(self) -> Decimal:
        return self.effective_rate * 100

    # Aliases for TaxResult interface compatibility
    @property
    def monthly_social_security(self) -> Decimal:
        """Alias for monthly_nhf (NHF is Nigeria's social security equivalent)."""
        return self.monthly_nhf

    @property
    def monthly_health_insurance(self) -> Decimal:
        """Alias for monthly_nhis (NHIS is Nigeria's health insurance)."""
        return self.monthly_nhis

    @property
    def total_monthly_deductions(self) -> Decimal:
        """Total monthly statutory deductions (tax + pension + nhf + nhis)."""
        return (
            self.monthly_tax
            + self.monthly_pension
            + self.monthly_nhf
            + self.monthly_nhis
        )

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "annual_gross": str(self.annual_gross),
            "annual_basic": str(self.annual_basic),
            "annual_rent": str(self.annual_rent),
            "pension_amount": str(self.pension_amount),
            "pension_rate": str(self.pension_rate),
            "employer_pension_amount": str(self.employer_pension_amount),
            "employer_pension_rate": str(self.employer_pension_rate),
            "nhf_amount": str(self.nhf_amount),
            "nhf_rate": str(self.nhf_rate),
            "nhis_amount": str(self.nhis_amount),
            "nhis_rate": str(self.nhis_rate),
            "total_statutory": str(self.total_statutory),
            "rent_relief": str(self.rent_relief),
            "taxable_income": str(self.taxable_income),
            "annual_tax": str(self.annual_tax),
            "monthly_tax": str(self.monthly_tax),
            "monthly_pension": str(self.monthly_pension),
            "monthly_employer_pension": str(self.monthly_employer_pension),
            "monthly_nhf": str(self.monthly_nhf),
            "monthly_nhis": str(self.monthly_nhis),
            "effective_rate": str(self.effective_rate),
            "effective_rate_percent": str(self.effective_rate_percent),
            "band_breakdowns": [
                {
                    "band_name": b.band_name,
                    "range": b.range_display,
                    "rate_percent": str(b.rate_percent),
                    "taxable_in_band": str(b.taxable_in_band),
                    "tax_amount": str(b.tax_amount),
                }
                for b in self.band_breakdowns
            ],
            "employee_id": str(self.employee_id) if self.employee_id else None,
            "tin": self.tin,
            "tax_state": self.tax_state,
            "is_tax_exempt": self.is_tax_exempt,
        }


class PAYECalculator:
    """
    NTA 2025 PAYE Calculator.

    Implements Nigerian PAYE tax calculation following the Nigeria Tax Act 2025.

    The calculation follows these steps:
    1. Annualize gross and basic salaries
    2. Calculate statutory deductions (Pension 8%, NHF 2.5%, NHIS)
    3. Calculate rent relief (20% of rent, max ₦500,000)
    4. Compute taxable income
    5. Apply progressive tax bands
    6. Prorate to monthly amount
    """

    # NTA 2025 Constants
    RENT_RELIEF_RATE = Decimal("0.20")  # 20%
    RENT_RELIEF_MAX = Decimal("500000")  # ₦500,000 per year
    DEFAULT_PENSION_RATE = Decimal("0.08")  # 8%
    DEFAULT_EMPLOYER_PENSION_RATE = Decimal("0.10")  # 10%
    DEFAULT_NHF_RATE = Decimal("0.025")  # 2.5%
    DEFAULT_NHIS_RATE = Decimal("0")  # Variable
    MONTHS_PER_YEAR = Decimal("12")

    # NTA 2025 Default Tax Bands
    NTA_2025_BANDS = [
        {"name": "NTA 2025 - 0%", "min": 0, "max": 800000, "rate": "0.00", "seq": 1},
        {
            "name": "NTA 2025 - 15%",
            "min": 800000,
            "max": 3000000,
            "rate": "0.15",
            "seq": 2,
        },
        {
            "name": "NTA 2025 - 18%",
            "min": 3000000,
            "max": 12000000,
            "rate": "0.18",
            "seq": 3,
        },
        {
            "name": "NTA 2025 - 21%",
            "min": 12000000,
            "max": 25000000,
            "rate": "0.21",
            "seq": 4,
        },
        {
            "name": "NTA 2025 - 23%",
            "min": 25000000,
            "max": 50000000,
            "rate": "0.23",
            "seq": 5,
        },
        {
            "name": "NTA 2025 - 25%",
            "min": 50000000,
            "max": None,
            "rate": "0.25",
            "seq": 6,
        },
    ]

    def __init__(self, db: Session):
        self.db = db

    @classmethod
    def _create_band_from_def(
        cls,
        band_def: dict,
        organization_id: UUID,
        effective_from: date,
        created_by_id: UUID | None = None,
    ) -> TaxBand:
        """
        Create a TaxBand instance from a band definition dict.

        This is a helper to avoid duplicating band creation logic.
        """
        return TaxBand(
            organization_id=organization_id,
            name=band_def["name"],
            min_amount=Decimal(str(band_def["min"])),
            max_amount=(Decimal(str(band_def["max"])) if band_def["max"] else None),
            rate=Decimal(band_def["rate"]),
            sequence=band_def["seq"],
            effective_from=effective_from,
            is_active=True,
            created_by_id=created_by_id,
        )

    def calculate(
        self,
        organization_id: UUID,
        gross_monthly: Decimal,
        basic_monthly: Decimal,
        employee_id: UUID | None = None,
        annual_rent: Decimal | None = None,
        rent_verified: bool = False,
        pension_rate: Decimal | None = None,
        employer_pension_rate: Decimal | None = None,
        nhf_rate: Decimal | None = None,
        nhis_rate: Decimal | None = None,
        as_of_date: date | None = None,
    ) -> PAYEBreakdown:
        """
        Calculate PAYE tax for given income.

        Args:
            organization_id: Organization for tax band lookup
            gross_monthly: Monthly gross salary
            basic_monthly: Monthly basic salary (for statutory deductions)
            employee_id: Optional employee ID for profile lookup
            annual_rent: Annual rent (for rent relief)
            rent_verified: Whether rent documentation is verified
            pension_rate: Override pension rate (default 8%)
            nhf_rate: Override NHF rate (default 2.5%)
            nhis_rate: Override NHIS rate (default 0%)
            as_of_date: Date for tax band lookup (default today)

        Returns:
            PAYEBreakdown with complete calculation details
        """
        org_id = coerce_uuid(organization_id)
        calc_date = as_of_date or date.today()

        # Get employee tax profile if employee_id provided
        profile = None
        if employee_id:
            profile = self._get_tax_profile(org_id, employee_id, calc_date)

        # Use profile values or provided/default values
        _pension_rate = (
            pension_rate
            if pension_rate is not None
            else (profile.pension_rate if profile else self.DEFAULT_PENSION_RATE)
        )
        _employer_pension_rate = (
            employer_pension_rate
            if employer_pension_rate is not None
            else self.DEFAULT_EMPLOYER_PENSION_RATE
        )
        _nhf_rate = (
            nhf_rate
            if nhf_rate is not None
            else (profile.nhf_rate if profile else self.DEFAULT_NHF_RATE)
        )
        _nhis_rate = (
            nhis_rate
            if nhis_rate is not None
            else (profile.nhis_rate if profile else self.DEFAULT_NHIS_RATE)
        )
        _annual_rent = (
            annual_rent
            if annual_rent is not None
            else (profile.annual_rent if profile else Decimal("0"))
        )
        _rent_verified = rent_verified or (
            profile.rent_receipt_verified if profile else False
        )

        # Check for tax exemption
        is_exempt = profile.is_tax_exempt if profile else False

        # Annualize amounts
        annual_gross = gross_monthly * self.MONTHS_PER_YEAR
        annual_basic = basic_monthly * self.MONTHS_PER_YEAR

        # Calculate statutory deductions (based on basic salary)
        pension_amount = annual_basic * _pension_rate
        employer_pension_amount = annual_basic * _employer_pension_rate
        nhf_amount = annual_basic * _nhf_rate
        nhis_amount = annual_basic * _nhis_rate
        total_statutory = pension_amount + nhf_amount + nhis_amount

        # Calculate rent relief
        rent_relief = Decimal("0")
        if _rent_verified and _annual_rent > 0:
            calculated_relief = _annual_rent * self.RENT_RELIEF_RATE
            rent_relief = min(calculated_relief, self.RENT_RELIEF_MAX)

        # Calculate taxable income
        taxable_income = annual_gross - total_statutory - rent_relief
        taxable_income = max(taxable_income, Decimal("0"))  # Can't be negative

        # Get tax bands
        bands = self._get_tax_bands(org_id, calc_date)

        # Calculate tax using bands
        band_breakdowns = []
        annual_tax = Decimal("0")

        if not is_exempt:
            for band in bands:
                tax_in_band = band.calculate_tax(taxable_income)
                if tax_in_band > 0 or band.min_amount < taxable_income:
                    # Determine taxable amount in this band
                    if taxable_income <= band.min_amount:
                        taxable_in_band = Decimal("0")
                    elif band.max_amount is None:
                        taxable_in_band = taxable_income - band.min_amount
                    else:
                        upper = min(taxable_income, band.max_amount)
                        taxable_in_band = max(upper - band.min_amount, Decimal("0"))

                    band_breakdowns.append(
                        TaxBandBreakdown(
                            band_name=band.name,
                            min_amount=band.min_amount,
                            max_amount=band.max_amount,
                            rate=band.rate,
                            taxable_in_band=taxable_in_band,
                            tax_amount=tax_in_band,
                        )
                    )
                    annual_tax += tax_in_band

        # Calculate monthly amounts
        monthly_tax = (annual_tax / self.MONTHS_PER_YEAR).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        monthly_pension = (pension_amount / self.MONTHS_PER_YEAR).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        monthly_employer_pension = (
            employer_pension_amount / self.MONTHS_PER_YEAR
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        monthly_nhf = (nhf_amount / self.MONTHS_PER_YEAR).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        monthly_nhis = (nhis_amount / self.MONTHS_PER_YEAR).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

        # Calculate effective rate
        effective_rate = Decimal("0")
        if annual_gross > 0:
            effective_rate = (annual_tax / annual_gross).quantize(
                Decimal("0.0001"), rounding=ROUND_HALF_UP
            )

        return PAYEBreakdown(
            annual_gross=annual_gross,
            annual_basic=annual_basic,
            annual_rent=_annual_rent,
            pension_amount=pension_amount,
            pension_rate=_pension_rate,
            employer_pension_amount=employer_pension_amount,
            employer_pension_rate=_employer_pension_rate,
            nhf_amount=nhf_amount,
            nhf_rate=_nhf_rate,
            nhis_amount=nhis_amount,
            nhis_rate=_nhis_rate,
            total_statutory=total_statutory,
            rent_relief=rent_relief,
            rent_relief_rate=self.RENT_RELIEF_RATE,
            taxable_income=taxable_income,
            band_breakdowns=band_breakdowns,
            annual_tax=annual_tax,
            monthly_tax=monthly_tax,
            monthly_pension=monthly_pension,
            monthly_employer_pension=monthly_employer_pension,
            monthly_nhf=monthly_nhf,
            monthly_nhis=monthly_nhis,
            effective_rate=effective_rate,
            employee_id=employee_id,
            tin=profile.tin if profile else None,
            tax_state=profile.tax_state if profile else None,
            is_tax_exempt=is_exempt,
        )

    def _get_tax_profile(
        self, organization_id: UUID, employee_id: UUID, as_of_date: date
    ) -> EmployeeTaxProfile | None:
        """Get the active tax profile for an employee."""
        return self.db.scalar(
            select(EmployeeTaxProfile)
            .where(
                EmployeeTaxProfile.organization_id == organization_id,
                EmployeeTaxProfile.employee_id == employee_id,
                EmployeeTaxProfile.effective_from <= as_of_date,
                (
                    (EmployeeTaxProfile.effective_to.is_(None))
                    | (EmployeeTaxProfile.effective_to >= as_of_date)
                ),
            )
            .order_by(EmployeeTaxProfile.effective_from.desc())
        )

    def _get_tax_bands(self, organization_id: UUID, as_of_date: date) -> list[TaxBand]:
        """Get active tax bands for the organization."""
        bands = list(
            self.db.scalars(
                select(TaxBand)
                .where(
                    TaxBand.organization_id == organization_id,
                    TaxBand.is_active.is_(True),
                    TaxBand.effective_from <= as_of_date,
                    (
                        (TaxBand.effective_to.is_(None))
                        | (TaxBand.effective_to >= as_of_date)
                    ),
                )
                .order_by(TaxBand.sequence)
            ).all()
        )

        # If no bands configured, use defaults (but warn)
        if not bands:
            bands = self._get_default_bands(organization_id, as_of_date)

        return bands

    def _get_default_bands(
        self, organization_id: UUID, as_of_date: date
    ) -> list[TaxBand]:
        """
        Create in-memory default bands for calculation.

        These are NOT persisted - they're used for ad-hoc calculations
        when no bands are configured. Use seed_nta_2025_bands() to persist.
        """
        return [
            self._create_band_from_def(band_def, organization_id, as_of_date)
            for band_def in self.NTA_2025_BANDS
        ]

    def seed_nta_2025_bands(
        self,
        organization_id: UUID,
        effective_from: date | None = None,
        created_by_id: UUID | None = None,
    ) -> list[TaxBand]:
        """
        Seed default NTA 2025 tax bands for an organization.

        Args:
            organization_id: Organization to seed bands for
            effective_from: When bands become effective (default: Jan 1, 2026)
            created_by_id: User creating the bands

        Returns:
            List of created TaxBand objects
        """
        org_id = coerce_uuid(organization_id)
        eff_date = effective_from or date(2026, 1, 1)

        # Check if bands already exist
        existing = self.db.scalar(
            select(TaxBand).where(
                TaxBand.organization_id == org_id,
                TaxBand.is_active.is_(True),
            )
        )

        if existing:
            return []  # Don't duplicate

        created_bands = []
        for band_def in self.NTA_2025_BANDS:
            band = self._create_band_from_def(band_def, org_id, eff_date, created_by_id)
            self.db.add(band)
            created_bands.append(band)

        self.db.flush()
        return created_bands

    def get_tax_bands(
        self, organization_id: UUID, active_only: bool = True
    ) -> list[TaxBand]:
        """
        Get all tax bands for an organization.

        Args:
            organization_id: Organization ID
            active_only: Whether to filter to active bands only

        Returns:
            List of TaxBand objects
        """
        org_id = coerce_uuid(organization_id)
        query = select(TaxBand).where(TaxBand.organization_id == org_id)

        if active_only:
            query = query.where(TaxBand.is_active.is_(True))

        return list(self.db.scalars(query.order_by(TaxBand.sequence)).all())


# Module-level convenience function
def calculate_paye(
    db: Session,
    organization_id: UUID,
    gross_monthly: Decimal,
    basic_monthly: Decimal,
    **kwargs,
) -> PAYEBreakdown:
    """
    Calculate PAYE tax - convenience function.

    Args:
        db: Database session
        organization_id: Organization for tax band lookup
        gross_monthly: Monthly gross salary
        basic_monthly: Monthly basic salary

    Returns:
        PAYEBreakdown with complete calculation details
    """
    calculator = PAYECalculator(db)
    return calculator.calculate(
        organization_id=organization_id,
        gross_monthly=gross_monthly,
        basic_monthly=basic_monthly,
        **kwargs,
    )
