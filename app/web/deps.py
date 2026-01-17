"""
Web route authentication dependencies.

Provides authentication dependencies for HTML template routes with
proper tenant context handling.
"""

import json
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
from app.services.auth_dependencies import is_session_inactive
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


def _brand_mark(name: str) -> str:
    """Generate a 2-letter brand mark from the brand name.

    For multi-word names, uses the first letter of first two words (e.g., "DotMac Books" → "DB").
    For single-word names, uses first two letters (e.g., "Ledger" → "LE").
    """
    parts = [part for part in name.split() if part]
    if not parts:
        return "DB"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[1][0]).upper()


def brand_context() -> dict:
    """Get standard brand context for templates."""
    # Use configured brand_mark or derive from name
    mark = settings.brand_mark or (
        _brand_mark(settings.brand_name) if settings.brand_name else "DB"
    )
    return {
        "name": settings.brand_name,
        "tagline": settings.brand_tagline,
        "logo_url": settings.brand_logo_url,
        "mark": mark,
    }


def _merge_dicts(base: dict, override: dict) -> dict:
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge_dicts(base[key], value)
        else:
            base[key] = value
    return base


def landing_content() -> dict:
    """Get landing page content for templates."""
    content = {
        "hero": {
            "badge": settings.landing_hero_badge,
            "title": settings.landing_hero_title,
            "subtitle": f"{settings.brand_tagline}. {settings.landing_hero_subtitle}",
            "cta_primary": settings.landing_cta_primary,
            "cta_secondary": settings.landing_cta_secondary,
        },
        "proof_pills": [
            {"key": "multi_entity", "label": "Multi-entity"},
            {"key": "audit_trail", "label": "Audit trail"},
            {"key": "ifrs_templates", "label": "IFRS templates"},
            {"key": "ar_ap_aging", "label": "AR/AP aging"},
        ],
        "modules": {
            "title": "Complete accounting modules",
            "subtitle": "Everything you need to manage your finances, from general ledger to detailed reporting.",
            "featured": {
                "title": "General Ledger",
                "description": (
                    "The foundation of your accounting system. Chart of accounts with flexible hierarchies, "
                    "journal entries with approval workflows, and trial balance with multi-currency support."
                ),
                "chips": [
                    "Chart of Accounts",
                    "Journal Entries",
                    "Trial Balance",
                    "Multi-Currency",
                ],
                "cta_label": "Explore General Ledger",
                "cta_href": "/gl/accounts",
            },
            "cards": [
                {
                    "key": "ar",
                    "title": "Accounts Receivable",
                    "description": "Customer invoices, payments, credit memos, and aging analysis.",
                    "cta_label": "View AR",
                    "cta_href": "/ar/customers",
                },
                {
                    "key": "ap",
                    "title": "Accounts Payable",
                    "description": "Supplier bills, payment scheduling, and expense allocation.",
                    "cta_label": "View AP",
                    "cta_href": "/ap/suppliers",
                },
                {
                    "key": "fa",
                    "title": "Fixed Assets",
                    "description": "Asset register and depreciation schedules per IAS 16.",
                    "cta_label": "View assets",
                    "cta_href": "/fa/assets",
                },
                {
                    "key": "banking",
                    "title": "Banking",
                    "description": "Bank accounts, reconciliation, and cash flow management.",
                    "cta_label": "View banking",
                    "cta_href": "/banking/accounts",
                },
                {
                    "key": "reports",
                    "title": "Financial Reports",
                    "description": "Trial balance, P&L, balance sheet, and IFRS notes.",
                    "cta_label": "View reports",
                    "cta_href": "/gl/trial-balance",
                },
            ],
        },
        "audit": {
            "badge": "Audit-ready",
            "title": "Every entry traceable.\nEvery report ready.",
            "description": (
                "Built for compliance from day one. Complete audit trail, approval workflows, document "
                "attachments, and row-level security ensure your books are always ready for review."
            ),
            "bullets": [
                "Complete change history with user attribution",
                "Multi-level approval workflows",
                "Document attachments for supporting evidence",
                "Row-level security for data isolation",
            ],
        },
        "reports": {
            "title": "IFRS-compliant reporting",
            "subtitle": "Generate standard financial statements with proper IFRS disclosures, ready for auditors.",
            "cards": [
                {"title": "Trial Balance", "subtitle": "Detailed and summary views"},
                {"title": "P&L Statement", "subtitle": "By period and comparative"},
                {"title": "Statement of Financial Position", "subtitle": "IFRS presentation"},
                {"title": "Cash Flow", "subtitle": "Direct and indirect methods"},
            ],
        },
        "security": {
            "title": "Enterprise-grade security",
            "subtitle": "Your financial data deserves the highest level of protection.",
            "cards": [
                {
                    "key": "rls",
                    "title": "Row-Level Security",
                    "description": "PostgreSQL RLS ensures data isolation between tenants.",
                },
                {
                    "key": "rbac",
                    "title": "Role-Based Access",
                    "description": "Fine-grained permissions control who can view, edit, and approve.",
                },
                {
                    "key": "encryption",
                    "title": "Encrypted at Rest",
                    "description": "Sensitive data is encrypted with industry-standard algorithms.",
                },
            ],
        },
        "cta": {
            "title": "Ready to close faster?",
            "subtitle": "Join finance teams who have shortened their month-end close with {brand}.",
            "cta_primary": settings.landing_cta_primary,
            "cta_secondary": settings.landing_cta_secondary,
        },
    }

    if settings.landing_content_json:
        try:
            override = json.loads(settings.landing_content_json)
        except json.JSONDecodeError:
            override = None
        if isinstance(override, dict):
            content = _merge_dicts(content, override)

    return content


def base_context(
    request: Request,
    auth: "WebAuthContext",
    page_title: str,
    active_module: str = "",
    notifications: list | None = None,
) -> dict:
    """
    Get base template context with authentication.

    Args:
        request: FastAPI request
        auth: WebAuthContext from authentication
        page_title: Page title for the template
        active_module: Active navigation module
        notifications: List of notification dicts with keys:
            - type: 'mention' | 'invoice' | 'payment' | 'alert' | 'info'
            - title: Short title text
            - message: Longer description
            - url: Link to navigate when clicked
            - time: Relative time string (e.g., "5 min ago")
            - read: bool indicating if notification was read

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
        "notifications": notifications or [],
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

    # Check for activity timeout (session idle too long)
    if is_session_inactive(session, now):
        raise HTTPException(status_code=401, detail="Session expired due to inactivity")

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

    # Check for activity timeout (session idle too long)
    if is_session_inactive(session, now):
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
