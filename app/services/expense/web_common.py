"""Shared helpers for expense web services."""

from __future__ import annotations

import json
import mimetypes
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.services.file_upload import get_expense_receipt_upload, resolve_safe_path

if TYPE_CHECKING:
    from app.models.people.hr.employee import Employee


class ExpenseWebCommonMixin:
    """Small helper methods reused across expense web service mixins."""

    @staticmethod
    def get_employee_for_person(
        db: Session, organization_id: UUID, person_id: UUID
    ) -> Employee | None:
        """Look up the Employee record linked to a person within an org."""
        from app.models.people.hr.employee import Employee

        return db.scalar(
            select(Employee).where(
                Employee.organization_id == organization_id,
                Employee.person_id == person_id,
            )
        )

    _UNSAFE_FILENAME_RE = re.compile(r'[\x00-\x1f\x7f"\\]')

    @staticmethod
    def _form_str(form: Any, key: str) -> str:
        value = form.get(key)
        if value is None:
            return ""
        return str(value).strip()

    @staticmethod
    def _resolve_claim_receipt_path(receipt_url: str) -> Path:
        upload_base = get_expense_receipt_upload().base_path
        raw = receipt_url.strip()
        candidate = Path(raw)

        if candidate.is_absolute():
            resolved = candidate.resolve(strict=True)
            if resolved != upload_base and upload_base not in resolved.parents:
                raise ValueError("Receipt path is outside configured upload directory")
            return resolved

        return resolve_safe_path(upload_base, raw).resolve(strict=True)

    @staticmethod
    def _parse_receipt_urls(receipt_url: str | None) -> list[str]:
        if not receipt_url:
            return []
        raw = receipt_url.strip()
        if not raw:
            return []
        if raw.startswith("["):
            try:
                decoded = json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                return [raw]
            if isinstance(decoded, list):
                return [str(entry).strip() for entry in decoded if str(entry).strip()]
        return [raw]

    @staticmethod
    def _guess_media_type(filename: str) -> str:
        return mimetypes.guess_type(filename)[0] or "application/octet-stream"

    @staticmethod
    def _is_remote_receipt(receipt_url: str) -> bool:
        parsed = urlparse(receipt_url)
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
