"""
IFRS Settings Web Service.

Provides context and update functions for settings UI pages.
"""
import uuid
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.domain_settings import SettingDomain
from app.models.ifrs.core_org import Organization
from app.services.settings_spec import (
    DOMAIN_SETTINGS_SERVICE,
    list_specs,
    resolve_value,
    get_spec,
)
from app.schemas.settings import DomainSettingUpdate


# Common timezone list
COMMON_TIMEZONES = [
    ("UTC", "UTC"),
    ("America/New_York", "Eastern Time (US)"),
    ("America/Chicago", "Central Time (US)"),
    ("America/Denver", "Mountain Time (US)"),
    ("America/Los_Angeles", "Pacific Time (US)"),
    ("Europe/London", "London"),
    ("Europe/Paris", "Paris"),
    ("Europe/Berlin", "Berlin"),
    ("Asia/Tokyo", "Tokyo"),
    ("Asia/Shanghai", "Shanghai"),
    ("Asia/Singapore", "Singapore"),
    ("Australia/Sydney", "Sydney"),
    ("Africa/Lagos", "Lagos"),
    ("Africa/Johannesburg", "Johannesburg"),
]

DATE_FORMATS = [
    ("YYYY-MM-DD", "2025-01-10"),
    ("DD/MM/YYYY", "10/01/2025"),
    ("MM/DD/YYYY", "01/10/2025"),
    ("DD-MM-YYYY", "10-01-2025"),
    ("DD.MM.YYYY", "10.01.2025"),
]

NUMBER_FORMATS = [
    ("1,234.56", "Comma thousand, dot decimal"),
    ("1.234,56", "Dot thousand, comma decimal"),
    ("1 234.56", "Space thousand, dot decimal"),
    ("1 234,56", "Space thousand, comma decimal"),
]


class SettingsWebService:
    """Service for IFRS Settings UI."""

    # ========== Organization Profile ==========

    async def get_organization_context(
        self, db: AsyncSession, organization_id: uuid.UUID
    ) -> dict[str, Any]:
        """Get organization profile for editing."""
        result = await db.execute(
            select(Organization).where(
                Organization.organization_id == organization_id
            )
        )
        org = result.scalar_one_or_none()
        if not org:
            return {"organization": None, "error": "Organization not found"}

        return {
            "organization": org,
            "timezones": COMMON_TIMEZONES,
            "date_formats": DATE_FORMATS,
            "number_formats": NUMBER_FORMATS,
        }

    async def update_organization(
        self,
        db: AsyncSession,
        organization_id: uuid.UUID,
        data: dict[str, Any],
    ) -> tuple[bool, Optional[str]]:
        """Update organization profile."""
        result = await db.execute(
            select(Organization).where(
                Organization.organization_id == organization_id
            )
        )
        org = result.scalar_one_or_none()
        if not org:
            return False, "Organization not found"

        # Update allowed fields
        allowed_fields = [
            "legal_name",
            "trading_name",
            "registration_number",
            "tax_identification_number",
            "functional_currency_code",
            "presentation_currency_code",
            "fiscal_year_end_month",
            "fiscal_year_end_day",
            "timezone",
            "date_format",
            "number_format",
            "contact_email",
            "contact_phone",
            "address_line1",
            "address_line2",
            "city",
            "state",
            "postal_code",
            "country",
            "logo_url",
            "website_url",
        ]

        for field in allowed_fields:
            if field in data:
                value = data[field]
                # Handle empty strings as None for optional fields
                if value == "" and field not in [
                    "legal_name",
                    "functional_currency_code",
                    "presentation_currency_code",
                ]:
                    value = None
                setattr(org, field, value)

        await db.commit()
        return True, None

    # ========== Email Settings ==========

    def get_email_settings_context(self, db, organization_id: uuid.UUID) -> dict[str, Any]:
        """Get email settings for the form."""
        specs = list_specs(SettingDomain.email)
        settings = {}

        for spec in specs:
            value = resolve_value(db, SettingDomain.email, spec.key)
            settings[spec.key] = {
                "value": value if not spec.is_secret else "",
                "default": spec.default,
                "type": spec.value_type.value,
                "is_secret": spec.is_secret,
            }

        return {"settings": settings, "specs": specs}

    def update_email_settings(
        self, db, organization_id: uuid.UUID, data: dict[str, Any]
    ) -> tuple[bool, Optional[str]]:
        """Update email settings."""
        service = DOMAIN_SETTINGS_SERVICE.get(SettingDomain.email)
        if not service:
            return False, "Email settings service not found"

        for key, value in data.items():
            spec = get_spec(SettingDomain.email, key)
            if not spec:
                continue

            # Skip empty password fields (don't overwrite existing)
            if spec.is_secret and value == "":
                continue

            payload = DomainSettingUpdate(
                value_type=spec.value_type,
                value_text=str(value) if value is not None else None,
                is_secret=spec.is_secret,
            )
            service.upsert_by_key(db, key, payload)

        db.commit()
        return True, None

    # ========== Automation Settings ==========

    def get_automation_settings_context(
        self, db, organization_id: uuid.UUID
    ) -> dict[str, Any]:
        """Get automation settings for the form."""
        specs = list_specs(SettingDomain.automation)
        settings = {}

        for spec in specs:
            value = resolve_value(db, SettingDomain.automation, spec.key)
            settings[spec.key] = {
                "value": value,
                "default": spec.default,
                "type": spec.value_type.value,
                "min": spec.min_value,
                "max": spec.max_value,
                "allowed": list(spec.allowed) if spec.allowed else None,
            }

        return {"settings": settings, "specs": specs}

    def update_automation_settings(
        self, db, organization_id: uuid.UUID, data: dict[str, Any]
    ) -> tuple[bool, Optional[str]]:
        """Update automation settings."""
        service = DOMAIN_SETTINGS_SERVICE.get(SettingDomain.automation)
        if not service:
            return False, "Automation settings service not found"

        for key, value in data.items():
            spec = get_spec(SettingDomain.automation, key)
            if not spec:
                continue

            payload = DomainSettingUpdate(
                value_type=spec.value_type,
                value_text=str(value) if value is not None else None,
            )
            service.upsert_by_key(db, key, payload)

        db.commit()
        return True, None

    # ========== Feature Flags ==========

    def get_features_context(self, db, organization_id: uuid.UUID) -> dict[str, Any]:
        """Get feature flags for the form."""
        specs = list_specs(SettingDomain.features)
        features = []

        feature_descriptions = {
            "enable_multi_currency": "Support multiple currencies in transactions and reporting",
            "enable_budgeting": "Budget planning and variance analysis",
            "enable_project_accounting": "Track costs and revenue by project",
            "enable_bank_reconciliation": "Match bank statements with ledger entries",
            "enable_recurring_transactions": "Automatically generate invoices, bills, and journal entries",
            "enable_inventory": "Track inventory items and stock levels",
            "enable_fixed_assets": "Manage fixed assets and depreciation",
            "enable_leases": "IFRS 16 lease accounting and right-of-use assets",
        }

        for spec in specs:
            value = resolve_value(db, SettingDomain.features, spec.key)
            features.append({
                "key": spec.key,
                "label": spec.key.replace("enable_", "").replace("_", " ").title(),
                "description": feature_descriptions.get(spec.key, ""),
                "enabled": bool(value),
                "default": spec.default,
            })

        return {"features": features}

    def toggle_feature(
        self, db, organization_id: uuid.UUID, key: str, enabled: bool
    ) -> tuple[bool, Optional[str]]:
        """Toggle a feature flag."""
        spec = get_spec(SettingDomain.features, key)
        if not spec:
            return False, f"Unknown feature: {key}"

        service = DOMAIN_SETTINGS_SERVICE.get(SettingDomain.features)
        if not service:
            return False, "Features settings service not found"

        payload = DomainSettingUpdate(
            value_type=spec.value_type,
            value_text="true" if enabled else "false",
        )
        service.upsert_by_key(db, key, payload)
        db.commit()
        return True, None

    # ========== Reporting Settings ==========

    def get_reporting_context(self, db, organization_id: uuid.UUID) -> dict[str, Any]:
        """Get reporting settings for the form."""
        specs = list_specs(SettingDomain.reporting)
        settings = {}

        for spec in specs:
            value = resolve_value(db, SettingDomain.reporting, spec.key)
            settings[spec.key] = {
                "value": value,
                "default": spec.default,
                "type": spec.value_type.value,
                "allowed": list(spec.allowed) if spec.allowed else None,
            }

        return {"settings": settings, "specs": specs}

    def update_reporting_settings(
        self, db, organization_id: uuid.UUID, data: dict[str, Any]
    ) -> tuple[bool, Optional[str]]:
        """Update reporting settings."""
        service = DOMAIN_SETTINGS_SERVICE.get(SettingDomain.reporting)
        if not service:
            return False, "Reporting settings service not found"

        for key, value in data.items():
            spec = get_spec(SettingDomain.reporting, key)
            if not spec:
                continue

            payload = DomainSettingUpdate(
                value_type=spec.value_type,
                value_text=str(value) if value is not None else None,
            )
            service.upsert_by_key(db, key, payload)

        db.commit()
        return True, None


# Singleton instance
settings_web_service = SettingsWebService()
