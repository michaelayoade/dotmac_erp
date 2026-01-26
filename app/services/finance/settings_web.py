"""
IFRS Settings Web Service.

Provides context and update functions for settings UI pages.
"""
import uuid
import logging
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.domain_settings import SettingDomain
from app.models.finance.core_config import SequenceType, ResetFrequency
from app.models.finance.core_org import Organization
from app.services.settings_spec import (
    DOMAIN_SETTINGS_SERVICE,
    list_specs,
    resolve_value,
    get_spec,
)
from app.schemas.settings import DomainSettingUpdate

logger = logging.getLogger(__name__)


# Friendly labels for numbering sequence types
SEQUENCE_TYPE_LABELS = {
    SequenceType.INVOICE: "Customer Invoice",
    SequenceType.CREDIT_NOTE: "Credit Note",
    SequenceType.PAYMENT: "Payment",
    SequenceType.RECEIPT: "Receipt",
    SequenceType.JOURNAL: "Journal Entry",
    SequenceType.PURCHASE_ORDER: "Purchase Order",
    SequenceType.SUPPLIER_INVOICE: "Supplier Invoice",
    SequenceType.ITEM: "Inventory Item",
    SequenceType.ASSET: "Fixed Asset",
    SequenceType.LEASE: "Lease",
    SequenceType.GOODS_RECEIPT: "Goods Receipt",
    SequenceType.QUOTE: "Quote",
    SequenceType.SALES_ORDER: "Sales Order",
    SequenceType.SHIPMENT: "Shipment",
    SequenceType.EXPENSE: "Expense",
    SequenceType.SUPPORT_TICKET: "Support Ticket",
    SequenceType.PROJECT: "Project",
    SequenceType.TASK: "Task",
}

RESET_FREQUENCY_LABELS = {
    ResetFrequency.NEVER: "Never",
    ResetFrequency.YEARLY: "Yearly",
    ResetFrequency.MONTHLY: "Monthly",
}


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

    # ========== Numbering Sequences ==========

    async def get_numbering_list_context(
        self, db: AsyncSession, organization_id: uuid.UUID
    ) -> dict[str, Any]:
        """
        Get context for numbering sequences list page.

        Ensures all sequence types exist (initializes missing ones) and
        returns formatted sequence data with labels and previews.
        """
        from app.services.finance.common import NumberingService

        numbering_service = NumberingService(db)

        # Get existing sequences
        sequences = await numbering_service.get_all_sequences(organization_id)

        # Ensure all sequence types exist (covers new types added after initial setup)
        existing_types = {seq.sequence_type for seq in sequences}
        if len(existing_types) < len(SequenceType):
            for seq_type in SequenceType:
                if seq_type not in existing_types:
                    await numbering_service.get_or_create_sequence(organization_id, seq_type)
            await db.commit()
            sequences = await numbering_service.get_all_sequences(organization_id)

        # Build sequence data with labels and previews
        sequence_data = []
        for seq in sequences:
            sequence_data.append({
                "sequence": seq,
                "label": SEQUENCE_TYPE_LABELS.get(seq.sequence_type, seq.sequence_type.value),
                "preview": numbering_service.preview_format(seq),
                "reset_label": RESET_FREQUENCY_LABELS.get(seq.reset_frequency, seq.reset_frequency.value),
            })

        return {
            "sequences": sequence_data,
            "sequence_types": SequenceType,
            "reset_frequencies": ResetFrequency,
            "reset_labels": RESET_FREQUENCY_LABELS,
        }

    async def get_numbering_edit_context(
        self,
        db: AsyncSession,
        organization_id: uuid.UUID,
        sequence_id: uuid.UUID,
    ) -> tuple[dict[str, Any], Optional[str]]:
        """
        Get context for numbering sequence edit page.

        Returns (context, error). If sequence not found, returns (empty, error message).
        """
        from app.services.finance.common import NumberingService

        numbering_service = NumberingService(db)
        sequence = await numbering_service.get_sequence_by_id(sequence_id)

        if not sequence or sequence.organization_id != organization_id:
            return {}, "Sequence not found"

        return {
            "sequence": sequence,
            "label": SEQUENCE_TYPE_LABELS.get(sequence.sequence_type, sequence.sequence_type.value),
            "preview": numbering_service.preview_format(sequence),
            "reset_frequencies": ResetFrequency,
            "reset_labels": RESET_FREQUENCY_LABELS,
        }, None

    async def update_numbering_sequence(
        self,
        db: AsyncSession,
        sequence_id: uuid.UUID,
        prefix: str,
        suffix: str,
        separator: str,
        min_digits: int,
        include_year: bool,
        include_month: bool,
        year_format: int,
        reset_frequency: str,
    ) -> tuple[bool, Optional[str]]:
        """
        Update a numbering sequence configuration.

        Returns (success, error_message).
        """
        from app.services.finance.common import NumberingService

        numbering_service = NumberingService(db)

        try:
            await numbering_service.update_sequence(
                sequence_id=sequence_id,
                prefix=prefix,
                suffix=suffix,
                separator=separator,
                min_digits=min_digits,
                include_year=include_year,
                include_month=include_month,
                year_format=year_format,
                reset_frequency=ResetFrequency(reset_frequency),
            )
            await db.commit()
            return True, None
        except Exception as e:
            return False, str(e)

    async def reset_numbering_sequence(
        self,
        db: AsyncSession,
        sequence_id: uuid.UUID,
        new_value: int,
    ) -> tuple[bool, Optional[str]]:
        """
        Reset a sequence counter to a specific value.

        Returns (success, error_message).
        """
        from app.services.finance.common import NumberingService

        numbering_service = NumberingService(db)

        try:
            await numbering_service.reset_sequence_counter(
                sequence_id=sequence_id,
                new_value=new_value,
            )
            await db.commit()
            return True, None
        except Exception as e:
            return False, str(e)

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

        # Ensure unchecked checkboxes are persisted as false
        data.setdefault("smtp_use_tls", "false")
        data.setdefault("smtp_use_ssl", "false")

        # Validate SMTP settings before persisting changes
        from app.services.email import _get_smtp_config, validate_smtp_config
        from app.services.settings_spec import coerce_value


        candidate_config = _get_smtp_config(db)
        has_smtp_change = False
        smtp_field_map = {
            "smtp_host": "host",
            "smtp_port": "port",
            "smtp_username": "username",
            "smtp_password": "password",
            "smtp_use_tls": "use_tls",
            "smtp_use_ssl": "use_ssl",
            "smtp_from_email": "from_email",
            "smtp_from_name": "from_name",
            "email_reply_to": "reply_to",
        }
        smtp_validation_keys = {
            "smtp_host",
            "smtp_port",
            "smtp_username",
            "smtp_password",
            "smtp_use_tls",
            "smtp_use_ssl",
        }
        smtp_fields_seen = False

        pending_updates: list[tuple[str, DomainSettingUpdate]] = []

        for key, value in data.items():
            spec = get_spec(SettingDomain.email, key)
            if not spec:
                continue

            # Skip empty password fields (don't overwrite existing)
            if spec.is_secret and value == "":
                continue

            coerced, error = coerce_value(spec, value)
            if error:
                return False, error

            if key in smtp_field_map:
                candidate_key = smtp_field_map[key]
                if key in smtp_validation_keys:
                    smtp_fields_seen = True
                if key in smtp_validation_keys and candidate_config.get(candidate_key) != coerced:
                    has_smtp_change = True
                candidate_config[candidate_key] = coerced

            payload = DomainSettingUpdate(
                value_type=spec.value_type,
                value_text=str(coerced) if coerced is not None else None,
                is_secret=spec.is_secret,
            )
            pending_updates.append((key, payload))

        if smtp_fields_seen or has_smtp_change:
            ok, error = validate_smtp_config(candidate_config)
            if not ok:
                return False, error

        for key, payload in pending_updates:
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

    # ========== Payments Settings ==========

    def get_payments_settings_context(
        self, db, organization_id: uuid.UUID
    ) -> dict[str, Any]:
        """Get payments settings for the form."""
        from sqlalchemy import select
        from app.models.finance.banking.bank_account import BankAccount, BankAccountStatus
        from app.models.finance.gl.account import Account
        from app.models.finance.gl.account_category import AccountCategory, IFRSCategory

        specs = list_specs(SettingDomain.payments)
        settings = {}

        for spec in specs:
            value = resolve_value(db, SettingDomain.payments, spec.key)
            settings[spec.key] = {
                "value": value if not spec.is_secret else "",
                "default": spec.default,
                "type": spec.value_type.value,
                "is_secret": spec.is_secret,
                "has_value": value is not None and value != "",
            }

        # Get active bank accounts from Banking module (for reconciliation features)
        bank_accounts = db.execute(
            select(BankAccount)
            .where(
                BankAccount.organization_id == organization_id,
                BankAccount.status == BankAccountStatus.active,
            )
            .order_by(BankAccount.account_name)
        ).scalars().all()

        # Get GL bank/cash accounts from Chart of Accounts
        # Try multiple approaches: is_cash_equivalent, subledger_type, or account code pattern (12xx)
        from sqlalchemy import or_
        gl_bank_accounts = db.execute(
            select(Account)
            .where(
                Account.organization_id == organization_id,
                Account.is_active.is_(True),
                or_(
                    Account.is_cash_equivalent.is_(True),
                    Account.subledger_type == "BANK",
                    Account.account_code.like("12%"),
                ),
            )
            .order_by(Account.account_code)
        ).scalars().all()

        # Get expense accounts for fee account dropdown
        expense_accounts = db.execute(
            select(Account)
            .join(AccountCategory, Account.category_id == AccountCategory.category_id)
            .where(
                Account.organization_id == organization_id,
                AccountCategory.ifrs_category == IFRSCategory.EXPENSES,
                Account.is_active.is_(True),
            )
            .order_by(Account.account_code)
        ).scalars().all()

        return {
            "settings": settings,
            "specs": specs,
            "bank_accounts": bank_accounts,
            "gl_bank_accounts": gl_bank_accounts,
            "expense_accounts": expense_accounts,
        }

    def update_payments_settings(
        self, db, organization_id: uuid.UUID, data: dict[str, Any]
    ) -> tuple[bool, Optional[str]]:
        """Update payments settings."""
        from app.services.settings_spec import coerce_value

        service = DOMAIN_SETTINGS_SERVICE.get(SettingDomain.payments)
        if not service:
            return False, "Payments settings service not found"

        # Validate UUID fields (bank accounts and GL accounts)
        uuid_fields = [
            "paystack_collection_bank_account_id",
            "paystack_transfer_bank_account_id",
            "paystack_transfer_fee_account_id",
        ]
        for field in uuid_fields:
            value = data.get(field)
            if value and value.strip():
                try:
                    uuid.UUID(value.strip())
                except ValueError:
                    return False, f"{field}: Must be a valid UUID"

        # Ensure unchecked checkboxes are persisted as false
        data.setdefault("paystack_enabled", "false")
        data.setdefault("paystack_transfers_enabled", "false")

        for key, value in data.items():
            spec = get_spec(SettingDomain.payments, key)
            if not spec:
                continue

            # Skip empty password fields (don't overwrite existing)
            if spec.is_secret and value == "":
                continue

            coerced, error = coerce_value(spec, value)
            if error:
                return False, f"{key}: {error}"

            payload = DomainSettingUpdate(
                value_type=spec.value_type,
                value_text=str(coerced) if coerced is not None else None,
                is_secret=spec.is_secret,
            )
            service.upsert_by_key(db, key, payload)

        db.commit()
        return True, None


# Singleton instance
settings_web_service = SettingsWebService()
