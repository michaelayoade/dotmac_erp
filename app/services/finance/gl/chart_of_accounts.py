"""
ChartOfAccountsService - Account management.

Manages chart of accounts entries including creation, updates, and queries.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.finance.gl.account import Account, AccountType, NormalBalance
from app.models.finance.gl.account_category import AccountCategory, IFRSCategory
from app.services.common import coerce_uuid
from app.services.finance.common import (
    apply_search_filter,
    get_org_scoped_entity,
    toggle_entity_status,
    validate_unique_code,
)
from app.services.finance.platform.org_context import org_context_service
from app.services.response import ListResponseMixin

logger = logging.getLogger(__name__)

# Standard chart of accounts code prefixes by IFRS category
IFRS_CODE_PREFIXES = {
    IFRSCategory.ASSETS: "1",
    IFRSCategory.LIABILITIES: "2",
    IFRSCategory.EQUITY: "3",
    IFRSCategory.REVENUE: "4",
    IFRSCategory.EXPENSES: "5",
    IFRSCategory.OTHER_COMPREHENSIVE_INCOME: "6",
}


@dataclass
class AccountInput:
    """Input for creating/updating an account."""

    account_code: str
    account_name: str
    category_id: UUID
    normal_balance: NormalBalance
    account_type: AccountType = AccountType.POSTING
    description: str | None = None
    search_terms: str | None = None
    is_multi_currency: bool = False
    default_currency_code: str | None = None
    is_active: bool = True
    is_posting_allowed: bool = True
    is_budgetable: bool = True
    is_reconciliation_required: bool = False
    subledger_type: str | None = None
    is_cash_equivalent: bool = False
    is_financial_instrument: bool = False


class ChartOfAccountsService(ListResponseMixin):
    """
    Service for chart of accounts management.

    Manages account creation, updates, and queries.
    """

    @staticmethod
    def ensure_default_categories(
        db: Session,
        organization_id: UUID,
    ) -> list[AccountCategory]:
        """Ensure default account categories exist and return active categories."""
        org_id = coerce_uuid(organization_id)

        categories = (
            db.query(AccountCategory)
            .filter(
                AccountCategory.organization_id == org_id,
                AccountCategory.is_active.is_(True),
            )
            .order_by(AccountCategory.category_code)
            .all()
        )

        if categories:
            return categories

        defaults = [
            ("AST", "Assets", IFRSCategory.ASSETS),
            ("LIA", "Liabilities", IFRSCategory.LIABILITIES),
            ("EQT", "Equity", IFRSCategory.EQUITY),
            ("REV", "Revenue", IFRSCategory.REVENUE),
            ("EXP", "Expenses", IFRSCategory.EXPENSES),
            (
                "OCI",
                "Other Comprehensive Income",
                IFRSCategory.OTHER_COMPREHENSIVE_INCOME,
            ),
        ]
        seeded = []
        for index, (code, name, ifrs_cat) in enumerate(defaults, start=1):
            seeded.append(
                AccountCategory(
                    organization_id=org_id,
                    category_code=code,
                    category_name=name,
                    description=f"Default {name} category",
                    ifrs_category=ifrs_cat,
                    hierarchy_level=1,
                    display_order=index,
                    is_active=True,
                )
            )
        db.add_all(seeded)
        db.commit()

        return (
            db.query(AccountCategory)
            .filter(
                AccountCategory.organization_id == org_id,
                AccountCategory.is_active.is_(True),
            )
            .order_by(AccountCategory.category_code)
            .all()
        )

    @staticmethod
    def build_input_from_payload(
        db: Session,
        organization_id: UUID,
        payload: dict,
    ) -> AccountInput:
        """Build AccountInput from raw payload."""
        org_id = coerce_uuid(organization_id)

        account_type_raw = payload.get("account_type")
        normal_balance_raw = payload.get("normal_balance")
        try:
            account_type = AccountType(account_type_raw)
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid account type: {account_type_raw}",
            ) from exc

        try:
            normal_balance = NormalBalance(normal_balance_raw)
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid normal balance: {normal_balance_raw}",
            ) from exc

        category_id = payload.get("category_id")
        if not category_id:
            raise HTTPException(status_code=400, detail="Category is required")

        category = get_org_scoped_entity(
            db=db,
            model_class=AccountCategory,
            entity_id=category_id,
            org_id=org_id,
            entity_name="Account category",
        )
        if category is None:
            raise HTTPException(status_code=404, detail="Account category not found")

        default_currency = payload.get(
            "default_currency_code"
        ) or org_context_service.get_functional_currency(db, org_id)

        return AccountInput(
            account_code=payload.get("account_code", ""),
            account_name=payload.get("account_name", ""),
            category_id=coerce_uuid(category_id),
            account_type=account_type,
            normal_balance=normal_balance,
            description=payload.get("description") or None,
            search_terms=payload.get("search_terms") or None,
            is_multi_currency=bool(payload.get("is_multi_currency")),
            default_currency_code=default_currency,
            is_active=payload.get("is_active", True),
            is_posting_allowed=payload.get("is_posting_allowed", True),
            is_budgetable=payload.get("is_budgetable", True),
            is_reconciliation_required=payload.get("is_reconciliation_required", False),
            subledger_type=payload.get("subledger_type") or None,
            is_cash_equivalent=payload.get("is_cash_equivalent", False),
            is_financial_instrument=payload.get("is_financial_instrument", False),
        )

    @staticmethod
    def create_account(
        db: Session,
        organization_id: UUID,
        input: AccountInput,
        created_by_user_id: UUID | None = None,
    ) -> Account:
        """
        Create a new account.

        Args:
            db: Database session
            organization_id: Organization scope
            input: Account input data
            created_by_user_id: User creating the account (for audit trail)

        Returns:
            Created Account

        Raises:
            HTTPException(400): If account code already exists
        """
        org_id = coerce_uuid(organization_id)

        # Validate unique account code
        validate_unique_code(
            db=db,
            model_class=Account,
            org_id=org_id,
            code_value=input.account_code,
            code_field_name="account_code",
            entity_name="Account",
        )

        account = Account(
            organization_id=org_id,
            category_id=coerce_uuid(input.category_id),
            account_code=input.account_code,
            account_name=input.account_name,
            description=input.description,
            search_terms=input.search_terms,
            account_type=input.account_type,
            normal_balance=input.normal_balance,
            is_multi_currency=input.is_multi_currency,
            default_currency_code=input.default_currency_code,
            is_active=input.is_active,
            is_posting_allowed=input.is_posting_allowed,
            is_budgetable=input.is_budgetable,
            is_reconciliation_required=input.is_reconciliation_required,
            subledger_type=input.subledger_type,
            is_cash_equivalent=input.is_cash_equivalent,
            is_financial_instrument=input.is_financial_instrument,
            created_by_user_id=created_by_user_id,
        )

        db.add(account)
        db.commit()
        db.refresh(account)

        return account

    @staticmethod
    def update_account_full(
        db: Session,
        organization_id: UUID,
        account_id: UUID,
        input: AccountInput,
        updated_by_user_id: UUID | None = None,
    ) -> Account:
        """Update an existing account using full input."""
        org_id = coerce_uuid(organization_id)
        acc_id = coerce_uuid(account_id)

        account = get_org_scoped_entity(
            db=db,
            model_class=Account,
            entity_id=acc_id,
            org_id=org_id,
            entity_name="Account",
        )
        if account is None:
            raise HTTPException(status_code=404, detail="Account not found")

        existing = (
            db.query(Account)
            .filter(Account.organization_id == org_id)
            .filter(Account.account_code == input.account_code)
            .filter(Account.account_id != acc_id)
            .first()
        )
        if existing:
            raise HTTPException(
                status_code=400,
                detail=f"Account code '{input.account_code}' already exists",
            )

        account.account_code = input.account_code
        account.account_name = input.account_name
        account.description = input.description
        account.search_terms = input.search_terms
        account.category_id = coerce_uuid(input.category_id)
        account.account_type = input.account_type
        account.normal_balance = input.normal_balance
        account.is_multi_currency = input.is_multi_currency
        account.default_currency_code = input.default_currency_code
        account.is_active = input.is_active
        account.is_posting_allowed = input.is_posting_allowed
        account.is_budgetable = input.is_budgetable
        account.is_reconciliation_required = input.is_reconciliation_required
        account.subledger_type = input.subledger_type
        account.is_cash_equivalent = input.is_cash_equivalent
        account.is_financial_instrument = input.is_financial_instrument
        if updated_by_user_id is not None:
            account.updated_by_user_id = updated_by_user_id

        db.commit()
        db.refresh(account)

        return account

    @staticmethod
    def delete_account(
        db: Session,
        organization_id: UUID,
        account_id: UUID,
    ) -> None:
        """Delete an account if it has no dependent records."""
        org_id = coerce_uuid(organization_id)
        acc_id = coerce_uuid(account_id)

        account = get_org_scoped_entity(
            db=db,
            model_class=Account,
            entity_id=acc_id,
            org_id=org_id,
            entity_name="Account",
        )
        if account is None:
            raise HTTPException(status_code=404, detail="Account not found")

        from app.models.finance.gl.account_balance import AccountBalance
        from app.models.finance.gl.journal_entry_line import JournalEntryLine

        line_count = (
            db.query(JournalEntryLine)
            .filter(JournalEntryLine.account_id == acc_id)
            .count()
        )
        if line_count > 0:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Cannot delete account with {line_count} journal entries. "
                    "Deactivate instead."
                ),
            )

        balance_count = (
            db.query(AccountBalance).filter(AccountBalance.account_id == acc_id).count()
        )
        if balance_count > 0:
            raise HTTPException(
                status_code=400,
                detail="Cannot delete account with balance records. Deactivate instead.",
            )

        db.delete(account)
        db.commit()

    @staticmethod
    def update_account(
        db: Session,
        organization_id: UUID,
        account_id: UUID,
        account_name: str | None = None,
        description: str | None = None,
        search_terms: str | None = None,
        is_active: bool | None = None,
        is_posting_allowed: bool | None = None,
        is_budgetable: bool | None = None,
        is_reconciliation_required: bool | None = None,
        is_multi_currency: bool | None = None,
        default_currency_code: str | None = None,
        subledger_type: str | None = None,
        is_cash_equivalent: bool | None = None,
        is_financial_instrument: bool | None = None,
        updated_by_user_id: UUID | None = None,
    ) -> Account:
        """
        Update an existing account.

        Args:
            db: Database session
            organization_id: Organization scope
            account_id: Account to update
            Various optional fields to update
            updated_by_user_id: User updating the account (for audit trail)

        Returns:
            Updated Account

        Raises:
            HTTPException(404): If account not found
        """
        acc_id = coerce_uuid(account_id)

        account = get_org_scoped_entity(
            db=db,
            model_class=Account,
            entity_id=acc_id,
            org_id=organization_id,
            entity_name="Account",
        )
        if account is None:
            raise HTTPException(status_code=404, detail="Account not found")

        if account_name is not None:
            account.account_name = account_name
        if description is not None:
            account.description = description
        if search_terms is not None:
            account.search_terms = search_terms
        if is_active is not None:
            account.is_active = is_active
        if is_posting_allowed is not None:
            account.is_posting_allowed = is_posting_allowed
        if is_budgetable is not None:
            account.is_budgetable = is_budgetable
        if is_reconciliation_required is not None:
            account.is_reconciliation_required = is_reconciliation_required
        if is_multi_currency is not None:
            account.is_multi_currency = is_multi_currency
        if default_currency_code is not None:
            account.default_currency_code = default_currency_code
        if subledger_type is not None:
            account.subledger_type = subledger_type
        if is_cash_equivalent is not None:
            account.is_cash_equivalent = is_cash_equivalent
        if is_financial_instrument is not None:
            account.is_financial_instrument = is_financial_instrument
        if updated_by_user_id is not None:
            account.updated_by_user_id = updated_by_user_id

        db.commit()
        db.refresh(account)

        return account

    @staticmethod
    def deactivate_account(
        db: Session,
        organization_id: UUID,
        account_id: UUID,
    ) -> Account:
        """
        Deactivate an account.

        Args:
            db: Database session
            organization_id: Organization scope
            account_id: Account to deactivate

        Returns:
            Updated Account

        Raises:
            HTTPException(404): If account not found
        """
        return toggle_entity_status(
            db=db,
            model_class=Account,
            entity_id=account_id,
            org_id=organization_id,
            is_active=False,
            entity_name="Account",
        )

    @staticmethod
    def activate_account(
        db: Session,
        organization_id: UUID,
        account_id: UUID,
    ) -> Account:
        """
        Activate an account.

        Args:
            db: Database session
            organization_id: Organization scope
            account_id: Account to activate

        Returns:
            Updated Account

        Raises:
            HTTPException(404): If account not found
        """
        return toggle_entity_status(
            db=db,
            model_class=Account,
            entity_id=account_id,
            org_id=organization_id,
            is_active=True,
            entity_name="Account",
        )

    @staticmethod
    def get(
        db: Session,
        account_id: str,
        organization_id: UUID | None = None,
    ) -> Account:
        """
        Get an account by ID.

        Args:
            db: Database session
            account_id: Account ID

        Returns:
            Account

        Raises:
            HTTPException(404): If not found
        """
        account = db.get(Account, coerce_uuid(account_id))
        if not account:
            raise HTTPException(status_code=404, detail="Account not found")
        if organization_id is not None and account.organization_id != coerce_uuid(
            organization_id
        ):
            raise HTTPException(status_code=404, detail="Account not found")
        return account

    @staticmethod
    def get_by_code(
        db: Session,
        organization_id: UUID,
        account_code: str,
    ) -> Account:
        """
        Get an account by code.

        Args:
            db: Database session
            organization_id: Organization scope
            account_code: Account code

        Returns:
            Account

        Raises:
            HTTPException(404): If not found
        """
        org_id = coerce_uuid(organization_id)

        account = (
            db.query(Account)
            .filter(
                Account.organization_id == org_id,
                Account.account_code == account_code,
            )
            .first()
        )
        if not account:
            raise HTTPException(status_code=404, detail="Account not found")
        return account

    @staticmethod
    def list(
        db: Session,
        organization_id: str | None = None,
        category_id: str | None = None,
        account_type: AccountType | None = None,
        is_active: bool | None = None,
        is_posting_allowed: bool | None = None,
        search: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Account]:
        """
        List accounts with filters.

        Args:
            db: Database session
            organization_id: Filter by organization
            category_id: Filter by category
            account_type: Filter by type
            is_active: Filter by active status
            is_posting_allowed: Filter by posting allowed
            search: Search in code, name, and search_terms
            limit: Maximum results
            offset: Pagination offset

        Returns:
            List of Account objects
        """
        query = db.query(Account)

        if organization_id:
            query = query.filter(
                Account.organization_id == coerce_uuid(organization_id)
            )

        if category_id:
            query = query.filter(Account.category_id == coerce_uuid(category_id))

        if account_type:
            query = query.filter(Account.account_type == account_type)

        if is_active is not None:
            query = query.filter(Account.is_active == is_active)

        if is_posting_allowed is not None:
            query = query.filter(Account.is_posting_allowed == is_posting_allowed)

        query = apply_search_filter(
            query,
            search,
            [
                Account.account_code,
                Account.account_name,
                Account.search_terms,
            ],
        )

        query = query.order_by(Account.account_code)
        return query.limit(limit).offset(offset).all()

    @staticmethod
    def suggest_next_code(
        db: Session,
        organization_id: str,
        category_id: str,
    ) -> dict:
        """
        Suggest the next account code based on category.

        Uses standard chart of accounts numbering:
        - 1xxx = Assets
        - 2xxx = Liabilities
        - 3xxx = Equity
        - 4xxx = Revenue
        - 5xxx = Expenses
        - 6xxx = Other Comprehensive Income

        Args:
            db: Database session
            organization_id: Organization scope
            category_id: Category to suggest code for

        Returns:
            dict with suggested_code and prefix
        """
        org_id = coerce_uuid(organization_id)

        # Get the category to determine IFRS category
        category = get_org_scoped_entity(
            db=db,
            model_class=AccountCategory,
            entity_id=category_id,
            org_id=org_id,
            entity_name="Category",
            raise_on_missing=False,
        )
        if not category:
            return {
                "suggested_code": None,
                "prefix": None,
                "error": "Category not found",
            }

        ifrs_category = category.ifrs_category
        prefix = IFRS_CODE_PREFIXES.get(ifrs_category, "9")

        # Find the highest account code starting with this prefix
        highest = (
            db.query(Account.account_code)
            .filter(
                Account.organization_id == org_id,
                Account.account_code.like(f"{prefix}%"),
            )
            .order_by(Account.account_code.desc())
            .first()
        )

        if highest and highest[0]:
            # Try to extract numeric part and increment
            code = highest[0]
            # Find the numeric portion
            numeric_part = ""
            for char in code:
                if char.isdigit():
                    numeric_part += char
                else:
                    break

            if numeric_part:
                next_num = int(numeric_part) + 1
                # Pad to at least 4 digits
                suggested = str(next_num).zfill(4)
            else:
                # Fallback: start at prefix + 001
                suggested = f"{prefix}001"
        else:
            # No existing accounts with this prefix, start at prefix + 001
            suggested = f"{prefix}001"

        return {
            "suggested_code": suggested,
            "prefix": prefix,
            "ifrs_category": ifrs_category.value,
        }


# Module-level singleton instance
chart_of_accounts_service = ChartOfAccountsService()
