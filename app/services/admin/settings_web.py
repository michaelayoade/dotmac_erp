"""
Admin Settings Web Service.

Provides context and update functions for Admin settings UI pages.
Handles org-wide settings: Organization profile, Branding, Email, Features, Payments.
"""

import logging
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.domain_settings import SettingDomain, SettingValueType
from app.models.finance.core_org import Organization
from app.schemas.settings import DomainSettingUpdate
from app.services.domain_settings import DomainSettings
from app.services.formatting_context import (
    COMMON_TIMEZONES,
)
from app.services.formatting_context import (
    DATE_FORMAT_CHOICES as DATE_FORMATS,
)
from app.services.formatting_context import (
    NUMBER_FORMAT_CHOICES as NUMBER_FORMATS,
)
from app.services.settings_cache import get_cached_setting
from app.services.settings_spec import (
    DOMAIN_SETTINGS_SERVICE,
    get_spec,
    list_specs,
    resolve_value,
)

logger = logging.getLogger(__name__)


# ── Font presets (available in the app's self-hosted stylesheet) ──
FONT_PRESETS: dict[str, list[dict[str, str]]] = {
    "display": [
        {"value": "", "label": "Default (Fraunces)"},
        {"value": "Fraunces, Georgia, serif", "label": "Fraunces"},
        {"value": "DM Sans, system-ui, sans-serif", "label": "DM Sans"},
        {"value": "Georgia, Cambria, serif", "label": "Georgia"},
        {"value": "Palatino Linotype, Book Antiqua, serif", "label": "Palatino"},
        {"value": "system-ui, -apple-system, sans-serif", "label": "System UI"},
    ],
    "body": [
        {"value": "", "label": "Default (DM Sans)"},
        {"value": "DM Sans, system-ui, sans-serif", "label": "DM Sans"},
        {"value": "Inter, system-ui, sans-serif", "label": "Inter"},
        {"value": "system-ui, -apple-system, sans-serif", "label": "System UI"},
        {"value": "Segoe UI, Roboto, sans-serif", "label": "Segoe UI"},
        {"value": "Helvetica Neue, Arial, sans-serif", "label": "Helvetica"},
    ],
    "mono": [
        {"value": "", "label": "Default (JetBrains Mono)"},
        {"value": "JetBrains Mono, monospace", "label": "JetBrains Mono"},
        {"value": "Fira Code, monospace", "label": "Fira Code"},
        {"value": "Source Code Pro, monospace", "label": "Source Code Pro"},
        {"value": "Menlo, Monaco, monospace", "label": "Menlo / Monaco"},
        {"value": "Consolas, monospace", "label": "Consolas"},
    ],
}


# Hub sections configuration
ADMIN_SETTINGS_SECTIONS = [
    {
        "title": "Organization",
        "description": "Company profile, legal details, and contact information",
        "url": "/admin/settings/organization",
        "icon": "building-office",
    },
    {
        "title": "Branding",
        "description": "Logo, colors, and visual identity",
        "url": "/admin/settings/branding",
        "icon": "swatch",
    },
    {
        "title": "Email",
        "description": "SMTP configuration and email profiles",
        "url": "/admin/settings/email",
        "icon": "envelope",
    },
    {
        "title": "Features",
        "description": "Enable or disable system features",
        "url": "/admin/settings/features",
        "icon": "flag",
    },
    {
        "title": "Service Hooks",
        "description": "Configure outbound hooks for domain events",
        "url": "/admin/settings/service-hooks",
        "icon": "link",
    },
    {
        "title": "Payments",
        "description": "Payment gateway integration",
        "url": "/admin/settings/payments",
        "icon": "credit-card",
    },
    {
        "title": "Coach / AI",
        "description": "Configure LLM backends (DeepSeek, Llama) for the AI Coach module.",
        "url": "/admin/settings/coach",
        "icon": "lightning-bolt",
    },
    {
        "title": "Advanced",
        "description": "Raw system settings (for administrators)",
        "url": "/admin/settings/advanced",
        "icon": "cog",
    },
]


class AdminSettingsWebService:
    """Service for Admin Settings UI."""

    # ========== Hub ==========

    def get_hub_context(self, organization_id: uuid.UUID) -> dict[str, Any]:
        """Get context for settings hub page."""
        return {
            "settings_sections": ADMIN_SETTINGS_SECTIONS,
        }

    # ========== Organization Profile ==========

    def get_organization_context(
        self, db: Session, organization_id: uuid.UUID
    ) -> dict[str, Any]:
        """Get organization profile for editing."""
        org = db.get(Organization, organization_id)
        if not org:
            return {"organization": None, "error": "Organization not found"}

        return {
            "organization": org,
            "timezones": COMMON_TIMEZONES,
            "date_formats": DATE_FORMATS,
            "number_formats": NUMBER_FORMATS,
        }

    def update_organization(
        self,
        db: Session,
        organization_id: uuid.UUID,
        data: dict[str, Any],
    ) -> tuple[bool, str | None]:
        """Update organization profile."""
        org = db.get(Organization, organization_id)
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

        db.commit()
        return True, None

    # ========== Branding ==========

    def get_branding_context(
        self, db: Session, organization_id: uuid.UUID
    ) -> dict[str, Any]:
        """Get branding settings for the form."""
        org = db.get(Organization, organization_id)
        if not org:
            return {"organization": None, "error": "Organization not found"}

        # Use get_or_create so branding is always non-None
        branding = None
        try:
            from app.services.finance.branding import BrandingService

            branding = BrandingService(db).get_or_create(organization_id)
        except Exception:
            # Fall back to raw query if BrandingService unavailable
            try:
                from app.models.finance.core_org.organization_branding import (
                    OrganizationBranding,
                )

                branding = db.execute(
                    select(OrganizationBranding).where(
                        OrganizationBranding.organization_id == organization_id
                    )
                ).scalar_one_or_none()
            except Exception:
                logger.exception("Ignored exception")

        email_logo_url = get_cached_setting(
            db, SettingDomain.email, "email_logo_url", ""
        )
        report_logo_url = get_cached_setting(
            db, SettingDomain.reporting, "report_logo_url", ""
        )

        # Import enums for UI controls
        from app.models.finance.core_org.organization_branding import (
            BorderRadiusStyle,
            ButtonStyle,
            SidebarStyle,
        )

        # Build Alpine.js-friendly config dict with safe defaults
        branding_config = {
            "display_name": (branding.display_name or "") if branding else "",
            "tagline": (branding.tagline or "") if branding else "",
            "brand_mark": (branding.brand_mark or "") if branding else "",
            "primary_color": (branding.primary_color or "#0D9488")
            if branding
            else "#0D9488",
            "accent_color": (branding.accent_color or "#D97706")
            if branding
            else "#D97706",
            "font_family_display": (branding.font_family_display or "")
            if branding
            else "",
            "font_family_body": (branding.font_family_body or "") if branding else "",
            "font_family_mono": (branding.font_family_mono or "") if branding else "",
            "border_radius": (
                branding.border_radius.value
                if branding and branding.border_radius
                else "rounded"
            ),
            "button_style": (
                branding.button_style.value
                if branding and branding.button_style
                else "gradient"
            ),
            "sidebar_style": (
                branding.sidebar_style.value
                if branding and branding.sidebar_style
                else "dark"
            ),
            "custom_css": (branding.custom_css or "") if branding else "",
        }

        return {
            "organization": org,
            "branding": branding,
            "branding_config": branding_config,
            "email_logo_url": email_logo_url or "",
            "report_logo_url": report_logo_url or "",
            "font_presets": FONT_PRESETS,
            "border_radius_choices": [
                {"value": e.value, "label": e.name.replace("_", " ").title()}
                for e in BorderRadiusStyle
            ],
            "button_style_choices": [
                {"value": e.value, "label": e.name.replace("_", " ").title()}
                for e in ButtonStyle
            ],
            "sidebar_style_choices": [
                {"value": e.value, "label": e.name.replace("_", " ").title()}
                for e in SidebarStyle
            ],
        }

    def update_branding(
        self,
        db: Session,
        organization_id: uuid.UUID,
        data: dict[str, Any],
    ) -> tuple[bool, str | None]:
        """Update branding settings."""
        org = db.get(Organization, organization_id)
        if not org:
            return False, "Organization not found"

        # Update logo_url on organization if provided
        if "logo_url" in data:
            org.logo_url = data["logo_url"] if data["logo_url"] else None

        # Try to update OrganizationBranding if model exists
        try:
            from app.models.finance.core_org.organization_branding import (
                OrganizationBranding,
            )

            branding = db.execute(
                select(OrganizationBranding).where(
                    OrganizationBranding.organization_id == organization_id
                )
            ).scalar_one_or_none()

            branding_fields = [
                "display_name",
                "tagline",
                "brand_mark",
                "logo_url",
                "logo_dark_url",
                "favicon_url",
                "primary_color",
                "primary_light",
                "primary_dark",
                "accent_color",
                "accent_light",
                "accent_dark",
                "success_color",
                "warning_color",
                "danger_color",
                "font_family_display",
                "font_family_body",
                "font_family_mono",
                "border_radius",
                "button_style",
                "sidebar_style",
                "custom_css",
            ]

            if branding:
                for field in branding_fields:
                    if field in data:
                        setattr(branding, field, data[field] if data[field] else None)
            else:
                # Create new branding record if any branding data provided
                has_branding_data = any(data.get(f) for f in branding_fields)
                if has_branding_data:
                    branding = OrganizationBranding(
                        organization_id=organization_id,
                        **{f: data.get(f) for f in branding_fields if f in data},
                    )
                    db.add(branding)
        except Exception as e:
            logger.debug("OrganizationBranding model unavailable: %s", e)

        email_logo_url = (data.get("email_logo_url") or "").strip()
        report_logo_url = (data.get("report_logo_url") or "").strip()

        email_settings = DomainSettings(SettingDomain.email)
        reporting_settings = DomainSettings(SettingDomain.reporting)

        email_settings.upsert_by_key(
            db,
            "email_logo_url",
            DomainSettingUpdate(
                value_type=SettingValueType.string,
                value_text=email_logo_url or None,
                is_active=bool(email_logo_url),
            ),
        )
        reporting_settings.upsert_by_key(
            db,
            "report_logo_url",
            DomainSettingUpdate(
                value_type=SettingValueType.string,
                value_text=report_logo_url or None,
                is_active=bool(report_logo_url),
            ),
        )

        db.commit()
        return True, None

    # ========== Email Settings ==========

    def get_email_context(
        self, db: Session, organization_id: uuid.UUID
    ) -> dict[str, Any]:
        """Get email settings for the form."""
        # Delegate to finance settings service for email context
        from app.services.finance.settings_web import settings_web_service

        return settings_web_service.get_email_settings_context(db, organization_id)

    def update_email(
        self,
        db: Session,
        organization_id: uuid.UUID,
        data: dict[str, Any],
    ) -> tuple[bool, str | None]:
        """Update email settings."""
        # Delegate to finance settings service for email updates
        from app.services.finance.settings_web import settings_web_service

        return settings_web_service.update_email_settings(db, organization_id, data)

    # ========== Feature Flags ==========

    def get_features_context(
        self, db: Session, organization_id: uuid.UUID
    ) -> dict[str, Any]:
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
            features.append(
                {
                    "key": spec.key,
                    "label": spec.key.replace("enable_", "").replace("_", " ").title(),
                    "description": feature_descriptions.get(spec.key, ""),
                    "enabled": bool(value),
                    "default": spec.default,
                }
            )

        return {"features": features}

    def toggle_feature(
        self,
        db: Session,
        organization_id: uuid.UUID,
        key: str,
        enabled: bool,
    ) -> tuple[bool, str | None]:
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

    # ========== Payments Settings ==========

    def get_payments_hub_context(
        self, db: Session, organization_id: uuid.UUID
    ) -> dict[str, Any]:
        """Get payments hub context with available providers."""
        # Check which payment providers are configured
        paystack_enabled = resolve_value(db, SettingDomain.payments, "paystack_enabled")

        providers = [
            {
                "name": "Paystack",
                "slug": "paystack",
                "description": "Accept payments via Paystack (cards, bank transfers)",
                "configured": bool(paystack_enabled),
                "url": "/admin/settings/payments/paystack",
                "icon": "credit-card",
            },
        ]

        return {"providers": providers}

    def get_paystack_context(
        self, db: Session, organization_id: uuid.UUID
    ) -> dict[str, Any]:
        """Get Paystack settings for the form."""
        # Delegate to finance settings service
        from app.services.finance.settings_web import settings_web_service

        return settings_web_service.get_payments_settings_context(db, organization_id)

    def update_paystack(
        self,
        db: Session,
        organization_id: uuid.UUID,
        data: dict[str, Any],
    ) -> tuple[bool, str | None]:
        """Update Paystack settings."""
        # Delegate to finance settings service
        from app.services.finance.settings_web import settings_web_service

        return settings_web_service.update_payments_settings(db, organization_id, data)

    # ========== Coach / AI Settings ==========

    def get_coach_context(
        self, db: Session, organization_id: uuid.UUID
    ) -> dict[str, Any]:
        """Get Coach / AI settings for the form."""
        from app.services.finance.settings_web import settings_web_service

        return settings_web_service.get_coach_settings_context(db, organization_id)

    def update_coach(
        self,
        db: Session,
        organization_id: uuid.UUID,
        data: dict[str, Any],
    ) -> tuple[bool, str | None]:
        """Update Coach / AI settings."""
        from app.services.finance.settings_web import settings_web_service

        return settings_web_service.update_coach_settings(db, organization_id, data)


# Singleton instance
admin_settings_web_service = AdminSettingsWebService()
