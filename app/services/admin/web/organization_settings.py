from __future__ import annotations

import json
from datetime import date as date_type

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.domain_settings import DomainSetting, SettingDomain, SettingValueType
from app.models.finance.core_org.organization import Organization
from app.models.person import Person
from app.services.common import coerce_uuid
from app.services.formatters import format_datetime as _format_datetime

from .common import (
    DEFAULT_PAGE_SIZE,
    _ORG_SLUG_PATTERN,
    _build_pagination,
    _parse_domain,
    _parse_status_filter,
    _setting_value_display,
)


class AdminOrganizationSettingsMixin:
    @staticmethod
    def organizations_context(db: Session, search: str | None, status: str | None, page: int, limit: int = DEFAULT_PAGE_SIZE) -> dict:
        offset = (page - 1) * limit
        conditions = []
        search_value = search.strip() if search else ""
        if search_value:
            search_pattern = f"%{search_value}%"
            conditions.append(
                or_(
                    Organization.organization_code.ilike(search_pattern),
                    Organization.legal_name.ilike(search_pattern),
                    Organization.trading_name.ilike(search_pattern),
                )
            )
        active_count = db.scalar(select(func.count(Organization.organization_id)).where(*conditions, Organization.is_active.is_(True))) or 0
        inactive_count = db.scalar(select(func.count(Organization.organization_id)).where(*conditions, Organization.is_active.is_(False))) or 0
        org_conditions = list(conditions)
        status_flag = _parse_status_filter(status)
        if status_flag is not None:
            org_conditions.append(Organization.is_active == status_flag)
        total_count = db.scalar(select(func.count(Organization.organization_id)).where(*org_conditions)) or 0
        organizations = list(
            db.scalars(
                select(Organization)
                .where(*org_conditions)
                .order_by(Organization.created_at.desc())
                .limit(limit)
                .offset(offset)
            ).all()
        )
        org_ids = [org.organization_id for org in organizations]
        user_counts: dict = {}
        active_user_counts: dict = {}
        if org_ids:
            user_counts = {
                org_id: count
                for org_id, count in db.execute(
                    select(Person.organization_id, func.count(Person.id))
                    .where(Person.organization_id.in_(org_ids))
                    .group_by(Person.organization_id)
                ).all()
            }
            active_user_counts = {
                org_id: count
                for org_id, count in db.execute(
                    select(Person.organization_id, func.count(Person.id))
                    .where(Person.organization_id.in_(org_ids), Person.is_active.is_(True))
                    .group_by(Person.organization_id)
                ).all()
            }
        return {
            "organizations": [
                {
                    "organization_id": org.organization_id,
                    "organization_code": org.organization_code,
                    "legal_name": org.legal_name,
                    "trading_name": org.trading_name,
                    "country_code": org.jurisdiction_country_code,
                    "functional_currency": org.functional_currency_code,
                    "presentation_currency": org.presentation_currency_code,
                    "is_active": org.is_active,
                    "total_users": user_counts.get(org.organization_id, 0),
                    "active_users": active_user_counts.get(org.organization_id, 0),
                    "created_at": _format_datetime(org.created_at),
                }
                for org in organizations
            ],
            "pagination": _build_pagination(page, max(1, (total_count + limit - 1) // limit), total_count, limit),
            "search": search_value,
            "status_filter": status or "",
            "stats": {"active": active_count, "inactive": inactive_count, "total": active_count + inactive_count},
        }

    @staticmethod
    def organization_form_context(db: Session, organization_id: str | None = None, default_currency_org_id: str | None = None) -> dict:
        from app.models.finance.core_org.organization import ConsolidationMethod
        from app.models.finance.gl.account import Account
        from app.models.finance.gl.account_category import AccountCategory, IFRSCategory

        parent_orgs = list(
            db.scalars(select(Organization).where(Organization.is_active.is_(True)).order_by(Organization.legal_name)).all()
        )
        parent_org_list = [
            {"id": str(org.organization_id), "code": org.organization_code, "name": org.legal_name or org.trading_name or org.organization_code}
            for org in parent_orgs
        ]
        consolidation_methods = [{"value": cm.value, "label": cm.value.replace("_", " ").title()} for cm in ConsolidationMethod]
        organization_data = None
        default_functional_currency_code = None
        default_presentation_currency_code = None
        default_org_id = organization_id or default_currency_org_id
        if default_org_id:
            default_org = db.get(Organization, coerce_uuid(default_org_id))
            if default_org:
                default_functional_currency_code = default_org.functional_currency_code
                default_presentation_currency_code = default_org.presentation_currency_code
        if not default_functional_currency_code:
            default_functional_currency_code = settings.default_functional_currency_code
        if not default_presentation_currency_code:
            default_presentation_currency_code = settings.default_presentation_currency_code

        expense_accounts = []
        liability_accounts = []
        if organization_id:
            org = db.get(Organization, coerce_uuid(organization_id))
            if org:
                user_count = db.scalar(select(func.count(Person.id)).where(Person.organization_id == org.organization_id)) or 0
                subsidiaries_count = db.scalar(
                    select(func.count(Organization.organization_id)).where(Organization.parent_organization_id == org.organization_id)
                ) or 0
                organization_data = {
                    "id": str(org.organization_id),
                    "organization_code": org.organization_code,
                    "legal_name": org.legal_name,
                    "slug": org.slug or "",
                    "trading_name": org.trading_name or "",
                    "registration_number": org.registration_number or "",
                    "tax_identification_number": org.tax_identification_number or "",
                    "incorporation_date": org.incorporation_date.isoformat() if org.incorporation_date else "",
                    "jurisdiction_country_code": org.jurisdiction_country_code or "",
                    "functional_currency_code": org.functional_currency_code,
                    "presentation_currency_code": org.presentation_currency_code,
                    "fiscal_year_end_month": org.fiscal_year_end_month,
                    "fiscal_year_end_day": org.fiscal_year_end_day,
                    "parent_organization_id": str(org.parent_organization_id) if org.parent_organization_id else "",
                    "consolidation_method": org.consolidation_method.value if org.consolidation_method else "",
                    "ownership_percentage": str(org.ownership_percentage) if org.ownership_percentage else "",
                    "is_active": org.is_active,
                    "user_count": user_count,
                    "subsidiaries_count": subsidiaries_count,
                    "salaries_expense_account_id": str(org.salaries_expense_account_id) if org.salaries_expense_account_id else "",
                    "salary_payable_account_id": str(org.salary_payable_account_id) if org.salary_payable_account_id else "",
                }
                parent_org_list = [p for p in parent_org_list if p["id"] != str(org.organization_id)]
                org_uuid = coerce_uuid(organization_id)
                expense_accounts = [
                    {"account_id": str(a.account_id), "account_code": a.account_code, "account_name": a.account_name}
                    for a in db.scalars(
                        select(Account)
                        .join(AccountCategory, Account.category_id == AccountCategory.category_id)
                        .where(
                            Account.organization_id == org_uuid,
                            Account.is_active.is_(True),
                            Account.is_posting_allowed.is_(True),
                            AccountCategory.ifrs_category == IFRSCategory.EXPENSES,
                        )
                        .order_by(Account.account_code)
                    ).all()
                ]
                liability_accounts = [
                    {"account_id": str(a.account_id), "account_code": a.account_code, "account_name": a.account_name}
                    for a in db.scalars(
                        select(Account)
                        .join(AccountCategory, Account.category_id == AccountCategory.category_id)
                        .where(
                            Account.organization_id == org_uuid,
                            Account.is_active.is_(True),
                            Account.is_posting_allowed.is_(True),
                            AccountCategory.ifrs_category == IFRSCategory.LIABILITIES,
                        )
                        .order_by(Account.account_code)
                    ).all()
                ]
        return {
            "organization_data": organization_data,
            "parent_organizations": parent_org_list,
            "consolidation_methods": consolidation_methods,
            "default_functional_currency_code": default_functional_currency_code,
            "default_presentation_currency_code": default_presentation_currency_code,
            "expense_accounts": expense_accounts,
            "liability_accounts": liability_accounts,
            "public_url_base": settings.app_url.rstrip("/"),
        }

    @staticmethod
    def _normalize_org_slug(slug: str) -> str | None:
        value = (slug or "").strip().lower()
        return value or None

    @staticmethod
    def _validate_org_slug(slug: str | None) -> str | None:
        if not slug:
            return None
        if len(slug) > 50:
            return "Slug must be 50 characters or fewer"
        if not _ORG_SLUG_PATTERN.fullmatch(slug):
            return "Slug must contain only lowercase letters, numbers, and single hyphens"
        return None

    @staticmethod
    def create_organization(
        db: Session,
        organization_code: str,
        legal_name: str,
        functional_currency_code: str,
        presentation_currency_code: str,
        fiscal_year_end_month: int,
        fiscal_year_end_day: int,
        trading_name: str = "",
        registration_number: str = "",
        tax_identification_number: str = "",
        incorporation_date: str = "",
        jurisdiction_country_code: str = "",
        parent_organization_id: str = "",
        consolidation_method: str = "",
        ownership_percentage: str = "",
        is_active: bool = True,
        slug: str = "",
    ) -> tuple[Organization | None, str | None]:
        from app.models.finance.core_org.organization import ConsolidationMethod

        existing = db.scalar(select(Organization).where(Organization.organization_code == organization_code))
        if existing:
            return None, "An organization with this code already exists"
        normalized_slug = AdminOrganizationSettingsMixin._normalize_org_slug(slug)
        slug_error = AdminOrganizationSettingsMixin._validate_org_slug(normalized_slug)
        if slug_error:
            return None, slug_error
        if normalized_slug and db.scalar(select(Organization).where(Organization.slug == normalized_slug)):
            return None, "An organization with this slug already exists"
        try:
            incorp_date = date_type.fromisoformat(incorporation_date) if incorporation_date else None
            consol_method = ConsolidationMethod(consolidation_method) if consolidation_method else None
            ownership_pct = None
            if ownership_percentage:
                from decimal import Decimal

                ownership_pct = Decimal(ownership_percentage)
            parent_org_id = coerce_uuid(parent_organization_id) if parent_organization_id else None
            org = Organization(
                organization_code=organization_code,
                legal_name=legal_name,
                slug=normalized_slug,
                trading_name=trading_name if trading_name else None,
                registration_number=registration_number if registration_number else None,
                tax_identification_number=tax_identification_number if tax_identification_number else None,
                incorporation_date=incorp_date,
                jurisdiction_country_code=jurisdiction_country_code if jurisdiction_country_code else None,
                functional_currency_code=functional_currency_code,
                presentation_currency_code=presentation_currency_code,
                fiscal_year_end_month=fiscal_year_end_month,
                fiscal_year_end_day=fiscal_year_end_day,
                parent_organization_id=parent_org_id,
                consolidation_method=consol_method,
                ownership_percentage=ownership_pct,
                is_active=is_active,
            )
            db.add(org)
            db.flush()
            from app.services.settings.bank_directory import OrgBankDirectoryService

            OrgBankDirectoryService(db).seed_defaults(org.organization_id)
            db.commit()
            return org, None
        except Exception as exc:
            db.rollback()
            return None, f"Failed to create organization: {str(exc)}"

    @staticmethod
    def update_organization(
        db: Session,
        organization_id: str,
        organization_code: str,
        legal_name: str,
        functional_currency_code: str,
        presentation_currency_code: str,
        fiscal_year_end_month: int,
        fiscal_year_end_day: int,
        trading_name: str = "",
        registration_number: str = "",
        tax_identification_number: str = "",
        incorporation_date: str = "",
        jurisdiction_country_code: str = "",
        parent_organization_id: str = "",
        consolidation_method: str = "",
        ownership_percentage: str = "",
        is_active: bool = True,
        salaries_expense_account_id: str = "",
        salary_payable_account_id: str = "",
        slug: str = "",
    ) -> tuple[Organization | None, str | None]:
        from app.models.finance.core_org.organization import ConsolidationMethod

        org = db.get(Organization, coerce_uuid(organization_id))
        if not org:
            return None, "Organization not found"
        existing = db.scalar(
            select(Organization).where(
                Organization.organization_code == organization_code,
                Organization.organization_id != org.organization_id,
            )
        )
        if existing:
            return None, "An organization with this code already exists"
        if parent_organization_id and coerce_uuid(parent_organization_id) == org.organization_id:
            return None, "An organization cannot be its own parent"
        normalized_slug = AdminOrganizationSettingsMixin._normalize_org_slug(slug)
        slug_error = AdminOrganizationSettingsMixin._validate_org_slug(normalized_slug)
        if slug_error:
            return None, slug_error
        if normalized_slug:
            slug_exists = db.scalar(
                select(Organization).where(
                    Organization.slug == normalized_slug,
                    Organization.organization_id != org.organization_id,
                )
            )
            if slug_exists:
                return None, "An organization with this slug already exists"
        try:
            org.organization_code = organization_code
            org.legal_name = legal_name
            org.slug = normalized_slug
            org.trading_name = trading_name if trading_name else None
            org.registration_number = registration_number if registration_number else None
            org.tax_identification_number = tax_identification_number if tax_identification_number else None
            org.incorporation_date = date_type.fromisoformat(incorporation_date) if incorporation_date else None
            org.jurisdiction_country_code = jurisdiction_country_code if jurisdiction_country_code else None
            org.functional_currency_code = functional_currency_code
            org.presentation_currency_code = presentation_currency_code
            org.fiscal_year_end_month = fiscal_year_end_month
            org.fiscal_year_end_day = fiscal_year_end_day
            org.parent_organization_id = coerce_uuid(parent_organization_id) if parent_organization_id else None
            org.consolidation_method = ConsolidationMethod(consolidation_method) if consolidation_method else None
            if ownership_percentage:
                from decimal import Decimal

                org.ownership_percentage = Decimal(ownership_percentage)
            else:
                org.ownership_percentage = None
            org.is_active = is_active
            org.salaries_expense_account_id = coerce_uuid(salaries_expense_account_id) if salaries_expense_account_id else None
            org.salary_payable_account_id = coerce_uuid(salary_payable_account_id) if salary_payable_account_id else None
            db.commit()
            return org, None
        except Exception as exc:
            db.rollback()
            return None, f"Failed to update organization: {str(exc)}"

    @staticmethod
    def delete_organization(db: Session, organization_id: str) -> str | None:
        org = db.get(Organization, coerce_uuid(organization_id))
        if not org:
            return "Organization not found"
        user_count = db.scalar(select(func.count(Person.id)).where(Person.organization_id == org.organization_id)) or 0
        if user_count > 0:
            return f"Cannot delete organization with {user_count} user(s). Remove users first."
        subsidiaries_count = db.scalar(
            select(func.count(Organization.organization_id)).where(Organization.parent_organization_id == org.organization_id)
        ) or 0
        if subsidiaries_count > 0:
            return f"Cannot delete organization with {subsidiaries_count} subsidiary(ies). Remove subsidiaries first."
        try:
            db.delete(org)
            db.commit()
            return None
        except Exception as exc:
            db.rollback()
            return f"Failed to delete organization: {str(exc)}"

    @staticmethod
    def settings_context(db: Session, search: str | None, domain: str | None, status: str | None, page: int, limit: int = DEFAULT_PAGE_SIZE) -> dict:
        offset = (page - 1) * limit
        conditions = []
        domain_value = _parse_domain(domain)
        if domain_value:
            conditions.append(DomainSetting.domain == domain_value)
        search_value = search.strip() if search else ""
        if search_value:
            conditions.append(DomainSetting.key.ilike(f"%{search_value}%"))
        active_count = db.scalar(select(func.count(DomainSetting.id)).where(*conditions, DomainSetting.is_active.is_(True))) or 0
        inactive_count = db.scalar(select(func.count(DomainSetting.id)).where(*conditions, DomainSetting.is_active.is_(False))) or 0
        setting_conditions = list(conditions)
        status_flag = _parse_status_filter(status)
        if status_flag is not None:
            setting_conditions.append(DomainSetting.is_active == status_flag)
        total_count = db.scalar(select(func.count(DomainSetting.id)).where(*setting_conditions)) or 0
        settings_rows = list(
            db.scalars(
                select(DomainSetting)
                .where(*setting_conditions)
                .order_by(DomainSetting.domain, DomainSetting.key)
                .limit(limit)
                .offset(offset)
            ).all()
        )
        return {
            "settings": [
                {
                    "setting_id": setting.id,
                    "domain": setting.domain.value,
                    "key": setting.key,
                    "value": _setting_value_display(setting),
                    "value_type": setting.value_type.value,
                    "is_secret": setting.is_secret,
                    "is_active": setting.is_active,
                    "updated_at": _format_datetime(setting.updated_at or setting.created_at),
                }
                for setting in settings_rows
            ],
            "pagination": _build_pagination(page, max(1, (total_count + limit - 1) // limit), total_count, limit),
            "search": search_value,
            "status_filter": status or "",
            "domain_filter": domain_value.value if domain_value else "",
            "domains": [value.value for value in SettingDomain],
            "stats": {"active": active_count, "inactive": inactive_count, "total": active_count + inactive_count},
        }

    @staticmethod
    def setting_form_context(db: Session, setting_id: str | None = None) -> dict:
        setting_data = None
        if setting_id:
            setting = db.get(DomainSetting, coerce_uuid(setting_id))
            if setting:
                value = json.dumps(setting.value_json, indent=2) if setting.value_type == SettingValueType.json else (setting.value_text or "")
                setting_data = {
                    "id": str(setting.id),
                    "domain": setting.domain.value,
                    "key": setting.key,
                    "value_type": setting.value_type.value,
                    "value": value,
                    "is_secret": setting.is_secret,
                    "is_active": setting.is_active,
                }
        return {
            "setting_data": setting_data,
            "domains": [value.value for value in SettingDomain],
            "value_types": [value.value for value in SettingValueType],
        }

    @staticmethod
    def create_setting(db: Session, domain: str, key: str, value_type: str, value: str, is_secret: bool = False, is_active: bool = True) -> tuple[DomainSetting | None, str | None]:
        try:
            domain_enum = SettingDomain(domain)
        except ValueError:
            return None, f"Invalid domain: {domain}"
        try:
            value_type_enum = SettingValueType(value_type)
        except ValueError:
            return None, f"Invalid value type: {value_type}"
        existing = db.scalar(select(DomainSetting).where(DomainSetting.domain == domain_enum, DomainSetting.key == key))
        if existing:
            return None, f"A setting with key '{key}' already exists in domain '{domain}'"
        try:
            value_text = None
            value_json = None
            if value_type_enum == SettingValueType.json:
                try:
                    value_json = json.loads(value) if value else None
                except json.JSONDecodeError as exc:
                    return None, f"Invalid JSON value: {str(exc)}"
            elif value_type_enum == SettingValueType.boolean:
                value_text = "true" if value.lower() in ("true", "1", "yes", "on") else "false"
            elif value_type_enum == SettingValueType.integer:
                try:
                    int(value) if value else 0
                    value_text = value
                except ValueError:
                    return None, "Value must be a valid integer"
            else:
                value_text = value
            setting = DomainSetting(
                domain=domain_enum,
                key=key,
                value_type=value_type_enum,
                value_text=value_text,
                value_json=value_json,
                is_secret=is_secret,
                is_active=is_active,
            )
            db.add(setting)
            db.commit()
            return setting, None
        except Exception as exc:
            db.rollback()
            return None, f"Failed to create setting: {str(exc)}"

    @staticmethod
    def update_setting(db: Session, setting_id: str, domain: str, key: str, value_type: str, value: str, is_secret: bool = False, is_active: bool = True) -> tuple[DomainSetting | None, str | None]:
        setting = db.get(DomainSetting, coerce_uuid(setting_id))
        if not setting:
            return None, "Setting not found"
        try:
            domain_enum = SettingDomain(domain)
        except ValueError:
            return None, f"Invalid domain: {domain}"
        try:
            value_type_enum = SettingValueType(value_type)
        except ValueError:
            return None, f"Invalid value type: {value_type}"
        existing = db.scalar(
            select(DomainSetting).where(
                DomainSetting.domain == domain_enum,
                DomainSetting.key == key,
                DomainSetting.id != setting.id,
            )
        )
        if existing:
            return None, f"A setting with key '{key}' already exists in domain '{domain}'"
        try:
            value_text = None
            value_json = None
            if value_type_enum == SettingValueType.json:
                try:
                    value_json = json.loads(value) if value else None
                except json.JSONDecodeError as exc:
                    return None, f"Invalid JSON value: {str(exc)}"
            elif value_type_enum == SettingValueType.boolean:
                value_text = "true" if value.lower() in ("true", "1", "yes", "on") else "false"
            elif value_type_enum == SettingValueType.integer:
                try:
                    int(value) if value else 0
                    value_text = value
                except ValueError:
                    return None, "Value must be a valid integer"
            else:
                value_text = value
            setting.domain = domain_enum
            setting.key = key
            setting.value_type = value_type_enum
            setting.value_text = value_text
            setting.value_json = value_json
            setting.is_secret = is_secret
            setting.is_active = is_active
            db.commit()
            return setting, None
        except Exception as exc:
            db.rollback()
            return None, f"Failed to update setting: {str(exc)}"

    @staticmethod
    def delete_setting(db: Session, setting_id: str) -> str | None:
        setting = db.get(DomainSetting, coerce_uuid(setting_id))
        if not setting:
            return "Setting not found"
        try:
            db.delete(setting)
            db.commit()
            return None
        except Exception as exc:
            db.rollback()
            return f"Failed to delete setting: {str(exc)}"
