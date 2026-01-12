"""
Tax seed helpers for country-specific defaults.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy.orm import Session

from app.config import settings
from app.models.ifrs.core_fx.currency import Currency
from app.models.ifrs.core_org.organization import Organization
from app.models.ifrs.gl.account import Account, AccountType, NormalBalance
from app.models.ifrs.gl.account_category import AccountCategory, IFRSCategory
from app.models.ifrs.tax.tax_code import TaxCode, TaxType
from app.models.ifrs.tax.tax_jurisdiction import TaxJurisdiction
from app.services.common import coerce_uuid


NIGERIA_COUNTRY_CODE = "NGA"
NIGERIA_CURRENCY_CODE = settings.default_functional_currency_code
NIGERIA_JURISDICTION_CODE = "NG-FED"


@dataclass
class NigeriaSeedSummary:
    organization_id: UUID
    currency_created: int = 0
    categories_created: int = 0
    accounts_created: int = 0
    jurisdictions_created: int = 0
    tax_codes_created: int = 0


def _ensure_currency(db: Session, summary: NigeriaSeedSummary) -> None:
    currency = db.get(Currency, NIGERIA_CURRENCY_CODE)
    if currency:
        return

    db.add(
        Currency(
            currency_code=NIGERIA_CURRENCY_CODE,
            currency_name="Nigerian Naira",
            symbol=NIGERIA_CURRENCY_CODE,
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
    summary: NigeriaSeedSummary,
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
    summary: NigeriaSeedSummary,
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
    summary: NigeriaSeedSummary,
) -> TaxJurisdiction:
    jurisdiction = (
        db.query(TaxJurisdiction)
        .filter(
            TaxJurisdiction.organization_id == organization_id,
            TaxJurisdiction.jurisdiction_code == NIGERIA_JURISDICTION_CODE,
        )
        .first()
    )
    if jurisdiction:
        return jurisdiction

    jurisdiction = TaxJurisdiction(
        organization_id=organization_id,
        jurisdiction_code=NIGERIA_JURISDICTION_CODE,
        jurisdiction_name="Nigeria Federal",
        description="Seeded Nigeria federal tax jurisdiction",
        country_code=NIGERIA_COUNTRY_CODE,
        state_province=None,
        jurisdiction_level="COUNTRY",
        current_tax_rate=Decimal("0.30"),
        tax_rate_effective_from=date(2020, 1, 1),
        future_tax_rate=None,
        future_rate_effective_from=None,
        has_reduced_rate=False,
        reduced_rate=None,
        reduced_rate_threshold=None,
        fiscal_year_end_month=12,
        filing_due_months=6,
        extension_months=None,
        currency_code=NIGERIA_CURRENCY_CODE,
        tax_authority_name="Federal Inland Revenue Service",
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
    summary: NigeriaSeedSummary,
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


def seed_nigeria_tax_data(db: Session, organization_id: UUID | str) -> NigeriaSeedSummary:
    """
    Seed baseline Nigeria tax data for an organization.

    Adds default currency, core tax accounts, a Nigeria federal jurisdiction,
    and default VAT and withholding tax codes.
    """
    org_id = coerce_uuid(organization_id)
    org = db.get(Organization, org_id)
    if not org:
        raise ValueError(f"Organization {org_id} not found")

    summary = NigeriaSeedSummary(organization_id=org_id)

    _ensure_currency(db, summary)

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
        summary,
    )

    _ensure_tax_code(
        db,
        org_id,
        jurisdiction.jurisdiction_id,
        "NG-VAT-7.5",
        "Nigeria VAT 7.5%",
        TaxType.VAT,
        Decimal("0.075"),
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
    _ensure_tax_code(
        db,
        org_id,
        jurisdiction.jurisdiction_id,
        "NG-WHT-5",
        "Nigeria Withholding Tax 5%",
        TaxType.WITHHOLDING,
        Decimal("0.05"),
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
