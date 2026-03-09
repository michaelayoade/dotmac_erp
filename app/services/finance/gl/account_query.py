"""
Shared GL account query builder for list + export.
"""

from __future__ import annotations

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.models.finance.gl.account import Account
from app.models.finance.gl.account_category import AccountCategory
from app.services.common import coerce_uuid
from app.services.finance.gl.web.base import parse_category


def build_account_query(
    db: Session,
    organization_id: str,
    search: str | None = None,
    category: str | None = None,
    status: str | None = None,
) -> Select:
    """
    Build the base GL account query with filters applied.
    """
    org_id = coerce_uuid(organization_id)

    is_active = None
    if status == "active":
        is_active = True
    elif status == "inactive":
        is_active = False

    category_value = parse_category(category)

    query = (
        select(Account)
        .join(AccountCategory, Account.category_id == AccountCategory.category_id)
        .where(Account.organization_id == org_id)
    )

    if is_active is not None:
        query = query.where(Account.is_active == is_active)
    if category_value:
        query = query.where(AccountCategory.ifrs_category == category_value)
    if search:
        search_pattern = f"%{search}%"
        query = query.where(
            (Account.account_code.ilike(search_pattern))
            | (Account.account_name.ilike(search_pattern))
            | (Account.search_terms.ilike(search_pattern))
        )

    return query
