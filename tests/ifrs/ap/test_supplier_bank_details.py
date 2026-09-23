"""Regression tests for secure supplier payment destinations."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.models.finance.ap.supplier import Supplier, SupplierType
from app.schemas.finance.ap import SupplierRead
from app.services.common import ValidationError
from app.services.finance.ap.supplier_bank_details import (
    build_supplier_bank_details,
    decrypt_supplier_account_number,
    supplier_bank_details_from_payload,
)


def test_build_supplier_bank_details_uses_org_directory_and_encrypts():
    db = MagicMock()
    org_id = uuid4()
    bank = SimpleNamespace(bank_sort_code="058", bank_name="Guaranty Trust Bank")

    with (
        patch(
            "app.services.finance.ap.supplier_bank_details."
            "OrgBankDirectoryService.get_active_bank_by_sort_code",
            return_value=bank,
        ) as lookup,
        patch(
            "app.services.finance.ap.supplier_bank_details.encrypt_credential",
            return_value="enc:ciphertext",
        ) as encrypt,
    ):
        result = build_supplier_bank_details(
            db,
            org_id,
            bank_code=" 058 ",
            account_name=" Example Supplier Ltd ",
            account_number="0123456789",
        )

    lookup.assert_called_once_with(org_id, "058")
    encrypt.assert_called_once_with("0123456789", db)
    assert result == {
        "schema_version": 1,
        "bank_code": "058",
        "bank_name": "Guaranty Trust Bank",
        "account_name": "Example Supplier Ltd",
        "account_number_encrypted": "enc:ciphertext",
        "account_number_last4": "6789",
    }
    assert "account_number" not in result


@pytest.mark.parametrize("account_number", ["123", "abcdefghij", "12345678901"])
def test_build_supplier_bank_details_rejects_invalid_account_number(account_number):
    with pytest.raises(ValidationError, match="exactly 10 digits"):
        build_supplier_bank_details(
            MagicMock(),
            uuid4(),
            bank_code="058",
            account_name="Example Supplier",
            account_number=account_number,
        )


def test_build_supplier_bank_details_rejects_bank_outside_org_directory():
    with patch(
        "app.services.finance.ap.supplier_bank_details."
        "OrgBankDirectoryService.get_active_bank_by_sort_code",
        return_value=None,
    ):
        with pytest.raises(ValidationError, match="organization bank directory"):
            build_supplier_bank_details(
                MagicMock(),
                uuid4(),
                bank_code="999",
                account_name="Example Supplier",
                account_number="0123456789",
            )


def test_edit_payload_keeps_existing_destination_when_number_is_blank():
    existing = {
        "schema_version": 1,
        "bank_code": "058",
        "bank_name": "Guaranty Trust Bank",
        "account_name": "Old Name",
        "account_number_encrypted": "enc:ciphertext",
        "account_number_last4": "6789",
    }

    result = supplier_bank_details_from_payload(
        MagicMock(),
        uuid4(),
        {
            "bank_code": "058",
            "bank_account_name": "Updated Account Name",
            "bank_account_number": "",
        },
        existing_bank_details=existing,
    )

    assert result["account_name"] == "Updated Account Name"
    assert result["account_number_encrypted"] == "enc:ciphertext"


def test_edit_payload_requires_explicit_removal():
    existing = {
        "bank_code": "058",
        "account_name": "Example Supplier",
        "account_number_encrypted": "enc:ciphertext",
        "account_number_last4": "6789",
    }

    assert (
        supplier_bank_details_from_payload(
            MagicMock(),
            uuid4(),
            {"remove_bank_details": "true"},
            existing_bank_details=existing,
        )
        is None
    )


def test_supplier_read_exposes_only_masked_bank_details():
    supplier = Supplier(
        supplier_id=uuid4(),
        organization_id=uuid4(),
        supplier_code="SUP-001",
        supplier_type=SupplierType.VENDOR,
        legal_name="Example Supplier",
        payment_terms_days=30,
        currency_code="NGN",
        ap_control_account_id=uuid4(),
        bank_details={
            "schema_version": 1,
            "bank_code": "058",
            "bank_name": "Guaranty Trust Bank",
            "account_name": "Example Supplier",
            "account_number_encrypted": "enc:secret-ciphertext",
            "account_number_last4": "6789",
        },
        is_active=True,
        created_at=datetime.now(UTC),
    )

    payload = SupplierRead.model_validate(supplier).model_dump()

    assert payload["bank_details"]["account_number_masked"] == "******6789"
    assert "secret-ciphertext" not in str(payload)


def test_decrypt_supplier_account_number_supports_encrypted_and_legacy_values():
    db = MagicMock()
    with patch(
        "app.services.finance.ap.supplier_bank_details.decrypt_credential",
        return_value="0123456789",
    ) as decrypt:
        assert (
            decrypt_supplier_account_number(
                db,
                {"account_number_encrypted": "enc:ciphertext"},
            )
            == "0123456789"
        )
    decrypt.assert_called_once_with("enc:ciphertext", db)

    assert (
        decrypt_supplier_account_number(
            db,
            {"account_number": "0987654321"},
        )
        == "0987654321"
    )
