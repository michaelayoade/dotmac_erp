"""Tests for app.licensing.enforcement — startup + runtime enforcement."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from app.licensing.enforcement import (
    _is_dev_mode,
    enforce_startup,
    get_licensed_modules,
    is_in_grace_period,
    is_within_org_limit,
    is_within_user_limit,
    validate_license,
)
from app.licensing.schema import LicenseStatus
from app.licensing.state import LicenseState, set_license_state
from app.licensing.validator import _SEPARATOR


def _make_keypair():
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    pub_b64 = base64.b64encode(
        public_key.public_bytes(Encoding.Raw, PublicFormat.Raw)
    ).decode()
    return private_key, pub_b64


def _create_signed_lic(payload_dict: dict, private_key, tmp_path: Path) -> Path:
    payload_bytes = json.dumps(payload_dict, default=str).encode("utf-8")
    signature = private_key.sign(payload_bytes)
    payload_b64 = base64.b64encode(payload_bytes).decode()
    sig_b64 = base64.b64encode(signature).decode()
    lic_path = tmp_path / "dotmac.lic"
    lic_path.write_text(f"{payload_b64}{_SEPARATOR}{sig_b64}", encoding="utf-8")
    return lic_path


class TestDevMode:
    def test_dev_mode_true(self) -> None:
        with patch.dict("os.environ", {"DOTMAC_DEV_MODE": "true"}):
            assert _is_dev_mode() is True

    def test_dev_mode_false(self) -> None:
        with patch.dict("os.environ", {"DOTMAC_DEV_MODE": "false"}):
            assert _is_dev_mode() is False

    def test_dev_mode_default_is_true(self) -> None:
        with patch.dict("os.environ", {}, clear=False):
            # Default in the code is "true"
            assert _is_dev_mode() is True


class TestValidateLicense:
    def test_missing_file(self, tmp_path) -> None:
        state = validate_license(str(tmp_path / "nonexistent.lic"))
        assert state.status == LicenseStatus.MISSING

    def test_valid_license(self, tmp_path) -> None:
        private_key, pub_b64 = _make_keypair()
        payload = {
            "version": 1,
            "license_id": "lic-test",
            "customer_name": "Test",
            "customer_id": "cust-001",
            "issued_at": datetime(2026, 1, 1, tzinfo=UTC).isoformat(),
            "expires_at": (datetime.now(tz=UTC) + timedelta(days=365)).isoformat(),
            "modules": ["finance"],
            "max_users": 50,
            "max_organizations": 5,
        }
        lic_path = _create_signed_lic(payload, private_key, tmp_path)

        with patch("app.licensing.validator._PUBLIC_KEY_B64", pub_b64):
            state = validate_license(str(lic_path))
        assert state.status == LicenseStatus.VALID
        assert state.payload is not None
        assert state.payload.license_id == "lic-test"

    def test_expired_within_grace(self, tmp_path) -> None:
        private_key, pub_b64 = _make_keypair()
        payload = {
            "version": 1,
            "license_id": "lic-grace",
            "customer_name": "Test",
            "customer_id": "cust-001",
            "issued_at": datetime(2025, 1, 1, tzinfo=UTC).isoformat(),
            "expires_at": (datetime.now(tz=UTC) - timedelta(days=5)).isoformat(),
            "grace_period_days": 30,
            "modules": ["finance"],
        }
        lic_path = _create_signed_lic(payload, private_key, tmp_path)

        with patch("app.licensing.validator._PUBLIC_KEY_B64", pub_b64):
            state = validate_license(str(lic_path))
        assert state.status == LicenseStatus.GRACE_PERIOD

    def test_expired_past_grace(self, tmp_path) -> None:
        private_key, pub_b64 = _make_keypair()
        payload = {
            "version": 1,
            "license_id": "lic-dead",
            "customer_name": "Test",
            "customer_id": "cust-001",
            "issued_at": datetime(2024, 1, 1, tzinfo=UTC).isoformat(),
            "expires_at": (datetime.now(tz=UTC) - timedelta(days=60)).isoformat(),
            "grace_period_days": 30,
            "modules": ["finance"],
        }
        lic_path = _create_signed_lic(payload, private_key, tmp_path)

        with patch("app.licensing.validator._PUBLIC_KEY_B64", pub_b64):
            state = validate_license(str(lic_path))
        assert state.status == LicenseStatus.EXPIRED

    def test_expiring_soon(self, tmp_path) -> None:
        private_key, pub_b64 = _make_keypair()
        payload = {
            "version": 1,
            "license_id": "lic-soon",
            "customer_name": "Test",
            "customer_id": "cust-001",
            "issued_at": datetime(2025, 1, 1, tzinfo=UTC).isoformat(),
            "expires_at": (datetime.now(tz=UTC) + timedelta(days=15)).isoformat(),
            "modules": ["finance"],
        }
        lic_path = _create_signed_lic(payload, private_key, tmp_path)

        with patch("app.licensing.validator._PUBLIC_KEY_B64", pub_b64):
            state = validate_license(str(lic_path))
        assert state.status == LicenseStatus.EXPIRING_SOON

    def test_invalid_signature(self, tmp_path) -> None:
        private_key, _ = _make_keypair()
        _, wrong_pub_b64 = _make_keypair()  # Different key
        payload = {
            "version": 1,
            "license_id": "lic-bad",
            "customer_name": "Test",
            "customer_id": "cust-001",
            "issued_at": datetime(2026, 1, 1, tzinfo=UTC).isoformat(),
            "expires_at": (datetime.now(tz=UTC) + timedelta(days=365)).isoformat(),
            "modules": ["finance"],
        }
        lic_path = _create_signed_lic(payload, private_key, tmp_path)

        with patch("app.licensing.validator._PUBLIC_KEY_B64", wrong_pub_b64):
            state = validate_license(str(lic_path))
        assert state.status == LicenseStatus.INVALID

    def test_hardware_fingerprint_mismatch(self, tmp_path) -> None:
        private_key, pub_b64 = _make_keypair()
        payload = {
            "version": 1,
            "license_id": "lic-hw",
            "customer_name": "Test",
            "customer_id": "cust-001",
            "issued_at": datetime(2026, 1, 1, tzinfo=UTC).isoformat(),
            "expires_at": (datetime.now(tz=UTC) + timedelta(days=365)).isoformat(),
            "modules": ["finance"],
            "hardware_fingerprint": "sha256:wrong",
            "hardware_fingerprint_required": True,
        }
        lic_path = _create_signed_lic(payload, private_key, tmp_path)

        with patch("app.licensing.validator._PUBLIC_KEY_B64", pub_b64):
            state = validate_license(str(lic_path))
        assert state.status == LicenseStatus.INVALID
        assert "fingerprint mismatch" in (state.error or "")


class TestEnforceStartup:
    def test_dev_mode_skips(self) -> None:
        with patch.dict("os.environ", {"DOTMAC_DEV_MODE": "true"}):
            state = enforce_startup()
        assert state.status == LicenseStatus.DEV_MODE

    def test_missing_license_exits(self, tmp_path) -> None:
        with (
            patch.dict(
                "os.environ",
                {
                    "DOTMAC_DEV_MODE": "false",
                    "LICENSE_FILE_PATH": str(tmp_path / "nope.lic"),
                },
            ),
            pytest.raises(SystemExit),
        ):
            enforce_startup()


class TestRuntimeHelpers:
    def test_get_licensed_modules_dev_mode(self) -> None:
        set_license_state(LicenseState(status=LicenseStatus.DEV_MODE))
        assert get_licensed_modules() is None

    def test_get_licensed_modules_with_payload(self) -> None:
        from app.licensing.schema import LicensePayload

        payload = LicensePayload(
            license_id="lic-001",
            customer_name="Test",
            customer_id="cust-001",
            issued_at=datetime(2026, 1, 1, tzinfo=UTC),
            expires_at=datetime(2027, 1, 1, tzinfo=UTC),
            modules=["finance", "people"],
        )
        set_license_state(LicenseState(status=LicenseStatus.VALID, payload=payload))
        assert get_licensed_modules() == ["finance", "people"]

    def test_is_within_user_limit(self) -> None:
        from app.licensing.schema import LicensePayload

        payload = LicensePayload(
            license_id="lic-001",
            customer_name="Test",
            customer_id="cust-001",
            issued_at=datetime(2026, 1, 1, tzinfo=UTC),
            expires_at=datetime(2027, 1, 1, tzinfo=UTC),
            max_users=10,
        )
        set_license_state(LicenseState(status=LicenseStatus.VALID, payload=payload))
        assert is_within_user_limit(5) is True
        assert is_within_user_limit(10) is True
        assert is_within_user_limit(11) is False

    def test_is_within_org_limit(self) -> None:
        from app.licensing.schema import LicensePayload

        payload = LicensePayload(
            license_id="lic-001",
            customer_name="Test",
            customer_id="cust-001",
            issued_at=datetime(2026, 1, 1, tzinfo=UTC),
            expires_at=datetime(2027, 1, 1, tzinfo=UTC),
            max_organizations=3,
        )
        set_license_state(LicenseState(status=LicenseStatus.VALID, payload=payload))
        assert is_within_org_limit(2) is True
        assert is_within_org_limit(3) is True
        assert is_within_org_limit(4) is False

    def test_is_in_grace_period(self) -> None:
        set_license_state(LicenseState(status=LicenseStatus.GRACE_PERIOD))
        assert is_in_grace_period() is True
        set_license_state(LicenseState(status=LicenseStatus.VALID))
        assert is_in_grace_period() is False
