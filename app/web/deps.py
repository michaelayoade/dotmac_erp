"""
Web route authentication dependencies.

Provides authentication dependencies for HTML template routes with
proper tenant context handling.
"""

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Request, Cookie
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.config import settings
from app.db import AsyncSessionLocal, SessionLocal
from app.models.auth import Session as AuthSession, SessionStatus
from app.models.person import Person
from app.rls import set_current_organization_sync
from app.services.auth_flow import decode_access_token
from app.services.common import coerce_uuid


def get_db():
    """Get database session for web routes."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


async def get_async_db():
    """Get async database session for web routes."""
    async with AsyncSessionLocal() as db:
        try:
            yield db
        finally:
            await db.close()


def brand_context() -> dict:
    """Get standard brand context for templates."""
    return {
        "name": settings.brand_name,
        "tagline": settings.brand_tagline,
        "logo_url": settings.brand_logo_url,
        "mark": settings.brand_name[:2].upper() if settings.brand_name else "IF",
    }


def base_context(
    request: Request,
    auth: "WebAuthContext",
    page_title: str,
    active_module: str = "",
) -> dict:
    """
    Get base template context with authentication.

    Args:
        request: FastAPI request
        auth: WebAuthContext from authentication
        page_title: Page title for the template
        active_module: Active navigation module

    Returns:
        Dict with common template context values (without request - use new TemplateResponse signature)
    """
    return {
        "title": page_title,
        "page_title": page_title,
        "brand": brand_context(),
        "active_module": active_module,
        "user": auth.user,
        "csrf_token": getattr(request.state, "csrf_token", ""),
    }


class WebAuthContext:
    """Authentication context for web routes."""

    def __init__(
        self,
        is_authenticated: bool = False,
        person_id: Optional[UUID] = None,
        organization_id: Optional[UUID] = None,
        user_name: str = "Guest",
        user_initials: str = "GU",
        roles: Optional[list[str]] = None,
    ):
        self.is_authenticated = is_authenticated
        self.person_id = person_id
        self.organization_id = organization_id
        self.user_name = user_name
        self.user_initials = user_initials
        self.roles = roles or []

    @property
    def user(self) -> dict:
        """Get user dict for template context."""
        return {
            "name": self.user_name,
            "initials": self.user_initials,
            "is_authenticated": self.is_authenticated,
        }


def _extract_bearer_token(authorization: str | None) -> str | None:
    """Extract Bearer token from Authorization header."""
    if not authorization:
        return None
    parts = authorization.split(" ", 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return None


def require_web_auth(
    request: Request,
    authorization: str | None = Header(default=None),
    access_token: str | None = Cookie(default=None),
    db: Session = Depends(get_db),
) -> WebAuthContext:
    """
    Require authentication for web routes and set tenant context.

    Checks for JWT in:
    1. Authorization header (Bearer token)
    2. access_token cookie

    Returns WebAuthContext with user info for templates.
    Sets RLS context for the user's organization.

    Usage:
        @router.get("/dashboard")
        def dashboard(
            request: Request,
            auth: WebAuthContext = Depends(require_web_auth),
            db: Session = Depends(get_db),
        ):
            # auth.organization_id is available
            # RLS context is set
            return templates.TemplateResponse(request, "dashboard.html", {
                "user": auth.user,
            })
    """
    # Try to get token from header or cookie
    token = _extract_bearer_token(authorization) or access_token

    if not token:
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = decode_access_token(db, token)
    except HTTPException:
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    person_id = payload.get("sub")
    session_id = payload.get("session_id")

    if not person_id or not session_id:
        raise HTTPException(status_code=401, detail="Invalid token")

    now = datetime.now(timezone.utc)
    person_uuid = coerce_uuid(person_id)
    session_uuid = coerce_uuid(session_id)

    # Validate session
    session = (
        db.query(AuthSession)
        .filter(AuthSession.id == session_uuid)
        .filter(AuthSession.person_id == person_uuid)
        .filter(AuthSession.status == SessionStatus.active)
        .filter(AuthSession.revoked_at.is_(None))
        .filter(AuthSession.expires_at > now)
        .first()
    )

    if not session:
        raise HTTPException(status_code=401, detail="Session expired or invalid")

    # Get person details
    person = db.get(Person, person_uuid)
    if not person:
        raise HTTPException(status_code=401, detail="User not found")

    organization_id = person.organization_id

    # Set RLS context
    if organization_id:
        set_current_organization_sync(db, organization_id)
        request.state.organization_id = str(organization_id)

    request.state.actor_id = str(person_id)

    # Build user display info
    user_name = person.name or person.email or "User"
    initials = "".join(word[0].upper() for word in user_name.split()[:2]) if user_name else "US"

    roles_value = payload.get("roles")
    roles = [str(role) for role in roles_value] if isinstance(roles_value, list) else []

    return WebAuthContext(
        is_authenticated=True,
        person_id=person_uuid,
        organization_id=organization_id,
        user_name=user_name,
        user_initials=initials,
        roles=roles,
    )


def optional_web_auth(
    request: Request,
    authorization: str | None = Header(default=None),
    access_token: str | None = Cookie(default=None),
    db: Session = Depends(get_db),
) -> WebAuthContext:
    """
    Optional authentication for web routes.

    Similar to require_web_auth but returns a guest context
    if no valid authentication is provided.

    Use this for pages that can be viewed by unauthenticated users
    but show different content for authenticated users.
    """
    token = _extract_bearer_token(authorization) or access_token

    if not token:
        return WebAuthContext(is_authenticated=False)

    try:
        payload = decode_access_token(db, token)
    except HTTPException:
        return WebAuthContext(is_authenticated=False)

    person_id = payload.get("sub")
    session_id = payload.get("session_id")

    if not person_id or not session_id:
        return WebAuthContext(is_authenticated=False)

    now = datetime.now(timezone.utc)
    person_uuid = coerce_uuid(person_id)
    session_uuid = coerce_uuid(session_id)

    # Validate session
    session = (
        db.query(AuthSession)
        .filter(AuthSession.id == session_uuid)
        .filter(AuthSession.person_id == person_uuid)
        .filter(AuthSession.status == SessionStatus.active)
        .filter(AuthSession.revoked_at.is_(None))
        .filter(AuthSession.expires_at > now)
        .first()
    )

    if not session:
        return WebAuthContext(is_authenticated=False)

    # Get person details
    person = db.get(Person, person_uuid)
    if not person:
        return WebAuthContext(is_authenticated=False)

    organization_id = person.organization_id

    # Set RLS context
    if organization_id:
        set_current_organization_sync(db, organization_id)
        request.state.organization_id = str(organization_id)

    request.state.actor_id = str(person_id)

    # Build user display info
    user_name = person.name or person.email or "User"
    initials = "".join(word[0].upper() for word in user_name.split()[:2]) if user_name else "US"

    roles_value = payload.get("roles")
    roles = [str(role) for role in roles_value] if isinstance(roles_value, list) else []

    return WebAuthContext(
        is_authenticated=True,
        person_id=person_uuid,
        organization_id=organization_id,
        user_name=user_name,
        user_initials=initials,
        roles=roles,
    )
