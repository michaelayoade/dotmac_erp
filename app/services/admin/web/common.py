from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, TypedDict
from urllib.parse import urlencode
from uuid import UUID

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.audit import AuditActorType
from app.models.domain_settings import DomainSetting, SettingDomain, SettingValueType
from app.models.finance.core_org.organization import Organization
from app.models.person import Person
from app.services.common import coerce_uuid
from app.services.formatters import format_datetime as _format_datetime
from app.templates import templates

if TYPE_CHECKING:
    from app.web.deps import WebAuthContext

logger = logging.getLogger(__name__)

DEFAULT_PAGE_SIZE = 20
DEFAULT_NEW_LOCAL_PASSWORD = "Dotmac@123"  # noqa: S105  # nosec B105
_ORG_SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_UUID_SEGMENT_PATTERN = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
)


class Pagination(TypedDict):
    page: int
    total_pages: int
    total: int
    per_page: int
    start: int
    end: int
    has_prev: bool
    has_next: bool
    pages: list[int | str]


def _truncate(value: str, max_length: int = 120) -> str:
    if len(value) <= max_length:
        return value
    return f"{value[: max_length - 3]}..."


def _safe_json_dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"))


def _build_pagination(
    page: int, total_pages: int, total: int, per_page: int
) -> Pagination:
    start = (page - 1) * per_page + 1 if total > 0 else 0
    end = min(page * per_page, total)

    pages: list[int | str] = []
    if total_pages <= 7:
        pages = list(range(1, total_pages + 1))
    else:
        if page <= 3:
            pages = [1, 2, 3, 4, "...", total_pages]
        elif page >= total_pages - 2:
            pages = [1, "...", total_pages - 3, total_pages - 2, total_pages - 1, total_pages]
        else:
            pages = [1, "...", page - 1, page, page + 1, "...", total_pages]

    return {
        "page": page,
        "total_pages": total_pages,
        "total": total,
        "per_page": per_page,
        "start": start,
        "end": end,
        "has_prev": page > 1,
        "has_next": page < total_pages,
        "pages": pages,
    }


def _parse_status_filter(value: str | None) -> bool | None:
    if value == "active":
        return True
    if value == "inactive":
        return False
    return None


def _parse_domain(value: str | None) -> SettingDomain | None:
    if not value:
        return None
    try:
        return SettingDomain(value)
    except ValueError:
        return None


def _parse_actor_type(value: str | None) -> AuditActorType | None:
    if not value:
        return None
    try:
        return AuditActorType(value)
    except ValueError:
        return None


def _parse_success_filter(value: str | None) -> bool | None:
    if value == "success":
        return True
    if value == "failed":
        return False
    return None


def _parse_flag(value: bool | str | None) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "on", "yes"}


def _parse_person_status(value: str | None):
    from app.models.person import PersonStatus

    if not value:
        return None
    try:
        return PersonStatus(value)
    except ValueError:
        return None


def _clean_name(value: str | None) -> str:
    return (value or "").strip()


def _derive_display_name(first_name: str, last_name: str, display_name: str | None) -> str | None:
    explicit = _clean_name(display_name)
    if explicit:
        return explicit
    full_name = " ".join(part for part in [first_name.strip(), last_name.strip()] if part).strip()
    return full_name or None


def _format_relative_time(value: datetime | None) -> str | None:
    if not value:
        return None
    now = datetime.now(UTC)
    current = value if value.tzinfo else value.replace(tzinfo=UTC)
    delta = now - current
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return "Just now"
    if seconds < 3600:
        minutes = max(1, seconds // 60)
        return f"{minutes}m ago"
    if seconds < 86400:
        hours = max(1, seconds // 3600)
        return f"{hours}h ago"
    days = max(1, seconds // 86400)
    return f"{days}d ago"


def _humanize_token(value: str | None) -> str:
    raw = (value or "").strip()
    if not raw:
        return "-"
    text = raw.replace("_", " ").replace("-", " ").strip()
    if not text:
        return "-"
    return text.title()


def _humanize_actor_type(value: str | None) -> str:
    mapping = {
        "user": "User",
        "system": "System",
        "service": "Service",
        "api_key": "API Key",
    }
    key = (value or "").strip().lower()
    return mapping.get(key, _humanize_token(value))


def _humanize_http_action(action: str | None) -> str:
    raw = (action or "").strip().upper()
    mapping = {
        "GET": "Read",
        "POST": "Create",
        "PUT": "Replace",
        "PATCH": "Update",
        "DELETE": "Delete",
    }
    if raw in mapping:
        return f"{mapping[raw]} ({raw})"
    return _humanize_token(action)


def _humanize_path(path: str | None) -> str:
    raw = (path or "").strip()
    if not raw:
        return "-"
    if not raw.startswith("/"):
        return _humanize_token(raw)
    aliases = {"ap": "AP", "ar": "AR", "gl": "GL", "hr": "HR", "pm": "PM", "api": "API"}
    parts: list[str] = []
    for segment in raw.split("/"):
        seg = segment.strip()
        if not seg:
            continue
        if _UUID_SEGMENT_PATTERN.match(seg) or seg.isdigit():
            parts.append("ID")
            continue
        mapped = aliases.get(seg.lower())
        parts.append(mapped if mapped else _humanize_token(seg))
    return " / ".join(parts) if parts else "-"


def _format_request_summary(
    action: str | None,
    metadata: dict | None,
    request_id: str | None,
) -> str:
    data = metadata if isinstance(metadata, dict) else {}
    path = str(data.get("path") or "").strip()
    query = data.get("query")
    query_text = ""
    if isinstance(query, dict) and query:
        query_text = urlencode(query, doseq=True)
    elif query:
        query_text = str(query).strip()
    method = (action or "").strip().upper()
    method_prefix = f"{method} " if method else ""

    if path:
        summary = f"{method_prefix}{path}"
        if query_text:
            summary = f"{summary}?{query_text}"
        return _truncate(summary, 140)
    if request_id:
        return f"Request {request_id}"
    return "-"


def _setting_value_display(setting: DomainSetting) -> str:
    if setting.is_secret:
        return "Hidden"
    if setting.value_type == SettingValueType.json:
        if setting.value_json is None:
            return ""
        return _truncate(_safe_json_dump(setting.value_json))
    if setting.value_text is None:
        return ""
    return _truncate(str(setting.value_text))


def _format_interval(seconds: int | None) -> str:
    if not seconds:
        return "-"
    if seconds < 60:
        return f"Every {seconds}s"
    if seconds % 86400 == 0:
        days = seconds // 86400
        return f"Every {days} day{'s' if days != 1 else ''}"
    if seconds % 3600 == 0:
        hours = seconds // 3600
        return f"Every {hours} hour{'s' if hours != 1 else ''}"
    if seconds % 60 == 0:
        minutes = seconds // 60
        return f"Every {minutes} minute{'s' if minutes != 1 else ''}"
    return f"Every {seconds}s"


def _resolve_person_name_map(
    db: Session,
    person_ids: list[str],
    organization_id: UUID | None,
) -> dict[str, str]:
    normalized_ids = []
    for person_id in person_ids:
        person_uuid = coerce_uuid(person_id, raise_http=False)
        if person_uuid:
            normalized_ids.append(person_uuid)
    if not normalized_ids:
        return {}

    query = select(Person).where(Person.id.in_(normalized_ids))
    if organization_id:
        query = query.where(Person.organization_id == organization_id)

    names: dict[str, str] = {}
    for person in db.scalars(query).all():
        display = (
            _clean_name(person.display_name)
            or _clean_name(person.name)
            or person.email
        )
        if display:
            names[str(person.id)] = display
    return names


class AdminWebCommonMixin:
    def _resolve_admin_brand_context(
        self,
        request: Request,
        db: Session,
        auth: "WebAuthContext",
    ) -> dict:
        from app.web.deps import resolve_brand_context

        primary_org_id = auth.organization_id
        if primary_org_id:
            organization = db.get(Organization, primary_org_id)
            brand = resolve_brand_context(db, organization, primary_org_id)
        else:
            brand = resolve_brand_context(db, None, None)

        state_org_id_raw = getattr(request.state, "organization_id", None)
        state_org_id = (
            coerce_uuid(state_org_id_raw, raise_http=False)
            if state_org_id_raw
            else None
        )
        if state_org_id and state_org_id != primary_org_id:
            organization = db.get(Organization, state_org_id)
            fallback_brand = resolve_brand_context(db, organization, state_org_id)
            if not brand.get("logo_url") or not brand.get("favicon_url"):
                brand = fallback_brand
        return brand

    def _request_path_with_query(self, request: Request) -> str:
        if request.url.query:
            return f"{request.url.path}?{request.url.query}"
        return request.url.path

    def _admin_login_redirect(self, next_path: str) -> RedirectResponse:
        return RedirectResponse(
            url=f"/admin/login?{urlencode({'next': next_path})}",
            status_code=302,
        )

    def _require_admin_web_auth(
        self,
        request: Request,
        auth: "WebAuthContext",
    ) -> "WebAuthContext" | RedirectResponse:
        if not auth.is_authenticated:
            return self._admin_login_redirect(self._request_path_with_query(request))
        if "admin" not in auth.roles:
            raise HTTPException(status_code=403, detail="Admin access required")
        return auth

    def _render_admin_template(
        self,
        request: Request,
        db: Session,
        template_name: str,
        auth: "WebAuthContext",
        title: str,
        page_title: str,
        active_page: str,
        context: dict | None = None,
        status_code: int | None = None,
    ) -> HTMLResponse:
        if status_code is None:
            status_code = 200
        csrf_token = getattr(request.state, "csrf_token", "")
        csrf_form_val = getattr(request.state, "csrf_form", None)
        if not isinstance(csrf_form_val, str):
            request.state.csrf_form = (
                f'<input type="hidden" name="csrf_token" value="{csrf_token}">'
                if csrf_token
                else ""
            )
        payload = {
            "title": title,
            "page_title": page_title,
            "brand": self._resolve_admin_brand_context(request, db, auth),
            "user": auth.user,
            "active_page": active_page,
            "csrf_token": csrf_token,
            **(context or {}),
        }
        return templates.TemplateResponse(
            request,
            template_name,
            payload,
            status_code=status_code,
        )
