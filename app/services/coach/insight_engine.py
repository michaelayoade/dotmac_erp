from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, TypeVar, cast

import httpx
from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from app.config import settings as app_settings
from app.services.cache import cache_service

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    pass


@dataclass(frozen=True)
class LLMBackend:
    name: str
    base_url: str
    api_key: str
    model_fast: str
    model_standard: str
    model_deep: str

    def is_configured(self) -> bool:
        return bool(self.base_url and self.api_key)


def _csv(value: str) -> list[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def _extract_json_object(text: str) -> dict[str, Any]:
    """
    Extract the first JSON object from a string.

    Many hosted LLMs occasionally wrap JSON in markdown fences; this is a
    defensive extraction step before strict validation.
    """
    text = (text or "").strip()
    if not text:
        raise LLMError("Empty LLM response")

    # Strip common code fence wrappers
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"\s*```$", "", text).strip()

    # Fast path: plain JSON
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    # Fallback: find the first {...} block
    m = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not m:
        raise LLMError("No JSON object found in LLM response")
    try:
        parsed = json.loads(m.group(0))
    except json.JSONDecodeError as exc:
        raise LLMError("Invalid JSON in LLM response") from exc
    if not isinstance(parsed, dict):
        raise LLMError("Expected JSON object")
    return parsed


class InsightEngine:
    """
    Provider-agnostic LLM wrapper (hosted Llama + DeepSeek via OpenAI-compatible API).

    This module is intentionally standalone: analyzers should provide deterministic
    context dicts and Pydantic output schemas; InsightEngine handles the LLM call
    + strict JSON validation + basic repair retries.

    When ``db`` is provided, configuration is read from domain settings first
    (admin-configurable via ``/admin/settings``), falling back to environment
    variables via ``app.config.settings``.
    """

    def __init__(self, db: Session | None = None) -> None:
        self._db = db
        self._timeout_s = int(
            self._setting("timeout_seconds", "coach_llm_timeout_s") or 30
        )
        self._max_retries = int(
            self._setting("max_retries", "coach_llm_max_retries") or 2
        )
        self._max_output_tokens = int(
            getattr(app_settings, "coach_llm_max_output_tokens", 1200)
        )
        self._cache_ttl_s = (
            int(getattr(app_settings, "coach_cache_ttl_hours", 24)) * 3600
        )

        self._backends = self._load_backends()

    def _setting(self, key: str, fallback_attr: str) -> str:
        """Read from DB domain settings first, fall back to env var config."""
        if self._db is not None:
            from app.models.domain_settings import SettingDomain
            from app.services.settings_spec import resolve_value

            val = resolve_value(self._db, SettingDomain.coach, key)
            if val:
                return str(val)
        return str(getattr(app_settings, fallback_attr, "") or "")

    def _cache_key(
        self,
        *,
        backend: str,
        model: str,
        tier: str,
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        digest = sha256(
            (system_prompt.strip() + "\n" + user_prompt.strip()).encode("utf-8")
        ).hexdigest()
        return f"coach:llm:{backend}:{model}:{tier}:{digest}"

    def _load_backends(self) -> dict[str, LLMBackend]:
        return {
            "llama": LLMBackend(
                name="llama",
                base_url=self._setting("llama_base_url", "coach_llm_llama_base_url"),
                api_key=self._setting("llama_api_key", "coach_llm_llama_api_key"),
                model_fast=self._setting(
                    "llama_model_fast", "coach_llm_llama_model_fast"
                ),
                model_standard=self._setting(
                    "llama_model_standard", "coach_llm_llama_model_standard"
                ),
                model_deep=self._setting(
                    "llama_model_deep", "coach_llm_llama_model_deep"
                ),
            ),
            "deepseek": LLMBackend(
                name="deepseek",
                base_url=self._setting(
                    "deepseek_base_url", "coach_llm_deepseek_base_url"
                ),
                api_key=self._setting("deepseek_api_key", "coach_llm_deepseek_api_key"),
                model_fast=self._setting(
                    "deepseek_model_fast", "coach_llm_deepseek_model_fast"
                ),
                model_standard=self._setting(
                    "deepseek_model_standard", "coach_llm_deepseek_model_standard"
                ),
                model_deep=self._setting(
                    "deepseek_model_deep", "coach_llm_deepseek_model_deep"
                ),
            ),
        }

    def _backend_order(self, preferred: str) -> list[str]:
        backends_csv = getattr(app_settings, "coach_llm_backends", "llama,deepseek")
        allowed = _csv(backends_csv)
        if not allowed:
            allowed = ["llama", "deepseek"]
        ordered = [preferred] + [b for b in allowed if b != preferred]
        return [b for b in ordered if b in self._backends]

    def _model_for_tier(self, backend: LLMBackend, tier: str) -> str:
        if tier == "fast":
            return backend.model_fast or backend.model_standard or backend.model_deep
        if tier == "deep":
            return backend.model_deep or backend.model_standard or backend.model_fast
        return backend.model_standard or backend.model_fast or backend.model_deep

    def generate_structured(
        self,
        *,
        tier: str,
        system_prompt: str,
        user_prompt: str,
        output_model: type[T],
        preferred_backend: str | None = None,
        temperature: float = 0.2,
    ) -> T:
        """
        Generate schema-valid JSON output.

        Notes:
        - Uses basic JSON-only prompting for broad compatibility.
        - Validates against a Pydantic model.
        - Retries are reserved for repair attempts (not endless sampling).
        """
        default_backend = getattr(app_settings, "coach_llm_default_backend", "deepseek")
        preferred = preferred_backend or default_backend

        last_error: Exception | None = None
        for backend_name in self._backend_order(preferred):
            backend = self._backends[backend_name]
            if not backend.is_configured():
                continue

            model = self._model_for_tier(backend, tier)
            if not model:
                continue

            try:
                cache_key = self._cache_key(
                    backend=backend.name,
                    model=model,
                    tier=tier,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                )
                if cache_service.is_available:
                    cached = cache_service.get(cache_key)
                    if isinstance(cached, dict):
                        try:
                            return cast(T, output_model.model_validate(cached))
                        except ValidationError:
                            cache_service.delete(cache_key)

                return self._call_with_repairs(
                    backend=backend,
                    model=model,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    output_model=output_model,
                    temperature=temperature,
                    tier=tier,
                )
            except Exception as exc:
                last_error = exc
                logger.warning("LLM backend %s failed: %s", backend.name, exc)
                continue

        raise LLMError("All LLM backends failed") from last_error

    def _call_with_repairs(
        self,
        *,
        backend: LLMBackend,
        model: str,
        system_prompt: str,
        user_prompt: str,
        output_model: type[T],
        temperature: float,
        tier: str,
    ) -> T:
        raw = self._call_chat_completions(
            backend=backend,
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=temperature,
        )

        try:
            data = _extract_json_object(raw)
            parsed = cast(T, output_model.model_validate(data))
            if cache_service.is_available:
                cache_key = self._cache_key(
                    backend=backend.name,
                    model=model,
                    tier=tier,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                )
                cache_service.set(
                    cache_key,
                    parsed.model_dump(mode="json"),
                    ttl_seconds=self._cache_ttl_s,
                )
            return parsed
        except (LLMError, ValidationError) as exc:
            last_exc: Exception = exc
            repair_prompt = self._repair_prompt(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                bad_output=raw,
                error=str(exc),
            )
            for _ in range(self._max_retries):
                raw = self._call_chat_completions(
                    backend=backend,
                    model=model,
                    system_prompt=system_prompt,
                    user_prompt=repair_prompt,
                    temperature=0.0,
                )
                try:
                    data = _extract_json_object(raw)
                    parsed = cast(T, output_model.model_validate(data))
                    if cache_service.is_available:
                        cache_key = self._cache_key(
                            backend=backend.name,
                            model=model,
                            tier=tier,
                            system_prompt=system_prompt,
                            user_prompt=user_prompt,
                        )
                        cache_service.set(
                            cache_key,
                            parsed.model_dump(mode="json"),
                            ttl_seconds=self._cache_ttl_s,
                        )
                    return parsed
                except (LLMError, ValidationError) as exc2:
                    last_exc = exc2
                    continue
            raise LLMError(
                "Invalid structured output after repair retries"
            ) from last_exc

    def _repair_prompt(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        bad_output: str,
        error: str,
    ) -> str:
        return (
            "You must output ONLY a single JSON object.\n\n"
            "The previous output was invalid.\n"
            f"Validation error: {error}\n\n"
            "Original instructions:\n"
            f"{system_prompt}\n\n"
            "Original user prompt:\n"
            f"{user_prompt}\n\n"
            "Bad output:\n"
            f"{bad_output}\n\n"
            "Now respond with corrected JSON only."
        )

    def _call_chat_completions(
        self,
        *,
        backend: LLMBackend,
        model: str,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
    ) -> str:
        url = backend.base_url.rstrip("/") + "/chat/completions"
        headers = {"Authorization": f"Bearer {backend.api_key}"}
        payload: dict[str, Any] = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": system_prompt.strip()
                    + "\n\nReturn ONLY valid JSON. Do not include markdown fences.",
                },
                {"role": "user", "content": user_prompt.strip()},
            ],
            "temperature": temperature,
            "max_tokens": self._max_output_tokens,
        }

        with httpx.Client(timeout=self._timeout_s) as client:
            resp = client.post(url, headers=headers, json=payload)
        if resp.status_code >= 400:
            raise LLMError(f"LLM HTTP {resp.status_code}: {resp.text[:500]}")
        data = resp.json()
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError("Unexpected LLM response shape") from exc
        return str(content or "")
