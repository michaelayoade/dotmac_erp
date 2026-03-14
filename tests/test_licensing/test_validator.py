"""Tests for app.licensing.validator — Ed25519 signature verification."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from app.licensing.schema import LicenseFile
from app.licensing.validator import _SEPARATOR, load_license_file, verify_signature


@pytest.fixture()
def ed25519_keypair():
    """Generate a fresh Ed25519 keypair for testing."""
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    pub_b64 = base64.b64encode(
        public_key.public_bytes(Encoding.Raw, PublicFormat.Raw)
    ).decode()
    return private_key, public_key, pub_b64


@pytest.fixture()
def sample_payload_dict() -> dict:
    return {
        "version": 1,
        "license_id": "lic-test-001",
        "customer_name": "Test Corp",
        "customer_id": "cust-001",
        "issued_at": "2026-01-01T00:00:00Z",
        "expires_at": "2027-01-01T00:00:00Z",
        "grace_period_days": 30,
        "modules": ["finance", "people"],
        "max_organizations": 5,
        "max_users": 100,
    }


def _create_lic_file(
    payload_dict: dict,
    private_key: Ed25519PrivateKey,
    tmp_path: Path,
) -> Path:
    """Helper: create a signed .lic file."""
    payload_bytes = json.dumps(payload_dict).encode("utf-8")
    signature = private_key.sign(payload_bytes)
    payload_b64 = base64.b64encode(payload_bytes).decode()
    sig_b64 = base64.b64encode(signature).decode()
    lic_path = tmp_path / "test.lic"
    lic_path.write_text(f"{payload_b64}{_SEPARATOR}{sig_b64}", encoding="utf-8")
    return lic_path


class TestLoadLicenseFile:
    def test_valid_file(self, ed25519_keypair, sample_payload_dict, tmp_path) -> None:
        private_key, _, _ = ed25519_keypair
        lic_path = _create_lic_file(sample_payload_dict, private_key, tmp_path)
        lic = load_license_file(lic_path)
        assert lic.payload.license_id == "lic-test-001"
        assert lic.payload.modules == ["finance", "people"]
        assert isinstance(lic.signature, bytes)
        assert len(lic.signature) == 64  # Ed25519 signature size

    def test_missing_separator(self, tmp_path) -> None:
        path = tmp_path / "bad.lic"
        path.write_text("just some text without separator")
        with pytest.raises(ValueError, match="separator"):
            load_license_file(path)

    def test_invalid_base64(self, tmp_path) -> None:
        path = tmp_path / "bad.lic"
        path.write_text(f"not-valid-base64!!!{_SEPARATOR}also-bad!!!")
        with pytest.raises(Exception):
            load_license_file(path)


class TestVerifySignature:
    def test_valid_signature(
        self, ed25519_keypair, sample_payload_dict, tmp_path
    ) -> None:
        private_key, _, pub_b64 = ed25519_keypair
        lic_path = _create_lic_file(sample_payload_dict, private_key, tmp_path)

        with patch("app.licensing.validator._PUBLIC_KEY_B64", pub_b64):
            lic = load_license_file(lic_path)
            assert verify_signature(lic) is True

    def test_tampered_payload(
        self, ed25519_keypair, sample_payload_dict, tmp_path
    ) -> None:
        private_key, _, pub_b64 = ed25519_keypair
        lic_path = _create_lic_file(sample_payload_dict, private_key, tmp_path)

        with patch("app.licensing.validator._PUBLIC_KEY_B64", pub_b64):
            lic = load_license_file(lic_path)
            # Tamper with the payload bytes
            tampered = LicenseFile(
                payload=lic.payload,
                payload_bytes=b"tampered data",
                signature=lic.signature,
            )
            assert verify_signature(tampered) is False

    def test_wrong_public_key(
        self, ed25519_keypair, sample_payload_dict, tmp_path
    ) -> None:
        private_key, _, _ = ed25519_keypair
        lic_path = _create_lic_file(sample_payload_dict, private_key, tmp_path)

        # Use a different keypair's public key
        other_private = Ed25519PrivateKey.generate()
        other_pub = other_private.public_key()
        other_pub_b64 = base64.b64encode(
            other_pub.public_bytes(Encoding.Raw, PublicFormat.Raw)
        ).decode()

        with patch("app.licensing.validator._PUBLIC_KEY_B64", other_pub_b64):
            lic = load_license_file(lic_path)
            assert verify_signature(lic) is False

    def test_invalid_public_key_constant(
        self, ed25519_keypair, sample_payload_dict, tmp_path
    ) -> None:
        private_key, _, _ = ed25519_keypair
        lic_path = _create_lic_file(sample_payload_dict, private_key, tmp_path)

        with patch("app.licensing.validator._PUBLIC_KEY_B64", "dGVzdA=="):  # "test"
            lic = load_license_file(lic_path)
            # Should return False, not crash
            assert verify_signature(lic) is False
