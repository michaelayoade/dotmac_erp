"""Validation helpers for Accounts Payable control accounts."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance.gl.account import Account
from app.models.finance.gl.account_category import AccountCategory, IFRSCategory
from app.services.common import ValidationError, coerce_uuid


def require_ap_control_account(
    db: Session,
    organization_id: UUID,
    account_id: UUID | None,
    *,
    field_name: str = "Default payable account",
) -> UUID:
    """Return a valid tenant-scoped AP liability account or reject the input."""
    if account_id is None:
        raise ValidationError(f"{field_name} is required.")

    org_id = coerce_uuid(organization_id)
    resolved_account_id = coerce_uuid(account_id)
    account = db.execute(
        select(Account)
        .join(AccountCategory, Account.category_id == AccountCategory.category_id)
        .where(
            Account.account_id == resolved_account_id,
            Account.organization_id == org_id,
            Account.is_active.is_(True),
            Account.subledger_type == "AP",
            AccountCategory.organization_id == org_id,
            AccountCategory.ifrs_category == IFRSCategory.LIABILITIES,
        )
    ).scalar_one_or_none()
    if account is None:
        raise ValidationError(
            f"{field_name} must be an active AP liability account for this "
            "organization."
        )
    return resolved_account_id
