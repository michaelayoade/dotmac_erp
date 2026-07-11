"""Shared helpers for parsing web form values."""

from __future__ import annotations

from typing import Any

from fastapi import UploadFile


def safe_form_text(
    value: object | None,
    default: str = "",
    *,
    strip: bool = False,
) -> str:
    """Convert a submitted form value to text while ignoring file inputs."""
    if value is None or isinstance(value, UploadFile):
        return default
    if isinstance(value, str):
        return value.strip() if strip else value
    text = str(value)
    return text.strip() if strip else text


def get_form_str(
    form: Any,
    key: str,
    default: str = "",
    *,
    strip: bool = True,
) -> str:
    """Read a string value from a form-like object."""
    value = form.get(key, default) if form is not None else default
    return safe_form_text(value, default, strip=strip)


def normalize_form(form: Any) -> dict[str, str]:
    """Return a string-only form mapping, dropping file inputs."""
    if form is None:
        return {}
    return {key: value if isinstance(value, str) else "" for key, value in form.items()}
