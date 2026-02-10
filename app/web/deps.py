"""
Web route authentication dependencies.

Provides authentication dependencies for HTML template routes with
proper tenant context handling.
"""

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from fastapi import Cookie, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from app.config import settings
from app.db import AsyncSessionLocal, SessionLocal, get_auth_db_session
from app.models.auth import Session as AuthSession
from app.models.auth import SessionStatus
from app.models.person import Person
from app.models.rbac import PersonRole, Role
from app.rls import set_current_organization_sync
from app.services.auth_dependencies import is_session_inactive
from app.services.auth_flow import (
    AuthFlow,
    _load_rbac_claims,
    decode_access_token,
    hash_session_token,
)
from app.services.common import coerce_uuid
from app.services.finance.branding import BrandingService, CSSGenerator
from app.templates import templates  # noqa: F401 - re-exported for web routes

logger = logging.getLogger(__name__)


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
        expires_at = expires_at.replace(tzinfo=UTC)

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


def org_brand_context(db: Session, org_id: UUID | None) -> dict:
    """
    Get organization-specific brand context for templates.

    Falls back to system defaults if no org branding exists.
    Uses Redis cache for CSS generation (1 hour TTL).

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

    no_branding = {
        **base,
        "logo_dark_url": None,
        "favicon_url": None,
        "css": "",
        "fonts_url": None,
        "has_custom_branding": False,
    }

    if not org_id:
        return no_branding

    service = BrandingService(db)
    branding = service.get_by_org_id(org_id)

    if not branding:
        return no_branding

    # Try cache for CSS generation (expensive HSL color math)
    from app.services.cache import CacheKeys, CacheService, cache_service

    cache_key = CacheKeys.org_branding_css(org_id)
    cached = cache_service.get(cache_key)

    if cached and isinstance(cached, dict):
        css = cached.get("css", "")
        fonts_url = cached.get("fonts_url")
    else:
        css_gen = CSSGenerator(branding)
        css = css_gen.generate()
        fonts_url = css_gen.get_google_fonts_url()

        # Cache the result
        cache_service.set(
            cache_key,
            {"css": css, "fonts_url": fonts_url},
            ttl_seconds=CacheService.TTL_BRANDING,
        )

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


def resolve_brand_context(
    db: Session | None,
    organization,
    organization_id: UUID | None,
) -> dict:
    """Resolve brand context with consistent fallback order."""
    brand = (
        org_brand_context(db, organization_id)
        if db and organization_id
        else brand_context()
    )
    if organization:
        org_name = organization.trading_name or organization.legal_name
        # Prefer org name/logo when branding isn't explicitly configured.
        if org_name and (
            not brand.get("name") or brand.get("name") == settings.brand_name
        ):
            brand["name"] = org_name
        if organization.logo_url and not brand.get("logo_url"):
            brand["logo_url"] = organization.logo_url
        if not brand.get("mark"):
            brand["mark"] = _brand_mark(brand.get("name") or org_name or "DB")
    return brand


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
                    "description": "Finance, HR, and operational modules stay in sync with shared data and approvals.",
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
            "subtitle": "Start with finance and HR, then add inventory, fleet, support, procurement, and projects.",
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
                    "key": "inventory",
                    "title": "Inventory",
                    "description": "Items, warehouses, stock movements, and valuation.",
                    "cta_label": "Explore inventory",
                    "cta_href": "/inventory/items",
                },
            ],
        },
        "modules": {
            "title": "ERP modules, fully connected",
            "subtitle": "Finance, people, and operational modules working from a single system of record.",
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
                    "cta_href": "/fixed-assets/assets",
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
                {
                    "title": "Financial Reports",
                    "subtitle": "P&L, balance sheet, cash flow",
                },
                {
                    "title": "HR Analytics",
                    "subtitle": "Headcount, attrition, payroll costs",
                },
                {
                    "title": "Operations Metrics",
                    "subtitle": "Inventory, procurement, fulfillment",
                },
                {
                    "title": "Custom Dashboards",
                    "subtitle": "Build reports for your KPIs",
                },
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
            "subtitle": "Unify finance, HR, and operational modules with {brand}.",
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
    db: Session | None = None,
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
    # Load organization object for template conditionals (e.g. IPSAS sidebar toggle)
    organization = None
    if db and auth.organization_id:
        from app.models.finance.core_org.organization import Organization

        organization = db.get(Organization, auth.organization_id)

    # Set per-request formatting preferences from organisation settings
    if organization is not None:
        from app.services.formatting_context import (
            resolve_from_org,
            set_formatting_prefs,
        )

        set_formatting_prefs(resolve_from_org(organization))

    # Get org-specific branding if db session available
    org_branding = None
    if db and auth.organization_id:
        org_branding = org_brand_context(db, auth.organization_id)
        try:
            from app.services.finance.platform.currency_context import (
                get_currency_context,
            )
        except Exception:
            get_currency_context = None  # type: ignore[assignment]
    else:
        get_currency_context = None  # type: ignore[assignment]

    # Auto-fetch notifications if db session available and user is authenticated
    if notifications is None and db and auth.is_authenticated and auth.person_id:
        try:
            # Import here to avoid circular import
            from datetime import datetime, timedelta

            from app.services.notification import notification_service

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

                if notification_type in (
                    NotificationType.MENTION,
                    NotificationType.COMMENT,
                    NotificationType.REPLY,
                ):
                    return "mention"
                elif notification_type in (
                    NotificationType.APPROVED,
                    NotificationType.COMPLETED,
                    NotificationType.RESOLVED,
                ):
                    return "payment"
                elif notification_type in (
                    NotificationType.REJECTED,
                    NotificationType.OVERDUE,
                    NotificationType.ALERT,
                ):
                    return "alert"
                else:
                    return "info"

            raw_notifications = notification_service.list_notifications(
                db,
                recipient_id=auth.person_id,
                organization_id=auth.organization_id,
                limit=5,
            )
            notifications = [
                {
                    "id": str(n.notification_id),
                    "type": _notification_type_to_display(
                        n.entity_type, n.notification_type
                    ),
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

    settings_url = "/settings"
    if request.url.path.startswith("/people"):
        settings_url = "/people/settings"
    elif active_module in {"support", "inventory", "projects", "fleet", "procurement"}:
        settings_url = f"/settings/{active_module}"

    brand = resolve_brand_context(db, organization, auth.organization_id)

    # Ensure csrf_form contains an HTML hidden input for template rendering.
    # On POST requests, the CSRF middleware caches the parsed FormData object
    # in request.state.csrf_form for handler consumption.  By this point all
    # handlers have already read the form data, so it is safe to replace it
    # with the HTML string that templates expect via {{ request.state.csrf_form | safe }}.
    csrf_token = getattr(request.state, "csrf_token", "")
    csrf_form_val = getattr(request.state, "csrf_form", None)
    if not isinstance(csrf_form_val, str):
        request.state.csrf_form = (
            f'<input type="hidden" name="csrf_token" value="{csrf_token}">'
            if csrf_token
            else ""
        )

    context = {
        "request": request,
        "title": page_title,
        "page_title": page_title,
        "brand": brand,
        "org_branding": org_branding,
        "active_module": active_module,
        "settings_url": settings_url,
        "auth": auth,
        "user": auth.user,
        "organization": organization,
        "accessible_modules": auth.accessible_modules,
        "can_team_leave": can_team_leave,
        "can_team_expenses": can_team_expenses,
        "csrf_token": csrf_token,
        "notifications": notifications or [],
        # Org formatting settings for JS / template use
        "org_date_format": getattr(organization, "date_format", None)
        if organization
        else None,
        "org_number_format": getattr(organization, "number_format", None)
        if organization
        else None,
        "org_timezone": getattr(organization, "timezone", None)
        if organization
        else None,
    }
    if db and auth.organization_id and get_currency_context is not None:
        try:
            context.update(get_currency_context(db, str(auth.organization_id)))
        except Exception:
            logger.exception("Ignored exception")
    return context


@dataclass(frozen=True)
class WebPrincipal:
    """Lightweight principal for web routes."""

    id: UUID | None
    user_id: UUID | None
    person_id: UUID | None
    organization_id: UUID | None
    employee_id: UUID | None
    roles: list[str]
    scopes: list[str]


class WebAuthContext:
    """Authentication context for web routes."""

    def __init__(
        self,
        is_authenticated: bool = False,
        person_id: UUID | None = None,
        organization_id: UUID | None = None,
        employee_id: UUID | None = None,
        user_name: str = "Guest",
        user_initials: str = "GU",
        roles: list[str] | None = None,
        scopes: list[str] | None = None,
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
    def user_id(self) -> UUID | None:
        """Alias for person_id for backward compatibility."""
        return self.person_id

    @property
    def principal(self) -> WebPrincipal:
        """Provide a Principal-like object for services that require it."""
        return WebPrincipal(
            id=self.person_id,
            user_id=self.person_id,
            person_id=self.person_id,
            organization_id=self.organization_id,
            employee_id=self.employee_id,
            roles=list(self.roles),
            scopes=list(self.scopes),
        )

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
        if self.is_admin or "inventory:access" in scopes_set:
            modules.append("inventory")
        if self.is_admin or "fleet:access" in scopes_set:
            modules.append("fleet")
        if self.is_admin or "support:access" in scopes_set:
            modules.append("support")
        if self.is_admin or "procurement:access" in scopes_set:
            modules.append("procurement")
        if self.is_admin or "projects:access" in scopes_set:
            modules.append("projects")
        if self.is_admin or "settings:access" in scopes_set:
            modules.append("settings")
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
            "inventory": "inventory",
            "fleet": "fleet",
            "support": "support",
            "procurement": "procurement",
            "projects": "projects",
            "settings": "settings",
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
    def default_module(self) -> str | None:
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
            if module == "inventory":
                return "/inventory/items"
            if module == "fleet":
                return "/fleet"
            if module == "support":
                return "/support/dashboard"
            if module == "procurement":
                return "/procurement"
            if module == "projects":
                return "/projects"
            if module == "settings":
                return "/settings"
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


def _refresh_cookie_name(db: Session | None) -> str:
    try:
        name = AuthFlow.refresh_cookie_settings(db).get("key")
    except Exception:
        name = None
    return name or "refresh_token"


def _get_refresh_token_cookie(request: Request, db: Session | None) -> str | None:
    return request.cookies.get(_refresh_cookie_name(db))


def _resolve_session_from_refresh_token(
    db: Session,
    refresh_token: str,
    now: datetime,
) -> tuple[UUID, UUID] | None:
    """Resolve (person_id, session_id) from refresh token with SSO support."""
    token_hash = hash_session_token(refresh_token)
    auth_db = _get_auth_db_for_sso()
    try:
        target_db = auth_db if auth_db else db
        session = (
            target_db.query(AuthSession)
            .filter(AuthSession.token_hash == token_hash)
            .filter(AuthSession.status == SessionStatus.active)
            .filter(AuthSession.revoked_at.is_(None))
            .filter(AuthSession.expires_at > now)
            .first()
        )
        if not session or is_session_inactive(session, now):
            return None
        # Update session activity tracking
        if auth_db:
            session.last_seen_at = now
            auth_db.commit()
        else:
            session.last_seen_at = now
            db.flush()
        return session.person_id, session.id
    finally:
        if auth_db:
            auth_db.close()


def _normalize_roles_scopes(
    roles: list[str], scopes: list[str]
) -> tuple[list[str], list[str]]:
    normalized_roles = [
        str(role).strip().lower() for role in roles if str(role).strip()
    ]
    normalized_scopes = [
        str(scope).strip().lower() for scope in scopes if str(scope).strip()
    ]
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
    payload = None
    if token:
        try:
            # Decode token (uses SSO secret when SSO is enabled)
            payload = decode_access_token(db, token)
        except HTTPException:
            payload = None

    now = datetime.now(UTC)

    if payload:
        person_id = payload.get("sub")
        session_id = payload.get("session_id")

        if not person_id or not session_id:
            raise HTTPException(status_code=401, detail="Invalid token")

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
                raise HTTPException(
                    status_code=401, detail="Session expired or invalid"
                )

            # Check for activity timeout (session idle too long)
            if is_session_inactive(session, now):
                raise HTTPException(
                    status_code=401, detail="Session expired due to inactivity"
                )

            # Update session activity tracking
            if auth_db:
                session.last_seen_at = now
                auth_db.commit()
            else:
                session.last_seen_at = now
                db.flush()

        finally:
            if auth_db:
                auth_db.close()

        roles_value = payload.get("roles")
        roles = (
            [str(role) for role in roles_value] if isinstance(roles_value, list) else []
        )
        scopes_value = payload.get("scopes")
        scopes = (
            [str(scope) for scope in scopes_value]
            if isinstance(scopes_value, list)
            else []
        )
        roles, scopes = _normalize_roles_scopes(roles, scopes)
        roles = _ensure_admin_role(db, person_uuid, roles)
    else:
        refresh_token = _get_refresh_token_cookie(request, db)
        if not refresh_token:
            raise HTTPException(
                status_code=401,
                detail="Authentication required",
                headers={"WWW-Authenticate": "Bearer"},
            )
        resolved = _resolve_session_from_refresh_token(db, refresh_token, now)
        if not resolved:
            raise HTTPException(status_code=401, detail="Invalid or expired session")
        person_uuid, _ = resolved
        roles, scopes = _load_rbac_claims(db, str(person_uuid))
        roles, scopes = _normalize_roles_scopes(roles, scopes)
        roles = _ensure_admin_role(db, person_uuid, roles)

    # Get person details
    person = db.get(Person, person_uuid)
    if not person:
        raise HTTPException(status_code=401, detail="User not found")

    organization_id = person.organization_id

    # Set RLS context
    if organization_id:
        set_current_organization_sync(db, organization_id)
        request.state.organization_id = str(organization_id)

    request.state.actor_id = str(person_uuid)

    # Build user display info
    def _clean_name(value: str | None) -> str:
        cleaned = (value or "").strip()
        return "" if cleaned.lower() in {"none", "null"} else cleaned

    display_name = _clean_name(person.display_name)
    first_name = _clean_name(person.first_name)
    last_name = _clean_name(person.last_name)
    base_name = f"{first_name} {last_name}".strip()
    user_name = display_name or base_name or _clean_name(person.email) or "User"
    initials = (
        "".join(word[0].upper() for word in user_name.split()[:2])
        if user_name
        else "US"
    )

    # Look up employee_id for the person (may be None if person is not an employee)
    from sqlalchemy import select

    from app.models.people.hr.employee import Employee

    employee = db.scalar(select(Employee).where(Employee.person_id == person_uuid))
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
    payload = None
    if token:
        try:
            payload = decode_access_token(db, token)
        except HTTPException:
            payload = None

    now = datetime.now(UTC)

    if payload:
        person_id = payload.get("sub")
        session_id = payload.get("session_id")

        if not person_id or not session_id:
            return WebAuthContext(is_authenticated=False)

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

            # Update session activity tracking
            if auth_db:
                session.last_seen_at = now
                auth_db.commit()
            else:
                session.last_seen_at = now
                db.flush()

        finally:
            if auth_db:
                auth_db.close()

        roles_value = payload.get("roles")
        roles = (
            [str(role) for role in roles_value] if isinstance(roles_value, list) else []
        )
        scopes_value = payload.get("scopes")
        scopes = (
            [str(scope) for scope in scopes_value]
            if isinstance(scopes_value, list)
            else []
        )
        roles, scopes = _normalize_roles_scopes(roles, scopes)
        roles = _ensure_admin_role(db, person_uuid, roles)
    else:
        refresh_token = _get_refresh_token_cookie(request, db)
        if not refresh_token:
            return WebAuthContext(is_authenticated=False)
        resolved = _resolve_session_from_refresh_token(db, refresh_token, now)
        if not resolved:
            return WebAuthContext(is_authenticated=False)
        person_uuid, _ = resolved
        roles, scopes = _load_rbac_claims(db, str(person_uuid))
        roles, scopes = _normalize_roles_scopes(roles, scopes)
        roles = _ensure_admin_role(db, person_uuid, roles)

    # Get person details
    person = db.get(Person, person_uuid)
    if not person:
        return WebAuthContext(is_authenticated=False)

    organization_id = person.organization_id

    # Set RLS context
    if organization_id:
        set_current_organization_sync(db, organization_id)
        request.state.organization_id = str(organization_id)

    request.state.actor_id = str(person_uuid)

    # Build user display info
    def _clean_name(value: str | None) -> str:
        cleaned = (value or "").strip()
        return "" if cleaned.lower() in {"none", "null"} else cleaned

    display_name = _clean_name(person.display_name)
    first_name = _clean_name(person.first_name)
    last_name = _clean_name(person.last_name)
    base_name = f"{first_name} {last_name}".strip()
    user_name = display_name or base_name or _clean_name(person.email) or "User"
    initials = (
        "".join(word[0].upper() for word in user_name.split()[:2])
        if user_name
        else "US"
    )

    # Look up employee_id for the person (may be None if person is not an employee)
    from sqlalchemy import select

    from app.models.people.hr.employee import Employee

    employee = db.scalar(select(Employee).where(Employee.person_id == person_uuid))
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


def require_finance_admin(
    auth: WebAuthContext = Depends(require_finance_access),
) -> WebAuthContext:
    """
    Require finance admin access.

    Use this dependency for sensitive finance admin routes like opening balance.
    """
    if not auth.is_admin:
        raise HTTPException(
            status_code=403,
            detail="Finance admin access required",
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


def require_inventory_access(
    auth: WebAuthContext = Depends(require_web_auth),
) -> WebAuthContext:
    """Require access to the Inventory module."""
    if not auth.has_module_access("inventory"):
        raise HTTPException(
            status_code=403,
            detail="Inventory module access required",
        )
    return auth


def require_fleet_access(
    auth: WebAuthContext = Depends(require_web_auth),
) -> WebAuthContext:
    """Require access to the Fleet module."""
    if not auth.has_module_access("fleet"):
        raise HTTPException(
            status_code=403,
            detail="Fleet module access required",
        )
    return auth


def require_support_access(
    auth: WebAuthContext = Depends(require_web_auth),
) -> WebAuthContext:
    """Require access to the Support module."""
    if not auth.has_module_access("support"):
        raise HTTPException(
            status_code=403,
            detail="Support module access required",
        )
    return auth


def require_procurement_access(
    auth: WebAuthContext = Depends(require_web_auth),
) -> WebAuthContext:
    """Require access to the Procurement module."""
    if not auth.has_module_access("procurement"):
        raise HTTPException(
            status_code=403,
            detail="Procurement module access required",
        )
    return auth


def require_projects_access(
    auth: WebAuthContext = Depends(require_web_auth),
) -> WebAuthContext:
    """Require access to the Projects module."""
    if not auth.has_module_access("projects"):
        raise HTTPException(
            status_code=403,
            detail="Projects module access required",
        )
    return auth


def require_settings_access(
    auth: WebAuthContext = Depends(require_web_auth),
) -> WebAuthContext:
    """Require access to the Settings module."""
    if not auth.has_module_access("settings"):
        raise HTTPException(
            status_code=403,
            detail="Settings module access required",
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
    if not auth.has_module_access("self_service") and not auth.has_module_access(
        "people"
    ):
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
