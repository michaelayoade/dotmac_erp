"""
Tax seed helpers for country-specific defaults.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.finance.core_fx.currency import Currency
from app.models.finance.core_org.organization import Organization
from app.models.finance.gl.account import Account, AccountType, NormalBalance
from app.models.finance.gl.account_category import AccountCategory, IFRSCategory
from app.models.finance.tax.tax_code import TaxCode, TaxType
from app.models.finance.tax.tax_jurisdiction import TaxJurisdiction
from app.services.common import coerce_uuid

logger = logging.getLogger(__name__)

NIGERIA_COUNTRY_CODE = "NGA"
NIGERIA_JURISDICTION_CODE = "NG-FED"


@dataclass
class TaxSeedSummary:
    """Summary of tax seed operation results."""

    organization_id: UUID
    country_code: str
    currency_created: int = 0
    categories_created: int = 0
    accounts_created: int = 0
    jurisdictions_created: int = 0
    tax_codes_created: int = 0
    default_jurisdiction_id: UUID | None = None


# Legacy alias for backwards compatibility
NigeriaSeedSummary = TaxSeedSummary


@dataclass
class CountryTaxConfig:
    """Configuration for country-specific tax seeding."""

    country_code: str  # ISO 3166-1 alpha-3, e.g., "NGA"
    country_name: str
    jurisdiction_code: str  # e.g., "NG-FED"
    jurisdiction_name: str
    currency_code: str  # ISO 4217, e.g., "NGN"
    currency_name: str
    currency_symbol: str
    corporate_tax_rate: Decimal
    tax_authority_name: str
    vat_rate: Decimal | None = None
    vat_code: str | None = None
    vat_name: str | None = None
    withholding_rate: Decimal | None = None
    withholding_code: str | None = None
    withholding_name: str | None = None


# Country configuration registry
COUNTRY_CONFIGS: dict[str, CountryTaxConfig] = {
    "NGA": CountryTaxConfig(
        country_code="NGA",
        country_name="Nigeria",
        jurisdiction_code="NG-FED",
        jurisdiction_name="Nigeria Federal",
        currency_code="NGN",
        currency_name="Nigerian Naira",
        currency_symbol="₦",
        corporate_tax_rate=Decimal("0.30"),
        tax_authority_name="Federal Inland Revenue Service",
        vat_rate=Decimal("0.075"),
        vat_code="NG-VAT-7.5",
        vat_name="Nigeria VAT 7.5%",
        withholding_rate=Decimal("0.05"),
        withholding_code="NG-WHT-5",
        withholding_name="Nigeria Withholding Tax 5%",
    ),
    "NG": CountryTaxConfig(  # Alternative 2-char code for convenience
        country_code="NGA",
        country_name="Nigeria",
        jurisdiction_code="NG-FED",
        jurisdiction_name="Nigeria Federal",
        currency_code="NGN",
        currency_name="Nigerian Naira",
        currency_symbol="₦",
        corporate_tax_rate=Decimal("0.30"),
        tax_authority_name="Federal Inland Revenue Service",
        vat_rate=Decimal("0.075"),
        vat_code="NG-VAT-7.5",
        vat_name="Nigeria VAT 7.5%",
        withholding_rate=Decimal("0.05"),
        withholding_code="NG-WHT-5",
        withholding_name="Nigeria Withholding Tax 5%",
    ),
    "GBR": CountryTaxConfig(
        country_code="GBR",
        country_name="United Kingdom",
        jurisdiction_code="UK-FED",
        jurisdiction_name="United Kingdom",
        currency_code="GBP",
        currency_name="British Pound Sterling",
        currency_symbol="£",
        corporate_tax_rate=Decimal("0.25"),
        tax_authority_name="HM Revenue & Customs",
        vat_rate=Decimal("0.20"),
        vat_code="UK-VAT-20",
        vat_name="UK VAT 20%",
    ),
    "GB": CountryTaxConfig(  # Alternative 2-char code
        country_code="GBR",
        country_name="United Kingdom",
        jurisdiction_code="UK-FED",
        jurisdiction_name="United Kingdom",
        currency_code="GBP",
        currency_name="British Pound Sterling",
        currency_symbol="£",
        corporate_tax_rate=Decimal("0.25"),
        tax_authority_name="HM Revenue & Customs",
        vat_rate=Decimal("0.20"),
        vat_code="UK-VAT-20",
        vat_name="UK VAT 20%",
    ),
    "USA": CountryTaxConfig(
        country_code="USA",
        country_name="United States",
        jurisdiction_code="US-FED",
        jurisdiction_name="United States Federal",
        currency_code="USD",
        currency_name="United States Dollar",
        currency_symbol="$",
        corporate_tax_rate=Decimal("0.21"),
        tax_authority_name="Internal Revenue Service",
    ),
    "US": CountryTaxConfig(  # Alternative 2-char code
        country_code="USA",
        country_name="United States",
        jurisdiction_code="US-FED",
        jurisdiction_name="United States Federal",
        currency_code="USD",
        currency_name="United States Dollar",
        currency_symbol="$",
        corporate_tax_rate=Decimal("0.21"),
        tax_authority_name="Internal Revenue Service",
    ),
}


def get_country_config(country_code: str) -> CountryTaxConfig | None:
    """Get country configuration by code (supports both 2 and 3 char codes)."""
    return COUNTRY_CONFIGS.get(country_code.upper())


def _ensure_currency(
    db: Session,
    currency_code: str,
    summary: TaxSeedSummary,
    currency_name: str = "Unknown Currency",
    currency_symbol: str = "",
) -> None:
    currency = db.get(Currency, currency_code)
    if currency:
        return

    db.add(
        Currency(
            currency_code=currency_code,
            currency_name=currency_name,
            symbol=currency_symbol or currency_code,
            decimal_places=2,
            is_active=True,
            is_crypto=False,
        )
    )
    summary.currency_created += 1


def _ensure_category(
    db: Session,
    organization_id: UUID,
    ifrs_category: IFRSCategory,
    default_code: str,
    default_name: str,
    summary: TaxSeedSummary,
) -> AccountCategory:
    category = (
        db.query(AccountCategory)
        .filter(
            AccountCategory.organization_id == organization_id,
            AccountCategory.ifrs_category == ifrs_category,
            AccountCategory.is_active.is_(True),
        )
        .order_by(AccountCategory.category_code)
        .first()
    )
    if category:
        return category

    code = default_code
    code_taken = (
        db.query(AccountCategory)
        .filter(
            AccountCategory.organization_id == organization_id,
            AccountCategory.category_code == code,
        )
        .first()
    )
    if code_taken:
        code = f"{default_code}-TAX"

    category = AccountCategory(
        organization_id=organization_id,
        category_code=code,
        category_name=default_name,
        description=f"Seeded {default_name} category",
        ifrs_category=ifrs_category,
        hierarchy_level=1,
        display_order=1,
        is_active=True,
    )
    db.add(category)
    summary.categories_created += 1
    return category


def _ensure_account(
    db: Session,
    organization_id: UUID,
    category_id: UUID,
    account_code: str,
    account_name: str,
    normal_balance: NormalBalance,
    summary: TaxSeedSummary,
    description: str = "",
) -> Account:
    account = (
        db.query(Account)
        .filter(
            Account.organization_id == organization_id,
            Account.account_code == account_code,
        )
        .first()
    )
    if account:
        if not account.is_active:
            account.is_active = True
        if not account.is_posting_allowed:
            account.is_posting_allowed = True
        return account

    account = Account(
        organization_id=organization_id,
        category_id=category_id,
        account_code=account_code,
        account_name=account_name,
        description=description or None,
        account_type=AccountType.POSTING,
        normal_balance=normal_balance,
        is_active=True,
        is_posting_allowed=True,
        is_budgetable=False,
        is_reconciliation_required=False,
    )
    db.add(account)
    summary.accounts_created += 1
    return account


def _ensure_jurisdiction(
    db: Session,
    organization_id: UUID,
    current_tax_payable_account_id: UUID,
    current_tax_expense_account_id: UUID,
    deferred_tax_asset_account_id: UUID,
    deferred_tax_liability_account_id: UUID,
    deferred_tax_expense_account_id: UUID,
    currency_code: str,
    summary: TaxSeedSummary,
    jurisdiction_code: str = NIGERIA_JURISDICTION_CODE,
    jurisdiction_name: str = "Nigeria Federal",
    country_code: str = NIGERIA_COUNTRY_CODE,
    corporate_tax_rate: Decimal = Decimal("0.30"),
    tax_authority_name: str = "Federal Inland Revenue Service",
) -> TaxJurisdiction:
    jurisdiction = (
        db.query(TaxJurisdiction)
        .filter(
            TaxJurisdiction.organization_id == organization_id,
            TaxJurisdiction.jurisdiction_code == jurisdiction_code,
        )
        .first()
    )
    if jurisdiction:
        return jurisdiction

    jurisdiction = TaxJurisdiction(
        organization_id=organization_id,
        jurisdiction_code=jurisdiction_code,
        jurisdiction_name=jurisdiction_name,
        description=f"Seeded {jurisdiction_name} tax jurisdiction",
        country_code=country_code,
        state_province=None,
        jurisdiction_level="COUNTRY",
        current_tax_rate=corporate_tax_rate,
        tax_rate_effective_from=date(2020, 1, 1),
        future_tax_rate=None,
        future_rate_effective_from=None,
        has_reduced_rate=False,
        reduced_rate=None,
        reduced_rate_threshold=None,
        fiscal_year_end_month=12,
        filing_due_months=6,
        extension_months=None,
        currency_code=currency_code,
        tax_authority_name=tax_authority_name,
        tax_id_number=None,
        current_tax_payable_account_id=current_tax_payable_account_id,
        current_tax_expense_account_id=current_tax_expense_account_id,
        deferred_tax_asset_account_id=deferred_tax_asset_account_id,
        deferred_tax_liability_account_id=deferred_tax_liability_account_id,
        deferred_tax_expense_account_id=deferred_tax_expense_account_id,
        is_active=True,
    )
    db.add(jurisdiction)
    summary.jurisdictions_created += 1
    return jurisdiction


def _ensure_tax_code(
    db: Session,
    organization_id: UUID,
    jurisdiction_id: UUID,
    tax_code: str,
    tax_name: str,
    tax_type: TaxType,
    tax_rate: Decimal,
    tax_collected_account_id: UUID | None,
    tax_paid_account_id: UUID | None,
    tax_expense_account_id: UUID | None,
    summary: TaxSeedSummary,
    is_recoverable: bool,
    recovery_rate: Decimal,
    applies_to_purchases: bool,
    applies_to_sales: bool,
    tax_return_box: str | None = None,
    reporting_code: str | None = None,
) -> TaxCode:
    existing = (
        db.query(TaxCode)
        .filter(
            TaxCode.organization_id == organization_id,
            TaxCode.tax_code == tax_code,
        )
        .first()
    )
    if existing:
        return existing

    code = TaxCode(
        organization_id=organization_id,
        tax_code=tax_code,
        tax_name=tax_name,
        description=None,
        tax_type=tax_type,
        jurisdiction_id=jurisdiction_id,
        tax_rate=tax_rate,
        effective_from=date(2020, 1, 1),
        effective_to=None,
        is_compound=False,
        is_inclusive=False,
        is_recoverable=is_recoverable,
        recovery_rate=recovery_rate,
        applies_to_purchases=applies_to_purchases,
        applies_to_sales=applies_to_sales,
        tax_return_box=tax_return_box,
        reporting_code=reporting_code,
        tax_collected_account_id=tax_collected_account_id,
        tax_paid_account_id=tax_paid_account_id,
        tax_expense_account_id=tax_expense_account_id,
        is_active=True,
    )
    db.add(code)
    summary.tax_codes_created += 1
    return code


def seed_nigeria_tax_data(db: Session, organization_id: UUID | str) -> TaxSeedSummary:
    """
    Seed baseline Nigeria tax data for an organization.

    Adds default currency, core tax accounts, a Nigeria federal jurisdiction,
    and default VAT and withholding tax codes.

    This is a convenience wrapper around seed_default_tax_data() for Nigeria.
    """
    return seed_default_tax_data(db, organization_id, country_code="NGA")


def seed_default_tax_data(
    db: Session,
    organization_id: UUID | str,
    country_code: str | None = None,
    create_tax_codes: bool = True,
) -> TaxSeedSummary:
    """
    Seed default tax data for an organization based on country configuration.

    Creates:
    - Required GL account categories (Assets, Liabilities, Expenses)
    - Required GL accounts for tax (Tax Payable, VAT In/Out, WHT, etc.)
    - A default tax jurisdiction for the country
    - Common tax codes (VAT, WHT) if configured for the country

    Args:
        db: Database session
        organization_id: Organization to seed
        country_code: ISO country code (2 or 3 char). If None, uses org's
                      jurisdiction_country_code or defaults to Nigeria.
        create_tax_codes: Whether to also create default tax codes

    Returns:
        TaxSeedSummary with counts of created entities
    """
    org_id = coerce_uuid(organization_id)
    org = db.get(Organization, org_id)
    if not org:
        raise ValueError(f"Organization {org_id} not found")

    # Determine country code
    effective_country = country_code or org.jurisdiction_country_code or "NG"
    config = get_country_config(effective_country)
    if not config:
        # Fall back to Nigeria if unknown country
        config = get_country_config("NGA")
        if not config:
            raise ValueError(f"No tax configuration for country: {effective_country}")

    summary = TaxSeedSummary(organization_id=org_id, country_code=config.country_code)

    # Use organization's currency or country default
    currency_code = org.functional_currency_code or config.currency_code
    _ensure_currency(
        db,
        currency_code,
        summary,
        currency_name=config.currency_name,
        currency_symbol=config.currency_symbol,
    )

    assets_category = _ensure_category(
        db, org_id, IFRSCategory.ASSETS, "AST", "Assets", summary
    )
    liabilities_category = _ensure_category(
        db, org_id, IFRSCategory.LIABILITIES, "LIA", "Liabilities", summary
    )
    expenses_category = _ensure_category(
        db, org_id, IFRSCategory.EXPENSES, "EXP", "Expenses", summary
    )
    db.flush()

    tax_payable = _ensure_account(
        db,
        org_id,
        liabilities_category.category_id,
        "TAX-PAY",
        "Current Tax Payable",
        NormalBalance.CREDIT,
        summary,
        description="Seeded tax payable account",
    )
    vat_output = _ensure_account(
        db,
        org_id,
        liabilities_category.category_id,
        "VAT-OUT",
        "VAT Output (Tax Collected)",
        NormalBalance.CREDIT,
        summary,
        description="Seeded VAT output account",
    )
    withholding_payable = _ensure_account(
        db,
        org_id,
        liabilities_category.category_id,
        "WHT-PAY",
        "Withholding Tax Payable",
        NormalBalance.CREDIT,
        summary,
        description="Seeded withholding tax payable account",
    )
    vat_input = _ensure_account(
        db,
        org_id,
        assets_category.category_id,
        "VAT-IN",
        "VAT Input (Tax Paid)",
        NormalBalance.DEBIT,
        summary,
        description="Seeded VAT input account",
    )
    tax_expense = _ensure_account(
        db,
        org_id,
        expenses_category.category_id,
        "TAX-EXP",
        "Current Tax Expense",
        NormalBalance.DEBIT,
        summary,
        description="Seeded current tax expense account",
    )
    deferred_tax_expense = _ensure_account(
        db,
        org_id,
        expenses_category.category_id,
        "DT-EXP",
        "Deferred Tax Expense",
        NormalBalance.DEBIT,
        summary,
        description="Seeded deferred tax expense account",
    )
    deferred_tax_asset = _ensure_account(
        db,
        org_id,
        assets_category.category_id,
        "DTA",
        "Deferred Tax Asset",
        NormalBalance.DEBIT,
        summary,
        description="Seeded deferred tax asset account",
    )
    deferred_tax_liability = _ensure_account(
        db,
        org_id,
        liabilities_category.category_id,
        "DTL",
        "Deferred Tax Liability",
        NormalBalance.CREDIT,
        summary,
        description="Seeded deferred tax liability account",
    )
    db.flush()

    jurisdiction = _ensure_jurisdiction(
        db,
        org_id,
        tax_payable.account_id,
        tax_expense.account_id,
        deferred_tax_asset.account_id,
        deferred_tax_liability.account_id,
        deferred_tax_expense.account_id,
        currency_code,
        summary,
        jurisdiction_code=config.jurisdiction_code,
        jurisdiction_name=config.jurisdiction_name,
        country_code=config.country_code,
        corporate_tax_rate=config.corporate_tax_rate,
        tax_authority_name=config.tax_authority_name,
    )
    summary.default_jurisdiction_id = jurisdiction.jurisdiction_id

    # Create tax codes if requested and configured
    if create_tax_codes:
        # VAT tax code
        if config.vat_rate and config.vat_code and config.vat_name:
            _ensure_tax_code(
                db,
                org_id,
                jurisdiction.jurisdiction_id,
                config.vat_code,
                config.vat_name,
                TaxType.VAT,
                config.vat_rate,
                vat_output.account_id,
                vat_input.account_id,
                tax_expense.account_id,
                summary,
                is_recoverable=True,
                recovery_rate=Decimal("1.0"),
                applies_to_purchases=True,
                applies_to_sales=True,
                tax_return_box="VAT",
                reporting_code="VAT",
            )

        # Withholding tax code
        if (
            config.withholding_rate
            and config.withholding_code
            and config.withholding_name
        ):
            _ensure_tax_code(
                db,
                org_id,
                jurisdiction.jurisdiction_id,
                config.withholding_code,
                config.withholding_name,
                TaxType.WITHHOLDING,
                config.withholding_rate,
                withholding_payable.account_id,
                None,
                None,
                summary,
                is_recoverable=False,
                recovery_rate=Decimal("0.0"),
                applies_to_purchases=True,
                applies_to_sales=False,
                tax_return_box="WHT",
                reporting_code="WHT",
            )

    db.commit()
    return summary


def get_default_jurisdiction(
    db: Session,
    organization_id: UUID | str,
) -> TaxJurisdiction | None:
    """
    Get the default tax jurisdiction for an organization.

    Looks for a jurisdiction matching the organization's country code.
    Returns the first active jurisdiction if found.
    """
    org_id = coerce_uuid(organization_id)
    org = db.get(Organization, org_id)
    if not org:
        return None

    # Get country config to find expected jurisdiction code
    country_code = org.jurisdiction_country_code
    if country_code:
        config = get_country_config(country_code)
        if config:
            jurisdiction = (
                db.query(TaxJurisdiction)
                .filter(
                    TaxJurisdiction.organization_id == org_id,
                    TaxJurisdiction.jurisdiction_code == config.jurisdiction_code,
                    TaxJurisdiction.is_active.is_(True),
                )
                .first()
            )
            if jurisdiction:
                return jurisdiction

    # Fallback: return first active jurisdiction
    return (
        db.query(TaxJurisdiction)
        .filter(
            TaxJurisdiction.organization_id == org_id,
            TaxJurisdiction.is_active.is_(True),
        )
        .order_by(TaxJurisdiction.jurisdiction_code)
        .first()
    )


def get_or_create_default_jurisdiction(
    db: Session,
    organization_id: UUID | str,
) -> TaxJurisdiction:
    """
    Get or create the default tax jurisdiction for an organization.

    If no jurisdiction exists, seeds the default tax data for the
    organization's country.
    """
    jurisdiction = get_default_jurisdiction(db, organization_id)
    if jurisdiction:
        return jurisdiction

    # Seed tax data which creates the default jurisdiction
    summary = seed_default_tax_data(db, organization_id, create_tax_codes=False)
    if summary.default_jurisdiction_id:
        jurisdiction = db.get(TaxJurisdiction, summary.default_jurisdiction_id)
        if jurisdiction:
            return jurisdiction

    raise ValueError("Failed to create default jurisdiction")
