import os
from datetime import datetime, timedelta, timezone
from typing import Optional, cast
from uuid import UUID

from fastapi import Cookie, Depends, Header, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.config import settings as app_settings
from app.db import SessionLocal, get_auth_db_session
from app.models.auth import ApiKey, Session as AuthSession, SessionStatus
from app.models.person import Person
from app.models.rbac import Permission, PersonRole, RolePermission, Role
from app.rls import set_current_organization_sync, enable_rls_bypass_sync
from app.services.auth import hash_api_key
from app.services.auth_flow import decode_access_token, hash_session_token
from app.services.common import coerce_uuid

# Cookie name for web session
WEB_SESSION_COOKIE = "session_token"

# Session activity timeout in days - sessions inactive longer than this are considered expired
# Default: 7 days. Override with SESSION_ACTIVITY_TIMEOUT_DAYS env var.
SESSION_ACTIVITY_TIMEOUT_DAYS = int(os.getenv("SESSION_ACTIVITY_TIMEOUT_DAYS", "7"))


def _get_auth_db_for_sso() -> Session | None:
    """Get auth database session for SSO validation.

    When SSO is enabled and this is an SSO client (not provider),
    returns a session to the shared auth database.
    When SSO is disabled or this is the SSO provider, returns None
    (use main database instead).
    """
    if app_settings.sso_enabled and not app_settings.sso_provider_mode:
        return get_auth_db_session()
    return None


def _validate_session_sso(
    session_id: UUID,
    person_id: UUID,
    now: datetime,
    auth_db: Session,
) -> AuthSession | None:
    """Validate session against SSO auth database.

    Args:
        session_id: Session UUID to validate
        person_id: Person UUID to match
        now: Current timestamp (must be timezone-aware)
        auth_db: Auth database session

    Returns:
        AuthSession if valid, None if invalid
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


def _decode_token_for_sso(token: str, db: Session | None = None) -> dict:
    """Decode access token with SSO-aware secret.

    When SSO is enabled, uses the shared SSO JWT secret.
    """
    # The decode_access_token function already uses _jwt_secret
    # which has been updated to use SSO secret when enabled
    if db:
        return decode_access_token(db, token)

    # If no db session provided, create a temporary one for decoding
    temp_db = SessionLocal()
    try:
        return decode_access_token(temp_db, token)
    finally:
        temp_db.close()


def is_session_inactive(session: AuthSession, now: datetime) -> bool:
    """Check if a session has been inactive for too long.

    A session is considered inactive if last_seen_at is older than
    SESSION_ACTIVITY_TIMEOUT_DAYS. This provides an additional security
    layer on top of absolute token expiration.
    """
    if SESSION_ACTIVITY_TIMEOUT_DAYS <= 0:
        # Activity timeout disabled
        return False

    if session.last_seen_at is None:
        # No activity recorded yet, use created_at
        last_activity = _make_aware(session.created_at)
    else:
        last_activity = _make_aware(session.last_seen_at)

    if last_activity is None:
        return False

    timeout = timedelta(days=SESSION_ACTIVITY_TIMEOUT_DAYS)
    return now - last_activity > timeout


def get_current_user_id(
    authorization: str | None = Header(default=None),
    db: Session = Depends(lambda: SessionLocal()),
) -> UUID:
    """
    Dependency to get the current authenticated user's ID from JWT token.

    For API routes only. Web routes should use require_web_auth instead.
    Raises 401 if not authenticated.

    SSO Support: Validates session against shared auth database for SSO clients.
    """
    token = _extract_bearer_token(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="Unauthorized")

    payload = decode_access_token(db, token)
    person_id = payload.get("sub")
    session_id = payload.get("session_id")
    if not person_id or not session_id:
        raise HTTPException(status_code=401, detail="Unauthorized")

    now = datetime.now(timezone.utc)
    person_uuid = cast(UUID, coerce_uuid(person_id))
    session_uuid = coerce_uuid(session_id)

    # SSO: validate session against shared auth database
    auth_db = _get_auth_db_for_sso()
    try:
        if auth_db:
            session = _validate_session_sso(session_uuid, person_uuid, now, auth_db)
        else:
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
            raise HTTPException(status_code=401, detail="Unauthorized")
        if is_session_inactive(session, now):
            raise HTTPException(status_code=401, detail="Session expired due to inactivity")

    finally:
        if auth_db:
            auth_db.close()

    return person_uuid


def get_current_org_id(
    authorization: str | None = Header(default=None),
    db: Session = Depends(lambda: SessionLocal()),
) -> UUID:
    """
    Dependency to get the current authenticated user's organization ID.

    For API routes only. Web routes should use require_web_auth instead.
    Raises 401 if not authenticated, 400 if user has no organization.

    SSO Support: Validates session against shared auth database for SSO clients.
    """
    token = _extract_bearer_token(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="Unauthorized")

    payload = decode_access_token(db, token)
    person_id = payload.get("sub")
    session_id = payload.get("session_id")
    if not person_id or not session_id:
        raise HTTPException(status_code=401, detail="Unauthorized")

    now = datetime.now(timezone.utc)
    person_uuid = coerce_uuid(person_id)
    session_uuid = coerce_uuid(session_id)

    # SSO: validate session against shared auth database
    auth_db = _get_auth_db_for_sso()
    try:
        if auth_db:
            session = _validate_session_sso(session_uuid, person_uuid, now, auth_db)
        else:
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
            raise HTTPException(status_code=401, detail="Unauthorized")
        if is_session_inactive(session, now):
            raise HTTPException(status_code=401, detail="Session expired due to inactivity")

    finally:
        if auth_db:
            auth_db.close()

    person = db.get(Person, person_uuid)
    if not person or not person.organization_id:
        raise HTTPException(status_code=400, detail="User has no organization")

    return person.organization_id


def _make_aware(dt: datetime) -> datetime:
    """Ensure datetime is timezone-aware (UTC). SQLite doesn't preserve tz info."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _extract_bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    parts = authorization.split(" ", 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return None


def _is_jwt(token: str) -> bool:
    return token.count(".") == 2


def _has_audit_scope(payload: dict) -> bool:
    scopes: set[str] = set()
    scope_value = payload.get("scope")
    if isinstance(scope_value, str):
        scopes.update(scope_value.split())
    scopes_value = payload.get("scopes")
    if isinstance(scopes_value, list):
        scopes.update(str(item) for item in scopes_value)
    role_value = payload.get("role")
    roles_value = payload.get("roles")
    roles: set[str] = set()
    if isinstance(role_value, str):
        roles.add(role_value)
    if isinstance(roles_value, list):
        roles.update(str(item) for item in roles_value)
    return (
        "audit:read" in scopes
        or "audit:*" in scopes
        or "admin" in roles
        or "auditor" in roles
    )


def require_audit_auth(
    authorization: str | None = Header(default=None),
    x_session_token: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
    request: Request = None,
    db: Session = Depends(_get_db),
):
    """Authenticate for audit access with SSO support.

    Supports JWT tokens, session tokens, and API keys.
    """
    token = _extract_bearer_token(authorization) or x_session_token
    now = datetime.now(timezone.utc)
    if token:
        if _is_jwt(token):
            payload = decode_access_token(db, token)
            if not _has_audit_scope(payload):
                raise HTTPException(status_code=403, detail="Insufficient scope")
            session_id = payload.get("session_id")
            person_id = payload.get("sub")
            if session_id and person_id:
                # SSO: validate session against shared auth database
                auth_db = _get_auth_db_for_sso()
                try:
                    session_uuid = coerce_uuid(session_id)
                    person_uuid = coerce_uuid(person_id)
                    if auth_db:
                        session = _validate_session_sso(session_uuid, person_uuid, now, auth_db)
                    else:
                        session = db.get(AuthSession, session_uuid)

                    if not session:
                        raise HTTPException(status_code=401, detail="Invalid session")
                    if session.status != SessionStatus.active or session.revoked_at:
                        raise HTTPException(status_code=401, detail="Invalid session")
                    if _make_aware(session.expires_at) <= now:
                        raise HTTPException(status_code=401, detail="Session expired")
                finally:
                    if auth_db:
                        auth_db.close()

            actor_id = str(person_id)
            if request is not None:
                request.state.actor_id = actor_id
            return {"actor_type": "user", "actor_id": actor_id}

        # Session token (hash-based) - requires local database lookup
        # For SSO clients, session tokens should be validated against shared DB
        auth_db = _get_auth_db_for_sso()
        try:
            target_db = auth_db if auth_db else db
            session = (
                target_db.query(AuthSession)
                .filter(AuthSession.token_hash == hash_session_token(token))
                .filter(AuthSession.status == SessionStatus.active)
                .filter(AuthSession.revoked_at.is_(None))
                .filter(AuthSession.expires_at > now)
                .first()
            )
            if session:
                if request is not None:
                    request.state.actor_id = str(session.person_id)
                return {"actor_type": "user", "actor_id": str(session.person_id)}
        finally:
            if auth_db:
                auth_db.close()

    if x_api_key:
        api_key = (
            db.query(ApiKey)
            .filter(ApiKey.key_hash == hash_api_key(x_api_key))
            .filter(ApiKey.is_active.is_(True))
            .filter(ApiKey.revoked_at.is_(None))
            .filter((ApiKey.expires_at.is_(None)) | (ApiKey.expires_at > now))
            .first()
        )
        if api_key:
            if request is not None:
                request.state.actor_id = str(api_key.id)
            return {"actor_type": "api_key", "actor_id": str(api_key.id)}
    raise HTTPException(status_code=401, detail="Unauthorized")


def require_user_auth(
    authorization: str | None = Header(default=None),
    request: Request = None,
    db: Session = Depends(_get_db),
):
    """Authenticate user from JWT token with SSO support.

    When SSO is enabled and this is an SSO client, validates the session
    against the shared auth database on the SSO provider.
    """
    # Try Authorization header first, then fall back to cookie
    token = _extract_bearer_token(authorization)
    if not token and request is not None:
        token = request.cookies.get("access_token")
    if not token:
        raise HTTPException(status_code=401, detail="Unauthorized")

    # Decode token (uses SSO secret when SSO is enabled)
    payload = decode_access_token(db, token)
    person_id = payload.get("sub")
    session_id = payload.get("session_id")
    if not person_id or not session_id:
        raise HTTPException(status_code=401, detail="Unauthorized")

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
            raise HTTPException(status_code=401, detail="Unauthorized")

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

    roles_value = payload.get("roles")
    scopes_value = payload.get("scopes")
    roles = [str(role) for role in roles_value] if isinstance(roles_value, list) else []
    scopes = [str(scope) for scope in scopes_value] if isinstance(scopes_value, list) else []
    actor_id = str(person_id)
    if request is not None:
        request.state.actor_id = actor_id
    return {
        "person_id": str(person_id),
        "session_id": str(session_id),
        "roles": roles,
        "scopes": scopes,
    }


def require_role(role_name: str):
    def _require_role(
        auth=Depends(require_user_auth),
        db: Session = Depends(_get_db),
    ):
        person_id = coerce_uuid(auth["person_id"])
        roles = set(auth.get("roles") or [])
        if role_name in roles:
            return auth
        role = (
            db.query(Role)
            .filter(Role.name == role_name)
            .filter(Role.is_active.is_(True))
            .first()
        )
        if not role:
            raise HTTPException(status_code=403, detail="Role not found")
        link = (
            db.query(PersonRole)
            .filter(PersonRole.person_id == person_id)
            .filter(PersonRole.role_id == role.id)
            .first()
        )
        if not link:
            raise HTTPException(status_code=403, detail="Forbidden")
        return auth

    return _require_role


def require_permission(permission_key: str):
    def _require_permission(
        auth=Depends(require_user_auth),
        db: Session = Depends(_get_db),
    ):
        person_id = coerce_uuid(auth["person_id"])
        roles = set(auth.get("roles") or [])
        scopes = set(auth.get("scopes") or [])
        if "admin" in roles or permission_key in scopes:
            return auth
        permission = (
            db.query(Permission)
            .filter(Permission.key == permission_key)
            .filter(Permission.is_active.is_(True))
            .first()
        )
        if not permission:
            raise HTTPException(status_code=403, detail="Permission not found")
        has_permission = (
            db.query(RolePermission)
            .join(Role, RolePermission.role_id == Role.id)
            .join(PersonRole, PersonRole.role_id == Role.id)
            .filter(PersonRole.person_id == person_id)
            .filter(RolePermission.permission_id == permission.id)
            .filter(Role.is_active.is_(True))
            .first()
        )
        if not has_permission:
            raise HTTPException(status_code=403, detail="Forbidden")
        return auth

    return _require_permission


def require_tenant_auth(
    authorization: str | None = Header(default=None),
    access_token: str | None = Cookie(default=None),
    request: Request = None,
    db: Session = Depends(_get_db),
):
    """
    Authenticate user and set RLS tenant context with SSO support.

    This dependency:
    1. Validates the user's JWT token (using SSO secret when enabled)
    2. Validates session against shared auth database (for SSO clients)
    3. Looks up the user's organization_id
    4. Sets the PostgreSQL session variable for RLS
    5. Returns auth dict with organization_id included

    Usage:
        @app.get("/items")
        def list_items(auth=Depends(require_tenant_auth), db: Session = Depends(get_db)):
            # All queries in this request are automatically scoped to the user's org
            return db.query(Item).all()
    """
    token = _extract_bearer_token(authorization) or access_token
    if not token:
        raise HTTPException(status_code=401, detail="Unauthorized")

    # Decode token (uses SSO secret when SSO is enabled)
    payload = decode_access_token(db, token)
    person_id = payload.get("sub")
    session_id = payload.get("session_id")
    if not person_id or not session_id:
        raise HTTPException(status_code=401, detail="Unauthorized")

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
            raise HTTPException(status_code=401, detail="Unauthorized")

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

    # Look up the user's organization (or use default if single-org mode)
    person = db.get(Person, person_uuid)
    organization_id = person.organization_id if person else None

    # Single-org mode: use default org if configured
    if not organization_id and app_settings.default_organization_id:
        organization_id = coerce_uuid(app_settings.default_organization_id)

    if not organization_id:
        raise HTTPException(status_code=403, detail="Organization access required")

    # Set RLS context if user has an organization
    if organization_id:
        set_current_organization_sync(db, organization_id)
        if request is not None:
            request.state.organization_id = str(organization_id)

    roles_value = payload.get("roles")
    scopes_value = payload.get("scopes")
    roles = [str(role) for role in roles_value] if isinstance(roles_value, list) else []
    scopes = [str(scope) for scope in scopes_value] if isinstance(scopes_value, list) else []
    actor_id = str(person_id)
    if request is not None:
        request.state.actor_id = actor_id
    return {
        "person_id": str(person_id),
        "session_id": str(session_id),
        "organization_id": str(organization_id) if organization_id else None,
        "roles": roles,
        "scopes": scopes,
    }


def require_tenant_role(role_name: str):
    """
    Require a specific role with tenant context set.

    Combines require_tenant_auth with role checking.
    """
    def _require_tenant_role(
        auth=Depends(require_tenant_auth),
        db: Session = Depends(_get_db),
    ):
        person_id = coerce_uuid(auth["person_id"])
        roles = set(auth.get("roles") or [])
        if role_name in roles:
            return auth
        role = (
            db.query(Role)
            .filter(Role.name == role_name)
            .filter(Role.is_active.is_(True))
            .first()
        )
        if not role:
            raise HTTPException(status_code=403, detail="Role not found")
        link = (
            db.query(PersonRole)
            .filter(PersonRole.person_id == person_id)
            .filter(PersonRole.role_id == role.id)
            .first()
        )
        if not link:
            raise HTTPException(status_code=403, detail="Forbidden")
        return auth

    return _require_tenant_role


def require_tenant_permission(permission_key: str):
    """
    Require a specific permission with tenant context set.

    Combines require_tenant_auth with permission checking.
    """
    def _require_tenant_permission(
        auth=Depends(require_tenant_auth),
        db: Session = Depends(_get_db),
    ):
        person_id = coerce_uuid(auth["person_id"])
        roles = set(auth.get("roles") or [])
        scopes = set(auth.get("scopes") or [])
        if "admin" in roles or permission_key in scopes:
            return auth
        permission = (
            db.query(Permission)
            .filter(Permission.key == permission_key)
            .filter(Permission.is_active.is_(True))
            .first()
        )
        if not permission:
            raise HTTPException(status_code=403, detail="Permission not found")
        has_permission = (
            db.query(RolePermission)
            .join(Role, RolePermission.role_id == Role.id)
            .join(PersonRole, PersonRole.role_id == Role.id)
            .filter(PersonRole.person_id == person_id)
            .filter(RolePermission.permission_id == permission.id)
            .filter(Role.is_active.is_(True))
            .first()
        )
        if not has_permission:
            raise HTTPException(status_code=403, detail="Forbidden")
        return auth

    return _require_tenant_permission


def require_admin_bypass(
    authorization: str | None = Header(default=None),
    request: Request = None,
    db: Session = Depends(_get_db),
):
    """
    Admin-only dependency that bypasses RLS with SSO support.

    Use this for system administration endpoints that need to see
    data across all tenants. Requires the 'admin' role.

    WARNING: Use with extreme caution! This bypasses tenant isolation.

    Usage:
        @app.get("/admin/all-organizations")
        def list_all_orgs(auth=Depends(require_admin_bypass), db: Session = Depends(get_db)):
            # Can see all organizations across tenants
            return db.query(Organization).all()
    """
    token = _extract_bearer_token(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="Unauthorized")
    payload = decode_access_token(db, token)
    person_id = payload.get("sub")
    session_id = payload.get("session_id")
    if not person_id or not session_id:
        raise HTTPException(status_code=401, detail="Unauthorized")

    now = datetime.now(timezone.utc)
    person_uuid = coerce_uuid(person_id)
    session_uuid = coerce_uuid(session_id)

    # SSO: validate session against shared auth database
    auth_db = _get_auth_db_for_sso()
    try:
        if auth_db:
            session = _validate_session_sso(session_uuid, person_uuid, now, auth_db)
        else:
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
            raise HTTPException(status_code=401, detail="Unauthorized")
        # Check for activity timeout (session idle too long)
        if is_session_inactive(session, now):
            raise HTTPException(status_code=401, detail="Session expired due to inactivity")
    finally:
        if auth_db:
            auth_db.close()

    # Check for admin role
    roles_value = payload.get("roles")
    roles = [str(role) for role in roles_value] if isinstance(roles_value, list) else []
    if "admin" not in roles:
        # Also check database for admin role
        admin_role = (
            db.query(Role)
            .filter(Role.name == "admin")
            .filter(Role.is_active.is_(True))
            .first()
        )
        if admin_role:
            link = (
                db.query(PersonRole)
                .filter(PersonRole.person_id == person_uuid)
                .filter(PersonRole.role_id == admin_role.id)
                .first()
            )
            if not link:
                raise HTTPException(status_code=403, detail="Admin access required")
        else:
            raise HTTPException(status_code=403, detail="Admin access required")

    # Enable RLS bypass for admin operations
    enable_rls_bypass_sync(db)

    scopes_value = payload.get("scopes")
    scopes = [str(scope) for scope in scopes_value] if isinstance(scopes_value, list) else []
    actor_id = str(person_id)
    if request is not None:
        request.state.actor_id = actor_id
        request.state.is_admin_bypass = True
    return {
        "person_id": str(person_id),
        "session_id": str(session_id),
        "organization_id": None,  # Not scoped to an org
        "roles": roles,
        "scopes": scopes,
        "is_admin_bypass": True,
    }


def _resolve_web_session_from_access_token(
    db: Session,
    access_token: str,
    now: datetime,
) -> tuple[AuthSession, Person] | None:
    """Resolve web session from access token with SSO support."""
    try:
        payload = decode_access_token(db, access_token)
    except HTTPException:
        return None
    person_id = payload.get("sub")
    session_id = payload.get("session_id")
    if not person_id or not session_id:
        return None

    person_uuid = coerce_uuid(person_id)
    session_uuid = coerce_uuid(session_id)

    # SSO: validate session against shared auth database
    auth_db = _get_auth_db_for_sso()
    try:
        if auth_db:
            session = _validate_session_sso(session_uuid, person_uuid, now, auth_db)
        else:
            session = (
                db.query(AuthSession)
                .filter(AuthSession.id == session_uuid)
                .filter(AuthSession.person_id == person_uuid)
                .filter(AuthSession.status == SessionStatus.active)
                .filter(AuthSession.revoked_at.is_(None))
                .filter(AuthSession.expires_at > now)
                .first()
            )

        if not session or is_session_inactive(session, now):
            return None
    finally:
        if auth_db:
            auth_db.close()

    person = db.get(Person, person_uuid)
    if not person:
        return None

    return session, person


def require_web_session(
    request: Request,
    session_token: Optional[str] = Cookie(default=None, alias=WEB_SESSION_COOKIE),
    access_token: Optional[str] = Cookie(default=None),
    db: Session = Depends(_get_db),
):
    """
    Web session authentication for HTML routes with SSO support.

    This dependency:
    1. Reads the session token from a cookie
    2. Validates the session against the database (or shared auth DB for SSO)
    3. Looks up the user's organization_id
    4. Sets the PostgreSQL session variable for RLS
    5. Returns auth dict with user and organization info

    If authentication fails, redirects to login page instead of returning 401.

    Usage:
        @app.get("/dashboard", response_class=HTMLResponse)
        def dashboard(request: Request, auth=Depends(require_web_session)):
            # User is authenticated, org context is set
            return templates.TemplateResponse(request, "dashboard.html", {"user": auth})
    """
    if not session_token and not access_token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    now = datetime.now(timezone.utc)
    session = None
    person = None

    if session_token:
        # SSO: validate session token against shared auth database
        auth_db = _get_auth_db_for_sso()
        try:
            target_db = auth_db if auth_db else db
            session = (
                target_db.query(AuthSession)
                .filter(AuthSession.token_hash == hash_session_token(session_token))
                .filter(AuthSession.status == SessionStatus.active)
                .filter(AuthSession.revoked_at.is_(None))
                .filter(AuthSession.expires_at > now)
                .first()
            )

            if not session or is_session_inactive(session, now):
                session = None
            else:
                # Get person from main database (not auth database)
                person = db.get(Person, session.person_id)
        finally:
            if auth_db:
                auth_db.close()

    if not session and access_token:
        resolved = _resolve_web_session_from_access_token(db, access_token, now)
        if resolved:
            session, person = resolved

    if not session or not person:
        raise HTTPException(status_code=401, detail="Invalid or expired session")

    # Get organization from person or use default (single-org mode)
    from app.config import settings

    organization_id = person.organization_id
    if not organization_id and settings.default_organization_id:
        organization_id = coerce_uuid(settings.default_organization_id)

    # Set RLS context if user has an organization
    if organization_id:
        set_current_organization_sync(db, organization_id)
        request.state.organization_id = str(organization_id)

    request.state.actor_id = str(person.id)

    def _clean_name(value: Optional[str]) -> str:
        cleaned = (value or "").strip()
        return "" if cleaned.lower() in {"none", "null"} else cleaned

    display_name = _clean_name(person.display_name)
    first_name = _clean_name(person.first_name)
    last_name = _clean_name(person.last_name)
    base_name = f"{first_name} {last_name}".strip()
    user_name = display_name or base_name or _clean_name(person.email) or "User"

    return {
        "person_id": str(person.id),
        "session_id": str(session.id),
        "organization_id": str(organization_id) if organization_id else None,
        "user_name": user_name,
        "user_initials": _get_initials(person),
    }


def _get_initials(person: Person) -> str:
    """Get user initials from person record."""
    def _clean_name(value: Optional[str]) -> str:
        cleaned = (value or "").strip()
        return "" if cleaned.lower() in {"none", "null"} else cleaned

    first_name = _clean_name(person.first_name)
    last_name = _clean_name(person.last_name)
    display_name = _clean_name(person.display_name)

    if first_name and last_name:
        return f"{first_name[0]}{last_name[0]}".upper()
    if display_name:
        parts = display_name.split()
        if len(parts) >= 2:
            return f"{parts[0][0]}{parts[-1][0]}".upper()
        return display_name[:2].upper()
    return "??"


def optional_web_session(
    request: Request,
    session_token: Optional[str] = Cookie(default=None, alias=WEB_SESSION_COOKIE),
    access_token: Optional[str] = Cookie(default=None),
    db: Session = Depends(_get_db),
):
    """
    Optional web session authentication with SSO support.

    Like require_web_session but returns None instead of raising an exception
    when not authenticated. Useful for pages that work with or without auth.

    Usage:
        @app.get("/public-page", response_class=HTMLResponse)
        def public_page(request: Request, auth=Depends(optional_web_session)):
            if auth:
                # User is logged in
                ...
            else:
                # Anonymous user
                ...
    """
    if not session_token and not access_token:
        return None

    now = datetime.now(timezone.utc)
    session = None
    person = None

    if session_token:
        # SSO: validate session token against shared auth database
        auth_db = _get_auth_db_for_sso()
        try:
            target_db = auth_db if auth_db else db
            session = (
                target_db.query(AuthSession)
                .filter(AuthSession.token_hash == hash_session_token(session_token))
                .filter(AuthSession.status == SessionStatus.active)
                .filter(AuthSession.revoked_at.is_(None))
                .filter(AuthSession.expires_at > now)
                .first()
            )

            if not session or is_session_inactive(session, now):
                session = None
            else:
                # Get person from main database (not auth database)
                person = db.get(Person, session.person_id)
        finally:
            if auth_db:
                auth_db.close()

    if not session and access_token:
        resolved = _resolve_web_session_from_access_token(db, access_token, now)
        if resolved:
            session, person = resolved

    if not session or not person:
        return None

    # Get organization from person or use default (single-org mode)
    from app.config import settings

    organization_id = person.organization_id
    if not organization_id and settings.default_organization_id:
        organization_id = coerce_uuid(settings.default_organization_id)

    if organization_id:
        set_current_organization_sync(db, organization_id)
        request.state.organization_id = str(organization_id)

    request.state.actor_id = str(person.id)

    def _clean_name(value: Optional[str]) -> str:
        cleaned = (value or "").strip()
        return "" if cleaned.lower() in {"none", "null"} else cleaned

    display_name = _clean_name(person.display_name)
    first_name = _clean_name(person.first_name)
    last_name = _clean_name(person.last_name)
    base_name = f"{first_name} {last_name}".strip()
    user_name = display_name or base_name or _clean_name(person.email) or "User"

    return {
        "person_id": str(person.id),
        "session_id": str(session.id),
        "organization_id": str(organization_id) if organization_id else None,
        "user_name": user_name,
        "user_initials": _get_initials(person),
    }
