from uuid import UUID

from fastapi import Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.db.session_context import prime_session
from app.rls import enable_rls_bypass_sync, set_current_organization_sync
from app.services.auth_dependencies import (
    optional_web_session,
    require_admin_bypass,
    require_audit_auth,
    require_permission,
    require_role,
    require_tenant_auth,
    require_tenant_permission,
    require_tenant_role,
    require_user_auth,
    require_web_session,
)
from app.services.common import coerce_uuid
from app.services.feature_flags import (
    FEATURE_BANK_RECONCILIATION,
    FEATURE_BUDGETING,
    FEATURE_FIXED_ASSETS,
    FEATURE_INVENTORY,
    FEATURE_LEASES,
    FEATURE_MULTI_CURRENCY,
    FEATURE_PROJECT_ACCOUNTING,
    FEATURE_RECURRING_TRANSACTIONS,
    is_feature_enabled,
    require_feature,
)

__all__ = [
    "require_audit_auth",
    "require_permission",
    "require_role",
    "require_user_auth",
    "require_tenant_auth",
    "require_tenant_role",
    "require_tenant_permission",
    "require_organization_id",
    "get_db_with_org",
    "get_db_admin_bypass",
    "get_db_auth_bypass",
    "require_current_employee_id",
    "get_current_employee_id_optional",
    "require_admin_bypass",
    "require_web_session",
    "optional_web_session",
    "require_feature",
    "is_feature_enabled",
    "FEATURE_INVENTORY",
    "FEATURE_FIXED_ASSETS",
    "FEATURE_LEASES",
    "FEATURE_BUDGETING",
    "FEATURE_MULTI_CURRENCY",
    "FEATURE_PROJECT_ACCOUNTING",
    "FEATURE_BANK_RECONCILIATION",
    "FEATURE_RECURRING_TRANSACTIONS",
]


def _get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_db_with_org(
    auth: dict = Depends(require_tenant_auth),
):
    """DB session dependency for tenant-scoped API routes.

    Yields a Session with the request's organization_id pinned in *both*
    Python-side (``session.info["organization_id"]``, read by the
    ``do_orm_execute`` listener at ``app/db/org_listener.py`` when
    ``settings.enforce_org_filter`` is on — currently False; the
    listener is shipped but gated) and PostgreSQL-side
    (``app.current_organization_id`` GUC, consumed by RLS policies on
    every org-scoped table — active today).

    Use this in place of any per-module ``get_db``. Without it,
    ``select(Foo).where(Foo.organization_id == X)`` on an RLS-protected
    schema silently returns zero rows because the policy
    ``organization_id = get_current_organization_id()`` evaluates to NULL
    when the GUC is unset. See ``app/db/multi_tenant.py`` and
    ``app/db/org_listener.py`` for the listener side.

    Auto-commits on successful yield, rolls back on exception — matches
    the historical per-module ``get_db`` behavior so migrations don't
    change route semantics.
    """
    organization_id_str = auth.get("organization_id")
    if not organization_id_str:
        raise HTTPException(status_code=403, detail="Organization access required")
    organization_id = UUID(organization_id_str)

    db = SessionLocal()
    try:
        prime_session(db, organization_id)
        set_current_organization_sync(db, organization_id)
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _yield_bypass_session():
    db = SessionLocal()
    try:
        enable_rls_bypass_sync(db)
        db.info["allow_cross_org"] = True
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_db_auth_bypass():
    """DB session dependency for authentication bootstrap flows.

    Login, refresh, MFA verification, and password reset routes must look
    up users/sessions before an authenticated tenant context exists. Keep
    those flows explicit instead of using the admin bypass dependency, which
    now requires an authenticated caller.
    """
    yield from _yield_bypass_session()


def get_db_admin_bypass(auth: dict = Depends(require_user_auth)):
    """DB session dependency for genuinely cross-tenant admin routes.

    Yields a Session that bypasses tenant scoping at *both* layers:
    - PostgreSQL: ``SET LOCAL app.bypass_rls = 'true'`` makes the RLS
      policies return rows regardless of GUC (the policies are
      ``should_bypass_rls() OR organization_id = get_current_org_id()``).
    - Python: ``session.info["allow_cross_org"] = True`` tells the
      ``do_orm_execute`` listener (when enabled) to skip its
      WHERE-injection — otherwise it would raise
      MissingOrgContextError on every org-scoped SELECT.

    Use only for routes that genuinely operate across all tenants:
    super-admin audit log views, system maintenance endpoints, etc.
    Routes that operate within a single org should depend on
    ``get_db_with_org`` instead — they get RLS protection for free.

    Requires an authenticated caller in its own signature so an accidental
    route use cannot expose unauthenticated cross-tenant access. Callers
    must still pair it with a narrower authorization gate such as
    ``require_audit_auth`` or ``require_permission(...)``.

    Auto-commits on successful yield, rolls back on exception.
    """
    _ = auth
    yield from _yield_bypass_session()


def require_organization_id(auth: dict = Depends(require_tenant_auth)) -> UUID:
    """Return the authenticated user's organization_id as a UUID."""
    organization_id = auth.get("organization_id")
    if not organization_id:
        raise HTTPException(status_code=403, detail="Organization access required")
    return UUID(organization_id)


def require_current_employee_id(
    auth: dict = Depends(require_tenant_auth),
    db: Session = Depends(get_db_with_org),
) -> UUID:
    """
    Return the employee_id for the authenticated user.

    Looks up the Employee record linked to the current Person (user).
    Raises 403 if the user is not linked to an employee record.

    Uses ``get_db_with_org`` (not the bare ``_get_db``) so the SELECT
    against RLS-protected ``hr.employee`` runs on an explicitly-primed
    session. The previous ``Depends(_get_db)`` shape worked in
    production via FastAPI's dep-cache shared session, but only by
    accident — any future refactor that introduces another ``get_db``
    variant would silently break this. ``require_tenant_auth`` is
    deduplicated against the one inside ``get_db_with_org`` so this
    still resolves to a single auth call per request.
    """
    from app.models.people.hr.employee import Employee

    person_id = auth.get("person_id")
    if not person_id:
        raise HTTPException(status_code=401, detail="Unauthorized")

    person_uuid = coerce_uuid(person_id)
    employee = db.scalar(select(Employee).where(Employee.person_id == person_uuid))
    if not employee:
        raise HTTPException(
            status_code=403, detail="No employee record linked to this user account"
        )
    return employee.employee_id


def get_current_employee_id_optional(
    auth: dict = Depends(require_tenant_auth),
    db: Session = Depends(get_db_with_org),
) -> UUID | None:
    """
    Return the employee_id for the authenticated user, or None if not linked.

    Used for endpoints where employee_id is optional (e.g., admin actions).
    """
    from app.models.people.hr.employee import Employee

    person_id = auth.get("person_id")
    if not person_id:
        return None

    person_uuid = coerce_uuid(person_id)
    employee = db.scalar(select(Employee).where(Employee.person_id == person_uuid))
    return employee.employee_id if employee else None
