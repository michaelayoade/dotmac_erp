"""
Row Level Security (RLS) context utilities.

This module is the one writer for ERP and shared-module PostgreSQL tenant scope.

ERP's Organization UUID maps directly to the shared Tenant UUID. Every tenant
primer sets ``app.current_organization_id`` and ``app.current_tenant`` in one
transaction-local statement. Runtime code cannot assert a PostgreSQL RLS
bypass. The separate ORM-listener boundary in ``app.db.session_context`` permits
application-layer cross-organization reads only where PostgreSQL RLS does not
require tenant context; it is not database authority.

Usage:
    # In a request middleware or dependency:
    async def set_tenant_context(
        db: AsyncSession,
        organization_id: UUID
    ):
        await set_current_organization(db, organization_id)

    # Or using the context manager:
    async with tenant_context(db, organization_id):
        # queries here are scoped to the organization
        pass
"""

import re
import uuid
from collections.abc import AsyncGenerator, Generator
from contextlib import asynccontextmanager, contextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from app.tenancy import OrganizationTenantContext

# Pattern for validating UUID strings - only allows hex digits and hyphens
# This prevents SQL injection since these characters cannot break out of the string
_UUID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)

_SET_CURRENT_SCOPE_SQL = text(
    """
    SELECT
        set_config('app.current_organization_id', :organization_id, true),
        set_config('app.current_tenant', :tenant_id, true)
    """
)
_CLEAR_CURRENT_SCOPE_SQL = text(
    """
    SELECT
        set_config('app.current_organization_id', '', true),
        set_config('app.current_tenant', '', true)
    """
)


def _validate_uuid_string(value: str) -> str:
    """
    Validate that a string is a valid UUID format.

    This ensures only valid UUID strings are used in SQL, preventing any
    possibility of SQL injection through this path.

    Args:
        value: The string to validate

    Returns:
        The validated string

    Raises:
        ValueError: If the string is not a valid UUID format
    """
    if not _UUID_PATTERN.match(value):
        raise ValueError(f"Invalid UUID format: {value}")
    return value


def _scope_params(organization_id: uuid.UUID) -> dict[str, str]:
    """Resolve ERP and shared-module scope from the one mapping owner."""
    context = OrganizationTenantContext.for_organization(organization_id)
    return {
        "organization_id": _validate_uuid_string(str(context.organization_id)),
        "tenant_id": _validate_uuid_string(str(context.tenant_id)),
    }


async def set_current_organization(
    db: AsyncSession,
    organization_id: uuid.UUID,
) -> None:
    """
    Set ERP organization and shared-module tenant context for RLS policies.

    This should be called at the beginning of each request/transaction
    to scope all subsequent queries to the specified organization.

    Args:
        db: The database session
        organization_id: The UUID of the current organization/tenant

    Both transaction-local GUCs are set by one SQL statement so a session can
    never enter a half-primed organization/tenant state. ERP policies continue
    to read ``app.current_organization_id``; shared modules read
    ``app.current_tenant``.
    """
    await db.execute(_SET_CURRENT_SCOPE_SQL, _scope_params(organization_id))


async def clear_organization_context(db: AsyncSession) -> None:
    """
    Clear the current organization context.

    Args:
        db: The database session
    """
    await db.execute(_CLEAR_CURRENT_SCOPE_SQL)


@asynccontextmanager
async def tenant_context(
    db: AsyncSession,
    organization_id: uuid.UUID,
) -> AsyncGenerator[None, None]:
    """
    Context manager to set tenant context for a block of code.

    Usage:
        async with tenant_context(db, org_id):
            # All queries here are scoped to org_id
            data = await db.execute(select(SomeModel))

    Args:
        db: The database session
        organization_id: The UUID of the organization/tenant
    """
    await set_current_organization(db, organization_id)
    try:
        yield
    finally:
        await clear_organization_context(db)


async def get_current_organization_id(db: AsyncSession) -> uuid.UUID | None:
    """
    Get the current organization ID from the session context.

    Args:
        db: The database session

    Returns:
        The current organization UUID, or None if not set
    """
    result = await db.execute(
        text("SELECT current_setting('app.current_organization_id', true)")
    )
    value = result.scalar()
    if value:
        try:
            return uuid.UUID(value)
        except ValueError:
            return None
    return None


# Synchronous versions for non-async code


def set_current_organization_sync(
    db: Session,
    organization_id: uuid.UUID,
) -> None:
    """
    Synchronous version of :func:`set_current_organization`.

    Args:
        db: The database session
        organization_id: The UUID of the current organization/tenant

    ERP and shared-module scope are set atomically from the explicit
    Organization-to-Tenant mapping.
    """
    db.execute(_SET_CURRENT_SCOPE_SQL, _scope_params(organization_id))


def set_current_organization_on_connection(
    connection: Connection,
    organization_id: uuid.UUID,
) -> None:
    """Set ERP and shared-module scope directly on a Connection.

    Needed by the ``after_begin`` re-arming in
    ``app.db.session_context``: that event fires while the Session is still
    provisioning its connection, so issuing the statement through the Session
    raises ``InvalidRequestError`` ("concurrent operations are not
    permitted"). The Connection handed to the handler is the one the new
    transaction runs on, which is exactly where the GUC belongs.
    """
    if connection.dialect.name != "postgresql":
        # SQLite (the unit-test lane) has no GUCs and rejects SET LOCAL
        # outright. Guarding here rather than at the call site keeps the
        # arming path a straight call, and mirrors prime_tenant_context.
        return
    connection.execute(_SET_CURRENT_SCOPE_SQL, _scope_params(organization_id))


def clear_organization_context_sync(db: Session) -> None:
    """Clear ERP and shared-module scope together (sync version)."""
    db.execute(_CLEAR_CURRENT_SCOPE_SQL)


@contextmanager
def tenant_context_sync(
    db: Session,
    organization_id: uuid.UUID,
) -> Generator[None, None, None]:
    """
    Synchronous context manager to set tenant context.

    Usage:
        with tenant_context_sync(db, org_id):
            # All queries here are scoped to org_id
            data = db.execute(select(SomeModel))

    Args:
        db: The database session
        organization_id: The UUID of the organization/tenant
    """
    set_current_organization_sync(db, organization_id)
    try:
        yield
    finally:
        clear_organization_context_sync(db)


def get_current_organization_id_sync(db: Session) -> uuid.UUID | None:
    """
    Get the current organization ID from the session context (sync version).

    Args:
        db: The database session

    Returns:
        The current organization UUID, or None if not set
    """
    result = db.execute(
        text("SELECT current_setting('app.current_organization_id', true)")
    )
    value = result.scalar()
    if value:
        try:
            return uuid.UUID(value)
        except ValueError:
            return None
    return None
