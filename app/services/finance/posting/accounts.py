"""Shared account-resolution helpers for posting services."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.models.finance.banking.bank_account import BankAccount
from app.models.finance.gl.account import Account


def resolve_bank_gl_account_id(
    db: Session,
    organization_id: UUID,
    bank_account_id: UUID,
) -> UUID | None:
    """Resolve a payment bank reference to a GL account ID.

    Supports both legacy storage patterns:
    - direct ``gl.account.account_id``
    - ``banking.bank_accounts.bank_account_id`` mapped through ``gl_account_id``
    """
    gl_account = db.get(Account, bank_account_id)
    if gl_account and gl_account.organization_id == organization_id:
        return bank_account_id

    bank_account = db.get(BankAccount, bank_account_id)
    if (
        bank_account
        and bank_account.organization_id == organization_id
        and bank_account.gl_account_id
    ):
        mapped_gl = db.get(Account, bank_account.gl_account_id)
        if mapped_gl and mapped_gl.organization_id == organization_id:
            return bank_account.gl_account_id

    return None
