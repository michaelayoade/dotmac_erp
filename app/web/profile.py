"""
Web routes for user profile pages.

Provides profile view and edit functionality for authenticated users.
"""

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile

from app.schemas.person import PersonUpdate
from app.services.person import people
from app.services.profile_web import profile_web_service
from app.web.deps import WebAuthContext, get_db, require_web_auth

router = APIRouter(tags=["web-profile"])


def _get_form_str(form: Any, key: str, default: str = "") -> str:
    value = form.get(key, default) if form is not None else default
    if isinstance(value, UploadFile) or value is None:
        return default
    return str(value).strip()


@router.get("/account/two-factor", response_class=HTMLResponse)
def two_factor_page(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """
    Display the two-factor authentication setup page.
    """
    return profile_web_service.two_factor_response(request, auth, db)


@router.get("/account/sessions", response_class=HTMLResponse)
def sessions_page(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """
    Display the sessions management page.
    """
    return profile_web_service.sessions_response(request, auth, db)


@router.get("/change-password", response_class=HTMLResponse)
def change_password_page(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """
    Display the change password page.
    """
    return profile_web_service.change_password_response(request, auth, db)


@router.get("/profile", response_class=HTMLResponse)
def profile_page(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """
    Display the user profile page.
    """
    return profile_web_service.profile_response(request, auth, db)


def _parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _clean_name(value: str | None) -> str:
    cleaned = (value or "").strip()
    return "" if cleaned.lower() in {"none", "null"} else cleaned


def _derive_display_name(
    first_name: str | None, last_name: str | None, display_name: str | None
) -> str | None:
    display = _clean_name(display_name)
    if display:
        return display
    base_name = f"{_clean_name(first_name)} {_clean_name(last_name)}".strip()
    return base_name or None


@router.post("/profile")
async def update_profile(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    form = getattr(request.state, "csrf_form", None)
    if form is None:
        form = await request.form()

    first_name = _clean_name(_get_form_str(form, "first_name"))
    last_name = _clean_name(_get_form_str(form, "last_name"))
    display_name = _clean_name(_get_form_str(form, "display_name"))
    phone = _get_form_str(form, "phone")
    bio = _get_form_str(form, "bio")

    payload = PersonUpdate(
        first_name=first_name or None,
        last_name=last_name or None,
        display_name=_derive_display_name(first_name, last_name, display_name),
        phone=phone or None,
        bio=bio or None,
    )

    people.update(db, str(auth.person_id), payload)
    return RedirectResponse(url="/profile?updated=1", status_code=303)


@router.post("/profile/preferences")
async def update_profile_preferences(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    form = getattr(request.state, "csrf_form", None)
    if form is None:
        form = await request.form()

    timezone = _get_form_str(form, "timezone") or None
    locale = _get_form_str(form, "locale") or None
    preferred_contact_method = _get_form_str(form, "preferred_contact_method") or None
    if preferred_contact_method not in {"email", "phone", "sms", "push", None}:
        preferred_contact_method = None
    marketing_opt_in = _parse_bool(
        _get_form_str(form, "marketing_opt_in") or None, False
    )

    payload = PersonUpdate(
        timezone=timezone,
        locale=locale,
        preferred_contact_method=preferred_contact_method,
        marketing_opt_in=marketing_opt_in,
    )

    people.update(db, str(auth.person_id), payload)
    return RedirectResponse(url="/profile?updated=1", status_code=303)
