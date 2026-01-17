"""
ChartOfAccountsService - Account management.

Manages chart of accounts entries including creation, updates, and queries.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.ifrs.gl.account import Account, AccountType, NormalBalance
from app.models.ifrs.gl.account_category import AccountCategory, IFRSCategory
from app.services.common import coerce_uuid
from app.services.response import ListResponseMixin
from app.services.ifrs.common import (
    validate_unique_code,
    get_org_scoped_entity,
    toggle_entity_status,
    apply_search_filter,
)


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
    description: Optional[str] = None
    search_terms: Optional[str] = None
    is_multi_currency: bool = False
    default_currency_code: Optional[str] = None
    is_budgetable: bool = True
    is_reconciliation_required: bool = False
    subledger_type: Optional[str] = None
    is_cash_equivalent: bool = False
    is_financial_instrument: bool = False


class ChartOfAccountsService(ListResponseMixin):
    """
    Service for chart of accounts management.

    Manages account creation, updates, and queries.
    """

    @staticmethod
    def create_account(
        db: Session,
        organization_id: UUID,
        input: AccountInput,
    ) -> Account:
        """
        Create a new account.

        Args:
            db: Database session
            organization_id: Organization scope
            input: Account input data

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
            is_budgetable=input.is_budgetable,
            is_reconciliation_required=input.is_reconciliation_required,
            subledger_type=input.subledger_type,
            is_cash_equivalent=input.is_cash_equivalent,
            is_financial_instrument=input.is_financial_instrument,
        )

        db.add(account)
        db.commit()
        db.refresh(account)

        return account

    @staticmethod
    def update_account(
        db: Session,
        organization_id: UUID,
        account_id: UUID,
        account_name: Optional[str] = None,
        description: Optional[str] = None,
        search_terms: Optional[str] = None,
        is_active: Optional[bool] = None,
        is_posting_allowed: Optional[bool] = None,
        is_budgetable: Optional[bool] = None,
    ) -> Account:
        """
        Update an existing account.

        Args:
            db: Database session
            organization_id: Organization scope
            account_id: Account to update
            Various optional fields to update

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
        organization_id: Optional[str] = None,
        category_id: Optional[str] = None,
        account_type: Optional[AccountType] = None,
        is_active: Optional[bool] = None,
        is_posting_allowed: Optional[bool] = None,
        search: Optional[str] = None,
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
            query = query.filter(Account.organization_id == coerce_uuid(organization_id))

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
            return {"suggested_code": None, "prefix": None, "error": "Category not found"}

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
