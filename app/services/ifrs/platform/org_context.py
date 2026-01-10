"""
OrgContextService - Organization context helpers.

Provides utility functions for retrieving organization settings
like functional currency, presentation currency, and fiscal year configuration.
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.ifrs.core_org.organization import Organization
from app.services.common import coerce_uuid


class OrgContextService:
    """
    Service for retrieving organization context information.

    Provides cached lookups for frequently accessed organization settings
    to avoid repeated database queries within the same request.
    """

    @staticmethod
    def get_functional_currency(
        db: Session,
        organization_id: UUID | str,
    ) -> str:
        """
        Get the functional currency code for an organization.

        Args:
            db: Database session
            organization_id: Organization ID

        Returns:
            ISO 4217 currency code (e.g., "USD", "EUR", "GBP")

        Raises:
            ValueError: If organization not found
        """
        org_id = coerce_uuid(organization_id)
        org = db.get(Organization, org_id)

        if not org:
            raise ValueError(f"Organization {org_id} not found")

        return org.functional_currency_code

    @staticmethod
    def get_presentation_currency(
        db: Session,
        organization_id: UUID | str,
    ) -> str:
        """
        Get the presentation currency code for an organization.

        Args:
            db: Database session
            organization_id: Organization ID

        Returns:
            ISO 4217 currency code (e.g., "USD", "EUR", "GBP")

        Raises:
            ValueError: If organization not found
        """
        org_id = coerce_uuid(organization_id)
        org = db.get(Organization, org_id)

        if not org:
            raise ValueError(f"Organization {org_id} not found")

        return org.presentation_currency_code

    @staticmethod
    def get_currency_settings(
        db: Session,
        organization_id: UUID | str,
    ) -> dict[str, str]:
        """
        Get all currency settings for an organization.

        Args:
            db: Database session
            organization_id: Organization ID

        Returns:
            Dictionary with 'functional' and 'presentation' currency codes

        Raises:
            ValueError: If organization not found
        """
        org_id = coerce_uuid(organization_id)
        org = db.get(Organization, org_id)

        if not org:
            raise ValueError(f"Organization {org_id} not found")

        return {
            "functional": org.functional_currency_code,
            "presentation": org.presentation_currency_code,
        }

    @staticmethod
    def get_fiscal_year_end(
        db: Session,
        organization_id: UUID | str,
    ) -> tuple[int, int]:
        """
        Get the fiscal year end month and day for an organization.

        Args:
            db: Database session
            organization_id: Organization ID

        Returns:
            Tuple of (month, day) for fiscal year end

        Raises:
            ValueError: If organization not found
        """
        org_id = coerce_uuid(organization_id)
        org = db.get(Organization, org_id)

        if not org:
            raise ValueError(f"Organization {org_id} not found")

        return (org.fiscal_year_end_month, org.fiscal_year_end_day)

    @staticmethod
    def get_organization(
        db: Session,
        organization_id: UUID | str,
    ) -> Optional[Organization]:
        """
        Get organization entity.

        Args:
            db: Database session
            organization_id: Organization ID

        Returns:
            Organization or None if not found
        """
        org_id = coerce_uuid(organization_id)
        return db.get(Organization, org_id)


# Module-level singleton instance
org_context_service = OrgContextService()
