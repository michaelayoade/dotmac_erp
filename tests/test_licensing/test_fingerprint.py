"""Tests for app.licensing.fingerprint — hardware fingerprint."""

from __future__ import annotations

from unittest.mock import mock_open, patch

from app.licensing.fingerprint import (
    _fallback_id,
    _read_machine_id,
    get_machine_fingerprint,
)


class TestMachineFingerprint:
    def setup_method(self) -> None:
        # Reset the cached fingerprint before each test
        import app.licensing.fingerprint as fp_mod

        fp_mod._CACHED_FINGERPRINT = None

    def test_fingerprint_starts_with_sha256(self) -> None:
        fp = get_machine_fingerprint()
        assert fp.startswith("sha256:")
        assert len(fp) == 7 + 64  # "sha256:" + 64 hex chars

    def test_fingerprint_is_cached(self) -> None:
        fp1 = get_machine_fingerprint()
        fp2 = get_machine_fingerprint()
        assert fp1 == fp2

    def test_read_machine_id_missing(self) -> None:
        with patch("builtins.open", side_effect=FileNotFoundError):
            result = _read_machine_id()
        assert result is None

    def test_read_machine_id_present(self) -> None:
        with patch("builtins.open", mock_open(read_data="abc123\n")):
            result = _read_machine_id()
        assert result == "abc123"

    def test_read_machine_id_empty(self) -> None:
        with patch("builtins.open", mock_open(read_data="  \n")):
            result = _read_machine_id()
        assert result is None

    def test_fallback_id_format(self) -> None:
        result = _fallback_id()
        assert ":" in result
