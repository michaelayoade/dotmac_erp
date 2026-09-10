import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.sync.sub_operational import (
    SubExpenseClaimPayload,
    SubExpenseDestinationVerifyPayload,
    SubExpenseDestinationVerifyResponse,
)
from app.services.sync.sub import expenses


def test_override_requires_bank_account_and_beneficiary() -> None:
    with pytest.raises(ValidationError):
        SubExpenseDestinationVerifyPayload(
            requested_by_email="tech@example.com",
            source_claim_id=uuid4(),
            mode="expense_override",
            bank_code="058",
            account_number="0123456789",
        )


def test_profile_mode_rejects_replacement_details() -> None:
    with pytest.raises(ValidationError):
        SubExpenseDestinationVerifyPayload(
            requested_by_email="tech@example.com",
            source_claim_id=uuid4(),
            mode="erp_profile",
            account_number="0123456789",
        )


def test_masked_verification_response_does_not_expose_account_number() -> None:
    now = datetime.now(UTC)
    response = SubExpenseDestinationVerifyResponse(
        destination_token="enc:" + "x" * 32,
        mode="expense_override",
        bank_code="058",
        bank_name="Guaranty Trust Bank",
        masked_account_number="******6789",
        verified_beneficiary_name="Field Technician",
        verified_at=now,
        expires_at=now + timedelta(minutes=30),
    )
    body = response.model_dump(mode="json")
    assert body["masked_account_number"] == "******6789"
    assert "account_number" not in body


def test_claim_contract_keeps_new_fields_optional_for_rolling_deployments() -> None:
    payload = SubExpenseClaimPayload(
        source_claim_id=str(uuid4()),
        purpose="Site visit",
        claim_date="2026-09-10",
        requested_by_email="tech@example.com",
        items=[
            {
                "category_code": "FUEL",
                "description": "Fuel",
                "claimed_amount": "1000.00",
            }
        ],
    )
    assert payload.requested_approver_id is None
    assert payload.payment_destination_token is None


def test_destination_decoder_preserves_expiry_for_server_side_inspection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    organization_id = uuid4()
    employee_id = uuid4()
    source_claim_id = str(uuid4())
    expires_at = datetime.now(UTC) + timedelta(minutes=30)
    token_payload = {
        "version": 1,
        "organization_id": str(organization_id),
        "employee_id": str(employee_id),
        "source_claim_id": source_claim_id,
        "mode": "expense_override",
        "bank_code": "058",
        "bank_name": "Guaranty Trust Bank",
        "account_number": "0123456789",
        "verified_beneficiary_name": "Field Technician",
        "verified_at": datetime.now(UTC).isoformat(),
        "expires_at": expires_at.isoformat(),
    }
    monkeypatch.setattr(
        expenses,
        "decrypt_credential",
        lambda _token, _db: json.dumps(token_payload),
    )
    service = expenses._ExpenseSyncMixin(None)  # type: ignore[arg-type]

    decoded = service._decode_expense_destination(
        org_id=organization_id,
        employee_id=employee_id,
        source_claim_id=source_claim_id,
        token="enc:opaque",
    )

    assert decoded["expires_at"] == expires_at.isoformat()
