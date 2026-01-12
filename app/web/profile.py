"""
Web routes for user profile pages.

Provides profile view and edit functionality for authenticated users.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.models.auth import Session as AuthSession, SessionStatus, UserCredential, MFAMethod, MFAMethodType
from app.models.person import Person
from app.services.auth_flow import decode_access_token
from app.services.common import coerce_uuid
from app.templates import templates
from app.web.deps import (
    base_context,
    brand_context,
    get_db,
    require_web_auth,
    WebAuthContext,
)


router = APIRouter(tags=["web-profile"])


@router.get("/account/two-factor", response_class=HTMLResponse)
def two_factor_page(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """
    Display the two-factor authentication setup page.
    """
    # Get existing MFA methods for this user
    mfa_methods = (
        db.query(MFAMethod)
        .filter(MFAMethod.person_id == auth.person_id)
        .order_by(MFAMethod.created_at.desc())
        .all()
    )

    # Check if user has an enabled TOTP method
    has_totp = any(
        m.method_type == MFAMethodType.totp and m.enabled
        for m in mfa_methods
    )

    # Get primary method if any
    primary_method = next(
        (m for m in mfa_methods if m.is_primary and m.enabled),
        None
    )

    context = base_context(request, auth, "Two-Factor Authentication", "settings")
    context.update({
        "mfa_methods": mfa_methods,
        "has_totp": has_totp,
        "primary_method": primary_method,
        "person_id": str(auth.person_id),
    })

    return templates.TemplateResponse(request, "ifrs/two_factor.html", context)


@router.get("/account/sessions", response_class=HTMLResponse)
def sessions_page(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """
    Display the sessions management page.
    """
    now = datetime.now(timezone.utc)

    # Get all active sessions
    sessions = (
        db.query(AuthSession)
        .filter(AuthSession.person_id == auth.person_id)
        .filter(AuthSession.status == SessionStatus.active)
        .filter(AuthSession.revoked_at.is_(None))
        .filter(AuthSession.expires_at > now)
        .order_by(AuthSession.last_seen_at.desc().nullsfirst())
        .all()
    )

    # Get current session ID from token
    access_token = request.cookies.get("access_token")
    current_session_id = None
    if access_token:
        try:
            payload = decode_access_token(db, access_token)
            current_session_id = payload.get("session_id")
        except Exception:
            pass

    context = base_context(request, auth, "Sessions", "settings")
    context.update({
        "sessions": sessions,
        "current_session_id": current_session_id,
        "session_count": len(sessions),
    })

    return templates.TemplateResponse(request, "ifrs/sessions.html", context)


@router.get("/change-password", response_class=HTMLResponse)
def change_password_page(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """
    Display the change password page.
    """
    # Get credential info
    credential = (
        db.query(UserCredential)
        .filter(UserCredential.person_id == auth.person_id)
        .filter(UserCredential.is_active.is_(True))
        .first()
    )

    context = base_context(request, auth, "Change Password", "settings")
    context.update({
        "credential": credential,
        "must_change": credential.must_change_password if credential else False,
    })

    return templates.TemplateResponse(request, "ifrs/change_password.html", context)


@router.get("/profile", response_class=HTMLResponse)
def profile_page(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """
    Display the user profile page.
    """
    # Get full person record
    person = db.get(Person, auth.person_id)
    if not person:
        return RedirectResponse(url="/logout", status_code=302)

    # Get credential info (for password last changed)
    credential = (
        db.query(UserCredential)
        .filter(UserCredential.person_id == auth.person_id)
        .filter(UserCredential.is_active.is_(True))
        .first()
    )

    # Get active sessions count
    now = datetime.now(timezone.utc)
    active_sessions = (
        db.query(AuthSession)
        .filter(AuthSession.person_id == auth.person_id)
        .filter(AuthSession.status == SessionStatus.active)
        .filter(AuthSession.revoked_at.is_(None))
        .filter(AuthSession.expires_at > now)
        .count()
    )

    # Get current session
    current_session = (
        db.query(AuthSession)
        .filter(AuthSession.person_id == auth.person_id)
        .filter(AuthSession.status == SessionStatus.active)
        .filter(AuthSession.revoked_at.is_(None))
        .filter(AuthSession.expires_at > now)
        .order_by(AuthSession.last_seen_at.desc())
        .first()
    )

    # Check if 2FA is enabled
    has_2fa = (
        db.query(MFAMethod)
        .filter(MFAMethod.person_id == auth.person_id)
        .filter(MFAMethod.enabled.is_(True))
        .filter(MFAMethod.method_type == MFAMethodType.totp)
        .first()
    ) is not None

    context = base_context(request, auth, "Profile", "settings")
    context.update({
        "person": person,
        "credential": credential,
        "active_sessions": active_sessions,
        "current_session": current_session,
        "roles": auth.roles,
        "has_2fa": has_2fa,
    })

    return templates.TemplateResponse(request, "ifrs/profile.html", context)
