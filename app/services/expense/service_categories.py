"""Expense category operations."""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, or_, select

from app.models.expense import ExpenseCategory
from app.services.common import PaginatedResult, PaginationParams
from app.services.expense.service_common import (
    ExpenseCategoryNotFoundError,
    ExpenseServiceBase,
)


class ExpenseCategoryMixin(ExpenseServiceBase):
    def list_categories(
        self,
        org_id: UUID,
        *,
        is_active: bool | None = None,
        search: str | None = None,
        pagination: PaginationParams | None = None,
    ) -> PaginatedResult[ExpenseCategory]:
        query = select(ExpenseCategory).where(ExpenseCategory.organization_id == org_id)

        if is_active is not None:
            query = query.where(ExpenseCategory.is_active == is_active)

        if search:
            search_term = f"%{search}%"
            query = query.where(
                or_(
                    ExpenseCategory.category_code.ilike(search_term),
                    ExpenseCategory.category_name.ilike(search_term),
                )
            )

        query = query.order_by(ExpenseCategory.category_name)
        total = self.db.scalar(select(func.count()).select_from(query.subquery())) or 0

        if pagination:
            query = query.offset(pagination.offset).limit(pagination.limit)

        items = list(self.db.scalars(query).all())
        return PaginatedResult(
            items=items,
            total=total,
            offset=pagination.offset if pagination else 0,
            limit=pagination.limit if pagination else len(items),
        )

    def get_category(self, org_id: UUID, category_id: UUID) -> ExpenseCategory:
        category = self.db.scalar(
            select(ExpenseCategory).where(
                ExpenseCategory.category_id == category_id,
                ExpenseCategory.organization_id == org_id,
            )
        )
        if not category:
            raise ExpenseCategoryNotFoundError(category_id)
        return category

    def create_category(
        self,
        org_id: UUID,
        *,
        category_code: str,
        category_name: str,
        expense_account_id: UUID | None = None,
        max_amount_per_claim: Decimal | None = None,
        requires_receipt: bool = True,
        is_active: bool = True,
        description: str | None = None,
    ) -> ExpenseCategory:
        category = ExpenseCategory(
            organization_id=org_id,
            category_code=category_code,
            category_name=category_name,
            expense_account_id=expense_account_id,
            max_amount_per_claim=max_amount_per_claim,
            requires_receipt=requires_receipt,
            is_active=is_active,
            description=description,
        )
        self.db.add(category)
        self.db.flush()
        return category

    def update_category(
        self,
        org_id: UUID,
        category_id: UUID,
        **kwargs,
    ) -> ExpenseCategory:
        category = self.get_category(org_id, category_id)
        for key, value in kwargs.items():
            if value is not None and hasattr(category, key):
                setattr(category, key, value)
        self.db.flush()
        return category

    def delete_category(self, org_id: UUID, category_id: UUID) -> ExpenseCategory:
        category = self.get_category(org_id, category_id)
        category.is_active = False
        self.db.flush()
        return category
