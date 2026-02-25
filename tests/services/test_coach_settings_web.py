"""Tests for Coach/AI settings web service: context building, update flow."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

from app.models.domain_settings import (
    SettingDomain,  # noqa: F811
)
from app.services.finance.settings_web import SettingsWebService

# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_sws() -> SettingsWebService:
    """Construct a SettingsWebService without requiring a real DB."""
    sws = SettingsWebService.__new__(SettingsWebService)
    return sws


# ── get_coach_settings_context ───────────────────────────────────────────────


class TestGetCoachSettingsContext:
    def test_returns_all_spec_keys(self) -> None:
        """Context dict includes every coach SettingSpec key."""
        sws = _make_sws()
        db = MagicMock()

        with patch(
            "app.services.finance.settings_web.resolve_value", return_value=None
        ):
            result = sws.get_coach_settings_context(db, uuid.uuid4())

        ctx = result["coach_settings"]
        expected_keys = {
            "deepseek_base_url",
            "deepseek_api_key",
            "deepseek_model_fast",
            "deepseek_model_standard",
            "deepseek_model_deep",
            "llama_base_url",
            "llama_api_key",
            "llama_model_fast",
            "llama_model_standard",
            "llama_model_deep",
            "timeout_seconds",
            "max_retries",
        }
        assert set(ctx.keys()) == expected_keys

    def test_secret_fields_masked(self) -> None:
        """Secret fields return empty string for value but has_value=True."""
        sws = _make_sws()
        db = MagicMock()

        with patch(
            "app.services.finance.settings_web.resolve_value",
            return_value="sk-real-key-123",
        ):
            result = sws.get_coach_settings_context(db, uuid.uuid4())

        ctx = result["coach_settings"]
        # API keys are secret
        assert ctx["deepseek_api_key"]["value"] == ""
        assert ctx["deepseek_api_key"]["has_value"] is True
        assert ctx["deepseek_api_key"]["is_secret"] is True
        assert ctx["llama_api_key"]["value"] == ""
        assert ctx["llama_api_key"]["has_value"] is True

    def test_non_secret_fields_returned(self) -> None:
        """Non-secret fields return the actual resolved value."""
        sws = _make_sws()
        db = MagicMock()

        with patch(
            "app.services.finance.settings_web.resolve_value",
            return_value="https://api.deepseek.com/v1",
        ):
            result = sws.get_coach_settings_context(db, uuid.uuid4())

        ctx = result["coach_settings"]
        assert ctx["deepseek_base_url"]["value"] == "https://api.deepseek.com/v1"
        assert ctx["deepseek_base_url"]["is_secret"] is False

    def test_none_value_has_value_false(self) -> None:
        """When resolve_value returns None, has_value is False."""
        sws = _make_sws()
        db = MagicMock()

        with patch(
            "app.services.finance.settings_web.resolve_value", return_value=None
        ):
            result = sws.get_coach_settings_context(db, uuid.uuid4())

        ctx = result["coach_settings"]
        assert ctx["deepseek_base_url"]["has_value"] is False

    def test_empty_string_has_value_false(self) -> None:
        """When resolve_value returns empty string, has_value is False."""
        sws = _make_sws()
        db = MagicMock()

        with patch("app.services.finance.settings_web.resolve_value", return_value=""):
            result = sws.get_coach_settings_context(db, uuid.uuid4())

        ctx = result["coach_settings"]
        assert ctx["timeout_seconds"]["has_value"] is False

    def test_each_entry_has_required_fields(self) -> None:
        """Every context entry has value, default, type, is_secret, has_value, label, etc."""
        sws = _make_sws()
        db = MagicMock()

        with patch(
            "app.services.finance.settings_web.resolve_value", return_value=None
        ):
            result = sws.get_coach_settings_context(db, uuid.uuid4())

        required = {
            "value",
            "default",
            "type",
            "is_secret",
            "has_value",
            "label",
            "description",
            "min",
            "max",
        }
        for key, entry in result["coach_settings"].items():
            assert required.issubset(set(entry.keys())), (
                f"Missing fields in {key}: {required - set(entry.keys())}"
            )

    def test_integer_specs_have_min_max(self) -> None:
        """timeout_seconds and max_retries have min/max values set."""
        sws = _make_sws()
        db = MagicMock()

        with patch(
            "app.services.finance.settings_web.resolve_value", return_value=None
        ):
            result = sws.get_coach_settings_context(db, uuid.uuid4())

        ctx = result["coach_settings"]
        # timeout_seconds: 5-120
        assert ctx["timeout_seconds"]["min"] == 5
        assert ctx["timeout_seconds"]["max"] == 120
        # max_retries: 0-5
        assert ctx["max_retries"]["min"] == 0
        assert ctx["max_retries"]["max"] == 5


# ── update_coach_settings ────────────────────────────────────────────────────


class TestUpdateCoachSettings:
    def test_happy_path_updates_values(self) -> None:
        """Valid data updates settings and returns (True, None)."""
        from app.models.domain_settings import SettingValueType

        sws = _make_sws()
        db = MagicMock()

        mock_service = MagicMock()
        with (
            patch(
                "app.services.finance.settings_web.DOMAIN_SETTINGS_SERVICE",
                {SettingDomain.coach: mock_service},
            ),
            patch(
                "app.services.finance.settings_web.get_spec",
            ) as mock_get_spec,
        ):
            # Set up a non-secret text spec
            spec = MagicMock()
            spec.is_secret = False
            spec.value_type = SettingValueType.string
            spec.label = "Base URL"
            mock_get_spec.return_value = spec

            with patch(
                "app.services.settings_spec.coerce_value",
                return_value=("https://api.deepseek.com/v1", None),
            ):
                ok, err = sws.update_coach_settings(
                    db,
                    uuid.uuid4(),
                    {"deepseek_base_url": "https://api.deepseek.com/v1"},
                )

        assert ok is True
        assert err is None
        mock_service.upsert_by_key.assert_called_once()
        db.flush.assert_called_once()

    def test_blank_secret_skipped(self) -> None:
        """Empty string for a secret field is skipped (preserves existing)."""
        sws = _make_sws()
        db = MagicMock()

        mock_service = MagicMock()
        with (
            patch(
                "app.services.finance.settings_web.DOMAIN_SETTINGS_SERVICE",
                {SettingDomain.coach: mock_service},
            ),
            patch(
                "app.services.finance.settings_web.get_spec",
            ) as mock_get_spec,
        ):
            spec = MagicMock()
            spec.is_secret = True
            spec.key = "deepseek_api_key"
            mock_get_spec.return_value = spec

            ok, err = sws.update_coach_settings(
                db, uuid.uuid4(), {"deepseek_api_key": ""}
            )

        assert ok is True
        assert err is None
        # upsert should NOT be called for blank secret
        mock_service.upsert_by_key.assert_not_called()

    def test_non_blank_secret_updated(self) -> None:
        """Non-empty secret field IS upserted."""
        from app.models.domain_settings import SettingValueType

        sws = _make_sws()
        db = MagicMock()

        mock_service = MagicMock()
        with (
            patch(
                "app.services.finance.settings_web.DOMAIN_SETTINGS_SERVICE",
                {SettingDomain.coach: mock_service},
            ),
            patch(
                "app.services.finance.settings_web.get_spec",
            ) as mock_get_spec,
        ):
            spec = MagicMock()
            spec.is_secret = True
            spec.value_type = SettingValueType.string
            spec.label = "API Key"
            mock_get_spec.return_value = spec

            with patch(
                "app.services.settings_spec.coerce_value",
                return_value=("sk-new-key", None),
            ):
                ok, err = sws.update_coach_settings(
                    db, uuid.uuid4(), {"deepseek_api_key": "sk-new-key"}
                )

        assert ok is True
        mock_service.upsert_by_key.assert_called_once()

    def test_unknown_key_skipped(self) -> None:
        """Keys not in the spec registry are silently ignored."""
        sws = _make_sws()
        db = MagicMock()

        mock_service = MagicMock()
        with (
            patch(
                "app.services.finance.settings_web.DOMAIN_SETTINGS_SERVICE",
                {SettingDomain.coach: mock_service},
            ),
            patch("app.services.finance.settings_web.get_spec", return_value=None),
        ):
            ok, err = sws.update_coach_settings(
                db, uuid.uuid4(), {"csrf_token": "abc123", "unknown_key": "x"}
            )

        assert ok is True
        assert err is None
        mock_service.upsert_by_key.assert_not_called()

    def test_coerce_error_returns_failure(self) -> None:
        """Coercion error returns (False, error_message)."""
        sws = _make_sws()
        db = MagicMock()

        mock_service = MagicMock()
        with (
            patch(
                "app.services.finance.settings_web.DOMAIN_SETTINGS_SERVICE",
                {SettingDomain.coach: mock_service},
            ),
            patch(
                "app.services.finance.settings_web.get_spec",
            ) as mock_get_spec,
        ):
            spec = MagicMock()
            spec.is_secret = False
            spec.value_type = MagicMock()
            spec.label = "Request Timeout"
            mock_get_spec.return_value = spec

            with patch(
                "app.services.settings_spec.coerce_value",
                return_value=(None, "Value must be an integer"),
            ):
                ok, err = sws.update_coach_settings(
                    db, uuid.uuid4(), {"timeout_seconds": "not_a_number"}
                )

        assert ok is False
        assert "Request Timeout" in err  # type: ignore[operator]
        assert "integer" in err  # type: ignore[operator]
        # Should NOT commit on error
        db.commit.assert_not_called()

    def test_missing_service_returns_failure(self) -> None:
        """If coach domain service not found, returns error."""
        sws = _make_sws()
        db = MagicMock()

        with patch("app.services.finance.settings_web.DOMAIN_SETTINGS_SERVICE", {}):
            ok, err = sws.update_coach_settings(
                db, uuid.uuid4(), {"deepseek_base_url": "http://example.com"}
            )

        assert ok is False
        assert "not found" in err  # type: ignore[operator]
