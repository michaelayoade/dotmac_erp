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
from app.db import AsyncSessionLocal, SessionLocal, get_auth_db_session
from app.models.auth import Session as AuthSession, SessionStatus
from app.models.person import Person
from app.models.rbac import Role, PersonRole
from app.rls import set_current_organization_sync
from app.services.auth_flow import decode_access_token
from app.services.auth_dependencies import is_session_inactive
from app.services.common import coerce_uuid
from app.services.finance.branding import BrandingService, CSSGenerator


def _get_auth_db_for_sso() -> Session | None:
    """Get auth database session for SSO validation in web routes.

    When SSO is enabled and this is an SSO client (not provider),
    returns a session to the shared auth database.
    """
    if settings.sso_enabled and not settings.sso_provider_mode:
        return get_auth_db_session()
    return None


def _validate_session_sso(
    session_id,
    person_id,
    now: datetime,
    auth_db: Session,
) -> AuthSession | None:
    """Validate session against SSO auth database.

    Handles timezone-naive expires_at values (SQLite compatibility).
    """
    session = (
        auth_db.query(AuthSession)
        .filter(AuthSession.id == session_id)
        .filter(AuthSession.person_id == person_id)
        .filter(AuthSession.status == SessionStatus.active)
        .filter(AuthSession.revoked_at.is_(None))
        .first()
    )

    if not session:
        return None

    # Handle timezone-naive expires_at (SQLite doesn't preserve timezone)
    expires_at = session.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    if expires_at <= now:
        return None

    return session


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

    For multi-word names, uses the first letter of first two words (e.g., "DotMac ERP" → "DB").
    For single-word names, uses first two letters (e.g., "Ledger" → "LE").
    """
    parts = [part for part in name.split() if part]
    if not parts:
        return "DB"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[1][0]).upper()


def brand_context() -> dict:
    """Get standard brand context for templates (system defaults)."""
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


def org_brand_context(db: Session, org_id: Optional[UUID]) -> dict:
    """
    Get organization-specific brand context for templates.

    Falls back to system defaults if no org branding exists.

    Returns dict with:
        - name: Display name
        - tagline: Brand tagline
        - logo_url: Logo URL for light backgrounds
        - logo_dark_url: Logo URL for dark backgrounds
        - favicon_url: Favicon URL
        - mark: 2-letter brand mark
        - css: Generated CSS for brand colors/fonts
        - fonts_url: Google Fonts URL if custom fonts
        - has_custom_branding: Whether org has custom branding
    """
    base = brand_context()

    if not org_id:
        return {
            **base,
            "logo_dark_url": None,
            "favicon_url": None,
            "css": "",
            "fonts_url": None,
            "has_custom_branding": False,
        }

    service = BrandingService(db)
    branding = service.get_by_org_id(org_id)

    if not branding:
        return {
            **base,
            "logo_dark_url": None,
            "favicon_url": None,
            "css": "",
            "fonts_url": None,
            "has_custom_branding": False,
        }

    # Generate CSS and fonts URL
    css_gen = CSSGenerator(branding)
    css = css_gen.generate()
    fonts_url = css_gen.get_google_fonts_url()

    return {
        "name": branding.display_name or base["name"],
        "tagline": branding.tagline or base["tagline"],
        "logo_url": branding.logo_url or base["logo_url"],
        "logo_dark_url": branding.logo_dark_url,
        "favicon_url": branding.favicon_url,
        "mark": branding.brand_mark or base["mark"],
        "css": css,
        "fonts_url": fonts_url,
        "has_custom_branding": True,
        "primary_color": branding.primary_color,
        "accent_color": branding.accent_color,
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
            {"key": "hr_payroll", "label": "HR & Payroll"},
            {"key": "real_time", "label": "Real-time"},
        ],
        "benefits": {
            "title": "Why teams choose Dotmac ERP",
            "subtitle": "Less busywork, faster closes, and a single source of truth.",
            "items": [
                {
                    "title": "One system, fewer handoffs",
                    "description": "Finance, HR, and operations stay in sync with shared data and approvals.",
                },
                {
                    "title": "Audit-ready out of the box",
                    "description": "Standard statements, audit trails, and controls built into every workflow.",
                },
                {
                    "title": "Real-time visibility",
                    "description": "Dashboards and reports update as transactions happen, not at month-end.",
                },
                {
                    "title": "Built for growing teams",
                    "description": "Multi-entity support, roles, and approvals that scale with your org.",
                },
            ],
        },
        "core_modules": {
            "title": "Core ERP modules",
            "subtitle": "Start with finance and HR, then expand across operations as you grow.",
            "cards": [
                {
                    "key": "finance",
                    "title": "Finance",
                    "description": "GL, AR/AP, fixed assets, banking, and financial reporting.",
                    "cta_label": "Explore finance",
                    "cta_href": "/finance/dashboard",
                },
                {
                    "key": "people",
                    "title": "People",
                    "description": "HR, payroll, leave, and employee expenses in one place.",
                    "cta_label": "Explore people",
                    "cta_href": "/people/hr/employees",
                },
                {
                    "key": "operations",
                    "title": "Operations",
                    "description": "Inventory, procurement, and workflow automation connected to finance.",
                    "cta_label": "Explore operations",
                    "cta_href": "/finance/inv/items",
                },
            ],
        },
        "modules": {
            "title": "ERP modules, fully connected",
            "subtitle": "Finance, people, and operations working from a single system of record.",
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
                "cta_href": "/finance/gl/accounts",
            },
            "cards": [
                {
                    "key": "ar",
                    "title": "Accounts Receivable",
                    "description": "Customer invoices, payments, credit memos, and aging analysis.",
                    "cta_label": "View AR",
                    "cta_href": "/finance/ar/customers",
                },
                {
                    "key": "ap",
                    "title": "Accounts Payable",
                    "description": "Supplier bills, payment scheduling, and expense allocation.",
                    "cta_label": "View AP",
                    "cta_href": "/finance/ap/suppliers",
                },
                {
                    "key": "fa",
                    "title": "Fixed Assets",
                    "description": "Asset register and depreciation schedules.",
                    "cta_label": "View assets",
                    "cta_href": "/finance/fa/assets",
                },
                {
                    "key": "banking",
                    "title": "Banking",
                    "description": "Bank accounts, reconciliation, and cash flow management.",
                    "cta_label": "View banking",
                    "cta_href": "/finance/banking/accounts",
                },
                {
                    "key": "reports",
                    "title": "Financial Reports",
                    "description": "Trial balance, P&L, balance sheet, and disclosure notes.",
                    "cta_label": "View reports",
                    "cta_href": "/finance/reports",
                },
            ],
        },
        "people": {
            "title": "People & Payroll",
            "subtitle": "Hire, pay, and manage teams with compliant HR workflows.",
            "featured": {
                "title": "Human Resources",
                "description": (
                    "Centralized employee management with departments, designations, and organizational hierarchy. "
                    "Track employee lifecycle from onboarding to offboarding."
                ),
                "chips": [
                    "Employee Database",
                    "Departments",
                    "Designations",
                    "Org Structure",
                ],
                "cta_label": "Explore HR",
                "cta_href": "/people/hr/employees",
            },
            "cards": [
                {
                    "key": "payroll",
                    "title": "Payroll",
                    "description": "Salary structures, components, payslips, and statutory compliance.",
                    "cta_label": "View Payroll",
                    "cta_href": "/people/payroll/slips",
                },
                {
                    "key": "leave",
                    "title": "Leave Management",
                    "description": "Leave types, applications, approvals, and balance tracking.",
                    "cta_label": "View Leave",
                    "cta_href": "/people/leave",
                },
                {
                    "key": "attendance",
                    "title": "Attendance",
                    "description": "Shift management, check-in/out tracking, and attendance reports.",
                    "cta_label": "View Attendance",
                    "cta_href": "/people/attendance",
                },
                {
                    "key": "recruit",
                    "title": "Recruitment",
                    "description": "Job postings, applicant tracking, and hiring workflows.",
                    "cta_label": "View Recruitment",
                    "cta_href": "/people/recruit",
                },
                {
                    "key": "expenses",
                    "title": "Expense Claims",
                    "description": "Employee expenses, cash advances, and corporate cards.",
                    "cta_label": "View Expenses",
                    "cta_href": "/people/expenses",
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
            "title": "Real-time reporting & insights",
            "subtitle": "Dashboards and reports that update as transactions happen across all modules.",
            "cards": [
                {"title": "Financial Reports", "subtitle": "P&L, balance sheet, cash flow"},
                {"title": "HR Analytics", "subtitle": "Headcount, attrition, payroll costs"},
                {"title": "Operations Metrics", "subtitle": "Inventory, procurement, fulfillment"},
                {"title": "Custom Dashboards", "subtitle": "Build reports for your KPIs"},
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
            "title": "Ready to run on one ERP?",
            "subtitle": "Unify finance, HR, and operations with {brand}.",
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
    db: Optional[Session] = None,
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
        db: Optional database session for loading org branding and notifications

    Returns:
        Dict with common template context values, including request for TemplateResponse.
    """
    # Get org-specific branding if db session available
    org_branding = None
    if db and auth.organization_id:
        org_branding = org_brand_context(db, auth.organization_id)
        try:
            from app.services.finance.platform.currency_context import get_currency_context
        except Exception:
            get_currency_context = None  # type: ignore[assignment]
    else:
        get_currency_context = None  # type: ignore[assignment]

    # Auto-fetch notifications if db session available and user is authenticated
    if notifications is None and db and auth.is_authenticated and auth.person_id:
        try:
            # Import here to avoid circular import
            from app.services.notification import notification_service
            from datetime import datetime, timedelta

            def _format_relative_time(dt: datetime) -> str:
                now = datetime.utcnow()
                diff = now - dt
                if diff < timedelta(minutes=1):
                    return "Just now"
                elif diff < timedelta(hours=1):
                    return f"{int(diff.total_seconds() / 60)} min ago"
                elif diff < timedelta(days=1):
                    return f"{int(diff.total_seconds() / 3600)}h ago"
                elif diff < timedelta(days=7):
                    return f"{diff.days}d ago"
                else:
                    return dt.strftime("%b %d")

            def _notification_type_to_display(entity_type, notification_type) -> str:
                from app.models.notification import NotificationType
                if notification_type in (NotificationType.MENTION, NotificationType.COMMENT, NotificationType.REPLY):
                    return "mention"
                elif notification_type in (NotificationType.APPROVED, NotificationType.COMPLETED, NotificationType.RESOLVED):
                    return "payment"
                elif notification_type in (NotificationType.REJECTED, NotificationType.OVERDUE, NotificationType.ALERT):
                    return "alert"
                else:
                    return "info"

            raw_notifications = notification_service.list_notifications(
                db, recipient_id=auth.person_id, organization_id=auth.organization_id, limit=5
            )
            notifications = [
                {
                    "id": str(n.notification_id),
                    "type": _notification_type_to_display(n.entity_type, n.notification_type),
                    "title": n.title,
                    "message": n.message,
                    "url": n.action_url or "#",
                    "time": _format_relative_time(n.created_at),
                    "read": n.is_read,
                }
                for n in raw_notifications
            ]
        except Exception:
            # Don't fail page load if notifications fail
            notifications = []

    can_team_leave = "admin" in auth.roles or auth.has_any_permission(
        [
            "leave:applications:approve:tier1",
            "leave:applications:approve:tier2",
            "leave:applications:approve:tier3",
        ]
    )
    can_team_expenses = "admin" in auth.roles or auth.has_any_permission(
        [
            "expense:claims:approve:tier1",
            "expense:claims:approve:tier2",
            "expense:claims:approve:tier3",
        ]
    )

    context = {
        "request": request,
        "title": page_title,
        "page_title": page_title,
        "brand": org_branding or brand_context(),
        "org_branding": org_branding,
        "active_module": active_module,
        "user": auth.user,
        "accessible_modules": auth.accessible_modules,
        "can_team_leave": can_team_leave,
        "can_team_expenses": can_team_expenses,
        "csrf_token": getattr(request.state, "csrf_token", ""),
        "notifications": notifications or [],
    }
    if db and auth.organization_id and get_currency_context:
        try:
            context.update(get_currency_context(db, str(auth.organization_id)))
        except Exception:
            pass
    return context


class WebAuthContext:
    """Authentication context for web routes."""

    def __init__(
        self,
        is_authenticated: bool = False,
        person_id: Optional[UUID] = None,
        organization_id: Optional[UUID] = None,
        employee_id: Optional[UUID] = None,
        user_name: str = "Guest",
        user_initials: str = "GU",
        roles: Optional[list[str]] = None,
        scopes: Optional[list[str]] = None,
    ):
        self.is_authenticated = is_authenticated
        self.person_id = person_id
        self.organization_id = organization_id
        self.employee_id = employee_id
        self.user_name = user_name
        self.user_initials = user_initials
        self.roles = roles or []
        self.scopes = scopes or []

    @property
    def user(self) -> dict:
        """Get user dict for template context."""
        return {
            "name": self.user_name,
            "initials": self.user_initials,
            "is_authenticated": self.is_authenticated,
            "is_admin": self.is_admin,
        }

    @property
    def user_id(self) -> Optional[UUID]:
        """Alias for person_id for backward compatibility."""
        return self.person_id

    @property
    def is_admin(self) -> bool:
        """Check if user has admin role."""
        return "admin" in self.roles

    @property
    def accessible_modules(self) -> list[str]:
        """Get list of modules the user can access."""
        modules = []
        scopes_set = set(self.scopes)

        if self.is_admin or "finance:access" in scopes_set:
            modules.append("finance")
        if self.is_admin or "hr:access" in scopes_set:
            modules.append("people")
        if self.is_admin or "operations:access" in scopes_set:
            modules.append("operations")
        if self.is_admin or "expense:access" in scopes_set:
            modules.append("expense")
        if "self:access" in scopes_set:
            modules.append("self_service")

        return modules

    def has_module_access(self, module: str) -> bool:
        """Check if user can access a specific module."""
        alias_map = {
            "hr": "people",
            "people": "people",
            "finance": "finance",
            "operations": "operations",
            "expense": "expense",
            "expenses": "expense",
            "self": "self_service",
            "self-service": "self_service",
            "self_service": "self_service",
        }
        canonical = alias_map.get(module, module)
        return canonical in self.accessible_modules

    def has_permission(self, permission: str) -> bool:
        """Check if user has a specific permission."""
        return self.is_admin or permission in self.scopes

    def has_any_permission(self, permissions: list[str]) -> bool:
        """Check if user has any of the specified permissions."""
        if self.is_admin:
            return True
        return bool(set(permissions) & set(self.scopes))

    def has_all_permissions(self, permissions: list[str]) -> bool:
        """Check if user has all specified permissions."""
        if self.is_admin:
            return True
        return set(permissions).issubset(set(self.scopes))

    @property
    def default_module(self) -> Optional[str]:
        """Get the user's default module (first accessible module)."""
        modules = self.accessible_modules
        return modules[0] if modules else None

    @property
    def default_redirect(self) -> str:
        """Get the default redirect URL based on accessible modules."""
        if not self.is_authenticated:
            return "/login"
        modules = self.accessible_modules
        if len(modules) == 0:
            return "/no-access"
        if len(modules) == 1:
            module = modules[0]
            if module == "finance":
                return "/finance/dashboard"
            if module == "people":
                return "/people/hr/employees"
            if module == "operations":
                return "/operations/dashboard"
            if module == "expense":
                return "/expense"
            if module == "self_service":
                return "/people/self/attendance"
            return f"/{module}/dashboard"
        # Multiple modules - go to module selector
        return "/"


def _extract_bearer_token(authorization: str | None) -> str | None:
    """Extract Bearer token from Authorization header."""
    if not authorization:
        return None
    parts = authorization.split(" ", 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return None


def _normalize_roles_scopes(roles: list[str], scopes: list[str]) -> tuple[list[str], list[str]]:
    normalized_roles = [str(role).strip().lower() for role in roles if str(role).strip()]
    normalized_scopes = [str(scope).strip().lower() for scope in scopes if str(scope).strip()]
    return normalized_roles, normalized_scopes


def _ensure_admin_role(db: Session, person_id: UUID, roles: list[str]) -> list[str]:
    if "admin" in roles:
        return roles
    admin_role = db.query(Role).filter(Role.name == "admin").first()
    if not admin_role:
        return roles
    has_admin_role = (
        db.query(PersonRole)
        .filter(PersonRole.person_id == person_id, PersonRole.role_id == admin_role.id)
        .first()
    )
    if has_admin_role:
        roles = [*roles, "admin"]
    return roles


def require_web_auth(
    request: Request,
    authorization: str | None = Header(default=None),
    access_token: str | None = Cookie(default=None),
    db: Session = Depends(get_db),
) -> WebAuthContext:
    """
    Require authentication for web routes and set tenant context.

    Supports SSO by validating tokens against shared auth database when
    SSO is enabled and this app is an SSO client.

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
        # Decode token (uses SSO secret when SSO is enabled)
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

    # SSO: validate session against shared auth database
    auth_db = _get_auth_db_for_sso()
    try:
        if auth_db:
            # SSO client mode - validate against shared auth database
            session = _validate_session_sso(session_uuid, person_uuid, now, auth_db)
        else:
            # SSO provider or non-SSO mode - validate against local database
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

        # Update session activity in auth database
        if auth_db:
            session.last_seen_at = now
            auth_db.commit()

    finally:
        if auth_db:
            auth_db.close()

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
    def _clean_name(value: Optional[str]) -> str:
        cleaned = (value or "").strip()
        return "" if cleaned.lower() in {"none", "null"} else cleaned

    display_name = _clean_name(person.display_name)
    first_name = _clean_name(person.first_name)
    last_name = _clean_name(person.last_name)
    base_name = f"{first_name} {last_name}".strip()
    user_name = display_name or base_name or _clean_name(person.email) or "User"
    initials = "".join(word[0].upper() for word in user_name.split()[:2]) if user_name else "US"

    roles_value = payload.get("roles")
    roles = [str(role) for role in roles_value] if isinstance(roles_value, list) else []
    scopes_value = payload.get("scopes")
    scopes = [str(scope) for scope in scopes_value] if isinstance(scopes_value, list) else []
    roles, scopes = _normalize_roles_scopes(roles, scopes)
    roles = _ensure_admin_role(db, person_uuid, roles)

    # Look up employee_id for the person (may be None if person is not an employee)
    from sqlalchemy import select
    from app.models.people.hr.employee import Employee
    employee = db.scalar(
        select(Employee).where(Employee.person_id == person_uuid)
    )
    employee_id = employee.employee_id if employee else None

    return WebAuthContext(
        is_authenticated=True,
        person_id=person_uuid,
        organization_id=organization_id,
        employee_id=employee_id,
        user_name=user_name,
        user_initials=initials,
        roles=roles,
        scopes=scopes,
    )


def optional_web_auth(
    request: Request,
    authorization: str | None = Header(default=None),
    access_token: str | None = Cookie(default=None),
    db: Session = Depends(get_db),
) -> WebAuthContext:
    """
    Optional authentication for web routes with SSO support.

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

    # SSO: validate session against shared auth database
    auth_db = _get_auth_db_for_sso()
    try:
        if auth_db:
            # SSO client mode - validate against shared auth database
            session = _validate_session_sso(session_uuid, person_uuid, now, auth_db)
        else:
            # SSO provider or non-SSO mode - validate against local database
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

        # Update session activity in auth database
        if auth_db:
            session.last_seen_at = now
            auth_db.commit()

    finally:
        if auth_db:
            auth_db.close()

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
    def _clean_name(value: Optional[str]) -> str:
        cleaned = (value or "").strip()
        return "" if cleaned.lower() in {"none", "null"} else cleaned

    display_name = _clean_name(person.display_name)
    first_name = _clean_name(person.first_name)
    last_name = _clean_name(person.last_name)
    base_name = f"{first_name} {last_name}".strip()
    user_name = display_name or base_name or _clean_name(person.email) or "User"
    initials = "".join(word[0].upper() for word in user_name.split()[:2]) if user_name else "US"

    roles_value = payload.get("roles")
    roles = [str(role) for role in roles_value] if isinstance(roles_value, list) else []
    scopes_value = payload.get("scopes")
    scopes = [str(scope) for scope in scopes_value] if isinstance(scopes_value, list) else []
    roles, scopes = _normalize_roles_scopes(roles, scopes)
    roles = _ensure_admin_role(db, person_uuid, roles)

    # Look up employee_id for the person (may be None if person is not an employee)
    from sqlalchemy import select
    from app.models.people.hr.employee import Employee
    employee = db.scalar(
        select(Employee).where(Employee.person_id == person_uuid)
    )
    employee_id = employee.employee_id if employee else None

    return WebAuthContext(
        is_authenticated=True,
        person_id=person_uuid,
        organization_id=organization_id,
        employee_id=employee_id,
        user_name=user_name,
        user_initials=initials,
        roles=roles,
        scopes=scopes,
    )


# =============================================================================
# Module Access Dependencies
# =============================================================================


def require_finance_access(
    auth: WebAuthContext = Depends(require_web_auth),
) -> WebAuthContext:
    """
    Require access to the finance module.

    Use this dependency for all finance/accounting web routes.

    Usage:
        @router.get("/finance/dashboard")
        def finance_dashboard(
            request: Request,
            auth: WebAuthContext = Depends(require_finance_access),
        ):
            ...
    """
    if not auth.has_module_access("finance"):
        raise HTTPException(
            status_code=403,
            detail="Finance module access required",
        )
    return auth


def require_hr_access(
    auth: WebAuthContext = Depends(require_web_auth),
) -> WebAuthContext:
    """
    Require access to the HR module.

    Use this dependency for all HR/people web routes.

    Usage:
        @router.get("/hr/dashboard")
        def hr_dashboard(
            request: Request,
            auth: WebAuthContext = Depends(require_hr_access),
        ):
            ...
    """
    if not auth.has_module_access("people"):
        raise HTTPException(
            status_code=403,
            detail="HR module access required",
        )
    return auth


def require_operations_access(
    auth: WebAuthContext = Depends(require_web_auth),
) -> WebAuthContext:
    """
    Require access to the Operations module.
    """
    if not auth.has_module_access("operations"):
        raise HTTPException(
            status_code=403,
            detail="Operations module access required",
        )
    return auth


def require_expense_access(
    auth: WebAuthContext = Depends(require_web_auth),
) -> WebAuthContext:
    """
    Require access to the Expense module.

    Use this dependency for all expense management web routes.
    Also allows access for users with finance:access scope since
    expense claims integrate with the GL.

    Usage:
        @router.get("/expense/claims")
        def expense_claims(
            request: Request,
            auth: WebAuthContext = Depends(require_expense_access),
        ):
            ...
    """
    # Allow both expense:access and finance:access since they're related
    if not auth.has_module_access("expense") and not auth.has_module_access("finance"):
        raise HTTPException(
            status_code=403,
            detail="Expense module access required",
        )
    return auth


def require_self_service_access(
    auth: WebAuthContext = Depends(require_web_auth),
) -> WebAuthContext:
    """
    Require access to employee self-service pages.

    Allows users with self-service access or full HR module access.
    """
    if not auth.has_module_access("self_service") and not auth.has_module_access("people"):
        raise HTTPException(
            status_code=403,
            detail="Self-service access required",
        )
    return auth


def require_self_service_leave_approver(
    auth: WebAuthContext = Depends(require_self_service_access),
) -> WebAuthContext:
    """Require self-service access plus leave approval permission."""
    if "admin" in auth.roles:
        return auth
    permissions = [
        "leave:applications:approve:tier1",
        "leave:applications:approve:tier2",
        "leave:applications:approve:tier3",
    ]
    if not auth.has_any_permission(permissions):
        raise HTTPException(
            status_code=403,
            detail="Leave approval permission required",
        )
    return auth


def require_self_service_expense_approver(
    auth: WebAuthContext = Depends(require_self_service_access),
) -> WebAuthContext:
    """Require self-service access plus expense approval permission."""
    if "admin" in auth.roles:
        return auth
    permissions = [
        "expense:claims:approve:tier1",
        "expense:claims:approve:tier2",
        "expense:claims:approve:tier3",
    ]
    if not auth.has_any_permission(permissions):
        raise HTTPException(
            status_code=403,
            detail="Expense approval permission required",
        )
    return auth


def require_module_access(module: str):
    """
    Factory for creating module access dependencies.

    Usage:
        @router.get("/custom/dashboard")
        def custom_dashboard(
            request: Request,
            auth: WebAuthContext = Depends(require_module_access("custom")),
        ):
            ...
    """
    def _require_module_access(
        auth: WebAuthContext = Depends(require_web_auth),
    ) -> WebAuthContext:
        if not auth.has_module_access(module):
            raise HTTPException(
                status_code=403,
                detail=f"{module.title()} module access required",
            )
        return auth

    return _require_module_access


def require_web_permission(permission: str):
    """
    Factory for creating permission-based web route dependencies.

    Usage:
        @router.get("/gl/journals")
        def list_journals(
            request: Request,
            auth: WebAuthContext = Depends(require_web_permission("gl:read")),
        ):
            ...
    """
    def _require_permission(
        auth: WebAuthContext = Depends(require_web_auth),
    ) -> WebAuthContext:
        if not auth.has_permission(permission):
            raise HTTPException(
                status_code=403,
                detail=f"Permission '{permission}' required",
            )
        return auth

    return _require_permission


def require_any_web_permission(permissions: list[str]):
    """
    Factory for requiring any of the specified permissions.

    Usage:
        @router.get("/reports/overview")
        def reports_overview(
            auth: WebAuthContext = Depends(require_any_web_permission(["reports:read", "gl:read"])),
        ):
            ...
    """
    def _require_any_permission(
        auth: WebAuthContext = Depends(require_web_auth),
    ) -> WebAuthContext:
        if not auth.has_any_permission(permissions):
            raise HTTPException(
                status_code=403,
                detail=f"One of these permissions required: {', '.join(permissions)}",
            )
        return auth

    return _require_any_permission
