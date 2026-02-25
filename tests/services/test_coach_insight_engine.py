"""Tests for InsightEngine: JSON extraction, backend config, LLM calls, retries, caching."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from pydantic import BaseModel

from app.services.coach.insight_engine import (
    InsightEngine,
    LLMBackend,
    LLMError,
    _csv,
    _extract_json_object,
)

# ── _extract_json_object ─────────────────────────────────────────────────────


class TestExtractJsonObject:
    def test_plain_json(self) -> None:
        result = _extract_json_object('{"key": "value", "n": 42}')
        assert result == {"key": "value", "n": 42}

    def test_markdown_fenced_json(self) -> None:
        text = '```json\n{"key": "value"}\n```'
        assert _extract_json_object(text) == {"key": "value"}

    def test_markdown_fenced_no_language(self) -> None:
        text = '```\n{"key": "value"}\n```'
        assert _extract_json_object(text) == {"key": "value"}

    def test_json_with_leading_text(self) -> None:
        text = 'Here is the result:\n{"severity": "WARNING"}'
        assert _extract_json_object(text) == {"severity": "WARNING"}

    def test_empty_string_raises(self) -> None:
        with pytest.raises(LLMError, match="Empty LLM response"):
            _extract_json_object("")

    def test_none_raises(self) -> None:
        with pytest.raises(LLMError, match="Empty LLM response"):
            _extract_json_object(None)  # type: ignore[arg-type]

    def test_no_json_raises(self) -> None:
        with pytest.raises(LLMError, match="No JSON object found"):
            _extract_json_object("This is just plain text.")

    def test_invalid_json_raises(self) -> None:
        with pytest.raises(LLMError, match="Invalid JSON"):
            _extract_json_object("{broken: json}")

    def test_array_raises(self) -> None:
        with pytest.raises(LLMError, match="No JSON object found"):
            _extract_json_object("[1, 2, 3]")

    def test_nested_json(self) -> None:
        text = '{"outer": {"inner": [1, 2]}, "ok": true}'
        result = _extract_json_object(text)
        assert result["outer"] == {"inner": [1, 2]}
        assert result["ok"] is True

    def test_whitespace_padding(self) -> None:
        text = '   \n\n  {"a": 1}  \n  '
        assert _extract_json_object(text) == {"a": 1}

    def test_case_insensitive_fence(self) -> None:
        text = '```JSON\n{"x": 1}\n```'
        assert _extract_json_object(text) == {"x": 1}


# ── _csv ──────────────────────────────────────────────────────────────────────


class TestCsv:
    def test_basic(self) -> None:
        assert _csv("a, b, c") == ["a", "b", "c"]

    def test_empty(self) -> None:
        assert _csv("") == []

    def test_none(self) -> None:
        assert _csv(None) == []  # type: ignore[arg-type]

    def test_whitespace_items(self) -> None:
        assert _csv("  x  ,  , y ") == ["x", "y"]


# ── LLMBackend ────────────────────────────────────────────────────────────────


class TestLLMBackend:
    def test_is_configured_both_present(self) -> None:
        b = LLMBackend(
            name="test",
            base_url="https://api.example.com",
            api_key="sk-test",
            model_fast="m-fast",
            model_standard="m-std",
            model_deep="m-deep",
        )
        assert b.is_configured() is True

    def test_not_configured_missing_url(self) -> None:
        b = LLMBackend(
            name="test",
            base_url="",
            api_key="sk-test",
            model_fast="m",
            model_standard="m",
            model_deep="m",
        )
        assert b.is_configured() is False

    def test_not_configured_missing_key(self) -> None:
        b = LLMBackend(
            name="test",
            base_url="https://api.example.com",
            api_key="",
            model_fast="m",
            model_standard="m",
            model_deep="m",
        )
        assert b.is_configured() is False

    def test_frozen(self) -> None:
        b = LLMBackend(
            name="test",
            base_url="url",
            api_key="key",
            model_fast="f",
            model_standard="s",
            model_deep="d",
        )
        with pytest.raises(AttributeError):
            b.name = "changed"  # type: ignore[misc]


# ── InsightEngine internals ───────────────────────────────────────────────────


class _SimpleOutput(BaseModel):
    severity: str
    summary: str


def _make_engine(**overrides: str) -> InsightEngine:
    """Create an engine with mocked settings (no DB, no env vars)."""
    defaults = {
        "timeout_seconds": "30",
        "max_retries": "2",
        "deepseek_base_url": "https://api.deepseek.test/v1",
        "deepseek_api_key": "sk-test-deepseek",
        "deepseek_model_fast": "ds-fast",
        "deepseek_model_standard": "ds-standard",
        "deepseek_model_deep": "ds-deep",
        "llama_base_url": "",
        "llama_api_key": "",
        "llama_model_fast": "",
        "llama_model_standard": "",
        "llama_model_deep": "",
    }
    defaults.update(overrides)

    def fake_setting(key: str, fallback_attr: str) -> str:
        return defaults.get(key, "")

    engine = InsightEngine.__new__(InsightEngine)
    engine._db = None
    engine._setting = fake_setting  # type: ignore[method-assign]
    engine._timeout_s = int(defaults["timeout_seconds"])
    engine._max_retries = int(defaults["max_retries"])
    engine._max_output_tokens = 1200
    engine._cache_ttl_s = 86400
    engine._backends = engine._load_backends()
    return engine


class TestInsightEngineModelTier:
    def test_fast_tier_returns_fast_model(self) -> None:
        engine = _make_engine()
        backend = engine._backends["deepseek"]
        assert engine._model_for_tier(backend, "fast") == "ds-fast"

    def test_standard_tier_returns_standard_model(self) -> None:
        engine = _make_engine()
        backend = engine._backends["deepseek"]
        assert engine._model_for_tier(backend, "standard") == "ds-standard"

    def test_deep_tier_returns_deep_model(self) -> None:
        engine = _make_engine()
        backend = engine._backends["deepseek"]
        assert engine._model_for_tier(backend, "deep") == "ds-deep"

    def test_fast_tier_falls_back_to_standard(self) -> None:
        engine = _make_engine(deepseek_model_fast="")
        backend = engine._backends["deepseek"]
        assert engine._model_for_tier(backend, "fast") == "ds-standard"

    def test_deep_tier_falls_back_to_standard(self) -> None:
        engine = _make_engine(deepseek_model_deep="")
        backend = engine._backends["deepseek"]
        assert engine._model_for_tier(backend, "deep") == "ds-standard"


class TestInsightEngineBackendOrder:
    def test_default_order_deepseek_first(self) -> None:
        engine = _make_engine()
        with patch.object(
            type(engine),
            "_backend_order",
            wraps=engine._backend_order,
        ):
            order = engine._backend_order("deepseek")
            assert order[0] == "deepseek"

    def test_preferred_backend_comes_first(self) -> None:
        engine = _make_engine(
            llama_base_url="https://llama.test",
            llama_api_key="sk-llama",
            llama_model_fast="llama-f",
            llama_model_standard="llama-s",
            llama_model_deep="llama-d",
        )
        order = engine._backend_order("llama")
        assert order[0] == "llama"


class TestInsightEngineGenerateStructured:
    def _fake_response(self, content: str) -> MagicMock:
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"choices": [{"message": {"content": content}}]}
        return resp

    @patch("app.services.coach.insight_engine.cache_service")
    @patch("app.services.coach.insight_engine.httpx.Client")
    def test_happy_path(
        self, mock_client_cls: MagicMock, mock_cache: MagicMock
    ) -> None:
        mock_cache.is_available = False
        engine = _make_engine(max_retries="0")

        response_json = json.dumps({"severity": "WARNING", "summary": "Cash low"})
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = self._fake_response(response_json)
        mock_client_cls.return_value = mock_client

        result = engine.generate_structured(
            tier="fast",
            system_prompt="You are an analyst.",
            user_prompt="Analyze cash flow.",
            output_model=_SimpleOutput,
        )
        assert result.severity == "WARNING"
        assert result.summary == "Cash low"

    @patch("app.services.coach.insight_engine.cache_service")
    @patch("app.services.coach.insight_engine.httpx.Client")
    def test_all_backends_fail_raises(
        self, mock_client_cls: MagicMock, mock_cache: MagicMock
    ) -> None:
        mock_cache.is_available = False
        engine = _make_engine(
            deepseek_base_url="",
            deepseek_api_key="",
            llama_base_url="",
            llama_api_key="",
        )

        with pytest.raises(LLMError, match="All LLM backends failed"):
            engine.generate_structured(
                tier="standard",
                system_prompt="test",
                user_prompt="test",
                output_model=_SimpleOutput,
            )

    @patch("app.services.coach.insight_engine.cache_service")
    @patch("app.services.coach.insight_engine.httpx.Client")
    def test_repair_retry_on_bad_json(
        self, mock_client_cls: MagicMock, mock_cache: MagicMock
    ) -> None:
        mock_cache.is_available = False
        engine = _make_engine(max_retries="1")

        bad_response = "not json at all"
        good_response = json.dumps({"severity": "INFO", "summary": "All clear"})

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.side_effect = [
            self._fake_response(bad_response),
            self._fake_response(good_response),
        ]
        mock_client_cls.return_value = mock_client

        result = engine.generate_structured(
            tier="standard",
            system_prompt="Analyze.",
            user_prompt="Data here.",
            output_model=_SimpleOutput,
        )
        assert result.severity == "INFO"
        assert mock_client.post.call_count == 2

    @patch("app.services.coach.insight_engine.cache_service")
    @patch("app.services.coach.insight_engine.httpx.Client")
    def test_cache_hit_skips_http(
        self, mock_client_cls: MagicMock, mock_cache: MagicMock
    ) -> None:
        mock_cache.is_available = True
        mock_cache.get.return_value = {"severity": "ATTENTION", "summary": "cached"}

        engine = _make_engine()

        result = engine.generate_structured(
            tier="fast",
            system_prompt="test",
            user_prompt="test",
            output_model=_SimpleOutput,
        )
        assert result.severity == "ATTENTION"
        assert result.summary == "cached"
        mock_client_cls.assert_not_called()

    @patch("app.services.coach.insight_engine.cache_service")
    @patch("app.services.coach.insight_engine.httpx.Client")
    def test_http_error_falls_back_to_next_backend(
        self, mock_client_cls: MagicMock, mock_cache: MagicMock
    ) -> None:
        mock_cache.is_available = False

        # Both backends configured
        engine = _make_engine(
            llama_base_url="https://llama.test",
            llama_api_key="sk-llama",
            llama_model_fast="llama-f",
            llama_model_standard="llama-s",
            llama_model_deep="llama-d",
        )

        error_resp = MagicMock()
        error_resp.status_code = 500
        error_resp.text = "Internal server error"

        good_resp = self._fake_response(
            json.dumps({"severity": "INFO", "summary": "ok"})
        )

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.side_effect = [error_resp, good_resp]
        mock_client_cls.return_value = mock_client

        result = engine.generate_structured(
            tier="fast",
            system_prompt="test",
            user_prompt="test",
            output_model=_SimpleOutput,
            preferred_backend="deepseek",
        )
        assert result.severity == "INFO"

    @patch("app.services.coach.insight_engine.cache_service")
    @patch("app.services.coach.insight_engine.httpx.Client")
    def test_validation_error_triggers_repair(
        self, mock_client_cls: MagicMock, mock_cache: MagicMock
    ) -> None:
        """Pydantic validation failure (missing field) triggers repair retry."""
        mock_cache.is_available = False
        engine = _make_engine(max_retries="1")

        # First response missing 'summary' field
        bad = json.dumps({"severity": "WARNING"})
        good = json.dumps({"severity": "WARNING", "summary": "Fixed"})

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.side_effect = [
            self._fake_response(bad),
            self._fake_response(good),
        ]
        mock_client_cls.return_value = mock_client

        result = engine.generate_structured(
            tier="standard",
            system_prompt="test",
            user_prompt="test",
            output_model=_SimpleOutput,
        )
        assert result.summary == "Fixed"


class TestInsightEngineRepairPrompt:
    def test_repair_prompt_contains_error(self) -> None:
        engine = _make_engine()
        prompt = engine._repair_prompt(
            system_prompt="sys",
            user_prompt="usr",
            bad_output="garbage",
            error="missing field",
        )
        assert "missing field" in prompt
        assert "garbage" in prompt
        assert "corrected JSON only" in prompt
