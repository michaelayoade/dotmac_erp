"""
Profile web view service.

Provides response builders for profile-related web routes.
"""

import logging
from datetime import UTC, datetime

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.models.auth import MFAMethod, MFAMethodType, SessionStatus, UserCredential
from app.models.auth import Session as AuthSession
from app.models.person import Person
from app.services.auth_flow import decode_access_token
from app.templates import templates
from app.web.deps import WebAuthContext, base_context

logger = logging.getLogger(__name__)


class ProfileWebService:
    """View service for profile web routes."""

    def two_factor_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        mfa_methods = (
            db.query(MFAMethod)
            .filter(MFAMethod.person_id == auth.person_id)
            .order_by(MFAMethod.created_at.desc())
            .all()
        )

        has_totp = any(
            method.method_type == MFAMethodType.totp and method.enabled
            for method in mfa_methods
        )

        primary_method = next(
            (method for method in mfa_methods if method.is_primary and method.enabled),
            None,
        )

        context = base_context(request, auth, "Two-Factor Authentication", "settings")
        context.update(
            {
                "mfa_methods": mfa_methods,
                "has_totp": has_totp,
                "primary_method": primary_method,
                "person_id": str(auth.person_id),
            }
        )

        return templates.TemplateResponse(request, "finance/two_factor.html", context)

    def sessions_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        now = datetime.now(UTC)

        sessions = (
            db.query(AuthSession)
            .filter(AuthSession.person_id == auth.person_id)
            .filter(AuthSession.status == SessionStatus.active)
            .filter(AuthSession.revoked_at.is_(None))
            .filter(AuthSession.expires_at > now)
            .order_by(AuthSession.last_seen_at.desc().nullsfirst())
            .all()
        )

        access_token = request.cookies.get("access_token")
        current_session_id = None
        if access_token:
            try:
                payload = decode_access_token(db, access_token)
                current_session_id = payload.get("session_id")
            except Exception:
                current_session_id = None

        context = base_context(request, auth, "Sessions", "settings")
        context.update(
            {
                "sessions": sessions,
                "current_session_id": current_session_id,
                "session_count": len(sessions),
            }
        )

        return templates.TemplateResponse(request, "finance/sessions.html", context)

    def change_password_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        credential = (
            db.query(UserCredential)
            .filter(UserCredential.person_id == auth.person_id)
            .filter(UserCredential.is_active.is_(True))
            .first()
        )

        context = base_context(request, auth, "Change Password", "settings")
        context.update(
            {
                "credential": credential,
                "must_change": credential.must_change_password if credential else False,
            }
        )

        return templates.TemplateResponse(
            request, "finance/change_password.html", context
        )

    def profile_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        person = db.get(Person, auth.person_id)
        if not person:
            return RedirectResponse(url="/logout", status_code=302)

        credential = (
            db.query(UserCredential)
            .filter(UserCredential.person_id == auth.person_id)
            .filter(UserCredential.is_active.is_(True))
            .first()
        )

        now = datetime.now(UTC)
        active_sessions = (
            db.query(AuthSession)
            .filter(AuthSession.person_id == auth.person_id)
            .filter(AuthSession.status == SessionStatus.active)
            .filter(AuthSession.revoked_at.is_(None))
            .filter(AuthSession.expires_at > now)
            .count()
        )

        current_session = (
            db.query(AuthSession)
            .filter(AuthSession.person_id == auth.person_id)
            .filter(AuthSession.status == SessionStatus.active)
            .filter(AuthSession.revoked_at.is_(None))
            .filter(AuthSession.expires_at > now)
            .order_by(AuthSession.last_seen_at.desc())
            .first()
        )

        has_2fa = (
            db.query(MFAMethod)
            .filter(MFAMethod.person_id == auth.person_id)
            .filter(MFAMethod.enabled.is_(True))
            .filter(MFAMethod.method_type == MFAMethodType.totp)
            .first()
        ) is not None

        context = base_context(request, auth, "Profile", "settings")
        context.update(
            {
                "person": person,
                "credential": credential,
                "active_sessions": active_sessions,
                "current_session": current_session,
                "roles": auth.roles,
                "has_2fa": has_2fa,
            }
        )

        return templates.TemplateResponse(request, "finance/profile.html", context)


profile_web_service = ProfileWebService()
