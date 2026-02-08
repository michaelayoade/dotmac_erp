"""
TaxBand Service - Management and validation of tax bands.

Provides validation to ensure tax bands are:
- Properly ordered (min < max)
- Non-overlapping
- Contiguous (no gaps)
- Have valid rates (0-100%)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.people.payroll.tax_band import TaxBand

logger = logging.getLogger(__name__)


@dataclass
class TaxBandValidationError:
    """Details about a tax band validation error."""

    band_id: UUID | None
    band_name: str
    error_type: str
    message: str


@dataclass
class TaxBandValidationResult:
    """Result of tax band validation."""

    is_valid: bool
    errors: list[TaxBandValidationError]
    warnings: list[str]

    @property
    def error_messages(self) -> list[str]:
        """Get list of error messages."""
        return [e.message for e in self.errors]


class TaxBandService:
    """
    Service for managing tax bands with validation.

    Ensures tax band configurations are valid for PAYE calculation.
    """

    # Maximum allowed tax rate (100%)
    MAX_RATE = Decimal("1.0")

    def __init__(self, db: Session):
        self.db = db

    def get_active_bands(
        self,
        organization_id: UUID,
        as_of_date: date | None = None,
    ) -> list[TaxBand]:
        """
        Get active tax bands for an organization, ordered by sequence.

        Args:
            organization_id: Organization scope
            as_of_date: Date to check effectiveness (default: today)

        Returns:
            List of TaxBand objects ordered by sequence
        """
        check_date = as_of_date or date.today()

        stmt = (
            select(TaxBand)
            .where(
                TaxBand.organization_id == organization_id,
                TaxBand.is_active == True,
                TaxBand.effective_from <= check_date,
            )
            .where(
                (TaxBand.effective_to.is_(None)) | (TaxBand.effective_to >= check_date)
            )
            .order_by(TaxBand.sequence, TaxBand.min_amount)
        )

        return list(self.db.scalars(stmt).all())

    def validate_band(self, band: TaxBand) -> list[TaxBandValidationError]:
        """
        Validate a single tax band.

        Checks:
        - min_amount >= 0
        - max_amount > min_amount (if set)
        - rate is between 0 and 100%

        Args:
            band: TaxBand to validate

        Returns:
            List of validation errors (empty if valid)
        """
        errors = []

        # Check min_amount is non-negative
        if band.min_amount < 0:
            errors.append(
                TaxBandValidationError(
                    band_id=band.tax_band_id,
                    band_name=band.name,
                    error_type="NEGATIVE_MIN",
                    message=f"Band '{band.name}': minimum amount cannot be negative",
                )
            )

        # Check max_amount > min_amount
        if band.max_amount is not None and band.max_amount <= band.min_amount:
            errors.append(
                TaxBandValidationError(
                    band_id=band.tax_band_id,
                    band_name=band.name,
                    error_type="INVALID_RANGE",
                    message=f"Band '{band.name}': maximum ({band.max_amount:,.0f}) must be greater than minimum ({band.min_amount:,.0f})",
                )
            )

        # Check rate is valid (0-100%)
        if band.rate < 0:
            errors.append(
                TaxBandValidationError(
                    band_id=band.tax_band_id,
                    band_name=band.name,
                    error_type="NEGATIVE_RATE",
                    message=f"Band '{band.name}': tax rate cannot be negative",
                )
            )
        elif band.rate > self.MAX_RATE:
            errors.append(
                TaxBandValidationError(
                    band_id=band.tax_band_id,
                    band_name=band.name,
                    error_type="EXCESSIVE_RATE",
                    message=f"Band '{band.name}': tax rate ({band.rate * 100:.1f}%) exceeds 100%",
                )
            )

        return errors

    def validate_band_set(
        self,
        organization_id: UUID,
        as_of_date: date | None = None,
    ) -> TaxBandValidationResult:
        """
        Validate the complete set of tax bands for an organization.

        Checks:
        - Individual band validity
        - No overlapping ranges
        - Contiguous coverage (no gaps)
        - At least one band exists
        - First band starts at 0
        - Last band is unbounded (no max)

        Args:
            organization_id: Organization to validate
            as_of_date: Date to check (default: today)

        Returns:
            TaxBandValidationResult with errors and warnings
        """
        bands = self.get_active_bands(organization_id, as_of_date)
        errors: list[TaxBandValidationError] = []
        warnings: list[str] = []

        # Check at least one band exists
        if not bands:
            errors.append(
                TaxBandValidationError(
                    band_id=None,
                    band_name="(none)",
                    error_type="NO_BANDS",
                    message="No active tax bands configured",
                )
            )
            return TaxBandValidationResult(
                is_valid=False, errors=errors, warnings=warnings
            )

        # Validate each individual band
        for band in bands:
            errors.extend(self.validate_band(band))

        # Check first band starts at 0
        if bands[0].min_amount != 0:
            warnings.append(
                f"First tax band starts at {bands[0].min_amount:,.0f}, not 0. "
                f"Income below this amount will be taxed at 0%."
            )

        # Check last band is unbounded
        if bands[-1].max_amount is not None:
            warnings.append(
                f"Last tax band has a maximum of {bands[-1].max_amount:,.0f}. "
                f"Income above this may not be taxed correctly."
            )

        # Check for overlaps and gaps
        for i in range(len(bands) - 1):
            current = bands[i]
            next_band = bands[i + 1]

            if current.max_amount is None:
                # Current band is unbounded but not last - error
                errors.append(
                    TaxBandValidationError(
                        band_id=current.tax_band_id,
                        band_name=current.name,
                        error_type="UNBOUNDED_NOT_LAST",
                        message=f"Band '{current.name}' has no maximum but is not the last band",
                    )
                )
                continue

            # Check for overlap
            if next_band.min_amount < current.max_amount:
                errors.append(
                    TaxBandValidationError(
                        band_id=current.tax_band_id,
                        band_name=current.name,
                        error_type="OVERLAP",
                        message=(
                            f"Bands overlap: '{current.name}' ends at {current.max_amount:,.0f} "
                            f"but '{next_band.name}' starts at {next_band.min_amount:,.0f}"
                        ),
                    )
                )

            # Check for gap
            if next_band.min_amount > current.max_amount:
                gap_start = current.max_amount
                gap_end = next_band.min_amount
                warnings.append(
                    f"Gap in tax bands: {gap_start:,.0f} to {gap_end:,.0f} "
                    f"(between '{current.name}' and '{next_band.name}')"
                )

        # Check for duplicate sequences
        sequences = [b.sequence for b in bands]
        if len(sequences) != len(set(sequences)):
            warnings.append("Some tax bands have duplicate sequence numbers")

        return TaxBandValidationResult(
            is_valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
        )

    def validate_new_band(
        self,
        organization_id: UUID,
        min_amount: Decimal,
        max_amount: Decimal | None,
        rate: Decimal,
        name: str,
        exclude_band_id: UUID | None = None,
    ) -> TaxBandValidationResult:
        """
        Validate a new or updated tax band against existing bands.

        Args:
            organization_id: Organization scope
            min_amount: New band's minimum
            max_amount: New band's maximum (None for unbounded)
            rate: New band's rate
            name: New band's name
            exclude_band_id: Band ID to exclude (for updates)

        Returns:
            TaxBandValidationResult
        """
        errors: list[TaxBandValidationError] = []
        warnings: list[str] = []

        # Validate individual band properties
        if min_amount < 0:
            errors.append(
                TaxBandValidationError(
                    band_id=None,
                    band_name=name,
                    error_type="NEGATIVE_MIN",
                    message="Minimum amount cannot be negative",
                )
            )

        if max_amount is not None and max_amount <= min_amount:
            errors.append(
                TaxBandValidationError(
                    band_id=None,
                    band_name=name,
                    error_type="INVALID_RANGE",
                    message=f"Maximum ({max_amount:,.0f}) must be greater than minimum ({min_amount:,.0f})",
                )
            )

        if rate < 0 or rate > self.MAX_RATE:
            errors.append(
                TaxBandValidationError(
                    band_id=None,
                    band_name=name,
                    error_type="INVALID_RATE",
                    message=f"Rate must be between 0% and 100%, got {rate * 100:.1f}%",
                )
            )

        # Check for overlap with existing bands
        existing_bands = self.get_active_bands(organization_id)

        for band in existing_bands:
            if exclude_band_id and band.tax_band_id == exclude_band_id:
                continue

            # Check if ranges overlap
            band_max = (
                band.max_amount
                if band.max_amount is not None
                else Decimal("999999999999")
            )
            new_max = max_amount if max_amount is not None else Decimal("999999999999")

            if min_amount < band_max and new_max > band.min_amount:
                errors.append(
                    TaxBandValidationError(
                        band_id=band.tax_band_id,
                        band_name=name,
                        error_type="OVERLAP",
                        message=f"Range overlaps with existing band '{band.name}' ({band.range_display})",
                    )
                )

        return TaxBandValidationResult(
            is_valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
        )
