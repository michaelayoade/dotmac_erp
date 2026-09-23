"""Secure supplier bank-detail handling.

Supplier account numbers are encrypted before persistence. Normal projections
receive only a masked suffix; decryption is reserved for payment execution.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.services.common import ValidationError, coerce_uuid
from app.services.integration_config import decrypt_credential, encrypt_credential
from app.services.settings.bank_directory import OrgBankDirectoryService

SUPPLIER_BANK_DETAILS_SCHEMA_VERSION = 1
_TRUTHY = frozenset({"1", "true", "yes", "on"})


def build_supplier_bank_details(
    db: Session,
    organization_id: UUID,
    *,
    bank_code: str,
    account_name: str,
    account_number: str,
) -> dict[str, Any]:
    """Validate and encrypt one supplier payment destination."""
    org_id = coerce_uuid(organization_id)
    normalized_code = bank_code.strip()
    normalized_name = account_name.strip()
    normalized_account = account_number.strip()

    if not normalized_code:
        raise ValidationError("Select a bank for the supplier payment destination")
    if not normalized_name:
        raise ValidationError("Enter the supplier bank account name")
    if len(normalized_name) > 255:
        raise ValidationError(
            "Supplier bank account name must be 255 characters or less"
        )
    if len(normalized_account) != 10 or not normalized_account.isdigit():
        raise ValidationError(
            "Supplier bank account number must contain exactly 10 digits"
        )

    bank = OrgBankDirectoryService(db).get_active_bank_by_sort_code(
        org_id,
        normalized_code,
    )
    if bank is None:
        raise ValidationError(
            "Select an active bank from the organization bank directory"
        )

    return {
        "schema_version": SUPPLIER_BANK_DETAILS_SCHEMA_VERSION,
        "bank_code": bank.bank_sort_code,
        "bank_name": bank.bank_name,
        "account_name": normalized_name,
        "account_number_encrypted": encrypt_credential(normalized_account, db),
        "account_number_last4": normalized_account[-4:],
    }


def supplier_bank_details_from_payload(
    db: Session,
    organization_id: UUID,
    payload: Mapping[str, Any],
    *,
    existing_bank_details: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Resolve optional supplier-form bank fields.

    Blank fields keep an existing destination. Removal must be explicit. A bank
    change requires the account number to be re-entered so a bank/account pair
    cannot be changed accidentally.
    """
    existing = dict(existing_bank_details) if existing_bank_details else None
    remove_requested = str(payload.get("remove_bank_details") or "").lower() in _TRUTHY
    bank_code = str(payload.get("bank_code") or "").strip()
    account_name = str(payload.get("bank_account_name") or "").strip()
    account_number = str(payload.get("bank_account_number") or "").strip()

    if remove_requested:
        return None

    if not bank_code and not account_name and not account_number:
        return existing

    if account_number:
        return build_supplier_bank_details(
            db,
            organization_id,
            bank_code=bank_code,
            account_name=account_name,
            account_number=account_number,
        )

    if existing is None:
        raise ValidationError(
            "Enter the bank, account name, and 10-digit account number together"
        )

    existing_code = str(existing.get("bank_code") or "")
    if bank_code and bank_code != existing_code:
        raise ValidationError("Re-enter the account number when changing the bank")

    if not account_name:
        account_name = str(existing.get("account_name") or "")
    if not account_name:
        raise ValidationError("Enter the supplier bank account name")
    if len(account_name) > 255:
        raise ValidationError(
            "Supplier bank account name must be 255 characters or less"
        )

    retained = dict(existing)
    retained["account_name"] = account_name
    return retained


def decrypt_supplier_account_number(
    db: Session,
    bank_details: Mapping[str, Any] | None,
) -> str | None:
    """Return the payment-only account number from encrypted or legacy storage."""
    if not bank_details:
        return None
    encrypted = bank_details.get("account_number_encrypted")
    if encrypted:
        return decrypt_credential(str(encrypted), db)
    legacy = bank_details.get("account_number")
    return str(legacy) if legacy else None
