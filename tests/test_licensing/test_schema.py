"""Tests for app.licensing.schema — Pydantic license models."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.licensing.schema import LicenseFile, LicensePayload, LicenseStatus


class TestLicenseStatus:
    def test_all_values(self) -> None:
        assert LicenseStatus.VALID == "VALID"
        assert LicenseStatus.EXPIRED == "EXPIRED"
        assert LicenseStatus.GRACE_PERIOD == "GRACE_PERIOD"
        assert LicenseStatus.MISSING == "MISSING"
        assert LicenseStatus.DEV_MODE == "DEV_MODE"
        assert LicenseStatus.INVALID == "INVALID"
        assert LicenseStatus.EXPIRING_SOON == "EXPIRING_SOON"


class TestLicensePayload:
    def test_minimal_payload(self) -> None:
        payload = LicensePayload(
            license_id="lic-001",
            customer_name="Test Corp",
            customer_id="cust-001",
            issued_at=datetime(2026, 1, 1, tzinfo=UTC),
            expires_at=datetime(2027, 1, 1, tzinfo=UTC),
        )
        assert payload.version == 1
        assert payload.grace_period_days == 30
        assert payload.modules == []
        assert payload.max_users == 50
        assert payload.max_organizations == 1
        assert payload.hardware_fingerprint is None
        assert payload.hardware_fingerprint_required is False
        assert payload.features == {}

    def test_full_payload(self) -> None:
        payload = LicensePayload(
            version=1,
            license_id="lic-2026-0001",
            customer_name="Acme Corp",
            customer_id="cust-001",
            issued_at=datetime(2026, 3, 14, tzinfo=UTC),
            expires_at=datetime(2027, 3, 14, tzinfo=UTC),
            grace_period_days=15,
            modules=["finance", "people", "expense"],
            max_organizations=5,
            max_users=100,
            hardware_fingerprint="sha256:abc123",
            hardware_fingerprint_required=True,
            features={"sso_enabled": True, "api_access": True},
        )
        assert payload.max_users == 100
        assert payload.modules == ["finance", "people", "expense"]
        assert payload.features["sso_enabled"] is True

    def test_from_dict(self) -> None:
        data = {
            "license_id": "lic-001",
            "customer_name": "Test",
            "customer_id": "cust-001",
            "issued_at": "2026-01-01T00:00:00Z",
            "expires_at": "2027-01-01T00:00:00Z",
            "modules": ["finance"],
        }
        payload = LicensePayload.model_validate(data)
        assert payload.license_id == "lic-001"
        assert payload.modules == ["finance"]

    def test_missing_required_field(self) -> None:
        with pytest.raises(Exception):
            LicensePayload(
                customer_name="Test",
                customer_id="cust-001",
                issued_at=datetime(2026, 1, 1, tzinfo=UTC),
                expires_at=datetime(2027, 1, 1, tzinfo=UTC),
            )  # type: ignore[call-arg]  # missing license_id


class TestLicenseFile:
    def test_creation(self) -> None:
        payload = LicensePayload(
            license_id="lic-001",
            customer_name="Test",
            customer_id="cust-001",
            issued_at=datetime(2026, 1, 1, tzinfo=UTC),
            expires_at=datetime(2027, 1, 1, tzinfo=UTC),
        )
        lf = LicenseFile(
            payload=payload,
            payload_bytes=b"test",
            signature=b"sig",
        )
        assert lf.payload.license_id == "lic-001"
        assert lf.payload_bytes == b"test"
        assert lf.signature == b"sig"
